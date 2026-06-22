from __future__ import annotations

from pathlib import Path


def test_bt_task_monitor_skips_weekday_market_holiday() -> None:
    script = Path("scripts/bt_task.sh").read_text(encoding="utf-8")
    monitor_branch = script.split("monitor)", maxsplit=1)[1].split(";;", maxsplit=1)[0]

    assert "skip_weekday_market_holiday" in monitor_branch

