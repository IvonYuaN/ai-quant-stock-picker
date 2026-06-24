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
