"""Conditional position worksheets; no signals, orders or frozen-threshold changes.

Reference prices are observed source levels, not optimized targets. Quantities,
prices and execution costs are user scenarios, never inferred from loss size.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class PriceReference:
    label: str
    price: float
    as_of: str
    source: str


@dataclass(frozen=True)
class ActionBasis:
    checked_at: str
    quote_as_of: str | None
    quote_source: str
    quote_issue: str
    price: float | None
    cost: float | None
    shares: int | None
    sellable_shares: int | None
    position_id: str | None
    references: tuple[PriceReference, ...]
    joint_weakness: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialize immutable source evidence for the existing alert journal."""
        return asdict(self)


def _positive(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def build_action_basis(
    holding: Mapping[str, Any], quote: Mapping[str, Any] | None,
    history: pd.DataFrame | None, *, checked_at: datetime, joint_weakness: bool,
) -> ActionBasis:
    """Collect verifiable levels from the current quote and completed raw bars."""
    from decision_context import _session, position_quote_issue

    quote = quote or {}
    issue = position_quote_issue(quote, checked_at=checked_at)
    stamp = quote.get("as_of")
    if not stamp:
        from astock import parse_gtimg_time
        stamp = parse_gtimg_time(quote.get("quote_time"))
    references: list[PriceReference] = []
    source = str(quote.get("source") or "腾讯行情")
    price = _positive(quote.get("price")) if not issue else None
    if price is None and not issue:
        issue = "缺少有效现价"
    low, high = _positive(quote.get("low")), _positive(quote.get("high"))
    if not issue and low is not None and high is not None and low <= price <= high:
        references.extend((PriceReference("本交易日已见低点", low, str(stamp), source),
                           PriceReference("本交易日已见高点", high, str(stamp), source)))
    if not issue and (close := _positive(quote.get("last_close"))) is not None:
        references.append(PriceReference("行情源前收盘价", close, str(stamp), source))
    completed = _session(checked_at, completed=True)
    if (history is not None and not history.empty and completed is not None
        and {"date", "high", "low", "close"}.issubset(history.columns)
        and history.attrs.get("price_mode", "raw") == "raw"):
        dates = pd.to_datetime(history["date"], errors="coerce")
        if dates.notna().all() and not (dates.dt.date > checked_at.date()).any():
            frame = history.assign(_day=dates.dt.date).sort_values("_day")
            frame = frame[frame["_day"] <= completed].drop_duplicates("_day")
            if not frame.empty and frame.iloc[-1]["_day"] == completed:
                row = frame.iloc[-1]
                lo, hi, cl = (_positive(row[key]) for key in ("low", "high", "close"))
                if lo is not None and hi is not None and cl is not None and lo <= cl <= hi:
                    bar_source = str(history.attrs.get("source_name") or history.attrs.get("source") or "持仓检查不复权日线")
                    references.extend((PriceReference("最近完成日低点", lo, completed.isoformat(), bar_source),
                                       PriceReference("最近完成日高点", hi, completed.isoformat(), bar_source)))
    def count(key: str) -> int | None:
        value = _positive(holding.get(key))
        return int(value) if value is not None and value.is_integer() else (0 if holding.get(key) == 0 else None)
    return ActionBasis(checked_at.isoformat(), str(stamp) if stamp else None, source, issue,
                       price, _positive(holding.get("cost")), count("shares"),
                       count("sellable_shares"), holding.get("position_id"),
                       tuple(references), joint_weakness)


@dataclass(frozen=True)
class ScenarioInput:
    quantity: int
    sell_price: float
    buy_price: float | None
    commission_rate: float
    min_commission: float
    stamp_tax_rate: float
    slippage_rate: float
    fees_confirmed: bool

    @classmethod
    def parse(cls, raw: Mapping[str, Any], *, choice: str) -> ScenarioInput:
        """Require explicit, finite user assumptions; do not infer trade size."""
        keys = set(cls.__dataclass_fields__)
        if set(raw) != keys or raw.get("fees_confirmed") is not True:
            raise ValueError("请完整填写并确认数量、价格及费用假设")
        if type(raw["quantity"]) is not int or raw["quantity"] <= 0:
            raise ValueError("测算数量必须是正整数股")
        for key in ("sell_price", "buy_price", "commission_rate", "min_commission", "stamp_tax_rate", "slippage_rate"):
            value = raw[key]
            if key == "buy_price" and value is None and choice != "t_watch":
                continue
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError(f"{key} 必须是有限数字")
            if value < 0 or (key.endswith("price") and value <= 0):
                raise ValueError("价格必须为正，费用不能为负")
            if key.endswith("rate") and value >= 1:
                raise ValueError("费用比例必须小于 1")
        return cls(**raw)


def calculate_scenario(
    choice: str, raw: Mapping[str, Any], holding: Mapping[str, Any],
) -> dict[str, Any]:
    """Recompute net cash and economic basis; never mutate broker/book costs.

    Uses Decimal for cash math. Round-trip profit is incremental to the existing
    loss; it is not evidence of recovery probability or suitable execution.
    """
    if choice not in {"reduce", "t_watch"}:
        raise ValueError("持有方案不需要成交测算")
    data = ScenarioInput.parse(raw, choice=choice)
    if choice == "reduce" and data.buy_price is not None:
        raise ValueError("减仓方案不包含回补价，请清空 buy_price")
    sellable = _positive(holding.get("sellable_shares"))
    shares = _positive(holding.get("shares"))
    cost = _positive(holding.get("cost"))
    if sellable is None or shares is None or cost is None or not shares.is_integer() or not sellable.is_integer() or data.quantity > min(sellable, shares):
        raise ValueError("数量超过当前可卖底仓或持仓成本、数量未知，请刷新持仓")
    code = str(holding.get("code") or "")
    if choice == "t_watch":
        # SSE STAR: minimum 200 with unit increments. BSE: minimum 100 with
        # unit increments. Other supported A-share boards: 100-share lots.
        minimum, unit = (200, 1) if code.startswith("688") else ((100, 1) if code.startswith(("4", "8", "92")) else (100, 100))
        if data.quantity < minimum or data.quantity % unit:
            raise ValueError(f"回补申报至少 {minimum} 股，数量递增单位 {unit} 股")
    q, total, book = (Decimal(str(value)) for value in (data.quantity, shares, cost))
    sell = Decimal(str(data.sell_price))
    buy = Decimal(str(data.buy_price or 0))
    rate, minimum_fee, tax, slip = (Decimal(str(value)) for value in (data.commission_rate, data.min_commission, data.stamp_tax_rate, data.slippage_rate))
    sell_amount, buy_amount = q * sell, q * buy
    sell_fee = max(sell_amount * rate, minimum_fee) + sell_amount * (tax + slip)
    buy_fee = max(buy_amount * rate, minimum_fee) + buy_amount * slip if choice == "t_watch" else Decimal(0)
    fee = sell_fee + buy_fee
    net = sell_amount - buy_amount - fee
    remaining = total if choice == "t_watch" else total - q
    economic = (total * book - net) / remaining if remaining > 0 else None
    def money(value: Decimal | None, precision: str = "0.01") -> float | None:
        return float(value.quantize(Decimal(precision), rounding=ROUND_HALF_UP)) if value is not None else None
    return {
        "parameters": asdict(data), "gross_spread": money(sell_amount - buy_amount),
        "estimated_fees": money(fee), "net_cash": money(net),
        "realized_pnl": money(net - q * book) if choice == "reduce" else None,
        "economic_cost": money(economic, "0.0001"), "remaining_shares": int(remaining),
        "cost_reduction_per_share": money(net / total, "0.0001") if choice == "t_watch" else None,
        "round_trip_positive": net > 0 if choice == "t_watch" else None,
        "note": "经济成本为原投入减本次净现金后除以剩余股数，仅作本次假设测算；不等于券商成本、不抵消历史亏损。实际成交与费用分别回录。",
    }
