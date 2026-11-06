# 策略健康度诊断接口（非运行集成）

> 本文只说明 `aqsp.monitor.strategy_health` 的遗留诊断 API。它没有生产调用者，不是策略生命周期、启停或调权方案；其中的 30 日窗口和健康阈值均为模块内历史启发式，不能作为策略晋级依据。

## 当前边界

- `StrategyHealthMonitor` 可对调用方传入的 `Trade` 列表生成观察状态和描述性统计，不读取正式 ledger、PIT 样本或 walk-forward 结果。
- `WARNING`、`UNHEALTHY` 是该诊断器的标签，不代表正式策略状态；无近期交易也会标为 `UNHEALTHY`，不得解释为策略已被验证失效。
- 本模块不调整阈值、权重、评分、策略目录状态或运行开关。已移除的自动调权逻辑不得恢复。
- 新的研究生命周期以 [`tickflow-aqsp-integration.md`](tickflow-aqsp-integration.md) §5 阶段 E 和 [`architecture.md`](architecture.md) §7.2 为准：假设/版本、PIT/OOS 证据、人工晋级、冻结运行、漂移观察、人工退役。

## 可用接口

```python
from aqsp.monitor.strategy_health import StrategyHealthMonitor, Trade

monitor = StrategyHealthMonitor()
metrics = monitor.get_strategy_metrics("example", trades, lookback_days=30)
status = monitor.check_strategy_health("example", trades, lookback_days=30)
```

调用方必须自行保证输入交易的来源、价格口径、时点和策略版本。接口结果仅供诊断展示；不应从该结果执行策略动作。生产级策略状态仍以冻结配置和可追溯的 PIT/OOS 验证、人工审计记录为准。

## 后续收敛

该模块目前仅有库内实现与单元测试，没有 API、dashboard 或后台任务调用者。若后续生命周期工作确认其指标已被正式证据模型替代，应按调用面、兼容性与历史测试审计后再决定迁移或退役；不得仅凭当前无调用点直接删除。
