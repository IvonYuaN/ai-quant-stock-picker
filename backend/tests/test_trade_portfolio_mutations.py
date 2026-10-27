from __future__ import annotations

from datetime import date

import portfolio as pf


def test_apply_holding_merges_lots_and_keeps_old_position_id(monkeypatch) -> None:
    monkeypatch.setattr(pf.risk_rules, "ensure_rules", lambda data: None)
    data = {
        "holdings": [{"code": "600000", "shares": 100, "cost": 10.0,
                      "opened_at": "2026-10-22", "position_id": "p1",
                      "lots": [{"shares": 100, "opened_at": "2026-10-22"}]}],
        "risk_rules": [],
    }
    row = pf._apply_holding(data, "600000", 50, 12.0, "2026-10-23")
    assert row["position_id"] == "p1"
    assert row["shares"] == 150
    assert row["cost"] == 10.6667
    assert len(row["lots"]) == 2


def test_apply_close_rejects_t1_lot_and_preserves_identity() -> None:
    data = {
        "holdings": [{"code": "600000", "shares": 100, "cost": 10.0,
                      "opened_at": "2026-10-23", "position_id": "p1",
                      "lots": [{"shares": 100, "opened_at": "2026-10-23"}]}],
        "risk_rules": [], "closed": [],
    }
    try:
        pf._apply_close(data, "600000", "2026-10-23", 9.0, 10, 10.0,
                        "sale-1", require_holding=True, expected_position_id="p1")
    except ValueError as exc:
        assert "T+1" in str(exc)
    else:
        raise AssertionError("same-day lot must remain unavailable")


def test_apply_close_consumes_fifo_and_uses_stored_name_without_network() -> None:
    data = {
        "holdings": [{"code": "600000", "name": "测试股份", "shares": 150,
                      "cost": 10.6667, "opened_at": "2026-10-22", "position_id": "p1",
                      "lots": [{"shares": 100, "opened_at": "2026-10-21"},
                               {"shares": 50, "opened_at": "2026-10-23"}]}],
        "risk_rules": [{"symbol": "600000", "position_id": "p1"}], "closed": [],
    }
    sale = pf._apply_close(data, "600000", "2026-10-23", 11, 25, 0, "sale-1",
                           require_holding=True, expected_position_id="p1")
    assert sale["name"] == "测试股份"
    assert sale["position_id"] == "p1"
    assert sale["shares"] == 25
    assert data["holdings"][0]["lots"] == [
        {"shares": 75, "opened_at": "2026-10-21"},
        {"shares": 50, "opened_at": "2026-10-23"},
    ]


def test_remove_closed_hides_visible_row_and_keeps_receipt(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(pf, "today_shanghai", lambda: date(2026, 10, 23))
    monkeypatch.setattr(pf.astock, "tencent_quote", lambda codes: {})
    pf._save({"holdings": [], "closed": [{"code": "600000", "operation_id": "sale-1", "pnl": 1}]})
    pf.remove_closed(0)
    raw = pf._load()
    assert raw["closed"][0]["history_hidden"] is True
    assert pf.get_portfolio()["closed"] == []
