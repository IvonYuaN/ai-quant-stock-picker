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

当前可维护清单见：

- `config/data_sources.yaml`: A股数据源候选与运行状态。
- `config/strategy_sources.yaml`: 策略家族、理论假设、验证要求。
- `scripts/collect_research_registry.py`: 输出本地 registry，供后续人工/自动搜集结果归档。
- `scripts/collect_open_source_research.py`: GitHub 开源项目采集器，默认要求至少 100 个真实仓库。
- `docs/open_source_quant_research.md`: 最近一次开源项目采集报告。

## 工程边界

当前版本刻意保持小而硬：可测、可解释、可替换数据源。自动交易、账户、委托、实盘风控不放在本切片里，避免把“选股研究”与“交易执行”混成一个不可验证的黑箱。
