"""
执行协调器 - 统一管理交易执行策略

整合TWAP拆单和交易成本计算，提供完整的执行方案。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .cost import TradingCostCalculator
from .twap import TWAPExecutor, TWAPPlan


@dataclass
class ExecutionPlan:
    """完整执行计划"""
    twap_plan: TWAPPlan
    total_commission: float           # 总佣金（元）
    total_stamp_tax: float            # 总印花税（元）
    total_slippage: float             # 总滑点（元）
    estimated_total_cost: float       # 预计总成本（元）
    estimated_cost_rate: float        # 预计成本率（%）
    is_valid: bool                    # 是否有效
    validation_errors: list[str]      # 验证错误


class ExecutionCoordinator:
    """执行协调器"""

    def __init__(self):
        self.twap_executor = TWAPExecutor()
        self.cost_calculator = TradingCostCalculator()

    def plan_execution(
        self,
        symbol: str,
        target_shares: int,
        avg_daily_volume: float,
        estimated_price: float,
        time_window_minutes: int = 30,
        max_participation_rate: float = 0.01,
        slippage_bps: int = 15,
        is_sell: bool = False,
        price_limit: Optional[float] = None,
    ) -> ExecutionPlan:
        """
        规划完整的执行方案

        包括拆单计划和成本估算

        参数：
            symbol: 股票代码
            target_shares: 目标股数（必须是100的倍数）
            avg_daily_volume: 日均成交量（股数）
            estimated_price: 预计成交价格（元）
            time_window_minutes: 拆单时间窗口（分钟），默认30分钟
            max_participation_rate: 最大参与率，默认1%
            slippage_bps: 滑点（基点），默认15bp
            is_sell: 是否卖出订单（买入为False，卖出为True）
            price_limit: 限价，None表示市价

        返回：
            ExecutionPlan: 完整执行计划

        示例：
            >>> coordinator = ExecutionCoordinator()
            >>> plan = coordinator.plan_execution(
            ...     symbol='000001.SZ',
            ...     target_shares=10000,
            ...     avg_daily_volume=1000000,
            ...     estimated_price=15.5,
            ...     time_window_minutes=30,
            ...     is_sell=False
            ... )
            >>> print(f"预计成本：{plan.estimated_total_cost:.2f}元")
            >>> print(f"成本率：{plan.estimated_cost_rate:.4f}%")
        """
        # 1. 生成TWAP拆单计划
        twap_plan = self.twap_executor.split_order(
            symbol=symbol,
            target_shares=target_shares,
            avg_daily_volume=avg_daily_volume,
            time_window_minutes=time_window_minutes,
            max_participation_rate=max_participation_rate,
            price_limit=price_limit,
        )

        # 2. 验证TWAP计划
        validation = self.twap_executor.validate_twap_plan(
            twap_plan, avg_daily_volume, max_participation_rate
        )

        # 3. 计算成本
        total_amount = target_shares * estimated_price

        if is_sell:
            total_commission = self.cost_calculator.calculate_sell_cost_absolute(
                total_amount, slippage_bps
            )
            # 卖出时的成本计算
            commission_only = max(
                total_amount * self.cost_calculator.COMMISSION_RATE,
                self.cost_calculator.MIN_COMMISSION,
            )
            stamp_tax = total_amount * self.cost_calculator.STAMP_TAX_RATE
            slippage = total_amount * slippage_bps / 10000
        else:
            total_commission = self.cost_calculator.calculate_buy_cost_absolute(
                total_amount, slippage_bps
            )
            # 买入时的成本计算
            commission_only = max(
                total_amount * self.cost_calculator.COMMISSION_RATE,
                self.cost_calculator.MIN_COMMISSION,
            )
            stamp_tax = 0.0
            slippage = total_amount * slippage_bps / 10000

        total_cost = total_commission
        cost_rate = (total_cost / total_amount * 100) if total_amount > 0 else 0.0

        return ExecutionPlan(
            twap_plan=twap_plan,
            total_commission=commission_only,
            total_stamp_tax=stamp_tax,
            total_slippage=slippage,
            estimated_total_cost=total_cost,
            estimated_cost_rate=cost_rate,
            is_valid=validation["valid"],
            validation_errors=validation["errors"],
        )

    def compare_execution_plans(
        self,
        plans: list[ExecutionPlan],
    ) -> dict:
        """
        比较多个执行计划

        参数：
            plans: 执行计划列表

        返回：
            比较结果，包括最优方案
        """
        if not plans:
            return {"error": "执行计划列表为空"}

        # 按成本率排序
        sorted_plans = sorted(
            plans, key=lambda p: p.estimated_cost_rate
        )

        return {
            "best_plan": sorted_plans[0],
            "worst_plan": sorted_plans[-1],
            "avg_cost_rate": sum(p.estimated_cost_rate for p in plans) / len(plans),
            "all_plans": sorted_plans,
        }

    def simulate_execution(
        self,
        plan: ExecutionPlan,
        actual_prices: Optional[list[float]] = None,
    ) -> dict:
        """
        模拟执行并计算实际成本

        参数：
            plan: 执行计划
            actual_prices: 实际成交价格列表（可选），如果不提供则假设按预计价格成交

        返回：
            模拟结果
        """
        twap_plan = plan.twap_plan

        if actual_prices is None:
            # 假设所有订单都以相同价格成交
            actual_prices = [None] * len(twap_plan.orders)

        results = []
        total_amount = 0

        for i, order in enumerate(twap_plan.orders):
            # 这里可以扩展为更复杂的模拟逻辑
            results.append({
                "order_index": i + 1,
                "shares": order.shares,
                "price": actual_prices[i] if i < len(actual_prices) else None,
                "symbol": order.symbol,
            })
            total_amount += order.shares

        return {
            "orders": results,
            "total_shares": total_amount,
            "plan_validity": plan.is_valid,
        }
