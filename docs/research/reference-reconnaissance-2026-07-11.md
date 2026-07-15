# AQSP 全量参考扫描 - 2026-07-11

本文件记录系统收敛前的外部证据，不替代 `docs/architecture.md` 的规划地位。后续进入实现前，必须将已批准的工作拆入 architecture 的 PR 顺序。

## 扫描范围

- 本地 `repo-scout` 索引：296 个项目；281 个可读取源码归档，15 个仅有元数据。
- 当前 GitHub：按多 Agent、决策审计、A 股筛选、资讯 RSS、量化研究等主题重新检索；匿名 API 随后触发限流，未核验的源码一律不作为复制依据。
- 所有筛选都遵守 AQSP 边界：不自动下单、LLM 不改写确定性评分、实时数据不得由历史源冒充、外部代码必须许可兼容。

## 结论

AQSP 不缺新的大平台或更多“投资人格”。当前最缺的是能证明一次短线研究为什么可信、何时失效、输入是否新鲜、讨论是否有证据、历史验证是否可复现的基础契约。

运行时只保留一条轻量主链：

```text
实时行情/资讯 -> 新鲜度与来源健康 -> 确定性候选评分
-> 跨市/消息上下文 -> 有证据的规则视角复核 -> 纸面研究卡/审计记录
```

历史数据只走：

```text
PIT 数据 -> 回测 manifest -> Purged/CPCV/DSR/PBO -> 冻结阈值 proposal
```

## 可吸收底座

| 优先级 | 参考项目 | 最小可吸收能力 | AQSP 落点 | 禁止吸收 |
| --- | --- | --- | --- | --- |
| P0 | `HKUDS/Vibe-Trading` (MIT) | 研究任务状态、输入/输出工件关系 | `research.run_registry` | 交易/MCP 平台 |
| P0 | `mnemox-ai/tradememory-protocol` (MIT) | append-only 决策哈希链、每日 root、完整性校验 | `audit.decision_chain` | MT5/Binance/live executor |
| P0 | `monarchjuno/tradingcodex` (Apache-2.0) | DecisionPackage、接受/退回/阻塞状态、失效条件 | `briefing.decision_package` | 订单、broker、Django/MCP 执行面 |
| P0 | `OpenBB` (模型契约) | 新闻原子记录统一字段 | `news.models` | OpenBB 运行时依赖 |
| P1 | `FinceptTerminal` (仅理念，AGPL 不复制) | 事件簇、来源数、传播速度、信息拥挤度 | `news.clustering`、`news.baseline` | 其源码、预测市场与终端 |
| P1 | `china-finance-rss` (MIT) | feed 级健康、空结果、失败原因 | `data.news_source` health 输出 | 常驻 RSS 服务 |
| P1 | `simonlin1212/TradingAgents-astock` (Apache-2.0) | A 股证据输入、质量门、可恢复讨论状态 | `briefing` advisory contract | LLM trader 最终结论 |
| P1 | `wangpage/quant-ashare` (MIT) | 涨停封板/炸板/连板等市场状态特征 | `research.limit_up_market_context` | 直接加分或自动交易 |
| P1 | `ling-0729/KHunter` (MIT) | 涨停后横盘再加速、突破后支撑确认 | shadow 策略/研究特征 | 未验证即接 runtime |
| P1 | `Freqtrade` (测试语义) | 全样本与截断样本前缀不变性检查 | `backtest` 防泄漏测试 | 交易机器人/Optuna runtime |
| P1 | `Qlib`、`mlfinlab` | PIT、Purged/Embargo、实验记录 | `backtest.manifest`、splitter | Qlib/MLflow 重型基座 |
| P1 | `PyPortfolioOpt`、`Riskfolio-Lib` | PSD/收缩协方差、尾部风险、风险贡献 | 纯函数 risk metrics | cvxpy 优化器 runtime |

## 已有但需要收敛的能力

- `artifact metadata`、候选上下文卡、PIT fail-loud、策略来源目录、多 Agent 讨论已经存在，不应再建第二套。
- `n_rebound` 已覆盖外部 N 字反弹的确定性形态；外部 `N-Rebound` 无许可证且含自动模拟交易，不再复制。
- 跨市规则、RSS、催化归因和实时源回退已在主链中；新增重点是来源原子化、事件簇与健康观测，不是继续加抓取器。
- 9 个角色的讨论框架已存在；新增重点是证据质量、覆盖率、任务恢复与建议状态，不是继续增加人格。

## 明确拒绝

- 所有 broker、订单、自动买卖、预测市场执行、交易所/券商客户端。
- AGPL/GPL 或无许可证项目的源码复制；只可保留独立理论假设和测试案例。
- OpenBB、Qlib、Lean、vectorbt、Riskfolio、cvxportfolio、FinceptTerminal 等重型运行时依赖。
- LLM/Agent 产生或重排 `PickResult.score` 的机制。
- 未经 A 股 T+1、涨跌停、停牌、不可成交、PIT 和 walk-forward 验证的海外/NSE 参数。

## 当前必须先修的事实问题

1. 盘中讨论回填曾只保留当天记录，会丢失其他任务和历史讨论；应和主链统一 30 天合并保留。
2. 新闻默认链路不得因可选 AkShare 缺失而阻断已配置 RSS；同时必须落盘每个来源的成功、失败、时效和有效条数。
3. `multi_agent_runtime_override=false` 时，确定性候选排序必须不受 Agent `adjusted_score` 影响。
4. 主运行链不得对同一候选进行两套策略体系的二次异构重评分；策略、实际权重、阈值版本和 ledger 必须可一一对应。
5. Dashboard 首页保留左栏日期/切换/状态和右栏结论/三张候选/委员会/消息摘要；历史日期应可展开，旧首页渲染树应删除而非继续并存。

## 收敛顺序

1. **运行完整性**：讨论保留、资讯独立降级、实时/历史边界、候选/讨论覆盖率和稳定排序。
2. **决策证据**：DecisionPackage、AgentEvidenceQualityGate、新闻原子记录/事件簇、DecisionAuditRecord。
3. **研究可复现**：BacktestRunManifest、PurgedIntervalSplitter、前缀不变性、组合级日账本与 A 股执行边界测试。
4. **策略与 Regime 收敛**：一套 runtime scorer、一套受阈值注入的 regime 权重；其余策略只能进入 research/shadow 或删除。
5. **前端删除式重构**：先删除不可达旧树和无消费负载，再拆路由、首页、候选复盘、归档与样式。
6. **新增研究方向**：涨停情绪上下文、涨停横盘再加速、资讯拥挤度、跨资产确认；全部先 shadow/report-only。

## 验收标准

- 每条运行时建议能还原数据源、数据时间、阈值版本、确定性分、证据工件、风险卡点和失效条件。
- 新闻源失败、缓存回退、无上下文、Agent 无证据时，确定性候选排序保持不变且状态显式可见。
- 多 Agent 输出覆盖/跳过原因可在 runtime snapshot 和 Dashboard 看到，且不伪装为真实技术分析。
- 每次回测可由 manifest 复现；数据、PIT、成本、split、代码版本和参数空间缺一不可。
- 任意外部项目进入代码前都有许可、最小吸收范围、测试和 runtime/research 分类记录。
