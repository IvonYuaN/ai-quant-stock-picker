"""
TWAP大单拆分算法的单元测试

测试范围：
- 基本拆单功能
- 单笔限制（参与率）
- 时间间隔计算
- 手数取整（100的倍数）
- 极小订单（不需要拆）
- 极大订单
- 边界情况
- 参数验证
- 计划验证
"""

from __future__ import annotations

import pytest
from aqsp.execution.twap import TWAPExecutor, Order, TWAPPlan


class TestBasicOrderSplit:
    """测试基本的拆单功能"""

    def test_basic_split_medium_order(self):
        """测试中等规模订单的拆分"""
        executor = TWAPExecutor()
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            time_window_minutes=30,
            max_participation_rate=0.01
        )

        # 验证拆单结果
        assert isinstance(plan, TWAPPlan)
        assert plan.total_shares == 10000
        assert len(plan.orders) > 0
        assert all(isinstance(order, Order) for order in plan.orders)

        # 验证订单总和
        total_shares = sum(order.shares for order in plan.orders)
        assert total_shares == 10000

    def test_basic_split_returns_correct_structure(self):
        """测试返回的结构是否正确"""
        executor = TWAPExecutor()
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=5000,
            avg_daily_volume=500000,
            time_window_minutes=20
        )

        # 验证TWAPPlan的所有字段
        assert plan.total_shares == 5000
        assert isinstance(plan.orders, list)
        assert isinstance(plan.interval_seconds, int)
        assert isinstance(plan.estimated_duration_minutes, int)
        assert plan.interval_seconds >= 60  # 至少60秒


class TestParticipationRateLimit:
    """测试单笔参与率限制"""

    def test_max_single_order_respects_participation_rate(self):
        """测试单笔订单不超过参与率限制"""
        executor = TWAPExecutor()
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=100000,
            avg_daily_volume=1000000,
            max_participation_rate=0.01  # 1%
        )

        # 日均成交量的1% = 10000股
        max_allowed = int(1000000 * 0.01)
        # 对齐到100的倍数
        max_allowed = (max_allowed // 100) * 100  # 10000

        for order in plan.orders:
            # 最后一笔可能会少一些，但每笔都应该不超过最大值
            assert order.shares <= max_allowed + 100, \
                f"Order {order.shares} exceeds max {max_allowed}"

    def test_participation_rate_calculation(self):
        """测试参与率计算函数"""
        executor = TWAPExecutor()

        # 10000股，日均100万股
        rate = executor.calculate_participation_rate(10000, 1000000)
        assert rate == 1.0  # 1%

        # 5000股，日均100万股
        rate = executor.calculate_participation_rate(5000, 1000000)
        assert rate == 0.5  # 0.5%

    def test_lower_participation_rate_means_more_slices(self):
        """测试更低的参与率会导致更多的拆分笔数"""
        executor = TWAPExecutor()

        # 1%参与率
        plan_1pct = executor.split_order(
            symbol='000001.SZ',
            target_shares=100000,
            avg_daily_volume=1000000,
            max_participation_rate=0.01
        )

        # 0.5%参与率
        plan_05pct = executor.split_order(
            symbol='000001.SZ',
            target_shares=100000,
            avg_daily_volume=1000000,
            max_participation_rate=0.005
        )

        # 更低的参与率应该导致更多的拆分
        assert len(plan_05pct.orders) >= len(plan_1pct.orders)


class TestTimeIntervalCalculation:
    """测试时间间隔计算"""

    def test_interval_at_least_60_seconds(self):
        """测试最小间隔为60秒"""
        executor = TWAPExecutor()
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=1000,
            avg_daily_volume=1000000,
            time_window_minutes=5  # 仅5分钟时间窗口
        )

        # 间隔应该至少60秒
        assert plan.interval_seconds >= 60

    def test_interval_within_time_window(self):
        """测试间隔应该在时间窗口内合理分布"""
        executor = TWAPExecutor()
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            time_window_minutes=30
        )

        # 总执行时间应该接近时间窗口
        estimated_time = (len(plan.orders) - 1) * plan.interval_seconds / 60
        assert estimated_time <= 30 + 5  # 容差5分钟

    def test_estimated_duration_calculation(self):
        """测试预计执行时间的计算"""
        executor = TWAPExecutor()
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            time_window_minutes=30
        )

        # 预计时间 = (订单数 - 1) * 间隔秒 / 60
        expected_duration = (len(plan.orders) - 1) * plan.interval_seconds / 60
        assert abs(plan.estimated_duration_minutes - expected_duration) < 1


class TestSharesRounding:
    """测试手数取整（100的倍数）"""

    def test_all_orders_are_multiple_of_100(self):
        """测试所有订单都是100的倍数"""
        executor = TWAPExecutor()
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=15000,  # 不能均等分的数量
            avg_daily_volume=1000000
        )

        for order in plan.orders:
            assert order.shares % 100 == 0, \
                f"Order {order.shares} is not multiple of 100"

    def test_total_shares_preserved_after_rounding(self):
        """测试取整后总股数保持不变"""
        executor = TWAPExecutor()
        target = 17500

        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=target,
            avg_daily_volume=1000000
        )

        total = sum(order.shares for order in plan.orders)
        assert total == target

    def test_minimum_order_size_100_shares(self):
        """测试最小订单数为100股"""
        executor = TWAPExecutor()
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=500,  # 较小的订单
            avg_daily_volume=1000000,
            time_window_minutes=10
        )

        for order in plan.orders:
            assert order.shares >= 100, \
                f"Order {order.shares} is less than minimum 100"


class TestSmallOrderNoSplit:
    """测试极小订单（不需要拆分）"""

    def test_small_order_single_slice(self):
        """测试小于参与率限制的订单只拆成1笔"""
        executor = TWAPExecutor()

        # 1000股，参与率1%，日均成交量100万
        # 参与率 = 1000 / 1000000 = 0.1% < 1%，不需要拆
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=1000,
            avg_daily_volume=1000000,
            max_participation_rate=0.01
        )

        assert len(plan.orders) == 1
        assert plan.orders[0].shares == 1000

    def test_exact_participation_rate_single_slice(self):
        """测试恰好等于参与率限制的订单"""
        executor = TWAPExecutor()

        # 10000股 = 日均成交量 × 1%
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000,
            max_participation_rate=0.01
        )

        # 应该拆成1笔或2笔
        assert len(plan.orders) <= 2


class TestLargeOrderMultipleSlices:
    """测试极大订单"""

    def test_large_order_multiple_slices(self):
        """测试大订单被正确拆分成多笔"""
        executor = TWAPExecutor()

        # 1000万股，日均100万，参与率1%
        # 需要拆成至少100笔
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=10000000,
            avg_daily_volume=1000000,
            max_participation_rate=0.01,
            time_window_minutes=480  # 8小时时间窗口
        )

        assert len(plan.orders) > 1
        total = sum(order.shares for order in plan.orders)
        assert total == 10000000

    def test_very_large_order_respects_limits(self):
        """测试超大订单仍然遵守所有限制"""
        executor = TWAPExecutor()

        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=50000000,  # 5千万股
            avg_daily_volume=10000000,  # 日均1千万
            max_participation_rate=0.01,
            time_window_minutes=480
        )

        # 每笔最大应为 1000万 × 1% = 10万股（取100倍）
        max_per_order = 100000

        for order in plan.orders:
            assert order.shares <= max_per_order + 100
            assert order.shares % 100 == 0


class TestEdgeCases:
    """测试边界情况"""

    def test_minimum_valid_order_100_shares(self):
        """测试最小有效订单100股"""
        executor = TWAPExecutor()
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=100,
            avg_daily_volume=1000000
        )

        assert len(plan.orders) == 1
        assert plan.orders[0].shares == 100

    def test_order_with_price_limit(self):
        """测试带限价的订单"""
        executor = TWAPExecutor()
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=5000,
            avg_daily_volume=1000000,
            price_limit=15.5
        )

        # 所有订单应该带上同样的限价
        for order in plan.orders:
            assert order.price_limit == 15.5

    def test_various_time_windows(self):
        """测试不同的时间窗口"""
        executor = TWAPExecutor()

        for window_minutes in [5, 15, 30, 60, 120]:
            plan = executor.split_order(
                symbol='000001.SZ',
                target_shares=10000,
                avg_daily_volume=1000000,
                time_window_minutes=window_minutes
            )

            # 应该成功生成计划
            assert plan.total_shares == 10000
            assert plan.interval_seconds >= 60


class TestParameterValidation:
    """测试参数验证"""

    def test_invalid_symbol_empty_string(self):
        """测试空symbol抛出异常"""
        executor = TWAPExecutor()
        with pytest.raises(ValueError, match="symbol必须是非空字符串"):
            executor.split_order(
                symbol='',
                target_shares=10000,
                avg_daily_volume=1000000
            )

    def test_invalid_symbol_none(self):
        """测试None symbol抛出异常"""
        executor = TWAPExecutor()
        with pytest.raises(ValueError, match="symbol必须是非空字符串"):
            executor.split_order(
                symbol=None,
                target_shares=10000,
                avg_daily_volume=1000000
            )

    def test_invalid_shares_not_integer(self):
        """测试非整数shares抛出异常"""
        executor = TWAPExecutor()
        with pytest.raises(ValueError, match="target_shares必须是整数"):
            executor.split_order(
                symbol='000001.SZ',
                target_shares=10000.5,
                avg_daily_volume=1000000
            )

    def test_invalid_shares_not_multiple_of_100(self):
        """测试shares不是100倍数时抛出异常"""
        executor = TWAPExecutor()
        with pytest.raises(ValueError, match="target_shares必须是100的倍数"):
            executor.split_order(
                symbol='000001.SZ',
                target_shares=10050,  # 不是100的倍数
                avg_daily_volume=1000000
            )

    def test_invalid_shares_zero(self):
        """测试零shares抛出异常"""
        executor = TWAPExecutor()
        with pytest.raises(ValueError, match="target_shares必须大于0"):
            executor.split_order(
                symbol='000001.SZ',
                target_shares=0,
                avg_daily_volume=1000000
            )

    def test_invalid_shares_negative(self):
        """测试负数shares抛出异常"""
        executor = TWAPExecutor()
        with pytest.raises(ValueError, match="target_shares必须大于0"):
            executor.split_order(
                symbol='000001.SZ',
                target_shares=-1000,
                avg_daily_volume=1000000
            )

    def test_invalid_avg_daily_volume_zero(self):
        """测试零日均成交量抛出异常"""
        executor = TWAPExecutor()
        with pytest.raises(ValueError, match="avg_daily_volume必须大于0"):
            executor.split_order(
                symbol='000001.SZ',
                target_shares=10000,
                avg_daily_volume=0
            )

    def test_invalid_avg_daily_volume_negative(self):
        """测试负数日均成交量抛出异常"""
        executor = TWAPExecutor()
        with pytest.raises(ValueError, match="avg_daily_volume必须大于0"):
            executor.split_order(
                symbol='000001.SZ',
                target_shares=10000,
                avg_daily_volume=-1000000
            )

    def test_invalid_time_window_zero(self):
        """测试零时间窗口抛出异常"""
        executor = TWAPExecutor()
        with pytest.raises(ValueError, match="time_window_minutes必须大于0"):
            executor.split_order(
                symbol='000001.SZ',
                target_shares=10000,
                avg_daily_volume=1000000,
                time_window_minutes=0
            )

    def test_invalid_time_window_negative(self):
        """测试负数时间窗口抛出异常"""
        executor = TWAPExecutor()
        with pytest.raises(ValueError, match="time_window_minutes必须大于0"):
            executor.split_order(
                symbol='000001.SZ',
                target_shares=10000,
                avg_daily_volume=1000000,
                time_window_minutes=-30
            )

    def test_invalid_participation_rate_zero(self):
        """测试零参与率抛出异常"""
        executor = TWAPExecutor()
        with pytest.raises(ValueError, match="max_participation_rate必须在"):
            executor.split_order(
                symbol='000001.SZ',
                target_shares=10000,
                avg_daily_volume=1000000,
                max_participation_rate=0
            )

    def test_invalid_participation_rate_above_one(self):
        """测试超过1的参与率抛出异常"""
        executor = TWAPExecutor()
        with pytest.raises(ValueError, match="max_participation_rate必须在"):
            executor.split_order(
                symbol='000001.SZ',
                target_shares=10000,
                avg_daily_volume=1000000,
                max_participation_rate=1.5
            )

    def test_valid_participation_rate_exactly_one(self):
        """测试参与率恰好为1是有效的"""
        executor = TWAPExecutor()
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=10000,
            max_participation_rate=1.0
        )
        assert plan.total_shares == 10000


class TestPlanValidation:
    """测试计划验证功能"""

    def test_validate_valid_plan(self):
        """测试验证有效的计划"""
        executor = TWAPExecutor()
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000
        )

        validation = executor.validate_twap_plan(plan, 1000000, 0.01)
        assert validation["valid"] is True
        assert len(validation["errors"]) == 0

    def test_validate_plan_checks_total_shares(self):
        """测试验证检查总股数一致性"""
        executor = TWAPExecutor()
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000
        )

        # 手动修改orders，制造不一致
        plan.total_shares = 9000

        validation = executor.validate_twap_plan(plan, 1000000, 0.01)
        assert validation["valid"] is False
        assert len(validation["errors"]) > 0

    def test_validate_plan_checks_shares_multiple_of_100(self):
        """测试验证检查每笔是否100倍数"""
        executor = TWAPExecutor()
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=10000,
            avg_daily_volume=1000000
        )

        # 手动修改第一笔订单为非100倍数
        plan.orders[0].shares = 5050

        validation = executor.validate_twap_plan(plan, 1000000, 0.01)
        # 总股数也会不一致
        assert validation["valid"] is False


class TestRealWorldScenarios:
    """测试现实场景"""

    def test_typical_a_share_order(self):
        """测试典型A股订单"""
        executor = TWAPExecutor()

        # 典型场景：10万股，日均成交量500万，30分钟内分散
        plan = executor.split_order(
            symbol='000001.SZ',
            target_shares=100000,
            avg_daily_volume=5000000,
            time_window_minutes=30,
            max_participation_rate=0.01
        )

        assert len(plan.orders) > 0
        assert sum(order.shares for order in plan.orders) == 100000

    def test_large_cap_blue_chip(self):
        """测试大市值蓝筹股"""
        executor = TWAPExecutor()

        # 大市值股票，日均成交量大
        plan = executor.split_order(
            symbol='600000.SH',
            target_shares=50000,
            avg_daily_volume=20000000,  # 日均2000万
            time_window_minutes=60,
            max_participation_rate=0.005  # 更严格的参与率
        )

        assert plan.total_shares == 50000
        assert all(o.shares % 100 == 0 for o in plan.orders)

    def test_mid_cap_stock(self):
        """测试中等市值股票"""
        executor = TWAPExecutor()

        # 中等市值股票
        plan = executor.split_order(
            symbol='002000.SZ',
            target_shares=200000,
            avg_daily_volume=2000000,
            time_window_minutes=120,
            max_participation_rate=0.01
        )

        # 应该拆成多笔
        assert len(plan.orders) > 1
        total = sum(o.shares for o in plan.orders)
        assert total == 200000

    def test_small_cap_illiquid_stock(self):
        """测试小市值流动性较差的股票"""
        executor = TWAPExecutor()

        # 小市值股票，成交量不大
        plan = executor.split_order(
            symbol='002500.SZ',
            target_shares=100000,
            avg_daily_volume=500000,  # 日均50万
            time_window_minutes=240,  # 4小时
            max_participation_rate=0.005  # 0.5%
        )

        # 由于流动性差，应该拆成更多笔
        assert len(plan.orders) >= 10
        assert plan.estimated_duration_minutes <= 240
