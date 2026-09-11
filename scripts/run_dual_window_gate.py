#!/usr/bin/env python3
"""Dual-window (multi-window) walk-forward gate orchestrator.

目的/口径：单一窗口的收益结论极易被样本区间主导（同一条 WF-001
3y −15.30% vs 5y +47.65%）。本编排在 >= 2 个窗口上分别跑生产 walk-forward
gate，汇总各窗口的 ``both_pass`` 方向，把 ``window_consistency`` 证据写入主
gate sidecar；``validate_walkforward_gate_payload`` 据此对「方向不一致」的档案
fail-closed（恢复「任何判决须两个不重叠窗口方向一致才采信」的门禁前置）。

本脚本只做编排与证据汇总，不改变任何门禁阈值；底层仍调用
``scripts/run_production_walkforward_gate.py``（其默认 profile = stable_plus，N=8）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aqsp.utils.jsonl_io import atomic_write_text  # noqa: E402
from aqsp.walkforward_gate import build_window_consistency  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_GATE = PROJECT_ROOT / "scripts" / "run_production_walkforward_gate.py"


def parse_window_spec(spec: str) -> tuple[str, str, str]:
    """解析 ``LABEL:START:END``（START/END 为 YYYY-MM-DD）。"""
    parts = [item.strip() for item in spec.split(":")]
    if len(parts) != 3 or not all(parts):
        raise argparse.ArgumentTypeError(
            f"窗口须为 LABEL:START:END，收到 {spec!r}"
        )
    label, start, end = parts
    return label, start, end


def summarize_window(
    gate_payload: dict[str, object],
    *,
    label: str,
    start: str,
    end: str,
) -> dict[str, object]:
    """从单个窗口的 gate sidecar 抽取供一致性判定的记录。"""
    return {
        "label": label,
        "start": start,
        "end": end,
        "deflated_sharpe": gate_payload.get("deflated_sharpe"),
        "pbo": gate_payload.get("pbo"),
        "n_variants": gate_payload.get("n_variants"),
        "both_pass": bool(gate_payload.get("both_pass")),
    }


def run_window(
    *,
    window: tuple[str, str, str],
    args: argparse.Namespace,
    run_dir: Path,
) -> dict[str, object]:
    label, start, end = window
    report = run_dir / f"{label}-report.md"
    gate = run_dir / f"{label}-gate.json"
    log = run_dir / f"{label}.log"
    cache = run_dir / f"{label}-cache.db"
    command = [
        sys.executable,
        str(PRODUCTION_GATE),
        "--db",
        str(args.db),
        "--start",
        start,
        "--end",
        end,
        "--grid-profile",
        args.grid_profile,
        "--report",
        str(report),
        "--gate-path",
        str(gate),
        "--log",
        str(log),
        "--cache-path",
        str(cache),
    ]
    if args.crash_protection:
        command.append("--crash-protection")
    print(f"[dual-window] 运行窗口 {label}: {start} ~ {end}")
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise SystemExit(
            f"窗口 {label} 生产 gate 退出码 {completed.returncode}；"
            f"见 {log}"
        )
    payload = json.loads(gate.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"窗口 {label} gate sidecar 非对象: {gate}")
    return summarize_window(payload, label=label, start=start, end=end)


def stamp_primary_gate(
    *,
    primary_gate: Path,
    consistency: dict[str, object],
) -> None:
    payload = json.loads(primary_gate.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"主 gate sidecar 非对象: {primary_gate}")
    payload["window_consistency"] = consistency
    atomic_write_text(
        primary_gate,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--window",
        action="append",
        required=True,
        type=parse_window_spec,
        help="窗口 LABEL:START:END，可重复（>=2）。第一个窗口的 gate 作为主 sidecar。",
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--grid-profile",
        default="stable_plus",
        choices=("stable", "stable_plus", "exploratory"),
    )
    parser.add_argument("--crash-protection", action="store_true")
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "dual_window_gate",
    )
    parser.add_argument(
        "--output-gate",
        type=Path,
        default=None,
        help="汇总一致性后的主 gate 路径；默认 = 第一个窗口的 gate。",
    )
    args = parser.parse_args(argv)

    if len(args.window) < 2:
        parser.error("--window 至少两个（不重叠窗口方向一致性判定）")

    args.run_dir.mkdir(parents=True, exist_ok=True)
    records = [
        run_window(window=window, args=args, run_dir=args.run_dir)
        for window in args.window
    ]
    consistency = build_window_consistency(records)

    primary_gate = (
        args.run_dir / f"{args.window[0][0]}-gate.json"
        if args.output_gate is None
        else args.output_gate
    )
    stamp_primary_gate(primary_gate=primary_gate, consistency=consistency)

    verdicts = [record["both_pass"] for record in records]
    all_pass = all(verdicts)
    print(
        f"[dual-window] 窗口={len(records)} 一致={consistency['consistent']} "
        f"全部通过={all_pass} → 主 gate: {primary_gate}"
    )
    if not consistency["consistent"]:
        print("[dual-window] BLOCK: 窗口方向不一致（窗口依赖，不予采信）")
        return 1
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
