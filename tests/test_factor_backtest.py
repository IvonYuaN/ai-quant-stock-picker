from __future__ import annotations

import pandas as pd
import pytest

from aqsp.strategies.factor_backtest import (
    FactorBacktester,
    _align_forward_returns_to_signal_session,
)


def test_factor_backtest_rejects_ambiguous_single_index_series() -> None:
    factor = pd.Series(list(range(25)), dtype=float)
    returns = pd.Series([10.0] * 5 + [0.0] * 5 + [0.0] * 5 + [0.0] * 5 + [-10.0] * 5)

    with pytest.raises(ValueError, match="MultiIndex"):
        FactorBacktester().backtest_factor(factor, returns, quantile=5)


def test_factor_backtest_rejects_unnamed_multiindex() -> None:
    index = pd.MultiIndex.from_product(
        [pd.date_range("2026-01-01", periods=2), ["000001", "000002"]]
    )
    factor = pd.Series([1.0, 2.0, 1.0, 2.0], index=index)
    returns = pd.Series([0.0, 0.0, 1.0, -1.0], index=index)

    with pytest.raises(ValueError, match="date, symbol"):
        FactorBacktester().backtest_factor(factor, returns)


def test_factor_combination_rejects_ambiguous_single_index_series() -> None:
    factor = pd.Series(list(range(25)), dtype=float)
    returns = pd.Series([10.0] * 5 + [0.0] * 5 + [0.0] * 5 + [0.0] * 5 + [-10.0] * 5)

    with pytest.raises(ValueError, match="MultiIndex"):
        FactorBacktester().backtest_factor_combination(
            {"factor": factor},
            {"factor": 1.0},
            returns,
            quantile=5,
        )


def test_factor_backtest_uses_daily_cross_section_for_multiindex() -> None:
    index = pd.MultiIndex.from_product(
        [pd.date_range("2026-01-01", periods=20), [f"{i:06d}" for i in range(10)]],
        names=["date", "symbol"],
    )
    factor = pd.Series(
        [float(symbol) for _date, symbol in index],
        index=index,
    )
    returns = pd.Series(
        [
            1.0 if int(symbol) >= 8 else -1.0 if int(symbol) <= 1 else 0.0
            for _date, symbol in index
        ],
        index=index,
    )

    result = FactorBacktester().backtest_factor(
        factor, returns, quantile=5, holding_days=1,
    )

    assert result.total_trades == 19
    assert result.total_return > 0


def test_factor_expression_backtest_uses_symbol_history_when_multiindex() -> None:
    index = pd.MultiIndex.from_product(
        [pd.date_range("2026-01-01", periods=20), [f"{i:06d}" for i in range(10)]],
        names=["date", "symbol"],
    )
    frame = pd.DataFrame(
        {
            "close": [
                10.0 + int(symbol) + date_idx
                for date_idx, (_date, symbol) in enumerate(index)
            ],
            "volume": [1000.0 + int(symbol) * 10 for _date, symbol in index],
        },
        index=index,
    )
    returns = pd.Series(
        [
            1.0 if int(symbol) <= 1 else -1.0 if int(symbol) >= 8 else 0.0
            for _date, symbol in index
        ],
        index=index,
    )

    result = FactorBacktester().backtest_expression(
        "close / ts_mean(close, 3)",
        frame,
        returns,
        quantile=5,
    )

    assert result.factor_name == "close / ts_mean(close, 3)"
    assert result.total_trades > 0
    assert result.total_return > 0


def test_factor_expression_backtest_rejects_single_index_frame() -> None:
    frame = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    returns = pd.Series([1.0, 1.0, 1.0])

    with pytest.raises(ValueError, match="MultiIndex"):
        FactorBacktester().backtest_expression("close", frame, returns)


def test_factor_combination_uses_daily_cross_section_for_multiindex() -> None:
    index = pd.MultiIndex.from_product(
        [pd.date_range("2026-01-01", periods=20), [f"{i:06d}" for i in range(10)]],
        names=["date", "symbol"],
    )
    factor = pd.Series(
        [float(symbol) for _date, symbol in index],
        index=index,
    )
    returns = pd.Series(
        [
            1.0 if int(symbol) >= 8 else -1.0 if int(symbol) <= 1 else 0.0
            for _date, symbol in index
        ],
        index=index,
    )

    result = FactorBacktester().backtest_factor_combination(
        {"factor": factor},
        {"factor": 1.0},
        returns,
        quantile=5,
    )

    assert result.factor_name == "factor"
    assert result.total_trades == 19
    assert result.total_return > 0


def test_factor_backtest_aligns_realized_returns_to_the_prior_signal_session() -> None:
    dates = pd.date_range("2026-01-01", periods=3)
    symbols = [f"{i:06d}" for i in range(10)]
    index = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
    factor_values = []
    realized_returns = []
    for date_index, _date in enumerate(dates):
        ranks = list(range(10)) if date_index % 2 == 0 else list(reversed(range(10)))
        factor_values.extend(float(value) for value in ranks)
        if date_index == 0:
            # Same-session outcomes oppose the signal and must never be scored.
            realized_returns.extend(float(-value) for value in ranks)
        else:
            prior_ranks = list(range(10)) if (date_index - 1) % 2 == 0 else list(reversed(range(10)))
            realized_returns.extend(float(value) for value in prior_ranks)
    factor = pd.Series(factor_values, index=index)
    returns = pd.Series(realized_returns, index=index)

    result = FactorBacktester().backtest_factor(
        factor, returns, quantile=5, holding_days=1,
    )

    assert result.total_trades == 2
    assert result.ic_mean == pytest.approx(1.0)
    assert result.total_return > 0


def test_factor_backtest_aligns_five_session_forward_returns_to_five_sessions_back():
    dates = pd.date_range("2026-01-01", periods=7)
    symbols = [f"{i:06d}" for i in range(10)]
    index = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
    factor = pd.Series([float(int(symbol)) for _date, symbol in index], index=index)
    realized_returns = []
    for date_index, _date in enumerate(dates):
        source_ranks = [
            float(int(symbol)) for _date, symbol in index[date_index * len(symbols):(date_index + 1) * len(symbols)]
        ]
        if date_index >= 5:
            realized_returns.extend(source_ranks)
        else:
            realized_returns.extend([-value for value in source_ranks])
    returns = pd.Series(realized_returns, index=index)

    result = FactorBacktester().backtest_factor(
        factor, returns, quantile=5, holding_days=5,
    )

    assert result.total_trades == 2
    assert result.ic_mean == pytest.approx(1.0)
    assert result.total_return > 0


def test_factor_return_alignment_counts_only_available_signal_sessions():
    dates = pd.date_range("2026-01-01", periods=6)
    factor_index = pd.MultiIndex.from_tuples(
        [(dates[index], "000001") for index in (0, 2, 3, 5)],
        names=["date", "symbol"],
    )
    returns_index = pd.MultiIndex.from_product(
        [dates, ["000001"]], names=["date", "symbol"],
    )
    factor = pd.Series([1.0, 2.0, 3.0, 4.0], index=factor_index)
    returns = pd.Series([0.0, 0.1, 0.2, 0.3, 0.4, 0.5], index=returns_index)

    aligned = _align_forward_returns_to_signal_session(
        factor, returns, holding_days=2,
    )

    assert aligned.loc[(dates[0], "000001")] == pytest.approx(0.3)
    assert aligned.loc[(dates[2], "000001")] == pytest.approx(0.5)
    assert pd.isna(aligned.loc[(dates[3], "000001")])
    assert pd.isna(aligned.loc[(dates[5], "000001")])


@pytest.mark.parametrize("holding_days", [0, -1, 1.5, True])
def test_factor_backtest_rejects_invalid_holding_horizon(holding_days):
    index = pd.MultiIndex.from_product(
        [pd.date_range("2026-01-01", periods=3), ["000001", "000002"]],
        names=["date", "symbol"],
    )
    factor = pd.Series([1.0, 2.0] * 3, index=index)
    returns = pd.Series([0.0, 0.0] * 3, index=index)

    with pytest.raises(ValueError, match="positive integer"):
        FactorBacktester().backtest_factor(factor, returns, holding_days=holding_days)
