"""T+1 workstation user-rule, frequency and actual-sale invariants."""

from __future__ import annotations

from datetime import date, datetime
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
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ae, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        tenant, "current_tenant", tenant.ContextVar("workstation_test", default="local")
    )
    monkeypatch.setattr(pf, "today_shanghai", lambda: date(2026, 10, 23))
    monkeypatch.setattr(
        pf.astock,
        "tencent_quote",
        lambda codes: {c: {"name": c, "price": 9} for c in codes},
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


def test_t1_ignore_reduces_frequency_when_two_episodes(workstation):
    pf.add_holding("600000", 100, 10, "2026-10-22")
    pf.update_risk_rule("600000-L1", {"freq_mode": "always"})
    pf.record_rule_disposition("600000-L1", True)
    pf.record_rule_disposition("600000-L1", True)
    rule = next(r for r in pf.get_risk_rules() if r["level"] == "L1")
    assert rule["freq_mode"] == "daily"
    assert "连续忽略" in rule["frequency_note"]


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


def test_t1_check_reports_gaps_when_history_missing(workstation, monkeypatch):
    from aqsp.data import source_factory

    class EmptySource:
        def fetch_daily(self, symbols, start, end, adjust):
            assert adjust == ""
            return {}

    monkeypatch.setattr(source_factory, "build_data_source", lambda name: EmptySource())
    pf.add_holding("600000", 100, 10, "2026-10-22")
    result = workstation.post("/api/live-risk/check", json={"notify": False})
    assert result.status_code == 200
    data = result.json()["data"]
    assert set(data["checks"]["checked"]) == {"600000:L1", "600000:L5"}
    assert {r["level"] for r in data["checks"]["skipped"]} == {"L2", "L4"}
    inbox = workstation.get("/api/alerts").json()["data"]
    assert inbox["last_checked_at"] == data["last_checked_at"]
    assert inbox["last_check"] == data["checks"]


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

    def fake_check(*, notify):
        called.append(tenant.current_tenant.get())
        assert notify is False
        if tenant.current_tenant.get() == user_b:
            raise ValueError("invalid user state")
        return {"checks": {"skipped": []}}

    monkeypatch.setattr(service, "check_positions", fake_check)
    result = service.check_existing_tenants()
    assert called == [user_a, user_b]
    assert result["tenants_checked"] == 1 and result["failed_tenants"] == 1
    assert tenant.current_tenant.get() == "local"
    assert user_a not in str(result) and "600000" not in str(result)


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
