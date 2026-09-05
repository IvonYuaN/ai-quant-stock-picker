from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _branch(script: str, action: str) -> str:
    return script.split(f"{action})", maxsplit=1)[1].split(";;", maxsplit=1)[0]


def test_bt_task_monitor_skips_weekday_market_holiday() -> None:
    script = (PROJECT_ROOT / "scripts" / "bt_task.sh").read_text(encoding="utf-8")
    monitor_branch = _branch(script, "monitor")

    assert "skip_weekday_market_holiday" in monitor_branch


def test_bt_task_walkforward_gate_status_path_uses_runtime_data_root() -> None:
    script = (PROJECT_ROOT / "scripts" / "bt_task.sh").read_text(encoding="utf-8")
    branch = _branch(script, "walkforward-gate")

    # The gate script defaults its "data/..." status path to PROJECT_ROOT, i.e. the
    # immutable release dir, which monitoring never reads. Force the runtime path.
    assert "--status-path" in branch
    assert (
        "${AQSP_WALKFORWARD_PRODUCTION_STATUS:-${RUNTIME_DATA_ROOT}/walkforward_production_status.json}"
        in branch
    )


def test_bt_task_walkforward_gate_status_path_precedes_user_args() -> None:
    script = (PROJECT_ROOT / "scripts" / "bt_task.sh").read_text(encoding="utf-8")
    branch = _branch(script, "walkforward-gate")

    # Placed before "$@" so an explicit --status-path from the caller still wins
    # (argparse keeps the last occurrence). rindex skips the comment above the call.
    assert branch.index("--status-path") < branch.rindex('"${@:2}"')
