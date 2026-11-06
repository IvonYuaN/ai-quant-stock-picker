"""Deterministic raw-price replay for one user-defined daily strategy snapshot."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Any, Callable, Mapping

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.indicators import enrich_indicators
from aqsp.research.strategy_expression import StrategyExpression


_PIT_SCALE_FIELDS = frozenset({
    "open", "high", "low", "close", "prev_close",
    "ma5", "ma10", "ma20", "ma60", "ema12", "ema26",
    "macd_dif", "macd_dea", "macd_hist", "atr14", "high_20", "low_20",
    "bb_middle20", "bb_upper20", "bb_lower20",
})


@dataclass(frozen=True)
class StrategyReplayPolicy:
    commission_rate: float
    stamp_tax_rate: float
    slippage: float
    fallback_limit_main_pct: float
    fallback_limit_growth_pct: float
    fallback_limit_bse_pct: float
    fallback_limit_st_pct: float
    stop_loss_pct: float | None = None
    max_hold_days: int | None = None
    fill_rule: str = "signal_close_next_open"
    allocation_rule: str = "100_percent_per_sequential_trade"


@dataclass(frozen=True)
class StrategyReplayTrade:
    symbol: str
    entry_signal_date: str
    entry_date: str | None
    entry_price: float | None
    exit_signal_date: str | None
    exit_date: str | None
    exit_price: float | None
    status: str
    exit_reason: str | None
    return_pct: float | None
    commission_paid: float
    stamp_tax_paid: float
    slippage_paid: float
    blocker: str | None = None


@dataclass(frozen=True)
class StrategyReplayResult:
    strategy_id: str
    strategy_revision: int
    strategy_hash: str
    symbol_count: int
    signal_start: str
    signal_end: str
    outcome_as_of: str
    source: str
    price_mode: str
    fill_rule: str
    policy: StrategyReplayPolicy
    closed_trades: int
    wins: int
    losses: int
    win_rate: float | None
    total_return_pct: float
    average_return_pct: float | None
    not_executable: int
    pending_entries: int
    open_positions: int
    blocked_symbols: tuple[dict[str, str], ...]
    trades: tuple[StrategyReplayTrade, ...]


class StrategyReplayCancelled(RuntimeError):
    """Raised when a user cancels a replay between bounded calculation steps."""


class StrategyReplayTimedOut(TimeoutError):
    """Raised when a replay exceeds its configured worker deadline."""


def replay_strategy(
    *,
    strategy: Mapping[str, Any],
    entry_expression: StrategyExpression,
    exit_expression: StrategyExpression | None,
    frames: Mapping[str, pd.DataFrame],
    pit_factors: Mapping[str, Mapping[date, Any]],
    symbols: list[str],
    signal_start: date,
    signal_end: date,
    outcome_as_of: date,
    names: Mapping[str, str],
    policy: StrategyReplayPolicy,
    market_sessions: list[date],
    check_cancel: Callable[[], None] | None = None,
) -> StrategyReplayResult:
    """Replay signal-close decisions at subsequent raw-bar opens with A-share T+1."""
    _validate_policy(policy)
    if signal_end < signal_start or outcome_as_of < signal_end:
        raise ValueError("outcome_as_of must be on or after signal_end")
    trades: list[StrategyReplayTrade] = []
    blocked: list[dict[str, str]] = []
    for symbol in symbols:
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            blocked.append({"symbol": symbol, "reason": "missing_daily_data"})
            continue
        ordered = _normalize_frame(frame)
        parsed = pd.to_datetime(ordered["date"], errors="coerce").dt.date
        ordered = ordered.loc[parsed <= outcome_as_of].reset_index(drop=True)
        if ordered.empty:
            blocked.append({"symbol": symbol, "reason": "no_data_before_outcome_as_of"})
            continue
        days = [date.fromisoformat(value) for value in ordered["date"]]
        if days[-1] < outcome_as_of:
            blocked.append({"symbol": symbol, "reason": f"stale_data:{days[-1].isoformat()}"})
            continue
        sessions = list(dict.fromkeys(day for day in market_sessions if day <= outcome_as_of))
        if not sessions or sessions != sorted(sessions) or any(day not in sessions for day in days):
            raise ValueError("market_sessions must be ordered and include every replay bar")
        factors = pit_factors.get(symbol, {})
        needs_pit = entry_expression.requires_pit_price_basis or bool(
            exit_expression and exit_expression.requires_pit_price_basis
        )
        if needs_pit and any(day not in factors for day in days):
            blocked.append({"symbol": symbol, "reason": "missing_pit_factor"})
            continue
        entry_values, entry_missing = _evaluate_by_signal_date(
            ordered, days, factors, entry_expression, signal_start, signal_end, check_cancel
        )
        exit_values: dict[date, bool] = {}
        exit_missing: set[date] = set()
        if exit_expression:
            exit_values, exit_missing = _evaluate_by_signal_date(
                ordered, days, factors, exit_expression, signal_start, outcome_as_of, check_cancel
            )
        _replay_symbol(
            symbol=symbol,
            name=names.get(symbol, symbol),
            frame=ordered,
            days=days,
            market_sessions=sessions,
            entry_values=entry_values,
            entry_missing=entry_missing,
            exit_values=exit_values,
            exit_missing=exit_missing,
            signal_start=signal_start,
            signal_end=signal_end,
            policy=policy,
            trades=trades,
            blocked=blocked,
            check_cancel=check_cancel,
        )

    closed = [trade for trade in trades if trade.status == "closed" and trade.return_pct is not None]
    returns = [float(trade.return_pct) for trade in closed]
    compound_growth = 1.0
    for value in returns:
        compound_growth *= 1.0 + value / 100.0
    wins = sum(value > 0 for value in returns)
    losses = sum(value < 0 for value in returns)
    not_executable = sum(trade.status == "not_executable" for trade in trades)
    pending_entries = sum(trade.status == "entry_pending" for trade in trades)
    open_positions = sum(trade.status in {"open", "exit_pending"} for trade in trades)
    return StrategyReplayResult(
        strategy_id=str(strategy["id"]),
        strategy_revision=int(strategy["revision"]),
        strategy_hash=str(strategy["expression_sha256"]),
        symbol_count=len(symbols),
        signal_start=signal_start.isoformat(),
        signal_end=signal_end.isoformat(),
        outcome_as_of=outcome_as_of.isoformat(),
        source="sqlite_db",
        price_mode="raw",
        fill_rule=policy.fill_rule,
        policy=policy,
        closed_trades=len(closed),
        wins=wins,
        losses=losses,
        win_rate=round(wins / len(closed), 6) if closed else None,
        total_return_pct=round((compound_growth - 1.0) * 100.0, 6),
        average_return_pct=round(sum(returns) / len(closed), 6) if closed else None,
        not_executable=not_executable,
        pending_entries=pending_entries,
        open_positions=open_positions,
        blocked_symbols=tuple(blocked),
        trades=tuple(trades),
    )


def _validate_policy(policy: StrategyReplayPolicy) -> None:
    for name in (
        "commission_rate", "stamp_tax_rate", "slippage", "fallback_limit_main_pct",
        "fallback_limit_growth_pct", "fallback_limit_bse_pct", "fallback_limit_st_pct",
    ):
        value = float(getattr(policy, name))
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and non-negative")
    if policy.stop_loss_pct is not None and not 0 < policy.stop_loss_pct < 1:
        raise ValueError("stop_loss_pct must be between 0 and 1")
    if policy.max_hold_days is not None and policy.max_hold_days < 1:
        raise ValueError("max_hold_days must be positive")


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    ordered = frame.copy()
    parsed = pd.to_datetime(ordered.get("date"), errors="coerce")
    if parsed.isna().any():
        raise DataError("strategy replay contains invalid daily dates")
    ordered["date"] = parsed.dt.strftime("%Y-%m-%d")
    ordered = ordered.sort_values("date", kind="stable").reset_index(drop=True)
    if ordered["date"].duplicated().any():
        raise DataError("strategy replay contains duplicate daily dates")
    for column in ("open", "high", "low", "close", "volume"):
        if column not in ordered:
            raise DataError(f"strategy replay missing raw daily column {column}")
        ordered[column] = pd.to_numeric(ordered[column], errors="coerce")
    if "amount" not in ordered:
        ordered["amount"] = ordered["close"] * ordered["volume"]
    if "raw_close" not in ordered:
        ordered["raw_close"] = ordered["close"]
    return ordered


def _evaluate_by_signal_date(
    frame: pd.DataFrame,
    days: list[date],
    factors: Mapping[date, Any],
    expression: StrategyExpression,
    start: date,
    end: date,
    check_cancel: Callable[[], None] | None = None,
) -> tuple[dict[date, bool], set[date]]:
    values: dict[date, bool] = {}
    missing: set[date] = set()
    selected_indexes = [index for index, day in enumerate(days) if start <= day <= end]
    if check_cancel:
        check_cancel()
    featured_input = frame.copy().reset_index(drop=True)
    factor_values: list[float] = []
    if expression.requires_pit_price_basis:
        for day in days:
            record = factors.get(day)
            try:
                factor = float(record.value) if record is not None else float("nan")
            except (AttributeError, TypeError, ValueError):
                factor = float("nan")
            factor_values.append(
                factor if math.isfinite(factor) and factor > 0 else float("nan")
            )
        for column in ("open", "high", "low", "close"):
            featured_input[column] = featured_input[column] * factor_values
    featured = enrich_indicators(featured_input)
    featured_by_day = {
        date.fromisoformat(str(row["date"])[:10]): row
        for row in featured.to_dict(orient="records")
    }

    for index in selected_indexes:
        if check_cancel:
            check_cancel()
        target_day = days[index]
        target_factor = 1.0
        if expression.requires_pit_price_basis:
            if any(not math.isfinite(value) for value in factor_values[: index + 1]):
                missing.add(target_day)
                continue
            target_factor = factor_values[index]
        last = featured_by_day.get(target_day)
        if last is None:
            missing.add(target_day)
            continue
        row_values = {
            field: last[field]
            for field in expression.fields
            if field in last
        }
        if target_factor != 1.0:
            for field in _PIT_SCALE_FIELDS.intersection(row_values):
                row_values[field] = float(row_values[field]) / target_factor
        if any(
            field not in row_values
            or pd.isna(row_values[field])
            or not math.isfinite(float(row_values[field]))
            for field in expression.fields
        ):
            missing.add(target_day)
            continue
        row = pd.DataFrame([row_values])
        values[target_day] = bool(expression.evaluate(row).iloc[0])
    return values, missing


def _replay_symbol(
    *, symbol: str, name: str, frame: pd.DataFrame, days: list[date],
    market_sessions: list[date],
    entry_values: Mapping[date, bool], entry_missing: set[date],
    exit_values: Mapping[date, bool], exit_missing: set[date],
    signal_start: date, signal_end: date, policy: StrategyReplayPolicy,
    trades: list[StrategyReplayTrade], blocked: list[dict[str, str]],
    check_cancel: Callable[[], None] | None = None,
) -> None:
    open_trade: dict[str, Any] | None = None
    session_indexes = {day: index for index, day in enumerate(market_sessions)}
    bar_indexes = {day: index for index, day in enumerate(days)}
    replay_indexes = range(len(days))
    for index in replay_indexes:
        if check_cancel:
            check_cancel()
        day = days[index]
        in_entry_window = signal_start <= day <= signal_end
        if in_entry_window and day in entry_missing and open_trade is None:
            blocked.append({"symbol": symbol, "reason": f"missing_entry_feature:{day.isoformat()}"})
            continue
        if open_trade is None:
            if not in_entry_window or not entry_values.get(day, False):
                continue
            session_index = session_indexes.get(day)
            next_session_index = session_index + 1 if session_index is not None else len(market_sessions)
            if next_session_index >= len(market_sessions):
                trades.append(_pending_entry(symbol, day, "no_next_market_session_before_outcome_as_of"))
                continue
            fill_day = market_sessions[next_session_index]
            fill_index = bar_indexes.get(fill_day)
            if fill_index is None:
                trades.append(_blocked_entry(symbol, day, fill_day, "no_bar_on_next_market_session"))
                continue
            executable, reason = _can_fill(
                frame, fill_index, symbol, name, side="buy", policy=policy
            )
            if not executable:
                trades.append(_blocked_entry(symbol, day, days[fill_index], reason))
                continue
            bar = frame.iloc[fill_index]
            raw_price = float(bar["open"])
            fill_price = raw_price * (1 + policy.slippage)
            open_trade = {
                "symbol": symbol,
                "entry_signal_date": day.isoformat(),
                "entry_date": days[fill_index].isoformat(),
                "entry_price": fill_price,
                "entry_commission": fill_price * policy.commission_rate,
                "entry_slippage": abs(fill_price - raw_price),
                "entry_index": fill_index,
                "entry_session_index": next_session_index,
                "entry_signal_index": index,
                "pending_exit": None,
            }
            continue

        if index < open_trade["entry_index"]:
            continue
        if index == open_trade["entry_index"]:
            if day in exit_missing:
                blocked.append({"symbol": symbol, "reason": f"missing_exit_feature:{day.isoformat()}"})
            elif exit_values.get(day, False):
                open_trade["pending_exit"] = _exit_request(
                    "exit_signal", day, market_sessions, session_indexes
                )
            continue
        signal_reason = None
        pending_exit = open_trade["pending_exit"] is not None
        if pending_exit:
            pending = open_trade["pending_exit"]
            if pending["scheduled_fill_day"] is None or day >= pending["scheduled_fill_day"]:
                signal_reason = {
                    **pending,
                    "deferred": pending.get("deferred")
                    or (pending["scheduled_fill_day"] is not None and day > pending["scheduled_fill_day"]),
                }
        elif policy.stop_loss_pct is not None:
            entry_price = float(open_trade["entry_price"])
            stop_price = entry_price * (1 - policy.stop_loss_pct)
            if float(frame.iloc[index]["low"]) <= stop_price:
                signal_reason = {
                    "reason": "stop_loss",
                    "signal_date": day,
                    "stop_price": stop_price,
                    "scheduled_fill_day": day,
                }
        if signal_reason is None and day in exit_missing:
            blocked.append({"symbol": symbol, "reason": f"missing_exit_feature:{day.isoformat()}"})
            continue
        if signal_reason is None and exit_values.get(day, False):
            signal_reason = _exit_request("exit_signal", day, market_sessions, session_indexes)
        held_sessions = session_indexes.get(day, index) - open_trade["entry_session_index"]
        if signal_reason is None and policy.max_hold_days is not None and held_sessions >= policy.max_hold_days:
            signal_reason = _exit_request("max_hold", day, market_sessions, session_indexes)
        if signal_reason is None:
            continue
        if signal_reason.get("reason") == "stop_loss":
            exit_index = index
            raw_stop = min(float(frame.iloc[index]["open"]), float(signal_reason["stop_price"]))
        elif pending_exit:
            exit_index = index
            raw_stop = None
        else:
            target_day = signal_reason["scheduled_fill_day"]
            if target_day is None:
                open_trade["pending_exit"] = signal_reason
                continue
            exit_index = bar_indexes.get(target_day)
            if exit_index is None:
                signal_reason["deferred"] = True
                open_trade["pending_exit"] = signal_reason
                continue
            raw_stop = None
        executable, reason = _can_fill(frame, exit_index, symbol, name, side="sell", policy=policy)
        if not executable:
            open_trade["pending_exit"] = {**signal_reason, "deferred": True}
            continue
        raw_price = raw_stop if raw_stop is not None else float(frame.iloc[exit_index]["open"])
        previous_close = float(frame.iloc[exit_index - 1]["close"]) if exit_index > 0 else float("nan")
        if raw_stop is not None and raw_stop < _limit_price(
            frame.iloc[exit_index], previous_close, symbol, name, policy, "sell"
        ) - 1e-8:
            open_trade["pending_exit"] = {**signal_reason, "deferred": True}
            continue
        fill_price = raw_price * (1 - policy.slippage)
        gross_entry = float(open_trade["entry_price"])
        exit_commission = fill_price * policy.commission_rate
        stamp_tax = fill_price * policy.stamp_tax_rate
        total_costs = float(open_trade["entry_commission"]) + exit_commission + stamp_tax
        net_return = (
            (fill_price - gross_entry) - total_costs
        ) / gross_entry * 100
        trades.append(
            StrategyReplayTrade(
                symbol=symbol,
                entry_signal_date=open_trade["entry_signal_date"],
                entry_date=open_trade["entry_date"],
                entry_price=round(gross_entry, 4),
                exit_signal_date=signal_reason["signal_date"].isoformat(),
                exit_date=days[exit_index].isoformat(),
                exit_price=round(fill_price, 4),
                status="closed",
                exit_reason=str(signal_reason["reason"]),
                return_pct=round(net_return, 6),
                commission_paid=round(float(open_trade["entry_commission"]) + exit_commission, 4),
                stamp_tax_paid=round(stamp_tax, 4),
                slippage_paid=round(float(open_trade["entry_slippage"]) + abs(raw_price - fill_price), 4),
                blocker="exit_deferred_until_next_available_bar" if signal_reason.get("deferred") else None,
            )
        )
        open_trade = None
    if open_trade is not None:
        pending = open_trade["pending_exit"]
        trades.append(
            StrategyReplayTrade(
                symbol=symbol,
                entry_signal_date=open_trade["entry_signal_date"],
                entry_date=open_trade["entry_date"],
                entry_price=round(float(open_trade["entry_price"]), 4),
                exit_signal_date=pending["signal_date"].isoformat() if pending else None,
                exit_date=None,
                exit_price=None,
                status="exit_pending" if pending else "open",
                exit_reason=str(pending["reason"]) if pending else None,
                return_pct=None,
                commission_paid=round(float(open_trade["entry_commission"]), 4),
                stamp_tax_paid=0.0,
                slippage_paid=round(float(open_trade["entry_slippage"]), 4),
                blocker="no_exit_fill_before_outcome_as_of" if pending else "open_at_outcome_as_of",
            )
        )


def _exit_request(
    reason: str,
    signal_day: date,
    market_sessions: list[date],
    session_indexes: Mapping[date, int],
) -> dict[str, Any]:
    session_index = session_indexes.get(signal_day)
    next_index = session_index + 1 if session_index is not None else len(market_sessions)
    return {
        "reason": reason,
        "signal_date": signal_day,
        "scheduled_fill_day": market_sessions[next_index] if next_index < len(market_sessions) else None,
    }


def _can_fill(
    frame: pd.DataFrame, index: int, symbol: str, name: str, *, side: str,
    policy: StrategyReplayPolicy,
) -> tuple[bool, str | None]:
    bar = frame.iloc[index]
    open_price = float(bar["open"])
    high = float(bar["high"])
    low = float(bar["low"])
    volume = float(bar["volume"])
    if not all(math.isfinite(value) for value in (open_price, high, low, volume)) or open_price <= 0:
        return False, "invalid_or_missing_open"
    if volume <= 0 or bool(bar.get("suspended", False)):
        return False, "suspended_or_zero_volume"
    previous_close = float(frame.iloc[index - 1]["close"]) if index > 0 else float("nan")
    if not math.isfinite(previous_close) or previous_close <= 0:
        return False, "missing_previous_close"
    upper = _limit_price(bar, previous_close, symbol, name, policy, "buy")
    lower = _limit_price(bar, previous_close, symbol, name, policy, "sell")
    if side == "buy" and open_price >= upper * 0.999 and high <= open_price * 1.0001:
        return False, "limit_up_at_open"
    if side == "buy" and open_price > upper + 0.011:
        return False, "open_above_limit_up"
    if side == "sell" and open_price <= lower * 1.001 and low >= open_price * 0.9999:
        return False, "limit_down_at_open"
    if side == "sell" and open_price < lower - 0.011:
        return False, "open_below_limit_down"
    return True, None


def _limit_price(
    bar: pd.Series,
    previous_close: float,
    symbol: str,
    name: str,
    policy: StrategyReplayPolicy,
    side: str,
) -> float:
    column = "limit_up" if side == "buy" else "limit_down"
    value = pd.to_numeric(bar.get(column), errors="coerce")
    if pd.notna(value) and math.isfinite(float(value)) and float(value) > 0:
        return float(value)
    limit_pct = _limit_pct(symbol, name, policy)
    return round(previous_close * (1 + limit_pct if side == "buy" else 1 - limit_pct) + 1e-8, 2)


def _limit_pct(symbol: str, name: str, policy: StrategyReplayPolicy) -> float:
    if "ST" in name.upper() or "退" in name:
        return policy.fallback_limit_st_pct
    if symbol.startswith(("300", "301", "688", "689")):
        return policy.fallback_limit_growth_pct
    if symbol.startswith(("4", "8", "92")):
        return policy.fallback_limit_bse_pct
    return policy.fallback_limit_main_pct


def _blocked_entry(symbol: str, signal_day: date, fill_day: date, reason: str) -> StrategyReplayTrade:
    return StrategyReplayTrade(
        symbol, signal_day.isoformat(), fill_day.isoformat(), None, None, None, None,
        "not_executable", None, None, 0.0, 0.0, 0.0, blocker=reason,
    )


def _open_trade(symbol: str, signal_day: date, reason: str) -> StrategyReplayTrade:
    return StrategyReplayTrade(
        symbol, signal_day.isoformat(), None, None, None, None, None,
        "not_executable", None, None, 0.0, 0.0, 0.0, blocker=reason,
    )


def _pending_entry(symbol: str, signal_day: date, reason: str) -> StrategyReplayTrade:
    return StrategyReplayTrade(
        symbol, signal_day.isoformat(), None, None, None, None, None,
        "entry_pending", None, None, 0.0, 0.0, 0.0, blocker=reason,
    )

