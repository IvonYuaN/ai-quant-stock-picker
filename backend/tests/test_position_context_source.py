"""Exercise real portfolio/service wiring with synthetic, source-timed evidence."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import alert_events as ae
import app as app_module
import decision_context
import live_risk_service as service
import market
import portfolio as pf
import position_context_source as source
import tenant

NOW = datetime(2026, 10, 23, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
STAMP = int(NOW.timestamp())


@pytest.fixture
def position_store(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ae, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(tenant, "current_tenant", tenant.ContextVar("position_context_test", default="local"))
    monkeypatch.setattr(pf, "today_shanghai", lambda: date(2026, 10, 23))
    monkeypatch.setattr(decision_context, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(market, "_MARKET_CACHE_DIR", str(tmp_path / "market"))
    monkeypatch.setattr(decision_context, "load_cached_market_overview", lambda: {
        "sentiment": {"date": "20261023", "up": 800, "down": 3000, "breadth": "偏弱"},
    })
    quote = {"name": "合成持仓", "price": 8, "quote_time": "20261023100000", "change_pct": -2}
    monkeypatch.setattr(pf.astock, "tencent_quote", lambda codes: {code: quote.copy() for code in codes})
    pf.add_holding("600000", 200, 10, "2026-10-22", "initial-buy")
    for level in ("L2", "L4"):
        pf.update_risk_rule(f"600000-{level}", {"enabled": False})
    return quote


def _upstream(monkeypatch, *, membership=None, sector=None):
    calls = []
    members = [{"f12": "600000", "f100": "银行", "f124": STAMP}] if membership is None else membership
    sectors = [{"f14": "银行", "f3": -2, "f124": STAMP}] if sector is None else sector

    class Response:
        def __init__(self, rows):
            self.rows = rows

        def json(self):
            return {"data": {"diff": self.rows}}

    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        return Response(members if "/ulist.np/" in url else sectors)

    monkeypatch.setattr(source.astock, "em_get", fetch)
    return calls


def test_position_context_reaches_joint_weakness_when_portfolio_has_no_industry(position_store, monkeypatch):
    calls = _upstream(monkeypatch)
    assert "industry" not in pf.get_portfolio()["holdings"][0]
    result = service.check_positions(notify=False)
    assert {row["level"] for row in result["reminders"]} == {"L1", "L5"}
    for event in ae.list_events():
        context = event["position_context"]
        assert context["name"] == "合成持仓"
        assert context["sector"]["status"] == context["stock"]["status"] == "falling"
        assert "银行" in context["sector"]["summary"]
        assert context["sector"]["as_of"] == NOW.isoformat()
        assert "同源个股行业归属" in context["sector"]["source"]
        assert "大盘、板块和个股走弱" in context["assessment"]["title"]
        assert context["assessment"]["t_plan"]["eligible"] is False
    service.check_positions(notify=False)
    assert len(calls) == 2  # batched source + board; second check uses shared cache
    assert calls[0][1]["params"]["secids"] == "1.600000"
    assert all(call[1]["timeout"] == source.PositionContextSourceConfig().timeout_seconds for call in calls)
    assert "industry" not in pf._load()["holdings"][0]


@pytest.mark.parametrize("members,sectors", [
    ([], None),
    ([{"f12": "600001", "f100": "银行", "f124": STAMP}], None),
    ([{"f12": "600000", "f100": "银行", "f124": STAMP - 86400}], None),
    ([{"f12": "600000", "f100": "银行"}], None),
    (None, [{"f14": "电力", "f3": -2, "f124": STAMP}]),
    (None, [{"f14": "银行", "f3": -2, "f124": STAMP - 86400}]),
    (None, [{"f14": "银行", "f3": -2}]),
    (None, [{"f14": "银行", "f3": "-", "f124": STAMP}]),
])
def test_position_context_keeps_unknown_when_membership_or_sector_unverified(position_store, monkeypatch, members, sectors):
    _upstream(monkeypatch, membership=members, sector=sectors)
    result = service.check_positions(notify=False)
    assert result["reminders"]
    for row in result["reminders"]:
        context = row["position_context"]
        assert context["sector"]["status"] == "unknown"
        assert "大盘、板块和个股走弱" not in context["assessment"]["title"]
        assert context["assessment"]["t_plan"]["eligible"] is None


def test_position_context_preserves_risk_when_optional_source_fails(position_store, monkeypatch):
    calls = []

    def failed(*args, **kwargs):
        calls.append(args)
        raise TimeoutError("synthetic timeout")

    monkeypatch.setattr(source.astock, "em_get", failed)
    result = service.check_positions(notify=False)
    assert {row["level"] for row in result["reminders"]} == {"L1", "L5"}
    assert all(row["position_context"]["sector"]["status"] == "unknown" for row in result["reminders"])
    service.check_positions(notify=False)
    assert len(calls) == 1  # failure is negatively cached, not retried per rule/check


def test_position_context_ignores_corrupt_or_unrequested_cached_rows_when_cache_loaded(monkeypatch):
    evidence = {"industry": "银行", "as_of": NOW.isoformat()}
    monkeypatch.setattr(market, "_cached", lambda *args: {
        "600000": evidence, "600001": "broken", "000001": evidence,
    })
    assert source.load_position_sectors(["600000", "600001"]) == {"600000": evidence}


@pytest.mark.parametrize("stamp", [None, "bad", "20261021100000", "20261023090000", "20261023100100"])
def test_position_risk_does_not_notify_or_observe_peak_when_quote_unverifiable(position_store, monkeypatch, stamp):
    _upstream(monkeypatch)
    service.check_positions(notify=False)
    before = {event["id"]: event for event in ae.list_events()}
    peaks = {rule["id"]: rule["high_water_price"] for rule in pf.get_risk_rules() if rule["level"] == "L5"}
    position_store.update(price=99, quote_time=stamp)
    captured = []
    import aqsp.notification_runtime as runtime

    monkeypatch.setattr(runtime, "dispatch_notification", lambda *args, **kwargs: captured.append(args) or pytest.fail("stale quote must not notify"))
    result = service.check_positions(notify=True)
    assert result["reminders"] == [] and result["notify"] == []
    assert not result["checks"]["checked"]
    assert {row["level"] for row in result["checks"]["skipped"]} == {"L1", "L5"}
    assert not captured
    assert {event["id"]: event for event in ae.list_events()} == before
    assert {rule["id"]: rule["high_water_price"] for rule in pf.get_risk_rules() if rule["level"] == "L5"} == peaks


def test_position_sale_handles_full_position_when_global_recorder_used(position_store):
    pid = pf.get_portfolio()["holdings"][0]["position_id"]
    ae.upsert_from_reminders([{"symbol": "600000", "level": level, "position_id": pid, "rule_id": f"600000-{level}", "basis_json": {"rule": "synthetic", "actual": "8"}} for level in ("L1", "L5")])
    client = TestClient(app_module.app)
    payload = {"code": "600000", "date": "2026-10-23", "price": 9, "shares": 200, "cost": 10, "operation_id": "global-sale"}
    for _ in range(2):
        result = client.post("/api/portfolio/close", json=payload)
        assert result.status_code == 200
        assert result.json()["data"]["holdings"] == []
    assert len(pf.get_portfolio()["closed"]) == 1
    assert all(event["status"] == "HANDLED" for event in ae.list_events())
    assert all(event["operation_id"] == "global-sale" for event in ae.list_events())


def test_position_sale_retains_risk_when_only_partial_sale_recorded(position_store):
    pid = pf.get_portfolio()["holdings"][0]["position_id"]
    event = ae.upsert_from_reminders([{"symbol": "600000", "level": "L1", "position_id": pid, "basis_json": {"rule": "synthetic", "actual": "8"}}])[0]
    result = ae.close_from_operation("600000", "2026-10-23", 9, 100, 10, "partial-sale")
    assert result["holdings"][0]["shares"] == 100
    current = ae.list_events()[0]
    assert current["id"] == event["id"] and current["status"] == "OPEN"
    assert current["last_sale"]["shares"] == 100


def test_position_sale_handles_legacy_event_when_full_position_closed(position_store):
    ae.upsert_from_reminders([{"symbol": "600000", "level": "L1", "basis_json": {"rule": "legacy", "actual": "8"}}])
    ae.close_from_operation("600000", "2026-10-23", 9, 200, 10, "legacy-sale")
    assert ae.list_events()[0]["status"] == "HANDLED"


def test_position_sale_handles_legacy_holding_when_neither_record_has_id(position_store):
    pf._save({"holdings": [{"code": "600000", "cost": 10, "shares": 200}]})
    ae.upsert_from_reminders([{"symbol": "600000", "level": "L1", "basis_json": {"rule": "legacy", "actual": "8"}}])
    ae.close_from_operation("600000", "2026-10-23", 9, 200, 10, "legacy-no-id")
    assert ae.list_events()[0]["status"] == "HANDLED"


@pytest.mark.parametrize("during", ["before_observe", "after_evaluate"])
def test_position_check_does_not_reopen_alert_when_position_sold_during_check(position_store, monkeypatch, during):
    _upstream(monkeypatch)
    service.check_positions(notify=False)
    if during == "before_observe":
        observe = pf.observe_risk_prices

        def hook(prices, **kwargs):
            ae.close_from_operation("600000", "2026-10-23", 9, 200, 10, "concurrent-sale")
            return observe(prices, **kwargs)

        monkeypatch.setattr(pf, "observe_risk_prices", hook)
    else:
        def source_hook(codes):
            ae.close_from_operation("600000", "2026-10-23", 9, 200, 10, "concurrent-sale")
            return {}

        monkeypatch.setattr(source, "load_position_sectors", source_hook)
    result = service.check_positions(notify=True)
    assert pf.get_portfolio()["holdings"] == []
    assert result["reminders"] == result["new_events"] == result["notify"] == []
    assert ae.list_events(status="OPEN") == []
    assert not result["checks"]["checked"]
    assert any("检查期间" in row["reason"] for row in result["checks"]["skipped"])


@pytest.mark.parametrize("mutation", ["reenter", "add", "rule"])
def test_position_check_discards_evidence_when_position_or_rule_changes(position_store, monkeypatch, mutation):
    def hook(codes):
        if mutation == "reenter":
            ae.close_from_operation("600000", "2026-10-23", 9, 200, 10, "race-sale")
            pf.add_holding("600000", 100, 12, "2026-10-23", "race-reentry")
        elif mutation == "add":
            pf.add_holding("600000", 100, 12, "2026-10-23", "race-add")
        else:
            for level in ("L1", "L5"):
                pf.update_risk_rule(f"600000-{level}", {"enabled": False})
        return {}

    monkeypatch.setattr(source, "load_position_sectors", hook)
    result = service.check_positions(notify=True)
    assert result["reminders"] == result["new_events"] == result["notify"] == []
    assert ae.list_events(status="OPEN") == []


def test_position_peak_ignores_old_price_when_position_reentered_before_observation(position_store):
    snapshot = pf.get_portfolio()["holdings"]
    ae.close_from_operation("600000", "2026-10-23", 9, 200, 10, "sold-old")
    pf.add_holding("600000", 100, 12, "2026-10-23", "reenter-new")
    pf.observe_risk_prices({"600000": 99}, expected_holdings=snapshot)
    assert next(rule for rule in pf.get_risk_rules() if rule["level"] == "L5")["high_water_price"] == 12


def test_position_sale_recovers_event_write_when_same_code_reentered(position_store, monkeypatch):
    old_id = pf.get_portfolio()["holdings"][0]["position_id"]
    old = ae.upsert_from_reminders([{"symbol": "600000", "level": "L1", "position_id": old_id, "rule_id": "600000-L1", "basis_json": {"rule": "synthetic", "actual": "8"}}])[0]
    save = ae._save
    monkeypatch.setattr(ae, "_save", lambda events: (_ for _ in ()).throw(OSError("synthetic disk full")))
    with pytest.raises(OSError):
        ae.close_from_operation("600000", "2026-10-23", 9, 200, 10, "recover-sale")
    monkeypatch.setattr(ae, "_save", save)
    pf.add_holding("600000", 100, 11, "2026-10-23", "new-buy")
    new_id = pf.get_portfolio()["holdings"][0]["position_id"]
    new = ae.upsert_from_reminders([{"symbol": "600000", "level": "L1", "position_id": new_id, "rule_id": "600000-L1", "basis_json": {"rule": "synthetic", "actual": "8"}}])[0]
    result = ae.close_from_operation("600000", "2026-10-23", 9, 200, 10, "recover-sale")
    assert result["holdings"][0]["shares"] == 100
    assert len(result["closed"]) == 1
    assert next(event for event in ae.list_events() if event["id"] == old["id"])["status"] == "HANDLED"
    assert next(event for event in ae.list_events() if event["id"] == new["id"])["status"] == "OPEN"
