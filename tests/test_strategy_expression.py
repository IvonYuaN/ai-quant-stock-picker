from __future__ import annotations

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.indicators import DAILY_INDICATOR_FIELDS, enrich_indicators
from aqsp.research.strategy_expression import (
    compile_strategy_expression,
    compile_strategy_score_expression,
)


def _daily_frame(rows: int) -> pd.DataFrame:
    close = [20.0 + index * 0.15 + (index % 4) * 0.07 for index in range(rows)]
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=rows, freq="B").strftime("%Y-%m-%d"),
            "open": [value - 0.1 for value in close],
            "high": [value + 0.5 for value in close],
            "low": [value - 0.6 for value in close],
            "close": close,
            "volume": [1000.0 + index * 5 for index in range(rows)],
            "amount": [25000.0 + index * 100 for index in range(rows)],
        }
    )


def test_strategy_expression_compiles_shared_fields_and_pit_contract():
    compiled = compile_strategy_expression("kdj_j < 50 and close > ma60")

    assert compiled.fields == ("close", "kdj_j", "ma60")
    assert compiled.max_lookback == 60
    assert compiled.requires_pit_price_basis


@pytest.mark.parametrize(
    "expression",
    [
        "close",
        "1 > 0",
        "future_return > 0",
        "close.shift(-1) > close",
        "rank(close) > 0.9",
    ],
)
def test_strategy_expression_rejects_non_boolean_unknown_and_unsafe_syntax(expression):
    with pytest.raises(DataError):
        compile_strategy_expression(expression)


def test_strategy_expression_uses_shared_kdj_bollinger_and_volatility_fields():
    frame = enrich_indicators(_daily_frame(80))
    expression = compile_strategy_expression(
        "kdj_j > kdj_d and close > bb_middle20 and annualized_volatility20 > 0"
    )

    evaluated = expression.evaluate(frame)

    assert DAILY_INDICATOR_FIELDS.issubset(frame.columns)
    assert evaluated.dtype == bool
    assert evaluated.iloc[-1]


def test_strategy_expression_indicators_do_not_depend_on_future_rows():
    full = _daily_frame(80)
    prefix = _daily_frame(55)
    expression = compile_strategy_expression("close > ma20 and kdj_k > kdj_d")

    full_values = expression.evaluate(enrich_indicators(full)).iloc[:55].reset_index(drop=True)
    prefix_values = expression.evaluate(enrich_indicators(prefix)).reset_index(drop=True)

    pd.testing.assert_series_equal(full_values, prefix_values)


def test_strategy_score_expression_accepts_numeric_cross_sectional_formula():
    compiled = compile_strategy_score_expression("rank(volume_ratio) + rank(ret_5)")

    assert compiled.fields == ("ret_5", "volume_ratio")
    assert compiled.requires_pit_price_basis


@pytest.mark.parametrize(
    "expression",
    ["close > ma20", "future_score", "close.shift(-1)", "ts_mean(close, 5)"],
)
def test_strategy_score_expression_rejects_boolean_unknown_and_unsafe_forms(expression):
    with pytest.raises(DataError):
        compile_strategy_score_expression(expression)
