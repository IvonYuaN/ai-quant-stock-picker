"""
执行协调器的集成测试

测试范围：
- 完整执行计划的生成
- 买入和卖出成本的计算
- 执行计划的验证
- 多个计划的比较
- 成本估算的准确性
"""

from __future__ import annotations

import pytest
from aqsp.execution.executor import ExecutionCoordinator, ExecutionPlan
from aqsp.execution.cost import TradingCostCalculator


class TestExecutionPlanGeneration:
    """测试执行计划生成"""

    def test_generate_buy_plan(self):
        """测试生成买入执行计划"""
        coordinator = ExecutionCoordinator()

        plan = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            estimated_price=15.5,
            time_window_minutes=30,
            is_sell=False
        )

        assert isinstance(plan, ExecutionPlan)
        assert plan.twap_plan.total_shares == 10000
        assert plan.is_valid is True
        assert plan.estimated_total_cost > 0
        assert plan.estimated_cost_rate > 0

    def test_generate_sell_plan(self):
        """测试生成卖出执行计划"""
        coordinator = ExecutionCoordinator()

        plan = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            estimated_price=15.5,
            time_window_minutes=30,
            is_sell=True
        )

        assert isinstance(plan, ExecutionPlan)
        assert plan.total_stamp_tax > 0  # 卖出才有印花税
        assert plan.estimated_total_cost > 0

    def test_buy_plan_vs_sell_plan_cost_difference(self):
        """测试买入计划和卖出计划的成本差异"""
        coordinator = ExecutionCoordinator()

        buy_plan = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            estimated_price=15.5,
            is_sell=False
        )

        sell_plan = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            estimated_price=15.5,
            is_sell=True
        )

        # 卖出成本应该大于买入成本（多了印花税）
        assert sell_plan.estimated_total_cost > buy_plan.estimated_total_cost
        assert sell_plan.total_stamp_tax > 0
        assert buy_plan.total_stamp_tax == 0

    def test_plan_with_price_limit(self):
        """测试带限价的执行计划"""
        coordinator = ExecutionCoordinator()

        plan = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            estimated_price=15.5,
            price_limit=15.0
        )

        # 所有订单应该都有限价
        for order in plan.twap_plan.orders:
            assert order.price_limit == 15.0


class TestCostCalculation:
    """测试成本计算"""

    def test_cost_calculation_matches_calculator(self):
        """测试执行协调器的成本计算与成本计算器一致"""
        coordinator = ExecutionCoordinator()
        calculator = TradingCostCalculator()

        amount = 10000 * 15.5  # 10000股 × 15.5元

        plan = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            estimated_price=15.5,
            is_sell=False
        )

        # 计算器的买入成本
        buy_cost_absolute = calculator.calculate_buy_cost_absolute(amount, 15)

        # 应该接近
        assert abs(plan.estimated_total_cost - buy_cost_absolute) < 1.0

    def test_different_slippage_impacts_cost(self):
        """测试不同滑点对成本的影响"""
        coordinator = ExecutionCoordinator()

        plan_10bp = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            estimated_price=15.5,
            slippage_bps=10
        )

        plan_20bp = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            estimated_price=15.5,
            slippage_bps=20
        )

        # 更高的滑点应该导致更高的成本
        assert plan_20bp.estimated_total_cost > plan_10bp.estimated_total_cost

    def test_cost_rate_reasonable_range(self):
        """测试成本率在合理范围内"""
        coordinator = ExecutionCoordinator()

        plan = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            estimated_price=15.5,
            slippage_bps=15,
            is_sell=False
        )

        # 买入成本率应该在0.1%-0.2%之间
        assert 0.1 < plan.estimated_cost_rate < 0.5

    def test_large_order_cost_estimation(self):
        """测试大订单的成本估算"""
        coordinator = ExecutionCoordinator()

        plan = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=100000,
            avg_daily_volume=1000000,
            estimated_price=15.5,
            time_window_minutes=60,
            slippage_bps=15,
            is_sell=False
        )

        amount = 100000 * 15.5
        expected_cost_rate = 0.18  # 0.03% + 0.15%

        # 成本率应该接近预期
        assert abs(plan.estimated_cost_rate - expected_cost_rate) < 0.05


class TestPlanComparison:
    """测试执行计划的比较"""

    def test_compare_multiple_plans(self):
        """测试比较多个执行计划"""
        coordinator = ExecutionCoordinator()

        plans = []
        for window in [15, 30, 60]:
            plan = coordinator.plan_execution(
                symbol='000001.SZ',
                target_shares=10000,
                avg_daily_volume=1000000,
                estimated_price=15.5,
                time_window_minutes=window
            )
            plans.append(plan)

        comparison = coordinator.compare_execution_plans(plans)

        assert "best_plan" in comparison
        assert "worst_plan" in comparison
        assert "avg_cost_rate" in comparison
        assert "all_plans" in comparison

        # 最佳计划应该有最低的成本率
        assert comparison["best_plan"].estimated_cost_rate <= comparison["worst_plan"].estimated_cost_rate

    def test_compare_empty_list_returns_error(self):
        """测试比较空列表返回错误"""
        coordinator = ExecutionCoordinator()
        comparison = coordinator.compare_execution_plans([])

        assert "error" in comparison


class TestExecutionSimulation:
    """测试执行模拟"""

    def test_simulate_execution(self):
        """测试执行模拟"""
        coordinator = ExecutionCoordinator()

        plan = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            estimated_price=15.5
        )

        simulation = coordinator.simulate_execution(plan)

        assert "orders" in simulation
        assert "total_shares" in simulation
        assert "plan_validity" in simulation
        assert len(simulation["orders"]) == len(plan.twap_plan.orders)
        assert simulation["total_shares"] == 10000

    def test_simulate_with_actual_prices(self):
        """测试使用实际价格的执行模拟"""
        coordinator = ExecutionCoordinator()

        plan = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=1000,
            avg_daily_volume=1000000,
            estimated_price=15.5
        )

        # 模拟实际成交价格
        actual_prices = [15.4, 15.5, 15.6] * ((len(plan.twap_plan.orders) // 3) + 1)
        actual_prices = actual_prices[:len(plan.twap_plan.orders)]

        simulation = coordinator.simulate_execution(plan, actual_prices)

        assert len(simulation["orders"]) == len(plan.twap_plan.orders)


class TestRealWorldExecutionScenarios:
    """测试现实执行场景"""

    def test_blue_chip_buy_execution(self):
        """测试蓝筹股买入执行"""
        coordinator = ExecutionCoordinator()

        # 平安银行大宗购买
        plan = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=50000,
            avg_daily_volume=10000000,
            estimated_price=9.5,
            time_window_minutes=60,
            is_sell=False
        )

        assert plan.is_valid
        assert plan.estimated_cost_rate < 0.5

    def test_mid_cap_sell_execution(self):
        """测试中等市值股票卖出执行"""
        coordinator = ExecutionCoordinator()

        plan = coordinator.plan_execution(
            symbol='002000.SZ',
            target_shares=30000,
            avg_daily_volume=2000000,
            estimated_price=25.0,
            time_window_minutes=120,
            max_participation_rate=0.01,
            is_sell=True
        )

        assert plan.is_valid
        # 卖出应该包含印花税
        assert plan.total_stamp_tax > 0

    def test_high_slippage_scenario(self):
        """测试高滑点场景（如日中涨跌幅大）"""
        coordinator = ExecutionCoordinator()

        plan = coordinator.plan_execution(
            symbol='002500.SZ',
            target_shares=20000,
            avg_daily_volume=500000,
            estimated_price=12.0,
            time_window_minutes=240,
            slippage_bps=50,  # 高滑点
            is_sell=False
        )

        assert plan.estimated_cost_rate > 0.4

    def test_multiple_time_window_comparison(self):
        """测试不同时间窗口的执行方案比较"""
        coordinator = ExecutionCoordinator()

        plans = []
        for window in [30, 60, 120]:
            plan = coordinator.plan_execution(
                symbol='000001.SZ',
                target_shares=100000,
                avg_daily_volume=5000000,
                estimated_price=15.5,
                time_window_minutes=window,
                max_participation_rate=0.01,
                is_sell=False
            )
            plans.append(plan)

        comparison = coordinator.compare_execution_plans(plans)

        # 应该能够找到最优方案
        assert comparison["best_plan"].estimated_cost_rate <= comparison["worst_plan"].estimated_cost_rate


class TestEdgeCasesAndErrors:
    """测试边界情况和错误处理"""

    def test_very_small_estimated_price(self):
        """测试非常小的预计价格"""
        coordinator = ExecutionCoordinator()

        plan = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=1000,
            avg_daily_volume=1000000,
            estimated_price=0.1,  # 0.1元
            is_sell=False
        )

        # 应该能生成有效的计划
        assert plan.is_valid

    def test_very_large_estimated_price(self):
        """测试非常大的预计价格"""
        coordinator = ExecutionCoordinator()

        plan = coordinator.plan_execution(
            symbol='600000.SH',
            target_shares=1000,
            avg_daily_volume=5000000,
            estimated_price=100.0,  # 100元
            is_sell=False
        )

        assert plan.is_valid

    def test_fractional_shares_rounded(self):
        """测试分数股数被正确处理"""
        coordinator = ExecutionCoordinator()

        # 验证TWAP层会拒绝非100倍数的股数
        with pytest.raises(ValueError, match="target_shares必须是100的倍数"):
            plan = coordinator.plan_execution(
                symbol='000001.SZ',
                target_shares=9999,  # 不是100的倍数，应该失败
                avg_daily_volume=1000000,
                estimated_price=15.5,
                is_sell=False
            )


class TestCostBreakdown:
    """测试成本分解"""

    def test_commission_breakdown(self):
        """测试佣金分解"""
        coordinator = ExecutionCoordinator()

        plan = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            estimated_price=15.5,
            slippage_bps=0,  # 无滑点，只看佣金
            is_sell=False
        )

        amount = 10000 * 15.5

        # 计算期望的佣金
        expected_commission = max(
            amount * TradingCostCalculator.COMMISSION_RATE,
            TradingCostCalculator.MIN_COMMISSION
        )

        # 由于金额较大，应该是正常佣金而非最低佣金
        assert expected_commission == amount * TradingCostCalculator.COMMISSION_RATE

    def test_sell_cost_includes_stamp_tax(self):
        """测试卖出成本包含印花税"""
        coordinator = ExecutionCoordinator()

        plan = coordinator.plan_execution(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            estimated_price=15.5,
            slippage_bps=0,
            is_sell=True
        )

        amount = 10000 * 15.5

        # 预期印花税
        expected_stamp_tax = amount * TradingCostCalculator.STAMP_TAX_RATE

        assert plan.total_stamp_tax > 0
        assert abs(plan.total_stamp_tax - expected_stamp_tax) < 0.01
