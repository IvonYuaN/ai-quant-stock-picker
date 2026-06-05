"""
A股交易成本计算模块

成本构成：
- 买入：佣金0.03% + 滑点0.1-0.2%
- 卖出：佣金0.03% + 印花税0.1% + 滑点0.1-0.2%

标准参数：
- 佣金率：0.03% (双边，最低5元)
- 印花税率：0.1% (仅卖出)
- 滑点：15-20个基点 (0.15%-0.2%)
"""

from __future__ import annotations


class TradingCostCalculator:
    """
    A股交易成本计算器

    提供了计算买入、卖出及单次买卖总成本的方法。
    所有计算均基于A股市场标准费率。

    属性：
        COMMISSION_RATE: 佣金率 (0.03%)
        STAMP_TAX_RATE: 印花税率 (0.1%, 仅卖出)
        MIN_COMMISSION: 最低佣金金额 (5元)
    """

    # 费率常数 (万分比)
    COMMISSION_RATE: float = 0.0003  # 0.03% = 3个基点
    STAMP_TAX_RATE: float = 0.001    # 0.1% = 10个基点
    MIN_COMMISSION: float = 5.0      # 最低佣金5元

    @staticmethod
    def calculate_buy_cost(
        amount: float,
        slippage_bps: int = 15,
    ) -> float:
        """
        计算买入成本百分比

        成本 = 佣金 + 滑点

        参数：
            amount: 买入金额 (元)
            slippage_bps: 滑点 (基点数，默认15bp = 0.15%)
                         范围：10-20

        返回：
            买入成本百分比 (%)

        异常：
            ValueError: 当amount为负数或滑点不在范围内时抛出

        示例：
            >>> calc = TradingCostCalculator()
            >>> cost = calc.calculate_buy_cost(10000, slippage_bps=15)
            >>> print(f"{cost:.4f}%")
            0.1800%
        """
        # 边界检查
        if amount < 0:
            raise ValueError(f"买入金额不能为负数，收到: {amount}")
        if slippage_bps < 0 or slippage_bps > 1000:
            raise ValueError(f"滑点必须在0-1000基点之间，收到: {slippage_bps}")

        # 金额为0时直接返回0
        if amount == 0:
            return 0.0

        # 计算佣金 (取最小值和计算值的较大者)
        commission_amount = max(amount * TradingCostCalculator.COMMISSION_RATE,
                               TradingCostCalculator.MIN_COMMISSION)
        commission_pct = (commission_amount / amount) * 100 if amount > 0 else 0

        # 滑点转换为百分比
        slippage_pct = slippage_bps / 10000 * 100  # 基点转百分比

        # 买入成本 = 佣金% + 滑点%
        total_cost_pct = commission_pct + slippage_pct

        return total_cost_pct

    @staticmethod
    def calculate_sell_cost(
        amount: float,
        slippage_bps: int = 15,
    ) -> float:
        """
        计算卖出成本百分比

        成本 = 佣金 + 印花税 + 滑点

        参数：
            amount: 卖出金额 (元)
            slippage_bps: 滑点 (基点数，默认15bp = 0.15%)
                         范围：10-20

        返回：
            卖出成本百分比 (%)

        异常：
            ValueError: 当amount为负数或滑点不在范围内时抛出

        示例：
            >>> calc = TradingCostCalculator()
            >>> cost = calc.calculate_sell_cost(10000, slippage_bps=15)
            >>> print(f"{cost:.4f}%")
            0.2800%
        """
        # 边界检查
        if amount < 0:
            raise ValueError(f"卖出金额不能为负数，收到: {amount}")
        if slippage_bps < 0 or slippage_bps > 1000:
            raise ValueError(f"滑点必须在0-1000基点之间，收到: {slippage_bps}")

        # 金额为0时直接返回0
        if amount == 0:
            return 0.0

        # 计算佣金 (取最小值和计算值的较大者)
        commission_amount = max(amount * TradingCostCalculator.COMMISSION_RATE,
                               TradingCostCalculator.MIN_COMMISSION)
        commission_pct = (commission_amount / amount) * 100 if amount > 0 else 0

        # 印花税百分比
        stamp_tax_pct = TradingCostCalculator.STAMP_TAX_RATE * 100  # 0.1% = 10bp

        # 滑点转换为百分比
        slippage_pct = slippage_bps / 10000 * 100  # 基点转百分比

        # 卖出成本 = 佣金% + 印花税% + 滑点%
        total_cost_pct = commission_pct + stamp_tax_pct + slippage_pct

        return total_cost_pct

    @staticmethod
    def calculate_round_trip_cost(
        amount: float,
        slippage_bps: int = 15,
    ) -> float:
        """
        计算单次买卖总成本百分比

        计算买入和卖出的总成本，假设买卖金额相同。

        成本 = 买入成本 + 卖出成本

        参数：
            amount: 买卖金额 (元)
            slippage_bps: 滑点 (基点数，默认15bp = 0.15%)
                         范围：10-20

        返回：
            单次买卖总成本百分比 (%)

        异常：
            ValueError: 当amount为负数或滑点不在范围内时抛出

        示例：
            >>> calc = TradingCostCalculator()
            >>> cost = calc.calculate_round_trip_cost(10000, slippage_bps=15)
            >>> print(f"{cost:.4f}%")
            0.4600%
        """
        # 边界检查
        if amount < 0:
            raise ValueError(f"交易金额不能为负数，收到: {amount}")
        if slippage_bps < 0 or slippage_bps > 1000:
            raise ValueError(f"滑点必须在0-1000基点之间，收到: {slippage_bps}")

        # 金额为0时直接返回0
        if amount == 0:
            return 0.0

        # 买入成本
        buy_cost = TradingCostCalculator.calculate_buy_cost(amount, slippage_bps)

        # 卖出成本
        sell_cost = TradingCostCalculator.calculate_sell_cost(amount, slippage_bps)

        # 单次买卖总成本
        total_cost_pct = buy_cost + sell_cost

        return total_cost_pct

    @staticmethod
    def calculate_buy_cost_absolute(
        amount: float,
        slippage_bps: int = 15,
    ) -> float:
        """
        计算买入绝对成本 (元)

        参数：
            amount: 买入金额 (元)
            slippage_bps: 滑点 (基点数，默认15bp)

        返回：
            买入成本金额 (元)

        异常：
            ValueError: 当amount为负数或滑点不在范围内时抛出
        """
        if amount < 0:
            raise ValueError(f"买入金额不能为负数，收到: {amount}")
        if slippage_bps < 0 or slippage_bps > 1000:
            raise ValueError(f"滑点必须在0-1000基点之间，收到: {slippage_bps}")

        if amount == 0:
            return 0.0

        # 佣金
        commission = max(amount * TradingCostCalculator.COMMISSION_RATE,
                        TradingCostCalculator.MIN_COMMISSION)

        # 滑点
        slippage = amount * slippage_bps / 10000

        return commission + slippage

    @staticmethod
    def calculate_sell_cost_absolute(
        amount: float,
        slippage_bps: int = 15,
    ) -> float:
        """
        计算卖出绝对成本 (元)

        参数：
            amount: 卖出金额 (元)
            slippage_bps: 滑点 (基点数，默认15bp)

        返回：
            卖出成本金额 (元)

        异常：
            ValueError: 当amount为负数或滑点不在范围内时抛出
        """
        if amount < 0:
            raise ValueError(f"卖出金额不能为负数，收到: {amount}")
        if slippage_bps < 0 or slippage_bps > 1000:
            raise ValueError(f"滑点必须在0-1000基点之间，收到: {slippage_bps}")

        if amount == 0:
            return 0.0

        # 佣金
        commission = max(amount * TradingCostCalculator.COMMISSION_RATE,
                        TradingCostCalculator.MIN_COMMISSION)

        # 印花税
        stamp_tax = amount * TradingCostCalculator.STAMP_TAX_RATE

        # 滑点
        slippage = amount * slippage_bps / 10000

        return commission + stamp_tax + slippage

    @staticmethod
    def calculate_round_trip_cost_absolute(
        amount: float,
        slippage_bps: int = 15,
    ) -> float:
        """
        计算单次买卖总成本 (元)

        参数：
            amount: 交易金额 (元)
            slippage_bps: 滑点 (基点数，默认15bp)

        返回：
            单次买卖总成本金额 (元)

        异常：
            ValueError: 当amount为负数或滑点不在范围内时抛出
        """
        if amount < 0:
            raise ValueError(f"交易金额不能为负数，收到: {amount}")
        if slippage_bps < 0 or slippage_bps > 1000:
            raise ValueError(f"滑点必须在0-1000基点之间，收到: {slippage_bps}")

        if amount == 0:
            return 0.0

        buy_cost = TradingCostCalculator.calculate_buy_cost_absolute(amount, slippage_bps)
        sell_cost = TradingCostCalculator.calculate_sell_cost_absolute(amount, slippage_bps)

        return buy_cost + sell_cost
