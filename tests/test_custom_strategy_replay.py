from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from aqsp.backtest.custom_strategy import (
    StrategyReplayCancelled,
    StrategyReplayPolicy,
    _evaluate_by_signal_date,
    replay_strategy,
)
from aqsp.research.strategy_expression import compile_strategy_expression


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
        max_hold_days=10,
    )


def _frame(closes: list[float], *, opens: list[float] | None = None) -> pd.DataFrame:
    start = date(2026, 1, 5)
    days: list[date] = []
    current = start
    while len(days) < len(closes):
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    open_values = opens or closes
    return pd.DataFrame(
        {
            "date": [day.isoformat() for day in days],
            "open": open_values,
            "high": [max(c, o) + 0.1 for c, o in zip(closes, open_values, strict=True)],
            "low": [min(c, o) - 0.1 for c, o in zip(closes, open_values, strict=True)],
            "close": closes,
            "raw_close": closes,
            "volume": [10000.0] * len(closes),
            "amount": [100000.0] * len(closes),
        }
    )


def _run(
    frame: pd.DataFrame,
    *,
    entry: str = "close > 10",
    exit_expression: str | None = None,
    signal_start_index: int = 1,
    signal_end_index: int = 1,
    pit: bool = True,
    market_sessions: list[date] | None = None,
    rules: StrategyReplayPolicy,
):
    days = [date.fromisoformat(value) for value in frame["date"]]
    factors = {
        day: SimpleNamespace(value=1.0)
        for day in days
    } if pit else {}
    return replay_strategy(
        strategy={"id": "test_strategy", "revision": 2, "expression_sha256": "a" * 64},
        entry_expression=compile_strategy_expression(entry),
        exit_expression=compile_strategy_expression(exit_expression) if exit_expression else None,
        frames={"600001": frame},
        pit_factors={"600001": factors},
        symbols=["600001"],
        signal_start=days[signal_start_index],
        signal_end=days[signal_end_index],
        outcome_as_of=days[-1],
        names={"600001": "测试股票"},
        policy=rules,
        market_sessions=market_sessions or days,
    )


def test_custom_strategy_replay_fills_entry_and_exit_next_session_open(
    policy: StrategyReplayPolicy,
):
    frame = _frame([9, 11, 12, 13, 13], opens=[9, 11, 12, 12.5, 13])

    result = _run(
        frame,
        entry="close > 10",
        exit_expression="close > 10",
        signal_start_index=1,
        signal_end_index=1,
        rules=policy,
    )

    trade = result.trades[0]
    assert trade.entry_signal_date == frame.iloc[1]["date"]
    assert trade.entry_date == frame.iloc[2]["date"]
    assert trade.exit_signal_date == frame.iloc[2]["date"]
    assert trade.exit_date == frame.iloc[3]["date"]
    assert trade.status == "closed"
    assert result.closed_trades == 1
    assert result.open_positions == 0
    assert trade.commission_paid > 0
    assert trade.stamp_tax_paid > 0
    assert trade.slippage_paid > 0


def test_custom_strategy_replay_reuses_pit_adjusted_feature_path_without_future_leak(
    policy: StrategyReplayPolicy,
):
    frame = _frame([100, 50, 52, 54])
    days = [date.fromisoformat(value) for value in frame["date"]]
    factors = {
        day: SimpleNamespace(value=value)
        for day, value in zip(days, [0.5, 1.0, 1.0, 1.0], strict=True)
    }

    values, missing = _evaluate_by_signal_date(
        frame,
        days,
        factors,
        compile_strategy_expression("ret_1 > 0.03"),
        days[2],
        days[2],
    )

    assert missing == set()
    assert values == {days[2]: True}


def test_custom_strategy_replay_aligns_features_after_invalid_raw_bar_is_dropped(
    policy: StrategyReplayPolicy,
):
    frame = _frame([9, 11, 12, 13])
    frame.loc[1, "open"] = float("nan")
    days = [date.fromisoformat(value) for value in frame["date"]]
    factors = {day: SimpleNamespace(value=1.0) for day in days}

    values, missing = _evaluate_by_signal_date(
        frame,
        days,
        factors,
        compile_strategy_expression("close > 10"),
        days[2],
        days[2],
    )

    assert missing == set()
    assert values == {days[2]: True}


def test_custom_strategy_replay_keeps_t1_and_marks_end_of_window_position_open(
    policy: StrategyReplayPolicy,
):
    frame = _frame([9, 11, 12], opens=[9, 11, 12])

    result = _run(frame, signal_start_index=1, signal_end_index=1, rules=policy)

    assert len(result.trades) == 1
    assert result.trades[0].entry_date == frame.iloc[2]["date"]
    assert result.trades[0].status == "open"
    assert result.trades[0].exit_date is None
    assert result.open_positions == 1
    assert result.closed_trades == 0
    assert result.win_rate is None


def test_custom_strategy_replay_keeps_blocked_limit_up_entry_out_of_win_rate(
    policy: StrategyReplayPolicy,
):
    frame = _frame([9, 10.5, 12], opens=[9, 10, 11.55])
    frame.loc[2, ["high", "low", "open"]] = [11.55, 11.55, 11.55]

    result = _run(frame, signal_start_index=1, signal_end_index=1, rules=policy)

    assert result.closed_trades == 0
    assert result.not_executable == 1
    assert result.win_rate is None
    assert result.trades[0].status == "not_executable"
    assert result.trades[0].blocker == "limit_up_at_open"


def test_custom_strategy_replay_exit_signal_after_signal_window_is_evaluated(
    policy: StrategyReplayPolicy,
):
    frame = _frame([9, 11, 11, 9, 9], opens=[9, 11, 11, 9, 9])

    result = _run(
        frame,
        entry="close > 10",
        exit_expression="close < 10",
        signal_start_index=1,
        signal_end_index=1,
        rules=policy,
    )

    assert result.trades[0].exit_signal_date == frame.iloc[3]["date"]
    assert result.trades[0].exit_date == frame.iloc[4]["date"]


def test_custom_strategy_replay_blocks_entry_when_pit_basis_is_missing(
    policy: StrategyReplayPolicy,
):
    frame = _frame([9, 11, 12])

    result = _run(
        frame,
        entry="close > 10",
        signal_start_index=1,
        signal_end_index=1,
        pit=False,
        rules=policy,
    )

    assert result.trades == ()
    assert result.closed_trades == 0
    assert {item["reason"] for item in result.blocked_symbols} == {"missing_pit_factor"}


def test_custom_strategy_replay_does_not_shift_entry_past_missing_market_session(
    policy: StrategyReplayPolicy,
):
    frame = _frame([11, 12, 13])
    frame = frame.drop(index=1).reset_index(drop=True)
    first, _missing, last = [date.fromisoformat(value) for value in _frame([1, 1, 1])["date"]]
    middle = first + timedelta(days=1)
    market_sessions = [first, middle, last]

    result = _run(
        frame,
        signal_start_index=0,
        signal_end_index=0,
        market_sessions=market_sessions,
        rules=policy,
    )

    assert len(result.trades) == 1
    assert result.trades[0].status == "not_executable"
    assert result.trades[0].blocker == "no_bar_on_next_market_session"
    assert result.not_executable == 1


def test_custom_strategy_replay_marks_missing_next_session_as_pending_entry(
    policy: StrategyReplayPolicy,
):
    frame = _frame([11, 12])

    result = _run(
        frame,
        signal_start_index=1,
        signal_end_index=1,
        rules=policy,
    )

    assert result.trades[0].status == "entry_pending"
    assert result.trades[0].blocker == "no_next_market_session_before_outcome_as_of"
    assert result.pending_entries == 1
    assert result.not_executable == 0


def test_custom_strategy_replay_defers_exit_when_next_session_has_no_bar(
    policy: StrategyReplayPolicy,
):
    frame = _frame([11, 12, 13])
    sessions = [date.fromisoformat(value) for value in _frame([1, 1, 1, 1])["date"]]
    frame = frame.drop(index=2).reset_index(drop=True)
    frame = pd.concat([frame, _frame([14]).assign(date=sessions[3].isoformat())], ignore_index=True)

    result = _run(
        frame,
        entry="close > 10",
        exit_expression="close > 10",
        signal_start_index=0,
        signal_end_index=0,
        market_sessions=sessions,
        rules=policy,
    )

    assert result.trades[0].status == "closed"
    assert result.trades[0].exit_date == sessions[3].isoformat()
    assert result.trades[0].blocker == "exit_deferred_until_next_available_bar"


def test_custom_strategy_replay_blocks_stop_price_below_actual_limit_down(
    policy: StrategyReplayPolicy,
):
    frame = _frame([10, 10, 7.9])
    frame.loc[2, ["open", "high", "low", "close", "limit_down"]] = [9, 9.2, 7.9, 8, 9]
    rules = replace(policy, stop_loss_pct=0.2)

    result = _run(
        frame,
        entry="close > 9",
        signal_start_index=0,
        signal_end_index=0,
        rules=rules,
    )

    assert result.trades[0].status == "exit_pending"
    assert result.trades[0].blocker == "no_exit_fill_before_outcome_as_of"
    assert result.closed_trades == 0


def test_custom_strategy_replay_checks_cancellation_during_signal_evaluation(
    policy: StrategyReplayPolicy,
):
    frame = _frame([9, 11, 12, 13])
    calls = 0

    def check_cancel() -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise StrategyReplayCancelled("cancelled")

    days = [date.fromisoformat(value) for value in frame["date"]]
    with pytest.raises(StrategyReplayCancelled):
        replay_strategy(
            strategy={"id": "test_strategy", "revision": 2, "expression_sha256": "a" * 64},
            entry_expression=compile_strategy_expression("close > 10"),
            exit_expression=compile_strategy_expression("close < 10"),
            frames={"600001": frame},
            pit_factors={"600001": {day: SimpleNamespace(value=1.0) for day in days}},
            symbols=["600001"],
            signal_start=days[1],
            signal_end=days[2],
            outcome_as_of=days[-1],
            names={"600001": "测试股票"},
            policy=policy,
            market_sessions=days,
            check_cancel=check_cancel,
        )
