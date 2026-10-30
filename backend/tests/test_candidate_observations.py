from __future__ import annotations

from datetime import datetime

import pytest

import candidate_observations as observations
import candidate_discussion
import tenant
import app as app_module
from fastapi.testclient import TestClient


NOW = datetime.fromisoformat("2026-10-30T10:00:00+08:00")
PAYLOAD = {
    "symbol": "600519",
    "name": "贵州茅台",
    "price": 1500.0,
    "comparator": "at_or_above",
    "condition": "放量站上观察位后复核趋势是否延续",
    "invalidation": "跌回区间下沿则本条件失效",
    "source_analysis_id": "analysis-123",
    "source_generated_at": "2026-10-30T09:59:30+08:00",
    "source_snapshot_id": "",
    "source_as_of": "2026-10-30T09:59:00+08:00",
}

SNAPSHOT = {
    "symbol": "600519",
    "display_name": "贵州茅台",
    "date": "2026-10-30",
    "score": 82.0,
    "research_status": "ready",
    "evidence_status": "complete",
    "deterministic_reasons": ["趋势向上"],
    "strategies": ["momentum"],
    "score_breakdown": ["动量 +"],
    "data_source": "runtime",
    "data_fetched_at": "2026-10-30T09:50:00+08:00",
    "debate": {"conclusion": "等待价位确认", "watch_items": ["成交量"]},
}
PAYLOAD["source_snapshot_id"] = candidate_discussion._snapshot_id(SNAPSHOT)


def register_for(payload: dict) -> None:
    observations.register_analysis({
        "analysis_id": payload["source_analysis_id"],
        "symbol": payload["symbol"],
        "source_snapshot_id": payload["source_snapshot_id"],
        "source_as_of": payload["source_as_of"],
        "generated_at": payload["source_generated_at"],
        "source_price": payload["price"],
        "status": "complete",
        "actions": [{"action": "observe", "price_ref": "current", "condition": payload["condition"], "invalidation": payload["invalidation"]}],
    })


@pytest.fixture
def candidate_store(tmp_path, monkeypatch):
    monkeypatch.setattr(observations.pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(observations, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(candidate_discussion.aqsp_bridge, "candidate_payload", lambda symbol, date=None: dict(SNAPSHOT))
    monkeypatch.setattr("decision_context.position_quote_issue", lambda quote, checked_at=None: "")
    token = tenant.current_tenant.set("local")
    yield
    tenant.current_tenant.reset(token)


def test_candidate_observation_adoption_stop_and_audit(candidate_store):
    register_for(PAYLOAD)
    item = observations.adopt_observation(PAYLOAD)
    assert item["state"] == "active"
    assert item["revision"] == 1
    assert observations.list_observations() == [item]

    updated_payload = {**PAYLOAD, "price": 1510.0}
    register_for(updated_payload)
    updated = observations.adopt_observation(updated_payload, expected_version=1)
    assert updated["revision"] == 2
    assert updated["audit"][-1]["previous"]["price"] == 1500.0
    with pytest.raises(ValueError, match="变化"):
        observations.adopt_observation(PAYLOAD, expected_version=1)

    stopped = observations.stop_observation(updated["id"], expected_version=2)
    assert stopped is not None and stopped["state"] == "stopped"
    assert stopped["revision"] == 3
    assert stopped["audit"][-1]["action"] == "stop"


def test_candidate_observation_rejects_bad_source_and_price(candidate_store):
    with pytest.raises(ValueError, match="時區|时区"):
        observations.adopt_observation({**PAYLOAD, "source_as_of": "2026-10-30T09:55:00"})
    with pytest.raises(ValueError, match="有效正数"):
        observations.adopt_observation({**PAYLOAD, "price": float("nan")})


def test_candidate_observation_monitor_records_quote_without_changing_intent(candidate_store, monkeypatch):
    register_for(PAYLOAD)
    item = observations.adopt_observation(PAYLOAD)
    monkeypatch.setattr("decision_context.position_quote_issue", lambda quote, checked_at: "")
    result = observations.check_observations(
        {"600519": {"price": 1501.0, "as_of": NOW.isoformat()}}, checked_at=NOW
    )
    assert result["checked"][0]["triggered"] is True
    persisted = observations.list_observations()[0]
    assert persisted["revision"] == item["revision"]
    assert persisted["last_check"]["quote_as_of"] == NOW.isoformat()


def test_candidate_observation_pauses_price_check_when_symbol_becomes_holding(candidate_store):
    register_for(PAYLOAD)
    observations.adopt_observation(PAYLOAD)
    result = observations.check_observations(
        {"600519": {"price": 1501.0, "as_of": NOW.isoformat()}},
        checked_at=NOW,
        held_symbols={"600519"},
    )
    assert result["checked"] == []
    assert "已转为持仓" in result["skipped"][0]["reason"]
    assert observations.list_observations()[0]["state"] == "active"


def test_candidate_observation_is_tenant_isolated(candidate_store, monkeypatch):
    register_for(PAYLOAD)
    observations.adopt_observation(PAYLOAD)
    token = tenant.current_tenant.set("u_0123456789abcdef0123")
    try:
        assert observations.list_observations() == []
        snapshot = {**SNAPSHOT, "symbol": "000001"}
        other = {**PAYLOAD, "symbol": "000001", "source_analysis_id": "analysis-other", "source_snapshot_id": candidate_discussion._snapshot_id(snapshot)}
        monkeypatch.setattr(candidate_discussion.aqsp_bridge, "candidate_payload", lambda symbol, date=None: dict(snapshot))
        register_for(other)
        observations.adopt_observation(other)
    finally:
        tenant.current_tenant.reset(token)
    assert [item["symbol"] for item in observations.list_observations()] == ["600519"]


def test_candidate_observation_api_adopts_lists_and_stops_with_cas(candidate_store):
    register_for(PAYLOAD)
    client = TestClient(app_module.app)
    created = client.post("/api/candidate-observations", json=PAYLOAD)
    assert created.status_code == 200
    item = created.json()["data"]
    assert item["source_analysis_id"] == "analysis-123"
    assert client.get("/api/candidate-observations").json()["data"]["items"] == [item]

    stopped = client.post(
        f"/api/candidate-observations/{item['id']}/stop",
        json={"expected_version": 1},
    )
    assert stopped.status_code == 200
    assert stopped.json()["data"]["state"] == "stopped"
    stale = client.post(
        f"/api/candidate-observations/{item['id']}/stop",
        json={"expected_version": 1},
    )
    assert stale.status_code == 409


def test_candidate_only_tenant_is_checked_by_existing_monitor(candidate_store, monkeypatch):
    import astock
    import live_risk_service

    register_for(PAYLOAD)
    observations.adopt_observation(PAYLOAD)
    monkeypatch.setattr("decision_context.position_quote_issue", lambda quote, checked_at: "")
    monkeypatch.setattr(
        astock,
        "tencent_quote",
        lambda codes: {
            code: {"price": 1501.0, "as_of": NOW.isoformat(), "source": "test"}
            for code in codes
        },
    )
    summary = live_risk_service.check_existing_tenants(notify=False)
    assert summary["tenants_checked"] == 1
    item = observations.list_observations()[0]
    assert item["last_check"]["triggered"] is True
