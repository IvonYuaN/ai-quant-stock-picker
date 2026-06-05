"""
策略健康度监控系统。

实时监控每个策略的表现，自动检测失效并降权/停用。
提供策略的胜率、夏普比率、最大回撤等关键指标。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, timedelta
from enum import Enum
from typing import Optional

import numpy as np


class HealthStatus(Enum):
    """策略健康状态"""
    HEALTHY = "healthy"        # 正常
    WARNING = "warning"        # 预警，降权50%
    UNHEALTHY = "unhealthy"    # 不健康，停用


@dataclass
class Trade:
    """交易记录"""
    symbol: str
    strategy: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    shares: int
    pnl: float
    return_pct: float


@dataclass
class StrategyMetrics:
    """策略指标"""
    name: str
    total_trades: int
    winning_trades: int
    win_rate: float
    sharpe_ratio: float
    max_drawdown: float
    avg_return: float
    last_updated: datetime


class StrategyHealthMonitor:
    """策略健康度监控"""

    def __init__(self) -> None:
        """初始化监控器"""
        self._strategy_cache: dict[str, list[Trade]] = {}
        self._health_cache: dict[str, HealthStatus] = {}

    def check_strategy_health(
        self,
        strategy_name: str,
        recent_trades: list[Trade],
        lookback_days: int = 30,
    ) -> HealthStatus:
        """
        检查策略健康度。

        判断标准：
        - 胜率 < 40%: UNHEALTHY（停用）
        - 胜率 < 45%: WARNING（降权50%）
        - Sharpe < 0: UNHEALTHY
        - Sharpe < 0.5: WARNING
        - 连续亏损 > 5次: WARNING

        Args:
            strategy_name: 策略名称
            recent_trades: 近期交易列表
            lookback_days: 回溯天数，默认30天

        Returns:
            HealthStatus: 策略健康状态
        """
        if not recent_trades:
            # 没有交易数据，认为不健康
            return HealthStatus.UNHEALTHY

        # 筛选回溯期内的交易
        cutoff_date = (datetime.now() - timedelta(days=lookback_days)).date()
        filtered_trades = [
            t for t in recent_trades
            if t.exit_date >= cutoff_date
        ]

        if not filtered_trades:
            # 回溯期内没有交易
            return HealthStatus.UNHEALTHY

        # 计算关键指标
        win_rate = self._calculate_win_rate(filtered_trades)
        sharpe_ratio = self._calculate_sharpe_ratio(filtered_trades)
        consecutive_losses = self._count_consecutive_losses(filtered_trades)

        # 综合判断
        unhealthy_count = 0
        warning_count = 0

        # 胜率判断
        if win_rate < 0.40:
            unhealthy_count += 1
        elif win_rate < 0.45:
            warning_count += 1

        # Sharpe比率判断
        if sharpe_ratio < 0:
            unhealthy_count += 1
        elif sharpe_ratio < 0.5:
            warning_count += 1

        # 连续亏损判断
        if consecutive_losses > 5:
            warning_count += 1

        # 最终状态判断
        if unhealthy_count > 0:
            return HealthStatus.UNHEALTHY
        elif warning_count > 0:
            return HealthStatus.WARNING
        else:
            return HealthStatus.HEALTHY

    def get_strategy_metrics(
        self,
        strategy_name: str,
        recent_trades: list[Trade],
        lookback_days: int = 30,
    ) -> StrategyMetrics:
        """
        获取策略的详细指标。

        Args:
            strategy_name: 策略名称
            recent_trades: 近期交易列表
            lookback_days: 回溯天数

        Returns:
            StrategyMetrics: 策略指标对象
        """
        if not recent_trades:
            return StrategyMetrics(
                name=strategy_name,
                total_trades=0,
                winning_trades=0,
                win_rate=0.0,
                sharpe_ratio=0.0,
                max_drawdown=0.0,
                avg_return=0.0,
                last_updated=datetime.now(),
            )

        # 筛选回溯期内的交易
        cutoff_date = (datetime.now() - timedelta(days=lookback_days)).date()
        filtered_trades = [
            t for t in recent_trades
            if t.exit_date >= cutoff_date
        ]

        if not filtered_trades:
            return StrategyMetrics(
                name=strategy_name,
                total_trades=0,
                winning_trades=0,
                win_rate=0.0,
                sharpe_ratio=0.0,
                max_drawdown=0.0,
                avg_return=0.0,
                last_updated=datetime.now(),
            )

        total_trades = len(filtered_trades)
        winning_trades = sum(1 for t in filtered_trades if t.pnl > 0)
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        sharpe_ratio = self._calculate_sharpe_ratio(filtered_trades)
        max_drawdown = self._calculate_max_drawdown(filtered_trades)
        avg_return = np.mean([t.return_pct for t in filtered_trades])

        return StrategyMetrics(
            name=strategy_name,
            total_trades=total_trades,
            winning_trades=winning_trades,
            win_rate=win_rate,
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            avg_return=avg_return,
            last_updated=datetime.now(),
        )

    def get_all_strategies_status(
        self,
        strategies_trades: dict[str, list[Trade]],
        lookback_days: int = 30,
    ) -> dict[str, HealthStatus]:
        """
        获取所有策略的状态。

        Args:
            strategies_trades: 策略名称到交易列表的映射
            lookback_days: 回溯天数

        Returns:
            dict: 策略名称到健康状态的映射
        """
        statuses = {}
        for strategy_name, trades in strategies_trades.items():
            status = self.check_strategy_health(
                strategy_name,
                trades,
                lookback_days=lookback_days,
            )
            statuses[strategy_name] = status
            self._health_cache[strategy_name] = status

        return statuses

    def auto_adjust_weights(
        self,
        current_weights: dict[str, float],
        strategies_trades: dict[str, list[Trade]] | None = None,
        lookback_days: int = 30,
    ) -> dict[str, float]:
        """
        根据健康度自动调整权重。

        规则：
        - HEALTHY: 保持原权重
        - WARNING: 权重降低50%
        - UNHEALTHY: 权重设为0

        Args:
            current_weights: 当前权重映射
            strategies_trades: 策略交易数据（用于动态计算状态）
            lookback_days: 回溯天数

        Returns:
            dict: 调整后的权重映射
        """
        adjusted_weights = {}

        for strategy_name, weight in current_weights.items():
            # 获取策略状态
            if strategies_trades:
                status = self.check_strategy_health(
                    strategy_name,
                    strategies_trades.get(strategy_name, []),
                    lookback_days=lookback_days,
                )
            else:
                status = self._health_cache.get(strategy_name, HealthStatus.HEALTHY)

            # 根据状态调整权重
            if status == HealthStatus.UNHEALTHY:
                adjusted_weights[strategy_name] = 0.0
            elif status == HealthStatus.WARNING:
                adjusted_weights[strategy_name] = weight * 0.5
            else:
                adjusted_weights[strategy_name] = weight

        # 归一化权重，确保总和为1
        total_weight = sum(adjusted_weights.values())
        if total_weight > 0:
            adjusted_weights = {
                k: v / total_weight for k, v in adjusted_weights.items()
            }

        return adjusted_weights

    def _calculate_win_rate(self, trades: list[Trade]) -> float:
        """
        计算胜率。

        Args:
            trades: 交易列表

        Returns:
            float: 胜率（0-1）
        """
        if not trades:
            return 0.0
        winning = sum(1 for t in trades if t.pnl > 0)
        return winning / len(trades)

    def _calculate_sharpe_ratio(
        self,
        trades: list[Trade],
        risk_free_rate: float = 0.02,
    ) -> float:
        """
        计算夏普比率。

        公式: Sharpe = (mean_return - risk_free_rate) / std_return * sqrt(252)

        Args:
            trades: 交易列表
            risk_free_rate: 无风险利率（年化），默认2%

        Returns:
            float: 夏普比率
        """
        if len(trades) < 2:
            return 0.0

        returns = np.array([t.return_pct for t in trades])
        mean_return = np.mean(returns)
        std_return = np.std(returns)

        if std_return == 0:
            return 0.0

        # 年化处理（252个交易日）
        sharpe = (mean_return - risk_free_rate / 252) / std_return * np.sqrt(252)
        return float(sharpe)

    def _calculate_max_drawdown(self, trades: list[Trade]) -> float:
        """
        计算最大回撤。

        Args:
            trades: 交易列表

        Returns:
            float: 最大回撤（负数）
        """
        if not trades:
            return 0.0

        # 按交易日期排序
        sorted_trades = sorted(trades, key=lambda t: t.exit_date)

        # 累积PnL计算
        cumulative_pnl = 0.0
        peak_pnl = 0.0
        max_drawdown = 0.0

        for trade in sorted_trades:
            cumulative_pnl += trade.pnl
            if cumulative_pnl > peak_pnl:
                peak_pnl = cumulative_pnl
            drawdown = (cumulative_pnl - peak_pnl) / peak_pnl if peak_pnl != 0 else 0
            max_drawdown = min(max_drawdown, drawdown)

        return float(max_drawdown)

    def _count_consecutive_losses(self, trades: list[Trade]) -> int:
        """
        计算最长连续亏损次数。

        Args:
            trades: 交易列表（按时间序列排序）

        Returns:
            int: 连续亏损次数
        """
        if not trades:
            return 0

        # 按交易日期排序
        sorted_trades = sorted(trades, key=lambda t: t.exit_date)

        max_consecutive = 0
        current_consecutive = 0

        for trade in sorted_trades:
            if trade.pnl < 0:
                current_consecutive += 1
                max_consecutive = max(max_consecutive, current_consecutive)
            else:
                current_consecutive = 0

        return max_consecutive
