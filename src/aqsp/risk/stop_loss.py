"""止损管理模块 - 实现单只股票、组合和移动止损三层止损机制。

止损哲学：
- 保守优先：宁可不触发，不要误触发
- 多重确认：触发前多检查
- 明确日志：清晰记录触发原因
"""

from __future__ import annotations

from dataclasses import dataclass


# 浮点数比较精度容差
_EPSILON = 1e-9


@dataclass
class StopLossConfig:
    """止损配置。
    
    Attributes:
        single_stock_stop: 单只股票止损阈值（负数，如-0.08表示-8%）
        portfolio_stop: 组合整体止损阈值（负数，如-0.15表示-15%）
        trailing_stop_pct: 移动止损回撤百分比（正数，如0.05表示5%）
        enable_trailing: 是否启用移动止损
    """
    single_stock_stop: float = -0.08  # 单只-8%
    portfolio_stop: float = -0.15     # 组合-15%
    trailing_stop_pct: float = 0.05   # 移动止损5%
    enable_trailing: bool = True
    
    def __post_init__(self) -> None:
        """验证配置的合理性。"""
        if self.single_stock_stop >= 0:
            raise ValueError(
                f"single_stock_stop必须为负数，当前值: {self.single_stock_stop}"
            )
        if self.portfolio_stop >= 0:
            raise ValueError(
                f"portfolio_stop必须为负数，当前值: {self.portfolio_stop}"
            )
        if not 0 < self.trailing_stop_pct < 1:
            raise ValueError(
                f"trailing_stop_pct必须在0-1之间，当前值: {self.trailing_stop_pct}"
            )


@dataclass
class Position:
    """持仓位置信息。
    
    Attributes:
        symbol: 股票代码
        shares: 持仓数量
        cost_basis: 成本价
        high_water_mark: 最高价（用于移动止损），默认等于成本价
    """
    symbol: str
    shares: int
    cost_basis: float
    high_water_mark: float | None = None
    
    def __post_init__(self) -> None:
        """初始化最高价为成本价。"""
        if self.shares < 0:
            raise ValueError(f"shares必须为非负数，当前值: {self.shares}")
        if self.cost_basis <= 0:
            raise ValueError(f"cost_basis必须为正数，当前值: {self.cost_basis}")
        if self.high_water_mark is None:
            object.__setattr__(self, "high_water_mark", self.cost_basis)
        if self.high_water_mark <= 0:
            raise ValueError(f"high_water_mark必须为正数，当前值: {self.high_water_mark}")


@dataclass
class StopLossCheckResult:
    """止损检查结果。
    
    Attributes:
        triggered: 是否触发止损
        reason: 触发原因（如果触发）
        pnl_pct: 损益百分比
        loss_amount: 损失金额（绝对值）
    """
    triggered: bool
    reason: str = ""
    pnl_pct: float = 0.0
    loss_amount: float = 0.0


@dataclass
class TrailingStopUpdate:
    """移动止损更新结果。
    
    Attributes:
        symbol: 股票代码
        new_high_water_mark: 新的最高价
        updated: 是否更新了最高价
        current_price: 当前价格
    """
    symbol: str
    new_high_water_mark: float
    updated: bool
    current_price: float


class StopLossManager:
    """止损管理器 - 实现多层次止损机制。
    
    职责：
    - 检查单只股票止损
    - 检查组合整体止损
    - 更新和管理移动止损位
    - 保证止损逻辑的保守性和准确性
    """
    
    def __init__(self, config: StopLossConfig | None = None) -> None:
        """初始化止损管理器。
        
        Args:
            config: 止损配置，默认使用StopLossConfig()
        """
        self.config = config or StopLossConfig()
    
    def _compare_loss(self, pnl_pct: float, threshold: float) -> bool:
        """比较损失百分比是否达到阈值（考虑浮点数精度）。
        
        Args:
            pnl_pct: 损益百分比（通常为负数）
            threshold: 阈值（通常为负数，如-0.08）
            
        Returns:
            bool: 是否达到或超过阈值（更多亏损）
        """
        return pnl_pct < threshold + _EPSILON
    
    def check_single_stock_stop(
        self,
        position: Position,
        current_price: float,
    ) -> StopLossCheckResult:
        """检查单只股票是否触发止损。
        
        保守策略：
        - 只在亏损超过阈值时触发
        - 盈利状态绝不触发
        - 小幅亏损（0到阈值之间）不触发
        
        Args:
            position: 持仓位置
            current_price: 当前价格
            
        Returns:
            StopLossCheckResult: 检查结果
            
        Raises:
            ValueError: 如果current_price <= 0
        """
        if current_price <= 0:
            raise ValueError(f"current_price必须为正数，当前值: {current_price}")
        
        # 计算损益百分比
        pnl_pct = (current_price - position.cost_basis) / position.cost_basis
        
        # 保守检查：只在明确亏损且超过阈值时触发
        # 使用 _compare_loss 处理浮点数精度问题
        if self._compare_loss(pnl_pct, self.config.single_stock_stop):
            loss_amount = position.shares * (position.cost_basis - current_price)
            return StopLossCheckResult(
                triggered=True,
                reason=(
                    f"单只股票止损触发: {position.symbol} "
                    f"亏损{pnl_pct:.2%} < {self.config.single_stock_stop:.2%}"
                ),
                pnl_pct=pnl_pct,
                loss_amount=loss_amount,
            )
        
        return StopLossCheckResult(
            triggered=False,
            pnl_pct=pnl_pct,
            loss_amount=max(0, position.shares * (position.cost_basis - current_price)),
        )
    
    def check_single_stock_stops(
        self,
        positions: list[Position],
        current_prices: dict[str, float],
    ) -> list[str]:
        """检查所有持仓中需要止损的标的。
        
        Args:
            positions: 持仓列表
            current_prices: 当前价格字典 {symbol: price}
            
        Returns:
            list[str]: 需要止损的标的代码列表
            
        Raises:
            ValueError: 如果有持仓缺少价格数据
        """
        stop_loss_symbols: list[str] = []
        
        for position in positions:
            if position.symbol not in current_prices:
                raise ValueError(
                    f"缺少持仓 {position.symbol} 的价格数据"
                )
            
            current_price = current_prices[position.symbol]
            result = self.check_single_stock_stop(position, current_price)
            
            if result.triggered:
                stop_loss_symbols.append(position.symbol)
        
        return stop_loss_symbols
    
    def check_portfolio_stop(
        self,
        portfolio_value: float,
        initial_value: float,
    ) -> StopLossCheckResult:
        """检查组合整体是否触发止损。
        
        保守策略：
        - 只在组合亏损超过阈值时触发
        - 初始值为0或负数时不触发（无效输入）
        - portfolio_value小于等于0时不触发（异常情况）
        
        Args:
            portfolio_value: 当前组合总市值
            initial_value: 初始投资金额
            
        Returns:
            StopLossCheckResult: 检查结果
            
        Raises:
            ValueError: 如果initial_value <= 0
        """
        if initial_value <= 0:
            raise ValueError(
                f"initial_value必须为正数，当前值: {initial_value}"
            )
        
        if portfolio_value <= 0:
            # 异常情况：组合被完全清空，返回未触发
            # 实际应由其他机制处理
            return StopLossCheckResult(
                triggered=False,
                reason="组合价值异常（<=0），停止止损检查",
                pnl_pct=0.0,
            )
        
        # 计算组合损益百分比
        pnl_pct = (portfolio_value - initial_value) / initial_value
        
        # 保守检查：只在明确亏损且超过阈值时触发
        if self._compare_loss(pnl_pct, self.config.portfolio_stop):
            loss_amount = initial_value - portfolio_value
            return StopLossCheckResult(
                triggered=True,
                reason=(
                    f"组合止损触发: 组合亏损{pnl_pct:.2%} < {self.config.portfolio_stop:.2%}"
                ),
                pnl_pct=pnl_pct,
                loss_amount=loss_amount,
            )
        
        return StopLossCheckResult(
            triggered=False,
            pnl_pct=pnl_pct,
            loss_amount=max(0, initial_value - portfolio_value),
        )
    
    def update_trailing_stops(
        self,
        positions: list[Position],
        current_prices: dict[str, float],
    ) -> dict[str, TrailingStopUpdate]:
        """更新移动止损位。
        
        规则：
        - 移动止损只升不降
        - 当前价格 > 最高价时，更新最高价
        - 只在启用移动止损时执行更新
        
        Args:
            positions: 持仓列表
            current_prices: 当前价格字典 {symbol: price}
            
        Returns:
            dict[str, TrailingStopUpdate]: 更新结果 {symbol: TrailingStopUpdate}
            
        Raises:
            ValueError: 如果有持仓缺少价格数据
        """
        if not self.config.enable_trailing:
            return {}
        
        updates: dict[str, TrailingStopUpdate] = {}
        
        for position in positions:
            if position.symbol not in current_prices:
                raise ValueError(
                    f"缺少持仓 {position.symbol} 的价格数据"
                )
            
            current_price = current_prices[position.symbol]
            
            # 只升不降：新高价 > 旧高价
            if current_price > position.high_water_mark:
                updated = True
                new_high = current_price
            else:
                updated = False
                new_high = position.high_water_mark
            
            updates[position.symbol] = TrailingStopUpdate(
                symbol=position.symbol,
                new_high_water_mark=new_high,
                updated=updated,
                current_price=current_price,
            )
        
        return updates
    
    def check_trailing_stop_trigger(
        self,
        position: Position,
        current_price: float,
    ) -> StopLossCheckResult:
        """检查移动止损是否触发。
        
        规则：
        - 从最高点回撤超过 trailing_stop_pct 时触发
        - 必须启用移动止损才会触发
        - 当前价格 < 最高价 * (1 - trailing_stop_pct) 时触发
        
        Args:
            position: 持仓位置
            current_price: 当前价格
            
        Returns:
            StopLossCheckResult: 检查结果
            
        Raises:
            ValueError: 如果current_price <= 0
        """
        if not self.config.enable_trailing:
            return StopLossCheckResult(triggered=False)
        
        if current_price <= 0:
            raise ValueError(f"current_price必须为正数，当前值: {current_price}")
        
        stop_price = position.high_water_mark * (1 - self.config.trailing_stop_pct)
        
        # 保守检查：当前价格必须跌破止损价才触发
        if current_price < stop_price - _EPSILON:
            pnl_pct = (current_price - position.cost_basis) / position.cost_basis
            loss_amount = position.shares * (position.cost_basis - current_price)
            
            return StopLossCheckResult(
                triggered=True,
                reason=(
                    f"移动止损触发: {position.symbol} "
                    f"从最高{position.high_water_mark:.2f}回撤超过"
                    f"{self.config.trailing_stop_pct:.2%}至{current_price:.2f}"
                ),
                pnl_pct=pnl_pct,
                loss_amount=loss_amount,
            )
        
        return StopLossCheckResult(triggered=False)
    
    def get_stop_price(
        self,
        position: Position,
    ) -> float:
        """获取移动止损价格。
        
        Args:
            position: 持仓位置
            
        Returns:
            float: 移动止损价格
        """
        if not self.config.enable_trailing:
            return 0.0
        
        return position.high_water_mark * (1 - self.config.trailing_stop_pct)
    
    def validate_positions(
        self,
        positions: list[Position],
        current_prices: dict[str, float],
    ) -> tuple[bool, list[str]]:
        """验证持仓数据的完整性和一致性。
        
        检查项：
        - 所有持仓都有对应的价格
        - 所有价格都是正数
        - 所有持仓的数据有效
        
        Args:
            positions: 持仓列表
            current_prices: 当前价格字典
            
        Returns:
            tuple[bool, list[str]]: (是否有效, 错误信息列表)
        """
        errors: list[str] = []
        
        for position in positions:
            if position.symbol not in current_prices:
                errors.append(f"缺少持仓 {position.symbol} 的价格")
            elif current_prices[position.symbol] <= 0:
                errors.append(
                    f"持仓 {position.symbol} 的价格非法: "
                    f"{current_prices[position.symbol]}"
                )
        
        return len(errors) == 0, errors
