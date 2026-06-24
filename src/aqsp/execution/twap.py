"""
TWAP (Time-Weighted Average Price) 大单拆分算法

用于将大单拆分成多个小单，避免冲击市场价格。
遵守A股交易规则：
- 单笔最小100股（1手）
- 必须是100的整数倍
- 交易时间：9:30-11:30, 13:00-15:00
- 单笔不超过日均成交量的1%（可配置）
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class Order:
    """单笔订单"""

    symbol: str
    shares: int
    price_limit: Optional[float] = None  # 限价，None表示市价


@dataclass
class TWAPPlan:
    """TWAP拆单计划"""

    orders: list[Order]
    total_shares: int
    interval_seconds: int
    estimated_duration_minutes: int


class TWAPExecutor:
    """TWAP执行器 - 大单拆分"""

    # A股交易规则常数
    MIN_SHARES_PER_ORDER = 100  # 最小100股
    SHARES_UNIT = 100  # 100的整数倍

    def split_order(
        self,
        symbol: str,
        target_shares: int,
        avg_daily_volume: float,
        time_window_minutes: int = 30,
        max_participation_rate: float = 0.01,
        price_limit: Optional[float] = None,
    ) -> TWAPPlan:
        """
        拆分订单为TWAP执行计划

        规则：
        1. 单笔不超过日均成交量的max_participation_rate
        2. 在time_window内均匀分布
        3. 每笔间隔至少1分钟
        4. 每笔必须是100股的整数倍
        5. 每笔不少于100股

        参数：
            symbol: 股票代码
            target_shares: 目标股数（必须是100的倍数）
            avg_daily_volume: 日均成交量（股数）
            time_window_minutes: 拆单时间窗口（分钟），默认30分钟
            max_participation_rate: 最大参与率（单笔占成交量百分比），默认1%
            price_limit: 限价，None表示市价

        返回：
            TWAPPlan: 拆单计划

        异常：
            ValueError: 参数验证失败

        示例：
            >>> executor = TWAPExecutor()
            >>> plan = executor.split_order(
            ...     symbol='000001.SZ',
            ...     target_shares=10000,
            ...     avg_daily_volume=1000000,
            ...     time_window_minutes=30
            ... )
            >>> print(f"拆成{len(plan.orders)}笔订单")
            >>> for i, order in enumerate(plan.orders):
            ...     print(f"第{i+1}笔：{order.shares}股")
        """
        # 1. 参数验证
        self._validate_parameters(
            symbol,
            target_shares,
            avg_daily_volume,
            time_window_minutes,
            max_participation_rate,
        )

        # 2. 计算单笔最大量
        max_single = self._calculate_max_single_order(
            avg_daily_volume, max_participation_rate
        )

        # 3. 计算需要拆成几笔
        num_slices = math.ceil(target_shares / max_single)

        # 4. 计算时间间隔（秒）
        # 确保每笔至少间隔1分钟（60秒）
        time_window_seconds = time_window_minutes * 60
        interval_seconds = max(60, time_window_seconds // num_slices)

        # 5. 生成订单列表
        orders = self._generate_orders(symbol, target_shares, num_slices, price_limit)

        # 6. 计算预计执行时间
        estimated_duration_minutes = math.ceil(
            (len(orders) - 1) * interval_seconds / 60
        )

        return TWAPPlan(
            orders=orders,
            total_shares=target_shares,
            interval_seconds=interval_seconds,
            estimated_duration_minutes=estimated_duration_minutes,
        )

    @staticmethod
    def _validate_parameters(
        symbol: str,
        target_shares: int,
        avg_daily_volume: float,
        time_window_minutes: int,
        max_participation_rate: float,
    ) -> None:
        """验证参数的合法性"""

        # 验证symbol
        if not symbol or not isinstance(symbol, str):
            raise ValueError("symbol必须是非空字符串")

        # 验证target_shares
        if not isinstance(target_shares, int):
            raise ValueError("target_shares必须是整数")
        if target_shares <= 0:
            raise ValueError("target_shares必须大于0")
        if target_shares % 100 != 0:
            raise ValueError("target_shares必须是100的倍数（A股规则）")

        # 验证avg_daily_volume
        if not isinstance(avg_daily_volume, (int, float)):
            raise ValueError("avg_daily_volume必须是数字")
        if avg_daily_volume <= 0:
            raise ValueError("avg_daily_volume必须大于0")

        # 验证time_window_minutes
        if not isinstance(time_window_minutes, int):
            raise ValueError("time_window_minutes必须是整数")
        if time_window_minutes <= 0:
            raise ValueError("time_window_minutes必须大于0")

        # 验证max_participation_rate
        if not isinstance(max_participation_rate, (int, float)):
            raise ValueError("max_participation_rate必须是数字")
        if not (0 < max_participation_rate <= 1):
            raise ValueError("max_participation_rate必须在(0, 1]之间")

    @staticmethod
    def _calculate_max_single_order(
        avg_daily_volume: float,
        max_participation_rate: float,
    ) -> int:
        """
        计算单笔最大订单量

        规则：单笔 = 日均成交量 × 最大参与率，然后四舍五入到100的倍数
        """
        max_single = int(avg_daily_volume * max_participation_rate)

        # 四舍五入到100的倍数
        max_single = (max_single // 100) * 100

        # 至少100股
        max_single = max(max_single, 100)

        return max_single

    @staticmethod
    def _generate_orders(
        symbol: str,
        target_shares: int,
        num_slices: int,
        price_limit: Optional[float] = None,
    ) -> list[Order]:
        """
        生成订单列表

        均匀分配：前num_slices-1笔平均分，最后一笔补齐
        """
        orders = []
        shares_per_order = target_shares // num_slices

        # 对齐到100的倍数
        shares_per_order = (shares_per_order // 100) * 100

        remaining_shares = target_shares

        for i in range(num_slices - 1):
            order = Order(
                symbol=symbol,
                shares=shares_per_order,
                price_limit=price_limit,
            )
            orders.append(order)
            remaining_shares -= shares_per_order

        # 最后一笔：补齐剩余股数
        if remaining_shares > 0:
            order = Order(
                symbol=symbol,
                shares=remaining_shares,
                price_limit=price_limit,
            )
            orders.append(order)

        return orders

    def calculate_participation_rate(
        self,
        order_shares: int,
        avg_daily_volume: float,
    ) -> float:
        """
        计算订单的参与率（占日均成交量的百分比）

        参数：
            order_shares: 订单股数
            avg_daily_volume: 日均成交量

        返回：
            参与率 (%)
        """
        if avg_daily_volume <= 0:
            raise ValueError("avg_daily_volume必须大于0")

        return (order_shares / avg_daily_volume) * 100

    def validate_twap_plan(
        self,
        plan: TWAPPlan,
        avg_daily_volume: float,
        max_participation_rate: float = 0.01,
    ) -> dict:
        """
        验证TWAP计划的合法性

        检查项：
        1. 订单总数与总股数一致
        2. 每笔订单都是100的倍数
        3. 每笔订单不超过参与率限制
        4. 时间间隔合理

        参数：
            plan: TWAP计划
            avg_daily_volume: 日均成交量
            max_participation_rate: 最大参与率

        返回：
            验证结果字典，包含：
            - valid: 是否有效
            - errors: 错误列表
            - warnings: 警告列表
        """
        errors = []
        warnings = []

        # 检查订单总数
        total_shares = sum(order.shares for order in plan.orders)
        if total_shares != plan.total_shares:
            errors.append(f"订单总股数{total_shares}与目标股数{plan.total_shares}不符")

        # 检查每笔订单
        for i, order in enumerate(plan.orders):
            # 检查是否是100的倍数
            if order.shares % 100 != 0:
                errors.append(f"第{i + 1}笔订单({order.shares}股)不是100的倍数")

            # 检查最小股数
            if order.shares < 100:
                errors.append(f"第{i + 1}笔订单({order.shares}股)少于最小100股")

            # 检查参与率
            participation_rate = self.calculate_participation_rate(
                order.shares, avg_daily_volume
            )
            if participation_rate > max_participation_rate * 100:
                warnings.append(
                    f"第{i + 1}笔订单参与率{participation_rate:.2f}%"
                    f"超过限制{max_participation_rate * 100:.2f}%"
                )

        # 检查时间间隔
        if plan.interval_seconds < 60:
            warnings.append(f"时间间隔{plan.interval_seconds}秒少于建议的60秒")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }
