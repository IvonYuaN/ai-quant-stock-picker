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


def test_check_cron_lock_collisions_parses_quoted_baota_wrappers(
    monkeypatch,
) -> None:
    crontab = "\n".join(
        (
            "*/15 * * * * flock -xn /tmp/shared.lock -c '/bin/bash /cron/monitor'",
            "*/10 * * * * flock -xn /tmp/shared.lock -c '/bin/bash /cron/intraday'",
        )
    )
    monkeypatch.setattr(check_scheduler, "_run", lambda _args: (0, crontab))

    result = check_scheduler.check_cron_lock_collisions()

    assert result.ok is False
    assert "/bin/bash /cron/intraday,/bin/bash /cron/monitor" in result.detail


def test_check_crontab_rejects_legacy_direct_entries(monkeypatch) -> None:
    crontab = "0 18 * * * /bin/bash /opt/aqsp/scripts/daily_run.sh\n"
    monkeypatch.setattr(check_scheduler, "_run", lambda _args: (0, crontab))

    result = check_scheduler.check_crontab()

    assert result.ok is False
    assert "daily_run.sh" in result.detail


def test_check_crontab_accepts_sanctioned_direct_entrypoint(monkeypatch) -> None:
    """受认可的直连入口形态（入口脚本 + 已知 action）不算遗留条目，但需如实提示。

    背景：event-data 以 `release_task_entrypoint.sh event-data` 直连登记（09-23 老大
    授权，09-28 归位为入口形态）。旧逻辑把它判成 legacy ⇒ 与「missing: event-data」
    一起产生双重 WARN（2026-09-29 实测）。
    """
    crontab = (
        "20 8 * * 1-5 /bin/bash /opt/aqsp-releases/aqsp-scheduler-current"
        "/scripts/release_task_entrypoint.sh event-data"
        " >> /opt/aqsp/data/logs/event-data/cron-wrap.log 2>&1\n"
    )
    monkeypatch.setattr(check_scheduler, "_run", lambda _args: (0, crontab))

    result = check_scheduler.check_crontab()

    assert result.ok is True
    assert "direct entrypoint schedule present" in result.detail
    assert "event-data" in result.detail


def test_check_crontab_still_rejects_bt_task_direct_call(monkeypatch) -> None:
    """遗留形态（直接调 bt_task.sh，不走入口/面板 wrapper）必须继续拒绝。"""
    crontab = "30 22 * * * /bin/bash /opt/aqsp/scripts/bt_task.sh daily\n"
    monkeypatch.setattr(check_scheduler, "_run", lambda _args: (0, crontab))

    result = check_scheduler.check_crontab()

    assert result.ok is False


def test_scheduled_actions_recognizes_direct_entrypoint_schedule() -> None:
    """直连入口形态也必须被认作「已排期」，否则已排期动作被误报 missing。"""
    crontab = (
        "20 8 * * 1-5 /bin/bash /opt/aqsp-releases/aqsp-scheduler-current"
        "/scripts/release_task_entrypoint.sh event-data"
        " >> /opt/aqsp/data/logs/event-data/cron-wrap.log 2>&1\n"
    )

    actions = check_scheduler._scheduled_actions(crontab, lambda _path: None)

    assert actions == {"event-data"}


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
            f"0 18 * * * flock -xn {tmp_path}/daily.lock -c '/bin/bash {daily}'",
            f"0 22 * * 6 flock -xn {tmp_path}/gate.lock -c '/bin/bash {gate}'",
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


def test_check_logs_accepts_missing_sync_log_for_immutable_release(
    monkeypatch, tmp_path
) -> None:
    project_root = tmp_path / "release"
    project_root.mkdir()
    (project_root / ".aqsp-release.json").write_text("{}\n", encoding="utf-8")
    runtime_data_root = tmp_path / "runtime-data"
    monkeypatch.setattr(check_scheduler, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(check_scheduler, "RUNTIME_DATA_ROOT", runtime_data_root)

    results = check_scheduler.check_logs()

    sync_result = next(result for result in results if "sync-" in result.label)
    assert sync_result.ok is True
    assert sync_result.detail == "not required for an immutable release"


def test_check_python_import_prefers_shared_runtime_venv(monkeypatch, tmp_path) -> None:
    shared_python = tmp_path / "aqsp-vibe-venv" / "bin" / "python3"
    shared_python.parent.mkdir(parents=True)
    shared_python.touch()
    monkeypatch.setenv("AQSP_SHARED_VENV_DIR", str(shared_python.parents[1]))
    monkeypatch.delenv("AQSP_RUNTIME_PYTHON", raising=False)
    monkeypatch.delenv("AQSP_RUNTIME_VENV_DIR", raising=False)
    calls: list[list[str]] = []

    def fake_run(args: list[str], cwd=None) -> tuple[int, str]:
        calls.append(args)
        return 0, "ok"

    monkeypatch.setattr(check_scheduler, "_run", fake_run)

    result = check_scheduler.check_python_import()

    assert result.ok is True
    assert calls[0][0] == str(shared_python)
