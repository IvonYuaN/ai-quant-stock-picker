from __future__ import annotations

import app as app_module
from fastapi.testclient import TestClient

import abnormal_history


def test_abnormal_historical_signals_api_returns_the_source_stamped_snapshot(
    monkeypatch,
):
    expected = {
        "source": "sqlite_db",
        "price_mode": "raw",
        "as_of": "2026-09-30",
        "expected_as_of": "2026-11-02",
        "freshness": "stale",
        "lookback_sessions": 60,
        "counts": {"new_high": 1, "new_low": 0, "volume_surge": 1},
        "rows": [{"symbol": "000001", "signals": ["new_high", "volume_surge"]}],
    }
    monkeypatch.setattr(abnormal_history, "scan_local_daily_signals", lambda: expected)

    response = TestClient(app_module.app).get("/api/aqsp/abnormal-historical-signals")

    assert response.status_code == 200
    assert response.json()["data"] == expected


def test_abnormal_historical_signals_api_hides_unavailable_raw_source(monkeypatch):
    def unavailable():
        raise FileNotFoundError("private db path")

    monkeypatch.setattr(abnormal_history, "scan_local_daily_signals", unavailable)

    response = TestClient(app_module.app).get("/api/aqsp/abnormal-historical-signals")

    assert response.status_code == 503
    assert "没有生成当前异动结果" in response.json()["detail"]
    assert "private db path" not in response.json()["detail"]
