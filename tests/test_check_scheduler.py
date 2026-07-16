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
