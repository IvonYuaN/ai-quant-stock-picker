from __future__ import annotations

import json
import os
import time
from datetime import date

import app as app_module
from fastapi.testclient import TestClient


def _write_history(root, *, ready: bool = True) -> None:
    factor_dir = root / "pit_cache" / "factor_ic"
    factor_dir.mkdir(parents=True)
    (factor_dir / "ic_history.jsonl").write_text(
        json.dumps({"as_of": "2026-10-30", "run_at": "2026-10-30T08:00:00Z", "factors": {"momentum": 0.1}}) + "\n",
        encoding="utf-8",
    )
    if ready:
        (factor_dir / "IC_READY").write_text("ok\n", encoding="utf-8")


def test_ic_history_endpoint_marks_missing_ready_as_unverified(monkeypatch, tmp_path) -> None:
    _write_history(tmp_path, ready=False)
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    response = TestClient(app_module.app).get("/api/aqsp/ic-history")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["readiness"]["status"] == "unverified"
    assert data["points"]


def test_ic_history_endpoint_marks_empty_runtime_when_no_artifacts(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    response = TestClient(app_module.app).get("/api/aqsp/ic-history")
    assert response.status_code == 200
    assert response.json()["data"]["readiness"]["status"] == "missing"


def test_ic_history_endpoint_marks_old_ready_as_stale(monkeypatch, tmp_path) -> None:
    _write_history(tmp_path, ready=True)
    ready = tmp_path / "pit_cache" / "factor_ic" / "IC_READY"
    old = time.time() - 48 * 3600
    os.utime(ready, (old, old))
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MAX_AGE_HOURS", "36")

    response = TestClient(app_module.app).get("/api/aqsp/ic-history")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["readiness"]["status"] == "stale"
    assert data["points"]


def test_ic_history_endpoint_rejects_fresh_marker_with_stale_market_data(
    monkeypatch, tmp_path
) -> None:
    _write_history(tmp_path, ready=True)
    history = tmp_path / "pit_cache" / "factor_ic" / "ic_history.jsonl"
    history.write_text(
        json.dumps({
            "as_of": "2026-10-27",
            "run_at": "2026-10-31T00:30:00Z",
            "factors": {"momentum": 0.1},
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MAX_AGE_HOURS", "36")
    monkeypatch.setenv("IC_MAX_LAG_TRADING_DAYS", "1")
    monkeypatch.setattr(
        app_module,
        "_dual_verdict_data_threshold",
        lambda _max_lag: date(2026, 10, 29),
    )

    response = TestClient(app_module.app).get("/api/aqsp/ic-history")

    assert response.status_code == 200
    readiness = response.json()["data"]["readiness"]
    assert readiness["marker_status"] == "fresh"
    assert readiness["data_status"] == "stale"
    assert readiness["status"] == "stale"
    assert "窗口数据截至 2026-10-27" in readiness["reason"]


def test_ic_history_endpoint_requires_verifiable_window_date_when_ready_is_fresh(
    monkeypatch, tmp_path
) -> None:
    _write_history(tmp_path, ready=True)
    history = tmp_path / "pit_cache" / "factor_ic" / "ic_history.jsonl"
    history.write_text(
        json.dumps({
            "run_at": "2026-10-31T00:30:00Z",
            "factors": {"momentum": 0.1},
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))

    response = TestClient(app_module.app).get("/api/aqsp/ic-history")

    assert response.status_code == 200
    readiness = response.json()["data"]["readiness"]
    assert readiness["marker_status"] == "fresh"
    assert readiness["data_status"] == "unknown"
    assert readiness["status"] == "unverified"
