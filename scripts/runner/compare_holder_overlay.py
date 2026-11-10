#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""holder_overlay 取证判读：逐臂对比 overlay(on) vs 基线 overlay(off)。

背景
----
`--grid-profile holder_overlay` 的 13 个臂 = `_WALKFORWARD_VALIDATED_GRID_VARIANTS`
全套（id 加 `WO-` 前缀）各开 overlay。其中 **8 个臂与生产 `stable_plus` 的生产
gate run 参数完全相同**（`WO-WF-001` ↔ `WF-001` 等）⇒ 与那份**已完成**的生产
gate 结果逐臂对比即可，无需再跑一轮 17h 的 overlay-off 基线。

判读口径（缺一不可）
--------------------
1. **逐臂对比（主证据）**：按**变体 id 去 `WO-` 前缀**匹配两边的臂（档位由
   `replace(v, variant_id=f"WO-{v.variant_id}")` 逐臂复制 ⇒ 构造即 1:1）。
   **不能按参数签名匹配**：报告表的权重列是**声明值**，`WF-001` 与 `WF-V01`
   声明参数完全相同（但后者额外启用 volume）⇒ 按签名会把两臂塌成一个
   （实测 8 臂只解析出 7 个）。看 ΔSharpe / Δ总收益：overlay 只改
   `select_stocks` 的重排 ⇒ 同参数臂的差异**只能**来自 overlay。
2. **gate 级判据（DSR/PBO）不可跨臂数直接比**：DSR 的多重检验惩罚依赖
   `n_variants`（基线 8 臂 vs 本档位 13 臂）⇒ 两个 DSR 数值不同不能直接判优劣，
   只能作参考。真正可比的是逐臂。
3. **窗口差异必须显式标注**：生产基线窗口 2023-11-07..2026-11-06，本档位
   2023-11-10..2026-11-09（差 3 天、长度一致）。差异极小但不为零 ⇒ 结论里标注。

🔴 预注册判据（2026-11-10 跑批**开始后、结果未知时**定下，防事后按结果编标准）
--------------------------------------------------------------------------------
「overlay 有正效果、可考虑开生产」须**同时**满足：

- **A（主判据）**：8 个可配对臂中 **≥5 臂 ΔSharpe > 0**，且 **ΔSharpe 均值 > 0**。
  （≥5/8 才不是噪声级翻转；只看均值会被单臂极值带偏，故两个条件都要。）
- **B（辅助判据）**：全档位 DSR 优于基线 **−1.2463**。
  ⚠️ 本档位 13 臂 vs 基线 8 臂 ⇒ 本档位受**更重**的多重检验惩罚，故 B 通过
  含义更强；但 B **不通过不足以单独否决**（臂数不同，非同口径）。

任一条不满足 ⇒ **不得**开生产 overlay（`thresholds.yaml` 保持
`holder_concentration_weight: 0.0` + `enabled: false`），结论如实记为
「无已证 edge」，**不许改判据/换窗口/挑臂重跑**（红线：禁「亏损就调参」）。
即使 A+B 都通过，开生产仍是**须老大拍板**的红线动作（AGENTS §6.1）。

用法
----
    # 跑完后（推荐）：
    python scripts/runner/compare_holder_overlay.py \\
      --baseline-report /tmp/gate_run_baseline/report.md \\
      --candidate-report /tmp/holder_overlay/report.md \\
      --baseline-gate   /tmp/gate_run_baseline/walkforward_gate.json \\
      --candidate-gate  /tmp/holder_overlay/walkforward_gate.json

    # 跑批中（增量，从日志里已完成的臂读）：
    python scripts/runner/compare_holder_overlay.py \\
      --baseline-report /tmp/gate_run_baseline/report.md \\
      --candidate-log /tmp/holder_overlay/runner_holder_gate.log
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

#: 报告表里的列顺序（稳定）：变体 id + 7 维权重 + lb/h/top + Sharpe + 总收益 +
#: 暴露归一化收益 + 周期数。
_PARAM_KEYS = ("mom", "tr", "qual", "val", "vol", "mr", "htf", "lb", "h", "top")


def _to_float(text: str) -> float:
    """把 ``0.3`` / ``-3.81%`` / ``+0.13%`` 统一解析成 float（百分数转成小数）。"""
    s = text.strip().replace(",", "")
    if not s:
        raise ValueError("empty number")
    if s.endswith("%"):
        return float(s[:-1]) / 100.0
    return float(s)


def parse_report_table(text: str) -> dict[str, dict[str, Any]]:
    """解析 report.md 的逐变体表，按**变体 id**索引（id 即 ``WF-*`` 原样）。

    🔴 为什么按 id 而非「参数签名」匹配：
    报告表的 mom/tr/qual/... 列是**声明参数**（variant 对象上的值），不是生效值。
    实证：`WF-001` 与 `WF-V01` 的声明参数完全相同（0.3/0.3/0/0/0/0/0/60/3/10），
    但 `WF-V01` 走 volume 分支（实际还启用了 volume 0.3）⇒ 按签名匹配会
    **把两个臂塌成一个、丢掉一臂**（实测：8 臂只解析出 7 个）。

    而 `holder_overlay` 档位是 `replace(v, variant_id=f"WO-{v.variant_id}")`
    逐臂复制的（构造即保证 1:1）⇒ **id 去前缀后与原臂严格对应**，是精确匹配。
    参数签名仅作展示列 + 交叉校验用。
    """
    rows: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 15:
            continue
        # 跳过表头 / 分隔行（第一个单元格是"变体"或全 '-'）
        if cells[0] in {"变体", ""} or set(cells[0]) <= {"-", ":"}:
            continue
        try:
            signature = tuple(_to_float(cells[i]) for i in range(1, 11))
            sharpe = _to_float(cells[11])
            total_return = _to_float(cells[12])
        except (ValueError, IndexError):
            continue
        rows[cells[0]] = {
            "variant": cells[0],
            "signature": signature,
            "sharpe": sharpe,
            "total_return": total_return,
            "ann_return": cells[13],
            "periods": cells[14],
        }
    return rows


_LOG_ARM_RE = re.compile(
    r"^grid variant (?P<vid>\S+): periods=(?P<periods>\d+) "
    r"sharpe=(?P<sharpe>-?\d+(?:\.\d+)?) return=(?P<ret>-?\d+(?:\.\d+)?)%"
)


def parse_log_arms(text: str) -> dict[str, dict[str, Any]]:
    """从跑批日志里解析**已完成**的臂（增量判读用）。

    只给出 id / sharpe / 总收益 —— 日志里没有 7 维权重，故无法按参数签名匹配，
    只能按 id 近似对应（`WO-XXX` ↔ `XXX`），**结论里必须标注这一点**。
    """
    out: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        match = _LOG_ARM_RE.match(line.strip())
        if not match:
            continue
        out[match.group("vid")] = {
            "sharpe": float(match.group("sharpe")),
            "total_return": float(match.group("ret")) / 100.0,
            "periods": int(match.group("periods")),
        }
    return out


def _strip_overlay_prefix(vid: str) -> str:
    return vid[3:] if vid.startswith("WO-") else vid


def _load_gate(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _gate_summary(payload: dict[str, Any]) -> str:
    if not payload:
        return "（gate json 未提供或不可读）"
    keys = (
        "deflated_sharpe",
        "pbo",
        "n_variants",
        "cscv_reliable",
        "dsr_pass",
        "pbo_pass",
        "both_pass",
        "data_start",
        "data_end",
    )
    return "、".join(f"{k}={payload.get(k)}" for k in keys)


#: 生产基线（`stable_plus`，overlay OFF）的 DSR。用于预注册判据 B。
BASELINE_DSR = -1.2463

#: 预注册判据 A 的阈值：8 个可配对臂中最少多少臂 ΔSharpe>0。
MIN_IMPROVED_ARMS = 5


def _preregistered_verdict(
    *,
    matched: int,
    baseline_total: int,
    deltas: list[float],
    baseline_gate: dict[str, Any] | None,
    candidate_gate: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    """按**预注册判据**（结果未知时定下）给出 PASS/FAIL/未完成 与理由。

    A：8 个可配对臂中 ≥5 臂 ΔSharpe>0 且 ΔSharpe 均值>0。
    B：全档位 DSR 优于基线 `BASELINE_DSR`。
    两条都满足才算「有正效果」；任一条不满足即不得开生产 overlay。
    未跑完（配对臂数 < 基线臂数）⇒ 明确「暂不判定」，不做半程结论。
    """
    reasons: list[str] = []
    if matched < baseline_total:
        return (
            f"未完成（已配对 {matched}/{baseline_total} 臂）——**暂不判定**",
            ["半程数据不构成结论；跑完后重跑本脚本。"],
        )
    improved = sum(1 for d in deltas if d > 0)
    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
    a_pass = improved >= MIN_IMPROVED_ARMS and mean_delta > 0
    reasons.append(
        f"A 逐臂：改善 {improved}/{len(deltas)} 臂（门槛 ≥{MIN_IMPROVED_ARMS}）、"
        f"ΔSharpe 均值 {mean_delta:+.3f}（门槛 >0）⇒ {'PASS' if a_pass else 'FAIL'}"
    )

    cand_dsr = (candidate_gate or {}).get("deflated_sharpe")
    if isinstance(cand_dsr, (int, float)):
        b_pass = float(cand_dsr) > BASELINE_DSR
        reasons.append(
            f"B DSR：本档位 {float(cand_dsr):.4f} vs 基线 {BASELINE_DSR:.4f} "
            f"⇒ {'PASS' if b_pass else 'FAIL'}"
        )
    else:
        b_pass = False
        reasons.append("B DSR：gate json 未就绪 ⇒ 暂缺（视为未通过，不作半程结论）")

    if a_pass and b_pass:
        verdict = "**PASS（有正效果）** —— 可提「开生产 overlay」给老大拍板"
    elif not a_pass and not b_pass:
        verdict = "**FAIL（无已证 edge）** —— 不得开生产 overlay；许可结论记「无已证 edge」"
    else:
        verdict = "**部分通过** —— 按预注册规则视为「未达可开」；如实记录，不许改判据重跑"
    return verdict, reasons


def build_report(
    *,
    baseline: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]] | None = None,
    candidate_log: dict[str, dict[str, Any]] | None = None,
    baseline_gate: dict[str, Any] | None = None,
    candidate_gate: dict[str, Any] | None = None,
) -> str:
    """生成判读 markdown。"""
    lines: list[str] = ["# holder_overlay 取证判读（overlay on vs 基线 off）", ""]

    # 候选侧：优先用 report（有完整表）；否则用日志（按 id 近似）
    cand_by_id: dict[str, dict[str, Any]] = {}
    if candidate:
        for vid, row in candidate.items():
            cand_by_id[_strip_overlay_prefix(vid)] = row
    elif candidate_log:
        for vid, row in candidate_log.items():
            cand_by_id[_strip_overlay_prefix(vid)] = row

    # 先算逐臂 Δ（预注册判定要用），再统一输出
    matched = 0
    deltas: list[float] = []
    unmatched_baseline: list[str] = []
    for base_id, base_row in sorted(baseline.items()):
        cand_row = cand_by_id.get(base_id)
        if cand_row is None:
            unmatched_baseline.append(base_id)
            continue
        deltas.append(cand_row["sharpe"] - base_row["sharpe"])
        matched += 1

    verdict, reasons = _preregistered_verdict(
        matched=matched,
        baseline_total=len(baseline),
        deltas=deltas,
        baseline_gate=baseline_gate,
        candidate_gate=candidate_gate,
    )
    lines += [
        "## 零、预注册判定（判据在结果未知时定下，防事后编标准）",
        "",
        f"**{verdict}**",
        "",
    ]
    lines += [f"- {r}" for r in reasons]
    lines += [
        "",
        "> 判据 A：8 个可配对臂中 ≥5 臂 ΔSharpe>0 **且** ΔSharpe 均值>0。",
        "> 判据 B：全档位 DSR 优于基线 −1.2463（本档位 13 臂 vs 基线 8 臂 ⇒ 受更重",
        "> 多重检验惩罚，故 B 通过含义更强；B 不通过不足以单独否决）。",
        "> **任一条不满足 ⇒ 不得开生产 overlay**；不许改判据/换窗口/挑臂重跑",
        "> （红线：禁「亏损就调参」）。A+B 全过也只是「可提议」，开生产仍须老大拍板。",
        "",
    ]

    lines += ["## 一、gate 级判据（⚠️ 不可跨臂数直接比）", ""]
    lines.append(f"- 基线（overlay OFF）：{_gate_summary(baseline_gate or {})}")
    lines.append(f"- 本档位（overlay ON）：{_gate_summary(candidate_gate or {})}")
    lines += [
        "",
        "> 🔴 DSR 的多重检验惩罚依赖 `n_variants`（基线 8 臂 vs 本档位 13 臂）⇒",
        "> 两个 DSR 数值不同**不能直接判优劣**，仅作参考。真正可比的是下面逐臂对比。",
        "",
    ]

    lines += ["## 二、逐臂对比（同参数臂，唯一差异 = overlay）", ""]
    lines.append(
        "| 基线臂 | 基线 Sharpe | 基线总收益 | overlay 臂 | overlay Sharpe | ΔSharpe | Δ总收益 |"
    )
    lines.append("|---|---|---|---|---|---|---|")

    for base_id, base_row in sorted(baseline.items()):
        cand_row = cand_by_id.get(base_id)
        if cand_row is None:
            lines.append(
                f"| {base_id} | {base_row['sharpe']:.2f} | {base_row['total_return']:.2%} | "
                "（未完成/无对应臂） | - | - | - |"
            )
            continue
        delta_sharpe = cand_row["sharpe"] - base_row["sharpe"]
        delta_ret = cand_row["total_return"] - base_row["total_return"]
        lines.append(
            f"| {base_id} | {base_row['sharpe']:.2f} | {base_row['total_return']:.2%} | "
            f"WO-{base_id} | {cand_row['sharpe']:.2f} | {delta_sharpe:+.2f} | {delta_ret:+.2%} |"
        )

    lines += ["", f"已配对臂数：**{matched}** / 基线 {len(baseline)} 臂。"]
    if unmatched_baseline:
        lines.append(f"未配对基线臂：{', '.join(unmatched_baseline)}（候选侧尚未跑完）。")
    if deltas:
        improved = sum(1 for d in deltas if d > 0)
        lines.append(
            f"ΔSharpe：均值 **{sum(deltas) / len(deltas):+.3f}**、"
            f"改善 **{improved}/{len(deltas)}** 臂、"
            f"最好 **{max(deltas):+.2f}**、最差 **{min(deltas):+.2f}**。"
        )

    # 自动检测「period 数不一致」——窗口错位会让对齐期数变化，直接影响可比性，
    # 必须机器报出来而不是靠人记着（2026-11-10 实测首臂 19 期 vs 基线 20 期）。
    period_mismatch: list[str] = []
    for base_id, base_row in sorted(baseline.items()):
        cand_row = cand_by_id.get(base_id)
        if cand_row is None:
            continue
        base_p = str(base_row.get("periods", "")).strip()
        cand_p = str(cand_row.get("periods", "")).strip()
        if base_p and cand_p and base_p != cand_p:
            period_mismatch.append(f"{base_id}: 基线 {base_p} 期 vs overlay {cand_p} 期")
    if period_mismatch:
        lines += [
            "",
            "> 🔴 **period 数不一致（对齐期数不同 ⇒ 可比性下降，结论须降权）**：",
        ]
        lines += [f"> - {item}" for item in period_mismatch]

    lines += [
        "",
        "> ⚠️ 窗口差异：生产基线 `2023-11-07..2026-11-06`、本档位 `2023-11-10..2026-11-09`",
        "> （差 3 天、长度一致）⇒ 极小但不为零，结论里须保留此标注。",
        ">",
        "> ⚠️ 匹配口径：`holder_overlay` 档位是 `replace(v, variant_id=f\"WO-{v.variant_id}\")`",
        "> 逐臂复制的（构造即 1:1）⇒ 按 id 去 `WO-` 前缀匹配是**精确**的。",
        "> 不能用「参数签名」匹配：报告表的权重列是**声明值**，`WF-001` 与 `WF-V01`",
        "> 声明参数完全相同（但 `WF-V01` 额外启用 volume）⇒ 按签名会把两臂塌成一个。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-report", type=Path, required=True,
                        help="生产基线 gate 的 report.md（overlay OFF）")
    parser.add_argument("--candidate-report", type=Path, default=None,
                        help="本档位的 report.md（跑完后）")
    parser.add_argument("--candidate-log", type=Path, default=None,
                        help="本档位的跑批日志（跑批中增量判读）")
    parser.add_argument("--baseline-gate", type=Path, default=None)
    parser.add_argument("--candidate-gate", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None, help="写出 markdown 路径")
    args = parser.parse_args(argv)

    if not args.candidate_report and not args.candidate_log:
        parser.error("须给 --candidate-report 或 --candidate-log 之一")

    baseline = parse_report_table(args.baseline_report.read_text(encoding="utf-8"))
    if not baseline:
        raise SystemExit(f"基线 report 未解析出任何变体行：{args.baseline_report}")

    candidate = None
    if args.candidate_report:
        candidate = parse_report_table(
            args.candidate_report.read_text(encoding="utf-8")
        )
    candidate_log = None
    if args.candidate_log:
        candidate_log = parse_log_arms(
            args.candidate_log.read_text(encoding="utf-8", errors="replace")
        )

    text = build_report(
        baseline=baseline,
        candidate=candidate,
        candidate_log=candidate_log,
        baseline_gate=_load_gate(args.baseline_gate),
        candidate_gate=_load_gate(args.candidate_gate),
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
