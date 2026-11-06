from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from aqsp.backtest.custom_strategy import StrategyReplayPolicy
from aqsp.backtest.strategy_portfolio import replay_strategy_portfolio
from aqsp.research.strategy_expression import (
    compile_strategy_expression,
    compile_strategy_score_expression,
)


@pytest.fixture
def policy() -> StrategyReplayPolicy:
    return StrategyReplayPolicy(
        commission_rate=0.0003,
        stamp_tax_rate=0.001,
        slippage=0.002,
        fallback_limit_main_pct=0.1,
        fallback_limit_growth_pct=0.2,
        fallback_limit_bse_pct=0.3,
        fallback_limit_st_pct=0.05,
        stop_loss_pct=None,
        max_hold_days=None,
    )


def _days(count: int) -> list[date]:
    result: list[date] = []
    current = date(2026, 1, 5)
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def _frame(
    dates: list[date],
    closes: list[float],
    *,
    opens: list[float] | None = None,
    suspended_indexes: set[int] | None = None,
) -> pd.DataFrame:
    open_values = opens or closes
    frame = pd.DataFrame({
        "date": [day.isoformat() for day in dates],
        "open": open_values,
        "high": [max(opening, close) + 0.1 for opening, close in zip(open_values, closes, strict=True)],
        "low": [min(opening, close) - 0.1 for opening, close in zip(open_values, closes, strict=True)],
        "close": closes,
        "raw_close": closes,
        "volume": [10000.0] * len(dates),
        "amount": [100000.0] * len(dates),
        "suspended": [index in (suspended_indexes or set()) for index in range(len(dates))],
    })
    return frame


def _run(
    frames: dict[str, pd.DataFrame],
    *,
    symbols: list[str],
    dates: list[date],
    policy: StrategyReplayPolicy,
    entry: str = "close > 10",
    exit_expression: str | None = None,
    signal_start_index: int = 1,
    signal_end_index: int = 1,
    initial_capital: float = 100_000.0,
    max_positions: int = 2,
    max_exposure_pct: float = 0.9,
    max_single_position_pct: float = 0.3,
    min_cash_reserve_pct: float = 0.1,
    pit_available: bool = True,
    score_expression: str | None = None,
    position_sizing: str = "equal",
    pit_factor_values: dict[str, list[float]] | None = None,
):
    factors = {
        symbol: {
            day: SimpleNamespace(value=value)
            for day, value in zip(
                dates,
                (pit_factor_values or {}).get(symbol, [1.0] * len(dates)),
                strict=True,
            )
        }
        for symbol in symbols
    } if pit_available else {}
    return replay_strategy_portfolio(
        strategy={"id": "portfolio_test", "revision": 1, "expression_sha256": "a" * 64},
        entry_expression=compile_strategy_expression(entry),
        exit_expression=compile_strategy_expression(exit_expression) if exit_expression else None,
        score_expression=compile_strategy_score_expression(score_expression) if score_expression else None,
        frames=frames,
        pit_factors=factors,
        symbols=symbols,
        signal_start=dates[signal_start_index],
        signal_end=dates[signal_end_index],
        outcome_as_of=dates[-1],
        names={symbol: symbol for symbol in symbols},
        policy=policy,
        market_sessions=dates,
        initial_capital=initial_capital,
        max_positions=max_positions,
        max_exposure_pct=max_exposure_pct,
        max_single_position_pct=max_single_position_pct,
        min_cash_reserve_pct=min_cash_reserve_pct,
        position_sizing=position_sizing,
    )


def test_strategy_portfolio_replay_allocates_shared_cash_in_stable_symbol_order(
    policy: StrategyReplayPolicy,
):
    dates = _days(5)
    frames = {
        "600002": _frame(dates, [9, 11, 12, 13, 13]),
        "600001": _frame(dates, [9, 11, 12, 13, 13]),
    }

    result = _run(
        frames,
        symbols=["600002", "600001"],
        dates=dates,
        policy=policy,
        max_positions=1,
        max_single_position_pct=0.4,
        min_cash_reserve_pct=0,
    )

    assert len(result.open_positions) == 1
    assert result.open_positions[0].symbol == "600001"
    assert result.open_positions[0].quantity > 0
    assert result.equity_curve[1]["positions"] == 1
    assert result.equity_curve[1]["exposure_pct"] <= 0.42
    rejected = next(trade for trade in result.trades if trade.symbol == "600002")
    assert rejected.status == "not_executable"
    assert rejected.blocker == "max_positions"


def test_strategy_portfolio_replay_uses_cross_sectional_score_weights(policy: StrategyReplayPolicy):
    dates = _days(5)
    first = _frame(dates, [9, 11, 11, 11, 11])
    second = _frame(dates, [9, 11, 11, 11, 11])
    first.loc[1, "volume"] = 10_000
    second.loc[1, "volume"] = 20_000

    result = _run(
        {"600001": first, "600002": second},
        symbols=["600002", "600001"],
        dates=dates,
        policy=policy,
        max_positions=2,
        max_exposure_pct=0.9,
        max_single_position_pct=1.0,
        min_cash_reserve_pct=0,
        score_expression="rank(volume)",
        position_sizing="score_weight",
    )

    positions = {item.symbol: item for item in result.open_positions}
    assert positions["600002"].quantity == 2 * positions["600001"].quantity
    assert result.position_sizing == "score_weight"
    entry_scores = {
        trade.symbol: trade.entry_score
        for trade in result.trades
        if trade.status == "open"
    }
    assert entry_scores == {"600001": 0.5, "600002": 1.0}


def test_strategy_portfolio_score_uses_signal_day_pit_price_basis(policy: StrategyReplayPolicy):
    dates = _days(4)
    split_adjusted = _frame(dates, [100, 100, 50, 50])
    unadjusted_drop = _frame(dates, [100, 100, 95, 95])

    result = _run(
        {"600001": split_adjusted, "600002": unadjusted_drop},
        symbols=["600001", "600002"],
        dates=dates,
        policy=policy,
        entry="close > 0",
        signal_start_index=2,
        signal_end_index=2,
        max_positions=2,
        max_exposure_pct=1.0,
        max_single_position_pct=1.0,
        min_cash_reserve_pct=0,
        score_expression="rank(ret_1)",
        position_sizing="score_weight",
        pit_factor_values={"600001": [0.5, 0.5, 1.0, 1.0]},
    )

    entry_scores = {
        trade.symbol: trade.entry_score
        for trade in result.trades
        if trade.status == "open"
    }
    assert entry_scores == {"600001": 1.0, "600002": 0.5}


def test_strategy_portfolio_replay_requires_score_for_score_weight(policy: StrategyReplayPolicy):
    dates = _days(3)
    with pytest.raises(ValueError, match="requires a score expression"):
        _run(
            {"600001": _frame(dates, [9, 11, 12])},
            symbols=["600001"],
            dates=dates,
            policy=policy,
            position_sizing="score_weight",
        )


def test_strategy_portfolio_replay_retries_exit_after_missing_bar_without_future_mark(
    policy: StrategyReplayPolicy,
):
    dates = _days(6)
    stock_dates = [dates[index] for index in (0, 1, 2, 4, 5)]
    frame = _frame(stock_dates, [9, 11, 12, 13, 14])

    result = _run(
        {"600001": frame},
        symbols=["600001"],
        dates=dates,
        policy=policy,
        exit_expression="close > 10",
        signal_start_index=1,
        signal_end_index=1,
        min_cash_reserve_pct=0,
    )

    closed = [trade for trade in result.trades if trade.status == "closed"]
    assert len(closed) == 1
    assert closed[0].entry_date == dates[2].isoformat()
    assert closed[0].exit_date == dates[4].isoformat()
    assert closed[0].exit_signal_date == dates[2].isoformat()
    curve_by_date = {point["date"]: point for point in result.equity_curve}
    assert curve_by_date[dates[3].isoformat()]["positions"] == 1
    assert curve_by_date[dates[4].isoformat()]["positions"] == 0


def test_strategy_portfolio_replay_carries_only_past_mark_when_symbol_bar_is_missing(
    policy: StrategyReplayPolicy,
):
    dates = _days(5)
    stock_dates = [dates[index] for index in (0, 1, 2, 4)]
    frame = _frame(stock_dates, [9, 11, 12, 15])

    result = _run(
        {"600001": frame},
        symbols=["600001"],
        dates=dates,
        policy=policy,
        signal_start_index=1,
        signal_end_index=1,
        min_cash_reserve_pct=0,
    )

    curve_by_date = {point["date"]: point for point in result.equity_curve}
    missing_day_curve = curve_by_date[dates[3].isoformat()]
    assert missing_day_curve["equity"] < curve_by_date[dates[4].isoformat()]["equity"]
    assert result.open_positions[0].mark_date == dates[4].isoformat()
    assert result.open_positions[0].stale_sessions == 0


def test_strategy_portfolio_replay_blocks_price_signals_when_pit_is_missing(
    policy: StrategyReplayPolicy,
):
    dates = _days(4)
    result = _run(
        {"600001": _frame(dates, [9, 11, 12, 13])},
        symbols=["600001"],
        dates=dates,
        policy=policy,
        entry="close > 10",
        pit_available=False,
    )

    assert result.trades == ()
    assert result.closed_trades == 0
    assert result.blocked_symbols == ({"symbol": "600001", "reason": "missing_pit_factor"},)


def test_strategy_portfolio_replay_validates_exposure_and_capital_limits(
    policy: StrategyReplayPolicy,
):
    dates = _days(3)
    frames = {"600001": _frame(dates, [9, 11, 12])}

    with pytest.raises(ValueError, match="initial_capital"):
        _run(frames, symbols=["600001"], dates=dates, policy=policy, initial_capital=0)
    with pytest.raises(ValueError, match="exposure limits"):
        _run(frames, symbols=["600001"], dates=dates, policy=policy, max_exposure_pct=1.2)
