from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from aqsp.research.factor_expression import FactorExpression, compile_factor_expression


@dataclass(frozen=True)
class FactorBacktestResult:
    factor_name: str
    total_return: float
    annualized_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    profit_loss_ratio: float
    total_trades: int
    ic_mean: float
    ic_std: float
    ic_ir: float


class FactorBacktester:
    def __init__(self, initial_capital: float = 1000000) -> None:
        self.initial_capital = initial_capital

    def backtest_factor(
        self,
        factor_values: pd.Series,
        returns: pd.Series,
        quantile: int = 5,
        holding_days: int = 5,
    ) -> FactorBacktestResult:
        if _is_date_symbol_multiindex(
            factor_values.index
        ) and _is_date_symbol_multiindex(returns.index):
            return self._backtest_cross_sectional_factor(
                factor_values,
                returns,
                quantile=quantile,
                holding_days=holding_days,
                factor_name="",
            )

        raise ValueError(
            "factor backtest requires a (date, symbol) MultiIndex for point-in-time "
            "cross-sectional validation"
        )

    def backtest_expression(
        self,
        expression: str,
        factor_frame: pd.DataFrame,
        returns: pd.Series,
        quantile: int = 5,
        holding_days: int = 5,
    ) -> FactorBacktestResult:
        if not _is_date_symbol_multiindex(factor_frame.index) or not (
            _is_date_symbol_multiindex(returns.index)
        ):
            raise ValueError(
                "factor expression backtest requires (date, symbol) MultiIndex inputs"
            )
        compiled = compile_factor_expression(expression)
        factor_values = _evaluate_expression_by_symbol(compiled, factor_frame)
        return self._backtest_cross_sectional_factor(
            factor_values,
            returns,
            quantile=quantile,
            holding_days=holding_days,
            factor_name=compiled.expression,
        )

    def backtest_factor_combination(
        self,
        factors: dict[str, pd.Series],
        weights: dict[str, float],
        returns: pd.Series,
        quantile: int = 5,
    ) -> FactorBacktestResult:
        if not factors or not weights:
            return self._empty_result("")

        if _is_date_symbol_multiindex(returns.index) and all(
            _is_date_symbol_multiindex(fv.index) for fv in factors.values()
        ):
            combined = self._combine_factor_scores(factors, weights, returns.index)
            return self._backtest_cross_sectional_factor(
                combined,
                returns,
                quantile=quantile,
                holding_days=1,
                factor_name="_".join(sorted(factors.keys())),
            )

        raise ValueError(
            "factor combination backtest requires (date, symbol) MultiIndex factors "
            "and returns"
        )

    def _backtest_cross_sectional_factor(
        self,
        factor_values: pd.Series,
        returns: pd.Series,
        *,
        quantile: int,
        holding_days: int,
        factor_name: str,
    ) -> FactorBacktestResult:
        aligned = pd.concat([factor_values, returns], axis=1).dropna()
        if len(aligned) < 20:
            return self._empty_result(factor_name)
        aligned.columns = ["factor", "return"]
        daily_returns: list[tuple[object, float]] = []
        ic_values: list[float] = []
        for trade_date, group in aligned.groupby(level=0, sort=True):
            if len(group) < quantile * 2:
                continue
            try:
                labels = pd.qcut(
                    group["factor"],
                    quantile,
                    labels=False,
                    duplicates="drop",
                )
            except ValueError:
                continue
            valid = labels.dropna()
            if valid.empty or valid.nunique() < 2:
                continue
            long_ret = group.loc[labels == valid.max(), "return"].mean()
            short_ret = group.loc[labels == valid.min(), "return"].mean()
            daily_returns.append((trade_date, float(long_ret - short_ret)))
            ic = group["factor"].corr(group["return"], method="spearman")
            if pd.notna(ic):
                ic_values.append(float(ic))
        if not daily_returns:
            return self._empty_result(factor_name)
        strategy_returns = pd.Series(
            [value for _date, value in daily_returns],
            index=[trade_date for trade_date, _value in daily_returns],
        )
        ic_mean = float(np.mean(ic_values)) if ic_values else 0.0
        ic_std = float(np.std(ic_values, ddof=1)) if len(ic_values) > 1 else 0.0
        return self._result_from_returns(
            strategy_returns,
            factor_name=factor_name,
            holding_days=holding_days,
            ic_mean=ic_mean,
            ic_std=ic_std,
            ic_ir=ic_mean / ic_std if ic_std > 0 else 0.0,
        )

    def _combine_factor_scores(
        self,
        factors: dict[str, pd.Series],
        weights: dict[str, float],
        target_index: pd.Index,
    ) -> pd.Series:
        common_index = target_index
        for name, series in factors.items():
            if name in weights:
                common_index = common_index.intersection(series.dropna().index)
        combined = pd.Series(0.0, index=common_index)
        total_weight = 0.0
        for name, series in factors.items():
            if name not in weights:
                continue
            weight = float(weights[name])
            aligned = series.reindex(common_index)
            ranks = aligned.groupby(level=0).rank(pct=True)
            combined += ranks * weight
            total_weight += abs(weight)
        if total_weight > 0:
            combined = combined / total_weight
        return combined

    def _result_from_returns(
        self,
        strategy_returns: pd.Series,
        *,
        factor_name: str,
        holding_days: int,
        ic_mean: float,
        ic_std: float,
        ic_ir: float,
    ) -> FactorBacktestResult:
        equity = np.cumprod(1 + strategy_returns.values / 100)
        total_return = float(equity[-1] - 1)
        n_periods = len(strategy_returns)
        periods_per_year = 252 / max(int(holding_days), 1)
        annualized_return = float(
            (1 + total_return) ** (periods_per_year / max(n_periods, 1)) - 1
        )
        returns_std = float(strategy_returns.std())
        sharpe_ratio = (
            float(strategy_returns.mean() / returns_std * np.sqrt(periods_per_year))
            if returns_std > 0
            else 0.0
        )
        running_max = np.maximum.accumulate(equity)
        drawdown = 1 - equity / running_max
        max_drawdown = float(drawdown.max())
        wins = sum(1 for r in strategy_returns if r > 0)
        win_rate = wins / len(strategy_returns) if len(strategy_returns) > 0 else 0.0
        pos_returns = strategy_returns[strategy_returns > 0]
        neg_returns = strategy_returns[strategy_returns < 0]
        avg_pos = float(pos_returns.mean()) if len(pos_returns) > 0 else 0.0
        avg_neg = float(neg_returns.mean()) if len(neg_returns) > 0 else 0.0
        profit_loss_ratio = abs(avg_pos / avg_neg) if avg_neg != 0 else 0.0
        return FactorBacktestResult(
            factor_name=factor_name,
            total_return=round(total_return, 6),
            annualized_return=round(annualized_return, 6),
            sharpe_ratio=round(sharpe_ratio, 4),
            max_drawdown=round(max_drawdown, 6),
            win_rate=round(win_rate, 4),
            profit_loss_ratio=round(profit_loss_ratio, 4),
            total_trades=len(strategy_returns),
            ic_mean=round(ic_mean, 6),
            ic_std=round(ic_std, 6),
            ic_ir=round(ic_ir, 4),
        )

    def generate_backtest_report(self, results: list[FactorBacktestResult]) -> str:
        if not results:
            return "无回测结果"

        lines = [
            "# 因子回测报告",
            "",
            f"共回测 {len(results)} 个因子/组合",
            "",
            "## 回测结果概览",
            "",
            "| 因子名称 | 总收益 | 年化收益 | Sharpe | 最大回撤 | 胜率 | 盈亏比 | 交易次数 | IC均值 | IC_IR |",
            "|----------|--------|----------|--------|----------|------|--------|----------|--------|-------|",
        ]

        for r in results:
            lines.append(
                f"| {r.factor_name} "
                f"| {r.total_return:.2%} "
                f"| {r.annualized_return:.2%} "
                f"| {r.sharpe_ratio:.2f} "
                f"| {r.max_drawdown:.2%} "
                f"| {r.win_rate:.2%} "
                f"| {r.profit_loss_ratio:.2f} "
                f"| {r.total_trades} "
                f"| {r.ic_mean:.4f} "
                f"| {r.ic_ir:.2f} |"
            )

        lines.append("")

        best_sharpe = max(results, key=lambda x: x.sharpe_ratio)
        best_ic = max(results, key=lambda x: abs(x.ic_ir))

        lines.extend(
            [
                "## 最佳因子",
                "",
                f"- **最高Sharpe**: {best_sharpe.factor_name} (Sharpe: {best_sharpe.sharpe_ratio:.2f})",
                f"- **最高IC_IR**: {best_ic.factor_name} (IC_IR: {best_ic.ic_ir:.2f})",
                "",
            ]
        )

        return "\n".join(lines)

    def _empty_result(self, factor_name: str) -> FactorBacktestResult:
        return FactorBacktestResult(
            factor_name=factor_name,
            total_return=0.0,
            annualized_return=0.0,
            sharpe_ratio=0.0,
            max_drawdown=0.0,
            win_rate=0.0,
            profit_loss_ratio=0.0,
            total_trades=0,
            ic_mean=0.0,
            ic_std=0.0,
            ic_ir=0.0,
        )


def _is_date_symbol_multiindex(index: pd.Index) -> bool:
    return isinstance(index, pd.MultiIndex) and index.nlevels >= 2


def _evaluate_expression_by_symbol(
    compiled: FactorExpression,
    frame: pd.DataFrame,
) -> pd.Series:
    symbol_level = (
        frame.index.names.index("symbol") if "symbol" in frame.index.names else 1
    )
    parts: list[pd.Series] = []
    for _symbol, group in frame.sort_index().groupby(
        level=symbol_level,
        sort=False,
    ):
        evaluated = compiled.evaluate(group)
        evaluated.index = group.index
        parts.append(evaluated)
    if not parts:
        return pd.Series(dtype=float, index=frame.index)
    return pd.concat(parts).sort_index()
