from __future__ import annotations

import app as app_module
from fastapi.testclient import TestClient

import signal_definitions


def test_signal_definitions_api_persists_revisions_and_isolates_tenants(monkeypatch, tmp_path):
    monkeypatch.setattr(signal_definitions.pf, "CACHE_DIR", str(tmp_path))
    client = TestClient(app_module.app)
    owner = {"X-User-Id": "condition-signal-owner"}
    other = {"X-User-Id": "condition-signal-other"}
    payload = {
        "id": "close_above_previous",
        "name": "收盘高于昨收",
        "kind": "entry",
        "enabled": True,
        "conditions": [{"left": "close", "op": ">", "right": "field:prev_close"}],
    }

    options = client.get("/api/aqsp/signal-definitions/options", headers=owner)
    assert options.status_code == 200
    assert options.json()["data"]["consumer_status"] == "monitor_snapshot_and_historical_scan"

    created = client.post("/api/aqsp/signal-definitions", json=payload, headers=owner)
    assert created.status_code == 200
    assert created.json()["data"]["revision"] == 1
    assert client.get("/api/aqsp/signal-definitions", headers=owner).json()["data"]["items"]
    assert client.get("/api/aqsp/signal-definitions", headers=other).json()["data"]["items"] == []

    updated = client.post(
        "/api/aqsp/signal-definitions",
        json={**payload, "name": "修订名称", "expected_revision": 1},
        headers=owner,
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["revision"] == 2
    stale = client.post(
        "/api/aqsp/signal-definitions",
        json={**payload, "expected_revision": 1},
        headers=owner,
    )
    assert stale.status_code == 409

    deleted = client.delete(
        "/api/aqsp/signal-definitions/close_above_previous?expected_revision=2",
        headers=owner,
    )
    assert deleted.status_code == 200
    assert client.get("/api/aqsp/signal-definitions", headers=owner).json()["data"]["items"] == []


def test_signal_definitions_api_rejects_unsupported_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(signal_definitions.pf, "CACHE_DIR", str(tmp_path))
    client = TestClient(app_module.app)
    response = client.post(
        "/api/aqsp/signal-definitions",
        json={
            "id": "unsafe",
            "name": "unsafe",
            "kind": "entry",
            "enabled": True,
            "conditions": [{"left": "future_return", "op": ">", "right": 0}],
        },
    )
    assert response.status_code == 422
