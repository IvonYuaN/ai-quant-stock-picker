"""止损管理器单元测试。

测试覆盖：
1. 单只股票止损触发/不触发
2. 组合整体止损触发/不触发
3. 移动止损更新逻辑
4. 边界情况处理
5. 异常输入验证
6. 空仓情况
"""

import pytest
from aqsp.risk.stop_loss import (
    Position,
    StopLossConfig,
    StopLossManager,
    StopLossCheckResult,
)


class TestStopLossConfig:
    """配置验证测试。"""
    
    def test_valid_config(self) -> None:
        """测试有效的配置。"""
        config = StopLossConfig(
            single_stock_stop=-0.08,
            portfolio_stop=-0.15,
            trailing_stop_pct=0.05,
            enable_trailing=True,
        )
        assert config.single_stock_stop == -0.08
        assert config.portfolio_stop == -0.15
        assert config.trailing_stop_pct == 0.05
        assert config.enable_trailing is True
    
    def test_default_config(self) -> None:
        """测试默认配置。"""
        config = StopLossConfig()
        assert config.single_stock_stop == -0.08
        assert config.portfolio_stop == -0.15
        assert config.trailing_stop_pct == 0.05
        assert config.enable_trailing is True
    
    def test_invalid_single_stock_stop_positive(self) -> None:
        """测试单股止损为正数时抛出异常。"""
        with pytest.raises(ValueError, match="single_stock_stop必须为负数"):
            StopLossConfig(single_stock_stop=0.05)
    
    def test_invalid_portfolio_stop_positive(self) -> None:
        """测试组合止损为正数时抛出异常。"""
        with pytest.raises(ValueError, match="portfolio_stop必须为负数"):
            StopLossConfig(portfolio_stop=0.1)
    
    def test_invalid_trailing_stop_pct_zero(self) -> None:
        """测试移动止损为0时抛出异常。"""
        with pytest.raises(ValueError, match="trailing_stop_pct必须在0-1之间"):
            StopLossConfig(trailing_stop_pct=0.0)
    
    def test_invalid_trailing_stop_pct_one(self) -> None:
        """测试移动止损为1时抛出异常。"""
        with pytest.raises(ValueError, match="trailing_stop_pct必须在0-1之间"):
            StopLossConfig(trailing_stop_pct=1.0)
    
    def test_invalid_trailing_stop_pct_negative(self) -> None:
        """测试移动止损为负数时抛出异常。"""
        with pytest.raises(ValueError, match="trailing_stop_pct必须在0-1之间"):
            StopLossConfig(trailing_stop_pct=-0.05)


class TestPosition:
    """持仓数据结构测试。"""
    
    def test_valid_position(self) -> None:
        """测试有效的持仓。"""
        pos = Position(
            symbol="000001",
            shares=100,
            cost_basis=10.0,
            high_water_mark=12.0,
        )
        assert pos.symbol == "000001"
        assert pos.shares == 100
        assert pos.cost_basis == 10.0
        assert pos.high_water_mark == 12.0
    
    def test_position_auto_set_high_water_mark(self) -> None:
        """测试持仓初始化时自动设置最高价。"""
        pos = Position(
            symbol="000001",
            shares=100,
            cost_basis=10.0,
        )
        # 最高价应该默认等于成本价
        assert pos.high_water_mark == 10.0
    
    def test_invalid_position_negative_shares(self) -> None:
        """测试持仓数量为负数时抛出异常。"""
        with pytest.raises(ValueError, match="shares必须为非负数"):
            Position(
                symbol="000001",
                shares=-100,
                cost_basis=10.0,
            )
    
    def test_invalid_position_zero_cost_basis(self) -> None:
        """测试成本价为0时抛出异常。"""
        with pytest.raises(ValueError, match="cost_basis必须为正数"):
            Position(
                symbol="000001",
                shares=100,
                cost_basis=0.0,
            )
    
    def test_invalid_position_negative_cost_basis(self) -> None:
        """测试成本价为负数时抛出异常。"""
        with pytest.raises(ValueError, match="cost_basis必须为正数"):
            Position(
                symbol="000001",
                shares=100,
                cost_basis=-10.0,
            )
    
    def test_invalid_position_invalid_high_water_mark(self) -> None:
        """测试最高价为0时抛出异常。"""
        with pytest.raises(ValueError, match="high_water_mark必须为正数"):
            Position(
                symbol="000001",
                shares=100,
                cost_basis=10.0,
                high_water_mark=0.0,
            )


class TestSingleStockStop:
    """单只股票止损测试。"""
    
    def test_single_stock_stop_triggered(self) -> None:
        """测试单只股票止损触发。"""
        manager = StopLossManager()
        pos = Position("000001", 100, 10.0)
        
        # 亏损8%，触发 -8% 止损
        current_price = 9.2
        result = manager.check_single_stock_stop(pos, current_price)
        
        assert result.triggered is True
        assert "止损触发" in result.reason
        assert result.pnl_pct == pytest.approx(-0.08)
        assert result.loss_amount == pytest.approx(80.0)
    
    def test_single_stock_stop_not_triggered_profit(self) -> None:
        """测试盈利状态不触发止损。"""
        manager = StopLossManager()
        pos = Position("000001", 100, 10.0)
        
        # 盈利10%
        current_price = 11.0
        result = manager.check_single_stock_stop(pos, current_price)
        
        assert result.triggered is False
        assert result.pnl_pct == pytest.approx(0.10)
        assert result.loss_amount == 0.0
    
    def test_single_stock_stop_not_triggered_small_loss(self) -> None:
        """测试小幅亏损不触发止损。"""
        manager = StopLossManager()
        pos = Position("000001", 100, 10.0)
        
        # 亏损3%，未达到 -8% 止损线
        current_price = 9.7
        result = manager.check_single_stock_stop(pos, current_price)
        
        assert result.triggered is False
        assert result.pnl_pct == pytest.approx(-0.03)
    
    def test_single_stock_stop_boundary_at_threshold(self) -> None:
        """测试恰好在止损线上不触发（保守）。"""
        manager = StopLossManager()
        pos = Position("000001", 100, 10.0)
        
        # 恰好亏损 -8%（边界值）
        current_price = 9.2
        result = manager.check_single_stock_stop(pos, current_price)
        
        assert result.triggered is True  # 等于或超过阈值就触发
    
    def test_single_stock_stop_deep_loss(self) -> None:
        """测试深度亏损清晰触发。"""
        manager = StopLossManager()
        pos = Position("000001", 100, 10.0)
        
        # 亏损20%
        current_price = 8.0
        result = manager.check_single_stock_stop(pos, current_price)
        
        assert result.triggered is True
        assert result.pnl_pct == pytest.approx(-0.20)
        assert result.loss_amount == pytest.approx(200.0)
    
    def test_single_stock_stop_invalid_price_zero(self) -> None:
        """测试当前价格为0时抛出异常。"""
        manager = StopLossManager()
        pos = Position("000001", 100, 10.0)
        
        with pytest.raises(ValueError, match="current_price必须为正数"):
            manager.check_single_stock_stop(pos, 0.0)
    
    def test_single_stock_stop_invalid_price_negative(self) -> None:
        """测试当前价格为负数时抛出异常。"""
        manager = StopLossManager()
        pos = Position("000001", 100, 10.0)
        
        with pytest.raises(ValueError, match="current_price必须为正数"):
            manager.check_single_stock_stop(pos, -5.0)


class TestMultipleStocks:
    """多持仓检查测试。"""
    
    def test_check_multiple_stops(self) -> None:
        """测试检查多只股票止损。"""
        manager = StopLossManager()
        positions = [
            Position("000001", 100, 10.0),
            Position("000002", 200, 20.0),
            Position("000003", 150, 15.0),
        ]
        
        current_prices = {
            "000001": 9.2,   # 触发 -8% 止损
            "000002": 19.5,  # 未触发 -2.5%
            "000003": 13.8,  # 触发 -8% 止损
        }
        
        stops = manager.check_single_stock_stops(positions, current_prices)
        assert set(stops) == {"000001", "000003"}
    
    def test_check_multiple_stops_empty_list(self) -> None:
        """测试空持仓列表。"""
        manager = StopLossManager()
        positions: list[Position] = []
        current_prices: dict[str, float] = {}
        
        stops = manager.check_single_stock_stops(positions, current_prices)
        assert stops == []
    
    def test_check_multiple_stops_missing_price(self) -> None:
        """测试缺少某只股票的价格数据。"""
        manager = StopLossManager()
        positions = [
            Position("000001", 100, 10.0),
            Position("000002", 200, 20.0),
        ]
        
        current_prices = {
            "000001": 9.2,
            # 缺少 000002 的价格
        }
        
        with pytest.raises(ValueError, match="缺少持仓.*的价格数据"):
            manager.check_single_stock_stops(positions, current_prices)


class TestPortfolioStop:
    """组合整体止损测试。"""
    
    def test_portfolio_stop_triggered(self) -> None:
        """测试组合止损触发。"""
        manager = StopLossManager()
        
        initial_value = 100000.0
        portfolio_value = 84500.0  # 亏损 15.5%
        
        result = manager.check_portfolio_stop(portfolio_value, initial_value)
        
        assert result.triggered is True
        assert "组合止损触发" in result.reason
        assert result.pnl_pct == pytest.approx(-0.155)
        assert result.loss_amount == pytest.approx(15500.0)
    
    def test_portfolio_stop_not_triggered(self) -> None:
        """测试组合未触发止损。"""
        manager = StopLossManager()
        
        initial_value = 100000.0
        portfolio_value = 87000.0  # 亏损 13%，未达到 -15%
        
        result = manager.check_portfolio_stop(portfolio_value, initial_value)
        
        assert result.triggered is False
        assert result.pnl_pct == pytest.approx(-0.13)
    
    def test_portfolio_stop_profit(self) -> None:
        """测试盈利状态不触发止损。"""
        manager = StopLossManager()
        
        initial_value = 100000.0
        portfolio_value = 120000.0  # 盈利 20%
        
        result = manager.check_portfolio_stop(portfolio_value, initial_value)
        
        assert result.triggered is False
        assert result.pnl_pct == pytest.approx(0.20)
        assert result.loss_amount == 0.0
    
    def test_portfolio_stop_boundary(self) -> None:
        """测试恰好在止损线上触发。"""
        manager = StopLossManager()
        
        initial_value = 100000.0
        portfolio_value = 85000.0  # 恰好亏损 -15%
        
        result = manager.check_portfolio_stop(portfolio_value, initial_value)
        
        assert result.triggered is True
    
    def test_portfolio_stop_invalid_initial_value_zero(self) -> None:
        """测试初始值为0时抛出异常。"""
        manager = StopLossManager()
        
        with pytest.raises(ValueError, match="initial_value必须为正数"):
            manager.check_portfolio_stop(100000.0, 0.0)
    
    def test_portfolio_stop_invalid_initial_value_negative(self) -> None:
        """测试初始值为负数时抛出异常。"""
        manager = StopLossManager()
        
        with pytest.raises(ValueError, match="initial_value必须为正数"):
            manager.check_portfolio_stop(100000.0, -50000.0)
    
    def test_portfolio_stop_zero_portfolio_value(self) -> None:
        """测试组合价值为0时返回未触发（异常情况）。"""
        manager = StopLossManager()
        
        result = manager.check_portfolio_stop(0.0, 100000.0)
        
        assert result.triggered is False
        assert "异常" in result.reason


class TestTrailingStop:
    """移动止损测试。"""
    
    def test_update_trailing_stops_price_increase(self) -> None:
        """测试价格上升时更新最高价。"""
        manager = StopLossManager()
        positions = [
            Position("000001", 100, 10.0, high_water_mark=11.0),
        ]
        
        current_prices = {
            "000001": 12.0,  # 价格上升，应更新最高价
        }
        
        updates = manager.update_trailing_stops(positions, current_prices)
        
        assert updates["000001"].updated is True
        assert updates["000001"].new_high_water_mark == 12.0
        assert updates["000001"].current_price == 12.0
    
    def test_update_trailing_stops_price_decrease(self) -> None:
        """测试价格下降时不更新最高价（只升不降）。"""
        manager = StopLossManager()
        positions = [
            Position("000001", 100, 10.0, high_water_mark=12.0),
        ]
        
        current_prices = {
            "000001": 11.5,  # 价格下降，不更新
        }
        
        updates = manager.update_trailing_stops(positions, current_prices)
        
        assert updates["000001"].updated is False
        assert updates["000001"].new_high_water_mark == 12.0
    
    def test_update_trailing_stops_disabled(self) -> None:
        """测试移动止损禁用时不更新。"""
        config = StopLossConfig(enable_trailing=False)
        manager = StopLossManager(config)
        
        positions = [
            Position("000001", 100, 10.0, high_water_mark=11.0),
        ]
        
        current_prices = {
            "000001": 12.0,
        }
        
        updates = manager.update_trailing_stops(positions, current_prices)
        
        assert updates == {}
    
    def test_check_trailing_stop_triggered(self) -> None:
        """测试移动止损触发。"""
        manager = StopLossManager()
        pos = Position("000001", 100, 10.0, high_water_mark=15.0)
        
        # 从最高 15.0 回撤超过 5%，即低于 14.25
        current_price = 14.2
        
        result = manager.check_trailing_stop_trigger(pos, current_price)
        
        assert result.triggered is True
        assert "移动止损触发" in result.reason
    
    def test_check_trailing_stop_not_triggered(self) -> None:
        """测试移动止损未触发。"""
        manager = StopLossManager()
        pos = Position("000001", 100, 10.0, high_water_mark=15.0)
        
        # 从最高 15.0 回撤 4%，即 14.4，未超过阈值
        current_price = 14.4
        
        result = manager.check_trailing_stop_trigger(pos, current_price)
        
        assert result.triggered is False
    
    def test_check_trailing_stop_disabled(self) -> None:
        """测试移动止损禁用时不触发。"""
        config = StopLossConfig(enable_trailing=False)
        manager = StopLossManager(config)
        
        pos = Position("000001", 100, 10.0, high_water_mark=15.0)
        current_price = 14.0  # 本应触发
        
        result = manager.check_trailing_stop_trigger(pos, current_price)
        
        assert result.triggered is False
    
    def test_get_stop_price(self) -> None:
        """测试获取移动止损价格。"""
        manager = StopLossManager()
        pos = Position("000001", 100, 10.0, high_water_mark=20.0)
        
        stop_price = manager.get_stop_price(pos)
        
        expected = 20.0 * (1 - 0.05)  # 20.0 * 0.95
        assert stop_price == pytest.approx(expected)
    
    def test_get_stop_price_disabled(self) -> None:
        """测试移动止损禁用时返回0。"""
        config = StopLossConfig(enable_trailing=False)
        manager = StopLossManager(config)
        
        pos = Position("000001", 100, 10.0, high_water_mark=20.0)
        
        stop_price = manager.get_stop_price(pos)
        
        assert stop_price == 0.0


class TestValidation:
    """数据验证测试。"""
    
    def test_validate_positions_valid(self) -> None:
        """测试有效的持仓数据。"""
        manager = StopLossManager()
        positions = [
            Position("000001", 100, 10.0),
            Position("000002", 200, 20.0),
        ]
        
        current_prices = {
            "000001": 10.5,
            "000002": 20.5,
        }
        
        is_valid, errors = manager.validate_positions(positions, current_prices)
        
        assert is_valid is True
        assert errors == []
    
    def test_validate_positions_missing_price(self) -> None:
        """测试缺少价格数据的检查。"""
        manager = StopLossManager()
        positions = [
            Position("000001", 100, 10.0),
            Position("000002", 200, 20.0),
        ]
        
        current_prices = {
            "000001": 10.5,
            # 缺少 000002
        }
        
        is_valid, errors = manager.validate_positions(positions, current_prices)
        
        assert is_valid is False
        assert len(errors) == 1
        assert "缺少持仓 000002" in errors[0]
    
    def test_validate_positions_invalid_price(self) -> None:
        """测试无效的价格数据。"""
        manager = StopLossManager()
        positions = [
            Position("000001", 100, 10.0),
        ]
        
        current_prices = {
            "000001": -5.0,  # 负数
        }
        
        is_valid, errors = manager.validate_positions(positions, current_prices)
        
        assert is_valid is False
        assert len(errors) == 1
        assert "000001" in errors[0]


class TestEdgeCases:
    """边界情况测试。"""
    
    def test_zero_shares_position(self) -> None:
        """测试零仓位。"""
        pos = Position("000001", 0, 10.0)
        manager = StopLossManager()
        
        result = manager.check_single_stock_stop(pos, 9.2)
        
        assert result.triggered is True
        assert result.loss_amount == 0.0
    
    def test_high_price_position(self) -> None:
        """测试高价股票。"""
        pos = Position("000001", 100, 5000.0)
        manager = StopLossManager()
        
        current_price = 4600.0  # 亏损 8%
        
        result = manager.check_single_stock_stop(pos, current_price)
        
        assert result.triggered is True
        assert result.loss_amount == pytest.approx(40000.0)
    
    def test_small_price_position(self) -> None:
        """测试低价股票。"""
        pos = Position("000001", 10000, 0.5)
        manager = StopLossManager()
        
        current_price = 0.46  # 亏损 8%
        
        result = manager.check_single_stock_stop(pos, current_price)
        
        assert result.triggered is True
        assert result.loss_amount == pytest.approx(400.0)
    
    def test_custom_config_thresholds(self) -> None:
        """测试自定义止损阈值。"""
        config = StopLossConfig(
            single_stock_stop=-0.10,  # 10% 止损
            portfolio_stop=-0.20,     # 20% 止损
            trailing_stop_pct=0.03,   # 3% 移动止损
        )
        manager = StopLossManager(config)
        
        pos = Position("000001", 100, 10.0)
        
        # 9% 亏损，不触发 10% 止损
        result = manager.check_single_stock_stop(pos, 9.1)
        assert result.triggered is False
        
        # 10% 亏损，触发 10% 止损
        result = manager.check_single_stock_stop(pos, 9.0)
        assert result.triggered is True


class TestConservativeBehavior:
    """保守性行为测试 - 确保不会误触发。"""
    
    def test_no_false_positive_on_profit(self) -> None:
        """确保盈利状态绝不触发止损。"""
        manager = StopLossManager()
        pos = Position("000001", 1000, 10.0)
        
        # 测试各种盈利水平
        for price in [10.1, 11.0, 15.0, 20.0, 100.0]:
            result = manager.check_single_stock_stop(pos, price)
            assert result.triggered is False, f"盈利{price}时不应触发止损"
    
    def test_no_false_positive_portfolio(self) -> None:
        """确保盈利组合绝不触发止损。"""
        manager = StopLossManager()
        
        initial = 100000.0
        for portfolio_val in [100001.0, 110000.0, 150000.0]:
            result = manager.check_portfolio_stop(portfolio_val, initial)
            assert result.triggered is False, f"盈利组合不应触发止损"
    
    def test_boundary_conservatism(self) -> None:
        """确保在边界处采取保守策略。"""
        manager = StopLossManager()
        pos = Position("000001", 100, 10.0)
        
        # 恰好达到止损线 -8%，应该触发（保守）
        result = manager.check_single_stock_stop(pos, 9.2)
        assert result.triggered is True
        
        # 略好于止损线，例如 -7.9%，不应触发
        result = manager.check_single_stock_stop(pos, 9.201)
        assert result.triggered is False
