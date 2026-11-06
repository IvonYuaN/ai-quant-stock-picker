from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from aqsp.core.errors import DataError


FactorFunc = Callable[..., pd.Series]


@dataclass(frozen=True)
class FactorExpression:
    expression: str
    fields: tuple[str, ...]
    max_lookback: int

    def evaluate(self, frame: pd.DataFrame) -> pd.Series:
        missing = set(self.fields) - set(frame.columns)
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise DataError(f"factor expression missing fields: {missing_text}")
        tree = _parse_expression(self.expression)
        result = _FactorEvaluator(frame).visit(tree.body)
        if not isinstance(result, pd.Series):
            result = pd.Series(result, index=frame.index, dtype=float)
        result = pd.to_numeric(result, errors="coerce")
        return result.where(np.isfinite(result), np.nan)


@dataclass(frozen=True)
class FactorExpressionFunction:
    name: str
    family: str
    signature: str
    description: str
    example: str


_FUNCTION_HELP: tuple[FactorExpressionFunction, ...] = (
    FactorExpressionFunction("rank", "截面", "rank(value)", "按交易日对标的做截面百分位排名。", "rank(close)"),
    FactorExpressionFunction("zscore", "截面", "zscore(value)", "按交易日做截面标准化。", "zscore(amount)"),
    FactorExpressionFunction("winsorize", "截面", "winsorize(value, k=3)", "按交易日截面均值和标准差缩尾。", "winsorize(volume, 3)"),
    FactorExpressionFunction("ts_mean", "时序", "ts_mean(value, window)", "单标的滚动均值。", "ts_mean(close, 20)"),
    FactorExpressionFunction("ts_std", "时序", "ts_std(value, window)", "单标的滚动标准差。", "ts_std(close, 20)"),
    FactorExpressionFunction("ts_sum", "时序", "ts_sum(value, window)", "单标的滚动求和。", "ts_sum(volume, 5)"),
    FactorExpressionFunction("ts_max", "时序", "ts_max(value, window)", "单标的滚动最大值。", "ts_max(high, 20)"),
    FactorExpressionFunction("ts_min", "时序", "ts_min(value, window)", "单标的滚动最小值。", "ts_min(low, 20)"),
    FactorExpressionFunction("ts_delay", "时序", "ts_delay(value, window)", "单标的向后取历史值，不读取未来数据。", "ts_delay(close, 1)"),
    FactorExpressionFunction("ts_delta", "时序", "ts_delta(value, window)", "当前值减去指定历史值。", "ts_delta(close, 5)"),
    FactorExpressionFunction("ts_rank", "时序", "ts_rank(value, window)", "单标的窗口内末值的时间序列百分位。", "ts_rank(close, 20)"),
    FactorExpressionFunction("ts_zscore", "时序", "ts_zscore(value, window)", "单标的滚动标准分。", "ts_zscore(close, 20)"),
    FactorExpressionFunction("ts_corr", "时序", "ts_corr(left, right, window)", "单标的两个序列的滚动相关系数。", "ts_corr(close, volume, 20)"),
    FactorExpressionFunction("ts_cov", "时序", "ts_cov(left, right, window)", "单标的两个序列的滚动协方差。", "ts_cov(close, volume, 20)"),
    FactorExpressionFunction("ts_quantile", "时序", "ts_quantile(value, window, q)", "单标的滚动分位数，q 为 0 到 1。", "ts_quantile(close, 20, 0.8)"),
    FactorExpressionFunction("decay_linear", "时序", "decay_linear(value, window)", "单标的线性递增权重滚动均值。", "decay_linear(close, 5)"),
    FactorExpressionFunction("delta", "时序", "delta(value, window)", "单标的历史差值，与 ts_delta 使用同一计算口径。", "delta(close, 5)"),
    FactorExpressionFunction("if_else", "条件", "if_else(condition, yes, no)", "按布尔条件逐项选择数值。", "if_else(close > prev_close, 1, 0)"),
    FactorExpressionFunction("min", "算术", "min(left, right)", "逐项取较小值。", "min(high, close)"),
    FactorExpressionFunction("max", "算术", "max(left, right)", "逐项取较大值。", "max(low, close)"),
    FactorExpressionFunction("power", "算术", "power(value, exponent)", "幂运算，指数范围为 -4 到 4。", "power(close / prev_close, 2)"),
    FactorExpressionFunction("clamp", "算术", "clamp(value, lower, upper)", "将值限制在上下界之间。", "clamp(close, 0, 100)"),
    FactorExpressionFunction("log", "算术", "log(value)", "正值自然对数；非正值返回缺失。", "log(amount)"),
    FactorExpressionFunction("abs", "算术", "abs(value)", "绝对值。", "abs(close - prev_close)"),
    FactorExpressionFunction("sign", "算术", "sign(value)", "返回 -1、0 或 1。", "sign(close - prev_close)"),
    FactorExpressionFunction("sqrt", "算术", "sqrt(value)", "非负值平方根；负值返回缺失。", "sqrt(volume)"),
)


def factor_expression_functions() -> tuple[FactorExpressionFunction, ...]:
    """Return editor help for exactly the callable functions accepted by the DSL."""
    return _FUNCTION_HELP


def compile_factor_expression(expression: str) -> FactorExpression:
    tree = _parse_expression(expression)
    inspector = _FactorInspector()
    inspector.visit(tree)
    return FactorExpression(
        expression=expression.strip(),
        fields=tuple(sorted(inspector.fields)),
        max_lookback=inspector.max_lookback,
    )


class _FactorInspector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.fields: set[str] = set()
        self.max_lookback = 1

    def visit_Name(self, node: ast.Name) -> None:
        if node.id not in _ALLOWED_FUNCTIONS:
            self.fields.add(node.id)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            not isinstance(node.func, ast.Name)
            or node.func.id not in _ALLOWED_FUNCTIONS
        ):
            raise DataError("factor expression uses unsupported function")
        if node.keywords:
            raise DataError("factor expression keyword arguments are not allowed")
        _validate_call(node)
        for arg in node.args:
            self.visit(arg)
        self.max_lookback = max(self.max_lookback, _expression_lookback(node))

    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, _ALLOWED_NODES):
            raise DataError(
                f"factor expression uses unsupported syntax: {type(node).__name__}"
            )
        super().generic_visit(node)


class _FactorEvaluator(ast.NodeVisitor):
    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def visit_Name(self, node: ast.Name) -> pd.Series:
        if node.id in _ALLOWED_FUNCTIONS:
            raise DataError("function name cannot be used as a value")
        return pd.to_numeric(self._frame[node.id], errors="coerce")

    def visit_Constant(self, node: ast.Constant) -> float:
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            raise DataError("factor expression constants must be numeric")
        return float(node.value)

    def visit_BinOp(self, node: ast.BinOp) -> pd.Series:
        left = self.visit(node.left)
        right = self.visit(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        raise DataError("factor expression operator is not allowed")

    def visit_UnaryOp(self, node: ast.UnaryOp) -> pd.Series:
        operand = self.visit(node.operand)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.Not):
            return ~operand.astype(bool)
        raise DataError("factor expression unary operator is not allowed")

    def visit_Compare(self, node: ast.Compare) -> pd.Series:
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise DataError("factor expression comparisons must have one operator")
        left = self.visit(node.left)
        right = self.visit(node.comparators[0])
        operation = node.ops[0]
        if isinstance(operation, ast.Gt):
            return left > right
        if isinstance(operation, ast.GtE):
            return left >= right
        if isinstance(operation, ast.Lt):
            return left < right
        if isinstance(operation, ast.LtE):
            return left <= right
        if isinstance(operation, ast.Eq):
            return left == right
        if isinstance(operation, ast.NotEq):
            return left != right
        raise DataError("factor expression comparison is not allowed")

    def visit_BoolOp(self, node: ast.BoolOp) -> pd.Series:
        values = [self.visit(value).astype(bool) for value in node.values]
        if isinstance(node.op, ast.And):
            result = values[0]
            for value in values[1:]:
                result = result & value
            return result
        if isinstance(node.op, ast.Or):
            result = values[0]
            for value in values[1:]:
                result = result | value
            return result
        raise DataError("factor expression boolean operator is not allowed")

    def visit_Call(self, node: ast.Call) -> pd.Series:
        if not isinstance(node.func, ast.Name):
            raise DataError("factor expression call target is not allowed")
        func = _ALLOWED_FUNCTIONS.get(node.func.id)
        if func is None:
            raise DataError(f"factor expression function not allowed: {node.func.id}")
        _validate_call(node)
        args = [self.visit(arg) for arg in node.args]
        constant_indexes = _CONSTANT_ARGUMENTS.get(node.func.id, frozenset())
        values = []
        for index, value in enumerate(args):
            if index not in constant_indexes and not isinstance(value, pd.Series):
                value = pd.Series(value, index=self._frame.index)
            values.append(value)
        return func(*values)


def _parse_expression(expression: str) -> ast.Expression:
    if not expression or len(expression) > 1000:
        raise DataError("factor expression must contain 1 to 1000 characters")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        location = f" at line {exc.lineno}, column {exc.offset}" if exc.lineno and exc.offset else ""
        raise DataError(f"factor expression syntax error{location}") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 200:
        raise DataError("factor expression exceeds 200 syntax nodes")
    if max((_node_depth(node) for node in nodes), default=0) > 12:
        raise DataError("factor expression exceeds maximum syntax depth 12")
    return tree


def _call_lookback(node: ast.Call) -> int:
    if node.func.id in {"rank", "zscore", "winsorize"}:
        return 1
    if node.func.id in {"ts_corr", "ts_cov"}:
        window_arg = 2
    elif node.func.id == "ts_quantile":
        window_arg = 1
    elif node.func.id in {"ts_mean", "ts_std", "ts_sum", "ts_max", "ts_min", "ts_delay", "ts_delta", "ts_rank", "ts_zscore", "decay_linear", "delta"}:
        window_arg = 1
    else:
        return max((_expression_lookback(arg) for arg in node.args), default=1)
    window = _literal_int_arg(node.args[window_arg])
    if window is None:
        raise DataError("factor expression lookback window must be an integer literal")
    if window <= 0:
        raise DataError("factor expression lookback window must be positive")
    return window


def _validate_call(node: ast.Call) -> None:
    if not isinstance(node.func, ast.Name):
        raise DataError("factor expression call target is not allowed")
    function_name = node.func.id
    arity = _FUNCTION_ARITY.get(function_name)
    if arity is None:
        raise DataError(f"factor expression function not allowed: {function_name}")
    minimum, maximum = arity
    if not minimum <= len(node.args) <= maximum:
        raise DataError(
            f"factor expression function {function_name} expects {minimum} argument(s)"
        )
    if function_name == "ts_quantile" and len(node.args) == 3:
        quantile = _literal_number_arg(node.args[2])
        if quantile is None or not 0 <= quantile <= 1:
            raise DataError("ts_quantile q must be a numeric literal in [0, 1]")
    if function_name == "winsorize" and len(node.args) == 2:
        scale = _literal_number_arg(node.args[1])
        if scale is None or not 1 <= scale <= 6:
            raise DataError("winsorize k must be a numeric literal in [1, 6]")
    if function_name == "power" and len(node.args) == 2:
        power = _literal_number_arg(node.args[1])
        if power is None or abs(power) > 4:
            raise DataError("power exponent must be a numeric literal in [-4, 4]")
    if function_name == "clamp" and len(node.args) == 3:
        lower = _literal_number_arg(node.args[1])
        upper = _literal_number_arg(node.args[2])
        if lower is None or upper is None or lower > upper:
            raise DataError("clamp bounds must be numeric literals with lo <= hi")


def _literal_int_arg(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return int(node.value)
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int)
    ):
        return -int(node.operand.value)
    return None


def _literal_number_arg(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value) if math.isfinite(float(node.value)) else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _literal_number_arg(node.operand)
        return -value if value is not None else None
    return None


def _node_depth(node: ast.AST) -> int:
    children = list(ast.iter_child_nodes(node))
    return 1 + max((_node_depth(child) for child in children), default=0)


def _expression_lookback(node: ast.AST) -> int:
    if isinstance(node, ast.Call):
        child_lookback = max(
            (_expression_lookback(arg) for arg in node.args),
            default=1,
        )
        if node.func.id in {
            "ts_mean", "ts_std", "ts_sum", "ts_max", "ts_min", "ts_delay",
            "ts_delta", "ts_rank", "ts_zscore", "ts_corr", "ts_cov",
            "ts_quantile", "decay_linear", "delta",
        }:
            return _call_lookback(node) + max(child_lookback - 1, 0)
        return child_lookback
    return max(( _expression_lookback(child) for child in ast.iter_child_nodes(node)), default=1)


def _ts_mean(series: pd.Series, window: float) -> pd.Series:
    size = _positive_window(window)
    return _by_symbol(series, lambda values: values.rolling(size, min_periods=size).mean())


def _ts_std(series: pd.Series, window: float) -> pd.Series:
    size = _positive_window(window)
    return _by_symbol(series, lambda values: values.rolling(size, min_periods=size).std(ddof=0))


def _delta(series: pd.Series, window: float) -> pd.Series:
    size = _positive_window(window)
    return _by_symbol(series, lambda values: values - values.shift(size))


def _rank(series: pd.Series) -> pd.Series:
    if _is_factor_panel(series):
        return series.groupby(level="date", sort=False).rank(pct=True)
    return series.rank(pct=True)


def _ts_sum(series: pd.Series, window: float) -> pd.Series:
    size = _positive_window(window)
    return _by_symbol(series, lambda values: values.rolling(size, min_periods=size).sum())


def _ts_max(series: pd.Series, window: float) -> pd.Series:
    size = _positive_window(window)
    return _by_symbol(series, lambda values: values.rolling(size, min_periods=size).max())


def _ts_min(series: pd.Series, window: float) -> pd.Series:
    size = _positive_window(window)
    return _by_symbol(series, lambda values: values.rolling(size, min_periods=size).min())


def _ts_delay(series: pd.Series, window: float) -> pd.Series:
    size = _positive_window(window)
    return _by_symbol(series, lambda values: values.shift(size))


def _ts_rank(series: pd.Series, window: float) -> pd.Series:
    size = _positive_window(window)
    def rank_last(values: np.ndarray) -> float:
        return float(pd.Series(values).rank(pct=True).iloc[-1])

    return _by_symbol(series, lambda values: values.rolling(size, min_periods=size).apply(rank_last, raw=True))


def _ts_zscore(series: pd.Series, window: float) -> pd.Series:
    mean = _ts_mean(series, window)
    std = _ts_std(series, window).replace(0, np.nan)
    return (series - mean) / std


def _ts_corr(left: pd.Series, right: pd.Series, window: float) -> pd.Series:
    size = _positive_window(window)
    return _binary_by_symbol(
        left,
        right,
        lambda a, b: a.rolling(size, min_periods=size).corr(b),
    )


def _ts_cov(left: pd.Series, right: pd.Series, window: float) -> pd.Series:
    size = _positive_window(window)
    return _binary_by_symbol(
        left,
        right,
        lambda a, b: a.rolling(size, min_periods=size).cov(b, ddof=0),
    )


def _ts_quantile(series: pd.Series, window: float, quantile: float) -> pd.Series:
    size = _positive_window(window)
    return _by_symbol(series, lambda values: values.rolling(size, min_periods=size).quantile(quantile))


def _decay_linear(series: pd.Series, window: float) -> pd.Series:
    size = _positive_window(window)
    weights = np.arange(1, size + 1, dtype=float)
    weights /= weights.sum()
    return _by_symbol(
        series,
        lambda values: values.rolling(size, min_periods=size).apply(lambda window_values: float(np.dot(window_values, weights)), raw=True),
    )


def _zscore(series: pd.Series) -> pd.Series:
    def normalize(values: pd.Series) -> pd.Series:
        std = values.std(ddof=0)
        return (values - values.mean()) / std if std > 0 else pd.Series(np.nan, index=values.index)

    return series.groupby(level="date", sort=False).transform(normalize) if _is_factor_panel(series) else normalize(series)


def _winsorize(series: pd.Series, scale: float = 3.0) -> pd.Series:
    def clip(values: pd.Series) -> pd.Series:
        mean = values.mean()
        std = values.std(ddof=0)
        return values.clip(mean - scale * std, mean + scale * std) if std > 0 else values

    return series.groupby(level="date", sort=False).transform(clip) if _is_factor_panel(series) else clip(series)


def _if_else(condition: pd.Series, when_true: pd.Series, when_false: pd.Series) -> pd.Series:
    return when_true.where(condition.fillna(False).astype(bool), when_false)


def _elementwise_min(left: pd.Series, right: pd.Series) -> pd.Series:
    return pd.concat([left, right], axis=1).min(axis=1, skipna=False)


def _elementwise_max(left: pd.Series, right: pd.Series) -> pd.Series:
    return pd.concat([left, right], axis=1).max(axis=1, skipna=False)


def _by_symbol(series: pd.Series, operation: Callable[[pd.Series], pd.Series]) -> pd.Series:
    if not _is_factor_panel(series):
        return operation(series)
    symbol_level = series.index.names.index("symbol")
    parts = [
        operation(group.sort_index(level="date"))
        for _symbol, group in series.groupby(level=symbol_level, sort=False)
    ]
    return pd.concat(parts).reindex(series.index) if parts else series.copy()


def _binary_by_symbol(
    left: pd.Series,
    right: pd.Series,
    operation: Callable[[pd.Series, pd.Series], pd.Series],
) -> pd.Series:
    if not _is_factor_panel(left):
        return operation(left, right)
    symbol_level = left.index.names.index("symbol")
    parts: list[pd.Series] = []
    for _symbol, group in left.groupby(level=symbol_level, sort=False):
        index = group.sort_index(level="date").index
        parts.append(operation(left.reindex(index), right.reindex(index)))
    return pd.concat(parts).reindex(left.index) if parts else left.copy()


def _is_factor_panel(series: pd.Series) -> bool:
    return isinstance(series.index, pd.MultiIndex) and set(series.index.names) == {"date", "symbol"}


def _positive_window(window: float) -> int:
    size = int(window)
    if size <= 0:
        raise DataError("factor expression lookback window must be positive")
    return size


_ALLOWED_FUNCTIONS: dict[str, FactorFunc] = {
    "ts_mean": _ts_mean,
    "ts_std": _ts_std,
    "ts_sum": _ts_sum,
    "ts_max": _ts_max,
    "ts_min": _ts_min,
    "ts_delay": _ts_delay,
    "ts_delta": _delta,
    "ts_rank": _ts_rank,
    "ts_zscore": _ts_zscore,
    "ts_corr": _ts_corr,
    "ts_cov": _ts_cov,
    "ts_quantile": _ts_quantile,
    "decay_linear": _decay_linear,
    "delta": _delta,
    "rank": _rank,
    "zscore": _zscore,
    "winsorize": _winsorize,
    "power": lambda series, exponent: series.pow(exponent),
    "clamp": lambda series, lower, upper: series.clip(lower, upper),
    "if_else": _if_else,
    "min": _elementwise_min,
    "max": _elementwise_max,
    "log": lambda series: np.log(series.where(series > 0)),
    "abs": lambda series: series.abs(),
    "sign": lambda series: np.sign(series),
    "sqrt": lambda series: np.sqrt(series.where(series >= 0)),
}

_FUNCTION_ARITY: dict[str, tuple[int, int]] = {
    "ts_mean": (2, 2),
    "ts_std": (2, 2),
    "ts_sum": (2, 2),
    "ts_max": (2, 2),
    "ts_min": (2, 2),
    "ts_delay": (2, 2),
    "ts_delta": (2, 2),
    "ts_rank": (2, 2),
    "ts_zscore": (2, 2),
    "ts_corr": (3, 3),
    "ts_cov": (3, 3),
    "ts_quantile": (3, 3),
    "decay_linear": (2, 2),
    "delta": (2, 2),
    "rank": (1, 1),
    "zscore": (1, 1),
    "winsorize": (1, 2),
    "power": (2, 2),
    "clamp": (3, 3),
    "if_else": (3, 3),
    "min": (2, 2),
    "max": (2, 2),
    "log": (1, 1),
    "abs": (1, 1),
    "sign": (1, 1),
    "sqrt": (1, 1),
}

_CONSTANT_ARGUMENTS: dict[str, frozenset[int]] = {
    name: frozenset({1})
    for name in (
        "ts_mean", "ts_std", "ts_sum", "ts_max", "ts_min", "ts_delay",
        "ts_delta", "ts_rank", "ts_zscore", "decay_linear", "delta",
        "power", "winsorize",
    )
}
_CONSTANT_ARGUMENTS.update(
    {
        "ts_corr": frozenset({2}),
        "ts_cov": frozenset({2}),
        "ts_quantile": frozenset({1, 2}),
        "clamp": frozenset({1, 2}),
    }
)

_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.USub,
    ast.UAdd,
    ast.Compare,
    ast.Gt,
    ast.GtE,
    ast.Lt,
    ast.LtE,
    ast.Eq,
    ast.NotEq,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.Not,
)
