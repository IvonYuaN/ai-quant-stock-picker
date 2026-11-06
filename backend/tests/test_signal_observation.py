from __future__ import annotations

from datetime import date, datetime
import hashlib
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import alert_events
import app as app_module
import live_risk_service
import portfolio as pf
import price_observation_rules as rules
import signal_definitions
import tenant
from aqsp.data.cache import AdjustmentFactorRecord


NOW = datetime.fromisoformat("2026-10-30T16:00:00+08:00")
AS_OF = date(2026, 10, 30)


@pytest.fixture
def signal_rule_store(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(alert_events, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(signal_definitions.pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(rules.pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(rules, "now_shanghai", lambda: NOW)
    monkeypatch.setattr("decision_context.position_quote_issue", lambda quote, checked_at=None: "")
    token = tenant.current_tenant.set("local")
    yield tmp_path
    tenant.current_tenant.reset(token)


def _signal() -> dict:
    return signal_definitions.save_signal(
        signal_id="close_above_previous",
        name="收盘高于昨收",
        kind="entry",
        enabled=True,
        conditions=[{"left": "close", "op": ">", "right": "field:prev_close"}],
        expected_revision=None,
    )


def _definition_hash(definition: dict) -> str:
    snapshot = {
        key: definition[key]
        for key in ("id", "name", "kind", "enabled", "conditions", "revision")
    }
    return hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_signal_observation_rule_pins_definition_and_emits_manual_review_event(signal_rule_store):
    first = _signal()
    rule = rules.create_rule({
        "symbol": "600519", "rule_type": "signal", "signal_id": first["id"],
        "severity": "warn", "cooldown_minutes": 15,
    })
    revised = signal_definitions.save_signal(
        signal_id=first["id"],
        name="新版收盘条件",
        kind="entry",
        enabled=True,
        conditions=[{"left": "close", "op": ">=", "right": "field:prev_close"}],
        expected_revision=first["revision"],
    )

    assert revised["revision"] == 2
    assert rule["signal_snapshot"]["revision"] == 1
    assert rule["signal_snapshot"]["name"] == "收盘高于昨收"

    signal_snapshot = rules.SignalSnapshot(
        triggered=True,
        as_of=AS_OF.isoformat(),
        source="sqlite_db",
        signal_name=rule["signal_snapshot"]["name"],
        signal_revision=rule["signal_snapshot"]["revision"],
        definition_sha256=rule["signal_snapshot_sha256"],
        current_price=1510.0,
    )
    checked = rules.check_rules(
        {}, checked_at=NOW, signal_values={("600519", first["id"]): signal_snapshot}
    )
    assert checked["checked_rule_ids"] == {rule["id"]}
    event = alert_events.upsert_price_observation_events(
        checked["triggered"], checked_rule_ids=checked["checked_rule_ids"]
    )[0]

    assert event["threshold_at"] is None
    assert event["action_hint"] == "满足条件信号，请人工复核"
    assert event["basis_json"]["signal_revision"] == 1
    assert event["basis_json"]["signal_definition_sha256"] == rule["signal_snapshot_sha256"]


def test_signal_observation_api_requires_enabled_signal_and_returns_pinned_revision(signal_rule_store):
    client = TestClient(app_module.app)
    created_definition = client.post("/api/aqsp/signal-definitions", json={
        "id": "api_signal", "name": "API 条件", "kind": "both", "enabled": True,
        "conditions": [{"left": "close", "op": ">", "right": "field:prev_close"}],
    })
    assert created_definition.status_code == 200
    created_rule = client.post("/api/price-observation-rules", json={
        "symbol": "600519", "rule_type": "signal", "signal_id": "api_signal",
    })
    assert created_rule.status_code == 200
    rule = created_rule.json()["data"]
    assert rule["signal_snapshot"]["revision"] == 1
    assert rule["signal_snapshot_sha256"] == _definition_hash(created_definition.json()["data"])

    disabled = client.post("/api/aqsp/signal-definitions", json={
        "id": "api_signal", "name": "API 条件", "kind": "both", "enabled": False,
        "conditions": [{"left": "close", "op": ">", "right": "field:prev_close"}],
        "expected_revision": 1,
    })
    assert disabled.status_code == 200
    rejected = client.post("/api/price-observation-rules", json={
        "symbol": "000001", "rule_type": "signal", "signal_id": "api_signal",
    })
    assert rejected.status_code == 400


def test_signal_observation_rule_blocks_stale_result_without_expiring_open_alert(signal_rule_store):
    definition = _signal()
    rule = rules.create_rule({"symbol": "600519", "rule_type": "signal", "signal_id": definition["id"]})
    fresh = rules.check_rules(
        {}, checked_at=NOW,
        signal_values={
            ("600519", definition["id"]): rules.SignalSnapshot(
                True, AS_OF.isoformat(), "sqlite_db", definition["name"], 1,
                rule["signal_snapshot_sha256"], 1510.0,
            )
        },
    )
    event = alert_events.upsert_price_observation_events(
        fresh["triggered"], checked_rule_ids=fresh["checked_rule_ids"]
    )[0]

    stale = rules.check_rules(
        {}, checked_at=NOW,
        signal_values={
            ("600519", definition["id"]): rules.SignalSnapshot(
                True, "2026-10-29", "sqlite_db", definition["name"], 1,
                rule["signal_snapshot_sha256"], 1510.0,
            )
        },
    )
    assert stale["checked_rule_ids"] == set()
    alert_events.upsert_price_observation_events(
        stale["triggered"], checked_rule_ids=stale["checked_rule_ids"]
    )
    stored = next(item for item in alert_events.list_events() if item["id"] == event["id"])
    assert stored["status"] == "OPEN"
    assert stale["skipped"][0]["reason"] == "条件信号数据日期陈旧"


def test_signal_observation_rule_blocks_corrupted_pinned_definition_snapshot(signal_rule_store):
    definition = _signal()
    rule = rules.create_rule({"symbol": "600519", "rule_type": "signal", "signal_id": definition["id"]})
    store_path = signal_rule_store / "price_observation_rules.json"
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    stored["items"][0]["signal_snapshot"]["name"] = "未经修订的篡改"
    store_path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
    snapshot = rules.SignalSnapshot(
        True, AS_OF.isoformat(), "sqlite_db", definition["name"], 1,
        rule["signal_snapshot_sha256"], 1510.0,
    )

    checked = rules.check_rules(
        {}, checked_at=NOW, signal_values={("600519", definition["id"]): snapshot}
    )

    assert checked["triggered"] == []
    assert checked["checked_rule_ids"] == set()
    assert checked["skipped"][0]["reason"] == "条件信号修订快照校验失败"


class _DailySource:
    name = "sqlite_db"

    def __init__(self, frame: pd.DataFrame, factors: dict[date, AdjustmentFactorRecord]):
        self.frame = frame
        self.factors = factors

    def price_mode(self) -> str:
        return "raw"

    def get_latest_daily_date(self) -> date:
        return AS_OF

    def get_latest_daily_dates(self, symbols):
        return {symbol: AS_OF for symbol in symbols}

    def fetch_daily(self, symbols, start, end, *, adjust):
        assert adjust == ""
        return {symbol: self.frame.copy() for symbol in symbols}

    def get_point_in_time_adjustment_factors(self, symbols, dates):
        return {symbol: {day: value for day, value in self.factors.items() if day in dates} for symbol in symbols}


def _monitor_rule(definition: dict) -> dict:
    snapshot = {
        key: definition[key]
        for key in ("id", "name", "kind", "enabled", "conditions", "revision")
    }
    return {
        "status": "active",
        "rule_type": "signal",
        "symbol": "600519",
        "signal_id": definition["id"],
        "signal_snapshot": snapshot,
        "signal_snapshot_sha256": _definition_hash(definition),
    }


def test_signal_monitor_aligns_prior_close_with_pit_factor_when_triggering(monkeypatch):
    definition = {
        "id": "close_above_previous", "name": "收盘高于昨收", "kind": "entry",
        "enabled": True, "revision": 1,
        "conditions": [{"left": "close", "op": ">", "right": "field:prev_close"}],
    }
    frame = pd.DataFrame({
        "date": ["2026-10-29", "2026-10-30"],
        "open": [100.0, 50.0], "high": [101.0, 51.0], "low": [99.0, 49.0],
        "close": [100.0, 50.0], "volume": [100.0, 100.0], "amount": [10000.0, 5000.0],
        "suspended": [False, False],
    })
    factors = {
        date(2026, 10, 29): AdjustmentFactorRecord(date(2026, 10, 29), 1.0, "test", None),
        AS_OF: AdjustmentFactorRecord(AS_OF, 0.5, "test", None),
    }
    source = _DailySource(frame, factors)
    monkeypatch.setattr(live_risk_service, "build_data_source", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(live_risk_service, "latest_completed_trading_day", lambda _checked: AS_OF)

    result = live_risk_service._signal_snapshots([_monitor_rule(definition)], checked_at=NOW)

    snapshot = result[("600519", definition["id"])]
    assert snapshot.issue is None
    assert snapshot.triggered is False
    assert snapshot.as_of == AS_OF.isoformat()


def test_signal_monitor_raw_daily_hit_flows_to_existing_alert_journal(monkeypatch, signal_rule_store):
    definition = _signal()
    monitor_rule = rules.create_rule({
        "symbol": "600519", "rule_type": "signal", "signal_id": definition["id"],
    })
    frame = pd.DataFrame({
        "date": ["2026-10-29", "2026-10-30"],
        "open": [100.0, 101.0], "high": [101.0, 103.0], "low": [99.0, 100.0],
        "close": [100.0, 102.0], "volume": [100.0, 110.0],
        "amount": [10000.0, 11220.0], "suspended": [False, False],
    })
    factors = {
        date(2026, 10, 29): AdjustmentFactorRecord(date(2026, 10, 29), 1.0, "test", None),
        AS_OF: AdjustmentFactorRecord(AS_OF, 1.0, "test", None),
    }
    source = _DailySource(frame, factors)
    monkeypatch.setattr(live_risk_service, "build_data_source", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(live_risk_service, "latest_completed_trading_day", lambda _checked: AS_OF)

    snapshots = live_risk_service._signal_snapshots([monitor_rule], checked_at=NOW)
    checked = rules.check_rules({}, checked_at=NOW, signal_values=snapshots)
    created_events = alert_events.upsert_price_observation_events(
        checked["triggered"], checked_rule_ids=checked["checked_rule_ids"]
    )

    assert snapshots[("600519", definition["id"])].triggered is True
    assert len(created_events) == 1
    assert created_events[0]["kind"] == "price_observation"
    assert created_events[0]["basis_json"]["signal_revision"] == definition["revision"]
    assert created_events[0]["threshold_at"] is None


def test_signal_monitor_blocks_cross_session_price_when_pit_factor_is_missing(monkeypatch):
    definition = {
        "id": "close_above_previous", "name": "收盘高于昨收", "kind": "entry",
        "enabled": True, "revision": 1,
        "conditions": [{"left": "close", "op": ">", "right": "field:prev_close"}],
    }
    frame = pd.DataFrame({
        "date": ["2026-10-29", "2026-10-30"],
        "open": [100.0, 50.0], "high": [101.0, 51.0], "low": [99.0, 49.0],
        "close": [100.0, 50.0], "volume": [100.0, 100.0], "amount": [10000.0, 5000.0],
        "suspended": [False, False],
    })
    factors = {AS_OF: AdjustmentFactorRecord(AS_OF, 0.5, "test", None)}
    source = _DailySource(frame, factors)
    monkeypatch.setattr(live_risk_service, "build_data_source", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(live_risk_service, "latest_completed_trading_day", lambda _checked: AS_OF)

    result = live_risk_service._signal_snapshots([_monitor_rule(definition)], checked_at=NOW)

    snapshot = result[("600519", definition["id"])]
    assert snapshot.triggered is None
    assert "PIT 复权因子" in str(snapshot.issue)
