from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

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
        "conclusion": "候选结论",
        "evidence": {"candidate_snapshot": {"date": "2026-10-30"}},
        "personal_context": {
            "personal_intent": "验证量能承接",
            "time_horizon": "未来 3 个交易日",
            "personal_hypothesis": "放量后可能延续",
            "counterargument": "回落可能延续",
        },
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
    assert item["personal_context"]["counterargument"] == "回落可能延续"
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


def test_candidate_decision_persists_current_evidence_and_uses_revision_cas(candidate_store, monkeypatch):
    monkeypatch.setattr(candidate_discussion.aqsp_bridge, "load_surface", lambda: SimpleNamespace(
        current=SimpleNamespace(selected_date="2026-10-30")
    ))
    value = {
        "symbol": "600519",
        "selected_date": "2026-10-30",
        "choice": "defer",
        "note": "暂缓到下一交易日，先确认量能是否延续。",
        "personal_context": {
            "personal_intent": "验证量能承接",
            "time_horizon": "未来 1 个交易日",
            "personal_hypothesis": "放量后可能延续",
            "counterargument": "回落可能延续",
        },
    }
    first = observations.save_decision(value)
    assert first["revision"] == 1
    assert first["choice"] == "defer"
    assert first["candidate_snapshot"]["score"] == SNAPSHOT["score"]
    assert observations.list_decisions() == [first]

    updated = observations.save_decision({**value, "choice": "reject", "note": "当前证据不符合我的观察目的。"}, expected_version=1)
    assert updated["id"] == first["id"]
    assert updated["revision"] == 2
    assert updated["audit"][-1]["previous"]["choice"] == "defer"
    with pytest.raises(observations.CandidateObservationConflict, match="已在其他页面变化"):
        observations.save_decision(value, expected_version=1)


def test_candidate_decision_rejects_historical_or_unavailable_snapshot(candidate_store, monkeypatch):
    monkeypatch.setattr(candidate_discussion.aqsp_bridge, "load_surface", lambda: SimpleNamespace(
        current=SimpleNamespace(selected_date="2026-10-30")
    ))
    value = {
        "symbol": "600519", "selected_date": "2026-10-29", "choice": "reject",
        "note": "不符合当前关注方向。", "personal_context": {
            "personal_intent": "验证趋势", "time_horizon": "未来 2 日",
        },
    }
    with pytest.raises(observations.CandidateObservationConflict, match="当前候选快照"):
        observations.save_decision(value)


def test_candidate_observation_adoption_resolves_prior_decision(candidate_store, monkeypatch):
    monkeypatch.setattr(candidate_discussion.aqsp_bridge, "load_surface", lambda: SimpleNamespace(
        current=SimpleNamespace(selected_date="2026-10-30")
    ))
    decision = observations.save_decision({
        "symbol": "600519", "selected_date": "2026-10-30", "choice": "defer",
        "note": "暂缓，先观察量能。", "personal_context": {
            "personal_intent": "验证量能", "time_horizon": "未来 1 日",
        },
    })
    register_for(PAYLOAD)
    item = observations.adopt_observation(PAYLOAD)
    resolved = observations.list_decisions()[0]
    assert resolved["id"] == decision["id"]
    assert resolved["choice"] == "adopted"
    assert resolved["adopted_observation_id"] == item["id"]
    assert resolved["audit"][-1]["action"] == "observation_adopted"


def test_candidate_observation_saves_versioned_personal_outcome_review(candidate_store, monkeypatch):
    register_for(PAYLOAD)
    item = observations.adopt_observation(PAYLOAD)
    stopped = observations.stop_observation(item["id"], expected_version=1)
    assert stopped is not None
    monkeypatch.setattr(
        "trade_journal.read_journal",
        lambda symbol: {"receipts": [{"receipt_id": "receipt-1", "code": symbol, "date": "2026-10-30", "position_id": None}]},
    )
    reviewed = observations.save_review(item["id"], {
        "outcome": "condition_confirmed",
        "process_quality": "mixed",
        "lesson": "本人假设部分成立，量能证据仍需单独核对。",
        "receipt_ids": ["receipt-1"],
        "confirm_same_day_order": True,
        "confirm_cross_position": True,
    }, expected_version=2)
    assert reviewed is not None
    assert reviewed["revision"] == 3
    assert reviewed["review"]["receipt_ids"] == ["receipt-1"]
    assert reviewed["analysis_snapshot"]["conclusion"] == "候选结论"
    assert reviewed["audit"][-1]["action"] == "review"
    with pytest.raises(observations.CandidateObservationConflict, match="已变化"):
        observations.save_review(item["id"], {
            "outcome": "unclear",
            "process_quality": "unrated",
            "lesson": "旧版本不能覆盖新复盘。",
            "receipt_ids": [],
        }, expected_version=2)


def test_candidate_observation_rejects_receipt_from_other_code(candidate_store, monkeypatch):
    register_for(PAYLOAD)
    item = observations.adopt_observation(PAYLOAD)
    stopped = observations.stop_observation(item["id"], expected_version=1)
    monkeypatch.setattr(
        "trade_journal.read_journal",
        lambda symbol: {"receipts": [{"receipt_id": "receipt-other", "code": "000001"}]},
    )
    with pytest.raises(ValueError, match="成交凭据不存在"):
        observations.save_review(item["id"], {
            "outcome": "condition_confirmed",
            "process_quality": "sound",
            "lesson": "不得引用其他股票的成交。",
            "receipt_ids": ["receipt-other"],
        }, expected_version=stopped["revision"])


def test_candidate_observation_rejects_receipt_before_decision_date(candidate_store, monkeypatch):
    register_for(PAYLOAD)
    item = observations.adopt_observation(PAYLOAD)
    stopped = observations.stop_observation(item["id"], expected_version=1)
    monkeypatch.setattr(
        "trade_journal.read_journal",
        lambda symbol: {"receipts": [{"receipt_id": "receipt-old", "code": symbol, "date": "2026-10-29", "position_id": None}]},
    )
    with pytest.raises(ValueError, match="采用日期当日或之后"):
        observations.save_review(item["id"], {
            "outcome": "condition_confirmed",
            "process_quality": "sound",
            "lesson": "不应把决策前成交算作执行结果。",
            "receipt_ids": ["receipt-old"],
        }, expected_version=stopped["revision"])


def test_candidate_observation_requires_confirmation_for_same_day_receipt(candidate_store, monkeypatch):
    register_for(PAYLOAD)
    item = observations.adopt_observation(PAYLOAD)
    stopped = observations.stop_observation(item["id"], expected_version=1)
    monkeypatch.setattr(
        "trade_journal.read_journal",
        lambda symbol: {"receipts": [{"receipt_id": "receipt-same-day", "code": symbol, "date": "2026-10-30", "position_id": None}]},
    )
    with pytest.raises(ValueError, match="显式确认成交发生在候选采用之后"):
        observations.save_review(item["id"], {
            "outcome": "condition_confirmed",
            "process_quality": "sound",
            "lesson": "同日顺序必须人工确认。",
            "receipt_ids": ["receipt-same-day"],
        }, expected_version=stopped["revision"])


def test_candidate_observation_requires_confirmation_for_cross_position_receipt(candidate_store, monkeypatch):
    register_for(PAYLOAD)
    observations.adopt_observation(PAYLOAD)
    result = observations.check_observations({}, checked_at=NOW, held_positions={"600519": "position-new"})
    assert result["skipped"]
    transferred = observations.list_observations()[0]
    monkeypatch.setattr(
        "trade_journal.read_journal",
        lambda symbol: {"receipts": [{"receipt_id": "receipt-cross", "code": symbol, "date": "2026-10-31", "position_id": "position-other"}]},
    )
    with pytest.raises(ValueError, match="显式确认跨持仓关联"):
        observations.save_review(transferred["id"], {
            "outcome": "condition_confirmed",
            "process_quality": "mixed",
            "lesson": "必须对跨仓引用显式确认。",
            "receipt_ids": ["receipt-cross"],
        }, expected_version=transferred["revision"])


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


def test_candidate_observation_handoff_closes_price_check_when_legacy_symbol_becomes_holding(candidate_store):
    register_for(PAYLOAD)
    observations.adopt_observation(PAYLOAD)
    result = observations.check_observations(
        {"600519": {"price": 1501.0, "as_of": NOW.isoformat()}},
        checked_at=NOW,
        held_symbols={"600519"},
    )
    assert result["checked"] == []
    assert "已是持仓" in result["skipped"][0]["reason"]
    assert observations.list_observations()[0]["state"] == "transferred_unlinked"


def test_candidate_observation_handoff_closes_candidate_state_when_position_identity_exists(candidate_store):
    register_for(PAYLOAD)
    observations.adopt_observation(PAYLOAD)
    result = observations.check_observations(
        {}, checked_at=NOW, held_positions={"600519": "position-123"}
    )
    assert result["checked"] == []
    item = observations.list_observations()[0]
    assert item["state"] == "transferred"
    assert item["transferred_to_position_id"] == "position-123"
    assert item["audit"][-1]["action"] == "position_handoff"


def test_candidate_observation_handoff_closes_candidate_without_legacy_position_identity(candidate_store):
    register_for(PAYLOAD)
    observations.adopt_observation(PAYLOAD)
    result = observations.check_observations({}, checked_at=NOW, held_symbols={"600519"})
    assert result["checked"] == []
    item = observations.list_observations()[0]
    assert item["state"] == "transferred_unlinked"
    assert item["audit"][-1]["action"] == "position_handoff_unlinked"


def test_candidate_observation_refuses_terminal_outcome_while_monitor_is_active(candidate_store):
    register_for(PAYLOAD)
    item = observations.adopt_observation(PAYLOAD)
    with pytest.raises(ValueError, match="先停止或完成持仓交接"):
        observations.save_review(item["id"], {
            "outcome": "not_reached",
            "process_quality": "unrated",
            "lesson": "仍在观察时不能写终局结论。",
            "receipt_ids": [],
        }, expected_version=item["revision"])


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


def test_candidate_decisions_are_tenant_isolated(candidate_store, monkeypatch):
    monkeypatch.setattr(candidate_discussion.aqsp_bridge, "load_surface", lambda: SimpleNamespace(
        current=SimpleNamespace(selected_date="2026-10-30")
    ))
    observations.save_decision({
        "symbol": "600519", "selected_date": "2026-10-30", "choice": "defer",
        "note": "等事件材料补齐。", "personal_context": {
            "personal_intent": "核实事件", "time_horizon": "未来 3 日",
        },
    })
    token = tenant.current_tenant.set("u_0123456789abcdef0123")
    try:
        assert observations.list_decisions() == []
    finally:
        tenant.current_tenant.reset(token)
    assert len(observations.list_decisions()) == 1


def test_candidate_decision_rejects_active_observation_or_current_holding(candidate_store, monkeypatch):
    monkeypatch.setattr(candidate_discussion.aqsp_bridge, "load_surface", lambda: SimpleNamespace(
        current=SimpleNamespace(selected_date="2026-10-30")
    ))
    value = {
        "symbol": "600519", "selected_date": "2026-10-30", "choice": "defer",
        "note": "先等证据变化。", "personal_context": {
            "personal_intent": "验证承接", "time_horizon": "未来 1 日",
        },
    }
    register_for(PAYLOAD)
    observations.adopt_observation(PAYLOAD)
    with pytest.raises(observations.CandidateObservationConflict, match="进行中的观察条件"):
        observations.save_decision(value)

    observations.stop_observation(observations.list_observations()[0]["id"], expected_version=1)
    monkeypatch.setattr(observations.pf, "_load", lambda: {"holdings": [{"code": "600519"}]})
    with pytest.raises(observations.CandidateObservationConflict, match="已在持仓中"):
        observations.save_decision(value)


def test_candidate_observation_api_adopts_lists_and_stops_with_cas(candidate_store):
    register_for(PAYLOAD)
    client = TestClient(app_module.app)
    created = client.post("/api/candidate-observations", json=PAYLOAD)
    assert created.status_code == 200
    item = created.json()["data"]
    assert item["source_analysis_id"] == "analysis-123"
    payload = client.get("/api/candidate-observations").json()["data"]
    assert payload["items"] == [item]
    assert payload["decisions"] == []

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
    reviewed = client.put(
        f"/api/candidate-observations/{item['id']}/review",
        json={
            "expected_version": 2,
            "outcome": "not_reached",
            "process_quality": "unrated",
            "lesson": "观察期内没有到达条件。",
            "receipt_ids": [],
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["data"]["review"]["outcome"] == "not_reached"
    stale_review = client.put(
        f"/api/candidate-observations/{item['id']}/review",
        json={
            "expected_version": 2,
            "outcome": "unclear",
            "process_quality": "unrated",
            "lesson": "旧版本复盘。",
            "receipt_ids": [],
        },
    )
    assert stale_review.status_code == 409


def test_candidate_decision_api_saves_decision_and_exposes_tenant_list(candidate_store, monkeypatch):
    monkeypatch.setattr(candidate_discussion.aqsp_bridge, "load_surface", lambda: SimpleNamespace(
        current=SimpleNamespace(selected_date="2026-10-30")
    ))
    client = TestClient(app_module.app)
    body = {
        "symbol": "600519", "selected_date": "2026-10-30", "choice": "defer",
        "note": "先核对下一交易日的量能。", "personal_context": {
            "personal_intent": "验证承接", "time_horizon": "未来 1 日",
            "personal_hypothesis": "放量可能延续", "counterargument": "存在回落风险",
        },
    }
    created = client.post("/api/candidate-observations/decision", json=body)
    assert created.status_code == 200
    decision = created.json()["data"]
    assert decision["choice"] == "defer"
    assert client.get("/api/candidate-observations").json()["data"]["decisions"] == [decision]
    update = client.post("/api/candidate-observations/decision", json={**body, "choice": "reject", "expected_version": 1})
    assert update.status_code == 200
    stale = client.post("/api/candidate-observations/decision", json={**body, "choice": "defer", "expected_version": 1})
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
