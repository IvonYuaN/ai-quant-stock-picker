from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


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
        aligned = pd.concat([factor_values, returns], axis=1).dropna()
        if len(aligned) < 20:
            return self._empty_result("")

        fv = aligned.iloc[:, 0]
        fr = aligned.iloc[:, 1]

        try:
            quantile_labels = pd.qcut(fv, quantile, labels=False, duplicates="drop")
        except ValueError:
            return self._empty_result("")

        if quantile_labels is None or quantile_labels.empty:
            return self._empty_result("")

        quantile_returns = fr.groupby(quantile_labels).mean()

        if len(quantile_returns) < 2:
            return self._empty_result("")

        long_quantile = quantile_returns.idxmax()
        short_quantile = quantile_returns.idxmin()

        long_mask = quantile_labels == long_quantile
        short_mask = quantile_labels == short_quantile

        long_returns = fr[long_mask]
        short_returns = fr[short_mask]

        strategy_returns = long_returns - short_returns.mean()

        equity = np.cumprod(1 + strategy_returns.values / 100)
        total_return = float(equity[-1] - 1)

        n_periods = len(strategy_returns)
        annualized_return = float((1 + total_return) ** (252 / max(n_periods, 1)) - 1)

        returns_std = float(strategy_returns.std())
        sharpe_ratio = (
            float(strategy_returns.mean() / returns_std * np.sqrt(252))
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

        ic_series = fv.rolling(20).corr(fr)
        ic_series = ic_series.dropna()
        ic_mean = float(ic_series.mean()) if len(ic_series) > 0 else 0.0
        ic_std = float(ic_series.std()) if len(ic_series) > 0 else 0.0
        ic_ir = ic_mean / ic_std if ic_std > 0 else 0.0

        return FactorBacktestResult(
            factor_name="",
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

    def backtest_factor_combination(
        self,
        factors: dict[str, pd.Series],
        weights: dict[str, float],
        returns: pd.Series,
        quantile: int = 5,
    ) -> FactorBacktestResult:
        if not factors or not weights:
            return self._empty_result("")

        common_index = returns.index
        for fv in factors.values():
            common_index = common_index.intersection(fv.dropna().index)

        if len(common_index) < 20:
            return self._empty_result("")

        combined_score = pd.Series(0.0, index=common_index)
        total_weight = 0.0

        for name, fv in factors.items():
            if name not in weights:
                continue
            w = weights[name]
            aligned = fv.reindex(common_index)
            ranked = aligned.rank(pct=True)
            combined_score += ranked * w
            total_weight += abs(w)

        if total_weight > 0:
            combined_score = combined_score / total_weight

        aligned_returns = returns.reindex(common_index)

        try:
            quantile_labels = pd.qcut(
                combined_score, quantile, labels=False, duplicates="drop"
            )
        except ValueError:
            return self._empty_result("")

        if quantile_labels is None or quantile_labels.empty:
            return self._empty_result("")

        quantile_returns = aligned_returns.groupby(quantile_labels).mean()

        if len(quantile_returns) < 2:
            return self._empty_result("")

        long_quantile = quantile_returns.idxmax()
        short_quantile = quantile_returns.idxmin()

        long_mask = quantile_labels == long_quantile
        short_mask = quantile_labels == short_quantile

        long_returns = aligned_returns[long_mask]
        short_returns = aligned_returns[short_mask]

        strategy_returns = long_returns - short_returns.mean()

        equity = np.cumprod(1 + strategy_returns.values / 100)
        total_return = float(equity[-1] - 1)

        n_periods = len(strategy_returns)
        annualized_return = float((1 + total_return) ** (252 / max(n_periods, 1)) - 1)

        returns_std = float(strategy_returns.std())
        sharpe_ratio = (
            float(strategy_returns.mean() / returns_std * np.sqrt(252))
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

        ic_series = combined_score.rolling(20).corr(aligned_returns)
        ic_series = ic_series.dropna()
        ic_mean = float(ic_series.mean()) if len(ic_series) > 0 else 0.0
        ic_std = float(ic_series.std()) if len(ic_series) > 0 else 0.0
        ic_ir = ic_mean / ic_std if ic_std > 0 else 0.0

        factor_names = "_".join(sorted(factors.keys()))

        return FactorBacktestResult(
            factor_name=factor_names,
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
