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
