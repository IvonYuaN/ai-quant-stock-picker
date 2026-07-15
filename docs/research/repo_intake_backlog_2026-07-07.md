# Repo Intake Backlog - 2026-07-07

来源：

- `docs/research/repo_radar_raw.json`
- `_external/archive/repo-scout-2026-06-04/recent_repos_manifest_2026-06-04.json`

当前可复现扫描池去重后为 296 个项目。`repo_intake` 自动分类结果：

- `substrate_candidate`: 165
- `reject_boundary`: 63
- `report_only`: 68

## Lane Counts

- `agent_context`: 17
- `backtest_validation`: 61
- `data_source`: 32
- `execution_boundary`: 46
- `factor_sandbox`: 19
- `portfolio_risk`: 30
- `research_reference`: 71
- `screening_strategy`: 20

## Backlog

| Priority | Lane | Repo | Landing | Next Action |
| --- | --- | --- | --- | --- |
| P1 | agent_context | [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) | briefing/context/artifact metadata | 抽取结构化输出、通知编排和证据追溯 |
| P1 | agent_context | [ValueCell-ai/valuecell](https://github.com/ValueCell-ai/valuecell) | briefing/context/artifact metadata | 抽取结构化输出、通知编排和证据追溯 |
| P1 | backtest_validation | [mementum/backtrader](https://github.com/mementum/backtrader) | backtest/walkforward guardrail | 抽取成本、PIT、防泄漏和窗口验证规则 |
| P1 | backtest_validation | [quantopian/zipline](https://github.com/quantopian/zipline) | backtest/walkforward guardrail | 抽取成本、PIT、防泄漏和窗口验证规则 |
| P1 | backtest_validation | [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | backtest/walkforward guardrail | 抽取成本、PIT、防泄漏和窗口验证规则 |
| P1 | data_source | [OpenBB-finance/OpenBB](https://github.com/OpenBB-finance/OpenBB) | config/data_sources.yaml + aqsp.data.source_catalog | 抽取字段/schema/freshness gate，不直接引入重依赖 |
| P1 | data_source | [Fincept-Corporation/FinceptTerminal](https://github.com/Fincept-Corporation/FinceptTerminal) | config/data_sources.yaml + aqsp.data.source_catalog | 抽取字段/schema/freshness gate，不直接引入重依赖 |
| P1 | data_source | [akfamily/akshare](https://github.com/akfamily/akshare) | config/data_sources.yaml + aqsp.data.source_catalog | 抽取字段/schema/freshness gate，不直接引入重依赖 |
| P1 | factor_sandbox | [microsoft/qlib](https://github.com/microsoft/qlib) | aqsp.research.factor_expression + factor backtest | 只进 shadow/report-only，补字段依赖和 IC 验证 |
| P1 | factor_sandbox | [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL) | aqsp.research.factor_expression + factor backtest | 只进 shadow/report-only，补字段依赖和 IC 验证 |
| P2 | agent_context | [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) | briefing/context/artifact metadata | 抽取结构化输出、通知编排和证据追溯 |
| P2 | factor_sandbox | [firmai/financial-machine-learning](https://github.com/firmai/financial-machine-learning) | aqsp.research.factor_expression + factor backtest | 只进 shadow/report-only，补字段依赖和 IC 验证 |
| P2 | portfolio_risk | [ranaroussi/quantstats](https://github.com/ranaroussi/quantstats) | portfolio/risk report-only metrics | 抽取集中度、相关性、回撤和风险归因指标 |
| P2 | portfolio_risk | [PyPortfolio/PyPortfolioOpt](https://github.com/PyPortfolio/PyPortfolioOpt) | portfolio/risk report-only metrics | 抽取集中度、相关性、回撤和风险归因指标 |
| P2 | screening_strategy | [Open-Dev-Society/OpenStock](https://github.com/Open-Dev-Society/OpenStock) | config/strategy_sources.yaml | 登记假设、信号、验证门槛，不直接入分 |
| P2 | screening_strategy | [myhhub/stock](https://github.com/myhhub/stock) | config/strategy_sources.yaml | 登记假设、信号、验证门槛，不直接入分 |
| P3 | portfolio_risk | [hudson-and-thames/mlfinlab](https://github.com/hudson-and-thames/mlfinlab) | portfolio/risk report-only metrics | 抽取集中度、相关性、回撤和风险归因指标 |
| P3 | screening_strategy | [Mathieu2301/TradingView-API](https://github.com/Mathieu2301/TradingView-API) | config/strategy_sources.yaml | 登记假设、信号、验证门槛，不直接入分 |

## Boundary

`execution_boundary` 项目不进入 AQSP runtime。它们只允许贡献执行红线、不可成交识别、成本模型和反例测试；任何下单、撤单、跟单、自动交易接口都不吸收。
