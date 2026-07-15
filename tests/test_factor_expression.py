from __future__ import annotations

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.research.factor_expression import compile_factor_expression


def test_compile_factor_expression_extracts_fields_and_lookback() -> None:
    expr = compile_factor_expression(
        "rank(close / ts_mean(close, 5)) + delta(volume, 3)"
    )

    assert expr.fields == ("close", "volume")
    assert expr.max_lookback == 5


def test_factor_expression_evaluates_allowed_operations() -> None:
    frame = pd.DataFrame(
        {
            "close": [10, 11, 12, 13, 14, 15],
            "volume": [100, 110, 120, 130, 140, 150],
        }
    )
    expr = compile_factor_expression("close / ts_mean(close, 3) + delta(volume, 1)")

    result = expr.evaluate(frame)

    assert pd.isna(result.iloc[0])
    assert result.iloc[-1] > 0


def test_factor_expression_rejects_attribute_access() -> None:
    with pytest.raises(DataError, match="unsupported syntax|unsupported function"):
        compile_factor_expression("__import__('os').system('echo unsafe')")


def test_factor_expression_rejects_missing_fields() -> None:
    expr = compile_factor_expression("close + volume")

    with pytest.raises(DataError, match="missing fields"):
        expr.evaluate(pd.DataFrame({"close": [1, 2, 3]}))


def test_factor_expression_rejects_negative_lookback_window() -> None:
    with pytest.raises(DataError, match="lookback window"):
        compile_factor_expression("delta(close, -1)")


def test_factor_expression_rejects_wrong_function_arity() -> None:
    with pytest.raises(DataError, match="expects 1 argument"):
        compile_factor_expression("rank(close, 2)")


def test_factor_expression_rejects_dynamic_lookback_window() -> None:
    with pytest.raises(DataError, match="integer literal"):
        compile_factor_expression("ts_mean(close, window)")
