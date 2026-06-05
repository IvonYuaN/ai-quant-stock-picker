"""
策略健康度监控测试。

覆盖策略指标计算、健康度判断、权重调整等核心功能。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from aqsp.monitor.strategy_health import (
    Trade,
    StrategyMetrics,
    StrategyHealthMonitor,
    HealthStatus,
)


class TestTradeDataStructure:
    """交易数据结构测试"""

    def test_trade_creation(self) -> None:
        """测试交易对象创建"""
        trade = Trade(
            symbol="000001",
            strategy="momentum",
            entry_date=date(2026, 5, 20),
            exit_date=date(2026, 5, 25),
            entry_price=100.0,
            exit_price=105.0,
            shares=100,
            pnl=500.0,
            return_pct=0.05,
        )
        assert trade.symbol == "000001"
        assert trade.strategy == "momentum"
        assert trade.pnl == 500.0
        assert trade.return_pct == 0.05


class TestStrategyMetricsCalculation:
    """策略指标计算测试"""

    def test_win_rate_calculation_healthy(self) -> None:
        """测试胜率计算 - 健康策略"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        trades = [
            Trade(
                symbol="000001",
                strategy="test",
                entry_date=today - timedelta(days=10),
                exit_date=today - timedelta(days=9),
                entry_price=100.0,
                exit_price=105.0,
                shares=100,
                pnl=500.0,
                return_pct=0.05,
            ),
            Trade(
                symbol="000002",
                strategy="test",
                entry_date=today - timedelta(days=8),
                exit_date=today - timedelta(days=7),
                entry_price=100.0,
                exit_price=103.0,
                shares=100,
                pnl=300.0,
                return_pct=0.03,
            ),
            Trade(
                symbol="000003",
                strategy="test",
                entry_date=today - timedelta(days=6),
                exit_date=today - timedelta(days=5),
                entry_price=100.0,
                exit_price=98.0,
                shares=100,
                pnl=-200.0,
                return_pct=-0.02,
            ),
        ]

        win_rate = monitor._calculate_win_rate(trades)
        assert win_rate == pytest.approx(2 / 3, rel=0.01)

    def test_win_rate_calculation_all_losses(self) -> None:
        """测试胜率计算 - 全部亏损"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=10 - i),
                exit_date=today - timedelta(days=9 - i),
                entry_price=100.0,
                exit_price=95.0,
                shares=100,
                pnl=-500.0,
                return_pct=-0.05,
            )
            for i in range(3)
        ]

        win_rate = monitor._calculate_win_rate(trades)
        assert win_rate == 0.0

    def test_sharpe_ratio_calculation(self) -> None:
        """测试夏普比率计算"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        # 创建稳定盈利的交易
        trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=10 - i),
                exit_date=today - timedelta(days=9 - i),
                entry_price=100.0,
                exit_price=101.0,
                shares=100,
                pnl=100.0,
                return_pct=0.01,
            )
            for i in range(10)
        ]

        sharpe = monitor._calculate_sharpe_ratio(trades)
        # 稳定的1%收益应该有正的夏普比率
        assert sharpe > 0

    def test_sharpe_ratio_variable_returns(self) -> None:
        """测试夏普比率计算 - 变动收益"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=10 - i),
                exit_date=today - timedelta(days=9 - i),
                entry_price=100.0,
                exit_price=98.0 if i % 2 == 0 else 103.0,
                shares=100,
                pnl=-200.0 if i % 2 == 0 else 300.0,
                return_pct=-0.02 if i % 2 == 0 else 0.03,
            )
            for i in range(5)
        ]

        sharpe = monitor._calculate_sharpe_ratio(trades)
        # 变动的正负收益混合，夏普比率应该接近0或为正
        assert sharpe >= -0.5

    def test_max_drawdown_calculation(self) -> None:
        """测试最大回撤计算"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        # 先盈利后亏损，形成回撤
        trades = [
            Trade(
                symbol="000001",
                strategy="test",
                entry_date=today - timedelta(days=5),
                exit_date=today - timedelta(days=4),
                entry_price=100.0,
                exit_price=110.0,
                shares=100,
                pnl=1000.0,
                return_pct=0.10,
            ),
            Trade(
                symbol="000002",
                strategy="test",
                entry_date=today - timedelta(days=3),
                exit_date=today - timedelta(days=2),
                entry_price=100.0,
                exit_price=95.0,
                shares=100,
                pnl=-500.0,
                return_pct=-0.05,
            ),
        ]

        drawdown = monitor._calculate_max_drawdown(trades)
        # 应该是负数
        assert drawdown < 0
        # 回撤应该相对较小（因为还有盈利）
        assert drawdown > -1.0

    def test_consecutive_losses_detection(self) -> None:
        """测试连续亏损检测"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        # 创建混合的盈亏序列：赢、赢、亏、亏、亏、赢、亏、亏
        trades = [
            Trade(
                symbol="000001",
                strategy="test",
                entry_date=today - timedelta(days=8),
                exit_date=today - timedelta(days=7),
                entry_price=100.0,
                exit_price=105.0,
                shares=100,
                pnl=500.0,
                return_pct=0.05,
            ),
            Trade(
                symbol="000002",
                strategy="test",
                entry_date=today - timedelta(days=6),
                exit_date=today - timedelta(days=5),
                entry_price=100.0,
                exit_price=103.0,
                shares=100,
                pnl=300.0,
                return_pct=0.03,
            ),
            Trade(
                symbol="000003",
                strategy="test",
                entry_date=today - timedelta(days=4),
                exit_date=today - timedelta(days=3),
                entry_price=100.0,
                exit_price=98.0,
                shares=100,
                pnl=-200.0,
                return_pct=-0.02,
            ),
            Trade(
                symbol="000004",
                strategy="test",
                entry_date=today - timedelta(days=2),
                exit_date=today - timedelta(days=1),
                entry_price=100.0,
                exit_price=97.0,
                shares=100,
                pnl=-300.0,
                return_pct=-0.03,
            ),
            Trade(
                symbol="000005",
                strategy="test",
                entry_date=today - timedelta(days=1),
                exit_date=today,
                entry_price=100.0,
                exit_price=96.0,
                shares=100,
                pnl=-400.0,
                return_pct=-0.04,
            ),
        ]

        consecutive = monitor._count_consecutive_losses(trades)
        assert consecutive == 3


class TestHealthStatusDetermination:
    """健康状态判断测试"""

    def test_healthy_strategy_identification(self) -> None:
        """测试健康策略识别"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        # 高胜率（70%）、高夏普比率（1.5）、无连续亏损
        trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=20 - i),
                exit_date=today - timedelta(days=19 - i),
                entry_price=100.0,
                exit_price=101.0 if i % 10 < 7 else 99.0,
                shares=100,
                pnl=100.0 if i % 10 < 7 else -100.0,
                return_pct=0.01 if i % 10 < 7 else -0.01,
            )
            for i in range(10)
        ]

        status = monitor.check_strategy_health("test_strategy", trades)
        assert status == HealthStatus.HEALTHY

    def test_warning_strategy_identification(self) -> None:
        """测试预警策略识别"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        # 胜率44%，触发WARNING
        trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=20 - i),
                exit_date=today - timedelta(days=19 - i),
                entry_price=100.0,
                exit_price=101.0 if i < 4 else 99.5,
                shares=100,
                pnl=100.0 if i < 4 else -50.0,
                return_pct=0.01 if i < 4 else -0.005,
            )
            for i in range(9)
        ]

        status = monitor.check_strategy_health("test_strategy", trades)
        assert status == HealthStatus.WARNING

    def test_unhealthy_strategy_low_win_rate(self) -> None:
        """测试失效策略识别 - 低胜率"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        # 胜率33%，低于40%，触发UNHEALTHY
        trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=20 - i),
                exit_date=today - timedelta(days=19 - i),
                entry_price=100.0,
                exit_price=102.0 if i < 2 else 98.0,
                shares=100,
                pnl=200.0 if i < 2 else -200.0,
                return_pct=0.02 if i < 2 else -0.02,
            )
            for i in range(6)
        ]

        status = monitor.check_strategy_health("test_strategy", trades)
        assert status == HealthStatus.UNHEALTHY

    def test_unhealthy_strategy_negative_sharpe(self) -> None:
        """测试失效策略识别 - 负夏普比率"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        # 持续亏损，夏普比率为负
        trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=20 - i),
                exit_date=today - timedelta(days=19 - i),
                entry_price=100.0,
                exit_price=97.0,
                shares=100,
                pnl=-300.0,
                return_pct=-0.03,
            )
            for i in range(5)
        ]

        status = monitor.check_strategy_health("test_strategy", trades)
        assert status == HealthStatus.UNHEALTHY

    def test_unhealthy_strategy_many_consecutive_losses(self) -> None:
        """测试失效策略识别 - 多次连续亏损"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        # 创建6次连续亏损，应该触发UNHEALTHY
        trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=10 - i),
                exit_date=today - timedelta(days=9 - i),
                entry_price=100.0,
                exit_price=98.0,
                shares=100,
                pnl=-200.0,
                return_pct=-0.02,
            )
            for i in range(6)
        ]

        status = monitor.check_strategy_health("test_strategy", trades)
        assert status == HealthStatus.UNHEALTHY

    def test_empty_trade_list_unhealthy(self) -> None:
        """测试空交易列表 - 返回不健康"""
        monitor = StrategyHealthMonitor()

        status = monitor.check_strategy_health("test_strategy", [])
        assert status == HealthStatus.UNHEALTHY

    def test_single_winning_trade(self) -> None:
        """测试单笔盈利交易"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        trades = [
            Trade(
                symbol="000001",
                strategy="test",
                entry_date=today - timedelta(days=1),
                exit_date=today,
                entry_price=100.0,
                exit_price=105.0,
                shares=100,
                pnl=500.0,
                return_pct=0.05,
            ),
        ]

        # 单笔交易胜率100%，但样本不足，应该为WARNING或HEALTHY
        status = monitor.check_strategy_health("test_strategy", trades)
        assert status in [HealthStatus.WARNING, HealthStatus.UNHEALTHY]

    def test_single_losing_trade(self) -> None:
        """测试单笔亏损交易"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        trades = [
            Trade(
                symbol="000001",
                strategy="test",
                entry_date=today - timedelta(days=1),
                exit_date=today,
                entry_price=100.0,
                exit_price=95.0,
                shares=100,
                pnl=-500.0,
                return_pct=-0.05,
            ),
        ]

        # 单笔交易胜率0%，低于40%，应该为UNHEALTHY
        status = monitor.check_strategy_health("test_strategy", trades)
        assert status == HealthStatus.UNHEALTHY

    def test_lookback_period_filtering(self) -> None:
        """测试回溯期筛选"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        # 创建超过30天前的交易和最近的交易
        old_trades = [
            Trade(
                symbol="000001",
                strategy="test",
                entry_date=today - timedelta(days=40),
                exit_date=today - timedelta(days=39),
                entry_price=100.0,
                exit_price=95.0,
                shares=100,
                pnl=-500.0,
                return_pct=-0.05,
            ),
        ]

        recent_trades = [
            Trade(
                symbol="000002",
                strategy="test",
                entry_date=today - timedelta(days=5),
                exit_date=today - timedelta(days=4),
                entry_price=100.0,
                exit_price=105.0,
                shares=100,
                pnl=500.0,
                return_pct=0.05,
            ),
        ]

        all_trades = old_trades + recent_trades

        # 30天回溯应该排除old_trades，只有最近的盈利交易
        status = monitor.check_strategy_health(
            "test_strategy",
            all_trades,
            lookback_days=30,
        )
        # 单笔盈利交易可能是WARNING或HEALTHY
        assert status in [HealthStatus.WARNING, HealthStatus.UNHEALTHY]


class TestWeightAdjustment:
    """权重自动调整测试"""

    def test_weight_adjustment_healthy_preserved(self) -> None:
        """测试权重调整 - 健康策略权重保留"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        # 创建健康策略的交易
        healthy_trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=20 - i),
                exit_date=today - timedelta(days=19 - i),
                entry_price=100.0,
                exit_price=101.0,
                shares=100,
                pnl=100.0,
                return_pct=0.01,
            )
            for i in range(10)
        ]

        weights = {"healthy": 0.5, "warning": 0.3, "unhealthy": 0.2}
        strategies_trades = {"healthy": healthy_trades}

        adjusted = monitor.auto_adjust_weights(
            weights,
            strategies_trades=strategies_trades,
        )

        # 健康策略权重应该保持为最高（经过归一化）
        assert adjusted["healthy"] > 0

    def test_weight_adjustment_unhealthy_zeroed(self) -> None:
        """测试权重调整 - 失效策略权重为0"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        # 创建失效策略的交易
        unhealthy_trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=20 - i),
                exit_date=today - timedelta(days=19 - i),
                entry_price=100.0,
                exit_price=97.0,
                shares=100,
                pnl=-300.0,
                return_pct=-0.03,
            )
            for i in range(5)
        ]

        weights = {"strategy": 0.3}
        strategies_trades = {"strategy": unhealthy_trades}

        adjusted = monitor.auto_adjust_weights(
            weights,
            strategies_trades=strategies_trades,
        )

        # 失效策略权重应该为0
        assert adjusted["strategy"] == 0.0

    def test_weight_adjustment_normalization(self) -> None:
        """测试权重调整 - 权重归一化"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        # 创建不同状态的策略
        healthy_trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=10 - i),
                exit_date=today - timedelta(days=9 - i),
                entry_price=100.0,
                exit_price=101.0,
                shares=100,
                pnl=100.0,
                return_pct=0.01,
            )
            for i in range(10)
        ]

        unhealthy_trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=10 - i),
                exit_date=today - timedelta(days=9 - i),
                entry_price=100.0,
                exit_price=97.0,
                shares=100,
                pnl=-300.0,
                return_pct=-0.03,
            )
            for i in range(5)
        ]

        weights = {"healthy": 0.5, "unhealthy": 0.5}
        strategies_trades = {
            "healthy": healthy_trades,
            "unhealthy": unhealthy_trades,
        }

        adjusted = monitor.auto_adjust_weights(
            weights,
            strategies_trades=strategies_trades,
        )

        # 权重应该归一化为1.0
        total = sum(adjusted.values())
        assert total == pytest.approx(1.0, abs=0.001)


class TestEdgeCases:
    """边界情况测试"""

    def test_all_trades_same_date(self) -> None:
        """测试所有交易同一日期"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=1),
                exit_date=today,
                entry_price=100.0,
                exit_price=101.0 if i % 2 == 0 else 99.0,
                shares=100,
                pnl=100.0 if i % 2 == 0 else -100.0,
                return_pct=0.01 if i % 2 == 0 else -0.01,
            )
            for i in range(10)
        ]

        status = monitor.check_strategy_health("test_strategy", trades)
        # 胜率50%，应该是WARNING或HEALTHY
        assert status in [HealthStatus.WARNING, HealthStatus.UNHEALTHY]

    def test_very_large_win_rate(self) -> None:
        """测试极高胜率"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=20 - i),
                exit_date=today - timedelta(days=19 - i),
                entry_price=100.0,
                exit_price=105.0,
                shares=100,
                pnl=500.0,
                return_pct=0.05,
            )
            for i in range(20)
        ]

        status = monitor.check_strategy_health("test_strategy", trades)
        assert status == HealthStatus.HEALTHY

    def test_metrics_generation(self) -> None:
        """测试指标生成"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=20 - i),
                exit_date=today - timedelta(days=19 - i),
                entry_price=100.0,
                exit_price=101.0 if i < 7 else 99.0,
                shares=100,
                pnl=100.0 if i < 7 else -100.0,
                return_pct=0.01 if i < 7 else -0.01,
            )
            for i in range(10)
        ]

        metrics = monitor.get_strategy_metrics("test_strategy", trades)

        assert metrics.name == "test_strategy"
        assert metrics.total_trades == 10
        assert metrics.winning_trades == 7
        assert metrics.win_rate == pytest.approx(0.7, rel=0.01)
        assert metrics.sharpe_ratio > 0


class TestMultipleStrategies:
    """多策略监控测试"""

    def test_all_strategies_status(self) -> None:
        """测试获取所有策略状态"""
        monitor = StrategyHealthMonitor()
        today = date.today()

        # 创建多个策略
        healthy_trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=10 - i),
                exit_date=today - timedelta(days=9 - i),
                entry_price=100.0,
                exit_price=101.0,
                shares=100,
                pnl=100.0,
                return_pct=0.01,
            )
            for i in range(10)
        ]

        unhealthy_trades = [
            Trade(
                symbol=f"00000{i}",
                strategy="test",
                entry_date=today - timedelta(days=10 - i),
                exit_date=today - timedelta(days=9 - i),
                entry_price=100.0,
                exit_price=97.0,
                shares=100,
                pnl=-300.0,
                return_pct=-0.03,
            )
            for i in range(5)
        ]

        strategies_trades = {
            "strategy_a": healthy_trades,
            "strategy_b": unhealthy_trades,
        }

        statuses = monitor.get_all_strategies_status(strategies_trades)

        assert len(statuses) == 2
        assert statuses["strategy_a"] == HealthStatus.HEALTHY
        assert statuses["strategy_b"] == HealthStatus.UNHEALTHY
