from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import app as app_module
import daily_data_refresh
import daily_data_refresh_worker


def test_daily_data_refresh_status_reports_partial_when_only_one_symbol_has_target_day_data(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "raw.sqlite"
    db_path.touch()
    expected = date(2026, 11, 2)
    cursor_path = tmp_path / "cursor.json"
    state_path = tmp_path / "job.json"
    monkeypatch.setattr(daily_data_refresh, "resolve_sqlite_db_path", lambda: str(db_path))
    monkeypatch.setattr(daily_data_refresh, "_cursor_path", lambda: cursor_path)
    monkeypatch.setattr(daily_data_refresh, "_state_path", lambda: state_path)
    monkeypatch.setattr(daily_data_refresh, "now_shanghai", lambda: datetime.fromisoformat("2026-11-02T18:00:00+08:00"))
    monkeypatch.setattr(daily_data_refresh, "latest_completed_trading_day", lambda _now: expected)
    monkeypatch.setattr(daily_data_refresh, "get_previous_trading_day", lambda _day: date(2026, 10, 30))

    class FakeSource:
        def __init__(self, *, db_path, cache):
            assert db_path == str(db_path_expected)
            assert cache is None

        def get_latest_daily_date(self):
            return expected

        def get_symbols_with_daily_coverage(self, symbols, start, end, *, min_rows, min_coverage_ratio):
            assert symbols == ["000001", "000002"]
            assert start == end == expected
            assert min_rows == 1
            assert min_coverage_ratio == 1.0
            return ["000001"]

    db_path_expected = str(db_path)
    monkeypatch.setattr(daily_data_refresh, "SqliteDbSource", FakeSource)
    monkeypatch.setattr(
        daily_data_refresh,
        "refreshable_daily_universe",
        lambda *_args, **_kwargs: ["000001", "000002"],
    )

    result = daily_data_refresh.daily_refresh_status()

    assert result["availability"] == "partial"
    assert result["latest_as_of"] == "2026-11-02"
    assert result["covered_symbols"] == 1
    assert result["eligible_symbols"] == 2
    assert result["coverage_ratio"] == 0.5


def test_daily_data_refresh_start_rejects_duplicate_live_job(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "raw.sqlite"
    db_path.touch()
    state_path = tmp_path / "job.json"
    state_path.write_text(json.dumps({"status": "running", "pid": os.getpid()}), encoding="utf-8")
    monkeypatch.setattr(daily_data_refresh, "resolve_sqlite_db_path", lambda: str(db_path))
    monkeypatch.setattr(daily_data_refresh, "_state_path", lambda: state_path)

    try:
        daily_data_refresh.start_daily_refresh()
    except daily_data_refresh.RefreshConflict:
        pass
    else:
        raise AssertionError("duplicate live refresh should be rejected")


def test_daily_data_refresh_api_exposes_status_and_start(monkeypatch):
    monkeypatch.setattr(daily_data_refresh, "daily_refresh_status", lambda: {"availability": "stale"})
    monkeypatch.setattr(
        daily_data_refresh,
        "start_daily_refresh",
        lambda: {"run_id": "run-1", "status": "starting", "target_day": "2026-11-02"},
    )
    client = TestClient(app_module.app)

    status = client.get("/api/aqsp/daily-data-refresh")
    started = client.post("/api/aqsp/daily-data-refresh", json={})

    assert status.status_code == 200
    assert status.json()["data"]["availability"] == "stale"
    assert started.status_code == 200
    assert started.json()["data"]["run_id"] == "run-1"


def test_daily_data_refresh_api_rejects_global_write_from_nonlocal_tenant(monkeypatch):
    monkeypatch.setattr(
        daily_data_refresh,
        "start_daily_refresh",
        lambda: pytest.fail("nonlocal tenant must not start a global refresh"),
    )

    response = TestClient(app_module.app).post(
        "/api/aqsp/daily-data-refresh",
        json={},
        headers={"X-User-Id": "separate-tenant"},
    )

    assert response.status_code == 403


def test_daily_data_refresh_worker_persists_partial_coverage_after_bounded_run(
    tmp_path: Path, monkeypatch
):
    state_path = tmp_path / ".state" / "job.json"
    cursor_path = tmp_path / ".state" / "cursor.json"
    state_path.parent.mkdir(parents=True)
    run_id = "run-1"
    state_path.write_text(json.dumps({"run_id": run_id, "pid": os.getpid(), "status": "starting"}), encoding="utf-8")
    cursor_path.write_text(
        json.dumps({
            "target_day": "2026-11-02",
            "universe_size": 2,
            "eligible_covered_count": 1,
            "eligible_coverage_pct": 0.5,
            "last_batch": {"processed_symbols": 2, "updated_rows": 1},
        }),
        encoding="utf-8",
    )

    class FinishedChild:
        pid = 456

        def __init__(self):
            self.poll_count = 0

        def poll(self):
            self.poll_count += 1
            return None if self.poll_count == 1 else 0

        def wait(self):
            return 0

    monkeypatch.setattr(daily_data_refresh_worker.subprocess, "Popen", lambda *_args, **_kwargs: FinishedChild())
    monkeypatch.setattr(daily_data_refresh_worker.time, "sleep", lambda _seconds: None)

    result = daily_data_refresh_worker.run_worker(
        run_id=run_id,
        target_day="2026-11-02",
        db_path=tmp_path / "raw.sqlite",
        cursor_path=cursor_path,
        state_path=state_path,
    )

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert result == 0
    assert payload["status"] == "partial"
    assert payload["eligible_covered_count"] == 1
    assert payload["eligible_coverage_pct"] == 0.5


def test_sqlite_daily_refresh_cli_rejects_overlapping_batch_lock(tmp_path: Path):
    from scripts.data import refresh_sqlite_batch

    state_path = tmp_path / "cursor.json"

    with refresh_sqlite_batch._exclusive_refresh_lock(state_path):
        with pytest.raises(SystemExit, match="already running"):
            with refresh_sqlite_batch._exclusive_refresh_lock(state_path):
                pass
