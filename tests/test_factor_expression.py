from __future__ import annotations

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.research import factor_expression as factor_expression_module
from aqsp.research.factor_expression import compile_factor_expression, factor_expression_functions


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


def test_factor_expression_rank_is_cross_sectional_by_date() -> None:
    index = pd.MultiIndex.from_product(
        [["2026-01-02", "2026-01-05"], ["000001", "600000"]],
        names=["date", "symbol"],
    )
    frame = pd.DataFrame({"close": [1.0, 10.0, 3.0, 20.0]}, index=index)

    result = compile_factor_expression("rank(close)").evaluate(frame)

    assert result.loc[("2026-01-02", "000001")] == 0.5
    assert result.loc[("2026-01-02", "600000")] == 1.0
    assert result.loc[("2026-01-05", "000001")] == 0.5
    assert result.loc[("2026-01-05", "600000")] == 1.0


def test_factor_expression_ts_rank_and_rolling_mean_are_per_symbol() -> None:
    index = pd.MultiIndex.from_product(
        [["2026-01-02", "2026-01-05", "2026-01-06"], ["000001", "600000"]],
        names=["date", "symbol"],
    )
    frame = pd.DataFrame({"close": [1.0, 10.0, 3.0, 20.0, 2.0, 15.0]}, index=index)

    rolling_mean = compile_factor_expression("ts_mean(close, 2)").evaluate(frame)
    time_rank = compile_factor_expression("ts_rank(close, 2)").evaluate(frame)

    assert rolling_mean.loc[("2026-01-05", "000001")] == 2.0
    assert rolling_mean.loc[("2026-01-05", "600000")] == 15.0
    assert time_rank.loc[("2026-01-05", "000001")] == 1.0
    assert time_rank.loc[("2026-01-05", "600000")] == 1.0
    assert time_rank.loc[("2026-01-06", "600000")] == 0.5


def test_factor_expression_rejects_future_shift_and_attribute_access() -> None:
    with pytest.raises(DataError, match="unsupported syntax|unsupported function"):
        compile_factor_expression("close.shift(-1)")


def test_factor_expression_computes_nested_warmup_without_lookahead() -> None:
    expression = compile_factor_expression("ts_mean(ts_mean(close, 5), 5)")
    assert expression.max_lookback == 9


def test_factor_expression_editor_examples_match_the_executable_dsl() -> None:
    functions = factor_expression_functions()

    assert len(functions) == 26
    assert len({item.name for item in functions}) == len(functions)
    assert {item.name for item in functions} == set(factor_expression_module._ALLOWED_FUNCTIONS)
    for item in functions:
        compiled = compile_factor_expression(item.example)
        assert compiled.max_lookback >= 1
