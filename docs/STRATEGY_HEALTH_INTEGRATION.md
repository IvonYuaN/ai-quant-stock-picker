# 策略健康度监控集成指南

## 概述

`StrategyHealthMonitor` 提供实时策略表现监控，自动检测失效策略并进行权重调整。

## 核心功能

### 1. 健康度检查 (check_strategy_health)

```python
from aqsp.monitor.strategy_health import StrategyHealthMonitor, Trade, HealthStatus
from datetime import date, timedelta

monitor = StrategyHealthMonitor()

# 创建交易记录
trades = [
    Trade(
        symbol="000001",
        strategy="momentum",
        entry_date=date(2026, 5, 20),
        exit_date=date(2026, 5, 25),
        entry_price=100.0,
        exit_price=105.0,
        shares=100,
        pnl=500.0,
        return_pct=0.05,
    ),
    # ... 更多交易
]

# 检查策略健康度（回溯30天）
status = monitor.check_strategy_health("momentum", trades, lookback_days=30)

if status == HealthStatus.HEALTHY:
    print("策略正常")
elif status == HealthStatus.WARNING:
    print("策略预警，建议降权50%")
else:  # UNHEALTHY
    print("策略失效，建议停用")
```

### 2. 健康度判断标准

#### UNHEALTHY（停用）
- 胜率 < 40%
- 夏普比率 < 0
- 连续亏损 > 5次

#### WARNING（降权50%）
- 胜率 44-45%
- 夏普比率 0-0.5
- 连续亏损 3-5次

#### HEALTHY（正常）
- 胜率 ≥ 45%
- 夏普比率 ≥ 0.5
- 连续亏损 ≤ 3次

### 3. 获取策略指标 (get_strategy_metrics)

```python
metrics = monitor.get_strategy_metrics("momentum", trades, lookback_days=30)

print(f"策略: {metrics.name}")
print(f"总交易数: {metrics.total_trades}")
print(f"盈利交易: {metrics.winning_trades}")
print(f"胜率: {metrics.win_rate:.1%}")
print(f"夏普比率: {metrics.sharpe_ratio:.2f}")
print(f"最大回撤: {metrics.max_drawdown:.1%}")
print(f"平均收益: {metrics.avg_return:.1%}")
```

### 4. 自动权重调整 (auto_adjust_weights)

```python
# 当前权重配置
current_weights = {
    "momentum": 0.30,
    "value": 0.25,
    "quality": 0.20,
    "technical": 0.15,
    "volume": 0.10,
}

# 策略交易数据
strategies_trades = {
    "momentum": momentum_trades,
    "value": value_trades,
    "quality": quality_trades,
    "technical": technical_trades,
    "volume": volume_trades,
}

# 自动调整权重
adjusted_weights = monitor.auto_adjust_weights(
    current_weights,
    strategies_trades=strategies_trades,
    lookback_days=30,
)

# 调整后的权重会自动归一化
print(adjusted_weights)
# 可能输出: {
#     "momentum": 0.0,      # UNHEALTHY，权重为0
#     "value": 0.255,       # WARNING，权重减半后归一化
#     "quality": 0.255,     # HEALTHY，权重保持
#     "technical": 0.245,   # HEALTHY，权重保持
#     "volume": 0.245,      # HEALTHY，权重保持
# }
```

## 集成到 Briefing Generator

在 `src/aqsp/briefing/generator.py` 中集成策略健康度监控：

```python
from aqsp.monitor.strategy_health import StrategyHealthMonitor, HealthStatus

class BriefingGenerator:
    def generate(self, picks, frames, regime="", ...):
        # ... 现有逻辑 ...

        # 添加策略健康度监控
        monitor = StrategyHealthMonitor()
        strategies_trades = self._collect_strategy_trades()  # 收集策略交易数据

        strategy_statuses = monitor.get_all_strategies_status(
            strategies_trades,
            lookback_days=30,
        )

        # 生成警告信息
        health_warnings = []
        for strategy_name, status in strategy_statuses.items():
            if status != HealthStatus.HEALTHY:
                health_warnings.append(
                    f"{strategy_name}: {status.value}（建议处理）"
                )

        # 建议权重调整
        current_weights = self._get_current_strategy_weights()
        adjusted_weights = monitor.auto_adjust_weights(
            current_weights,
            strategies_trades=strategies_trades,
        )

        # 添加到briefing中
        strategy_health_section = self._build_strategy_health_section(
            strategy_statuses,
            health_warnings,
            adjusted_weights,
        )

        sections.insert(1, strategy_health_section)  # 在市况后面添加

        return Briefing(
            date=date_str,
            sections=sections,
            picks=ordered_picks,
            # ...
        )

    def _build_strategy_health_section(self, statuses, warnings, adjusted_weights):
        """构建策略健康度章节"""
        lines = []

        if warnings:
            lines.append("### 策略健康度预警")
            for warning in warnings:
                lines.append(f"- {warning}")
            lines.append("")

        lines.append("### 策略状态汇总")
        for strategy_name, status in sorted(statuses.items()):
            icon = "✓" if status == HealthStatus.HEALTHY else "⚠" if status == HealthStatus.WARNING else "✗"
            lines.append(f"- {icon} {strategy_name}: {status.value}")

        if adjusted_weights:
            lines.append("")
            lines.append("### 建议权重调整")
            for strategy_name, weight in sorted(adjusted_weights.items(), key=lambda x: -x[1]):
                if weight > 0:
                    lines.append(f"- {strategy_name}: {weight:.0%}")

        return BriefingSection(
            title="策略健康度",
            content="\n".join(lines),
        )
```

## 数据收集建议

### 从交易日志收集交易数据

```python
from aqsp.audit.trade_logger import TradeLogger
from aqsp.core.time import now_shanghai
from aqsp.monitor.strategy_health import Trade
from datetime import timedelta

def collect_strategy_trades() -> dict[str, list[Trade]]:
    """从交易日志收集策略交易数据"""
    logger = TradeLogger()

    # 查询最近30天的交易
    start_date = now_shanghai() - timedelta(days=30)
    execution_logs = logger.query_executions(
        start_date=start_date,
        symbol=None,
    )

    # 按策略分组
    strategies_trades = {}
    for log in execution_logs:
        strategy = log.strategy  # 从日志中获取策略名
        if strategy not in strategies_trades:
            strategies_trades[strategy] = []

        # 转换为Trade对象
        trade = Trade(
            symbol=log.symbol,
            strategy=strategy,
            entry_date=log.entry_date,
            exit_date=log.exit_date,
            entry_price=log.entry_price,
            exit_price=log.exit_price,
            shares=log.shares,
            pnl=log.pnl,
            return_pct=log.return_pct,
        )
        strategies_trades[strategy].append(trade)

    return strategies_trades
```

### 从回测结果收集交易数据

```python
from aqsp.backtest.walk_forward import BacktestResult, TradeResult

def trades_from_backtest_result(result: BacktestResult) -> list[Trade]:
    """从回测结果提取交易数据"""
    trades = []
    for trade_result in result.trades:
        trade = Trade(
            symbol=trade_result.symbol,
            strategy=result.strategy_name,
            entry_date=parse_date(trade_result.entry_date),
            exit_date=parse_date(trade_result.exit_date),
            entry_price=trade_result.entry_price,
            exit_price=trade_result.exit_price,
            shares=trade_result.shares,
            pnl=trade_result.pnl,
            return_pct=trade_result.return_pct,
        )
        trades.append(trade)
    return trades
```

## 监控指标解释

### 胜率 (Win Rate)
- 定义：盈利交易数 / 总交易数
- 含义：策略在该周期内的赚钱概率
- 健康标准：> 45%

### 夏普比率 (Sharpe Ratio)
- 定义：(平均收益 - 无风险利率) / 收益标准差 * sqrt(252)
- 含义：每单位风险的收益（风险调整后收益）
- 健康标准：> 0.5（年化）

### 最大回撤 (Max Drawdown)
- 定义：从峰值到谷值的最大下跌幅度
- 含义：策略最坏的风险暴露
- 监控用途：识别风险事件

### 连续亏损 (Consecutive Losses)
- 定义：最长的连续亏损交易数
- 含义：策略的最差连续表现
- 预警标准：> 3次为预警，> 5次为失效

## 最佳实践

1. **定期监控**：每日运行一次健康度检查
2. **历史对比**：与基准收益率对比，不仅看绝对值
3. **多维度评估**：不依赖单一指标，综合考虑多个维度
4. **防止过度反应**：避免因单个失效交易就停用策略
5. **渐进式调整**：采用WARNING预警而不是立即停用
6. **定期回顾**：月度回顾失效策略，识别问题根源

## 故障排除

### 问题：所有策略都显示UNHEALTHY
可能原因：
- 市场整体不利
- 数据源异常
- 入场信号有问题

解决方案：
- 检查市场regime是否发生变化
- 验证数据源完整性
- 审视策略的市场假设

### 问题：权重调整后总和不为1
原因：正常的floating point精度问题

解决方案：
```python
# 手动归一化
total = sum(adjusted_weights.values())
normalized = {k: v/total for k, v in adjusted_weights.items()}
```

## 扩展方向

1. **动态阈值**：基于市场regime调整判断阈值
2. **集成因子分析**：识别策略失效的具体原因
3. **恢复机制**：停用后的自动重启逻辑
4. **多时间框架**：同时监控日线、周线、月线表现
5. **跨策略相关性**：检测策略间的风险集中
