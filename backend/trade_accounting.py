"""Pure accounting for explicitly linked actual sell/buy receipts.

No holding cost, quoted price or fee estimate can stand in for a receipt. Cash
from unpaired sales is shown separately and is never counted as a T profit.
Decimal arithmetic is retained until the JSON-number boundary; display rounding
belongs to the caller, not to each partial fee allocation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any


@dataclass(frozen=True)
class _Receipt:
    receipt_id: str
    position_id: str
    tenant: str
    code: str
    direction: str
    date: date
    price: Decimal
    shares: Decimal
    actual_fees: Decimal | None


def _text(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"成交/关联的 {key} 必须是非空标识")
    return value


def _number(value: Any, label: str, *, allow_zero: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{label}必须是有限数字")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label}必须是有限数字") from exc
    if not number.is_finite() or number < 0 or (not allow_zero and number == 0):
        raise ValueError(f"{label}必须是有限{'非负数' if allow_zero else '正数'}")
    return number


def _receipt(row: dict[str, Any]) -> _Receipt:
    if not isinstance(row, dict):
        raise ValueError("成交凭据必须是对象")
    values = {key: _text(row, key) for key in ("receipt_id", "position_id", "tenant", "code", "direction")}
    if values["direction"] not in {"sell", "buy"}:
        raise ValueError("成交方向必须是 sell 或 buy")
    date_value = _text(row, "date")
    try:
        traded_at = date.fromisoformat(date_value)
    except ValueError as exc:
        raise ValueError("成交日期必须是有效 YYYY-MM-DD") from exc
    if traded_at.isoformat() != date_value:
        raise ValueError("成交日期必须是 YYYY-MM-DD")
    stamp = _text(row, "recorded_at")
    try:
        recorded_at = datetime.fromisoformat(stamp)
    except ValueError as exc:
        raise ValueError("回录时点必须是带时区的 ISO 8601") from exc
    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
        raise ValueError("回录时点必须带时区")
    if "actual_fees" not in row:
        raise ValueError("实际费用未知时必须明确记为 null")
    fees = row["actual_fees"]
    return _Receipt(
        **values, date=traded_at,
        price=_number(row.get("price"), "成交价"),
        shares=_number(row.get("shares"), "成交股数"),
        actual_fees=None if fees is None else _number(fees, "实际费用", allow_zero=True),
    )


def _json_number(value: Decimal) -> float:
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("成交核算结果超出可表示范围") from exc
    if not math.isfinite(number):
        raise ValueError("成交核算结果超出可表示范围")
    return number


def summarize_trades(receipts: list[dict], links: list[dict]) -> dict:
    """Validate all active allocations and summarize only proven round-trip cash.

    Allocations can span several receipts and links. Their cumulative use may
    never exceed either receipt's shares. Date equality needs explicit human
    confirmation of sell-before-buy order; distinct position identities need a
    separate explicit confirmation. Missing actual fees keep net cash unknown.
    The caller supplies the same-tenant/code view appropriate for its UI.
    """
    if not isinstance(receipts, list) or not isinstance(links, list):
        raise ValueError("成交凭据与关联必须是列表")
    parsed: dict[str, _Receipt] = {}
    for row in receipts:
        receipt = _receipt(row)
        if receipt.receipt_id in parsed:
            raise ValueError("成交凭据编号重复")
        parsed[receipt.receipt_id] = receipt

    # 50 significant decimal digits retain sub-cent proportional fee fractions
    # across normal broker receipts. No fee or PnL rounding occurs per allocation.
    with localcontext() as context:
        context.prec = 50
        used = {key: Decimal(0) for key in parsed}
        seen_links: set[str] = set()
        allocated: list[dict[str, Any]] = []
        gross_total, fees_total, matched_total = Decimal(0), Decimal(0), Decimal(0)
        fees_known = True
        for link in links:
            if not isinstance(link, dict):
                raise ValueError("成交关联必须是对象")
            link_id = _text(link, "link_id")
            if link_id in seen_links:
                raise ValueError("成交关联编号重复")
            seen_links.add(link_id)
            allocations = link.get("allocations")
            if not isinstance(allocations, list):
                raise ValueError("关联分配必须是列表")
            for allocation in allocations:
                if not isinstance(allocation, dict):
                    raise ValueError("关联分配必须是对象")
                sell_id = _text(allocation, "sell_receipt_id")
                buy_id = _text(allocation, "buy_receipt_id")
                if sell_id not in parsed or buy_id not in parsed:
                    raise ValueError("关联引用的成交凭据不存在")
                sell, buy = parsed[sell_id], parsed[buy_id]
                if sell.direction != "sell" or buy.direction != "buy":
                    raise ValueError("关联必须明确卖出凭据和买回凭据")
                if sell.tenant != buy.tenant or sell.code != buy.code:
                    raise ValueError("只能关联同租户、同代码的成交")
                if buy.date < sell.date:
                    raise ValueError("买回日期不能早于卖出日期")
                if buy.date == sell.date and allocation.get("confirm_same_day_order") is not True:
                    raise ValueError("同日成交须明确确认先卖后买的顺序")
                if sell.position_id != buy.position_id and allocation.get("confirm_cross_position") is not True:
                    raise ValueError("跨仓位身份的成交须明确确认清仓后重开关联")
                shares = _number(allocation.get("shares"), "关联股数")
                if used[sell_id] + shares > sell.shares or used[buy_id] + shares > buy.shares:
                    raise ValueError("累计关联股数超过成交凭据股数")
                used[sell_id] += shares
                used[buy_id] += shares
                gross = (sell.price - buy.price) * shares
                fees = (
                    sell.actual_fees * shares / sell.shares + buy.actual_fees * shares / buy.shares
                    if sell.actual_fees is not None and buy.actual_fees is not None else None
                )
                gross_total += gross
                matched_total += shares
                if fees is None:
                    fees_known = False
                else:
                    fees_total += fees
                allocated.append({
                    "link_id": link_id, "sell_receipt_id": sell_id,
                    "buy_receipt_id": buy_id, "shares": _json_number(shares),
                    "gross_difference": _json_number(gross),
                    "allocated_fees": _json_number(fees) if fees is not None else None,
                    "net_difference": _json_number(gross - fees) if fees is not None else None,
                })
        sells = [row for row in parsed.values() if row.direction == "sell"]
        sold_total = sum((row.shares for row in sells), Decimal(0))
        unmatched_proceeds = sum((row.price * (row.shares - used[row.receipt_id]) for row in sells), Decimal(0))
        known_net = bool(allocated) and fees_known
        return {
            "sold_shares": _json_number(sold_total),
            "bought_back_shares": _json_number(matched_total),
            "unmatched_sell_shares": _json_number(sold_total - matched_total),
            "gross_difference": _json_number(gross_total),
            "allocated_fees": _json_number(fees_total) if known_net else None,
            "net_difference": _json_number(gross_total - fees_total) if known_net else None,
            "unmatched_sell_proceeds": _json_number(unmatched_proceeds),
            "allocations": allocated,
        }
