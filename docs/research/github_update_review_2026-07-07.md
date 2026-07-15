# GitHub Upstream Review - 2026-07-07

本轮复核目标：检查近期 GitHub 上游是否有比当前 Horizon/RSS 信息雷达、多 Agent 讨论、回测/研究经验更值得吸收的方向。数据来自 GitHub API，时间为 2026-07-07。

## Current Absorption Status

- Horizon/RSS 类信息雷达：已进入 AQSP runtime。当前实现为 `RssNewsSource -> CatalystReport -> market_context -> portfolio/briefing`，可结构化影响候选复核优先级。
- 微信第 2 篇回测优化：不进 runtime，作为性能经验保存。后续只有在 walk-forward/参数搜索变慢时，才按 profiling-first 和可失效缓存落代码。
- 微信第 3 篇因子回测系统：不进 runtime，作为未来 factor sandbox 经验保存。AST 白名单、按需字段、max lookback、结果索引可进入后续研究沙箱。

## Updated Upstreams

| Repo | Latest Signal | AQSP Decision |
| --- | --- | --- |
| [Thysrael/Horizon](https://github.com/Thysrael/Horizon) | `pushed_at=2026-07-06`; latest commit supports GPT-5/o-series `max_completion_tokens`; MIT; ~7.9k stars | 已吸收核心模式。继续只取 RSS/news radar/structured notification，不吸收 Playwright、OpenBB、MCP server。 |
| [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) | `pushed_at=2026-07-06`; latest commits include Feishu file upload and `decision signal stock context`; MIT; ~55k stars | 比 Horizon 更贴近日报/通知产品形态。可吸收“决策信号上下文”命名和报告交付思路，但不让 LLM 决策替代 AQSP 评分。 |
| [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) | `pushed_at=2026-07-06`; latest commits mention CN search fallback tests and artifact metadata store; MIT; ~18k stars | 可吸收 artifact metadata store 思路，用于记录每次外部情报/讨论/报告产物来源、hash、生成时间。交易 agent/MCP 不吸收。 |
| [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | `pushed_at=2026-07-03`; latest commits include lookahead leak fix, `filing_date`, fail-loud client, DataClient protocol typing; MIT; ~61k stars | 重要。可吸收“PIT 字段必须 fail-loud，不允许静默空值”的数据治理原则。人格投资大师层暂不优先。 |
| [ZhuLinsen/alphasift](https://github.com/ZhuLinsen/alphasift) | `pushed_at=2026-07-03`; latest commits include strategy catalog metadata, data source wrapper hardening, multi-window price-path analysis; Apache-2.0; ~259 stars | 值得关注。它和 AQSP 边界最像：AI-native stock screening + auditable evaluation。可吸收 strategy catalog metadata 和 multi-window price-path evaluation。 |
| [YoungCan-Wang/WyckoffTradingAgent](https://github.com/YoungCan-Wang/WyckoffTradingAgent) | `pushed_at=2026-07-06`; Wyckoff volume-price analysis, A-share screener, CLI/MCP/Web; AGPL-3.0; ~538 stars | 可吸收量价结构/阶段复核产品形态；因 AGPL 不复制实现，且阶段识别先做 report-only 上下文，不进入核心评分。 |
| [OpenBB-finance/OpenBB](https://github.com/OpenBB-finance/OpenBB) | `pushed_at=2026-07-06`; latest commits include SEC financials IFRS mapping fixes; ~70k stars | 仍只作为可选跨市场数据/研究工具候选。包体和依赖太重，不进 runtime 默认路径。 |
| [microsoft/qlib](https://github.com/microsoft/qlib) / [microsoft/RD-Agent](https://github.com/microsoft/RD-Agent) | qlib 最近 pushed 为 2026-04；RD-Agent 最近 pushed 为 2026-06 | 中长期研究基座，不替代当前短线 runtime。因子沙箱成熟后再评估。 |
| [vectorbt](https://github.com/polakowo/vectorbt) / [backtesting.py](https://github.com/kernc/backtesting.py) | vectorbt 2026-07 仍活跃；backtesting.py 近期提交偏维护/性能 | 不引入依赖；吸收性能经验和 benchmark 思路即可。 |

## Better Than Current?

没有一个项目适合作为 AQSP 的整体替代。更好的做法是继续吸收“窄而硬”的 substrate：

1. `daily_stock_analysis` 的 **decision signal stock context**：适合改进 AQSP 报告表达，把每个候选的“量价、新闻、跨市、讨论、风险”压成同一张上下文卡。
2. `Vibe-Trading` 的 **artifact metadata store**：适合给情报、报告、讨论结果加元数据，方便回溯“当天判断用了哪些外部内容”。
3. `ai-hedge-fund` 的 **lookahead fail-loud**：适合升级 PIT/财报/公告类数据 adapter，缺 `filing_date` 或公告时间时直接失败，不做默认填充。
4. `alphasift` 的 **strategy catalog metadata + multi-window price-path analysis**：适合让策略来源、假设、窗口、风险、验证结果可索引，而不是散在代码和文档里。

## Local Guardrail Check

本轮本地扫描：

- 未发现生产代码中的 `shift(-N)`。
- 未发现 `rolling(..., center=True)`。
- `datetime.now()` 只出现在 `core/time.py`，符合项目统一时间入口。
- 大量 `.mean()` 是 rolling/window/group 聚合或测试代码，未在本轮发现明显全期归一化新问题。

## Next Absorption Queue

1. `artifact_metadata`：为 catalyst report、briefing、debate result 增加来源、生成时间、输入 hash、上游版本字段。
2. `decision_context_card`：在 briefing/dashboard 中统一候选上下文卡：量价证据、消息支持/反对、跨市传导、讨论结论、风险阻塞。
3. `pit_fail_loud_policy`：为 PIT financial/news/announcement adapters 增加缺时间戳失败策略。
4. `strategy_catalog_metadata`：把策略假设、适用窗口、验证指标、版本、来源统一索引化。

当前优先级：`artifact_metadata` 和 `decision_context_card` 高于引入任何新依赖或新平台。

## Implementation Status

- 2026-07-07: `artifact_metadata` 已进入 briefing schema/renderer。产物可记录 `artifact_id`、类型、生成时间、来源、输入 hash 和上游版本。
- 2026-07-07: `decision_context_card` 已进入 briefing schema/renderer/generator。候选上下文统一表达量价、消息、跨市、讨论、风险、下一步和证据 id。
- 2026-07-07: `pit_fail_loud_policy` 已进入 `aqsp.data.pit_policy`，并接到 PIT 财务合并路径；财报行缺 `pubDate` 或时间戳无效时直接 `DataError`，不静默填充。
- 2026-07-07: `strategy_catalog_metadata` 已进入 `aqsp.strategies.catalog`。`config/strategy_sources.yaml` 可被 typed loader 校验并索引，来源、假设、验证门槛不再只停留在散文文档。
- 2026-07-07: 追加吸收 `WyckoffTradingAgent` 的量价结构方向，但只落为 `wyckoff_volume_price_context` 研究目录项和 `aqsp.research.price_path` 多窗口路径摘要；不复制 AGPL 代码，不生成交易信号。
- 2026-07-07: `data_source_catalog_metadata` 已进入 `aqsp.data.source_catalog`，`config/data_sources.yaml` 不再只是文档，可被 typed loader 校验 runtime/research/adoption gate。
- 2026-07-07: `factor_sandbox_expression` 已进入 `aqsp.research.factor_expression`，吸收第三篇微信因子系统的 AST 白名单、按需字段和 max lookback 思路；当前只做研究沙箱，不进入 runtime scoring。
- 2026-07-07: `repo_intake` 已进入 `aqsp.research.repo_intake`，把历史扫描文件 `docs/research/repo_radar_raw.json` 和 repo-scout manifest 作为可复用输入。当前仓库可复现的扫描池去重后是 296 个项目，不是几千；全局文件搜索未找到更大的原始清单。该底座支持任意规模 JSON 清单继续追加。
- 2026-07-07: `repo_intake_backlog` 已进入 `docs/research/repo_intake_backlog_2026-07-07.md`，用同一套分类器生成可评审吸收队列：每个候选都有 priority、lane、landing 和 next action。

## Full Repo Intake Snapshot

基于当前可复现的历史扫描池，`repo_intake` 自动分类结果：

- 总项目数：296
- 可沉淀底座候选：165
- 明确边界拒绝：63
- 仅研究记录：68
- 分类分布：backtest_validation 61、execution_boundary 46、data_source 32、portfolio_risk 30、screening_strategy 20、factor_sandbox 19、agent_context 17、research_reference 71

这批项目不应该逐个接运行时。正确落点是：数据源目录、策略目录、因子沙箱、PIT/freshness 守门、组合风险指标、通知上下文、执行红线拒绝清单。
