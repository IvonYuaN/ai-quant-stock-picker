"""A股T+1交易制度约束处理测试"""

from __future__ import annotations

from datetime import date

import pytest

from aqsp.portfolio.position_tracker import (
    InsufficientSharesError,
    InvalidPriceError,
    NegativeSharesError,
    Position,
    PositionTracker,
)


class TestPosition:
    """Position 单元测试"""

    def test_position_creation_valid(self) -> None:
        """测试创建有效的持仓对象"""
        pos = Position(
            symbol="000001",
            total_shares=100,
            available_shares=100,
            cost_basis=10.0,
            last_buy_date=date(2026, 6, 5),
        )
        assert pos.symbol == "000001"
        assert pos.total_shares == 100
        assert pos.available_shares == 100
        assert pos.cost_basis == 10.0

    def test_position_frozen_shares_property(self) -> None:
        """测试冻结股数属性计算"""
        pos = Position(
            symbol="000001",
            total_shares=100,
            available_shares=60,
            cost_basis=10.0,
            last_buy_date=date(2026, 6, 5),
        )
        assert pos.frozen_shares == 40

    def test_position_is_fully_sellable_true(self) -> None:
        """测试全部可卖判断 - 为真"""
        pos = Position(
            symbol="000001",
            total_shares=100,
            available_shares=100,
            cost_basis=10.0,
            last_buy_date=date(2026, 6, 5),
        )
        assert pos.is_fully_sellable is True

    def test_position_is_fully_sellable_false(self) -> None:
        """测试全部可卖判断 - 为假"""
        pos = Position(
            symbol="000001",
            total_shares=100,
            available_shares=60,
            cost_basis=10.0,
            last_buy_date=date(2026, 6, 5),
        )
        assert pos.is_fully_sellable is False

    def test_position_invalid_negative_total_shares(self) -> None:
        """测试负数总持仓异常"""
        with pytest.raises(NegativeSharesError):
            Position(
                symbol="000001",
                total_shares=-100,
                available_shares=0,
                cost_basis=10.0,
                last_buy_date=date(2026, 6, 5),
            )

    def test_position_invalid_negative_available_shares(self) -> None:
        """测试负数可卖数量异常"""
        with pytest.raises(NegativeSharesError):
            Position(
                symbol="000001",
                total_shares=100,
                available_shares=-50,
                cost_basis=10.0,
                last_buy_date=date(2026, 6, 5),
            )

    def test_position_invalid_available_exceeds_total(self) -> None:
        """测试可卖数量超过总持仓异常"""
        with pytest.raises(ValueError):
            Position(
                symbol="000001",
                total_shares=100,
                available_shares=150,
                cost_basis=10.0,
                last_buy_date=date(2026, 6, 5),
            )

    def test_position_invalid_negative_cost_basis(self) -> None:
        """测试负数成本价异常"""
        with pytest.raises(InvalidPriceError):
            Position(
                symbol="000001",
                total_shares=100,
                available_shares=100,
                cost_basis=-10.0,
                last_buy_date=date(2026, 6, 5),
            )

    def test_position_add_shares_weighted_average_cost(self) -> None:
        """测试买入时加权平均成本价计算"""
        pos = Position(
            symbol="000001",
            total_shares=100,
            available_shares=100,
            cost_basis=10.0,
            last_buy_date=date(2026, 6, 5),
        )
        # 买入100股，价格11元
        pos.add_shares(100, 11.0, date(2026, 6, 6))
        # 加权平均：(100*10 + 100*11) / 200 = 10.5
        assert pos.cost_basis == 10.5
        assert pos.total_shares == 200
        # 新买的100股不立即加入可卖
        assert pos.available_shares == 100

    def test_position_add_shares_negative_raises_error(self) -> None:
        """测试买入负数股份异常"""
        pos = Position(
            symbol="000001",
            total_shares=100,
            available_shares=100,
            cost_basis=10.0,
            last_buy_date=date(2026, 6, 5),
        )
        with pytest.raises(NegativeSharesError):
            pos.add_shares(-50, 10.0, date(2026, 6, 6))

    def test_position_add_shares_invalid_price_raises_error(self) -> None:
        """测试买入无效价格异常"""
        pos = Position(
            symbol="000001",
            total_shares=100,
            available_shares=100,
            cost_basis=10.0,
            last_buy_date=date(2026, 6, 5),
        )
        with pytest.raises(InvalidPriceError):
            pos.add_shares(50, -10.0, date(2026, 6, 6))

    def test_position_remove_shares_success(self) -> None:
        """测试卖出股份成功"""
        pos = Position(
            symbol="000001",
            total_shares=100,
            available_shares=100,
            cost_basis=10.0,
            last_buy_date=date(2026, 6, 5),
        )
        pos.remove_shares(30)
        assert pos.total_shares == 70
        assert pos.available_shares == 70

    def test_position_remove_shares_insufficient_raises_error(self) -> None:
        """测试卖出数量不足异常"""
        pos = Position(
            symbol="000001",
            total_shares=100,
            available_shares=50,
            cost_basis=10.0,
            last_buy_date=date(2026, 6, 5),
        )
        with pytest.raises(InsufficientSharesError):
            pos.remove_shares(60)

    def test_position_unfreeze_for_date_same_day(self) -> None:
        """测试同日不解冻"""
        trade_date = date(2026, 6, 5)
        pos = Position(
            symbol="000001",
            total_shares=100,
            available_shares=0,
            cost_basis=10.0,
            last_buy_date=trade_date,
        )
        pos.unfreeze_for_date(trade_date)
        # 同一天不应解冻
        assert pos.available_shares == 0

    def test_position_unfreeze_for_date_next_day(self) -> None:
        """测试次日解冻"""
        trade_date = date(2026, 6, 5)
        pos = Position(
            symbol="000001",
            total_shares=100,
            available_shares=0,
            cost_basis=10.0,
            last_buy_date=trade_date,
        )
        pos.unfreeze_for_date(date(2026, 6, 6))
        # 次日应完全解冻
        assert pos.available_shares == 100
        assert pos.is_fully_sellable is True


class TestPositionTracker:
    """PositionTracker 单元测试"""

    def test_tracker_initial_empty(self) -> None:
        """测试追踪器初始为空"""
        tracker = PositionTracker()
        assert len(tracker.positions) == 0
        assert tracker.get_sellable_shares("000001") == 0

    def test_tracker_buy_on_day1_cannot_sell_same_day(self) -> None:
        """测试1：Day1买入，当日不可卖"""
        tracker = PositionTracker()
        trade_date = date(2026, 6, 5)

        # Day 1: 买入100股
        tracker.add_buy("000001", 100, 10.0, trade_date)

        # 当日不可卖
        assert tracker.get_sellable_shares("000001") == 0
        assert tracker.get_total_shares("000001") == 100
        assert tracker.get_frozen_shares("000001") == 100

    def test_tracker_buy_and_t1_unfreeze(self) -> None:
        """测试2：T+1后可卖"""
        tracker = PositionTracker()
        trade_date = date(2026, 6, 5)

        # Day 1: 买入100股
        tracker.add_buy("000001", 100, 10.0, trade_date)
        assert tracker.get_sellable_shares("000001") == 0

        # Day 2: 解冻T+1
        tracker.update_available_shares(date(2026, 6, 6))
        assert tracker.get_sellable_shares("000001") == 100
        assert tracker.get_frozen_shares("000001") == 0

    def test_tracker_multiple_buy_batches(self) -> None:
        """测试3：分批买入"""
        tracker = PositionTracker()

        # Day 1: 买入100股
        tracker.add_buy("000001", 100, 10.0, date(2026, 6, 5))
        assert tracker.get_sellable_shares("000001") == 0
        assert tracker.get_total_shares("000001") == 100

        # Day 2: 解冻，再买入50股
        tracker.update_available_shares(date(2026, 6, 6))
        assert tracker.get_sellable_shares("000001") == 100

        tracker.add_buy("000001", 50, 11.0, date(2026, 6, 6))
        # 新买的50股不能卖，旧的100股仍可卖
        assert tracker.get_sellable_shares("000001") == 100
        assert tracker.get_total_shares("000001") == 150
        assert tracker.get_frozen_shares("000001") == 50

        # Day 3: 全部可卖
        tracker.update_available_shares(date(2026, 6, 7))
        assert tracker.get_sellable_shares("000001") == 150
        assert tracker.get_frozen_shares("000001") == 0

    def test_tracker_partial_sell(self) -> None:
        """测试4：部分卖出"""
        tracker = PositionTracker()

        # Day 1: 买入100股
        tracker.add_buy("000001", 100, 10.0, date(2026, 6, 5))

        # Day 2: 解冻后卖出30股
        tracker.update_available_shares(date(2026, 6, 6))
        assert tracker.add_sell("000001", 30, date(2026, 6, 6)) is True

        assert tracker.get_total_shares("000001") == 70
        assert tracker.get_sellable_shares("000001") == 70

    def test_tracker_sell_insufficient_shares(self) -> None:
        """测试5：可卖数量不足"""
        tracker = PositionTracker()

        # Day 1: 买入100股
        tracker.add_buy("000001", 100, 10.0, date(2026, 6, 5))

        # Day 1: 尝试卖出（应失败，因为当日新买不能卖）
        with pytest.raises(InsufficientSharesError):
            tracker.add_sell("000001", 50, date(2026, 6, 5))

        # Day 2: 解冻后卖出120股（应失败，超过总持仓）
        tracker.update_available_shares(date(2026, 6, 6))
        with pytest.raises(InsufficientSharesError):
            tracker.add_sell("000001", 120, date(2026, 6, 6))

    def test_tracker_cost_basis_calculation(self) -> None:
        """测试6：成本价计算"""
        tracker = PositionTracker()

        # 买入100股@10元
        tracker.add_buy("000001", 100, 10.0, date(2026, 6, 5))
        assert tracker.get_cost_basis("000001") == 10.0

        # 再买50股@11元，成本价应为(100*10 + 50*11)/150 = 10.333...
        tracker.add_buy("000001", 50, 11.0, date(2026, 6, 6))
        expected_cost = (100 * 10.0 + 50 * 11.0) / 150
        assert abs(tracker.get_cost_basis("000001") - expected_cost) < 0.01

    def test_tracker_multiple_stocks(self) -> None:
        """测试7：多只股票管理"""
        tracker = PositionTracker()

        # 买入3只股票
        tracker.add_buy("000001", 100, 10.0, date(2026, 6, 5))
        tracker.add_buy("600000", 50, 20.0, date(2026, 6, 5))
        tracker.add_buy("600036", 75, 15.0, date(2026, 6, 6))

        # 验证初始状态
        assert tracker.get_sellable_shares("000001") == 0
        assert tracker.get_sellable_shares("600000") == 0
        assert tracker.get_sellable_shares("600036") == 0

        # Day 2 解冻
        tracker.update_available_shares(date(2026, 6, 6))
        assert tracker.get_sellable_shares("000001") == 100
        assert tracker.get_sellable_shares("600000") == 50
        # 600036 在Day 2 买的，还是冻结
        assert tracker.get_sellable_shares("600036") == 0

        # Day 3 全部解冻
        tracker.update_available_shares(date(2026, 6, 7))
        assert tracker.get_sellable_shares("000001") == 100
        assert tracker.get_sellable_shares("600000") == 50
        assert tracker.get_sellable_shares("600036") == 75

    def test_tracker_edge_case_zero_shares(self) -> None:
        """测试8：边界情况 - 零股份"""
        tracker = PositionTracker()

        with pytest.raises(NegativeSharesError):
            tracker.add_buy("000001", 0, 10.0, date(2026, 6, 5))

    def test_tracker_edge_case_negative_shares(self) -> None:
        """测试8b：边界情况 - 负股份"""
        tracker = PositionTracker()

        with pytest.raises(NegativeSharesError):
            tracker.add_buy("000001", -100, 10.0, date(2026, 6, 5))

    def test_tracker_edge_case_zero_price(self) -> None:
        """测试8c：边界情况 - 零价格"""
        tracker = PositionTracker()

        with pytest.raises(InvalidPriceError):
            tracker.add_buy("000001", 100, 0.0, date(2026, 6, 5))

    def test_tracker_edge_case_negative_price(self) -> None:
        """测试8d：边界情况 - 负价格"""
        tracker = PositionTracker()

        with pytest.raises(InvalidPriceError):
            tracker.add_buy("000001", 100, -10.0, date(2026, 6, 5))

    def test_tracker_sell_all_removes_position(self) -> None:
        """测试10：卖出全部持仓"""
        tracker = PositionTracker()

        # 买入100股
        tracker.add_buy("000001", 100, 10.0, date(2026, 6, 5))

        # 解冻后卖出全部
        tracker.update_available_shares(date(2026, 6, 6))
        assert tracker.add_sell("000001", 100, date(2026, 6, 6)) is True

        # 持仓应被删除
        assert tracker.get_total_shares("000001") == 0
        assert tracker.get_sellable_shares("000001") == 0
        assert not tracker.has_position("000001")

    def test_tracker_can_sell(self) -> None:
        """测试 can_sell 方法"""
        tracker = PositionTracker()

        tracker.add_buy("000001", 100, 10.0, date(2026, 6, 5))

        # Day 1: 不能卖
        assert tracker.can_sell("000001", 50) is False
        assert tracker.can_sell("000001", 100) is False

        # Day 2: 解冻后可以卖
        tracker.update_available_shares(date(2026, 6, 6))
        assert tracker.can_sell("000001", 50) is True
        assert tracker.can_sell("000001", 100) is True
        assert tracker.can_sell("000001", 101) is False

        # 不存在的股票
        assert tracker.can_sell("999999", 1) is False

    def test_tracker_get_position(self) -> None:
        """测试 get_position 方法"""
        tracker = PositionTracker()

        tracker.add_buy("000001", 100, 10.0, date(2026, 6, 5))

        pos = tracker.get_position("000001")
        assert pos is not None
        assert pos.symbol == "000001"
        assert pos.total_shares == 100

        assert tracker.get_position("999999") is None

    def test_tracker_get_all_positions(self) -> None:
        """测试 get_all_positions 方法"""
        tracker = PositionTracker()

        tracker.add_buy("000001", 100, 10.0, date(2026, 6, 5))
        tracker.add_buy("600000", 50, 20.0, date(2026, 6, 5))

        all_pos = tracker.get_all_positions()
        assert len(all_pos) == 2
        assert "000001" in all_pos
        assert "600000" in all_pos

    def test_tracker_complex_scenario(self) -> None:
        """综合测试：复杂交易场景"""
        tracker = PositionTracker()

        # Day 1: 买入500股000001 @10元
        tracker.add_buy("000001", 500, 10.0, date(2026, 6, 5))
        assert tracker.get_total_shares("000001") == 500
        assert tracker.get_sellable_shares("000001") == 0

        # Day 2: 解冻，再买200股 @11元
        tracker.update_available_shares(date(2026, 6, 6))
        assert tracker.get_sellable_shares("000001") == 500
        tracker.add_buy("000001", 200, 11.0, date(2026, 6, 6))

        # 总共700股，其中500可卖，200冻结
        assert tracker.get_total_shares("000001") == 700
        assert tracker.get_sellable_shares("000001") == 500
        assert tracker.get_frozen_shares("000001") == 200

        # Day 2: 卖出150股
        tracker.add_sell("000001", 150, date(2026, 6, 6))
        assert tracker.get_total_shares("000001") == 550
        assert tracker.get_sellable_shares("000001") == 350

        # Day 3: 全部解冻
        tracker.update_available_shares(date(2026, 6, 7))
        assert tracker.get_sellable_shares("000001") == 550
        assert tracker.get_frozen_shares("000001") == 0

        # 成本价验证：(500*10 + 200*11) / 700 = 7200/700 ≈ 10.286
        expected_cost = (500 * 10.0 + 200 * 11.0) / 700
        assert abs(tracker.get_cost_basis("000001") - expected_cost) < 0.01
