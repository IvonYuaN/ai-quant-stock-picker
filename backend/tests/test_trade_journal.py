from __future__ import annotations

from datetime import date

import alert_events as ae
import portfolio as pf
import trade_journal as tj


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ae, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(pf, "today_shanghai", lambda: date(2026, 10, 27))
    monkeypatch.setattr(tj, "today_shanghai", lambda: date(2026, 10, 27))
    monkeypatch.setattr(pf.risk_rules, "ensure_rules", lambda data: None)
    pf._save({"holdings": [{"code": "600000", "name": "测试股份", "shares": 100,
                             "cost": 12.0, "opened_at": "2026-10-20", "position_id": "p1",
                             "lots": [{"shares": 100, "opened_at": "2026-10-20"}]}],
              "risk_rules": [], "closed": []})
    ae._save([])
    return tj.position_revision(pf._load(), "600000")


def test_trade_journal_records_sale_and_exact_retry_without_double_deduction(tmp_path, monkeypatch):
    revision = _setup(tmp_path, monkeypatch)
    request = tj.TradeInput("sale-1", "sell", "600000", "2026-10-27", 10.0, 40, None, "p1", revision)
    first = tj.record_trade(request)
    retry = tj.record_trade(request)
    assert first == retry
    assert first["summary"]["sold_shares"] == 40
    assert len(pf._load()["trade_receipts"]) == 1
    assert pf._load()["holdings"][0]["shares"] == 60


def test_trade_journal_links_partial_buy_and_keeps_unknown_net(tmp_path, monkeypatch):
    revision = _setup(tmp_path, monkeypatch)
    sale = tj.record_trade(tj.TradeInput("sale-1", "sell", "600000", "2026-10-27", 10.0, 40, None, "p1", revision))
    buy_rev = sale["position"]["revision"]
    bought = tj.record_trade(tj.TradeInput("buy-1", "buy", "600000", "2026-10-27", 9.0, 20, 0.0, "p1", buy_rev))
    linked = tj.save_link("t-1", [{"sell_receipt_id": "sale-1", "buy_receipt_id": "buy-1", "shares": 20,
                                   "confirm_same_day_order": True, "confirm_cross_position": False}],
                          bought["revision"], "link-1")
    assert linked["summary"]["bought_back_shares"] == 20
    assert linked["summary"]["net_difference"] is None
    assert linked["summary"]["unmatched_sell_proceeds"] == 200


def test_trade_journal_rejects_stale_revision_without_writing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    request = tj.TradeInput("sale-1", "sell", "600000", "2026-10-27", 10.0, 40, 0.0, "p1", "stale")
    try:
        tj.record_trade(request)
    except tj.TradeConflictError:
        pass
    else:
        raise AssertionError("stale trade must be rejected")
    assert "trade_receipts" not in pf._load()
