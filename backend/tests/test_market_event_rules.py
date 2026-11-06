from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import alert_events
import app as app_module
import market_event_rules as rules
import portfolio as pf
import tenant
from market import ShortTermEmotionSnapshot

NOW = datetime.fromisoformat("2026-11-03T10:00:00+08:00")


def _snapshot(
    *,
    count: int = 3,
    date: str = "2026-11-03",
    freshness: str = "fresh",
) -> ShortTermEmotionSnapshot:
    return ShortTermEmotionSnapshot(
        data={
            "date": date,
            "abnormal_pools": {
                "limit_up": {
                    "count": count,
                    "items": [{"code": "600001", "name": "样例股份"}],
                },
                "blown_up": {"count": 0, "items": []},
                "limit_down": {"count": 0, "items": []},
            },
        },
        source="eastmoney_push2ex",
        cached_at="2026-11-03T09:59:00+08:00",
        age_seconds=60,
        freshness=freshness,  # type: ignore[arg-type]
    )


@pytest.fixture
def rule_store(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(alert_events, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(rules, "now_shanghai", lambda: NOW)
    token = tenant.current_tenant.set("local")
    yield tmp_path
    tenant.current_tenant.reset(token)


def _create(**overrides) -> dict:
    payload = {
        "event_type": "limit_up",
        "min_count": 2,
        "severity": "warn",
        "cooldown_minutes": 15,
        "note": "市场状态供我核验",
    }
    payload.update(overrides)
    return rules.create_rule(payload)


def _check(snapshot: ShortTermEmotionSnapshot) -> dict:
    return rules.check_rules(snapshot, checked_at=NOW)


def test_market_event_rule_writes_one_deduplicated_manual_review_event(rule_store):
    rule = _create()

    result = _check(_snapshot())
    created = alert_events.upsert_market_event_rule_events(
        result["triggered"], checked_rule_ids=result["checked_rule_ids"]
    )

    assert result["checked"] == 1
    assert len(created) == 1
    assert created[0]["kind"] == "market_event"
    assert created[0]["status"] == "OPEN"
    assert created[0]["market_event_rule_id"] == rule["id"]
    assert "涨停池标的数 ≥ 2" == created[0]["basis_json"]["rule"]
    assert len(alert_events.upsert_market_event_rule_events(
        result["triggered"], checked_rule_ids=result["checked_rule_ids"]
    )) == 0
    assert len(alert_events.list_events(status="OPEN")) == 1
    assert alert_events.pending_notifications() == []
    alert_events.upsert_from_reminders([], active_symbols=set())
    assert len(alert_events.list_events(status="OPEN")) == 1


def test_stale_or_wrong_day_market_snapshot_preserves_open_event(rule_store):
    _create()
    hit = _check(_snapshot())
    alert_events.upsert_market_event_rule_events(
        hit["triggered"], checked_rule_ids=hit["checked_rule_ids"]
    )

    stale = _check(_snapshot(freshness="stale"))
    wrong_day = _check(_snapshot(date="2026-11-02"))
    alert_events.upsert_market_event_rule_events(
        stale["triggered"], checked_rule_ids=stale["checked_rule_ids"]
    )
    alert_events.upsert_market_event_rule_events(
        wrong_day["triggered"], checked_rule_ids=wrong_day["checked_rule_ids"]
    )

    assert stale["checked_rule_ids"] == set()
    assert stale["skipped"][0]["reason"] == "市场异动池缓存不是新鲜数据"
    assert wrong_day["checked_rule_ids"] == set()
    assert alert_events.list_events(status="OPEN")[0]["condition_active"] is True


def test_market_event_rule_expires_open_event_after_fresh_pool_falls_below_threshold(
    rule_store,
):
    _create()
    hit = _check(_snapshot())
    alert_events.upsert_market_event_rule_events(
        hit["triggered"], checked_rule_ids=hit["checked_rule_ids"]
    )

    clear = _check(_snapshot(count=1))
    assert clear["triggered"] == []
    alert_events.upsert_market_event_rule_events(
        clear["triggered"], checked_rule_ids=clear["checked_rule_ids"]
    )

    event = alert_events.list_events()[0]
    assert event["status"] == "EXPIRED"
    assert event["condition_active"] is False


def test_market_event_rule_api_pausing_rule_expires_its_open_event(
    rule_store,
):
    rule = _create()
    hit = _check(_snapshot())
    alert_events.upsert_market_event_rule_events(
        hit["triggered"], checked_rule_ids=hit["checked_rule_ids"]
    )

    response = TestClient(app_module.app).patch(
        f"/api/market-event-rules/{rule['id']}",
        json={"status": "paused", "expected_revision": rule["revision"]},
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "paused"
    assert alert_events.list_events(status="OPEN") == []
    assert alert_events.list_events()[0]["status"] == "EXPIRED"


def test_market_event_rules_are_tenant_isolated(rule_store):
    local_rule = _create()
    user_token = tenant.current_tenant.set("u_0123456789abcdef0123")
    try:
        assert rules.list_rules() == []
        user_rule = _create(event_type="limit_down", min_count=1)
        assert user_rule["id"] != local_rule["id"]
    finally:
        tenant.current_tenant.reset(user_token)
    assert [item["id"] for item in rules.list_rules()] == [local_rule["id"]]


def test_check_existing_tenants_discovers_market_only_rule_store(
    rule_store, monkeypatch
):
    active_rule = _create()
    store = rule_store / "market_event_rules.json"
    store.write_text(json.dumps({"version": 1, "items": [active_rule]}), encoding="utf-8")
    monkeypatch.setattr(
        "live_risk_service.check_positions",
        lambda **_kwargs: {
            "checks": {"skipped": []},
            "candidate_observations": {"skipped": []},
            "price_observation_rules": {"skipped": []},
            "market_event_rules": {"skipped": []},
        },
    )

    import live_risk_service

    result = live_risk_service.check_existing_tenants()

    assert result["tenants_checked"] == 1
    assert result["failed_tenants"] == 0
