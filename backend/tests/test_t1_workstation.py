"""T+1 workstation user-rule, frequency and actual-sale invariants."""

from __future__ import annotations

from datetime import date, datetime
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import alert_events as ae
import app as app_module
import portfolio as pf
import tenant
from aqsp.risk.live_risk import run_live_risk_check


@pytest.fixture
def workstation(tmp_path, monkeypatch):
    import decision_context
    import position_context_source

    monkeypatch.setattr(decision_context, "now_shanghai", lambda: datetime(2026, 10, 23, 10, tzinfo=ZoneInfo("Asia/Shanghai")))
    monkeypatch.setattr(position_context_source, "load_position_sectors", lambda codes: {})
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ae, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        tenant, "current_tenant", tenant.ContextVar("workstation_test", default="local")
    )
    monkeypatch.setattr(pf, "today_shanghai", lambda: date(2026, 10, 23))
    monkeypatch.setattr(
        pf.astock,
        "tencent_quote",
        lambda codes: {c: {"name": c, "price": 9, "quote_time": "20261023100000"} for c in codes},
    )
    return TestClient(app_module.app)


def _reminder(mode="once"):
    return {
        "symbol": "600000",
        "level": "L1",
        "rule_id": "600000-L1",
        "freq_mode": mode,
        "current_price": 9,
        "threshold_price": 9.5,
        "sellable_now": True,
        "action_hint": "该卖",
        "basis_json": {"rule": "cost*0.95", "actual": "9"},
    }


def _plan_body(client: TestClient, event_id: str, choice: str) -> dict:
    worksheet = client.get(f"/api/alerts/{event_id}/action-plan").json()["data"]
    return {"choice": choice, "expected_position": worksheet["position_snapshot"], "expected_revision": worksheet["revision"]}


def test_t1_rules_attach_and_preserve_when_cost_changes(workstation):
    pf.add_holding("600000", 100, 10, "2026-10-22", "buy-1")
    initial = workstation.get("/api/portfolio/risk-rules").json()["data"]
    assert initial["unsupported_levels"] == ["L3"]
    assert {r["level"] for r in initial["rules"]} == {"L1", "L2", "L4", "L5"}
    patch = workstation.patch(
        "/api/portfolio/risk-rules/600000-L1",
        json={"enabled": False, "param_value": 0.1, "freq_mode": "always"},
    )
    assert patch.status_code == 200
    pf.add_holding("600000", 100, 20, "2026-10-23", "buy-2")
    rule = next(r for r in pf.get_risk_rules() if r["level"] == "L1")
    assert rule["trigger_price"] == 13.5
    assert rule["enabled"] is False and rule["freq_mode"] == "always"
    assert rule["origin"] == "MANUAL"


def test_t1_new_rules_default_to_once_to_limit_repeat_alert_noise(workstation):
    pf.add_holding("600000", 100, 10, "2026-10-22")
    rules = workstation.get("/api/portfolio/risk-rules").json()["data"]["rules"]
    assert {rule["freq_mode"] for rule in rules} == {"once"}


def test_t1_review_plan_keeps_risk_and_groups_rules_when_saved(workstation):
    pf.add_holding("600000", 100, 10, "2026-10-22")
    position_id = pf._load()["holdings"][0]["position_id"]
    first, second = ae.upsert_from_reminders([
        {**_reminder(), "position_id": position_id},
        {**_reminder(), "level": "L5", "rule_id": "600000-L5", "position_id": position_id},
        {**_reminder(), "position_id": "position-b", "rule_revision": 3},
    ])[:2]
    result = workstation.post(f"/api/alerts/{first['id']}/plan", json=_plan_body(workstation, first["id"], "t_watch"))
    assert result.status_code == 200
    events = ae.list_events()
    matching = [event for event in events if event.get("position_id") == position_id]
    assert len(matching) == 2
    assert all(event["review_plan"]["choice"] == "t_watch" for event in matching)
    assert all(event["status"] == "OPEN" for event in events)
    assert "review_plan" not in next(event for event in events if event["position_id"] == "position-b")
    assert pf._load()["holdings"][0]["shares"] == 100
    assert second["id"] in {event["id"] for event in matching}


@pytest.mark.parametrize("choice", ["buy_more", "", "sell_now"])
def test_t1_review_plan_rejects_when_choice_invalid(workstation, choice):
    event = ae.upsert_from_reminders([_reminder()])[0]
    before = ae._load()
    response = workstation.post(f"/api/alerts/{event['id']}/plan", json={"choice": choice})
    assert response.status_code == 400
    assert ae._load() == before


def test_t1_review_plan_rejects_when_event_terminal_or_missing(workstation):
    event = ae.upsert_from_reminders([_reminder()])[0]
    ae.transition(event["id"], "handled")
    for event_id in (event["id"], "not-present"):
        result = workstation.post(f"/api/alerts/{event_id}/plan", json={"choice": "hold"})
        assert result.status_code == 409


def test_t1_review_plan_preserves_prior_when_storage_fails(workstation, monkeypatch):
    pf.add_holding("600000", 100, 10, "2026-10-22")
    position_id = pf._load()["holdings"][0]["position_id"]
    event = ae.upsert_from_reminders([{**_reminder(), "position_id": position_id}])[0]
    body = _plan_body(workstation, event["id"], "hold")
    assert workstation.post(f"/api/alerts/{event['id']}/plan", json=body).status_code == 200
    body = _plan_body(workstation, event["id"], "t_watch")
    monkeypatch.setattr(pf, "_save", lambda data: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        workstation.post(f"/api/alerts/{event['id']}/plan", json=body)
    assert ae.list_events()[0]["review_plan"]["choice"] == "hold"


def test_t1_context_uses_actual_quote_name_date_and_risk_when_checked(workstation, monkeypatch):
    import decision_context

    monkeypatch.setattr(decision_context, "now_shanghai", lambda: datetime(2026, 10, 23, 10, tzinfo=ZoneInfo("Asia/Shanghai")))
    monkeypatch.setattr(decision_context, "load_cached_market_overview", lambda: None)
    monkeypatch.setattr(pf.astock, "tencent_quote", lambda codes: {code: {"name": "合成持仓", "price": 8, "quote_time": "20261023100000", "change_pct": -2.0} for code in codes})
    pf.add_holding("600000", 100, 10, "2026-10-22")
    response = workstation.post("/api/live-risk/check", json={"notify": False})
    assert response.status_code == 200
    events = workstation.get("/api/alerts").json()["data"]["events"]
    assert len(events) >= 2
    for event in events:
        context = event["position_context"]
        assert context["name"] == "合成持仓"
        assert context["holding_trade_days"] == 1
        assert context["stock"]["status"] == "falling"
        assert context["market"]["status"] == "unknown"
        assert context["sector"]["status"] == "unknown"
        assert context["assessment"]["stance"] == "risk_review"
        assert "-20.0%" in context["assessment"]["reason"]
        assert "不等于必须一次性清仓" in context["assessment"]["reason"]
        assert context["assessment"]["t_plan"]["eligible"] is None
        assert "可记录做T观察意向" in context["assessment"]["t_plan"]["reason"]


@pytest.mark.parametrize('background', [False, True])
def test_t1_check_preserves_typed_direction_facts_when_manual_or_background(workstation, monkeypatch, background):
    import decision_context
    import live_risk_service as service
    import position_context_source
    import position_strategy_evidence as evidence

    stamp = '2026-10-23T10:00:00+08:00'
    overview = {'sentiment': {'date': '20261023', 'up': 800, 'down': 3000, 'breadth': '偏弱'}}
    sector = {'industry': '银行', 'name': '银行', 'membership_as_of': stamp,
              'as_of': stamp, 'change_pct': -2, 'source': '合成板块源'}
    indices = {'status': 'unknown', 'source': '合成指数源', 'as_of': stamp, 'summary': '合成指数',
               'evidence': [], 'indices': [{'name': '合成上证', 'change_pct': -1, 'source': '合成指数源', 'as_of': stamp}]}
    monkeypatch.setattr(decision_context, 'load_cached_market_overview', lambda: overview)
    monkeypatch.setattr(position_context_source, 'load_position_sectors', lambda codes: {'600000': sector})
    monkeypatch.setattr(evidence, '_indices', lambda current: indices)
    pf.add_holding('600000', 100, 10, '2026-10-22')
    for level in ('L2', 'L4'):
        pf.update_risk_rule(f'600000-{level}', {'enabled': False})
    if background:
        result = service.check_positions(notify=False, strategy_sectors={'600000': sector})
    else:
        response = workstation.post('/api/live-risk/check', json={'notify': False})
        assert response.status_code == 200
        result = response.json()['data']
    assert result['reminders']
    for reminder in result['reminders']:
        context = reminder['position_context']
        assert context['sector']['industry'] == '银行'
        assert context['market']['breadth'] == '偏弱'
        assert context['market']['indices'] == indices['indices']


def test_t1_batch_receipt_keeps_newer_daily_time_when_recovered(workstation):
    events = ae.upsert_from_reminders([_reminder(), {**_reminder(), "level": "L5", "rule_id": "600000-L5"}])
    ids = [event["id"] for event in events]
    ae.mark_notified_many(ids, notified_at="2026-10-23T10:00:00+08:00")
    ae.mark_notified_many(ids, notified_at="2026-10-22T10:00:00+08:00")
    assert all(event["last_notified_at"] == "2026-10-23T10:00:00+08:00" for event in ae.list_events())


def test_t1_batch_receipt_does_not_partially_save_when_write_fails(workstation, monkeypatch):
    events = ae.upsert_from_reminders([_reminder(), {**_reminder(), "level": "L5", "rule_id": "600000-L5"}])
    monkeypatch.setattr(ae, "_save", lambda events: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        ae.mark_notified_many([event["id"] for event in events], notified_at="2026-10-23T10:00:00+08:00")
    assert all(event["last_notified_at"] is None for event in ae.list_events())


@pytest.mark.parametrize(
    "patch",
    [
        {"param_value": 0},
        {"param_value": 1},
        {"freq_mode": "weekly"},
        {"enabled": None},
    ],
)
def test_t1_rules_reject_when_patch_invalid(workstation, patch):
    pf.add_holding("600000", 100, 10, "2026-10-22")
    before = pf._load()
    response = workstation.patch("/api/portfolio/risk-rules/600000-L1", json=patch)
    assert response.status_code == 400
    assert pf._load() == before


def test_t1_l5_never_moves_down_when_price_retraces(workstation):
    pf.add_holding("600000", 100, 10, "2026-10-22")
    pf.observe_risk_prices({"600000": 15})
    pf.observe_risk_prices({"600000": 12})
    rule = next(r for r in pf.get_risk_rules() if r["level"] == "L5")
    assert rule["high_water_price"] == 15
    assert rule["trigger_price"] == pytest.approx(14.25)


def test_t1_engine_consumes_enabled_rules_when_user_edits(workstation):
    pf.add_holding("600000", 100, 10, "2026-10-22")
    rules = pf.get_risk_rules()
    history = pd.DataFrame(
        {"date": pd.date_range("2026-10-15", periods=5), "close": [10, 10, 10, 10, 10]}
    )
    output = run_live_risk_check(
        [{"code": "600000", "cost": 10, "shares": 100}],
        {"600000": 9},
        position_rules=rules,
        price_history={"600000": history},
    )
    assert {r.level for r in output} == {"L1", "L2", "L5"}
    for rule in rules:
        rule["enabled"] = False
    assert (
        run_live_risk_check(
            [{"code": "600000", "cost": 10, "shares": 100}],
            {"600000": 9},
            position_rules=rules,
        )
        == []
    )


@pytest.mark.parametrize("status", ["handled", "ignored"])
def test_t1_event_stays_terminal_when_condition_continuous(workstation, status):
    event = ae.upsert_from_reminders([_reminder()])[0]
    ae.transition(event["id"], status)
    assert ae.upsert_from_reminders([_reminder()]) == []
    assert len(ae.list_events()) == 1
    ae.upsert_from_reminders([], checked_rules={"600000-L1"})
    assert len(ae.upsert_from_reminders([_reminder()])) == 1
    assert len({e["id"] for e in ae.list_events()}) == 2


def test_t1_frequency_applies_when_once_daily_always(workstation, monkeypatch):
    current = [datetime(2026, 10, 23, 10, tzinfo=ZoneInfo("Asia/Shanghai"))]
    monkeypatch.setattr(ae, "now_shanghai", lambda: current[0])
    event = ae.upsert_from_reminders([_reminder("daily")])[0]
    assert ae.upsert_from_reminders([_reminder("daily")]) == []
    ae.transition(event["id"], "ignored")
    current[0] = datetime(2026, 10, 26, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert len(ae.upsert_from_reminders([_reminder("daily")])) == 1
    assert len(ae.upsert_from_reminders([_reminder("always")])) == 1
    assert len(ae.upsert_from_reminders([_reminder("always")])) == 1
    assert len(ae.list_events(status="OPEN")) == 1


def test_t1_event_updates_return_when_price_changes_without_new_episode(
    workstation: TestClient,
) -> None:
    first = {**_reminder(), "pnl_pct": -0.1}
    event = ae.upsert_from_reminders([first])[0]
    assert ae.upsert_from_reminders([
        {**first, "current_price": 8, "pnl_pct": -0.2}
    ]) == []
    current = ae.list_events()[0]
    assert current["id"] == event["id"]
    assert current["trigger_at"] == 8
    assert current["pnl_pct"] == -0.2


def test_t1_ignore_reduces_frequency_when_two_episodes(workstation):
    pf.add_holding("600000", 100, 10, "2026-10-22")
    pf.update_risk_rule("600000-L1", {"freq_mode": "always"})
    pf.record_rule_disposition("600000-L1", True)
    pf.record_rule_disposition("600000-L1", True)
    rule = next(r for r in pf.get_risk_rules() if r["level"] == "L1")
    assert rule["freq_mode"] == "daily"
    assert "连续忽略" in rule["frequency_note"]


def test_t1_ignore_returns_frequency_when_two_current_episodes(
    workstation: TestClient,
) -> None:
    pf.add_holding("600000", 100, 10, "2026-10-22")
    rule = pf.update_risk_rule("600000-L1", {"freq_mode": "always"})
    reminder = {**_reminder("always"), "position_id": rule["position_id"]}
    for _ in range(2):
        event = ae.upsert_from_reminders([reminder])[0]
        response = workstation.patch(
            f"/api/alerts/{event['id']}", params={"action": "ignored"}
        )
        assert response.status_code == 200
        ae.upsert_from_reminders([], checked_rules={rule["id"]})
    result = response.json()["data"]
    assert result["freq_mode"] == "daily"
    assert result["ignore_streak"] == 2
    assert "连续忽略两次" in result["frequency_note"]


def test_t1_ignore_preserves_new_rule_when_event_is_for_old_position(
    workstation: TestClient,
) -> None:
    pf.add_holding("600000", 100, 10, "2026-10-22")
    old_rule = pf.get_risk_rules()[0]
    event = ae.upsert_from_reminders([
        {**_reminder("always"), "position_id": old_rule["position_id"]}
    ])[0]
    pf.close_position("600000", "2026-10-23", 9, 100, 0)
    pf.add_holding("600000", 100, 9, "2026-10-23")
    pf.update_risk_rule("600000-L1", {"freq_mode": "always"})
    before = pf.get_risk_rules()
    response = workstation.patch(
        f"/api/alerts/{event['id']}", params={"action": "ignored"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "IGNORED"
    assert pf.get_risk_rules() == before


def test_t1_ignore_retries_once_when_alert_save_fails_after_rule_save(
    workstation: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pf.add_holding("600000", 100, 10, "2026-10-22")
    rule = pf.update_risk_rule("600000-L1", {"freq_mode": "always"})
    event = ae.upsert_from_reminders([
        {**_reminder("always"), "position_id": rule["position_id"]}
    ])[0]
    original_save = ae._save

    def fail_save(events: list[dict]) -> None:
        raise OSError("simulated alert write failure")

    monkeypatch.setattr(ae, "_save", fail_save)
    with pytest.raises(OSError, match="simulated alert write failure"):
        workstation.patch(f"/api/alerts/{event['id']}", params={"action": "ignored"})
    monkeypatch.setattr(ae, "_save", original_save)
    assert ae.list_events()[0]["status"] == "OPEN"
    response = workstation.patch(
        f"/api/alerts/{event['id']}", params={"action": "ignored"}
    )
    assert response.status_code == 200
    current = next(r for r in pf.get_risk_rules() if r["level"] == "L1")
    assert current["ignore_streak"] == 1
    assert current["freq_mode"] == "always"


def test_t1_ignore_keeps_event_open_when_rule_save_fails(
    workstation: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pf.add_holding("600000", 100, 10, "2026-10-22")
    rule = pf.update_risk_rule("600000-L1", {"freq_mode": "always"})
    event = ae.upsert_from_reminders([
        {**_reminder("always"), "position_id": rule["position_id"]}
    ])[0]
    before = pf._load()
    original_save = pf._save

    def fail_save(data: dict) -> None:
        raise OSError("simulated portfolio write failure")

    monkeypatch.setattr(pf, "_save", fail_save)
    with pytest.raises(OSError, match="simulated portfolio write failure"):
        workstation.patch(f"/api/alerts/{event['id']}", params={"action": "ignored"})
    monkeypatch.setattr(pf, "_save", original_save)
    assert pf._load() == before
    assert ae.list_events()[0]["status"] == "OPEN"
    response = workstation.patch(
        f"/api/alerts/{event['id']}", params={"action": "ignored"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["ignore_streak"] == 1


@pytest.mark.parametrize("manual_edit", [False, True])
def test_t1_ignore_recovers_original_receipt_when_monitor_expires_failed_commit(
    workstation: TestClient, monkeypatch: pytest.MonkeyPatch, manual_edit: bool,
) -> None:
    pf.add_holding("600000", 100, 10, "2026-10-22")
    rule = pf.update_risk_rule("600000-L1", {"freq_mode": "always"})
    event = ae.upsert_from_reminders([
        {**_reminder("always"), "position_id": rule["position_id"]}
    ])[0]
    original_save = ae._save

    def fail_save(events: list[dict]) -> None:
        raise OSError("simulated alert write failure")

    monkeypatch.setattr(ae, "_save", fail_save)
    with pytest.raises(OSError):
        workstation.patch(f"/api/alerts/{event['id']}", params={"action": "ignored"})
    monkeypatch.setattr(ae, "_save", original_save)
    ae.upsert_from_reminders([], checked_rules={rule["id"]})
    assert ae.list_events()[0]["status"] == "EXPIRED"
    if manual_edit:
        pf.update_risk_rule(rule["id"], {"param_value": 0.1, "freq_mode": "once"})
    before = pf.get_risk_rules()
    response = workstation.patch(
        f"/api/alerts/{event['id']}", params={"action": "ignored"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "IGNORED"
    assert response.json()["data"]["ignore_streak"] == 1
    assert response.json()["data"]["freq_mode"] == "always"
    assert pf.get_risk_rules() == before
    assert ae.list_events()[0]["status"] == "IGNORED"


def test_t1_ignore_rejects_expired_event_when_no_disposition_receipt(
    workstation: TestClient,
) -> None:
    pf.add_holding("600000", 100, 10, "2026-10-22")
    rule = pf.get_risk_rules()[0]
    event = ae.upsert_from_reminders([
        {**_reminder(), "position_id": rule["position_id"]}
    ])[0]
    ae.upsert_from_reminders([], checked_rules={rule["id"]})
    before = pf.get_risk_rules()
    response = workstation.patch(
        f"/api/alerts/{event['id']}", params={"action": "ignored"}
    )
    assert response.status_code == 409
    assert pf.get_risk_rules() == before
    assert ae.list_events()[0]["status"] == "EXPIRED"


def test_t1_ignore_preserves_edits_when_event_rule_revision_is_old(
    workstation: TestClient,
) -> None:
    pf.add_holding("600000", 100, 10, "2026-10-22")
    rule = pf.update_risk_rule("600000-L1", {"freq_mode": "always"})
    event = ae.upsert_from_reminders([
        {**_reminder("always"), "position_id": rule["position_id"]}
    ])[0]
    pf.update_risk_rule("600000-L1", {"param_value": 0.1})
    before = pf.get_risk_rules()
    response = workstation.patch(
        f"/api/alerts/{event['id']}", params={"action": "ignored"}
    )
    assert response.status_code == 200
    assert pf.get_risk_rules() == before


def test_t1_disposition_rejects_opposite_retry_when_first_journal_save_failed(
    workstation: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pf.add_holding("600000", 100, 10, "2026-10-22")
    rule = pf.get_risk_rules()[0]
    event = ae.upsert_from_reminders([
        {**_reminder(), "position_id": rule["position_id"]}
    ])[0]
    original_save = ae._save

    def fail_save(events: list[dict]) -> None:
        raise OSError("simulated alert write failure")

    monkeypatch.setattr(ae, "_save", fail_save)
    with pytest.raises(OSError):
        workstation.patch(f"/api/alerts/{event['id']}", params={"action": "ignored"})
    monkeypatch.setattr(ae, "_save", original_save)
    response = workstation.patch(
        f"/api/alerts/{event['id']}", params={"action": "handled"}
    )
    assert response.status_code == 409
    assert "按原操作重试" in response.json()["detail"]
    assert ae.list_events()[0]["status"] == "OPEN"
    assert workstation.patch(
        f"/api/alerts/{event['id']}", params={"action": "ignored"}
    ).status_code == 200


def test_t1_sell_only_settled_lots_when_today_addition(workstation):
    pf.add_holding("600000", 100, 10, "2026-10-22", "old")
    pf.add_holding("600000", 100, 20, "2026-10-23", "new")
    with pytest.raises(ValueError, match=r"T\+1"):
        pf.close_position("600000", "2026-10-23", 12, 101, 0)
    result = pf.close_position("600000", "2026-10-23", 12, 100, 0, "sell-1")
    assert result["holdings"][0]["shares"] == 100
    assert result["holdings"][0]["sellable_shares"] == 0
    assert result["closed"][0]["cost"] == 15
    again = pf.close_position("600000", "2026-10-23", 12, 100, 0, "sell-1")
    assert len(again["closed"]) == 1
    assert again["holdings"][0]["shares"] == 100


def test_t1_buy_is_idempotent_when_request_replayed(workstation):
    for _ in range(2):
        assert (
            workstation.post(
                "/api/portfolio/holding",
                json={
                    "code": "600000",
                    "shares": 100,
                    "cost": 10,
                    "operation_id": "same-buy",
                },
            ).status_code
            == 200
        )
    assert pf.get_portfolio()["holdings"][0]["shares"] == 100
    response = workstation.post(
        "/api/portfolio/holding",
        json={"code": "600000", "shares": 200, "cost": 10, "operation_id": "same-buy"},
    )
    assert response.status_code == 400


def test_t1_alert_sale_recovers_when_event_write_fails(workstation, monkeypatch):
    pf.add_holding("600000", 100, 10, "2026-10-22")
    event = ae.upsert_from_reminders([_reminder()])[0]
    ae.upsert_from_reminders([{**_reminder(), "level": "L5", "rule_id": "600000-L5"}])
    original_save = ae._save
    monkeypatch.setattr(
        ae, "_save", lambda events: (_ for _ in ()).throw(OSError("disk failed"))
    )
    with pytest.raises(OSError, match="disk failed"):
        ae.close_from_event(event["id"], "2026-10-23", 9, 100)
    assert pf.get_portfolio()["holdings"] == []
    monkeypatch.setattr(ae, "_save", original_save)
    # A background check can observe the committed sale before the retry and
    # expire the still-OPEN alert. Recovery must use the portfolio sale receipt.
    ae.upsert_from_reminders([], active_symbols=set())
    assert next(row for row in ae.list_events() if row["id"] == event["id"])["status"] == "EXPIRED"
    response = workstation.post(
        f"/api/alerts/{event['id']}/close",
        json={"date": "2026-10-23", "price": 9, "shares": 100},
    )
    assert response.status_code == 200
    assert response.json()["data"]["event"]["status"] == "HANDLED"
    assert len(response.json()["data"]["portfolio"]["closed"]) == 1
    assert not ae.list_events(status="OPEN")
    replay = workstation.post(
        f"/api/alerts/{event['id']}/close",
        json={"date": "2026-10-23", "price": 9, "shares": 100},
    )
    assert replay.status_code == 200
    assert len(replay.json()["data"]["portfolio"]["closed"]) == 1


@pytest.mark.parametrize("status", ["HANDLED", "IGNORED", "EXPIRED"])
def test_t1_alert_sale_rejects_when_terminal_without_sale_receipt(workstation, status):
    pf.add_holding("600000", 100, 10, "2026-10-22")
    event = ae.upsert_from_reminders([_reminder()])[0]
    if status == "EXPIRED":
        ae.upsert_from_reminders([], checked_rules={"600000-L1"})
    else:
        ae.transition(event["id"], "handled" if status == "HANDLED" else "ignored")
    response = workstation.post(
        f"/api/alerts/{event['id']}/close",
        json={"date": "2026-10-23", "price": 9, "shares": 100},
    )
    assert response.status_code == 409
    assert pf.get_portfolio()["holdings"][0]["shares"] == 100
    assert pf.get_portfolio()["closed"] == []


def test_t1_alert_sale_rejects_when_symbol_reentered_with_different_position(workstation):
    pf.add_holding("600000", 100, 10, "2026-10-22")
    old_id = pf.get_portfolio()["holdings"][0]["position_id"]
    event = ae.upsert_from_reminders([{**_reminder(), "position_id": old_id}])[0]
    pf.close_position("600000", "2026-10-23", 9, 100, 10, "global-old-sale")
    pf.add_holding("600000", 100, 11, "2026-10-22")
    new_id = pf.get_portfolio()["holdings"][0]["position_id"]
    assert new_id != old_id
    response = workstation.post(
        f"/api/alerts/{event['id']}/close",
        json={"date": "2026-10-23", "price": 9, "shares": 100},
    )
    assert response.status_code == 409
    assert "旧持仓" in response.json()["detail"]
    assert pf.get_portfolio()["holdings"][0]["position_id"] == new_id
    assert pf.get_portfolio()["holdings"][0]["shares"] == 100
    assert len(pf.get_portfolio()["closed"]) == 1


def test_t1_alert_sale_recovers_when_position_reentered_after_committed_sale(workstation, monkeypatch):
    pf.add_holding("600000", 100, 10, "2026-10-22")
    old_id = pf.get_portfolio()["holdings"][0]["position_id"]
    event = ae.upsert_from_reminders([{**_reminder(), "position_id": old_id}])[0]
    original_save = ae._save
    monkeypatch.setattr(ae, "_save", lambda events: (_ for _ in ()).throw(OSError("disk failed")))
    with pytest.raises(OSError):
        ae.close_from_event(event["id"], "2026-10-23", 9, 100)
    monkeypatch.setattr(ae, "_save", original_save)
    ae.upsert_from_reminders([], active_symbols=set())
    pf.add_holding("600000", 100, 11, "2026-10-22")
    new_id = pf.get_portfolio()["holdings"][0]["position_id"]
    result = ae.close_from_event(event["id"], "2026-10-23", 9, 100)
    assert result["event"]["status"] == "HANDLED"
    assert result["portfolio"]["holdings"][0]["position_id"] == new_id
    assert result["portfolio"]["holdings"][0]["shares"] == 100
    assert len(result["portfolio"]["closed"]) == 1


def test_t1_check_reports_gaps_when_history_missing(workstation, monkeypatch):
    from aqsp.data import source_factory

    class EmptySource:
        def fetch_daily(self, symbols, start, end, adjust):
            assert adjust == ""
            return {}

    monkeypatch.setattr(source_factory, "build_data_source", lambda name, **kwargs: EmptySource())
    pf.add_holding("600000", 100, 10, "2026-10-22")
    result = workstation.post("/api/live-risk/check", json={"notify": False})
    assert result.status_code == 200
    data = result.json()["data"]
    assert set(data["checks"]["checked"]) == {"600000:L1", "600000:L5"}
    assert {r["level"] for r in data["checks"]["skipped"]} == {"L2", "L4"}
    inbox = workstation.get("/api/alerts").json()["data"]
    assert inbox["last_checked_at"] == data["last_checked_at"]
    assert inbox["last_check"] == data["checks"]


@pytest.mark.parametrize("tenant_id", ["local", "u_" + "a" * 20])
def test_t1_history_uses_tenant_cache_when_scheduler_cache_unavailable(
    workstation, monkeypatch, tmp_path, tenant_id
):
    from pathlib import Path
    from aqsp.data import source_factory

    shared = tmp_path / "scheduler-cache.db"
    shared.write_text("scheduler-owned-cache", encoding="utf-8")
    monkeypatch.setenv("AQSP_CACHE_DB", str(shared))
    captured = []

    class EmptySource:
        def fetch_daily(self, symbols, start, end, adjust):
            assert adjust == ""
            return {}

    def build(name, *, cache):
        assert name == "tencent"
        captured.append(cache.db_path)
        return EmptySource()

    monkeypatch.setattr(source_factory, "build_data_source", build)
    token = tenant.current_tenant.set(tenant_id)
    try:
        pf.add_holding("600000", 100, 10, "2026-10-22")
        import live_risk_service as service

        result = service.check_positions(notify=False)
        expected = Path(pf._tenant_dir()) / "live-risk-history.db"
        assert captured == [expected]
        assert expected.is_file()
        assert shared.read_text(encoding="utf-8") == "scheduler-owned-cache"
        assert {row["reason"] for row in result["checks"]["skipped"]} == {"缺少不复权日线"}
    finally:
        tenant.current_tenant.reset(token)


def test_t1_readonly_check_preserves_outbound_when_background_follows(
    workstation, monkeypatch
):
    import live_risk_service as service
    import aqsp.notification_runtime as notification_runtime
    from aqsp.notifier import NotifyResult

    pf.add_holding("600000", 100, 10, "2026-10-22")
    for level in ("L2", "L4", "L5"):
        pf.update_risk_rule(f"600000-{level}", {"enabled": False})
    calls = []
    monkeypatch.setattr(
        notification_runtime,
        "dispatch_notification_once",
        lambda *args, **kwargs: (
            calls.append(kwargs) or [NotifyResult("test", True, "stub")]
        ),
    )
    assert len(service.check_positions(notify=False)["new_events"]) == 1
    assert not calls
    assert service.check_positions(notify=True)["notify"]
    assert len(calls) == 1
    assert service.check_positions(notify=True)["notify"] == []
    assert len(calls) == 1


def test_t1_service_groups_delivery_when_multiple_rules_trigger(workstation, monkeypatch):
    import live_risk_service as service
    import aqsp.notification_runtime as notification_runtime
    from aqsp.notifier import NotifyResult

    pf.add_holding("600000", 100, 10, "2026-10-22")
    for level in ("L2", "L4"):
        pf.update_risk_rule(f"600000-{level}", {"enabled": False})
    calls = []
    monkeypatch.setattr(
        notification_runtime,
        "dispatch_notification",
        lambda markdown, **kwargs: (
            calls.append(markdown) or [NotifyResult("synthetic", True, "stub")]
        ),
    )
    response = service.check_positions(notify=True)
    events = ae.list_events()
    assert {event["level"] for event in events} == {"L1", "L5"}
    assert len(calls) == 1 and response["notify"]
    assert all(event["last_notified_at"] for event in events)
    assert service.check_positions(notify=True)["notify"] == []
    assert len(calls) == 1


def test_t1_tenant_monitor_keeps_context_when_one_portfolio_fails(
    workstation, monkeypatch, tmp_path
):
    import live_risk_service as service
    import json

    user_a, user_b = "u_" + "a" * 20, "u_" + "b" * 20
    for user in (user_a, user_b, "invalid-tenant"):
        folder = tmp_path / "users" / user
        folder.mkdir(parents=True)
        (folder / "portfolio.json").write_text(
            json.dumps({"holdings": [{"code": "600000"}]})
        )
    called = []

    def fake_check(*, notify, strategy_sectors):
        called.append(tenant.current_tenant.get())
        assert notify is False
        if tenant.current_tenant.get() == user_b:
            raise ValueError("invalid user state")
        return {"checks": {"skipped": []}}

    monkeypatch.setattr(service, "check_positions", fake_check)
    monkeypatch.setattr(service, "_refresh_shared_strategy_sources", lambda codes: ({}, {"state": "skipped"}))
    result = service.check_existing_tenants()
    assert called == [user_a, user_b]
    assert result["tenants_checked"] == 1 and result["failed_tenants"] == 1
    assert tenant.current_tenant.get() == "local"
    assert user_a not in str(result) and "600000" not in str(result)


@pytest.mark.parametrize("has_positions", [True, False])
def test_t1_tenant_monitor_refreshes_shared_sources_once_for_positions_and_candidates(
    workstation, monkeypatch, tmp_path, has_positions
):
    import candidate_monitor
    import live_risk_service as service
    import position_strategy_evidence

    user_a = "u_" + "a" * 20
    user_folder = tmp_path / "users" / user_a
    if has_positions:
        user_folder.mkdir(parents=True)
        (tmp_path / "portfolio.json").write_text(
            '{"holdings": [{"code": "600000"}]}', encoding="utf-8"
        )
        (user_folder / "portfolio.json").write_text(
            '{"holdings": [{"code": "600000"}]}', encoding="utf-8"
        )
    candidate_store = {
        "schema_version": 1,
        "resources": {
            "600001": {
                "symbol": "600001", "version": 1, "operations": {},
                "watch": {"status": "active", "choice": "defer", "id": "w",
                          "condition": "等待", "invalidation": "失效", "evidence_version": "e",
                          "discussion_id": "d", "adopted_at": "2026-10-23T10:00:00+08:00"},
                "discussion": None,
            }
        },
    }
    (tmp_path / "candidate-observations.json").write_text(json.dumps(candidate_store), encoding="utf-8")
    refreshed, checked, positions, candidates = [], [], [], []

    async def refresh(codes, *, checked_at):
        refreshed.append(tuple(codes))
        return SimpleNamespace(to_dict=lambda: {"state": "updated", "issue": ""})

    monkeypatch.setattr(position_strategy_evidence, "refresh_strategy_sources", refresh)
    monkeypatch.setattr(
        position_strategy_evidence,
        "load_cached_strategy_sectors",
        lambda codes, *, checked_at: checked.append(tuple(codes)) or {},
    )

    def fake_positions(*, notify, strategy_sectors):
        positions.append((tenant.current_tenant.get(), dict(strategy_sectors)))
        return {"checks": {"skipped": []}}

    monkeypatch.setattr(service, "check_positions", fake_positions)
    monkeypatch.setattr(candidate_monitor, "check_candidates", lambda *, notify: candidates.append(tenant.current_tenant.get()) or {"checked": 1, "notified": 0})

    result = service.check_existing_tenants(notify=False)
    expected_codes = ("600000", "600001") if has_positions else ("600001",)
    assert refreshed == [expected_codes]
    assert checked == [expected_codes]
    assert {item[0] for item in positions} == ({"local", user_a} if has_positions else set())
    assert candidates == ["local"]
    assert result["strategy_evidence_refresh"]["state"] == "updated"
    assert result["tenants_checked"] == (2 if has_positions else 1)
    if not has_positions:
        assert not (tmp_path / "portfolio.json").exists()


@pytest.mark.parametrize("state", ["busy", "partial"])
def test_t1_shared_strategy_refresh_status_does_not_block_risk_check(
    workstation, monkeypatch, state
):
    import live_risk_service as service
    import position_strategy_evidence

    pf.add_holding("600000", 100, 10, "2026-10-22")
    for level in ("L2", "L4"):
        pf.update_risk_rule(f"600000-{level}", {"enabled": False})

    async def refresh(*args, **kwargs):
        return SimpleNamespace(to_dict=lambda: {"state": state, "issue": "来源忙"})

    monkeypatch.setattr(position_strategy_evidence, "refresh_strategy_sources", refresh)
    monkeypatch.setattr(position_strategy_evidence, "load_cached_strategy_sectors", lambda *args, **kwargs: {})
    import position_context_source
    monkeypatch.setattr(position_context_source, "load_position_sectors", lambda *args: pytest.fail("monitor must not refetch industry"))
    result = service.check_existing_tenants()
    assert result["strategy_evidence_refresh"] == {"state": state, "issue": "来源忙"}
    assert result["tenants_checked"] == 1 and result["failed_tenants"] == 0
    assert {event["level"] for event in ae.list_events(status="OPEN")} == {"L1", "L5"}


def test_t1_shared_strategy_refresh_reports_unavailable_when_batch_exceeds_budget(workstation, monkeypatch):
    import live_risk_service as service
    import position_strategy_evidence

    codes = tuple(f"{index:06d}" for index in range(101))
    monkeypatch.setattr(position_strategy_evidence, "_fetch_sources", lambda *args: pytest.fail("oversized batch must not fetch"))
    cached, result = service._refresh_shared_strategy_sources(codes)
    assert cached == {} and result["state"] == "unavailable"
    assert "预算" in result["issue"]


def test_t1_tenant_monitor_keeps_collected_positions_when_candidate_store_corrupt(workstation, monkeypatch, tmp_path):
    import live_risk_service as service
    import position_strategy_evidence

    pf.add_holding("600000", 100, 10, "2026-10-22")
    for level in ("L2", "L4"):
        pf.update_risk_rule(f"600000-{level}", {"enabled": False})
    (tmp_path / "candidate-observations.json").write_text("{broken", encoding="utf-8")
    calls = []

    async def refresh(codes, *, checked_at):
        calls.append(tuple(codes))
        return SimpleNamespace(to_dict=lambda: {"state": "partial", "issue": ""})

    monkeypatch.setattr(position_strategy_evidence, "refresh_strategy_sources", refresh)
    monkeypatch.setattr(position_strategy_evidence, "load_cached_strategy_sectors", lambda *args, **kwargs: {})
    result = service.check_existing_tenants()
    assert calls == [("600000",)]
    assert result["failed_tenants"] == 1
    assert {event["level"] for event in ae.list_events(status="OPEN")} == {"L1", "L5"}


def test_t1_engine_macd_cross_when_raw_history_ends_below_signal(workstation):
    pf.add_holding("600000", 100, 10, "2026-10-22")
    rules = [r for r in pf.get_risk_rules() if r["level"] == "L4"]
    closes = list(range(10, 50)) + [10]
    history = pd.DataFrame(
        {"date": pd.date_range("2026-09-01", periods=len(closes)), "close": closes}
    )
    reminders = run_live_risk_check(
        [{"code": "600000", "cost": 10, "shares": 100}],
        {"600000": 10},
        position_rules=rules,
        price_history={"600000": history},
    )
    assert len(reminders) == 1 and reminders[0].level == "L4"


def test_t1_new_position_and_new_cost_have_distinct_conditions(workstation):
    def evaluate():
        current = pf.get_portfolio()["holdings"]
        rules = [r for r in pf.get_risk_rules() if r["level"] == "L1"]
        reminders = run_live_risk_check(current, {"600000": 9}, position_rules=rules)
        return ae.upsert_from_reminders([r.to_dict() for r in reminders], checked_rules={"600000-L1"})

    pf.add_holding("600000", 100, 10, "2026-10-22")
    first = evaluate()[0]
    ae.transition(first["id"], "ignored")
    assert evaluate() == []
    pf.add_holding("600000", 100, 20, "2026-10-22")
    changed_cost = evaluate()[0]
    assert changed_cost["id"] != first["id"]
    ae.close_from_event(changed_cost["id"], "2026-10-23", 9, 200)
    pf.add_holding("600000", 100, 10, "2026-10-22")
    reentered = evaluate()[0]
    assert reentered["position_id"] != first["position_id"]
