"""开仓日期与部分手动覆盖不得绕过 T+1 提醒降格。"""

from __future__ import annotations

import copy
from datetime import date

import pytest
from fastapi.testclient import TestClient

import alert_events as ae
import app as app_module
import portfolio as pf


@pytest.fixture
def t1_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ae, "_file", lambda: str(tmp_path / "alert_events.json"))
    monkeypatch.setattr(pf, "_tenant_id", lambda: "local")
    monkeypatch.setattr(pf, "today_shanghai", lambda: date(2026, 10, 23))
    monkeypatch.setattr(
        pf.astock,
        "tencent_quote",
        lambda codes: {code: {"name": code, "price": 9.0} for code in codes},
    )
    return TestClient(app_module.app)


@pytest.mark.parametrize("overrides", [{}, {"600036": 0}])
def test_live_risk_keeps_t1_when_override_omits_new_holding(
    t1_client,
    monkeypatch,
    overrides,
) -> None:
    today = date(2026, 10, 23)
    pf_data = {
        "holdings": [
            {
                "code": "600000",
                "shares": 100,
                "cost": 10.0,
                "price": 9.0,
                "opened_at": today.isoformat(),
            },
            {
                "code": "600036",
                "shares": 100,
                "cost": 10.0,
                "price": 9.0,
                "opened_at": "2026-10-22",
            },
        ],
    }
    monkeypatch.setattr(pf, "get_portfolio", lambda: copy.deepcopy(pf_data))

    response = t1_client.post(
        "/api/live-risk/check",
        json={"holding_days": overrides, "notify": False},
    )

    assert response.status_code == 200
    reminders = {row["symbol"]: row for row in response.json()["data"]["reminders"]}
    assert reminders["600000"]["sellable_now"] is False
    assert reminders["600000"]["action_hint"] == "明日生效"
    assert reminders["600036"]["sellable_now"] is ("600036" not in overrides)


@pytest.mark.parametrize(
    "opened_at", ["2026-10-24", "2026-02-30", "20261023", "2026-1-2", "bad", ""]
)
def test_portfolio_rejects_opened_at_when_invalid_without_writing(
    t1_client,
    opened_at,
) -> None:
    response = t1_client.post(
        "/api/portfolio/holding",
        json={"code": "600000", "shares": 100, "cost": 10.0, "opened_at": opened_at},
    )
    assert response.status_code == 400
    assert pf._load()["holdings"] == []


@pytest.mark.parametrize("opened_at", [None, "2026-10-23", "2026-10-22", "2024-02-29"])
def test_portfolio_keeps_opened_at_when_valid_or_omitted(
    t1_client,
    opened_at,
) -> None:
    response = t1_client.post(
        "/api/portfolio/holding",
        json={"code": "600000", "shares": 100, "cost": 10.0, "opened_at": opened_at},
    )
    assert response.status_code == 200
    assert pf._load()["holdings"][0]["opened_at"] == (opened_at or "2026-10-23")


def test_portfolio_preserves_existing_holding_when_added_date_is_invalid(
    t1_client,
) -> None:
    pf.add_holding("600000", 100, 10.0, "2026-10-22")
    original = pf._load()

    with pytest.raises(ValueError, match="开仓日期"):
        pf.add_holding("600000", 50, 12.0, "2026-10-24")

    assert pf._load() == original


def test_portfolio_resolves_days_when_legacy_and_dated_holdings_are_mixed(
    t1_client,
) -> None:
    holdings = [
        {"code": "600000", "opened_at": "2026-10-23"},
        {"code": "600036", "opened_at": "2026-10-22"},
        {"code": "000001"},
        {"code": "600519", "opened_at": None},
    ]
    original = copy.deepcopy(holdings)
    assert pf.resolve_holding_days(holdings, {"600036": 0, "000001": 5}) == {
        "600000": 0,
        "600036": 0,
        "000001": 5,
    }
    assert holdings == original


@pytest.mark.parametrize("days", [-1, 0.5, "1", None, True])
def test_portfolio_rejects_days_when_direct_override_is_invalid(
    t1_client,
    days,
) -> None:
    with pytest.raises(ValueError, match="非负整数"):
        pf.resolve_holding_days([], {"600000": days})


@pytest.mark.parametrize("days", [-1, 0.5, "1", "bad", None, True])
def test_live_risk_rejects_override_when_not_nonnegative_integer(
    t1_client,
    monkeypatch,
    days,
) -> None:
    def unexpected_read() -> dict:
        pytest.fail("无效覆盖必须在读取持仓前拒绝")

    monkeypatch.setattr(pf, "get_portfolio", unexpected_read)
    response = t1_client.post(
        "/api/live-risk/check",
        json={"holding_days": {"600000": days}, "notify": False},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "opened_at", ["2026-10-24", "2026-02-30", "20261023", "bad", "", 20261023]
)
def test_live_risk_stops_before_events_when_stored_date_is_invalid(
    t1_client,
    monkeypatch,
    opened_at,
) -> None:
    monkeypatch.setattr(
        pf,
        "get_portfolio",
        lambda: {
            "holdings": [
                {
                    "code": "600000",
                    "shares": 100,
                    "cost": 10.0,
                    "price": 9.0,
                    "opened_at": opened_at,
                },
            ]
        },
    )
    response = t1_client.post("/api/live-risk/check", json={"notify": False})
    assert response.status_code == 409
    assert "600000" in response.json()["detail"]
    assert t1_client.get("/api/alerts").json()["data"]["open_count"] == 0
