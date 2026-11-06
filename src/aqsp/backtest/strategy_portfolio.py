"""Account-level daily portfolio replay for a frozen custom strategy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from aqsp.backtest.custom_strategy import (
    StrategyReplayPolicy,
    _PIT_SCALE_FIELDS,
    _evaluate_by_signal_date,
    _limit_pct,
    _normalize_frame,
)
from aqsp.core.errors import DataError
from aqsp.execution.cost import TradingCostCalculator
from aqsp.indicators import enrich_indicators
from aqsp.research.strategy_expression import StrategyExpression, StrategyScoreExpression


@dataclass(frozen=True)
class PortfolioReplayPosition:
    symbol: str
    quantity: int
    average_price: float
    mark_price: float
    mark_date: str
    stale_sessions: int
    market_value: float


@dataclass(frozen=True)
class PortfolioReplayTrade:
    symbol: str
    entry_signal_date: str
    entry_date: str
    entry_price: float
    entry_quantity: int
    exit_signal_date: str | None
    exit_date: str | None
    exit_price: float | None
    exit_quantity: int
    status: str
    exit_reason: str | None
    return_pct: float | None
    pnl_amount: float | None
    fees_paid: float
    slippage_paid: float
    blocker: str | None = None
    entry_score: float | None = None


@dataclass(frozen=True)
class PortfolioReplayResult:
    strategy_id: str
    strategy_revision: int
    strategy_hash: str
    symbols: tuple[str, ...]
    signal_start: str
    signal_end: str
    outcome_as_of: str
    source: str
    price_mode: str
    fill_rule: str
    position_sizing: str
    score_expression: str | None
    initial_capital: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float | None
    max_positions: int
    max_exposure_pct: float
    max_single_position_pct: float
    closed_trades: int
    wins: int
    losses: int
    win_rate: float | None
    not_executable: int
    pending_entries: int
    open_positions: tuple[PortfolioReplayPosition, ...]
    blocked_symbols: tuple[dict[str, str], ...]
    equity_curve: tuple[dict[str, float | str | int], ...]
    trades: tuple[PortfolioReplayTrade, ...]


@dataclass
class _Position:
    symbol: str
    entry_signal_date: str
    entry_date: str
    entry_price: float
    entry_quantity: int
    quantity: int
    average_price: float
    entry_fees: float
    entry_slippage: float
    entry_index: int
    entry_signal_index: int
    entry_expression_score: float
    last_mark: float
    last_mark_index: int
    pending_exit: dict[str, Any] | None = None


def replay_strategy_portfolio(
    *,
    strategy: Mapping[str, Any],
    entry_expression: StrategyExpression,
    exit_expression: StrategyExpression | None,
    score_expression: StrategyScoreExpression | None = None,
    frames: Mapping[str, pd.DataFrame],
    pit_factors: Mapping[str, Mapping[date, Any]],
    symbols: list[str],
    signal_start: date,
    signal_end: date,
    outcome_as_of: date,
    names: Mapping[str, str],
    policy: StrategyReplayPolicy,
    market_sessions: list[date],
    initial_capital: float,
    max_positions: int,
    max_exposure_pct: float,
    max_single_position_pct: float,
    min_cash_reserve_pct: float,
    position_sizing: str = "equal",
    check_cancel: Callable[[], None] | None = None,
) -> PortfolioReplayResult:
    """Replay multiple strategy signals through one cash and exposure account."""
    if initial_capital <= 0 or max_positions < 1:
        raise ValueError("initial_capital and max_positions must be positive")
    if not 0 < max_exposure_pct <= 1 or not 0 < max_single_position_pct <= 1:
        raise ValueError("exposure limits must be in (0, 1]")
    if not 0 <= min_cash_reserve_pct < 1:
        raise ValueError("min_cash_reserve_pct must be in [0, 1)")
    if position_sizing not in {"equal", "score_weight"}:
        raise ValueError("position_sizing must be equal or score_weight")
    if position_sizing == "score_weight" and score_expression is None:
        raise ValueError("score_weight requires a score expression")
    if signal_end < signal_start or outcome_as_of < signal_end:
        raise ValueError("outcome_as_of must be on or after the signal window")

    all_sessions = list(dict.fromkeys(day for day in market_sessions if day <= outcome_as_of))
    if not all_sessions or all_sessions != sorted(all_sessions):
        raise ValueError("market_sessions must be ordered through outcome_as_of")
    if any(day not in all_sessions for frame in frames.values() for day in _frame_days(frame)):
        raise DataError("daily bars do not align with market sessions")
    sessions = [day for day in all_sessions if signal_start <= day <= outcome_as_of]
    if not sessions or sessions[0] != signal_start:
        raise DataError("signal_start must be a market session in the replay data")
    session_index = {day: index for index, day in enumerate(sessions)}
    normalized: dict[str, pd.DataFrame] = {}
    bars: dict[str, dict[date, dict[str, Any]]] = {}
    entries: dict[str, dict[date, bool]] = {}
    exits: dict[str, dict[date, bool]] = {}
    blocked: list[dict[str, str]] = []

    for symbol in symbols:
        if check_cancel:
            check_cancel()
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
        factors = pit_factors.get(symbol, {})
        if entry_expression.requires_pit_price_basis or (
            exit_expression and exit_expression.requires_pit_price_basis
        ):
            if any(day not in factors for day in days):
                blocked.append({"symbol": symbol, "reason": "missing_pit_factor"})
                continue
        entry_values, entry_missing = _evaluate_by_signal_date(
            ordered, days, factors, entry_expression, signal_start, signal_end, check_cancel
        )
        if entry_missing:
            blocked.append({
                "symbol": symbol,
                "reason": f"missing_entry_feature:{min(entry_missing).isoformat()}",
            })
            continue
        exit_values: dict[date, bool] = {}
        if exit_expression:
            exit_values, exit_missing = _evaluate_by_signal_date(
                ordered, days, factors, exit_expression, signal_start, outcome_as_of, check_cancel
            )
            if exit_missing:
                blocked.append({
                    "symbol": symbol,
                    "reason": f"missing_exit_feature:{min(exit_missing).isoformat()}",
                })
                continue
        normalized[symbol] = ordered
        symbol_bars: dict[date, dict[str, Any]] = {}
        previous_close: float | None = None
        limit_pct = _limit_pct(symbol, names.get(symbol, symbol), policy)
        for row in ordered.to_dict(orient="records"):
            current_day = date.fromisoformat(row["date"])
            if previous_close is not None:
                if not _positive_finite(row.get("limit_up")):
                    row["limit_up"] = round(previous_close * (1 + limit_pct) + 1e-8, 2)
                if not _positive_finite(row.get("limit_down")):
                    row["limit_down"] = round(previous_close * (1 - limit_pct) + 1e-8, 2)
            symbol_bars[current_day] = row
            if _positive_finite(row.get("close")):
                previous_close = float(row["close"])
        bars[symbol] = symbol_bars
        entries[symbol] = entry_values
        exits[symbol] = exit_values

    active_symbols = sorted(normalized)
    if not active_symbols:
        return _empty_result(
            strategy, symbols, signal_start, signal_end, outcome_as_of, policy,
            initial_capital, max_positions, max_exposure_pct, max_single_position_pct,
            blocked, position_sizing,
            score_expression.compiled.expression if score_expression else None,
        )

    score_values = _score_values(
        frames=normalized,
        pit_factors=pit_factors,
        symbols=active_symbols,
        expression=score_expression,
        signal_start=signal_start,
        signal_end=signal_end,
        check_cancel=check_cancel,
    )

    cash = float(initial_capital)
    pending_entries: list[dict[str, str]] = []
    scheduled_entries: dict[date, list[tuple[date, str, float | None]]] = {}
    for symbol in active_symbols:
        for signal_day, triggered in entries[symbol].items():
            if not triggered:
                continue
            next_index = session_index[signal_day] + 1
            if next_index >= len(sessions):
                pending_entries.append({"symbol": symbol, "signal_date": signal_day.isoformat(), "reason": "no_next_session"})
            else:
                scheduled_entries.setdefault(sessions[next_index], []).append((
                    signal_day,
                    symbol,
                    score_values.get((signal_day, symbol)) if score_expression else None,
                ))
    positions: dict[str, _Position] = {}
    trades: list[PortfolioReplayTrade] = []
    curve: list[dict[str, float | str | int]] = []
    peak = initial_capital
    slippage = policy.slippage
    min_commission = TradingCostCalculator.MIN_COMMISSION
    buy_fee_rate = policy.commission_rate

    for day_index, day in enumerate(sessions):
        if check_cancel:
            check_cancel()
        day_text = day.isoformat()
        sold_today: set[str] = set()

        # Exits scheduled from an earlier signal are attempted before new entries.
        for symbol in sorted(list(positions)):
            position = positions[symbol]
            bar = bars.get(symbol, {}).get(day)
            if bar is None:
                continue
            pending = position.pending_exit
            stop_fill = _stop_fill(bar, position.entry_price, policy.stop_loss_pct)
            reason: dict[str, Any] | None = None
            raw_price: float | None = None
            if day_index > position.entry_index and stop_fill is not None:
                reason = {"reason": "stop_loss", "signal_date": day, "deferred": False}
                raw_price = stop_fill
            elif pending is not None:
                if pending["scheduled_index"] is None or day_index >= pending["scheduled_index"]:
                    reason = pending
                    raw_price = float(bar["open"])
            if reason is None:
                continue
            executable, blocker = _can_portfolio_fill(
                bar, symbol, names.get(symbol, symbol), policy, side="sell"
            )
            if raw_price is not None and reason["reason"] == "stop_loss":
                lower = _limit_price_for_bar(bar, symbol, names.get(symbol, symbol), policy, "sell")
                if raw_price < lower - 1e-8:
                    executable, blocker = False, "stop_beyond_limit_down"
            if not executable:
                position.pending_exit = {**reason, "blocker": blocker, "deferred": True}
                continue
            price = float(raw_price if raw_price is not None else bar["open"]) * (1 - slippage)
            fees = max(price * position.quantity * buy_fee_rate, min_commission)
            fees += price * position.quantity * policy.stamp_tax_rate
            proceeds = price * position.quantity - fees
            cash += proceeds
            pnl = proceeds - position.entry_price * position.entry_quantity - position.entry_fees
            gross_cost = position.entry_price * position.entry_quantity + position.entry_fees
            trades.append(PortfolioReplayTrade(
                symbol=symbol,
                entry_signal_date=position.entry_signal_date,
                entry_date=position.entry_date,
                entry_price=round(position.entry_price, 4),
                entry_quantity=position.entry_quantity,
                exit_signal_date=reason["signal_date"].isoformat(),
                exit_date=day_text,
                exit_price=round(price, 4),
                exit_quantity=position.quantity,
                status="closed",
                exit_reason=str(reason["reason"]),
                return_pct=round(pnl / gross_cost * 100, 6) if gross_cost else None,
                pnl_amount=round(pnl, 2),
                fees_paid=round(position.entry_fees + fees, 2),
                slippage_paid=round(position.entry_quantity * position.entry_price * slippage + position.quantity * float(raw_price or bar["open"]) * slippage, 2),
                blocker=reason.get("blocker"),
                entry_score=position.entry_expression_score if score_expression else None,
            ))
            sold_today.add(symbol)
            del positions[symbol]

        daily_candidates = sorted(
            scheduled_entries.get(day, []),
            key=lambda item: (
                (-(item[2] if item[2] is not None else float("-inf")), item[1])
                if score_expression else (0.0, item[1])
            ),
        )
        executable_candidates: list[tuple[date, str, float | None, Mapping[str, Any]]] = []
        for signal_day, symbol, score in daily_candidates:
            if check_cancel:
                check_cancel()
            if score_expression and score is None:
                trades.append(_pending_entry_trade(symbol, signal_day, day, "missing_score"))
                continue
            if symbol in sold_today:
                trades.append(_pending_entry_trade(symbol, signal_day, day, "buy_same_day_reentry", score))
                continue
            if symbol in positions:
                continue
            bar = bars.get(symbol, {}).get(day)
            if bar is None:
                trades.append(_pending_entry_trade(symbol, signal_day, day, "no_bar_on_next_market_session", score))
                continue
            executable, blocker = _can_portfolio_fill(
                bar, symbol, names.get(symbol, symbol), policy, side="buy"
            )
            if not executable:
                trades.append(_pending_entry_trade(symbol, signal_day, day, blocker or "not_executable", score))
                continue
            executable_candidates.append((signal_day, symbol, score, bar))

        available_slots = max(max_positions - len(positions), 0)
        selected_candidates = executable_candidates[:available_slots]
        for signal_day, symbol, _score, _bar in executable_candidates[available_slots:]:
            trades.append(_pending_entry_trade(symbol, signal_day, day, "max_positions", _score))
        if selected_candidates:
            mark_values = {
                held_symbol: _mark_at_day(held_symbol, held, bars, day, day_index)
                for held_symbol, held in positions.items()
            }
            equity_before = cash + sum(mark_values.values())
            exposure_capacity = max(equity_before * max_exposure_pct - sum(mark_values.values()), 0.0)
            target_value = equity_before * max_single_position_pct
            total_budget = min(
                cash,
                exposure_capacity,
                max(cash - equity_before * min_cash_reserve_pct, 0.0),
                target_value * len(selected_candidates),
            )
            weights = _position_weights(selected_candidates, position_sizing)
            for (signal_day, symbol, score, bar), weight in zip(selected_candidates, weights):
                mark_values = {
                    held_symbol: _mark_at_day(held_symbol, held, bars, day, day_index)
                    for held_symbol, held in positions.items()
                }
                equity = cash + sum(mark_values.values())
                capacity = max(equity * max_exposure_pct - sum(mark_values.values()), 0.0)
                allocation = min(total_budget * float(weight), target_value, cash, capacity)
                raw_price = float(bar["open"])
                fill_price = raw_price * (1 + slippage)
                quantity = int(allocation / fill_price / 100) * 100
                fees = max(quantity * fill_price * buy_fee_rate, min_commission)
                while quantity and quantity * fill_price + fees > cash:
                    quantity -= 100
                    fees = max(quantity * fill_price * buy_fee_rate, min_commission)
                if quantity <= 0:
                    trades.append(_pending_entry_trade(symbol, signal_day, day, "insufficient_cash_or_lot", score))
                    continue
                gross = quantity * fill_price
                cash -= gross + fees
                positions[symbol] = _Position(
                    symbol=symbol,
                    entry_signal_date=signal_day.isoformat(),
                    entry_date=day_text,
                    entry_price=fill_price,
                    entry_quantity=quantity,
                    quantity=quantity,
                    average_price=fill_price,
                    entry_fees=fees,
                    entry_slippage=abs(fill_price - raw_price),
                    entry_index=day_index,
                    entry_signal_index=session_index[signal_day],
                    entry_expression_score=float(score or 0.0),
                    last_mark=float(bar["close"]),
                    last_mark_index=day_index,
                )

        # Existing positions may hit their stop during this session. Newly
        # opened positions remain protected by A-share T+1.
        for symbol in sorted(list(positions)):
            position = positions[symbol]
            bar = bars.get(symbol, {}).get(day)
            if bar is None or day_index <= position.entry_index or position.pending_exit is not None:
                continue
            stop_fill = _stop_fill(bar, position.entry_price, policy.stop_loss_pct)
            if stop_fill is None:
                continue
            lower = _limit_price_for_bar(bar, symbol, names.get(symbol, symbol), policy, "sell")
            if stop_fill < lower - 1e-8:
                position.pending_exit = {
                    "reason": "stop_loss", "signal_date": day, "scheduled_index": day_index,
                    "blocker": "stop_beyond_limit_down", "deferred": True,
                }
                continue
            raw_price = stop_fill
            price = raw_price * (1 - slippage)
            fees = max(price * position.quantity * buy_fee_rate, min_commission)
            fees += price * position.quantity * policy.stamp_tax_rate
            proceeds = price * position.quantity - fees
            cash += proceeds
            pnl = proceeds - position.entry_price * position.entry_quantity - position.entry_fees
            gross_cost = position.entry_price * position.entry_quantity + position.entry_fees
            trades.append(PortfolioReplayTrade(
                symbol=symbol, entry_signal_date=position.entry_signal_date,
                entry_date=position.entry_date, entry_price=round(position.entry_price, 4),
                entry_quantity=position.entry_quantity, exit_signal_date=day_text,
                exit_date=day_text, exit_price=round(price, 4), exit_quantity=position.quantity,
                status="closed", exit_reason="stop_loss",
                return_pct=round(pnl / gross_cost * 100, 6) if gross_cost else None,
                pnl_amount=round(pnl, 2), fees_paid=round(position.entry_fees + fees, 2),
                slippage_paid=round(position.entry_slippage + position.quantity * raw_price * slippage, 2),
                entry_score=position.entry_expression_score if score_expression else None,
            ))
            sold_today.add(symbol)
            del positions[symbol]

        for symbol, position in positions.items():
            bar = bars.get(symbol, {}).get(day)
            if bar is not None and _positive_finite(bar.get("close")):
                position.last_mark = float(bar["close"])
                position.last_mark_index = day_index
            if symbol in exits and exits[symbol].get(day, False) and position.pending_exit is None:
                next_index = day_index + 1
                position.pending_exit = {
                    "reason": "exit_signal", "signal_date": day,
                    "scheduled_index": next_index if next_index < len(sessions) else None,
                    "blocker": None, "deferred": False,
                }
            elif policy.max_hold_days is not None and day_index - position.entry_index >= policy.max_hold_days and position.pending_exit is None:
                next_index = day_index + 1
                position.pending_exit = {
                    "reason": "max_hold", "signal_date": day,
                    "scheduled_index": next_index if next_index < len(sessions) else None,
                    "blocker": None, "deferred": False,
                }

        market_value = sum(position.quantity * position.last_mark for position in positions.values())
        equity = cash + market_value
        peak = max(peak, equity)
        drawdown = (equity - peak) / peak if peak > 0 else 0.0
        curve.append({
            "date": day_text,
            "equity": round(equity, 2),
            "cash": round(cash, 2),
            "market_value": round(market_value, 2),
            "exposure_pct": round(market_value / equity, 6) if equity > 0 else 0.0,
            "drawdown_pct": round(drawdown * 100, 6),
            "positions": len(positions),
        })

    for position in positions.values():
        pending = position.pending_exit
        trades.append(PortfolioReplayTrade(
            symbol=position.symbol,
            entry_signal_date=position.entry_signal_date,
            entry_date=position.entry_date,
            entry_price=round(position.entry_price, 4),
            entry_quantity=position.entry_quantity,
            exit_signal_date=pending["signal_date"].isoformat() if pending else None,
            exit_date=None,
            exit_price=None,
            exit_quantity=0,
            status="exit_pending" if pending else "open",
            exit_reason=str(pending["reason"]) if pending else None,
            return_pct=None,
            pnl_amount=None,
            fees_paid=round(position.entry_fees, 2),
            slippage_paid=round(position.entry_slippage, 2),
            blocker=(pending.get("blocker") if pending else None) or ("no_exit_fill_before_outcome_as_of" if pending else "open_at_outcome_as_of"),
            entry_score=position.entry_expression_score if score_expression else None,
        ))

    closed = [trade for trade in trades if trade.status == "closed" and trade.return_pct is not None]
    wins = sum(float(trade.return_pct) > 0 for trade in closed)
    losses = sum(float(trade.return_pct) < 0 for trade in closed)
    equity_values = np.asarray([float(item["equity"]) for item in curve], dtype=float)
    daily_returns = np.diff(equity_values) / equity_values[:-1] if len(equity_values) > 1 else np.asarray([])
    volatility = float(np.std(daily_returns, ddof=1)) if len(daily_returns) > 1 else 0.0
    sharpe = float(np.mean(daily_returns) / volatility * math.sqrt(252)) if volatility > 0 else None
    total_return = (equity_values[-1] / initial_capital - 1) * 100 if len(equity_values) else 0.0
    max_drawdown = min((float(item["drawdown_pct"]) for item in curve), default=0.0)
    open_positions = tuple(
        PortfolioReplayPosition(
            symbol=symbol,
            quantity=position.quantity,
            average_price=round(position.average_price, 4),
            mark_price=round(position.last_mark, 4),
            mark_date=sessions[position.last_mark_index].isoformat(),
            stale_sessions=len(sessions) - 1 - position.last_mark_index,
            market_value=round(position.quantity * position.last_mark, 2),
        )
        for symbol, position in sorted(positions.items())
    )
    return PortfolioReplayResult(
        strategy_id=str(strategy["id"]),
        strategy_revision=int(strategy["revision"]),
        strategy_hash=str(strategy["expression_sha256"]),
        symbols=tuple(symbols),
        signal_start=signal_start.isoformat(),
        signal_end=signal_end.isoformat(),
        outcome_as_of=outcome_as_of.isoformat(),
        source="sqlite_db",
        price_mode="raw",
        fill_rule="signal_close_next_open",
        position_sizing=position_sizing,
        score_expression=score_expression.compiled.expression if score_expression else None,
        initial_capital=initial_capital,
        final_equity=round(float(equity_values[-1]), 2) if len(equity_values) else initial_capital,
        total_return_pct=round(total_return, 6),
        max_drawdown_pct=round(max_drawdown, 6),
        sharpe_ratio=round(sharpe, 6) if sharpe is not None else None,
        max_positions=max_positions,
        max_exposure_pct=max_exposure_pct,
        max_single_position_pct=max_single_position_pct,
        closed_trades=len(closed),
        wins=wins,
        losses=losses,
        win_rate=round(wins / len(closed), 6) if closed else None,
        not_executable=sum(trade.status == "not_executable" for trade in trades),
        pending_entries=len(pending_entries) + sum(trade.status == "entry_pending" for trade in trades),
        open_positions=open_positions,
        blocked_symbols=tuple(blocked),
        equity_curve=tuple(curve),
        trades=tuple(trades),
    )


def _score_values(
    *,
    frames: Mapping[str, pd.DataFrame],
    pit_factors: Mapping[str, Mapping[date, Any]],
    symbols: list[str],
    expression: StrategyScoreExpression | None,
    signal_start: date,
    signal_end: date,
    check_cancel: Callable[[], None] | None,
) -> dict[tuple[date, str], float]:
    if expression is None:
        return {}
    records: list[dict[str, Any]] = []
    for symbol in symbols:
        if check_cancel:
            check_cancel()
        ordered = _normalize_frame(frames[symbol])
        days = [date.fromisoformat(value) for value in ordered["date"]]
        factors = pit_factors.get(symbol, {})
        factor_values: list[float] = []
        for day in days:
            record = factors.get(day)
            try:
                value = float(record.value) if record is not None else float("nan")
            except (AttributeError, TypeError, ValueError):
                value = float("nan")
            factor_values.append(value if math.isfinite(value) and value > 0 else float("nan"))
        featured_input = ordered.copy()
        if expression.requires_pit_price_basis:
            for column in ("open", "high", "low", "close"):
                featured_input[column] = featured_input[column] * factor_values
        featured = enrich_indicators(featured_input)
        featured_by_day = {
            date.fromisoformat(str(row["date"])[:10]): row
            for row in featured.to_dict(orient="records")
        }
        for index, day in enumerate(days):
            if not signal_start <= day <= signal_end:
                continue
            if check_cancel:
                check_cancel()
            if expression.requires_pit_price_basis:
                prefix_factors = factor_values[: index + 1]
                if any(not math.isfinite(value) for value in prefix_factors):
                    continue
                target_factor = factor_values[index]
                if target_factor <= 0:
                    continue
            row = featured_by_day.get(day)
            if row is None:
                continue
            values = {field: row.get(field, float("nan")) for field in expression.fields}
            if expression.requires_pit_price_basis:
                for field in _PIT_SCALE_FIELDS.intersection(values):
                    values[field] = float(values[field]) / target_factor
            if any(
                not _positive_finite_or_finite(value)
                for value in values.values()
            ):
                continue
            records.append({"date": day, "symbol": symbol, **values})
    if not records:
        return {}
    panel = pd.DataFrame.from_records(records).set_index(["date", "symbol"]).sort_index()
    scores = expression.evaluate(panel)
    result: dict[tuple[date, str], float] = {}
    for (day, symbol), raw_score in scores.items():
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            continue
        if math.isfinite(score):
            result[(day, str(symbol))] = score
    return result


def _position_weights(
    selected: list[tuple[date, str, float | None, Mapping[str, Any]]],
    position_sizing: str,
) -> np.ndarray:
    if position_sizing != "score_weight":
        return np.full(len(selected), 1.0 / len(selected), dtype=float)
    raw = np.asarray([max(float(item[2] or 0.0), 0.0) for item in selected], dtype=float)
    total = float(raw.sum())
    if total <= 0:
        return np.full(len(selected), 1.0 / len(selected), dtype=float)
    return raw / total


def _positive_finite_or_finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _stop_fill(bar: Mapping[str, Any], entry_price: float, stop_pct: float | None) -> float | None:
    if stop_pct is None:
        return None
    stop = entry_price * (1 - stop_pct)
    opening = float(bar.get("open", float("nan")))
    low = float(bar.get("low", float("nan")))
    if not math.isfinite(opening) or not math.isfinite(low):
        return None
    if opening <= stop:
        return opening
    if low <= stop:
        return stop
    return None


def _positive_finite(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def _frame_days(frame: pd.DataFrame) -> set[date]:
    parsed = pd.to_datetime(frame.get("date"), errors="coerce").dropna()
    return {item.date() for item in parsed}


def _mark_at_day(
    symbol: str, position: _Position, bars: Mapping[str, Mapping[date, Mapping[str, Any]]],
    day: date, day_index: int,
) -> float:
    bar = bars.get(symbol, {}).get(day)
    if bar and _positive_finite(bar.get("open")):
        return float(bar["open"])
    return position.last_mark


def _limit_price_for_bar(
    bar: Mapping[str, Any], symbol: str, name: str,
    policy: StrategyReplayPolicy, side: str,
) -> float:
    column = "limit_up" if side == "buy" else "limit_down"
    value = bar.get(column)
    if _positive_finite(value):
        return float(value)
    previous = float(bar.get("prev_close", float("nan")))
    if not math.isfinite(previous) or previous <= 0:
        return float("nan")
    limit = _limit_pct(symbol, name, policy)
    return round(previous * (1 + limit if side == "buy" else 1 - limit) + 1e-8, 2)


def _can_portfolio_fill(
    bar: Mapping[str, Any], symbol: str, name: str,
    policy: StrategyReplayPolicy, *, side: str,
) -> tuple[bool, str | None]:
    opening = float(bar.get("open", float("nan")))
    high = float(bar.get("high", float("nan")))
    low = float(bar.get("low", float("nan")))
    volume = float(bar.get("volume", 0.0))
    if not all(math.isfinite(value) for value in (opening, high, low, volume)) or opening <= 0:
        return False, "invalid_or_missing_open"
    if volume <= 0 or bool(bar.get("suspended", False)):
        return False, "suspended_or_zero_volume"
    limit = _limit_price_for_bar(bar, symbol, name, policy, side)
    if not math.isfinite(limit):
        return False, "missing_limit_reference"
    if side == "buy" and opening >= limit * 0.999 and high <= opening * 1.0001:
        return False, "limit_up_at_open"
    if side == "sell" and opening <= limit * 1.001 and low >= opening * 0.9999:
        return False, "limit_down_at_open"
    return True, None


def _pending_entry_trade(
    symbol: str,
    signal_day: date,
    fill_day: date,
    blocker: str,
    score: float | None = None,
) -> PortfolioReplayTrade:
    return PortfolioReplayTrade(
        symbol=symbol,
        entry_signal_date=signal_day.isoformat(),
        entry_date=fill_day.isoformat(),
        entry_price=0.0,
        entry_quantity=0,
        exit_signal_date=None,
        exit_date=None,
        exit_price=None,
        exit_quantity=0,
        status="not_executable" if blocker != "no_next_session" else "entry_pending",
        exit_reason=None,
        return_pct=None,
        pnl_amount=None,
        fees_paid=0.0,
        slippage_paid=0.0,
        blocker=blocker,
        entry_score=score,
    )


def _empty_result(
    strategy: Mapping[str, Any], symbols: list[str], start: date, end: date, as_of: date,
    policy: StrategyReplayPolicy, initial_capital: float, max_positions: int,
    max_exposure: float, max_single: float, blocked: list[dict[str, str]],
    position_sizing: str, score_expression: str | None,
) -> PortfolioReplayResult:
    return PortfolioReplayResult(
        strategy_id=str(strategy["id"]), strategy_revision=int(strategy["revision"]),
        strategy_hash=str(strategy["expression_sha256"]), symbols=tuple(symbols),
        signal_start=start.isoformat(), signal_end=end.isoformat(), outcome_as_of=as_of.isoformat(),
        source="sqlite_db", price_mode="raw", fill_rule="signal_close_next_open",
        position_sizing=position_sizing,
        score_expression=score_expression,
        initial_capital=initial_capital, final_equity=initial_capital, total_return_pct=0.0,
        max_drawdown_pct=0.0, sharpe_ratio=None, max_positions=max_positions,
        max_exposure_pct=max_exposure, max_single_position_pct=max_single,
        closed_trades=0, wins=0, losses=0, win_rate=None, not_executable=0,
        pending_entries=0, open_positions=(), blocked_symbols=tuple(blocked),
        equity_curve=(), trades=(),
    )
