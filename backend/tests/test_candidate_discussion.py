from __future__ import annotations

import json
import asyncio
from datetime import datetime

import pytest

import candidate_discussion as discussion
import candidate_observations as observations
import tenant
import app as app_module
from fastapi.testclient import TestClient


NOW = datetime.fromisoformat("2026-10-30T10:00:00+08:00")
CANDIDATE = {
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
QUOTE = {"price": 1500.0, "as_of": "2026-10-30T09:55:00+08:00", "source": "test"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(observations.pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(discussion, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(discussion.aqsp_bridge, "candidate_payload", lambda symbol, date=None: dict(CANDIDATE))
    monkeypatch.setattr(discussion.astock, "tencent_quote", lambda codes: {code: dict(QUOTE) for code in codes})
    monkeypatch.setattr(discussion, "resolve_endpoint", lambda config: discussion.ModelEndpoint("http://model", "secret", "test-model"))
    monkeypatch.setattr(discussion, "discussion_limits", lambda: discussion.DiscussionLimits(synthesis_timeout=1.0))
    token = tenant.current_tenant.set("local")
    yield
    tenant.current_tenant.reset(token)


def test_candidate_discussion_persists_exact_source_and_actions(store, monkeypatch):
    async def fake_call(endpoint, payload, timeout, limits, *, system_prompt):
        assert payload["allowed_evidence_ids"] == ["candidate_debate", "candidate_snapshot", "quote_current"]
        return json.dumps({
            "conclusion": "先观察放量确认",
            "evidence_ids": ["candidate_snapshot", "quote_current"],
            "actions": [{"action": "observe", "price_ref": "current", "condition": "放量后复核趋势", "invalidation": "跌回区间则失效"}],
            "missing_evidence": ["成交量连续性"],
        }, ensure_ascii=False)

    monkeypatch.setattr(discussion, "call_model", fake_call)
    result = asyncio.run(discussion.run_candidate_discussion("600519"))
    assert result["status"] == "complete"
    assert result["source_snapshot_id"].startswith("candidate-snapshot:")
    assert result["source_as_of"] == QUOTE["as_of"]
    assert observations.get_analysis(result["analysis_id"])["symbol"] == "600519"


def test_candidate_discussion_rejects_historical_date_before_model_call(store):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(discussion.aqsp_bridge, "load_surface", lambda: type("S", (), {"current": type("C", (), {"selected_date": "2026-10-30"})()})())
    try:
        with pytest.raises(discussion.CandidateDiscussionError, match="历史快照"):
            asyncio.run(discussion.run_candidate_discussion("600519", "2026-10-29"))
    finally:
        monkeypatch.undo()


def test_candidate_discussion_rejects_symbol_already_in_portfolio(store, monkeypatch):
    monkeypatch.setattr(discussion.pf, "_load", lambda: {"holdings": [{"code": "600519"}]})
    with pytest.raises(discussion.CandidateDiscussionError, match="持仓中"):
        asyncio.run(discussion.run_candidate_discussion("600519"))


def test_candidate_discussion_api_passes_current_candidate_request(monkeypatch):
    async def fake_run(symbol, selected_date, config, server_model):
        return {"symbol": symbol, "selected_date": selected_date, "status": "failed"}

    monkeypatch.setattr(discussion, "run_candidate_discussion", fake_run)
    response = TestClient(app_module.app).post(
        "/api/candidate-observations/analyze",
        json={"symbol": "600519", "date": "2026-10-30"},
    )
    assert response.status_code == 200
    assert response.json()["data"] == {
        "symbol": "600519", "selected_date": "2026-10-30", "status": "failed"
    }


def test_candidate_discussion_rejects_unknown_model_citation(store, monkeypatch):
    async def fake_call(endpoint, payload, timeout, limits, *, system_prompt):
        return json.dumps({
            "conclusion": "不确定",
            "evidence_ids": ["invented"],
            "actions": [],
            "missing_evidence": [],
        })

    monkeypatch.setattr(discussion, "call_model", fake_call)
    result = asyncio.run(discussion.run_candidate_discussion("600519"))
    assert result["status"] == "failed"
    assert not observations.list_observations()
