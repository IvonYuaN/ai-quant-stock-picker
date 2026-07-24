from __future__ import annotations

from scripts import check_scheduler


def test_check_cron_lock_collisions_rejects_shared_outer_lock(monkeypatch) -> None:
    crontab = "\n".join(
        (
            "*/15 * * * * flock -xn /tmp/monitor.lock -c /cron/monitor",
            "*/10 * * * * flock -xn /tmp/monitor.lock -c /cron/intraday",
        )
    )
    monkeypatch.setattr(check_scheduler, "_run", lambda _args: (0, crontab))

    result = check_scheduler.check_cron_lock_collisions()

    assert result.ok is False
    assert "/cron/intraday,/cron/monitor" in result.detail


def test_check_cron_lock_collisions_accepts_per_task_locks(monkeypatch) -> None:
    crontab = "\n".join(
        (
            "*/15 * * * * flock -xn /tmp/monitor.lock -c /cron/monitor",
            "*/10 * * * * flock -xn /tmp/intraday.lock -c /cron/intraday",
        )
    )
    monkeypatch.setattr(check_scheduler, "_run", lambda _args: (0, crontab))

    result = check_scheduler.check_cron_lock_collisions()

    assert result.ok is True
    assert result.detail == "no cross-task flock collisions"


def test_scheduled_actions_returns_actions_from_bt_panel_wrappers(tmp_path) -> None:
    daily = tmp_path / "daily"
    daily.write_text(
        "/bin/bash /opt/aqsp/scripts/release_task_entrypoint.sh daily\n",
        encoding="utf-8",
    )
    gate = tmp_path / "gate"
    gate.write_text(
        "/bin/bash /opt/aqsp/scripts/release_task_entrypoint.sh walkforward-gate\n",
        encoding="utf-8",
    )
    crontab = "\n".join(
        (
            f"0 18 * * * flock -xn {tmp_path}/daily.lock -c {daily}",
            f"0 22 * * 6 flock -xn {tmp_path}/gate.lock -c {gate}",
        )
    )

    actions = check_scheduler._scheduled_actions(
        crontab,
        lambda path: path.read_text(encoding="utf-8"),
    )

    assert actions == {"daily", "walkforward-gate"}


def test_scheduled_actions_ignores_bt_task_comment_words(tmp_path) -> None:
    wrapper = tmp_path / "intraday"
    wrapper.write_text(
        "# bt_task.sh owns the market-hours gate\n"
        "/bin/bash /opt/aqsp/scripts/release_task_entrypoint.sh intraday\n",
        encoding="utf-8",
    )
    crontab = f"*/10 * * * * flock -xn {tmp_path}/intraday.lock -c {wrapper}"

    actions = check_scheduler._scheduled_actions(
        crontab,
        lambda path: path.read_text(encoding="utf-8"),
    )

    assert actions == {"intraday"}
