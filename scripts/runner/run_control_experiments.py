#!/usr/bin/env python3
"""Drive the #199 control experiments on the runner, one gate at a time.

Each gate run is ~11.5h (9 serial walk-forwards), so the control runs of
#199 cannot be started by hand inside one operating window.  This driver makes
them a single resumable command:

* runs the specs **sequentially** (the runner only has ~1 spare core, two
  concurrent gates would thrash the shared business tenant),
* gives each spec its **own** ``OUT`` directory so runs cannot overwrite each
  other's ``walkforward_gate.json``,
* always sets ``MARK_READY=0`` so a research gate is never picked up by prod's
  ``runner_fetch.sh`` as the weekly production verdict,
* skips specs whose result file already exists, so an interrupted overnight
  window can be resumed instead of restarted.

Results land in ``<out>/<name>.gate.json`` and are compared with
``scripts/analysis/compare_gate_results.py``.

Usage (running is opt-in; the bare command only prints the plan):
    python scripts/runner/run_control_experiments.py
    python scripts/runner/run_control_experiments.py --execute
    python scripts/runner/run_control_experiments.py --execute --only net_fees
"""

from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_RUNNER_ROOT = Path("/opt/aqsp-runner")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NODE_REGISTRY = PROJECT_ROOT / "config" / "compute_nodes.yaml"
GATE_SCRIPT = "scripts/runner/runner_gate.sh"
RESULT_NAME = "walkforward_gate.json"
READY_NAME = "RESULT_READY"
DEFAULT_HARD_TIMEOUT_SECONDS = 57_600
DEFAULT_LOCK_PATH = "/tmp/aqsp-control-experiments.lock"


def _memory_gib(value: str) -> float:
    """Parse a cgroup memory value expressed as GiB, MiB, or bytes."""
    raw = str(value).strip().upper()
    units = (
        ("GIB", 1.0),
        ("GB", 1.0),
        ("G", 1.0),
        ("MIB", 1 / 1024),
        ("MB", 1 / 1024),
        ("M", 1 / 1024),
    )
    for suffix, factor in units:
        if raw.endswith(suffix):
            return float(raw[: -len(suffix)]) * factor
    return float(raw) / (1024**3)


def validate_compute_node(
    node_id: str,
    *,
    registry_path: Path = DEFAULT_NODE_REGISTRY,
    cpu_quota_percent: int,
    memory_max: str,
    tasks_max: int,
    runtime_max_seconds: int,
) -> dict[str, object]:
    """Fail closed unless a registered heavy compute node can host the run."""
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    nodes = payload.get("nodes", []) if isinstance(payload, dict) else []
    node = next((item for item in nodes if item.get("id") == node_id), None)
    if not isinstance(node, dict):
        raise ValueError(f"unregistered compute node: {node_id}")
    if node.get("role") not in {"shared_compute", "dedicated_compute"}:
        raise ValueError(f"node {node_id} is not a heavy-compute node")
    if node.get("heavy_workloads_allowed") is not True:
        raise ValueError(f"heavy workloads forbidden on node: {node_id}")
    limits = node.get("resource_limits")
    if not isinstance(limits, dict):
        raise ValueError(f"node {node_id} has no resource limits")
    if cpu_quota_percent > int(limits["cpu_quota_percent"]):
        raise ValueError(f"CPUQuota exceeds registered limit for {node_id}")
    if _memory_gib(memory_max) > float(limits["memory_max_gib"]):
        raise ValueError(f"MemoryMax exceeds registered limit for {node_id}")
    if tasks_max > int(limits["tasks_max"]):
        raise ValueError(f"TasksMax exceeds registered limit for {node_id}")
    if runtime_max_seconds > int(limits["runtime_max_seconds"]):
        raise ValueError(f"runtime exceeds registered limit for {node_id}")
    return node


@dataclass(frozen=True)
class Spec:
    """One control experiment.

    ``verdict_meaningful`` is False when the grid is thinner than
    ``MIN_CSCV_VARIANTS`` (=8).  In that case ``both_pass`` is forced to False
    by the gate itself, so the run can only be read as a *point estimate*
    (does DSR/PBO move?) and never as a pass/fail verdict.
    """

    name: str
    env: dict[str, str] = field(default_factory=dict)
    note: str = ""
    verdict_meaningful: bool = True


# The frozen control runs of #199. ``full_span`` is the second time-window
# control required by the long-term recommendation spec; it changes coverage
# mode only and keeps the grid/cost identical to ``baseline``.
SPECS: tuple[Spec, ...] = (
    Spec(
        name="baseline",
        env={"GRID_PROFILE": "stable_plus", "LOOKBACK_YEARS": "3", "NET_FEES": "0"},
        note="生产口径复现：stable_plus(N=8) / legacy 成本 / 近 3y",
    ),
    Spec(
        name="net_fees",
        env={"GRID_PROFILE": "stable_plus", "LOOKBACK_YEARS": "3", "NET_FEES": "1"},
        note="成本口径对照：同上但透传 --net-fees",
    ),
    Spec(
        name="full_span",
        env={
            "GRID_PROFILE": "stable_plus",
            "LOOKBACK_YEARS": "3",
            "NET_FEES": "0",
            "COVERAGE_MODE": "legacy_full_span",
        },
        note="时间窗口对照：同 baseline，但使用 legacy_full_span",
    ),
    Spec(
        name="shrink",
        env={"GRID_PROFILE": "stable", "LOOKBACK_YEARS": "3", "NET_FEES": "0"},
        note="收缩 grid：stable(N=5) —— 结构性 FAIL，只看 DSR/PBO 点估计",
        verdict_meaningful=False,
    ),
)


def production_gate_run(runner_root: Path) -> Path:
    """The dir prod's ``runner_fetch.sh`` pulls from; never write here."""
    return (runner_root / "gate_run").resolve()


def build_env(
    spec: Spec,
    out_root: Path,
    runner_root: Path = DEFAULT_RUNNER_ROOT,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return the environment for one spec, with isolation enforced.

    Raises ``ValueError`` if the requested output dir would collide with the
    production ``gate_run`` -- that collision is the one failure mode that can
    silently promote a research gate to the weekly production verdict.
    """
    out_dir = (out_root / spec.name).resolve()
    if out_dir == production_gate_run(runner_root):
        raise ValueError(f"refusing to write into production gate_run: {out_dir}")

    env = dict(os.environ if base_env is None else base_env)
    env.update(spec.env)
    env["RUNNER_ROOT"] = str(runner_root)
    env["OUT"] = str(out_dir)
    env["MARK_READY"] = "0"  # research run: never raise RESULT_READY
    return env


def result_path(spec: Spec, out_root: Path) -> Path:
    return out_root / f"{spec.name}.gate.json"


def clear_ready_marker(out_dir: Path) -> None:
    """Remove a stale production marker before/after a research run."""
    marker = out_dir / READY_NAME
    try:
        marker.unlink()
    except FileNotFoundError:
        pass


def build_scoped_command(
    gate_script: Path,
    *,
    use_systemd_scope: bool,
    cpu_quota_percent: int,
    memory_max: str,
    tasks_max: int,
) -> list[str]:
    """Build a cgroup-scoped command for a long-running research gate."""
    base = ["bash", str(gate_script)]
    if not use_systemd_scope:
        return base
    if shutil.which("systemd-run") is None:
        raise RuntimeError("systemd-run unavailable; refusing unbounded research run")
    return [
        "systemd-run",
        "--quiet",
        "--wait",
        "--collect",
        "--scope",
        "-p",
        f"CPUQuota={cpu_quota_percent}%",
        "-p",
        f"MemoryMax={memory_max}",
        "-p",
        "MemorySwapMax=0",
        "-p",
        f"TasksMax={tasks_max}",
        *base,
    ]


def select_specs(
    specs: tuple[Spec, ...] = SPECS,
    only: str | None = None,
    allow_thin_cscv: bool = False,
) -> list[Spec]:
    """Apply --only and the thin-CSCV opt-in filter."""
    chosen = [s for s in specs if only is None or s.name == only]
    if not allow_thin_cscv:
        chosen = [s for s in chosen if s.verdict_meaningful]
    return chosen


def pending(specs: list[Spec], out_root: Path, force: bool = False) -> list[Spec]:
    """Specs still to run.  An existing result file means "already done"."""
    if force:
        return list(specs)
    return [s for s in specs if not result_path(s, out_root).exists()]


def render_plan(specs: list[Spec], out_root: Path) -> str:
    if not specs:
        return "nothing to run"
    width = max(len(s.name) for s in specs)
    lines = [f"# out: {out_root}"]
    for s in specs:
        verdict = "verdict" if s.verdict_meaningful else "POINT-ESTIMATE-ONLY"
        kv = " ".join(f"{k}={v}" for k, v in sorted(s.env.items()))
        lines.append(f"{s.name.ljust(width)}  [{verdict}]  {kv}")
        if s.note:
            lines.append(f"{' ' * width}  {s.note}")
    return "\n".join(lines)


def run_one(
    spec: Spec,
    out_root: Path,
    runner_root: Path,
    gate_script: Path,
    *,
    hard_timeout_seconds: int = DEFAULT_HARD_TIMEOUT_SECONDS,
    use_systemd_scope: bool = False,
    cpu_quota_percent: int = 150,
    memory_max: str = "4G",
    tasks_max: int = 256,
) -> int:
    """Run one gate.  Returns the subprocess exit code (0 = success)."""
    env = build_env(spec, out_root, runner_root)
    out_dir = Path(env["OUT"])
    out_dir.mkdir(parents=True, exist_ok=True)
    clear_ready_marker(out_dir)
    print(f"[ctl] === {spec.name} ===", flush=True)
    try:
        command = build_scoped_command(
            gate_script,
            use_systemd_scope=use_systemd_scope,
            cpu_quota_percent=cpu_quota_percent,
            memory_max=memory_max,
            tasks_max=tasks_max,
        )
        proc = subprocess.run(
            command,
            env=env,
            check=False,
            timeout=hard_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        clear_ready_marker(out_dir)
        print(
            f"[ctl] {spec.name} HARD TIMEOUT after {hard_timeout_seconds}s",
            file=sys.stderr,
        )
        return 124
    except RuntimeError as exc:
        clear_ready_marker(out_dir)
        print(f"[ctl] {spec.name} REFUSED: {exc}", file=sys.stderr)
        return 125
    if proc.returncode != 0:
        clear_ready_marker(out_dir)
        print(f"[ctl] {spec.name} FAILED rc={proc.returncode}", file=sys.stderr)
        return proc.returncode

    produced = Path(env["OUT"]) / RESULT_NAME
    if not produced.exists():
        # A load-guard skip exits 0 without writing a gate.  Treat as "not
        # done" rather than success, otherwise the driver would report a
        # complete set while a result is silently missing.
        print(
            f"[ctl] {spec.name}: gate exited 0 but {produced} absent "
            "(load-guard skip?); leaving it pending",
            file=sys.stderr,
        )
        clear_ready_marker(out_dir)
        return 1

    target = result_path(spec, out_root)
    target.write_bytes(produced.read_bytes())
    clear_ready_marker(out_dir)
    print(f"[ctl] {spec.name} -> {target}", flush=True)
    return 0


def supports_out_override(gate_script: Path) -> bool:
    """``runner_gate.sh`` 是否把 ``OUT`` 做成可覆盖（``${OUT:-...}``）。

    老 release 里 ``OUT="$RUNNER_ROOT/gate_run"`` 是硬编码的，驱动传进去的
    ``OUT`` 会被无视：gate 仍然写生产 ``gate_run/``，本驱动找不到产物，
    于是白跑一次 ~11.5h 才报错。跑之前先查这一行，比跑完再发现便宜得多。
    """
    try:
        text = gate_script.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(
        line.lstrip().startswith("OUT=") and "${OUT:-" in line
        for line in text.splitlines()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the #199 control experiments.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually run the gates (default is a dry-run plan)",
    )
    parser.add_argument("--only", default=None, help="run a single spec by name")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output root (default: <runner-root>/gate_run/199_control)",
    )
    parser.add_argument("--runner-root", type=Path, default=DEFAULT_RUNNER_ROOT)
    parser.add_argument("--node-id", default="aqsp-runner")
    parser.add_argument("--node-registry", type=Path, default=DEFAULT_NODE_REGISTRY)
    parser.add_argument(
        "--hard-timeout-seconds",
        type=int,
        default=DEFAULT_HARD_TIMEOUT_SECONDS,
        help="outer deadline; timeout is fail-closed and clears RESULT_READY",
    )
    parser.add_argument(
        "--lock-path",
        type=Path,
        default=Path(DEFAULT_LOCK_PATH),
        help="single-instance lock for control experiments",
    )
    parser.add_argument(
        "--no-systemd-scope",
        action="store_true",
        help="diagnostic only; allow an unscoped run explicitly",
    )
    parser.add_argument("--cpu-quota-percent", type=int, default=150)
    parser.add_argument("--memory-max", default="4G")
    parser.add_argument("--tasks-max", type=int, default=256)
    parser.add_argument(
        "--force", action="store_true", help="re-run specs that already have a result"
    )
    parser.add_argument(
        "--allow-thin-cscv",
        action="store_true",
        help="include grids with fewer variants than MIN_CSCV_VARIANTS "
        "(both_pass is then structurally FAIL; point estimate only)",
    )
    args = parser.parse_args(argv)

    out_root = args.out or (args.runner_root / "gate_run" / "199_control")
    specs = select_specs(only=args.only, allow_thin_cscv=args.allow_thin_cscv)
    if not specs:
        print("no spec selected (check --only / --allow-thin-cscv)", file=sys.stderr)
        return 2

    todo = pending(specs, out_root, force=args.force)
    print(render_plan(todo, out_root))

    if not args.execute:
        print("\n# dry-run; pass --execute to run", file=sys.stderr)
        return 0
    if not todo:
        return 0

    if args.hard_timeout_seconds <= 0:
        print("--hard-timeout-seconds must be positive", file=sys.stderr)
        return 2
    if args.cpu_quota_percent <= 0 or args.tasks_max <= 0:
        print("resource limits must be positive", file=sys.stderr)
        return 2

    try:
        validate_compute_node(
            args.node_id,
            registry_path=args.node_registry,
            cpu_quota_percent=args.cpu_quota_percent,
            memory_max=args.memory_max,
            tasks_max=args.tasks_max,
            runtime_max_seconds=args.hard_timeout_seconds,
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"compute node refused: {exc}", file=sys.stderr)
        return 2

    gate_script = args.runner_root / "aqsp-scheduler-current" / GATE_SCRIPT
    if not gate_script.exists():
        print(f"missing gate script: {gate_script}", file=sys.stderr)
        return 1
    if not supports_out_override(gate_script):
        print(
            f"{gate_script} 的 OUT 仍是硬编码（缺少 ${{OUT:-...}}），本驱动传的 "
            "OUT 会被无视 —— 每组结果都会落进生产 gate_run/ 并互相覆盖。\n"
            "需要先部署含该改动的 release，或改用 runner_gate.sh 原生环境变量跑单组。",
            file=sys.stderr,
        )
        return 1

    args.lock_path.parent.mkdir(parents=True, exist_ok=True)
    with args.lock_path.open("w") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"control experiment already running: {args.lock_path}", file=sys.stderr)
            return 3
        for spec in todo:
            rc = run_one(
                spec,
                out_root,
                args.runner_root,
                gate_script,
                hard_timeout_seconds=args.hard_timeout_seconds,
                use_systemd_scope=not args.no_systemd_scope,
                cpu_quota_percent=args.cpu_quota_percent,
                memory_max=args.memory_max,
                tasks_max=args.tasks_max,
            )
            if rc != 0:
                # Stop on first failure: the next gate would just contend for the
                # same single spare core and produce a second unusable result.
                return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
