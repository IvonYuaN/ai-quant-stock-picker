"""Monitor integration is bounded and reports incomplete checks without tenant data."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from aqsp.monitor import live_positions as monitor


@pytest.fixture
def monitor_store(tmp_path, monkeypatch):
    monkeypatch.setenv("VR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(monitor, "is_market_open", lambda current: True)
    (tmp_path / "portfolio.json").write_text('{"holdings": [{"code": "600000"}]}')
    return tmp_path


def test_live_position_monitor_does_not_run_when_dry_or_market_closed(
    monitor_store, monkeypatch
):
    def unexpected(*args, **kwargs):
        raise AssertionError("must not run subprocess")

    monkeypatch.setattr(monitor.subprocess, "run", unexpected)
    assert monitor.check_live_positions(dry_run=True) == []
    monkeypatch.setattr(monitor, "is_market_open", lambda current: False)
    assert monitor.check_live_positions() == []


def test_live_position_monitor_consumes_service_when_store_exists(
    monitor_store, monkeypatch
):
    captured = []

    def run(command, **kwargs):
        captured.append((command, kwargs))
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "tenants_checked": 1,
                    "open_events": 2,
                    "skipped_checks": 0,
                    "failed_tenants": 0,
                }
            )
        )

    monkeypatch.setattr(monitor.subprocess, "run", run)
    result = monitor.check_live_positions(notify=True)
    assert result[0].triggered is False
    assert "--notify" in captured[0][0]
    assert captured[0][1]["timeout"] > 0
    assert captured[0][1]["env"]["VR_DATA_DIR"] == str(monitor_store)
    assert "600000" not in result[0].message


@pytest.mark.parametrize(
    ("uid", "gid", "expected"),
    [(1200, 1300, {"user": 1200, "group": 1300, "extra_groups": []}), (0, 0, {})],
)
def test_live_position_monitor_uses_data_owner_when_running_as_root(
    monkeypatch, uid, gid, expected
):
    monkeypatch.setattr(monitor.os, "geteuid", lambda: 0)
    data_root = SimpleNamespace(stat=lambda: SimpleNamespace(st_uid=uid, st_gid=gid))
    assert monitor._subprocess_identity(data_root) == expected


def test_live_position_monitor_preserves_identity_when_running_as_nonroot(monkeypatch):
    monkeypatch.setattr(monitor.os, "geteuid", lambda: 1200)

    def unexpected():
        raise AssertionError("non-root must not inspect or override data owner")

    assert monitor._subprocess_identity(SimpleNamespace(stat=unexpected)) == {}


def test_live_position_monitor_forwards_credentials_when_owner_selected(
    monitor_store, monkeypatch
):
    credentials = {"user": 1200, "group": 1300, "extra_groups": []}
    monkeypatch.setattr(monitor, "_subprocess_identity", lambda path: credentials)
    captured = {}

    def run(command, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "tenants_checked": 1,
                    "open_events": 0,
                    "skipped_checks": 0,
                    "failed_tenants": 0,
                }
            )
        )

    monkeypatch.setattr(monitor.subprocess, "run", run)
    assert monitor.check_live_positions()[0].triggered is False
    assert {key: captured[key] for key in credentials} == credentials


def test_live_position_monitor_reports_failure_when_owner_unreadable(
    monitor_store, monkeypatch
):
    def unavailable(path):
        raise PermissionError("cannot inspect data root owner")

    def unexpected(*args, **kwargs):
        raise AssertionError("must not start service with an unknown identity")

    monkeypatch.setattr(monitor, "_subprocess_identity", unavailable)
    monkeypatch.setattr(monitor.subprocess, "run", unexpected)
    result = monitor.check_live_positions()
    assert result[0].triggered is True
    assert "未产生完整结论" in result[0].message


@pytest.mark.parametrize(
    "failure", [subprocess.TimeoutExpired("monitor", 1), OSError("denied")]
)
def test_live_position_monitor_reports_failure_when_service_unavailable(
    monitor_store, monkeypatch, failure
):
    def failed(*args, **kwargs):
        raise failure

    monkeypatch.setattr(monitor.subprocess, "run", failed)
    result = monitor.check_live_positions()
    assert result[0].triggered is True
    assert "未产生完整结论" in result[0].message


def test_live_position_monitor_reports_gaps_when_history_skipped(
    monitor_store, monkeypatch
):
    monkeypatch.setattr(
        monitor.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=json.dumps(
                {
                    "tenants_checked": 1,
                    "open_events": 0,
                    "skipped_checks": 2,
                    "failed_tenants": 0,
                }
            )
        ),
    )
    assert monitor.check_live_positions()[0].triggered is True


@pytest.mark.parametrize('tenant_folder', ['', 'users/u_' + 'a' * 20])
def test_live_position_monitor_runs_when_only_candidate_observation_exists(monitor_store, monkeypatch, tenant_folder):
    (monitor_store / 'portfolio.json').unlink()
    folder = monitor_store / tenant_folder
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'candidate-observations.json').write_text('{"schema_version":1,"resources":{}}')
    captured = []
    def run(command, **kwargs):
        captured.append((command, kwargs))
        return SimpleNamespace(stdout=json.dumps({'tenants_checked': 1, 'open_events': 0, 'skipped_checks': 0,
                               'failed_tenants': 0, 'candidate_checks': 1, 'candidate_notifications': 0}))
    monkeypatch.setattr(monitor.subprocess, 'run', run)
    result = monitor.check_live_positions()
    assert len(captured) == 1 and result[0].triggered is False
    assert '候选观察已检查 1 项' in result[0].message
