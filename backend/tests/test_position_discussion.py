"""Synthetic model transport, discussion rounds and position mutation regressions."""
from __future__ import annotations

import asyncio
import fcntl
import json
from pathlib import Path

import httpx
import pytest

import alert_events as ae
import portfolio as pf
import position_discussion as discussion
from test_position_action_plan import plan_client  # noqa: F401



OPINION = {"summary": "原有亏损不能证明此刻适合回补，先核对观察条件。", "evidence_ids": ["quote", "holding"],
           "uncertainties": ["缺少有效板块快照"], "actions": [{"action": "observe", "price_ref": "price_0", "condition": "观察低点附近是否止跌", "invalidation": "继续破低则取消"}],
           "reply_to": [], "challenge": ""}
SUMMARY = {"conclusion": "证据不足，保留做 T 条件观察，不能将亏损自动转成买入理由。", "agreements": ["必须先核对费用"],
           "disagreements": ["对反弹持续性尚无共识"], "missing_evidence": ["当日板块快照"], "actions": OPINION["actions"]}


@pytest.fixture
def discussion_client(plan_client, monkeypatch):  # noqa: F811
    client, event_id = plan_client
    monkeypatch.setattr(discussion, "resolve_endpoint", lambda config: discussion.ModelEndpoint("https://model.invalid/chat/completions", "private-key", "test-model"))
    calls = []
    async def fake_model(endpoint, payload, timeout, limits):
        calls.append(payload)
        if "rounds" in payload:
            return json.dumps(SUMMARY, ensure_ascii=False)
        peers = payload["peer_opinions"]
        return json.dumps({**OPINION, "reply_to": list(peers)[:1], "challenge": "同意需核对费用，但缺少板块数据不能确认方向" if peers else ""}, ensure_ascii=False)
    monkeypatch.setattr(discussion, "call_model", fake_model)
    client.post(f"/api/alerts/{event_id}/plan", json={"choice": "t_watch"})
    worksheet = client.get(f"/api/alerts/{event_id}/action-plan").json()["data"]
    return client, event_id, worksheet, calls


def test_position_discussion_forms_peer_replies_when_models_return_valid_evidence(discussion_client) -> None:
    client, event_id, worksheet, calls = discussion_client
    before = pf._load()
    response = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"]})
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["status"] == "complete"
    assert len(calls) == 7
    assert all(not row["peer_opinions"] for row in calls[:3])
    assert all(len(row["peer_opinions"]) == 2 for row in calls[3:6])
    assert result["summary"]["disagreements"] == SUMMARY["disagreements"]
    assert "private-key" not in response.text
    saved = client.get(f"/api/alerts/{event_id}/action-plan").json()["data"]["review_plan"]["discussion"]
    assert saved == result
    assert pf._load() == before and ae.list_events()[0]["status"] == "OPEN"


def test_position_discussion_does_not_claim_consensus_when_all_models_fail(discussion_client, monkeypatch) -> None:
    client, event_id, worksheet, calls = discussion_client
    async def failure(*args):
        raise TimeoutError("upstream contains private-key")
    monkeypatch.setattr(discussion, "call_model", failure)
    response = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"]})
    result = response.json()["data"]
    assert result["status"] == "failed" and result["summary"] is None
    assert len(result["rounds"]) == 1 and "private-key" not in response.text


def test_position_discussion_keeps_partial_results_when_one_role_fails(discussion_client, monkeypatch) -> None:
    client, event_id, worksheet, calls = discussion_client
    original = discussion.call_model
    async def partly_failed(endpoint, payload, timeout, limits):
        if payload.get("role") == discussion.ROLES["risk"][1]:
            raise TimeoutError()
        return await original(endpoint, payload, timeout, limits)
    monkeypatch.setattr(discussion, "call_model", partly_failed)
    result = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"]}).json()["data"]
    assert result["status"] == "partial" and result["summary"]
    assert len(result["rounds"][1]["opinions"]) == 2


@pytest.mark.parametrize("mutation", ["position", "choice"])
def test_position_discussion_rejects_old_result_when_state_changes(discussion_client, monkeypatch, mutation: str) -> None:
    client, event_id, worksheet, calls = discussion_client
    original = discussion.call_model
    async def changed(endpoint, payload, timeout, limits):
        if "rounds" in payload:
            if mutation == "position":
                data = pf._load()
                data["holdings"][0]["shares"] += 100
                pf._save(data)
            else:
                ae.record_review_plan(event_id, "hold")
        return await original(endpoint, payload, timeout, limits)
    monkeypatch.setattr(discussion, "call_model", changed)
    result = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"]})
    assert result.status_code == 409 and "已变化" in result.text
    assert "discussion" not in ae.list_events()[0]["review_plan"]


def test_position_discussion_refuses_foreign_tenant_before_model_call(discussion_client) -> None:
    client, event_id, worksheet, calls = discussion_client
    response = client.post(f"/api/alerts/{event_id}/discussion", headers={"X-User-Id": "other"}, json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"]})
    assert response.status_code == 409 and not calls


def test_position_discussion_refuses_parallel_requests_when_lease_held(discussion_client) -> None:
    client, event_id, worksheet, calls = discussion_client
    with (Path(ae.CACHE_DIR) / ".position-discussion.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        response = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"]})
    assert response.status_code == 409 and "进行中" in response.text and not calls


@pytest.mark.parametrize("patch,peers,guards", [
    ({"evidence_ids": ["invented"]}, set(), {"pause_t": False, "sellable_shares": 1000}),
    ({"actions": [{**OPINION["actions"][0], "price_ref": "invented"}]}, set(), {"pause_t": False, "sellable_shares": 1000}),
    ({"actions": [{**OPINION["actions"][0], "action": "buy_review"}]}, set(), {"pause_t": True, "sellable_shares": 1000}),
    ({"actions": [{**OPINION["actions"][0], "action": "sell_review"}]}, set(), {"pause_t": False, "sellable_shares": 0}),
    ({}, {"risk"}, {"pause_t": False, "sellable_shares": 1000}),
])
def test_position_discussion_rejects_unsupported_output_when_model_breaks_contract(patch: dict, peers: set, guards: dict) -> None:
    with pytest.raises(ValueError):
        discussion._parse(json.dumps({**OPINION, **patch}), discussion.RoleOpinion, {"quote": {}, "holding": {}}, {"price_0": {}}, peers, guards)


def test_position_model_transport_uses_bounded_real_http_contract_when_endpoint_returns_json(monkeypatch) -> None:
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(OPINION)}}]})
    transport = httpx.MockTransport(handler)
    client_class = httpx.AsyncClient
    monkeypatch.setattr(discussion.httpx, "AsyncClient", lambda **kwargs: client_class(transport=transport, **kwargs))
    output = asyncio.run(discussion.call_model(discussion.ModelEndpoint("https://model.invalid/v1/chat/completions", "private-key", "test-model"), {"data": "test"}, 1, discussion.DiscussionLimits()))
    assert json.loads(output)["summary"] == OPINION["summary"]
    payload = json.loads(seen[0].content)
    assert payload["model"] == "test-model" and "tools" not in payload
    assert seen[0].headers["Authorization"] == "Bearer private-key"


def test_position_model_transport_cancels_stuck_request_when_deadline_passes(monkeypatch) -> None:
    cancelled = []
    async def handler(request):
        try:
            await asyncio.sleep(5)
        finally:
            cancelled.append(True)
    transport = httpx.MockTransport(handler)
    client_class = httpx.AsyncClient
    monkeypatch.setattr(discussion.httpx, "AsyncClient", lambda **kwargs: client_class(transport=transport, **kwargs))
    with pytest.raises(TimeoutError):
        asyncio.run(discussion.call_model(discussion.ModelEndpoint("https://model.invalid/v1/chat/completions", "key", "model"), {}, 0.01, discussion.DiscussionLimits()))
    assert cancelled


def test_position_discussion_rejects_invalidated_scenario_before_model_call(discussion_client) -> None:
    client, event_id, worksheet, calls = discussion_client
    from test_position_action_plan import PARAMS
    client.post(f"/api/alerts/{event_id}/plan", json={"choice": "t_watch", "parameters": PARAMS, "expected_position": worksheet["position_snapshot"]})
    data = pf._load()
    data["holdings"][0]["shares"] = 200
    data["holdings"][0]["lots"] = [{"shares": 200, "opened_at": "2026-10-22"}]
    pf._save(data)
    changed = client.get(f"/api/alerts/{event_id}/action-plan").json()["data"]
    response = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": changed["position_snapshot"]})
    assert response.status_code == 409 and "失效" in response.text and not calls


def test_position_discussion_uses_anthropic_v1_messages_when_default_sdk_base_has_no_version(monkeypatch) -> None:
    from types import SimpleNamespace
    from aqsp.utils import llm_safe
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(llm_safe, "_make_client", lambda: SimpleNamespace(base_url="https://api.anthropic.com", api_key="key", close=lambda: None))
    assert discussion.resolve_endpoint(None).url == "https://api.anthropic.com/v1/messages"


def test_position_discussion_hides_old_analysis_when_position_changes_after_save(discussion_client) -> None:
    client, event_id, worksheet, calls = discussion_client
    assert client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"]}).status_code == 200
    data = pf._load()
    data["holdings"][0]["cost"] += 1
    pf._save(data)
    changed = client.get(f"/api/alerts/{event_id}/action-plan").json()["data"]
    assert changed["review_plan"]["discussion"] is None


def test_position_discussion_reports_safe_http_status_when_provider_rejects_request(discussion_client, monkeypatch) -> None:
    client, event_id, worksheet, calls = discussion_client
    async def limited(*args):
        raise discussion.ModelHTTPError(429)
    monkeypatch.setattr(discussion, "call_model", limited)
    response = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"]})
    result = response.json()["data"]
    assert result["status"] == "failed" and result["summary"] is None
    assert all("HTTP 429" in row["issue"] for row in result["rounds"][0]["opinions"])
