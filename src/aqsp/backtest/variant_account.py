"""Account-level paper execution for isolated research variants.

This module is deliberately separate from the formal candidate score.  It is a
deterministic paper simulator: no broker calls, no look-ahead, and no shared
cash or positions between variants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

import pandas as pd


Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class VariantExecutionRules:
    """Hard A-share execution rules used by a research variant."""

    initial_cash: float = 100_000.0
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.001
    min_commission: float = 5.0
    slippage_bps: float = 15.0
    lot_size: int = 100


@dataclass(frozen=True)
class VariantOrder:
    date: str
    symbol: str
    side: Side
    weight: float = 1.0
    reason: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VariantFill:
    date: str
    symbol: str
    side: Side
    quantity: int
    price: float
    fees: float
    status: str
    reason: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VariantHolding:
    """Marked-to-market holding at the end of the historical run."""

    symbol: str
    quantity: int
    average_price: float
    last_price: float
    market_value: float
    unrealized_pnl: float


@dataclass
class _Position:
    quantity: int = 0
    available_quantity: int = 0
    average_price: float = 0.0


@dataclass(frozen=True)
class VariantResult:
    variant_id: str
    initial_cash: float
    final_equity: float
    cash: float
    fills: tuple[VariantFill, ...]
    positions: Mapping[str, int]
    holdings: tuple[VariantHolding, ...]
    rejected_orders: int
    snapshots: Mapping[str, tuple[VariantHolding, ...]]

    @property
    def return_pct(self) -> float:
        return (self.final_equity / self.initial_cash - 1.0) * 100.0

    @property
    def total_pnl(self) -> float:
        return self.final_equity - self.initial_cash


@dataclass(frozen=True)
class VariantPreparedData:
    frames: Mapping[str, pd.DataFrame]
    bars: Mapping[str, Mapping[str, Mapping[str, Any]]]
    dates: tuple[str, ...]
    last_close: Mapping[str, float]


VariantDataInput = Mapping[str, pd.DataFrame] | VariantPreparedData


def prepare_variant_data(data: Mapping[str, pd.DataFrame]) -> VariantPreparedData:
    frames = _normalize_frames(data)
    bars = {
        symbol: {
            str(row["date"]): row
            for row in frame.to_dict(orient="records")
            if str(row.get("date", ""))
        }
        for symbol, frame in frames.items()
    }
    return VariantPreparedData(
        frames=frames,
        bars=bars,
        dates=tuple(
            sorted({date for frame in frames.values() for date in frame["date"]})
        ),
        last_close={symbol: _last_close(frame) for symbol, frame in frames.items()},
    )


def simulate_variant(
    variant_id: str,
    data: VariantDataInput,
    orders: Sequence[VariantOrder],
    *,
    rules: VariantExecutionRules | None = None,
    snapshot_dates: Sequence[str] = (),
) -> VariantResult:
    """Execute dated orders at the next available bar open.

    Bars must be unadjusted and contain ``date/open/high/low/close``.  A buy is
    available for sale only on a later bar, which models A-share T+1.  Orders
    at a suspended, limit-up (buy), or limit-down (sell) open are rejected.
    """

    cfg = rules or VariantExecutionRules()
    if cfg.initial_cash <= 0 or cfg.lot_size <= 0:
        raise ValueError("initial_cash and lot_size must be positive")
    prepared = (
        data if isinstance(data, VariantPreparedData) else prepare_variant_data(data)
    )
    order_by_date: dict[str, list[VariantOrder]] = {}
    for order in orders:
        order_by_date.setdefault(str(order.date)[:10], []).append(order)
    cash = float(cfg.initial_cash)
    positions: dict[str, _Position] = {}
    fills: list[VariantFill] = []
    rejected = 0
    slip = cfg.slippage_bps / 10_000.0
    snapshot_set = {str(date)[:10] for date in snapshot_dates}
    snapshots: dict[str, tuple[VariantHolding, ...]] = {}

    for date in prepared.dates:
        for order in order_by_date.get(date, ()):
            symbol_bars = prepared.bars.get(order.symbol)
            if symbol_bars is None:
                fills.append(_reject(date, order, "missing_symbol"))
                rejected += 1
                continue
            bar = symbol_bars.get(date)
            if bar is None:
                fills.append(_reject(date, order, "missing_bar"))
                rejected += 1
                continue
            if _as_bool(bar.get("suspended", False)):
                fills.append(_reject(date, order, "suspended"))
                rejected += 1
                continue
            open_price = float(bar["open"])
            limit_up = _optional_float(bar.get("limit_up"))
            limit_down = _optional_float(bar.get("limit_down"))
            if order.side == "buy" and limit_up is not None and open_price >= limit_up:
                fills.append(_reject(date, order, "limit_up"))
                rejected += 1
                continue
            if (
                order.side == "sell"
                and limit_down is not None
                and open_price <= limit_down
            ):
                fills.append(_reject(date, order, "limit_down"))
                rejected += 1
                continue
            position = positions.setdefault(order.symbol, _Position())
            if order.side == "sell" and position.available_quantity <= 0:
                fills.append(_reject(date, order, "t_plus_one"))
                rejected += 1
                continue
            price = open_price * (1.0 + slip if order.side == "buy" else 1.0 - slip)
            if order.side == "buy":
                budget = max(0.0, cash * min(max(order.weight, 0.0), 1.0))
                quantity = int(budget / price / cfg.lot_size) * cfg.lot_size
                fees = _buy_fees(quantity * price, cfg)
                while quantity and quantity * price + fees > cash:
                    quantity -= cfg.lot_size
                    fees = _buy_fees(quantity * price, cfg)
                if quantity <= 0:
                    fills.append(_reject(date, order, "insufficient_cash"))
                    rejected += 1
                    continue
                cash -= quantity * price + fees
                position.average_price = (
                    position.average_price * position.quantity + quantity * price
                ) / (position.quantity + quantity)
                position.quantity += quantity
                fills.append(
                    VariantFill(
                        date,
                        order.symbol,
                        "buy",
                        quantity,
                        price,
                        fees,
                        "filled",
                        order.reason,
                        dict(order.evidence),
                    )
                )
            else:
                quantity = (
                    int(
                        position.available_quantity
                        * min(max(order.weight, 0.0), 1.0)
                        / cfg.lot_size
                    )
                    * cfg.lot_size
                )
                if quantity <= 0:
                    fills.append(_reject(date, order, "no_lot_available"))
                    rejected += 1
                    continue
                fees = _sell_fees(quantity * price, cfg)
                cash += quantity * price - fees
                position.quantity -= quantity
                position.available_quantity -= quantity
                fills.append(
                    VariantFill(
                        date,
                        order.symbol,
                        "sell",
                        quantity,
                        price,
                        fees,
                        "filled",
                        order.reason,
                        dict(order.evidence),
                    )
                )
        for position in positions.values():
            position.available_quantity = position.quantity
        if date in snapshot_set:
            snapshots[date] = _holdings_for_positions(positions, prepared, date)

    holdings = _holdings_for_positions(positions, prepared, None)
    final_equity = cash + sum(holding.market_value for holding in holdings)
    return VariantResult(
        variant_id=variant_id,
        initial_cash=cfg.initial_cash,
        final_equity=final_equity,
        cash=cash,
        fills=tuple(fills),
        positions={
            symbol: position.quantity
            for symbol, position in positions.items()
            if position.quantity
        },
        holdings=holdings,
        rejected_orders=rejected,
        snapshots=snapshots,
    )


def variant_result_to_dict(result: VariantResult) -> dict[str, Any]:
    """Serialize a result for the isolated experiment artifact."""
    return {
        "variant_id": result.variant_id,
        "initial_cash": result.initial_cash,
        "final_equity": result.final_equity,
        "cash": result.cash,
        "total_pnl": result.total_pnl,
        "return_pct": result.return_pct,
        "filled_orders": sum(fill.status == "filled" for fill in result.fills),
        "rejected_orders": result.rejected_orders,
        "positions": dict(result.positions),
        "holdings": [
            {
                "symbol": holding.symbol,
                "quantity": holding.quantity,
                "average_price": holding.average_price,
                "last_price": holding.last_price,
                "market_value": holding.market_value,
                "unrealized_pnl": holding.unrealized_pnl,
            }
            for holding in result.holdings
        ],
        "fills": [
            {
                "date": fill.date,
                "symbol": fill.symbol,
                "side": fill.side,
                "quantity": fill.quantity,
                "price": fill.price,
                "fees": fill.fees,
                "status": fill.status,
                "reason": fill.reason,
                "evidence": dict(fill.evidence),
            }
            for fill in result.fills
        ],
    }


def _normalize_frames(data: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    required = {"date", "open", "high", "low", "close"}
    result: dict[str, pd.DataFrame] = {}
    for symbol, raw in data.items():
        missing = required - set(raw.columns)
        if missing:
            raise ValueError(f"{symbol} missing columns: {sorted(missing)}")
        frame = raw.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime(
            "%Y-%m-%d"
        )
        result[str(symbol)] = (
            frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        )
    return result


def _last_close(frame: pd.DataFrame) -> float:
    return float(frame.iloc[-1]["close"])


def _holdings_for_positions(
    positions: Mapping[str, _Position],
    prepared: VariantPreparedData,
    date: str | None,
) -> tuple[VariantHolding, ...]:
    holdings: list[VariantHolding] = []
    for symbol, position in positions.items():
        if not position.quantity:
            continue
        price = prepared.last_close[symbol]
        if date is not None:
            bar = prepared.bars.get(symbol, {}).get(date)
            if bar is not None:
                price = float(bar["close"])
        holdings.append(
            VariantHolding(
                symbol=symbol,
                quantity=position.quantity,
                average_price=position.average_price,
                last_price=price,
                market_value=position.quantity * price,
                unrealized_pnl=position.quantity * (price - position.average_price),
            )
        )
    return tuple(holdings)


def _optional_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if pd.notna(result) else None


def _as_bool(value: object) -> bool:
    return bool(value) and str(value).lower() not in {"0", "false", "nan", "none"}


def _buy_fees(amount: float, cfg: VariantExecutionRules) -> float:
    return (
        amount * cfg.commission_rate
        if amount * cfg.commission_rate >= cfg.min_commission
        else cfg.min_commission
    )


def _sell_fees(amount: float, cfg: VariantExecutionRules) -> float:
    return _buy_fees(amount, cfg) + amount * cfg.stamp_tax_rate


def _reject(date: str, order: VariantOrder, reason: str) -> VariantFill:
    evidence = dict(order.evidence)
    if order.reason:
        evidence.setdefault("signal_reason", order.reason)
    evidence["blocked_reason"] = reason
    return VariantFill(
        date, order.symbol, order.side, 0, 0.0, 0.0, "rejected", reason, evidence
    )
