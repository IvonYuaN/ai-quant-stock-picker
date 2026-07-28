from __future__ import annotations

from scripts import check_scheduler
from scripts.check_scheduler import CheckResult


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


def test_check_crontab_rejects_legacy_coldstart_entry(monkeypatch) -> None:
    crontab = "40 19 * * 1-5 /bin/bash /opt/aqsp/scripts/coldstart_daily.sh\n"
    monkeypatch.setattr(check_scheduler, "_run", lambda _args: (0, crontab))

    result = check_scheduler.check_crontab()

    assert result.ok is False
    assert "coldstart_daily.sh" in result.detail


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


def test_check_duplicate_bt_panel_actions_rejects_two_coldstart_wrappers(
    monkeypatch, tmp_path
) -> None:
    first = tmp_path / "coldstart-first"
    second = tmp_path / "coldstart-second"
    for wrapper in (first, second):
        wrapper.write_text(
            "/bin/bash /opt/aqsp/scripts/release_task_entrypoint.sh coldstart\n",
            encoding="utf-8",
        )
    crontab = "\n".join(
        (
            f"40 19 * * 1-5 flock -xn {tmp_path}/coldstart-a.lock -c '/bin/bash {first}'",
            f"45 19 * * 1-5 flock -xn {tmp_path}/coldstart-b.lock -c '/bin/bash {second}'",
        )
    )
    monkeypatch.setattr(check_scheduler, "_run", lambda _args: (0, crontab))

    result = check_scheduler.check_duplicate_bt_panel_actions()

    assert result.ok is False
    assert "coldstart" in result.detail
    assert str(first) in result.detail
    assert str(second) in result.detail


def test_bt_panel_wrapper_audit_rejects_legacy_direct_heavy_entry(tmp_path) -> None:
    wrapper = tmp_path / "legacy-variant"
    wrapper.write_text(
        "/bin/bash /opt/aqsp/scripts/variant_refresh.sh\n", encoding="utf-8"
    )

    result = check_scheduler.check_bt_panel_wrapper_integrity(tmp_path)

    assert result.ok is False
    assert "legacy direct AQSP" in result.detail
    assert str(wrapper) in result.detail


def test_bt_panel_wrapper_audit_rejects_duplicate_heavy_action(tmp_path) -> None:
    first = tmp_path / "coldstart-first"
    second = tmp_path / "coldstart-second"
    for wrapper in (first, second):
        wrapper.write_text(
            "/bin/bash /opt/aqsp/scripts/release_task_entrypoint.sh coldstart\n",
            encoding="utf-8",
        )

    result = check_scheduler.check_bt_panel_wrapper_integrity(tmp_path)

    assert result.ok is False
    assert "coldstart" in result.detail
    assert str(first) in result.detail
    assert str(second) in result.detail


def test_bt_panel_wrapper_audit_ignores_commented_legacy_entry(tmp_path) -> None:
    wrapper = tmp_path / "daily"
    wrapper.write_text(
        "# /bin/bash /opt/aqsp/scripts/daily_run.sh\n"
        "/bin/bash /opt/aqsp/scripts/release_task_entrypoint.sh daily\n",
        encoding="utf-8",
    )

    result = check_scheduler.check_bt_panel_wrapper_integrity(tmp_path)

    assert result.ok is True
    assert result.detail == "scheduled actions: daily"


def test_scheduler_main_strict_schedule_ignores_missing_runtime_logs(
    monkeypatch, capsys
) -> None:
    passing = CheckResult("schedule", True, "ok")
    missing_log = CheckResult("runtime log", False, "not written yet")
    monkeypatch.setenv("AQSP_SCHEDULER_STRICT_SCHEDULE", "true")
    monkeypatch.delenv("AQSP_SCHEDULER_STRICT", raising=False)
    for name in (
        "check_project_root",
        "check_python_import",
        "check_bt_script",
        "check_crontab",
        "check_cron_lock_collisions",
        "check_bt_panel_actions",
        "check_duplicate_bt_panel_actions",
        "check_bt_panel_wrapper_integrity",
    ):
        monkeypatch.setattr(check_scheduler, name, lambda: passing)
    monkeypatch.setattr(check_scheduler, "check_logs", lambda: [missing_log])
    monkeypatch.setattr(check_scheduler, "check_locks", lambda: [])

    assert check_scheduler.main() == 0
    assert "runtime log" in capsys.readouterr().out


def test_scheduler_main_strict_schedule_rejects_unsafe_wrapper(monkeypatch) -> None:
    passing = CheckResult("schedule", True, "ok")
    unsafe = CheckResult("BT Panel wrapper audit", False, "duplicate coldstart")
    monkeypatch.setenv("AQSP_SCHEDULER_STRICT_SCHEDULE", "true")
    monkeypatch.delenv("AQSP_SCHEDULER_STRICT", raising=False)
    for name in (
        "check_project_root",
        "check_python_import",
        "check_bt_script",
        "check_crontab",
        "check_cron_lock_collisions",
        "check_bt_panel_actions",
        "check_duplicate_bt_panel_actions",
    ):
        monkeypatch.setattr(check_scheduler, name, lambda: passing)
    monkeypatch.setattr(
        check_scheduler, "check_bt_panel_wrapper_integrity", lambda: unsafe
    )
    monkeypatch.setattr(check_scheduler, "check_logs", lambda: [])
    monkeypatch.setattr(check_scheduler, "check_locks", lambda: [])

    assert check_scheduler.main() == 1


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
