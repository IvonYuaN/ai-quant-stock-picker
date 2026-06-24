"""A股T+1交易制度约束处理模块。

T+1制度约束：当日买入的股票，当日不能卖出，次日才可卖出。
本模块提供持仓追踪和T+1状态管理功能。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from aqsp.core.time import today_shanghai


class InsufficientSharesError(Exception):
    """可卖数量不足异常"""

    def __init__(self, symbol: str, requested: int, available: int):
        super().__init__(
            f"可卖数量不足: {symbol} 申请卖出 {requested} 股, 实际可卖 {available} 股"
        )
        self.symbol = symbol
        self.requested = requested
        self.available = available


class NegativeSharesError(Exception):
    """负数股份异常"""

    def __init__(self, symbol: str, shares: int):
        super().__init__(f"股份数量不能为负: {symbol} - {shares} 股")
        self.symbol = symbol
        self.shares = shares


class InvalidPriceError(Exception):
    """无效价格异常"""

    def __init__(self, symbol: str, price: float):
        super().__init__(f"价格必须为正数: {symbol} - {price}")
        self.symbol = symbol
        self.price = price


@dataclass
class Position:
    """持仓记录（支持T+1）

    Attributes:
        symbol: 股票代码
        total_shares: 总持仓数量
        available_shares: 可卖持仓数量（T+1解冻后）
        cost_basis: 平均成本价
        last_buy_date: 最后一次买入日期
        buy_history: 买入历史记录 (date, shares, price)
    """

    symbol: str
    total_shares: int
    available_shares: int
    cost_basis: float
    last_buy_date: date
    buy_history: list[tuple[date, int, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """验证持仓数据的合法性"""
        if self.total_shares < 0:
            raise NegativeSharesError(self.symbol, self.total_shares)
        if self.available_shares < 0:
            raise NegativeSharesError(self.symbol, self.available_shares)
        if self.available_shares > self.total_shares:
            raise ValueError(
                f"可卖数量不能超过总持仓: {self.symbol} - "
                f"可卖 {self.available_shares}, 总持 {self.total_shares}"
            )
        if self.cost_basis < 0:
            raise InvalidPriceError(self.symbol, self.cost_basis)

    @property
    def frozen_shares(self) -> int:
        """冻结股数（T+1未解冻）

        Returns:
            T+1冻结的股数，即当日新买入且未解冻的股数
        """
        return self.total_shares - self.available_shares

    @property
    def is_fully_sellable(self) -> bool:
        """是否全部可卖

        Returns:
            True 表示所有持仓都已解冻可卖，False 表示还有冻结股数
        """
        return self.available_shares == self.total_shares

    def add_shares(self, shares: int, price: float, trade_date: date) -> None:
        """增加持仓（买入时调用）

        新买入的股份当日不可卖，会在T+1状态下冻结。

        Args:
            shares: 买入数量（必须为正整数）
            price: 买入价格（必须为正数）
            trade_date: 买入日期

        Raises:
            NegativeSharesError: 买入数量为负或零
            InvalidPriceError: 价格为负或零
        """
        if shares <= 0:
            raise NegativeSharesError(self.symbol, shares)
        if price <= 0:
            raise InvalidPriceError(self.symbol, price)

        # 使用加权平均法计算成本价
        total_value = (self.total_shares * self.cost_basis) + (shares * price)
        new_total = self.total_shares + shares
        self.cost_basis = total_value / new_total

        # 买入数量不立即加入可卖数量（T+1约束）
        self.total_shares = new_total
        self.last_buy_date = trade_date

        # 记录买入历史
        self.buy_history.append((trade_date, shares, price))

    def remove_shares(self, shares: int) -> None:
        """减少可卖持仓（卖出时调用）

        只能卖出已解冻的可卖数量。

        Args:
            shares: 卖出数量（必须为正整数且不超过可卖数量）

        Raises:
            NegativeSharesError: 卖出数量为负或零
            InsufficientSharesError: 可卖数量不足
        """
        if shares <= 0:
            raise NegativeSharesError(self.symbol, shares)
        if shares > self.available_shares:
            raise InsufficientSharesError(self.symbol, shares, self.available_shares)

        self.available_shares -= shares
        self.total_shares -= shares

    def unfreeze_for_date(self, current_date: date) -> None:
        """解冻T+1约束（当日开盘前调用）

        将前一日及更早买入的股份转为可卖状态。

        Args:
            current_date: 当前交易日期
        """
        # 如果今天的日期大于最后买入日期，说明最后买入的已经过了T+1
        if current_date > self.last_buy_date:
            self.available_shares = self.total_shares

    def get_buy_info_for_date(self, target_date: date) -> dict[str, int]:
        """获取指定日期的买入信息

        Args:
            target_date: 目标日期

        Returns:
            包含该日期买入数量和总数的字典
        """
        today_buy = sum(shares for d, shares, _ in self.buy_history if d == target_date)
        return {"today": today_buy, "total": self.total_shares}


@dataclass
class PositionTracker:
    """持仓追踪器（支持T+1）

    管理多只股票的持仓，确保T+1约束得到正确实施。

    Attributes:
        positions: 股票代码 -> 持仓对象的映射
    """

    positions: dict[str, Position] = field(default_factory=dict)

    def add_buy(self, symbol: str, shares: int, price: float, trade_date: date) -> None:
        """记录买入（新买入的shares当日不可卖）

        买入操作会增加总持仓，但新买入的股份在T+1解冻前不能卖出。

        Args:
            symbol: 股票代码
            shares: 买入数量（必须为正整数）
            price: 买入价格（必须为正数）
            trade_date: 买入日期

        Raises:
            NegativeSharesError: 买入数量为负或零
            InvalidPriceError: 价格为负或零
        """
        if shares <= 0:
            raise NegativeSharesError(symbol, shares)
        if price <= 0:
            raise InvalidPriceError(symbol, price)

        if symbol not in self.positions:
            # 创建新持仓
            self.positions[symbol] = Position(
                symbol=symbol,
                total_shares=shares,
                available_shares=0,  # 当日新买不可卖
                cost_basis=price,
                last_buy_date=trade_date,
                buy_history=[(trade_date, shares, price)],
            )
        else:
            # 更新现有持仓
            self.positions[symbol].add_shares(shares, price, trade_date)

    def add_sell(self, symbol: str, shares: int, trade_date: date) -> bool:
        """记录卖出（检查可卖数量，不足则失败）

        卖出操作会检查可卖数量是否足够。只有已解冻的股份才能卖出。

        Args:
            symbol: 股票代码
            shares: 卖出数量（必须为正整数）
            trade_date: 卖出日期（用于未来扩展）

        Returns:
            True 表示卖出成功，False 表示操作失败

        Raises:
            NegativeSharesError: 卖出数量为负或零
            InsufficientSharesError: 可卖数量不足
            KeyError: 持仓不存在
        """
        if shares <= 0:
            raise NegativeSharesError(symbol, shares)

        if symbol not in self.positions:
            raise KeyError(f"没有找到 {symbol} 的持仓")

        position = self.positions[symbol]
        if shares > position.available_shares:
            raise InsufficientSharesError(symbol, shares, position.available_shares)

        position.remove_shares(shares)

        # 如果持仓为0，删除该持仓记录
        if position.total_shares == 0:
            del self.positions[symbol]

        return True

    def update_available_shares(self, current_date: date) -> None:
        """更新可卖数量（每日开盘前调用，解冻T+1）

        此方法应在每日开盘前调用，将前一日及更早买入的股份解冻为可卖状态。

        Args:
            current_date: 当前交易日期
        """
        for position in self.positions.values():
            position.unfreeze_for_date(current_date)

    def get_sellable_shares(self, symbol: str) -> int:
        """获取可卖数量

        Args:
            symbol: 股票代码

        Returns:
            该股票的可卖数量，若持仓不存在则返回0
        """
        return self.positions.get(
            symbol,
            Position(
                symbol=symbol,
                total_shares=0,
                available_shares=0,
                cost_basis=0.0,
                last_buy_date=today_shanghai(),
            ),
        ).available_shares

    def get_total_shares(self, symbol: str) -> int:
        """获取总持仓数量

        Args:
            symbol: 股票代码

        Returns:
            该股票的总持仓数量，若持仓不存在则返回0
        """
        return self.positions.get(
            symbol,
            Position(
                symbol=symbol,
                total_shares=0,
                available_shares=0,
                cost_basis=0.0,
                last_buy_date=today_shanghai(),
            ),
        ).total_shares

    def get_frozen_shares(self, symbol: str) -> int:
        """获取冻结数量（T+1未解冻）

        Args:
            symbol: 股票代码

        Returns:
            该股票的冻结数量，若持仓不存在则返回0
        """
        if symbol not in self.positions:
            return 0
        return self.positions[symbol].frozen_shares

    def can_sell(self, symbol: str, shares: int) -> bool:
        """检查是否可以卖出指定数量

        Args:
            symbol: 股票代码
            shares: 要卖出的数量

        Returns:
            True 表示可以卖出，False 表示不能卖出
        """
        if symbol not in self.positions:
            return False
        return shares > 0 and shares <= self.positions[symbol].available_shares

    def get_position(self, symbol: str) -> Optional[Position]:
        """获取持仓对象

        Args:
            symbol: 股票代码

        Returns:
            持仓对象，若不存在则返回 None
        """
        return self.positions.get(symbol)

    def get_all_positions(self) -> dict[str, Position]:
        """获取所有持仓

        Returns:
            所有持仓的副本字典
        """
        return dict(self.positions)

    def has_position(self, symbol: str) -> bool:
        """检查是否存在某股票的持仓

        Args:
            symbol: 股票代码

        Returns:
            True 表示存在持仓，False 表示不存在
        """
        return symbol in self.positions

    def get_cost_basis(self, symbol: str) -> float:
        """获取某股票的平均成本价

        Args:
            symbol: 股票代码

        Returns:
            平均成本价，若持仓不存在则返回 0.0
        """
        return self.positions.get(
            symbol,
            Position(
                symbol=symbol,
                total_shares=0,
                available_shares=0,
                cost_basis=0.0,
                last_buy_date=today_shanghai(),
            ),
        ).cost_basis
