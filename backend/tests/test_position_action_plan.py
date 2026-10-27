"""Scenario arithmetic and current-position binding; all prices are synthetic."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import alert_events as ae
import app as app_module
import decision_context
import portfolio as pf
import position_plan_service as service
import tenant
from position_action_plan import build_action_basis, calculate_scenario

NOW = datetime(2026, 10, 23, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
PARAMS = {"quantity": 200, "sell_price": 9.5, "buy_price": 9.0,
          "commission_rate": 0.0003, "min_commission": 5,
          "stamp_tax_rate": 0.0005, "slippage_rate": 0.001,
          "fees_confirmed": True}
HOLDING = {"code": "002129", "position_id": "p1", "shares": 1000, "sellable_shares": 1000, "cost": 12}
QUOTE = {"price": 9.2, "high": 9.5, "low": 9, "last_close": 9.1, "quote_time": "20261023100000"}


def record_plan(event_id: str, choice: str) -> dict | None:
    worksheet = service.read_action_plan(event_id)
    return ae.record_review_plan(event_id, choice, expected_position=worksheet["position_snapshot"], expected_revision=worksheet["revision"])


def plan_body(client: TestClient, event_id: str, choice: str) -> dict:
    worksheet = client.get(f"/api/alerts/{event_id}/action-plan").json()["data"]
    return {"choice": choice, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]}


def test_action_plan_computes_net_two_legs_when_explicit_costs_provided() -> None:
    result = calculate_scenario("t_watch", PARAMS, HOLDING)
    assert result["gross_spread"] == 100
    assert result["estimated_fees"] == 14.65
    assert result["net_cash"] == 85.35
    assert result["economic_cost"] == 11.9147
    assert result["remaining_shares"] == 1000
    assert result["round_trip_positive"] is True
    assert HOLDING["cost"] == 12


def test_action_plan_does_not_call_losing_round_trip_cost_reduction() -> None:
    result = calculate_scenario("t_watch", {**PARAMS, "buy_price": 9.5}, HOLDING)
    assert result["net_cash"] < 0
    assert result["cost_reduction_per_share"] < 0
    assert result["economic_cost"] > HOLDING["cost"]
    assert result["round_trip_positive"] is False


def test_action_plan_handles_partial_and_full_exit_without_division_by_zero() -> None:
    partial = calculate_scenario("reduce", {**PARAMS, "buy_price": None}, HOLDING)
    assert partial["remaining_shares"] == 800
    assert partial["estimated_fees"] == 7.85
    assert partial["net_cash"] == 1892.15
    full = calculate_scenario("reduce", {**PARAMS, "quantity": 1000, "buy_price": None}, HOLDING)
    assert full["remaining_shares"] == 0
    assert full["economic_cost"] is None


@pytest.mark.parametrize("patch", [{"quantity": True}, {"quantity": 200.5}, {"quantity": 1001}, {"sell_price": float("nan")}, {"sell_price": 0}, {"commission_rate": -1}, {"stamp_tax_rate": float("inf")}, {"fees_confirmed": False}, {"slippage_rate": 1}, {"buy_price": None}])
def test_action_plan_rejects_invalid_assumptions_when_outside_contract(patch: dict) -> None:
    with pytest.raises(ValueError):
        calculate_scenario("t_watch", {**PARAMS, **patch}, HOLDING)


@pytest.mark.parametrize("code,quantity,allowed", [("600000", 101, False), ("688001", 200, True), ("688001", 201, True), ("688001", 100, False), ("920001", 101, True)])
def test_action_plan_respects_board_reentry_unit_when_selecting_quantity(code: str, quantity: int, allowed: bool) -> None:
    if allowed:
        assert calculate_scenario("t_watch", {**PARAMS, "quantity": quantity}, {**HOLDING, "code": code})
    else:
        with pytest.raises(ValueError):
            calculate_scenario("t_watch", {**PARAMS, "quantity": quantity}, {**HOLDING, "code": code})


def test_action_basis_uses_only_source_timed_prices_and_completed_raw_bars() -> None:
    history = pd.DataFrame({"date": ["2026-10-22", "2026-10-23"], "close": [9.2, 9.3], "high": [9.4, 999], "low": [9, 0.01]})
    basis = build_action_basis(HOLDING, QUOTE, history, checked_at=NOW, joint_weakness=False)
    refs = {ref.label: ref.price for ref in basis.references}
    assert refs["本交易日已见高点"] == 9.5
    assert refs["最近完成日高点"] == 9.4
    assert 999 not in refs.values()
    history.attrs["price_mode"] = "qfq"
    assert not any(ref.label.startswith("最近完成日") for ref in build_action_basis(HOLDING, QUOTE, history, checked_at=NOW, joint_weakness=False).references)


def test_action_basis_does_not_invent_prices_when_quote_stale() -> None:
    basis = build_action_basis(HOLDING, {**QUOTE, "quote_time": "20261022150000"}, None, checked_at=NOW, joint_weakness=False)
    assert basis.quote_issue == "行情过期"
    assert basis.price is None and basis.references == ()


@pytest.fixture
def plan_client(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ae, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(tenant, "current_tenant", tenant.ContextVar("plans", default="local"))
    monkeypatch.setattr(pf, "today_shanghai", lambda: date(2026, 10, 23))
    monkeypatch.setattr(service, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(decision_context, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(pf.astock, "tencent_quote", lambda codes: {code: {**QUOTE, "name": "合成持仓"} for code in codes})
    pf.add_holding("002129", 1000, 12, "2026-10-22")
    holding = pf.get_portfolio()["holdings"][0]
    context = decision_context.build_position_context(holding, [], quote=QUOTE, checked_at=NOW).to_dict()
    event = ae.upsert_from_reminders([{"symbol": "002129", "level": "L1", "rule_id": "002129-L1", "position_id": holding["position_id"], "current_price": 9.2, "threshold_price": 11.4, "sellable_now": True, "basis_json": {"rule": "合成规则", "actual": "合成触发"}, "position_context": context}])[0]
    return TestClient(app_module.app), event["id"]


def test_action_plan_endpoint_persists_and_recomputes_without_trade(plan_client) -> None:
    client, event = plan_client
    before = pf._load()
    worksheet = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    result = client.post(f"/api/alerts/{event}/plan", json={"choice": "t_watch", "parameters": PARAMS, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    assert result.status_code == 200, result.text
    reloaded = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    assert reloaded["review_plan"]["parameters"] == PARAMS
    assert reloaded["scenario"]["net_cash"] == 85.35
    assert ae.list_events()[0]["status"] == "OPEN"
    assert {key: value for key, value in pf._load().items() if key != "position_reviews"} == {key: value for key, value in before.items() if key != "position_reviews"}


def test_action_plan_endpoint_invalidates_scenario_when_holdings_changed(plan_client) -> None:
    client, event = plan_client
    worksheet = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    body = {"choice": "t_watch", "parameters": PARAMS, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]}
    assert client.post(f"/api/alerts/{event}/plan", json=body).status_code == 200
    pf.add_holding("002129", 100, 9.2, "2026-10-23")
    changed = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    assert changed["scenario"] is None and "已变化" in changed["scenario_issue"]
    assert client.post(f"/api/alerts/{event}/plan", json=body).status_code == 409


def test_action_plan_endpoint_rejects_stale_quote_when_saving(plan_client, monkeypatch) -> None:
    client, event = plan_client
    worksheet = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    monkeypatch.setattr(pf.astock, "tencent_quote", lambda codes: {code: {**QUOTE, "quote_time": "20261022150000"} for code in codes})
    result = client.post(f"/api/alerts/{event}/plan", json={"choice": "t_watch", "parameters": PARAMS, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    assert result.status_code == 400 and "过期" in result.text
    assert "parameters" not in ae.list_events()[0].get("review_plan", {})


def test_action_plan_endpoint_rejects_reopened_identity_and_cross_tenant(plan_client) -> None:
    client, event = plan_client
    assert client.get(f"/api/alerts/{event}/action-plan", headers={"X-User-Id": "unrelated"}).status_code == 409
    data = pf._load()
    data["holdings"][0]["position_id"] = "reopened"
    pf._save(data)
    assert client.get(f"/api/alerts/{event}/action-plan").status_code == 409


def test_action_plan_endpoint_preserves_prior_inputs_when_journal_write_fails(plan_client, monkeypatch) -> None:
    client, event = plan_client
    worksheet = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    record_plan(event, "hold")
    worksheet = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    monkeypatch.setattr(pf, "_save", lambda rows: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        client.post(f"/api/alerts/{event}/plan", json={"choice": "t_watch", "parameters": PARAMS, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    assert ae.list_events()[0]["review_plan"]["choice"] == "hold"


def test_action_plan_rejects_buy_price_when_choice_is_reduction() -> None:
    with pytest.raises(ValueError, match="不包含回补价"):
        calculate_scenario("reduce", PARAMS, HOLDING)


def test_action_plan_joint_weakness_expires_when_quote_or_sector_recovers(plan_client, monkeypatch) -> None:
    client, event = plan_client
    events = ae._load()
    context = events[0]["position_context"]
    context["action_basis"]["joint_weakness"] = True
    context["market"] = {"as_of": "2026-10-23", "status": "weak", "source": "合成市场源"}
    context["sector"] = {"as_of": "2026-10-23T10:00:00+08:00", "status": "falling", "source": "合成板块源"}
    ae._save(events)
    monkeypatch.setattr(pf.astock, "tencent_quote", lambda codes: {code: {**QUOTE, "change_pct": -1} for code in codes})
    assert client.get(f"/api/alerts/{event}/action-plan").json()["data"]["basis"]["joint_weakness"] is True
    monkeypatch.setattr(pf.astock, "tencent_quote", lambda codes: {code: {**QUOTE, "change_pct": 1} for code in codes})
    assert client.get(f"/api/alerts/{event}/action-plan").json()["data"]["basis"]["joint_weakness"] is False
    monkeypatch.setattr(pf.astock, "tencent_quote", lambda codes: {code: {**QUOTE, "change_pct": -1} for code in codes})
    monkeypatch.setattr(service, "now_shanghai", lambda: NOW.replace(hour=14))
    assert client.get(f"/api/alerts/{event}/action-plan").json()["data"]["basis"]["joint_weakness"] is False


def test_action_plan_does_not_claim_joint_weakness_when_background_has_no_source(plan_client, monkeypatch) -> None:
    client, event = plan_client
    events = ae._load()
    events[0]["position_context"]["market"] = {"as_of": "2026-10-23", "status": "weak"}
    events[0]["position_context"]["sector"] = {"as_of": "2026-10-23T10:00:00+08:00", "status": "falling"}
    ae._save(events)
    monkeypatch.setattr(pf.astock, "tencent_quote", lambda codes: {code: {**QUOTE, "change_pct": -1} for code in codes})
    data = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    assert not data["basis"]["joint_weakness"]
    assert data["route_review"]["evidence"]["market"]["status"] == "unknown"


def test_action_plan_rejects_mid_commit_change_and_t1_locked_when_saving(plan_client, monkeypatch) -> None:
    client, event = plan_client
    worksheet = service.read_action_plan(event)
    data = pf._load()
    data["holdings"][0]["lots"] = [{"shares": 1000, "opened_at": "2026-10-23"}]
    pf._save(data)
    monkeypatch.setattr(service, "read_position_plan", lambda position_id: worksheet)
    response = client.post(f"/api/alerts/{event}/plan", json={"choice": "t_watch", "parameters": PARAMS, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]})
    assert response.status_code == 409 and "已变化" in response.text


def test_action_plan_rejects_fractional_position_when_rounding_would_hide_quantity() -> None:
    with pytest.raises(ValueError):
        calculate_scenario("t_watch", PARAMS, {**HOLDING, "shares": 1000.5})


def test_action_plan_reports_realized_loss_when_reduction_is_below_cost() -> None:
    scenario = calculate_scenario("reduce", {**PARAMS, "buy_price": None}, HOLDING)
    assert scenario["realized_pnl"] == -507.85


def test_action_plan_keeps_parameters_when_selected_choice_is_clicked_again(plan_client) -> None:
    client, event = plan_client
    worksheet = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    body = {"choice": "t_watch", "parameters": PARAMS, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]}
    assert client.post(f"/api/alerts/{event}/plan", json=body).status_code == 200
    for _ in range(2):
        assert client.post(f"/api/alerts/{event}/plan", json=plan_body(client, event, "t_watch")).status_code == 200
    again = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    assert again["scenario"]["net_cash"] == 85.35
    assert again["review_plan"]["parameters"] == PARAMS


def test_action_plan_returns_recorded_sale_when_receipt_matches_same_position(plan_client) -> None:
    client, event = plan_client
    before = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    before = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    assert client.post(f"/api/alerts/{event}/plan", json={"choice": "t_watch", "parameters": PARAMS, "expected_position": before["position_snapshot"], "expected_revision": before["revision"]}).status_code == 200
    ae.close_from_operation("002129", "2026-10-23", 9.5, 200, 12, "journey-sale")
    result = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    assert result["recorded_sale"] == {"date": "2026-10-23", "price": 9.5, "shares": 200}
    assert result["basis"]["shares"] == 800
    assert result["scenario"] is None and result["scenario_issue"]
    assert ae.list_events()[0]["status"] == "OPEN"
    assert result["review_plan"]["position_snapshot"]["shares"] == 1000


def test_action_plan_ignores_event_sale_when_no_durable_receipt(plan_client) -> None:
    client, event = plan_client
    events = ae._load()
    events[0]["last_sale"] = {"operation_id": "unknown", "date": "2026-10-23", "price": 9.5, "shares": 200}
    ae._save(events)
    assert client.get(f"/api/alerts/{event}/action-plan").json()["data"]["recorded_sale"] is None


@pytest.mark.parametrize("changed", [{"position_id": "another"}, {"code": "600000"}])
def test_action_plan_ignores_sale_when_receipt_does_not_match(plan_client, changed: dict) -> None:
    client, event = plan_client
    ae.close_from_operation("002129", "2026-10-23", 9.5, 200, 12, "journey-sale")
    data = pf._load()
    data["closed"][-1].update(changed)
    pf._save(data)
    assert client.get(f"/api/alerts/{event}/action-plan").json()["data"]["recorded_sale"] is None


def test_action_plan_acknowledges_sale_only_when_current_scenario_saved(plan_client) -> None:
    client, event = plan_client
    record_plan(event, "t_watch")
    ae.close_from_operation("002129", "2026-10-23", 9.5, 200, 12, "journey-sale")
    before = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    assert before["sale_requires_review"] is True
    assert client.post(f"/api/alerts/{event}/plan", json=plan_body(client, event, "t_watch")).status_code == 200
    assert client.get(f"/api/alerts/{event}/action-plan").json()["data"]["sale_requires_review"] is True
    before = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    assert client.post(f"/api/alerts/{event}/plan", json={"choice": "t_watch", "parameters": PARAMS, "expected_position": before["position_snapshot"], "expected_revision": before["revision"]}).status_code == 200
    after = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    assert after["recorded_sale"] == before["recorded_sale"]
    assert after["sale_requires_review"] is False
    assert after["scenario"] and not after["scenario_issue"]
    assert pf._load()["holdings"][0]["shares"] == 800


def test_action_plan_can_resume_holding_after_recorded_sale_without_scenario(plan_client) -> None:
    client, event = plan_client
    ae.close_from_operation("002129", "2026-10-23", 9.5, 200, 12, "journey-sale")
    assert client.post(f"/api/alerts/{event}/plan", json=plan_body(client, event, "hold")).status_code == 200
    result = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    assert result["recorded_sale"]
    assert result["sale_requires_review"] is True
    assert client.post(f"/api/alerts/{event}/plan", json={"choice": "hold", "expected_position": result["position_snapshot"], "expected_revision": result["revision"]}).status_code == 200
    result = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    assert result["sale_requires_review"] is False
    assert result["review_plan"]["choice"] == "hold"
    assert not result["review_plan"].get("parameters")


def test_action_plan_uses_durable_receipt_when_event_copy_differs(plan_client) -> None:
    client, event = plan_client
    ae.close_from_operation("002129", "2026-10-23", 9.5, 200, 12, "journey-sale")
    events = ae._load()
    events[0]["last_sale"]["shares"] = 999
    ae._save(events)
    result = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    assert result["recorded_sale"]["shares"] == 200
    assert result["sale_requires_review"] is True
