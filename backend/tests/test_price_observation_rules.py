from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
import pytest

import alert_events
import app as app_module
import portfolio as pf
import price_observation_rules as rules
import tenant

NOW = datetime.fromisoformat("2026-10-31T10:00:00+08:00")


@pytest.fixture
def rule_store(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(alert_events, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(rules, "now_shanghai", lambda: NOW)
    monkeypatch.setattr("decision_context.position_quote_issue", lambda quote, checked_at=None: "")
    token = tenant.current_tenant.set("local")
    yield tmp_path
    tenant.current_tenant.reset(token)


def _create(**overrides: object) -> dict:
    value: dict[str, object] = {
        "symbol": "600519", "comparator": "at_or_above", "price": 1500.0,
        "note": "量价确认后再研究",
    }
    value.update(overrides)
    return rules.create_rule(value)


def _quote(price: float, *, as_of: str = "2026-10-31T09:59:00+08:00") -> dict:
    return {"price": price, "as_of": as_of, "source": "test-feed"}


def test_indicator_observation_rule_persists_and_emits_rsi_event(rule_store):
    rules.create_rule({
        "symbol": "600519", "rule_type": "indicator", "metric": "rsi12",
        "comparator": "at_or_above", "threshold": 80, "severity": "warn",
        "cooldown_minutes": 15,
    })
    result = rules.check_rules(
        {}, checked_at=NOW,
        indicator_values={
            "600519": rules.IndicatorSnapshot(83.5, "2026-10-30", "sqlite_db")
        },
    )

    assert result["triggered"][0]["indicator_value"] == 83.5
    created = alert_events.upsert_price_observation_events(
        result["triggered"], checked_rule_ids=result["checked_rule_ids"],
    )
    assert len(created) == 1
    assert "RSI(12)" in created[0]["basis_json"]["rule"]
    assert "83.50" in created[0]["basis_json"]["actual"]
    assert created[0]["quote_source"] == "sqlite_db"


def test_indicator_observation_rule_missing_or_stale_data_preserves_open_event(rule_store):
    rule = rules.create_rule({
        "symbol": "600519", "rule_type": "indicator", "metric": "rsi12",
        "comparator": "at_or_above", "threshold": 80,
    })
    hit = rules.check_rules(
        {}, checked_at=NOW,
        indicator_values={"600519": rules.IndicatorSnapshot(90, "2026-10-30", "sqlite_db")},
    )
    event = alert_events.upsert_price_observation_events(
        hit["triggered"], checked_rule_ids=hit["checked_rule_ids"],
    )[0]

    missing = rules.check_rules(
        {}, checked_at=NOW,
        indicator_values={
            "600519": rules.IndicatorSnapshot(None, "2026-10-30", "sqlite_db", "日线数据陈旧")
        },
    )
    assert missing["checked_rule_ids"] == set()
    assert missing["skipped"] == [{"symbol": "600519", "reason": "日线数据陈旧"}]
    alert_events.upsert_price_observation_events(
        missing["triggered"], checked_rule_ids=missing["checked_rule_ids"],
    )
    stored = next(row for row in alert_events.list_events() if row["id"] == event["id"])
    assert stored["status"] == "OPEN"
    assert stored["condition_active"] is True
    assert rules.list_rules()[0]["id"] == rule["id"]


def test_indicator_observation_rule_rejects_unsupported_metric_and_threshold(rule_store):
    with pytest.raises(ValueError, match="暂只支持 RSI"):
        rules.create_rule({
            "symbol": "600519", "rule_type": "indicator", "metric": "macd_hist",
            "comparator": "at_or_above", "threshold": 0,
        })
    with pytest.raises(ValueError, match="0 至 100"):
        rules.create_rule({
            "symbol": "600519", "rule_type": "indicator", "metric": "rsi12",
            "comparator": "at_or_above", "threshold": 101,
        })


def test_indicator_observation_rule_rejects_old_as_of_even_without_source_issue(rule_store):
    rules.create_rule({
        "symbol": "600519", "rule_type": "indicator", "metric": "rsi12",
        "comparator": "at_or_above", "threshold": 80,
    })
    result = rules.check_rules(
        {}, checked_at=NOW,
        indicator_values={
            "600519": rules.IndicatorSnapshot(95, "2026-10-29", "sqlite_db")
        },
    )

    assert result["triggered"] == []
    assert result["checked_rule_ids"] == set()
    assert result["skipped"] == [{"symbol": "600519", "reason": "指标数据日期陈旧"}]


def test_price_observation_rule_hit_is_deduplicated_and_expires_after_recross(rule_store):
    rule = _create()
    result = rules.check_rules({"600519": _quote(1501)}, checked_at=NOW)
    assert result["triggered"][0]["current_price"] == 1501

    first = alert_events.upsert_price_observation_events(
        result["triggered"], checked_rule_ids={rule["id"]},
    )
    assert len(first) == 1
    assert first[0]["kind"] == "price_observation"
    assert first[0]["trigger_at"] == 1501
    assert alert_events.upsert_price_observation_events(
        result["triggered"], checked_rule_ids={rule["id"]},
    ) == []
    import live_risk_service

    handled = live_risk_service.transition_alert(first[0]["id"], "handled")
    assert handled and handled["status"] == "HANDLED"

    rules.check_rules({"600519": _quote(1499)}, checked_at=NOW)
    alert_events.upsert_price_observation_events([], checked_rule_ids={rule["id"]})
    assert alert_events.list_events()[0]["condition_active"] is False

    crossed_again = rules.check_rules({"600519": _quote(1502)}, checked_at=NOW)
    reopened = alert_events.upsert_price_observation_events(
        crossed_again["triggered"], checked_rule_ids={rule["id"]},
    )
    assert len(reopened) == 1
    assert reopened[0]["id"] != first[0]["id"]


def test_price_observation_rules_validate_duplicates_holdings_and_revision(rule_store):
    rule = _create()
    with pytest.raises(ValueError, match="已存在"):
        _create()
    with pytest.raises(rules.RuleConflict, match="已变化"):
        rules.update_rule(rule["id"], status="paused", expected_revision=2)
    paused = rules.update_rule(rule["id"], status="paused", expected_revision=1)
    assert paused and paused["status"] == "paused" and paused["revision"] == 2
    _create()
    with pytest.raises(ValueError, match="组合条件.*已存在"):
        rules.update_rule(rule["id"], status="active", expected_revision=2)

    with pf._LOCK:
        data = pf._load()
        data["holdings"] = [{"code": "600000", "name": "浦发", "shares": 100, "cost": 10}]
        pf._save(data)
    with pytest.raises(ValueError, match="持仓工作区"):
        rules.create_rule({"symbol": "600000", "comparator": "at_or_below", "price": 9})


def test_price_observation_rule_rejects_unverifiable_quote(rule_store, monkeypatch):
    rule = _create()
    valid = rules.check_rules({"600519": _quote(1501)}, checked_at=NOW)
    alert_events.upsert_price_observation_events(
        valid["triggered"], checked_rule_ids=valid["checked_rule_ids"],
    )
    monkeypatch.setattr("decision_context.position_quote_issue", lambda quote, checked_at=None: "行情已过期")
    result = rules.check_rules({"600519": _quote(1600)}, checked_at=NOW)
    assert result["triggered"] == []
    assert result["skipped"] == [{"symbol": "600519", "reason": "行情已过期"}]
    assert rules.list_rules()[0]["last_check"]["issue"] == "行情已过期"
    assert result["checked_rule_ids"] == set()
    alert_events.upsert_price_observation_events(
        result["triggered"], checked_rule_ids=result["checked_rule_ids"],
    )
    assert alert_events.list_events(status="OPEN")[0]["rule_id"] == f"price-observation:{rule['id']}"


def test_price_observation_rule_requires_quote_source(rule_store):
    _create()
    result = rules.check_rules({
        "600519": {"price": 1600, "as_of": "2026-10-31T09:59:00+08:00"},
    }, checked_at=NOW)
    assert result["triggered"] == []
    assert result["skipped"] == [{"symbol": "600519", "reason": "行情来源缺失"}]


def test_price_observation_rule_combines_conditions_when_and_or_configured(rule_store):
    rules.create_rule({
        "symbol": "600519",
        "conditions": [
            {"comparator": "at_or_above", "price": 110},
            {"comparator": "at_or_below", "price": 115},
        ],
        "combination": "all",
        "severity": "critical",
    })
    any_rule = rules.create_rule({
        "symbol": "600519",
        "conditions": [
            {"comparator": "at_or_above", "price": 110},
            {"comparator": "at_or_below", "price": 115},
        ],
        "combination": "any",
        "severity": "warn",
    })

    result = rules.check_rules({"600519": _quote(120)}, checked_at=NOW)
    assert [row["id"] for row in result["triggered"]] == [any_rule["id"]]
    created = alert_events.upsert_price_observation_events(
        result["triggered"], checked_rule_ids=result["checked_rule_ids"],
    )
    assert len(created) == 1
    assert created[0]["severity"] == "warn"
    assert "或" in created[0]["basis_json"]["rule"]
    assert "命中 1/2 条" in created[0]["basis_json"]["actual"]
    assert rules.list_rules()[0]["last_check"]["triggered"] is False
    assert rules.list_rules()[1]["last_check"]["triggered"] is True


def test_price_observation_cooldown_suppresses_repeated_crossings_when_not_expired(rule_store, monkeypatch):
    clock = {"now": NOW}
    monkeypatch.setattr(alert_events, "now_shanghai", lambda: clock["now"])
    _create(cooldown_minutes=15)

    first_hit = rules.check_rules({"600519": _quote(1501)}, checked_at=NOW)
    first = alert_events.upsert_price_observation_events(
        first_hit["triggered"], checked_rule_ids=first_hit["checked_rule_ids"],
    )
    assert len(first) == 1

    clock["now"] = NOW + timedelta(minutes=1)
    clear = rules.check_rules({"600519": _quote(1499)}, checked_at=clock["now"])
    alert_events.upsert_price_observation_events(
        clear["triggered"], checked_rule_ids=clear["checked_rule_ids"],
    )
    assert alert_events.list_events()[0]["condition_active"] is False

    clock["now"] = NOW + timedelta(minutes=10)
    crossed_during_cooldown = rules.check_rules(
        {"600519": _quote(1502)}, checked_at=clock["now"],
    )
    assert alert_events.upsert_price_observation_events(
        crossed_during_cooldown["triggered"],
        checked_rule_ids=crossed_during_cooldown["checked_rule_ids"],
    ) == []

    clock["now"] = NOW + timedelta(minutes=16)
    crossed_after_cooldown = rules.check_rules(
        {"600519": _quote(1503)}, checked_at=clock["now"],
    )
    reopened = alert_events.upsert_price_observation_events(
        crossed_after_cooldown["triggered"],
        checked_rule_ids=crossed_after_cooldown["checked_rule_ids"],
    )
    assert len(reopened) == 1
    assert reopened[0]["id"] != first[0]["id"]


def test_price_observation_rule_reads_legacy_schema_when_new_fields_are_absent(rule_store):
    rule = _create()
    payload = rules._load()
    for key in ("conditions", "combination", "severity", "cooldown_minutes"):
        payload["items"][0].pop(key)
    rules._save(payload)

    below = rules.check_rules({"600519": _quote(1499)}, checked_at=NOW)
    hit = rules.check_rules({"600519": _quote(1501)}, checked_at=NOW)

    assert below["triggered"] == []
    assert [item["id"] for item in hit["triggered"]] == [rule["id"]]


def test_price_observation_rule_hands_off_when_symbol_becomes_a_holding(rule_store):
    rule = _create()
    hit = rules.check_rules({"600519": _quote(1501)}, checked_at=NOW)
    event = alert_events.upsert_price_observation_events(
        hit["triggered"], checked_rule_ids=hit["checked_rule_ids"],
    )[0]

    handed_off = rules.check_rules(
        {}, checked_at=NOW, held_positions={"600519": "position-1"},
    )
    assert handed_off["transferred"] == [{"symbol": "600519", "position_id": "position-1"}]
    assert handed_off["checked_rule_ids"] == {rule["id"]}
    alert_events.upsert_price_observation_events(
        handed_off["triggered"], checked_rule_ids=handed_off["checked_rule_ids"],
    )
    stored = rules.list_rules()[0]
    assert stored["status"] == "transferred"
    assert stored["transferred_to_position_id"] == "position-1"
    assert stored["audit"][-1]["action"] == "position_handoff"
    assert alert_events.list_events()[0]["id"] == event["id"]
    assert alert_events.list_events()[0]["status"] == "EXPIRED"
    with pytest.raises(ValueError, match="已移交"):
        rules.update_rule(rule["id"], status="active", expected_revision=2)


def test_price_observation_rules_are_tenant_isolated(rule_store):
    created = _create()
    other = tenant.current_tenant.set("u_0123456789abcdef0123")
    try:
        assert rules.list_rules() == []
        assert alert_events.list_events() == []
    finally:
        tenant.current_tenant.reset(other)
    assert rules.list_rules()[0]["id"] == created["id"]


def test_price_rule_only_tenant_uses_existing_monitor_round(rule_store, monkeypatch):
    import astock
    import live_risk_service

    rule = _create()
    monkeypatch.setattr("decision_context.position_quote_issue", lambda quote, checked_at=None: "")
    monkeypatch.setattr(
        astock,
        "tencent_quote",
        lambda codes: {code: _quote(1501) for code in codes},
    )
    summary = live_risk_service.check_existing_tenants(notify=False)
    assert summary["tenants_checked"] == 1
    current = rules.list_rules()[0]
    assert current["id"] == rule["id"]
    assert current["last_check"]["triggered"] is True


def test_price_observation_rule_api_creates_and_pauses_with_cas(rule_store):
    client = TestClient(app_module.app)
    created = client.post("/api/price-observation-rules", json={
        "symbol": "600519",
        "conditions": [
            {"comparator": "at_or_above", "price": 1500},
            {"comparator": "at_or_below", "price": 1600},
        ],
        "combination": "all", "severity": "warn", "cooldown_minutes": 15,
        "note": "等量价确认",
    })
    assert created.status_code == 200
    item = created.json()["data"]
    assert item["conditions"][0]["price"] == 1500
    assert item["combination"] == "all"
    assert item["severity"] == "warn"
    assert item["cooldown_minutes"] == 15
    assert client.get("/api/price-observation-rules").json()["data"]["items"] == [item]
    hit = rules.check_rules({"600519": _quote(1501)}, checked_at=NOW)
    event = alert_events.upsert_price_observation_events(
        hit["triggered"], checked_rule_ids=hit["checked_rule_ids"],
    )[0]
    assert event["severity"] == "warn"
    paused = client.patch(f"/api/price-observation-rules/{item['id']}", json={
        "status": "paused", "expected_revision": 1,
    })
    assert paused.status_code == 200
    closed = next(row for row in alert_events.list_events() if row["id"] == event["id"])
    assert closed["status"] == "EXPIRED"
    assert closed["observation_resolution"] == "规则已暂停"
    retry = client.patch(f"/api/price-observation-rules/{item['id']}", json={
        "status": "paused", "expected_revision": 2,
    })
    assert retry.status_code == 200
    stale = client.patch(f"/api/price-observation-rules/{item['id']}", json={
        "status": "active", "expected_revision": 1,
    })
    assert stale.status_code == 409


def test_indicator_observation_rule_api_round_trips_rsi_contract(rule_store):
    client = TestClient(app_module.app)
    created = client.post("/api/price-observation-rules", json={
        "symbol": "600519", "rule_type": "indicator", "metric": "rsi12",
        "comparator": "at_or_above", "threshold": 80,
        "severity": "warn", "cooldown_minutes": 15,
    })

    assert created.status_code == 200
    item = created.json()["data"]
    assert item["rule_type"] == "indicator"
    assert item["metric"] == "rsi12"
    assert item["threshold"] == 80
    assert item["last_check"] is None
    assert client.get("/api/price-observation-rules").json()["data"]["items"] == [item]

    unsupported = client.post("/api/price-observation-rules", json={
        "symbol": "600519", "rule_type": "indicator", "metric": "macd_hist",
        "comparator": "at_or_above", "threshold": 0,
    })
    assert unsupported.status_code == 422
