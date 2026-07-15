from __future__ import annotations

import pandas as pd
import pytest

from aqsp.strategies.factor_backtest import FactorBacktester


def test_factor_backtest_rejects_ambiguous_single_index_series() -> None:
    factor = pd.Series(list(range(25)), dtype=float)
    returns = pd.Series([10.0] * 5 + [0.0] * 5 + [0.0] * 5 + [0.0] * 5 + [-10.0] * 5)

    with pytest.raises(ValueError, match="MultiIndex"):
        FactorBacktester().backtest_factor(factor, returns, quantile=5)


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

    result = FactorBacktester().backtest_factor(factor, returns, quantile=5)

    assert result.total_trades == 20
    assert result.total_return > 0


def test_factor_expression_backtest_uses_symbol_history_when_multiindex() -> None:
    index = pd.MultiIndex.from_product(
        [pd.date_range("2026-01-01", periods=20), [f"{i:06d}" for i in range(10)]],
        names=["date", "symbol"],
    )
    frame = pd.DataFrame(
        {
            "close": [10.0 + int(symbol) + date_idx for date_idx, (_date, symbol) in enumerate(index)],
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
    assert result.total_trades == 20
    assert result.total_return > 0
