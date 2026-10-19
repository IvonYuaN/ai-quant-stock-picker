#!/usr/bin/env python3
"""Drive the #199 control experiments on the runner, one gate at a time.

Each gate run is ~11.5h (9 serial walk-forwards), so the three control runs of
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
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_RUNNER_ROOT = Path("/opt/aqsp-runner")
GATE_SCRIPT = "scripts/runner/runner_gate.sh"
RESULT_NAME = "walkforward_gate.json"


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


# The three control runs of #199.
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


def run_one(spec: Spec, out_root: Path, runner_root: Path, gate_script: Path) -> int:
    """Run one gate.  Returns the subprocess exit code (0 = success)."""
    env = build_env(spec, out_root, runner_root)
    Path(env["OUT"]).mkdir(parents=True, exist_ok=True)
    print(f"[ctl] === {spec.name} ===", flush=True)
    proc = subprocess.run(["bash", str(gate_script)], env=env, check=False)
    if proc.returncode != 0:
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
        return 1

    target = result_path(spec, out_root)
    target.write_bytes(produced.read_bytes())
    print(f"[ctl] {spec.name} -> {target}", flush=True)
    return 0


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

    gate_script = args.runner_root / "aqsp-scheduler-current" / GATE_SCRIPT
    if not gate_script.exists():
        print(f"missing gate script: {gate_script}", file=sys.stderr)
        return 1

    for spec in todo:
        rc = run_one(spec, out_root, args.runner_root, gate_script)
        if rc != 0:
            # Stop on first failure: the next gate would just contend for the
            # same single spare core and produce a second unusable result.
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
