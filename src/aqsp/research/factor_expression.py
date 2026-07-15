from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass

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
        return pd.to_numeric(result, errors="coerce")


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
        self.max_lookback = max(self.max_lookback, _call_lookback(node))

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
        if not isinstance(node.value, (int, float)):
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
        raise DataError("factor expression unary operator is not allowed")

    def visit_Call(self, node: ast.Call) -> pd.Series:
        if not isinstance(node.func, ast.Name):
            raise DataError("factor expression call target is not allowed")
        func = _ALLOWED_FUNCTIONS.get(node.func.id)
        if func is None:
            raise DataError(f"factor expression function not allowed: {node.func.id}")
        _validate_call(node)
        return func(*(self.visit(arg) for arg in node.args))


def _parse_expression(expression: str) -> ast.Expression:
    try:
        return ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise DataError("factor expression syntax error") from exc


def _call_lookback(node: ast.Call) -> int:
    if node.func.id == "rank":
        return 1
    window = _literal_int_arg(node.args[1])
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


def _ts_mean(series: pd.Series, window: float) -> pd.Series:
    size = _positive_window(window)
    return series.rolling(size, min_periods=size).mean()


def _ts_std(series: pd.Series, window: float) -> pd.Series:
    size = _positive_window(window)
    return series.rolling(size, min_periods=size).std(ddof=0)


def _delta(series: pd.Series, window: float) -> pd.Series:
    return series - series.shift(_positive_window(window))


def _rank(series: pd.Series) -> pd.Series:
    return series.rank(pct=True)


def _positive_window(window: float) -> int:
    size = int(window)
    if size <= 0:
        raise DataError("factor expression lookback window must be positive")
    return size


_ALLOWED_FUNCTIONS: dict[str, FactorFunc] = {
    "ts_mean": _ts_mean,
    "ts_std": _ts_std,
    "delta": _delta,
    "rank": _rank,
}

_FUNCTION_ARITY: dict[str, tuple[int, int]] = {
    "ts_mean": (2, 2),
    "ts_std": (2, 2),
    "delta": (2, 2),
    "rank": (1, 1),
}

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
)
