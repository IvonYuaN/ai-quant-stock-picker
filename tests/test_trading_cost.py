"""
A股交易成本计算模块的单元测试

测试范围：
- 买入成本计算
- 卖出成本计算
- 单次买卖总成本
- 最低佣金处理
- 边界情况处理
- 绝对成本计算
"""

from __future__ import annotations

import pytest
from aqsp.execution.cost import TradingCostCalculator


class TestBuyCostCalculation:
    """测试买入成本计算"""

    def test_buy_cost_standard_amount(self):
        """测试标准金额的买入成本"""
        # 10000元买入，滑点15bp
        # 佣金：max(10000 * 0.03%, 5) = 5元
        # 滑点：10000 * 0.15% = 15元
        # 成本%：(5 + 15) / 10000 * 100 = 0.20%
        cost = TradingCostCalculator.calculate_buy_cost(10000, slippage_bps=15)
        assert 0.19 < cost < 0.21, f"Expected ~0.20%, got {cost:.4f}%"

    def test_buy_cost_small_amount_min_commission(self):
        """测试小金额时最低佣金的应用"""
        # 100元买入，滑点15bp
        # 佣金：max(100 * 0.03%, 5) = 5元
        # 滑点：100 * 0.15% = 0.15元
        # 成本%：(5 + 0.15) / 100 * 100 = 5.15%
        cost = TradingCostCalculator.calculate_buy_cost(100, slippage_bps=15)
        assert 5.0 < cost < 5.5, f"Expected ~5.15%, got {cost:.4f}%"

    def test_buy_cost_zero_amount(self):
        """测试零金额的买入成本"""
        cost = TradingCostCalculator.calculate_buy_cost(0, slippage_bps=15)
        assert cost == 0.0

    def test_buy_cost_negative_amount_raises_error(self):
        """测试负数金额抛出异常"""
        with pytest.raises(ValueError, match="买入金额不能为负数"):
            TradingCostCalculator.calculate_buy_cost(-1000, slippage_bps=15)

    def test_buy_cost_invalid_slippage_negative(self):
        """测试负数滑点抛出异常"""
        with pytest.raises(ValueError, match="滑点必须在0-1000基点之间"):
            TradingCostCalculator.calculate_buy_cost(10000, slippage_bps=-1)

    def test_buy_cost_invalid_slippage_too_large(self):
        """测试过大滑点抛出异常"""
        with pytest.raises(ValueError, match="滑点必须在0-1000基点之间"):
            TradingCostCalculator.calculate_buy_cost(10000, slippage_bps=1001)

    def test_buy_cost_zero_slippage(self):
        """测试零滑点的买入成本"""
        cost = TradingCostCalculator.calculate_buy_cost(10000, slippage_bps=0)
        # 佣金：max(10000 * 0.03%, 5) = 5元 = 0.05%
        assert 0.045 < cost < 0.055, f"Expected ~0.05%, got {cost:.4f}%"

    def test_buy_cost_max_slippage(self):
        """测试最大滑点的买入成本"""
        cost = TradingCostCalculator.calculate_buy_cost(10000, slippage_bps=1000)
        # 佣金：0.03% + 滑点：10% = 10.03%
        assert 10.0 < cost < 10.1, f"Expected ~10.03%, got {cost:.4f}%"

    def test_buy_cost_large_amount(self):
        """测试大金额的买入成本"""
        cost = TradingCostCalculator.calculate_buy_cost(1000000, slippage_bps=15)
        # 佣金：1000000 * 0.03% = 300元 = 0.03%
        # 滑点：0.15%
        # 总成本：0.18%
        assert 0.17 < cost < 0.19, f"Expected ~0.18%, got {cost:.4f}%"


class TestSellCostCalculation:
    """测试卖出成本计算"""

    def test_sell_cost_standard_amount(self):
        """测试标准金额的卖出成本"""
        # 10000元卖出，滑点15bp
        # 佣金：max(10000 * 0.03%, 5) = 5元
        # 印花税：10000 * 0.1% = 10元
        # 滑点：10000 * 0.15% = 15元
        # 成本%：(5 + 10 + 15) / 10000 * 100 = 0.30%
        cost = TradingCostCalculator.calculate_sell_cost(10000, slippage_bps=15)
        assert 0.29 < cost < 0.31, f"Expected ~0.30%, got {cost:.4f}%"

    def test_sell_cost_includes_stamp_tax(self):
        """测试卖出成本包含印花税"""
        # 验证卖出成本 = 买入成本 + 印花税(0.1%)
        buy_cost = TradingCostCalculator.calculate_buy_cost(10000, slippage_bps=15)
        sell_cost = TradingCostCalculator.calculate_sell_cost(10000, slippage_bps=15)
        stamp_tax_pct = 0.1  # 10bp
        expected_diff = stamp_tax_pct
        actual_diff = sell_cost - buy_cost
        assert abs(actual_diff - expected_diff) < 0.001, (
            f"Expected diff {expected_diff}%, got {actual_diff}%"
        )

    def test_sell_cost_zero_amount(self):
        """测试零金额的卖出成本"""
        cost = TradingCostCalculator.calculate_sell_cost(0, slippage_bps=15)
        assert cost == 0.0

    def test_sell_cost_negative_amount_raises_error(self):
        """测试负数金额抛出异常"""
        with pytest.raises(ValueError, match="卖出金额不能为负数"):
            TradingCostCalculator.calculate_sell_cost(-1000, slippage_bps=15)

    def test_sell_cost_small_amount_min_commission(self):
        """测试小金额时最低佣金的应用"""
        # 100元卖出，滑点15bp
        # 佣金：max(100 * 0.03%, 5) = 5元
        # 印花税：100 * 0.1% = 0.1元
        # 滑点：100 * 0.15% = 0.15元
        # 成本%：(5 + 0.1 + 0.15) / 100 * 100 = 5.25%
        cost = TradingCostCalculator.calculate_sell_cost(100, slippage_bps=15)
        assert 5.1 < cost < 5.4, f"Expected ~5.25%, got {cost:.4f}%"

    def test_sell_cost_large_amount(self):
        """测试大金额的卖出成本"""
        cost = TradingCostCalculator.calculate_sell_cost(1000000, slippage_bps=15)
        # 佣金：0.03% + 印花税：0.1% + 滑点：0.15% = 0.28%
        assert 0.27 < cost < 0.29, f"Expected ~0.28%, got {cost:.4f}%"


class TestRoundTripCostCalculation:
    """测试单次买卖总成本"""

    def test_round_trip_cost_standard(self):
        """测试标准金额的单次买卖总成本"""
        # 买入：0.20% + 卖出：0.30% = 0.50%
        cost = TradingCostCalculator.calculate_round_trip_cost(10000, slippage_bps=15)
        assert 0.49 < cost < 0.51, f"Expected ~0.50%, got {cost:.4f}%"

    def test_round_trip_cost_is_sum_of_buy_and_sell(self):
        """测试单次买卖成本 = 买入成本 + 卖出成本"""
        amount = 10000
        buy_cost = TradingCostCalculator.calculate_buy_cost(amount)
        sell_cost = TradingCostCalculator.calculate_sell_cost(amount)
        round_trip_cost = TradingCostCalculator.calculate_round_trip_cost(amount)
        expected = buy_cost + sell_cost
        assert abs(round_trip_cost - expected) < 0.0001, (
            f"Expected {expected}%, got {round_trip_cost}%"
        )

    def test_round_trip_cost_zero_amount(self):
        """测试零金额的单次买卖成本"""
        cost = TradingCostCalculator.calculate_round_trip_cost(0)
        assert cost == 0.0

    def test_round_trip_cost_negative_amount_raises_error(self):
        """测试负数金额抛出异常"""
        with pytest.raises(ValueError, match="交易金额不能为负数"):
            TradingCostCalculator.calculate_round_trip_cost(-1000)

    def test_round_trip_cost_small_amount(self):
        """测试小金额的单次买卖成本"""
        # 100元交易
        cost = TradingCostCalculator.calculate_round_trip_cost(100, slippage_bps=15)
        # 买入：~5.15% + 卖出：~5.25% = ~10.4%
        assert 10.0 < cost < 10.8, f"Expected ~10.4%, got {cost:.4f}%"

    def test_round_trip_cost_reasonable_range(self):
        """测试单次买卖成本在合理范围内"""
        # 标准情况下，单次买卖成本应在0.4-0.6%之间
        cost = TradingCostCalculator.calculate_round_trip_cost(10000, slippage_bps=15)
        assert 0.4 < cost < 0.6, f"Cost {cost}% out of reasonable range"


class TestMinimumCommissionHandling:
    """测试最低佣金处理"""

    def test_min_commission_applies_to_small_buy(self):
        """测试最低佣金应用于小额买入"""
        # 100元买入
        # 正常佣金：100 * 0.03% = 0.03元
        # 最低佣金：5元
        # 应用最低佣金
        cost = TradingCostCalculator.calculate_buy_cost(100, slippage_bps=0)
        # 佣金成本%：5 / 100 * 100 = 5%
        assert cost > 4.9, f"Min commission not applied, got {cost}%"

    def test_min_commission_not_applies_to_large_buy(self):
        """测试大额买入时最低佣金不适用"""
        # 100000元买入
        # 正常佣金：100000 * 0.03% = 30元
        # 30元 > 5元，不应用最低佣金
        cost = TradingCostCalculator.calculate_buy_cost(100000, slippage_bps=0)
        # 佣金成本%：30 / 100000 * 100 = 0.03%
        assert 0.025 < cost < 0.035, f"Expected ~0.03%, got {cost}%"

    def test_min_commission_threshold(self):
        """测试最低佣金的临界点"""
        # 佣金 = 金额 * 0.03%
        # 当佣金 = 5元时，金额 = 5 / 0.0003 = 16666.67元
        threshold = 5.0 / 0.0003

        # 金额略小于临界点
        cost_below = TradingCostCalculator.calculate_buy_cost(
            threshold - 1, slippage_bps=0
        )
        # 应该应用最低佣金
        assert cost_below > 0.029, "Min commission not applied below threshold"

        # 金额略大于临界点
        cost_above = TradingCostCalculator.calculate_buy_cost(
            threshold + 1, slippage_bps=0
        )
        # 不应该应用最低佣金
        assert cost_above < 0.035, "Min commission incorrectly applied above threshold"


class TestEdgeCases:
    """测试边界情况"""

    def test_very_small_amount(self):
        """测试非常小的金额"""
        cost = TradingCostCalculator.calculate_buy_cost(1, slippage_bps=15)
        # 1元买入：最低佣金5元占 500%
        assert cost > 100, "Very small amount handling failed"

    def test_very_large_amount(self):
        """测试非常大的金额"""
        cost = TradingCostCalculator.calculate_buy_cost(100000000, slippage_bps=15)
        # 1亿元买入：佣金0.03% + 滑点0.15% = 0.18%
        assert 0.17 < cost < 0.19, "Very large amount handling failed"

    def test_float_precision(self):
        """测试浮点数精度"""
        cost = TradingCostCalculator.calculate_buy_cost(12345.67, slippage_bps=15)
        assert isinstance(cost, float)
        assert not isinstance(cost, int)

    def test_consistent_results(self):
        """测试结果一致性"""
        cost1 = TradingCostCalculator.calculate_buy_cost(10000, slippage_bps=15)
        cost2 = TradingCostCalculator.calculate_buy_cost(10000, slippage_bps=15)
        assert cost1 == cost2, "Results should be consistent"


class TestAbsoluteCostCalculation:
    """测试绝对成本计算 (元)"""

    def test_buy_cost_absolute_standard(self):
        """测试标准金额的绝对买入成本"""
        cost = TradingCostCalculator.calculate_buy_cost_absolute(10000, slippage_bps=15)
        # 佣金：max(10000 * 0.03%, 5) = 5元
        # 滑点：10000 * 0.15% = 15元
        # 总成本：20元
        assert 19 < cost < 21, f"Expected ~20 yuan, got {cost}"

    def test_sell_cost_absolute_standard(self):
        """测试标准金额的绝对卖出成本"""
        cost = TradingCostCalculator.calculate_sell_cost_absolute(
            10000, slippage_bps=15
        )
        # 佣金：5元 + 印花税：10元 + 滑点：15元 = 30元
        assert 29 < cost < 31, f"Expected ~30 yuan, got {cost}"

    def test_round_trip_cost_absolute_standard(self):
        """测试标准金额的绝对单次买卖成本"""
        cost = TradingCostCalculator.calculate_round_trip_cost_absolute(
            10000, slippage_bps=15
        )
        # 买入：20元 + 卖出：30元 = 50元
        assert 49 < cost < 51, f"Expected ~50 yuan, got {cost}"

    def test_buy_cost_absolute_min_commission(self):
        """测试最低佣金的绝对成本"""
        cost = TradingCostCalculator.calculate_buy_cost_absolute(100, slippage_bps=0)
        # 佣金：max(100 * 0.03%, 5) = 5元
        # 滑点：0元
        # 总成本：5元
        assert cost == 5.0, f"Expected 5 yuan, got {cost}"

    def test_absolute_cost_matches_percentage(self):
        """测试绝对成本与百分比的一致性"""
        amount = 10000
        buy_cost_pct = TradingCostCalculator.calculate_buy_cost(amount, slippage_bps=15)
        buy_cost_abs = TradingCostCalculator.calculate_buy_cost_absolute(
            amount, slippage_bps=15
        )

        expected_abs = amount * buy_cost_pct / 100
        assert abs(buy_cost_abs - expected_abs) < 0.01, (
            f"Mismatch: {buy_cost_abs} vs {expected_abs}"
        )


class TestSlippageVariation:
    """测试不同滑点的影响"""

    def test_slippage_10bp(self):
        """测试10bp滑点"""
        cost = TradingCostCalculator.calculate_buy_cost(10000, slippage_bps=10)
        # 佣金：0.05% + 滑点：0.1% = 0.15%
        assert 0.14 < cost < 0.16, f"Expected ~0.15%, got {cost}%"

    def test_slippage_20bp(self):
        """测试20bp滑点"""
        cost = TradingCostCalculator.calculate_buy_cost(10000, slippage_bps=20)
        # 佣金：0.05% + 滑点：0.2% = 0.25%
        assert 0.24 < cost < 0.26, f"Expected ~0.25%, got {cost}%"

    def test_slippage_linear_impact(self):
        """测试滑点的线性影响"""
        cost_10bp = TradingCostCalculator.calculate_buy_cost(10000, slippage_bps=10)
        cost_20bp = TradingCostCalculator.calculate_buy_cost(10000, slippage_bps=20)
        # 成本差异应为0.1%
        diff = cost_20bp - cost_10bp
        assert abs(diff - 0.1) < 0.001, f"Expected 0.1% difference, got {diff}%"


class TestDocstringExamples:
    """验证docstring中的示例是否准确"""

    def test_buy_cost_docstring_example(self):
        """验证buy_cost docstring的示例"""
        cost = TradingCostCalculator.calculate_buy_cost(10000, slippage_bps=15)
        # Docstring示例：0.2000%
        assert 0.19 < cost < 0.21, f"Docstring example failed: {cost}%"

    def test_sell_cost_docstring_example(self):
        """验证sell_cost docstring的示例"""
        cost = TradingCostCalculator.calculate_sell_cost(10000, slippage_bps=15)
        # Docstring示例：0.3000%
        assert 0.29 < cost < 0.31, f"Docstring example failed: {cost}%"

    def test_round_trip_cost_docstring_example(self):
        """验证round_trip_cost docstring的示例"""
        cost = TradingCostCalculator.calculate_round_trip_cost(10000, slippage_bps=15)
        # Docstring示例：0.5000%
        assert 0.49 < cost < 0.51, f"Docstring example failed: {cost}%"
