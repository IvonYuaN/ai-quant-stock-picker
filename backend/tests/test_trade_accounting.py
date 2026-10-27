"""Synthetic actual-receipt accounting: allocation, identity and missing fees."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from trade_accounting import summarize_trades  # noqa: E402


def receipt(receipt_id: str, direction: str, *, price: float = 10, shares: float = 300,
            fees: float | None = 3, date: str = "2026-10-26", position: str = "p1",
            tenant: str = "t1", code: str = "600000") -> dict:
    return {"receipt_id": receipt_id, "position_id": position, "tenant": tenant,
            "code": code, "direction": direction, "date": date, "price": price,
            "shares": shares, "actual_fees": fees, "recorded_at": "2026-10-27T10:00:00+08:00"}


def allocation(sell: str, buy: str, shares: float = 100, **flags: bool) -> dict:
    return {"sell_receipt_id": sell, "buy_receipt_id": buy, "shares": shares, **flags}


def link(*allocations: dict, link_id: str = "l1") -> dict:
    return {"link_id": link_id, "allocations": list(allocations)}


def pair(*, sell_fees: float | None = 3, buy_fees: float | None = 3) -> list[dict]:
    return [receipt("s1", "sell", price=11, fees=sell_fees),
            receipt("b1", "buy", price=10, fees=buy_fees, date="2026-10-27")]


def test_trade_accounting_summary_when_no_receipts() -> None:
    result = summarize_trades([], [])
    assert result == {"sold_shares": 0, "bought_back_shares": 0,
                      "unmatched_sell_shares": 0, "gross_difference": 0,
                      "allocated_fees": None, "net_difference": None,
                      "unmatched_sell_proceeds": 0, "allocations": []}


def test_trade_accounting_no_profit_when_sales_are_unmatched() -> None:
    result = summarize_trades(pair(), [])
    assert result["sold_shares"] == 300
    assert result["bought_back_shares"] == 0
    assert result["unmatched_sell_shares"] == 300
    assert result["unmatched_sell_proceeds"] == 3300
    assert result["net_difference"] is None


def test_trade_accounting_partial_fee_when_some_shares_are_matched() -> None:
    result = summarize_trades(pair(), [link(allocation("s1", "b1"))])
    assert result["bought_back_shares"] == 100
    assert result["unmatched_sell_shares"] == 200
    assert result["gross_difference"] == 100
    assert result["allocated_fees"] == 2
    assert result["net_difference"] == 98
    assert result["unmatched_sell_proceeds"] == 2200
    assert result["allocations"][0]["net_difference"] == 98


def test_trade_accounting_split_fees_when_multiple_sales_and_buys() -> None:
    rows = [receipt("s1", "sell", price=12, shares=300, fees=3),
            receipt("s2", "sell", price=11, shares=100, fees=2),
            receipt("b1", "buy", price=10, shares=200, fees=4, date="2026-10-27"),
            receipt("b2", "buy", price=9, shares=200, fees=2, date="2026-10-27")]
    links = [link(allocation("s1", "b1", 200)),
             link(allocation("s1", "b2", 100), allocation("s2", "b2", 100), link_id="l2")]
    result = summarize_trades(rows, links)
    assert result["sold_shares"] == result["bought_back_shares"] == 400
    assert result["unmatched_sell_shares"] == result["unmatched_sell_proceeds"] == 0
    assert result["gross_difference"] == 900
    assert result["allocated_fees"] == 11
    assert result["net_difference"] == 889


@pytest.mark.parametrize("sell_fees,buy_fees", [(None, 3), (3, None), (None, None)])
def test_trade_accounting_net_unknown_when_any_actual_fee_is_unknown(sell_fees: float | None, buy_fees: float | None) -> None:
    result = summarize_trades(pair(sell_fees=sell_fees, buy_fees=buy_fees), [link(allocation("s1", "b1"))])
    assert result["gross_difference"] == 100
    assert result["net_difference"] is None
    assert result["allocated_fees"] is None
    assert result["allocations"][0]["net_difference"] is None


def test_trade_accounting_zero_fees_when_explicitly_confirmed() -> None:
    result = summarize_trades(pair(sell_fees=0, buy_fees=0), [link(allocation("s1", "b1"))])
    assert result["allocated_fees"] == 0
    assert result["net_difference"] == 100


def test_trade_accounting_net_negative_when_buy_cost_exceeds_sell_proceeds() -> None:
    rows = pair()
    rows[1]["price"] = 12
    result = summarize_trades(rows, [link(allocation("s1", "b1"))])
    assert result["gross_difference"] == -100
    assert result["net_difference"] == -102


def test_trade_accounting_proportional_fee_when_fractional_allocations_accumulate() -> None:
    rows = [receipt("s1", "sell", shares=3, price=10.01, fees=1),
            receipt("b1", "buy", shares=3, price=10, fees=0, date="2026-10-27")]
    links = [link(allocation("s1", "b1", 1), link_id=f"l{i}") for i in range(3)]
    result = summarize_trades(rows, links)
    assert result["gross_difference"] == 0.03
    assert result["allocated_fees"] == 1
    assert result["net_difference"] == -0.97


@pytest.mark.parametrize("changes", [{"date": "2026-10-26"}, {"position_id": "p2"}])
def test_trade_accounting_rejects_when_required_confirmation_missing(changes: dict) -> None:
    rows = pair()
    rows[1].update(changes)
    with pytest.raises(ValueError, match="须明确确认"):
        summarize_trades(rows, [link(allocation("s1", "b1"))])


def test_trade_accounting_same_day_cross_position_when_both_confirmations_present() -> None:
    rows = pair()
    rows[1].update(date="2026-10-26", position_id="p2")
    result = summarize_trades(rows, [link(allocation("s1", "b1", confirm_same_day_order=True, confirm_cross_position=True))])
    assert result["net_difference"] == 98


@pytest.mark.parametrize("flag", [1, "true", None])
def test_trade_accounting_rejects_when_order_confirmation_is_not_true_bool(flag: object) -> None:
    rows = pair()
    rows[1]["date"] = "2026-10-26"
    with pytest.raises(ValueError, match="同日"):
        summarize_trades(rows, [link(allocation("s1", "b1", confirm_same_day_order=flag))])


@pytest.mark.parametrize("changes", [{"tenant": "other"}, {"code": "000001"}, {"date": "2026-10-25"}, {"direction": "sell"}])
def test_trade_accounting_rejects_when_identity_order_or_direction_is_wrong(changes: dict) -> None:
    rows = pair()
    rows[1].update(changes)
    with pytest.raises(ValueError):
        summarize_trades(rows, [link(allocation("s1", "b1"))])


@pytest.mark.parametrize("overused", ["sell", "buy"])
def test_trade_accounting_rejects_when_receipt_reused_across_links(overused: str) -> None:
    rows = pair()
    rows.append(receipt("extra", "buy" if overused == "sell" else "sell", date="2026-10-27" if overused == "sell" else "2026-10-26"))
    first = allocation("s1", "b1", 250)
    second = allocation("s1", "extra", 100) if overused == "sell" else allocation("extra", "b1", 100)
    with pytest.raises(ValueError, match="累计关联股数超过"):
        summarize_trades(rows, [link(first), link(second, link_id="l2")])


@pytest.mark.parametrize("duplicate", ["receipt", "link"])
def test_trade_accounting_rejects_when_identifiers_duplicate(duplicate: str) -> None:
    rows, links = pair(), [link(allocation("s1", "b1"))]
    if duplicate == "receipt":
        rows.append(deepcopy(rows[0]))
    else:
        links.append(deepcopy(links[0]))
    with pytest.raises(ValueError, match="编号重复"):
        summarize_trades(rows, links)


@pytest.mark.parametrize("key,value", [("price", 0), ("price", -1), ("price", float("nan")),
    ("shares", 0), ("shares", float("inf")), ("shares", True), ("actual_fees", -1),
    ("actual_fees", float("nan")), ("actual_fees", True), ("position_id", None),
    ("tenant", ""), ("date", "2026-02-30"), ("date", "20261026"),
    ("recorded_at", "2026-10-27T10:00:00"), ("recorded_at", "invalid")])
def test_trade_accounting_rejects_when_receipt_is_invalid(key: str, value: object) -> None:
    rows = pair()
    rows[0][key] = value
    with pytest.raises(ValueError):
        summarize_trades(rows, [])


def test_trade_accounting_rejects_when_fee_field_is_absent() -> None:
    rows = pair()
    del rows[0]["actual_fees"]
    with pytest.raises(ValueError, match="null"):
        summarize_trades(rows, [])


@pytest.mark.parametrize("shares", [0, -1, float("nan"), float("inf"), True, "100"])
def test_trade_accounting_rejects_when_allocation_quantity_invalid(shares: object) -> None:
    with pytest.raises(ValueError):
        summarize_trades(pair(), [link(allocation("s1", "b1", shares))])


def test_trade_accounting_rejects_when_receipt_is_unknown() -> None:
    with pytest.raises(ValueError, match="不存在"):
        summarize_trades(pair(), [link(allocation("missing", "b1"))])


def test_trade_accounting_unchanged_when_inputs_are_validated() -> None:
    rows, links = pair(), [link(allocation("s1", "b1"))]
    before = deepcopy((rows, links))
    summarize_trades(rows, links)
    assert (rows, links) == before


def test_trade_accounting_ignores_deleted_allocation_when_link_is_empty() -> None:
    result = summarize_trades(pair(), [link()])
    assert result["bought_back_shares"] == 0
    assert result["net_difference"] is None


def test_trade_accounting_rejects_when_computation_overflows_json_number() -> None:
    rows = pair()
    rows[0].update(price=1e308, shares=1e308)
    with pytest.raises(ValueError, match="超出可表示"):
        summarize_trades(rows, [])
