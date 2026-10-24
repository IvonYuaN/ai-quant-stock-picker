"""Tenant-safe watchlist removal audit and stale risk-event cleanup."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

import alert_events as ae
import app as app_module
import portfolio as pf
import watchlist


@pytest.fixture
def workstation(tmp_path, monkeypatch):
    monkeypatch.setattr(watchlist, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ae, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(pf, "today_shanghai", lambda: date(2026, 10, 23))
    monkeypatch.setattr(
        pf.astock,
        "tencent_quote",
        lambda codes: {code: {"name": code, "price": 9} for code in codes},
    )
    return TestClient(app_module.app)


def test_watchlist_removal_is_idempotent_and_readable(workstation: TestClient) -> None:
    first = workstation.post(
        "/api/watchlist/removals",
        json={"codes": ["600001", "600001", "000858"], "operation_id": "remove-1"},
    )
    assert first.status_code == 200
    record = first.json()["data"]["record"]
    assert record["codes"] == ["600001", "000858"]
    assert record["removed_at"].endswith("+08:00")

    replay = workstation.post(
        "/api/watchlist/removals",
        json={"codes": ["600001", "000858"], "operation_id": "remove-1"},
    )
    assert replay.status_code == 200
    assert replay.json()["data"]["record"] == record
    assert workstation.get("/api/watchlist/removals").json()["data"]["records"] == [record]


def test_watchlist_cors_allows_tenant_identity_header(workstation: TestClient) -> None:
    response = workstation.options(
        "/api/watchlist/removals",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-user-id",
        },
    )
    assert response.status_code == 200
    assert "x-user-id" in response.headers.get("access-control-allow-headers", "").lower()


@pytest.mark.parametrize("codes", [[], ["60001"], ["600001", "bad"]])
def test_watchlist_removal_rejects_invalid_codes(
    workstation: TestClient, codes: list[str]
) -> None:
    response = workstation.post("/api/watchlist/removals", json={"codes": codes})
    assert response.status_code in {400, 422}
    assert watchlist.list_removals() == []


def test_watchlist_removal_journal_is_tenant_isolated(workstation: TestClient) -> None:
    local = workstation.post(
        "/api/watchlist/removals", json={"codes": ["600001"], "operation_id": "local"}
    )
    assert local.status_code == 200
    other_headers = {"X-User-Id": "other-browser"}
    assert workstation.get("/api/watchlist/removals", headers=other_headers).json()["data"]["records"] == []
    other = workstation.post(
        "/api/watchlist/removals",
        json={"codes": ["600002"], "operation_id": "other"},
        headers=other_headers,
    )
    assert other.status_code == 200
    assert [row["codes"] for row in workstation.get("/api/watchlist/removals").json()["data"]["records"]] == [["600001"]]


def test_remove_holding_expires_open_events_for_removed_position(
    workstation: TestClient,
) -> None:
    pf.add_holding("600001", 100, 10, "2026-10-22")
    position_id = pf.get_portfolio()["holdings"][0]["position_id"]
    event = ae.upsert_from_reminders(
        [
            {
                "symbol": "600001",
                "level": "L1",
                "rule_id": "600001-L1",
                "position_id": position_id,
                "current_price": 9,
                "threshold_price": 9.5,
                "freq_mode": "once",
                "sellable_now": True,
                "basis_json": {"rule": "cost*0.95", "actual": "9"},
            }
        ]
    )[0]
    pf.remove_holding("600001")
    current = ae.list_events()[0]
    assert current["id"] == event["id"]
    assert current["status"] == "EXPIRED"
    assert current["condition_active"] is False
    assert "持仓已移除" in current["handled_note"]
