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
from test_position_action_plan import plan_body, record_plan, plan_client  # noqa: F401



OPINION = {"summary": "原有亏损不能证明此刻适合回补，先核对观察条件。", "evidence_ids": ["quote", "holding"],
           "uncertainties": ["缺少有效板块快照"], "actions": [{"action": "observe", "price_ref": "price_0", "condition": "观察低点附近是否止跌", "invalidation": "继续破低则取消"}],
           "reply_to": [], "challenge": ""}
SUMMARY = {"conclusion": "证据不足，保留做 T 条件观察，不能将亏损自动转成买入理由。", "agreements": ["必须先核对费用"],
           "disagreements": ["对反弹持续性尚无共识"], "missing_evidence": ["当日板块快照"], "actions": OPINION["actions"]}
COMPARISON = {**SUMMARY, "routes": [{"choice": choice, "judgment": "insufficient", "reason": "须结合本仓风险和当前证据核对", "evidence_ids": ["holding"], "needs": ["个人约束"]}
                                      for choice in ("hold", "reduce", "t_watch")], "focus_choice": None, "focus_reason": "缺少当前证据，暂不选定"}


@pytest.fixture
def discussion_client(plan_client, monkeypatch):  # noqa: F811
    client, event_id = plan_client
    monkeypatch.setattr(discussion, "resolve_endpoint", lambda config: discussion.ModelEndpoint("https://model.invalid/chat/completions", "private-key", "test-model"))
    calls = []
    async def fake_model(endpoint, payload, timeout, limits):
        calls.append(payload)
        if "rounds" in payload:
            return json.dumps(COMPARISON if payload.get("chosen_plan") is None else SUMMARY, ensure_ascii=False)
        peers = payload["peer_opinions"]
        return json.dumps({**OPINION, "reply_to": list(peers)[:1], "challenge": "同意需核对费用，但缺少板块数据不能确认方向" if peers else ""}, ensure_ascii=False)
    monkeypatch.setattr(discussion, "call_model", fake_model)
    client.post(f"/api/alerts/{event_id}/plan", json=plan_body(client, event_id, "t_watch"))
    worksheet = client.get(f"/api/alerts/{event_id}/action-plan").json()["data"]
    return client, event_id, worksheet, calls


def test_position_discussion_forms_peer_replies_when_models_return_valid_evidence(discussion_client) -> None:
    client, event_id, worksheet, calls = discussion_client
    before = pf._load()
    response = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
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
    assert {key: value for key, value in pf._load().items() if key != "position_reviews"} == {key: value for key, value in before.items() if key != "position_reviews"} and ae.list_events()[0]["status"] == "OPEN"


def test_position_discussion_does_not_claim_consensus_when_all_models_fail(discussion_client, monkeypatch) -> None:
    client, event_id, worksheet, calls = discussion_client
    async def failure(*args):
        raise TimeoutError("upstream contains private-key")
    monkeypatch.setattr(discussion, "call_model", failure)
    response = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
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
    result = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]}).json()["data"]
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
                record_plan(event_id, "hold")
        return await original(endpoint, payload, timeout, limits)
    monkeypatch.setattr(discussion, "call_model", changed)
    result = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    assert result.status_code == 409 and "已变化" in result.text
    assert "discussion" not in ae.list_events()[0]["review_plan"]


def test_position_discussion_refuses_foreign_tenant_before_model_call(discussion_client) -> None:
    client, event_id, worksheet, calls = discussion_client
    response = client.post(f"/api/alerts/{event_id}/discussion", headers={"X-User-Id": "other"}, json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    assert response.status_code == 409 and not calls


def test_position_discussion_refuses_parallel_requests_when_lease_held(discussion_client) -> None:
    client, event_id, worksheet, calls = discussion_client
    with (Path(ae.CACHE_DIR) / ".position-discussion.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        response = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
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


def test_position_discussion_explains_current_route_without_invalidated_calculation(discussion_client) -> None:
    client, event_id, worksheet, calls = discussion_client
    from test_position_action_plan import PARAMS
    client.post(f"/api/alerts/{event_id}/plan", json={"choice": "t_watch", "parameters": PARAMS, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    data = pf._load()
    data["holdings"][0]["shares"] = 200
    data["holdings"][0]["lots"] = [{"shares": 200, "opened_at": "2026-10-22"}]
    pf._save(data)
    changed = client.get(f"/api/alerts/{event_id}/action-plan").json()["data"]
    response = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": changed["position_snapshot"], "expected_revision": changed["revision"]})
    assert response.status_code == 200 and calls
    assert calls[-1]['saved_parameters'] is None and calls[-1]['scenario'] is None
    assert '失效' in calls[-1]['scenario_issue']


def test_position_discussion_uses_anthropic_v1_messages_when_default_sdk_base_has_no_version(monkeypatch) -> None:
    from types import SimpleNamespace
    from aqsp.utils import llm_safe
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(llm_safe, "_make_client", lambda: SimpleNamespace(base_url="https://api.anthropic.com", api_key="key", close=lambda: None))
    assert discussion.resolve_endpoint(None).url == "https://api.anthropic.com/v1/messages"


def test_position_discussion_hides_old_analysis_when_position_changes_after_save(discussion_client) -> None:
    client, event_id, worksheet, calls = discussion_client
    assert client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]}).status_code == 200
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
    response = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": "t_watch", "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    result = response.json()["data"]
    assert result["status"] == "failed" and result["summary"] is None
    assert all("HTTP 429" in row["issue"] for row in result["rounds"][0]["opinions"])


def test_position_comparison_runs_before_choice_when_no_trade_parameters_exist(discussion_client) -> None:
    client, event_id, worksheet, calls = discussion_client
    data = pf._load()
    data["position_reviews"][worksheet["position_id"]]["review_plan"] = {}
    data["position_reviews"][worksheet["position_id"]]["version"] += 1
    pf._save(data)
    worksheet = client.get(f"/api/alerts/{event_id}/action-plan").json()["data"]
    before = pf._load()
    response = client.post(f"/api/alerts/{event_id}/discussion", json={"expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["choice"] is None and result["status"] == "complete"
    assert len(result["summary"]["routes"]) == 3 and len(calls) == 7
    assert all(row["chosen_plan"] is None and row["saved_parameters"] is None for row in calls)
    assert {key: value for key, value in pf._load().items() if key != "position_reviews"} == {key: value for key, value in before.items() if key != "position_reviews"}
    reloaded = client.get(f"/api/alerts/{event_id}/action-plan").json()["data"]
    assert reloaded["review_plan"] == {} and reloaded["comparison"] == result
    assert ae.list_events()[0]["status"] == "OPEN"


def test_position_comparison_hides_result_when_position_changes_after_analysis(discussion_client) -> None:
    client, event_id, worksheet, calls = discussion_client
    assert client.post(f"/api/alerts/{event_id}/discussion", json={"choice": None, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]}).status_code == 200
    data = pf._load()
    data["holdings"][0]["cost"] += 1
    pf._save(data)
    assert client.get(f"/api/alerts/{event_id}/action-plan").json()["data"]["comparison"] is None


@pytest.mark.parametrize("patch", [
    {"routes": [COMPARISON["routes"][0]] * 3},
    {"routes": [{**row, "evidence_ids": ["invented"]} for row in COMPARISON["routes"]]},
    {"focus_choice": "t_watch"},
])
def test_position_comparison_rejects_fabricated_or_paused_route_when_model_violates_constraints(patch: dict) -> None:
    with pytest.raises(ValueError):
        discussion._parse(json.dumps({**COMPARISON, **patch}), discussion.ComparisonSummary,
                          {"quote": {}, "holding": {}}, {"price_0": {}}, set(), {"pause_t": True, "sellable_shares": 1000})


def test_position_discussion_accepts_catalog_choice_without_persisting_key_or_global_config(discussion_client, monkeypatch) -> None:
    import position_model_catalog
    client, event_id, worksheet, calls = discussion_client
    async def directory(*args, **kwargs):
        return {"models": ["agnes-3.0-flash"], "directory_complete": True}
    monkeypatch.setattr(position_model_catalog, "read_catalog", directory)
    response = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": None, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"], "server_model": "agnes-3.0-flash"})
    assert response.status_code == 200
    assert response.json()["data"]["model"] == "agnes-3.0-flash"
    assert "private-key" not in response.text
    rejected = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": None, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"], "server_model": "retired"})
    assert rejected.status_code == 409 and "未自动替换" in rejected.text


def test_position_discussion_defaults_to_current_model_when_no_explicit_override(monkeypatch) -> None:
    from types import SimpleNamespace
    from aqsp.utils import llm_safe
    monkeypatch.setenv("LLM_PROVIDER", "agnes")
    monkeypatch.delenv("AGNES_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(llm_safe, "_make_client", lambda: SimpleNamespace(base_url="https://model.invalid/v1", api_key="key", close=lambda: None))
    assert discussion.resolve_endpoint(None).model == "agnes-3.0-flash"
    monkeypatch.setenv("AGNES_MODEL", "deliberately-pinned-model")
    assert discussion.resolve_endpoint(None).model == "deliberately-pinned-model"


def test_position_comparison_does_not_write_old_result_when_risk_evidence_changes(discussion_client, monkeypatch) -> None:
    client, event_id, worksheet, calls = discussion_client
    original = discussion.call_model
    async def update(endpoint, payload, timeout, limits):
        if "rounds" in payload:
            events = ae._load()
            events[0]["threshold_at"] += 1
            ae._save(events)
        return await original(endpoint, payload, timeout, limits)
    monkeypatch.setattr(discussion, "call_model", update)
    result = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": None, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    assert result.status_code == 409 and "风险依据已变化" in result.text
    assert ae.list_events()[0].get("position_analysis") is None


def test_position_comparison_identifies_old_source_when_quote_changes_without_trade(discussion_client, monkeypatch) -> None:
    from test_position_action_plan import QUOTE
    client, event_id, worksheet, calls = discussion_client
    result = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": None, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]}).json()["data"]
    monkeypatch.setattr(pf.astock, "tencent_quote", lambda codes: {code: {**QUOTE, "price": 9.3} for code in codes})
    latest = client.get(f"/api/alerts/{event_id}/action-plan").json()["data"]
    assert latest["position_snapshot"] == worksheet["position_snapshot"]
    assert latest["analysis_basis_id"] != result["analysis_basis_id"]


def test_position_discussion_keeps_explicit_model_when_catalog_is_unavailable(discussion_client, monkeypatch) -> None:
    import position_model_catalog
    client, event_id, worksheet, calls = discussion_client
    async def directory(*args, **kwargs):
        return {"models": [], "listed": None, "directory_complete": False, "issue": "目录不可用"}
    monkeypatch.setattr(position_model_catalog, "read_catalog", directory)
    result = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": None, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"], "server_model": "explicit-model"})
    assert result.status_code == 200 and result.json()["data"]["model"] == "explicit-model"


def test_position_discussion_describes_exact_reference_ids_when_requesting_new_model(discussion_client) -> None:
    client, event_id, worksheet, calls = discussion_client
    client.post(f"/api/alerts/{event_id}/discussion", json={"choice": None, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    first, reply, summary = calls[0], calls[3], calls[-1]
    assert first["output_schema"]["properties"]["reply_to"]["maxItems"] == 0
    assert reply["output_schema"]["properties"]["reply_to"]["items"]["enum"] == sorted(reply["peer_opinions"])
    refs = summary["output_schema"]["$defs"]["RouteOpinion"]["properties"]["evidence_ids"]["items"]["enum"]
    assert refs == sorted(summary["evidence"]) and "cost" not in refs


def test_position_discussion_stream_reports_result_and_keeps_tenant_isolated(discussion_client) -> None:
    client, event_id, worksheet, calls = discussion_client
    body = {"choice": None, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]}
    denied = client.post(f"/api/alerts/{event_id}/discussion/stream", headers={"X-User-Id": "other"}, json=body)
    assert calls == [] and '"event": "error"' in denied.text
    response = client.post(f"/api/alerts/{event_id}/discussion/stream", json=body)
    rows = [json.loads(line) for line in response.text.splitlines()]
    assert response.headers["x-accel-buffering"] == "no"
    assert rows[0] == {"event": "progress", "stage": "preparing"}
    assert rows[-1]["event"] == "result" and rows[-1]["data"]["status"] == "complete"
    assert len(calls) == 7
    assert "private-key" not in response.text
    saved = client.get(f"/api/alerts/{event_id}/action-plan").json()["data"]["comparison"]
    assert saved == rows[-1]["data"]


def test_position_discussion_stream_cancels_model_and_releases_lease_when_disconnected(discussion_client, monkeypatch) -> None:
    _, event_id, worksheet, _ = discussion_client
    cancelled = []
    async def waiting(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    monkeypatch.setattr(discussion, "call_model", waiting)
    async def consume() -> None:
        stream = discussion.stream_discussion(event_id, None, worksheet["position_snapshot"], expected_revision=worksheet["revision"])
        assert json.loads(await anext(stream))["stage"] == "preparing"
        assert json.loads(await anext(stream))["stage"] == "independent"
        await asyncio.sleep(0)
        await stream.aclose()
    asyncio.run(consume())
    assert cancelled
    with (Path(ae.CACHE_DIR) / ".position-discussion.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_position_discussion_stream_has_heartbeats_and_hides_transport_errors(monkeypatch) -> None:
    from dataclasses import replace
    limits = replace(discussion.DiscussionLimits(), heartbeat_seconds=0.001)
    monkeypatch.setattr(discussion, "DiscussionLimits", lambda: limits)
    async def failed(*args, **kwargs):
        await asyncio.sleep(0.01)
        raise RuntimeError("secret-private-key")
    monkeypatch.setattr(discussion, "run_discussion", failed)
    async def consume() -> list[dict]:
        return [json.loads(line) async for line in discussion.stream_discussion("event", None, {})]
    rows = asyncio.run(consume())
    assert len(rows) > 2 and rows[-1]["event"] == "error"
    assert all(row["stage"] == "preparing" for row in rows[:-1])
    assert "secret" not in json.dumps(rows)


@pytest.mark.parametrize("error,kind", [(TimeoutError("private-key"), "timeout"), (httpx.ConnectError("private-key"), "transport"), (ValueError("private-key"), "invalid_output"), (discussion.ModelHTTPError(500), "upstream_status")])
def test_position_discussion_explains_compatibility_failure_without_leaking_provider_body(discussion_client, monkeypatch, error: Exception, kind: str) -> None:
    client, event_id, worksheet, _ = discussion_client
    async def failed(*args):
        raise error
    monkeypatch.setattr(discussion, "call_model", failed)
    response = client.post(f"/api/alerts/{event_id}/discussion/stream", json={"choice": None, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    result = json.loads(response.text.splitlines()[-1])["data"]
    assert result["status"] == "failed" and result["summary"] is None
    assert {row["failure_kind"] for row in result["rounds"][0]["opinions"]} == {kind}
    assert "private-key" not in response.text
    assert result["evidence_snapshot"]["quote"]["price"] == worksheet["basis"]["price"]


def test_position_discussion_uses_operational_budgets_when_explicitly_configured(discussion_client, monkeypatch) -> None:
    client, event_id, worksheet, _ = discussion_client
    monkeypatch.setenv("AQSP_POSITION_ROLE_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("AQSP_POSITION_SYNTHESIS_TIMEOUT_SECONDS", "44")
    original = discussion.call_model
    budgets = []
    async def measured(endpoint, payload, timeout, limits):
        budgets.append(("summary" if "rounds" in payload else "role", timeout))
        return await original(endpoint, payload, timeout, limits)
    monkeypatch.setattr(discussion, "call_model", measured)
    result = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": None, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    assert result.json()["data"]["status"] == "complete"
    assert budgets == [("role", 12.5)] * 6 + [("summary", 44)]
    assert discussion.discussion_limits({}).synthesis_timeout == 45


@pytest.mark.parametrize("value", ["0", "-1", "61", "inf", "nan", "private-key", ""])
def test_position_discussion_refuses_invalid_budget_before_model_io(discussion_client, monkeypatch, value: str) -> None:
    client, event_id, worksheet, calls = discussion_client
    monkeypatch.setenv("AQSP_POSITION_SYNTHESIS_TIMEOUT_SECONDS", value)
    response = client.post(f"/api/alerts/{event_id}/discussion/stream", json={"choice": None, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    assert calls == []
    record = json.loads(response.text.splitlines()[-1])
    assert record["event"] == "error" and "AQSP_POSITION_SYNTHESIS_TIMEOUT_SECONDS" in record["detail"]
    if value == "private-key":
        assert value not in record["detail"]


@pytest.mark.parametrize("anthropic,kind", [(False, "truncated_output"), (True, "truncated_output"), (False, "empty_output"), (True, "empty_output")])
def test_position_model_transport_rejects_incomplete_response_even_when_json_parseable(monkeypatch, anthropic: bool, kind: str) -> None:
    text = json.dumps(COMPARISON) if kind == "truncated_output" else "  "
    payload = ({"content": [{"type": "text", "text": text}], "stop_reason": "max_tokens" if kind == "truncated_output" else "end_turn"}
               if anthropic else {"choices": [{"message": {"content": text}, "finish_reason": "length" if kind == "truncated_output" else "stop"}]})
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    original = httpx.AsyncClient
    monkeypatch.setattr(discussion.httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    with pytest.raises(discussion.ModelOutputError) as error:
        asyncio.run(discussion.call_model(discussion.ModelEndpoint("https://model.invalid", "private-key", "model", anthropic), {}, 1, discussion.DiscussionLimits()))
    assert discussion._model_failure(error.value)["failure_kind"] == kind
    assert "private-key" not in str(error.value)


def test_position_discussion_requests_short_summary_without_losing_routes_or_disagreements(discussion_client) -> None:
    client, event_id, worksheet, calls = discussion_client
    response = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": None, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    assert response.json()["data"]["summary"]["disagreements"] == COMPARISON["disagreements"]
    schema = calls[-1]["output_schema"]
    assert schema["properties"]["conclusion"]["maxLength"] == 180
    assert schema["$defs"]["RouteOpinion"]["properties"]["reason"]["maxLength"] == 180
    assert schema["properties"]["agreements"]["maxItems"] == 3
    assert schema["properties"]["routes"]["minItems"] == schema["properties"]["routes"]["maxItems"] == 3
    assert len(calls[-1]["rounds"]) == 2
    assert all(len(row["opinions"]) == 3 for row in calls[-1]["rounds"])
    assert calls[-1]["guards"]["no_orders"]


def test_position_discussion_preserves_roles_without_fake_summary_when_synthesis_times_out(discussion_client, monkeypatch) -> None:
    client, event_id, worksheet, calls = discussion_client
    original = discussion.call_model
    async def failed_summary(endpoint, payload, timeout, limits):
        if "rounds" in payload:
            calls.append(payload)
            raise TimeoutError("private-key")
        return await original(endpoint, payload, timeout, limits)
    monkeypatch.setattr(discussion, "call_model", failed_summary)
    response = client.post(f"/api/alerts/{event_id}/discussion", json={"choice": None, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    result = response.json()["data"]
    assert len(calls) == 7 and result["status"] == "partial" and result["summary"] is None
    assert result["summary_failure_kind"] == "timeout" and "private-key" not in response.text
    assert all(row["status"] == "ok" and row["elapsed_seconds"] >= 0 for rnd in result["rounds"] for row in rnd["opinions"])
    assert result["summary_seconds"] >= 0 and result["elapsed_seconds"] >= result["summary_seconds"]
    assert result["io_budget"] == {"role_timeout_seconds": 45, "synthesis_timeout_seconds": 45}
    assert result["choice"] is None


def test_position_discussion_describes_required_peer_reply_when_challenge_has_peers() -> None:
    schema = discussion.output_schema(discussion.RoleOpinion, {"quote": {}}, {"price_0": {}}, {"trend"})
    assert schema["properties"]["reply_to"]["minItems"] == 1
    assert schema["properties"]["challenge"]["minLength"] == 1
    assert schema["properties"]["reply_to"]["items"]["enum"] == ["trend"]
