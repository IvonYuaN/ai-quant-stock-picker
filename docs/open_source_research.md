# 开源量化项目调研摘要

调研时间：2026-05-27。

## 可借鉴项目

| 项目 | 定位 | 可借鉴点 |
| --- | --- | --- |
| AKShare | Python 财经数据接口库 | A 股日线、实时、资金、板块、公告等免费数据入口 |
| daily_stock_analysis | AI 股票日报与推送系统 | 可作为后续报告增强/通知增强参考，不作为选股策略来源 |
| AlphaEvo | 自进化策略研究 Agent | 策略 YAML、回测-诊断-变异-复测闭环 |
| InStock | A 股量化投资系统 | 指标、形态、综合选股、回测验证闭环 |
| Sequoia-X | 收盘后自动扫描并推送 | 每日收盘自动选股、消息通知产品形态 |
| A-share-Quant-Selector | A 股选股策略集合 | BowlRebound、均线、突破等策略模板 |
| Qlib | AI-oriented Quant 平台 | 因子研究、ML 建模、生产化研究链路 |
| vn.py | Python 开源量化交易框架 | 实盘交易、网关、事件引擎 |
| QUANTAXIS | 本地量化解决方案 | 数据/回测/模拟/交易/可视化一体化 |
| rqalpha | Python 回测与交易框架 | 可扩展回测框架、证券交易模拟 |
| backtrader | Python 策略回测库 | 经典事件驱动回测模型 |
| zvt | modular quant framework | 数据实体、因子、调度模块化 |
| MyTT | 通达信/同花顺指标 Python 化 | 轻量指标公式、CROSS/MA/RSI/MACD 等 |
| yfinance | Yahoo Finance 数据下载 | 美股/ETF 全球数据兜底 |
| mootdx/pytdx | 通达信行情读取 | A 股行情补充数据源 |

## 策略抽象

本项目当前落地“候选池生成器 + 每日自检账本”，不做自动下单：

1. 开盘选股：用前一完整交易日数据，过滤过热与流动性不足，生成次日观察池。
2. 尾盘选股：用当日收盘形态，强调强收盘、放量突破、缩量回踩、长上影风险。
3. 选股打分：趋势、动能、量价、买点、风险五组分数。
4. 风控否决：破 MA20、乖离过高、流动性不足、长上影放量、弱趋势。
5. 每日自检：把当日候选写入 `data/predictions.jsonl`，下次收盘用后续真实 K 线验证。
6. 真实交易约束：信号日只记录，不假设能以收盘价成交；验证用下一交易日开盘价，计入滑点、手续费、止损止盈和持有期。
7. 自优化：按策略历史胜率和平均超额收益生成动态权重，表现差的策略自动降权，表现好的策略自动加权。

## 数据源优先级建议

1. 免费快速原型：AKShare。
2. 稳定历史数据：Tushare Pro / Baostock / 本地缓存。
3. 实时/盘口补充：通达信生态、TickFlow、券商 API。
4. 海外市场：yfinance。
5. 研究缓存：统一落本地 Parquet/SQLite，避免每次请求公网接口。

## 文章级补充线索（2026-06-01）

以下 3 篇微信文章已经做过正文核查，结论不是“照抄”，而是只吸收能被本项目验证和约束的部分：

### 1. GitHub好项目：《最新AI股票诊断平台开源！别再买入、进坑、等回本不断循环了！》

- 可吸收：
  - “纪律检查清单”产品形态，适合强化 briefing 中的 `满足 / 注意 / 不满足` 输出。
  - 乖离率过热保护、趋势确认后再入场、新闻时效限制，适合映射到现有 risk / freshness gate。
  - 截图识股、多渠道推送、定时运行，是产品层能力，不是策略证据。
- 不吸收：
  - “AI直接告诉你该干嘛”这类决策表述，不符合本项目“LLM 不参与打分”的边界。
  - 文中胜率、市场判断、牛市叙事没有给出可复现实验口径，不能当阈值来源。

### 2. 华泰证券：《AI涨乐Skills上线：给你的“龙虾”装上专业投资大脑》

- 可吸收：
  - “专业数据 + 技能化接口 + 自然语言调用”的封装思路，适合作为外部 benchmark，不直接替代本地选股引擎。
  - 条件选股、行情检索、自选股管理这类能力划分，适合反向校验我们的 CLI / briefing / dashboard 信息组织。
  - 如果你愿意长期保留它的 key，可把它接成 `report-only comparator`，用于比较“我们的候选”与“券商技能筛出的候选”差异。
- 不吸收：
  - 模拟交易、账户、下单、撤单接口不进入本仓库。
  - 闭源技能输出不能直接作为策略依据，除非能拆出确定性规则并独立复验。

### 3. 山风与路：《【GitHub开源】27.8k Star，这个国产量化框架把 AI 装进了量化交易系统，把 AI 模型变成策略》

- 可吸收：
  - `vnpy.alpha` 的研究流水线思路：特征工程 → 模型训练 → 信号生成 → 回测分析。
  - `Alpha 158` 这类公开特征集可以作为因子候选池，但必须走 point-in-time 和 purged walk-forward。
  - lab / notebook 工作流适合作为“研究沙箱”，不应污染 runtime 评分主链路。
- 不吸收：
  - 任何实盘交易、算法执行、接口接入能力都不进本仓库。
  - 机器学习模型不能直接覆盖 deterministic score，只能先走 shadow mode / report-only。

当前可维护清单见：

- `config/data_sources.yaml`: A股数据源候选与运行状态。
- `config/strategy_sources.yaml`: 策略家族、理论假设、验证要求。
- `scripts/collect_research_registry.py`: 输出本地 registry，供后续人工/自动搜集结果归档。
- `scripts/collect_open_source_research.py`: GitHub 开源项目采集器，默认要求至少 100 个真实仓库。
- `docs/open_source_quant_research.md`: 最近一次开源项目采集报告。
- `docs/secret-and-upload-policy.md`: token、本地数据和 GitHub 上传边界。
- `docs/research_pipeline.md`: 开源项目如何进入数据源/策略/验证待办的研究流水线。
- `scripts/absorb_research_findings.py`: 把公开仓库元数据吸收为 data/strategy/timing/risk/AI 五条队列。
- `scripts/validate_research_registries.py`: 防止吸收结果退化成无假设、无验证门槛的链接列表。
- `docs/research_absorption.md`: 当前吸收后的人工审阅队列。
- `docs/source_level_absorption.md`: 已 clone 外部源码后的正向/负面吸收记录。

## 工程边界

当前版本刻意保持小而硬：可测、可解释、可替换数据源。自动交易、账户、委托、实盘风控不放在本切片里，避免把“选股研究”与“交易执行”混成一个不可验证的黑箱。
