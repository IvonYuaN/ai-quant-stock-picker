"""Safe daily boolean expressions for tenant-authored strategy definitions."""
from __future__ import annotations

import ast
from dataclasses import dataclass

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.indicators import (
    DAILY_EXPRESSION_FIELDS,
    DAILY_FEATURE_LOOKBACK_BARS,
    DAILY_PIT_PRICE_FIELDS,
)
from aqsp.research.factor_expression import (
    FactorExpression,
    compile_factor_expression,
)


MAX_EXPRESSION_CHARS = 512
MAX_STRATEGY_LOOKBACK_BARS = 120
_CROSS_SECTIONAL_FUNCTIONS = frozenset({"rank", "zscore", "winsorize"})
_TIME_SERIES_SCORE_FUNCTIONS = frozenset({
    "ts_mean", "ts_std", "ts_sum", "ts_max", "ts_min", "ts_delay",
    "ts_delta", "ts_rank", "ts_zscore", "ts_corr", "ts_cov",
    "ts_quantile", "decay_linear", "delta",
})


@dataclass(frozen=True)
class StrategyExpression:
    compiled: FactorExpression
    fields: tuple[str, ...]
    max_lookback: int
    requires_pit_price_basis: bool

    def evaluate(self, frame: pd.DataFrame) -> pd.Series:
        """Return one fail-closed boolean per ascending daily bar."""
        values = self.compiled.evaluate(frame)
        return values.fillna(False).astype(bool)


@dataclass(frozen=True)
class StrategyScoreExpression:
    compiled: FactorExpression
    fields: tuple[str, ...]
    max_lookback: int
    requires_pit_price_basis: bool

    def evaluate(self, frame: pd.DataFrame) -> pd.Series:
        """Return numeric scores without filling missing values."""
        return self.compiled.evaluate(frame)


def compile_strategy_expression(expression: str) -> StrategyExpression:
    """Validate a boolean DSL expression against the shared daily feature schema."""
    clean = expression.strip()
    if not clean or len(clean) > MAX_EXPRESSION_CHARS:
        raise DataError(f"strategy expression must contain 1-{MAX_EXPRESSION_CHARS} characters")
    tree = ast.parse(clean, mode="eval")
    if any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _CROSS_SECTIONAL_FUNCTIONS
        for node in ast.walk(tree)
    ):
        raise DataError("single-symbol daily strategy scans cannot use cross-sectional functions")
    compiled = compile_factor_expression(clean)
    unknown = set(compiled.fields) - DAILY_EXPRESSION_FIELDS
    if unknown:
        raise DataError(f"strategy expression fields not in daily schema: {', '.join(sorted(unknown))}")
    if not compiled.fields:
        raise DataError("strategy expression must depend on at least one daily field")
    _require_boolean_expression(tree.body)
    lookback = max(
        compiled.max_lookback,
        *(DAILY_FEATURE_LOOKBACK_BARS.get(field, 1) for field in compiled.fields),
    )
    if lookback > MAX_STRATEGY_LOOKBACK_BARS:
        raise DataError(
            f"strategy expression lookback exceeds {MAX_STRATEGY_LOOKBACK_BARS} daily bars"
        )
    return StrategyExpression(
        compiled=compiled,
        fields=compiled.fields,
        max_lookback=lookback,
        requires_pit_price_basis=bool(set(compiled.fields) & DAILY_PIT_PRICE_FIELDS),
    )


def compile_strategy_score_expression(expression: str) -> StrategyScoreExpression:
    """Validate a numeric score expression against the shared daily schema."""
    clean = expression.strip()
    if not clean or len(clean) > MAX_EXPRESSION_CHARS:
        raise DataError(f"score expression must contain 1-{MAX_EXPRESSION_CHARS} characters")
    tree = ast.parse(clean, mode="eval")
    if any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _TIME_SERIES_SCORE_FUNCTIONS
        for node in ast.walk(tree)
    ):
        raise DataError("score expressions use daily feature fields; rolling functions belong in the field itself")
    if isinstance(tree.body, (ast.Compare, ast.BoolOp)) or (
        isinstance(tree.body, ast.UnaryOp) and isinstance(tree.body.op, ast.Not)
    ):
        raise DataError("score expression must return a numeric value")
    compiled = compile_factor_expression(clean)
    unknown = set(compiled.fields) - DAILY_EXPRESSION_FIELDS
    if unknown:
        raise DataError(f"score expression fields not in daily schema: {', '.join(sorted(unknown))}")
    if not compiled.fields:
        raise DataError("score expression must depend on at least one daily field")
    lookback = max(
        compiled.max_lookback,
        *(DAILY_FEATURE_LOOKBACK_BARS.get(field, 1) for field in compiled.fields),
    )
    if lookback > MAX_STRATEGY_LOOKBACK_BARS:
        raise DataError(
            f"score expression lookback exceeds {MAX_STRATEGY_LOOKBACK_BARS} daily bars"
        )
    return StrategyScoreExpression(
        compiled=compiled,
        fields=compiled.fields,
        max_lookback=lookback,
        requires_pit_price_basis=bool(set(compiled.fields) & DAILY_PIT_PRICE_FIELDS),
    )


def _require_boolean_expression(node: ast.expr) -> None:
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
        return
    if isinstance(node, ast.BoolOp) and len(node.values) >= 2:
        for value in node.values:
            _require_boolean_expression(value)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        _require_boolean_expression(node.operand)
        return
    raise DataError("strategy expression must be a comparison or boolean combination of comparisons")
