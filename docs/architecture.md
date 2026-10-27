# AI 量化选股 — 架构与编码规划

本文件是项目的 **唯一规划源**。所有新模块、PR、阈值变更必须先来这里对齐。
两人协作:小米Pro 编码,Claude 审查。任何与本文件冲突的代码默认不合并。

最后更新:2026-07-20。实时短线研究与独立 T+1 变体账户已纳入当前主线。

---

## 1. 项目宪法(边界,改不动)

### 1.1 做什么

- A 股(第一版)、港股、美股(后续扩展)的 **候选池生成器 + 信号台账 + 通知**。
- 服务器 / GitHub Actions 自动运行,所有下单决策由人完成。
- 所有策略 **可解释、可回测、可替换数据源**。
- 数据过期、信号不可成交 → 失败,不发通知,不污染统计。

### 1.2 不做什么

- 不自动下单,不接券商交易接口。
- 不做 LLM 决策的硬依赖(LLM 只能作为通知附件,不参与选股打分)。
- 不做屏幕截图采集(数据源接口已经够用,截图是兜底,优先级最低)。
- 不预测涨跌幅,只产出"候选 + 评分 + 命中策略 + 风险 + 参考买点/止损/止盈"。
- 不对外提供 API/服务,单用户自用。

### 1.3 核心原则

1. **真实交易思维**:回测/校验里不能出现"事后才知道的信息"。任何一个数字都要回答 "在 t 时刻能不能拿到"。
2. **冻结优先**:阈值上线后冻结到下次 walk-forward,中途不允许"看到亏了就调"。
3. **学习的尺度慢于噪音的尺度**:权重学习按月级别,不按日级别。
4. **不可成交 ≠ 失败的样本**:涨跌停/停牌买不到的信号 status="not_executable",**不计入胜率**,否则系统会被自己骗。
5. **风控是硬约束,不是优化目标**:熔断触线就停,不参与"再平衡决策"。

---

## 2. 模块结构

> 本节由 issue #352 于 2026-10-12 基于实际文件树**全量重生成**：`src/aqsp/` 共 **228** 个 `.py`（25 个一级子包 + 25 个顶层单文件，另有 `web/README.md` 与日报模板 2 个非代码文件），并覆盖 `backend/`、`scripts/` 关键入口、`frontend/src` 顶层与 `config/`。**实际文件树是唯一事实源**；本节每个路径均经脚本核验真实存在（核验脚本见 PR #352 描述），一句话职责仅供导航，以代码 docstring 为准。

### 2.1 `src/aqsp/` — 核心量化选股包

- `src/aqsp/core/__init__.py` — 包导出
- `src/aqsp/core/types.py` — PickResult / SignalDay / OHLCV schema
- `src/aqsp/core/time.py` — Asia/Shanghai 时区工具
- `src/aqsp/core/errors.py` — DataError / FreshnessError / NotExecutableError 异常层级
- `src/aqsp/core/http.py` — HTTP 会话构建（超时/代理环境开关）
- `src/aqsp/core/logging.py` — 统一日志配置
- `src/aqsp/core/runtime.py` — 运行时数据根目录解析（pit_cache 读写单一来源）
- `src/aqsp/data/__init__.py` — 多源装配与包导出
- `src/aqsp/data/source.py` — DataSource 抽象（Protocol）
- `src/aqsp/data/source_factory.py` — 数据源工厂（按名称/负载构建实例）
- `src/aqsp/data/source_catalog.py` — 数据源目录（DataSourceCatalog）
- `src/aqsp/data/registry.py` — 数据源注册表（注册项/负载适配标注）
- `src/aqsp/data/source_readiness.py` — 数据源就绪判定与工作负载匹配
- `src/aqsp/data/source_health.py` — 数据源健康检查
- `src/aqsp/data/source_health_alerts.py` — 数据源健康告警
- `src/aqsp/data/multi_source.py` — 多源故障切换包装器
- `src/aqsp/data/multi_source_health.py` — MultiSource 健康事件记录
- `src/aqsp/data/fetcher.py` — MultiSourceFetcher（Tushare 主 + Akshare 备）
- `src/aqsp/data/akshare_source.py` — akshare 数据源实现
- `src/aqsp/data/sina_source.py` — 新浪数据源实现
- `src/aqsp/data/sina_fundamental.py` — 新浪基本面数据
- `src/aqsp/data/eastmoney_source.py` — 东财数据源实现
- `src/aqsp/data/tencent_source.py` — 腾讯行情源实现
- `src/aqsp/data/baostock_source.py` — baostock 数据源实现
- `src/aqsp/data/efinance_source.py` — efinance 数据源实现
- `src/aqsp/data/mootdx_source.py` — mootdx（TDX 行情）数据源实现
- `src/aqsp/data/sqlite_db_source.py` — 本地 SQLite 落库数据源
- `src/aqsp/data/tdx_vipdoc_source.py` — TDX vipdoc 本地数据源
- `src/aqsp/data/cache.py` — SQLite/Parquet 本地缓存
- `src/aqsp/data/adjust.py` — 复权因子表（point-in-time）
- `src/aqsp/data/intraday.py` — 盘中分时 + 5min bar 合成
- `src/aqsp/data/realtime.py` — RealtimeService 实时行情（含来源注记）
- `src/aqsp/data/trading_calendar.py` — 交易日历
- `src/aqsp/data/freshness.py` — 数据新鲜度检查
- `src/aqsp/data/validation.py` — OHLCV 数据质量验证与异常检测（保守原则）
- `src/aqsp/data/anomaly.py` — 异常告警记录（AnomalyAlert）
- `src/aqsp/data/coverage_density.py` — 数据密度探测（逐交易日标的覆盖度）
- `src/aqsp/data/quote_metadata.py` — 行情时间戳元数据规范化（不改行情经济语义）
- `src/aqsp/data/pit_financial.py` — 财务 point-in-time 数据
- `src/aqsp/data/pit_policy.py` — PIT 时间戳策略校验
- `src/aqsp/data/tushare_pit.py` — Tushare PIT 取数
- `src/aqsp/data/industry_pit.py` — 行业分类 point-in-time
- `src/aqsp/data/macro_pit.py` — 宏观指标 point-in-time
- `src/aqsp/data/cn/__init__.py` — 包导出
- `src/aqsp/data/cn/margin_trading.py` — 两融数据（缓存读写）
- `src/aqsp/data/cn/northbound.py` — 北向资金流向与因子
- `src/aqsp/data/index_constituents.py` — 指数成分加载（可选）
- `src/aqsp/data/cli_fetch.py` — CLI 取数入口（带元数据的多源取帧）
- `src/aqsp/data/announcement.py` — 公告数据
- `src/aqsp/data/cls_news.py` — 财联社快讯
- `src/aqsp/data/news_source.py` — 新闻数据源聚合
- `src/aqsp/data/concept_board.py` — 概念板块
- `src/aqsp/data/dividend_plan.py` — 分红方案
- `src/aqsp/data/earnings_forecast.py` — 盈利预测
- `src/aqsp/data/holder_num.py` — 股东户数
- `src/aqsp/data/lockup.py` — 限售解禁
- `src/aqsp/data/longhubang.py` — 龙虎榜
- `src/aqsp/data/national_team.py` — 国家队持仓
- `src/aqsp/data/suspend_resume.py` — 停复牌（东财，事件日历数据源）
- `src/aqsp/data/market_context_source.py` — 市场上下文数据源
- `src/aqsp/data/filters.py` — 数据层过滤工具
- `src/aqsp/universe/__init__.py` — 包导出
- `src/aqsp/universe/pool.py` — 默认股票池
- `src/aqsp/universe/filters.py` — ST / 退市 / 停牌 / 新股过滤
- `src/aqsp/universe/t1_filter.py` — T+1 过滤（排除昨日已买标的）
- `src/aqsp/universe/intraday_cursor.py` — 盘中批次游标（原子持久化）
- `src/aqsp/universe/runtime.py` — 运行时股票池装配
- `src/aqsp/features/__init__.py` — 包导出
- `src/aqsp/features/cyq.py` — CYQ 筹码分布（获利比例/成本区间/集中度）
- `src/aqsp/features/event_calendar.py` — 事件日历（PIT 安全的前瞻预警/回溯佐证）
- `src/aqsp/features/pit_enrichment.py` — PIT 数据源管道适配
- `src/aqsp/indicators.py` — 技术指标库（被 data/strategy/auto_factor_mining 引用）
- `src/aqsp/strategies/__init__.py` — 策略注册与协议导出
- `src/aqsp/strategies/base.py` — Strategy 协议基类
- `src/aqsp/strategies/catalog.py` — 策略目录（StrategyCatalog）
- `src/aqsp/strategies/thresholds.py` — config/thresholds.yaml 加载器（load_thresholds）
- `src/aqsp/strategies/candidates.py` — 候选因子（外部方案吸收，仅诊断不进 gate）
- `src/aqsp/strategies/composite.py` — 复合打分
- `src/aqsp/strategies/momentum.py` — RPS 动量
- `src/aqsp/strategies/ma_breakout.py` — 均线突破
- `src/aqsp/strategies/volume.py` — 放量突破
- `src/aqsp/strategies/volume_surge.py` — 放量急涨
- `src/aqsp/strategies/amount_ratio.py` — 成交额比因子
- `src/aqsp/strategies/macd_hist.py` — MACD 柱因子
- `src/aqsp/strategies/mean_reversion.py` — 均值回归
- `src/aqsp/strategies/triple_rise.py` — 三连阳
- `src/aqsp/strategies/n_rebound.py` — N 字反包
- `src/aqsp/strategies/morning_breakout.py` — 早盘突破
- `src/aqsp/strategies/closing_premium.py` — 尾盘溢价
- `src/aqsp/strategies/lower_shadow_ratio.py` — 下影线比因子
- `src/aqsp/strategies/price_volume_corr.py` — 量价相关性
- `src/aqsp/strategies/quality.py` — 质量因子
- `src/aqsp/strategies/value.py` — 价值因子
- `src/aqsp/strategies/event_driven.py` — 事件驱动策略
- `src/aqsp/strategies/intraday_trade.py` — 盘中交易策略
- `src/aqsp/strategies/limit_up_ladder.py` — 涨停梯队
- `src/aqsp/strategies/sector_rotation.py` — 板块轮动
- `src/aqsp/strategies/multi_factor_rotation.py` — 多因子轮动
- `src/aqsp/strategies/volatility_position.py` — 波动率仓位
- `src/aqsp/strategies/national_team_filter.py` — 国家队持仓过滤
- `src/aqsp/strategies/fund_flow_analyzer.py` — 资金流分析（主力/北向/龙虎榜席位）
- `src/aqsp/strategies/factor_backtest.py` — 因子回测器（FactorBacktester）
- `src/aqsp/strategies/factor_monitor.py` — 因子表现监控
- `src/aqsp/strategies/auto_factor_mining.py` — 自动因子挖掘
- `src/aqsp/strategies/auto_evolution.py` — 策略自动进化
- `src/aqsp/strategies/adaptive_evolution.py` — 策略自适应进化
- `src/aqsp/regime/__init__.py` — 包导出
- `src/aqsp/regime/detector.py` — 市场状态二分类（趋势市 vs 震荡市）
- `src/aqsp/regime/hmm_detector.py` — HMM 牛/熊/震荡三态检测
- `src/aqsp/regime/vocabulary.py` — 市场状态词表规范化（跨模块共享）
- `src/aqsp/regime/runtime.py` — 运行时状态上下文构建
- `src/aqsp/regime/strategy_mixer.py` — 按状态混合策略权重
- `src/aqsp/portfolio/__init__.py` — 包导出
- `src/aqsp/portfolio/manager.py` — 组合管理
- `src/aqsp/portfolio/correlation.py` — 候选池内相关性去重
- `src/aqsp/portfolio/diversification.py` — 分散度度量
- `src/aqsp/portfolio/sector_check.py` — 行业集中去重
- `src/aqsp/portfolio/optimizer.py` — 组合优化（等权/风险平价）
- `src/aqsp/portfolio/position_tracker.py` — 持仓跟踪
- `src/aqsp/portfolio/risk_summary.py` — 组合风险汇总
- `src/aqsp/portfolio/snapshot.py` — 组合快照
- `src/aqsp/risk/__init__.py` — 包导出
- `src/aqsp/risk/circuit_breaker.py` — 账户级熔断
- `src/aqsp/risk/stop_loss.py` — 三层止损（个股/组合/移动）
- `src/aqsp/risk/dynamic_stop.py` — ATR 动态止损
- `src/aqsp/risk/unified_risk.py` — 三层风控架构（个股/组合/系统）
- `src/aqsp/execution/__init__.py` — 包导出
- `src/aqsp/execution/executor.py` — 执行协调器（整合 TWAP 与成本）
- `src/aqsp/execution/twap.py` — TWAP 大单拆分（A 股规则约束）
- `src/aqsp/execution/cost.py` — A 股交易成本计算
- `src/aqsp/backtest/__init__.py` — 包导出
- `src/aqsp/backtest/walk_forward.py` — Walk-forward 回测引擎（不复权 + PIT、T+1 退出）
- `src/aqsp/backtest/variant_account.py` — 独立研究变体账户的纸面执行
- `src/aqsp/backtest/audit.py` — 回测假设审计（BacktestAssumptionAudit）
- `src/aqsp/ledger/__init__.py` — 包导出
- `src/aqsp/ledger/base.py` — JSONL 台账读写（原 store.py 职责）
- `src/aqsp/ledger/runtime.py` — pending 信号验证（原 validator.py 职责）
- `src/aqsp/ledger/learner.py` — 策略权重学习（月级尺度）
- `src/aqsp/ledger/debate_reconciliation.py` — debate 与实盘战绩对账（只读）
- `src/aqsp/ledger/failure_analysis.py` — 亏损模式归因分析
- `src/aqsp/ledger/special_signals.py` — 特殊策略信号台账行
- `src/aqsp/filters_lethal/__init__.py` — 包导出
- `src/aqsp/filters_lethal/base.py` — 一票否决过滤器基类
- `src/aqsp/filters_lethal/pipeline.py` — 否决过滤器管道
- `src/aqsp/filters_lethal/announcement_keyword.py` — 公告关键词否决
- `src/aqsp/filters_lethal/holder_count.py` — 股东户数否决
- `src/aqsp/filters_lethal/lockup_release.py` — 解禁否决
- `src/aqsp/news/__init__.py` — 包导出
- `src/aqsp/news/catalysts.py` — 消息面催化剂雷达
- `src/aqsp/news/entity_graph.py` — 新闻实体/供应链别名确定性匹配
- `src/aqsp/news/watch_candidates.py` — 新闻事件 → 全市场观察候选的确定性扩展
- `src/aqsp/briefing/__init__.py` — 收评/早盘日报包导出
- `src/aqsp/briefing/generator.py` — 日报生成主流程
- `src/aqsp/briefing/schema.py` — 日报数据 schema
- `src/aqsp/briefing/renderer.py` — 日报渲染
- `src/aqsp/briefing/templates/default.md.j2` — 日报 Jinja2 模板
- `src/aqsp/briefing/conclusion.py` — 日报结论段
- `src/aqsp/briefing/agent_roles.py` — 辩论角色定义
- `src/aqsp/briefing/debate.py` — 多空辩论流程
- `src/aqsp/briefing/debate_tracker.py` — 辩论状态跟踪
- `src/aqsp/briefing/closing_review.py` — 收评复盘
- `src/aqsp/briefing/notifier.py` — 日报通知分发
- `src/aqsp/briefing/email_notifier.py` — 邮件通知
- `src/aqsp/briefing/llm.py` — LLM 增强日报（可选，不参与选股打分）
- `src/aqsp/reports/__init__.py` — 包导出
- `src/aqsp/reports/v2.py` — v2 研究日报生成器
- `src/aqsp/review/__init__.py` — 复盘笔记系统（历史信号标签/笔记/评分）
- `src/aqsp/monitor/__init__.py` — 包导出
- `src/aqsp/monitor/checker.py` — 周期监控检查（MonitorChecker）
- `src/aqsp/monitor/notifier.py` — 监控告警格式化与发送
- `src/aqsp/monitor/strategy_health.py` — 策略健康监控
- `src/aqsp/runtime/__init__.py` — 包导出
- `src/aqsp/runtime/gate_notify.py` — gate 状态通知
- `src/aqsp/runtime/recommendation_gate.py` — 短线推荐全局纯门
- `src/aqsp/audit/__init__.py` — 包导出
- `src/aqsp/audit/trade_logger.py` — 交易日志记录
- `src/aqsp/audit/agent_runs.py` — 多 agent 协调 append-only 记录
- `src/aqsp/audit/decision_chain.py` — 确定性研究决策哈希链
- `src/aqsp/optimizer/__init__.py` — 包导出
- `src/aqsp/optimizer/param_optimizer.py` — 网格/贝叶斯参数优化
- `src/aqsp/optimizer/pattern_discovery.py` — 模式发现引擎
- `src/aqsp/experiment/__init__.py` — 实验框架：策略参数优化与 A/B 测试
- `src/aqsp/research/__init__.py` — 包导出
- `src/aqsp/research/factor_expression.py` — 因子表达式编译与求值
- `src/aqsp/research/price_path.py` — 价格路径窗口摘要
- `src/aqsp/research/repo_intake.py` — 外部仓库研究摄取
- `src/aqsp/research/summary.py` — 研究管线汇总
- `src/aqsp/research_engine.py` — Walk-forward 引擎装配（builtin/akquant）
- `src/aqsp/web/__init__.py` — 包导出
- `src/aqsp/web/entrypoint.py` — 规范入口与遗留静态页护栏
- `src/aqsp/web/config.py` — Web 服务配置
- `src/aqsp/web/dashboard.py` — FastAPI Dashboard 主应用
- `src/aqsp/web/dashboard_beginner.py` — 新手页兼容入口（原页面已退役）
- `src/aqsp/web/dashboard_beginner_compat.py` — 新手页兼容垫片
- `src/aqsp/web/data_provider.py` — 基于落盘数据构建仪表盘视图
- `src/aqsp/web/home_snapshot.py` — 首页快照契约（仅文件、有界）
- `src/aqsp/web/live_candidate_view.py` — 盘中候选有界视图
- `src/aqsp/web/archive_safety.py` — 归档报告措辞护栏（只改展示层）
- `src/aqsp/web/README.md` — web 子包说明
- `src/aqsp/services/__init__.py` — 包导出
- `src/aqsp/services/scheduled.py` — 调度服务入口
- `src/aqsp/services/walkforward_data.py` — Walk-forward 取数请求/结果封装
- `src/aqsp/utils/__init__.py` — 包导出
- `src/aqsp/utils/concurrency.py` — 并发工具
- `src/aqsp/utils/env.py` — 环境变量工具
- `src/aqsp/utils/jsonl_io.py` — JSONL 读写
- `src/aqsp/utils/llm_safe.py` — LLM 调用安全封装
- `src/aqsp/__init__.py` — 包标识
- `src/aqsp/__main__.py` — `python -m aqsp` 入口
- `src/aqsp/_constitution_check.py` — 宪法启动门（CONSTITUTION §8.1）
- `src/aqsp/candidate_quality.py` — 短线候选确定性质量边界
- `src/aqsp/cli.py` — CLI 总入口（全部子命令注册）
- `src/aqsp/config.py` — RuntimeConfig 运行时配置（含辩论角色配置）
- `src/aqsp/freshness.py` — MarketDataFreshnessError 等新鲜度异常
- `src/aqsp/goal_switches.py` — 目标切换/跟踪状态
- `src/aqsp/internet_strategies.py` — 外部来源策略信号评估
- `src/aqsp/market_context.py` — 市场上下文工件与跨市场规则
- `src/aqsp/models.py` — ScreeningConfig 等轻量模型
- `src/aqsp/notification_runtime.py` — 通知状态与发送判定（should_send/reserve/mark）
- `src/aqsp/notification_style.py` — 通知样式
- `src/aqsp/notifier.py` — 通知分发与结果汇总
- `src/aqsp/notify_templates.py` — 通知文案模板
- `src/aqsp/paper.py` — 模拟盘读写与报告
- `src/aqsp/presentation.py` — 展示辅助（优先级标签/复盘窗口格式化）
- `src/aqsp/ratings.py` — 评级判定（is_tradable_rating）
- `src/aqsp/report.py` — 每日选股报告渲染
- `src/aqsp/runtime_snapshot.py` — 只读运行时快照（dashboard/agent 消费）
- `src/aqsp/settings.py` — pydantic-settings 统一配置（env > YAML > 默认）
- `src/aqsp/strategy.py` — 选股主流程（screen_universe / 质量门 / 按状态权重）
- `src/aqsp/walkforward_gate.py` — Walk-forward 门校验与阈值版本显示

### 2.2 `backend/` — Vibe-Research 独立后端（FastAPI）

- `backend/app.py` — FastAPI 应用装配（/api 全部端点 + CORS + API Key）
- `backend/astock.py` — A 股全栈数据层（五层数据源，自包含）
- `backend/gstock.py` — 美股/港股数据层（东财域内合规子集）
- `backend/market.py` — 市场总览（情绪 + 板块资金流，全站共享缓存）
- `backend/newsradar.py` — 资讯雷达（12 赛道 108 个公开 RSS 源，合规过滤）
- `backend/info_stream.py` — 小道信息流聚合（财联社快讯 + 概念异动，只读）
- `backend/chat.py` — 系统 AI 对话层（function calling 循环）
- `backend/cli_runtime.py` — 调用本机已装 AI CLI 的订阅接入
- `backend/mcp_server.py` — MCP server（把 A 股数据工具暴露给 agent）
- `backend/aqsp_bridge.py` — AQSP 运行时快照 → 研究 API 只读桥
- `backend/performance_bridge.py` — 绩效只读桥接（选得准不准）
- `backend/portfolio.py` — 用户持仓录入 + 实时行情叠加浮动盈亏（本地）
- `backend/myreports.py` — 我的研报（用户上传归档，存本地不上传）
- `backend/tenant.py` — 请求级租户识别（防共享 API Key 串号）
- `backend/metrics.py` — Prometheus 指标定义与收集器
- `backend/conftest.py` — pytest 配置（路径/live 标记/数据目录隔离）
- `backend/README.md` — 后端说明

### 2.3 `scripts/` — 关键入口与运维脚本

`scripts/` 另含 `dev/ deploy/ monitor/ backup/ data/ maintenance/ production/ analysis/ runner/ debate/ launchd/` 等子目录与配套 shell 入口（`daily_pipeline.sh`、`runner_sync.sh` 等），完整清单见 `scripts/README.md`。关键入口：

- `scripts/daily_pipeline.py` — 每日管线入口
- `scripts/preflight_upload.py` — 上传前预检（三门禁之一）
- `scripts/run_production_walkforward_gate.py` — 生产 Walk-forward gate
- `scripts/run_dual_window_gate.py` — 双窗口 gate 对比
- `scripts/run_variant_suite.py` — 变体账户套件运行
- `scripts/refresh_sqlite_batch.py` — SQLite 批量刷新
- `scripts/update_sqlite_daily.py` — SQLite 日度更新
- `scripts/backfill_akshare.py` — akshare 历史回补
- `scripts/manage_data_lifecycle.py` — 数据生命周期管理
- `scripts/collect_stock_data.py` — 股票数据采集
- `scripts/fetch_pit_data.py` — PIT 数据抓取
- `scripts/generate_cold_start_signals.py` — 冷启动信号生成
- `scripts/ic_diagnosis.py` — IC 诊断
- `scripts/ic_dual_verdict.py` — IC 双窗口裁决
- `scripts/factor_ic_diagnosis.py` — 因子 IC 诊断
- `scripts/write_home_snapshot.py` — 首页快照写入
- `scripts/write_release_manifest.py` — release manifest 写入
- `scripts/check_release_consistency.py` — release 一致性检查
- `scripts/check_no_secrets.py` — 密钥泄漏检查
- `scripts/check_scheduler.py` — 调度配置检查
- `scripts/check_before_live.py` — 上线前检查
- `scripts/open_dashboard.py` — 启动 Dashboard
- `scripts/verify_dashboard.py` — Dashboard 验证
- `scripts/headless_dashboard_check.py` — 无头 Dashboard 检查
- `scripts/export_dashboard_db.py` — Dashboard DB 导出
- `scripts/server_doctor.py` — 服务器体检
- `scripts/remote_runtime_probe.py` — 远端运行时探针
- `scripts/sync_runtime_files_to_server.py` — 运行时文件同步到服务器
- `scripts/merge_server_ledgers.py` — 双机台账合并
- `scripts/cleanup_ledger.py` — 台账清理
- `scripts/diagnose_runtime.py` — 运行时诊断
- `scripts/resolve_ticker.py` — 股票代码解析
- `scripts/push_with_report.py` — 带报告的 git 推送

### 2.4 `frontend/src` — Dashboard 前端（React + Vite）

- `frontend/src/main.tsx` — 前端入口
- `frontend/src/router.tsx` — 路由定义
- `frontend/src/components/` — 通用组件
- `frontend/src/pages/` — 页面（Today/Market/StockResearch/Concepts/InfoStream/my）
- `frontend/src/hooks/` — React hooks
- `frontend/src/lib/` — 工具库
- `frontend/src/data/` — 前端本地数据
- `frontend/src/types/` — 类型定义
- `frontend/src/index.css` — 全局样式

### 2.5 `config/` — 运行时配置单一来源

- `config/thresholds.yaml` — 策略阈值单一来源（version + effective_from）
- `config/settings.prod.yaml` — 生产配置
- `config/settings.dev.yaml` — 开发配置
- `config/blacklist.yaml` — 黑名单
- `config/data_sources.yaml` — 数据源配置
- `config/evolution_config.yaml` — 进化配置
- `config/factor_config.yaml` — 因子配置
- `config/factor_library.json` — 因子库
- `config/goal_switches.yaml` — 目标开关
- `config/monitors.yaml` — 监控配置
- `config/news_sources.yaml` — 新闻源配置
- `config/strategy_sources.yaml` — 外部策略源配置
- `config/thresholds_variant_a.yaml` — 变体 A 阈值
- `config/thresholds_variant_b.yaml` — 变体 B 阈值

---

## 3. 数据层契约

### 3.1 DataSource 抽象(Protocol)

```python
# src/aqsp/data/source.py
from typing import Protocol, Literal
import pandas as pd
from datetime import date

# OHLCV 标准列:
#   date, symbol, name, open, high, low, close, volume, amount,
#   suspended (bool), limit_up (float), limit_down (float)
OhlcvFrame = pd.DataFrame

class DataSource(Protocol):
    name: str  # "akshare" / "sina" / "eastmoney"

    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",  # **默认不复权**
    ) -> dict[str, OhlcvFrame]: ...

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]: ...

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        # 返回字段至少含:price, bid1, ask1, volume, amount, ts(带时区 ISO 字符串)
        ...

    def fetch_index(
        self,
        index_codes: list[str],  # ["000300", "000905", "HSI", "SPX"]
        start: date,
        end: date,
    ) -> dict[str, OhlcvFrame]: ...
```

**契约规则**:

- 返回的 OHLCV **必须是不复权原始价**。复权在 `adjust.py` 用 point-in-time 因子表后处理。
- `suspended/limit_up/limit_down` 三列必填。无信息时显式 `False`/`NaN`,不可以省略。
- 时间戳带时区(`Asia/Shanghai`),禁止裸 `datetime.now()`。
- fetch 失败必须抛 `DataError` 子类,不允许静默返回空 dict。

### 3.2 故障切换

`MultiSource(primary, fallbacks=[...])`:primary 失败时按顺序尝试 fallbacks,记录哪一路成功;两路数据不一致(open/close 差异 > 0.5%)时抛 `DataInconsistencyError`。

### 3.3 实时数据路线

盘中(`live_short`)的实际链路由 `source_factory.build_data_source("online_first")` 决定,
当前是 `tencent → sina → akshare → eastmoney(deferred)`。**下面是 2026-09-18 生产实测后的现状,
与旧版本文档差别很大 —— 排障前先看这里,别按老印象假设"备份是好的"。**

1. **腾讯 `web.ifzq.gtimg.cn/appstock/app/day/query`** — 实际主源(分时)。
   ⚠️ 不要改回同主机的 `appstock/app/minute/query`:该路径已被腾讯 WAF **按路径封禁**
   (实测 http/https、sh/sz 一律 501 + 拦截页)。详见 `tencent_source.py` 的注释。
2. **新浪 `hq.sinajs.cn`** — ⚠️ **实测已不可用,不要再当盘中备份**:`hq.sinajs.cn` 403
   (带 `Referer` 也 403)、`CN_MarketData.getKLineData` 返回字面量 `null`、
   分时响应无法 JSON 解码。它仍留在 `fallback_order` 里**只是因为**
   `source_catalog` 要求该链成员 `runtime_ready`(见 `_validate_fallback_order`)。
   见 issue #144 / #149。
3. **akshare** — 生产机**未安装**;`_build_source_refs` 会静默跳过它。
   因此它虽然在配置里,实际不参与盘中链路。
4. **东方财富 `push2delay.eastmoney.com/api/qt/stock/trends2/get`** — 延迟兜底。
   ⚠️ 有单 IP 配额与全局节流,实测**接不住完整批次**(256 只 0 覆盖)。见 #149。
5. **券商 QMT** — 实盘升级路径(后续)。
6. **屏幕截图 OCR** — 只在某个数据真的没接口时启用,默认不实现。

⚠️ **兜底 ≠ 可用**:主源挂掉时兜底只覆盖单波次(60 只 100%),256 只真实批次覆盖 0。
**目前 tencent 是事实上的单点**(见 #149)。

---

## 4. 策略层契约

### 4.1 Strategy 协议

```python
# src/aqsp/strategies/base.py
from typing import Protocol
from dataclasses import dataclass

@dataclass(frozen=True)
class SignalScore:
    strategy_id: str
    score: float
    reasons: tuple[str, ...]
    fired: bool       # 没触发时 score=0,reasons=()

class Strategy(Protocol):
    id: str
    version: str           # "v1.0" 改阈值必须加版本号
    hypothesis: str        # 必填:一句话经济假设
    regime_required: tuple[str, ...]  # 适用 regime,空 tuple 表示全状态

    def evaluate(self, df: pd.DataFrame, regime: str) -> SignalScore: ...
```

**契约规则**:

- `hypothesis` 不允许为空字符串。提交策略 PR 时必须写清楚"为什么这条规则在 A 股有效"。
- 所有阈值通过构造函数从 `thresholds.yaml` 注入,**禁止字面量魔法数字**。
- `evaluate` 必须是纯函数。
- 不允许跨策略共享状态。
- 不允许在 `evaluate` 内部访问磁盘、网络。

### 4.2 thresholds.yaml 格式

```yaml
version: "2026.05.27"
effective_from: "2026-06-01"
last_walkforward_run: "2026-05-15"
notes: "首版冻结值,基于 InStock / A-share-Selector 项目经验值"

strategies:
  volume_breakout:
    breakout_proximity: 0.995
    volume_ratio_min: 1.35
    range_pos_min: 0.62
    score_base: 18
  ma_pullback:
    upper_band: 1.025
    lower_band: 0.985
    volume_ratio_max: 1.1
    score_base: 16
  # ...
```

**约定**:CLI 启动时把 `version` 写到日志和 ledger 每条记录,事后能反查"那次信号用的哪一版阈值"。

---

## 5. Ledger / 学习层(改进版)

### 5.1 LedgerRow Schema

每条记录新增字段:

```python
{
  # 已有字段保持不变
  "thresholds_version": "2026.05.27",
  "regime_at_signal": "trend",      # 或 "range"
  "executable": True,                # 次日开盘是否真能成交
  "not_executable_reason": "",       # "limit_up_at_open" / "suspended"
  "signal_day_group": "2026-05-27_volume_breakout",  # 用于"按信号日聚合"
}
```

### 5.2 Learner 配置

```python
@dataclass
class LearnerConfig:
    min_independent_signal_days: int = 30
    rolling_window_days: int = 90
    weight_floor: float = 0.65
    weight_ceiling: float = 1.45
    aggregation: Literal["per_signal_day", "per_pick"] = "per_signal_day"
    weight_change_cooldown_days: int = 30
    by_regime: bool = True
```

**契约规则**:

- 不满足 `min_independent_signal_days` 的策略权重必须为 `1.0`,不参与学习。
- 同一个 `signal_day_group` 内多个 pick 合成 **1 个观察**(平均收益)。该「平均收益」
  属于**数据聚合层**产物（§5.2），用于形成 observation；**不得**作为调权信号进入
  权重学习（机制层，受 §8 约束）。
- 只看 `rolling_window_days` 内的样本。
- 权重变更必须写入 `data/weight_history.jsonl`(前后值、reason、生效时间)。
- 冷却期内不允许再次调整同一策略权重。
- `status="not_executable"` 的记录**不进入胜率统计**。

> **§5.2 vs §8 关系**：
> - §5.2 描述的是 observation 层（同一信号日内多个 pick 合并为 1 个观察样本）；
> - §8 描述的是 mechanism 层（学习对象是 IC / 命中率分布 / 特征漂移，**不是 PnL**）。
> - `avg_return` / `sharpe_ratio` 是 PnL 派生指标，可作为观测与告警字段（见 StrategyDecayDetector），
>   但**严禁**进入 `_calculate_weight` 权重乘法。

### 5.4 冷启动期规则

**冷启动期**:系统首次运行或 ledger 清空后的前 30 个独立信号日。

| 行为 | 冷启动期内 | 冷启动期后 |
|------|-----------|-----------|
| 策略权重调整 | ❌ 不调整,所有策略权重 = 1.0 | ✅ 按 learner 计算 |
| 策略胜率展示 | ❌ 不展示,显示"数据积累中" | ✅ 正常展示 |
| walk-forward 报告 | ❌ 不生成 | ✅ 正常生成 |
| 信号生成 | ✅ 正常生成 | ✅ 正常生成 |

**原因**:前 30 个信号日的样本量不足以支撑统计推断。展示空表或调整权重会误导用户以为系统故障或产生虚假信心。

**实现**:Learner 在 `min_independent_signal_days` 未满足时返回 `weights = {}`,CLI 检测到空权重时在报告中显示"⏳ 冷启动期:已积累 X/30 个独立信号日"。

### 5.3 Execution 判定

```python
def is_executable(
    bar_next_open: dict,   # 信号日次日开盘 bar
    prev_close: float,
    market: Literal["A", "HK", "US"] = "A",
) -> tuple[bool, str]:
    """
    A 股:
      - suspended == True → (False, "suspended")
      - open >= prev_close * 1.099 → (False, "limit_up_at_open")
      - open <= prev_close * 0.901 → (False, "limit_down_at_open")
      - ST 股阈值改用 5%(±0.05)
    HK/US 暂不限,但保留扩展位。
    """
```

---

## 6. 组合 + 风控

### 6.1 Portfolio

候选池 → 行业去重(单板块 ≤ 30%)→ 相关性去重(60日相关系数 > 0.7 的强制只留得分高的)→ 仓位规划(等权 / 风险平价)。

### 6.2 Circuit Breaker

```python
@dataclass
class CircuitBreakerConfig:
    daily_loss_pct: float = 3.0      # 单日组合浮亏阈值
    weekly_loss_pct: float = 6.0
    monthly_loss_pct: float = 10.0
    cooldown_days: int = 5            # 触发后停止生成新信号的天数
```

熔断状态持久化到 `data/risk_state.json`,通知模板内 banner 显示"组合保护中,本期信号仅供参考"。

---

## 7. 任务拆分(PR 顺序)

小米Pro 按下面顺序提,Claude 逐个审查。

| # | PR 标题 | 依赖 | 验收 | 状态 |
|---|---|---|---|---|
| 1 | core/types + errors + 时区工具 | 无 | 单测 100% | ✅ |
| 2 | data/source 抽象 + akshare 迁移 | #1 | CLI 现有命令不变 | ✅ |
| 3 | data/cache 本地缓存 + adjust 因子表 | #2 | 第二次 fetch 只走缓存 | ✅ |
| 4 | data/sina + eastmoney 备份源 + MultiSource | #2 | akshare 失败时自动降级 | ✅ |
| 5 | data/intraday + realtime quote | #4 | 14:45 班次能拿到当天数据 | ✅ |
| 6 | universe/filters(ST/停牌/新股) | #2 | fixture 验证已知股票被过滤 | ✅ |
| 7 | strategies 拆模块 + thresholds.yaml + version | #1 | 输出与旧版字节级一致 | ✅ |
| 8 | ledger/execution:涨跌停/停牌 + benchmark 修复 | #2 | 一字涨停信号 status="not_executable" | ✅ |
| 9 | ledger/learner v2:聚合+滚动+样本门槛 | #8 | 30 样本以下保持 1.0 | ✅ |
| 10 | regime/detector + 分 regime 学习 | #9 | 趋势/震荡市权重独立 | ✅ |
| 11 | portfolio/correlation + sector 去重 | #7 | top10 输出后再过 portfolio 层 | ✅ |
| 12 | risk/circuit_breaker | #9 | 模拟回撤超阈值 → 通知改 banner | ✅ |
| 13 | reports v2:展示 version、regime、熔断状态 | 上面所有 | 报告能反查阈值版本 | ✅ |
| 14 | walk-forward 回测脚本 + DSR/PBO | #7 #8 | 输出折扣后 Sharpe | ✅ |
| 15 | walk-forward 真实回测:从 ledger 拉真实收益 | #14 | 真实 open/close 价格计算收益,支持手续费/滑点/止损止盈 | ✅ 已通过验收 |
| 16 | 数据基础设施扩容:tencent + mootdx 数据源 | #4 | MultiSource(akshare,[tencent,mootdx,sina,eastmoney]) fallback 真触发 | ✅ |
| 17 | 排雷过滤层 filters_lethal | #6 | LockupRelease/HolderCount/AnnouncementKeyword 三过滤器 + hypothesis 非空 | ✅ |
| 18 | 北向资金 + 融资融券观察因子 | #2 | northbound_flow_5d_z/margin_balance_change_5d 入 ledger,不进评分 | ✅ |
| 19 | 简报生成 briefing 模块 | #13 | jinja2 模板降级 + LLM 可选 + notifier 复用 | ✅ |
| 20 | 监控告警 aqsp monitor | #12 | monitors.yaml 配置 + GitHub Actions 每30分钟 | ✅ |

**P0 三件**(本仓库当前最大风险,Claude 直接修了,不走小米Pro):

- benchmark 000300 拉取没生效 → `excess_return_pct` 永远是 0
- ledger 验证不处理涨跌停/停牌 → 不可成交信号污染胜率
- 14:45 那班拿不到当天日 K → close 模式名不副实

### 7.1 当前完整交付目标：恢复 T+1 实盘决策工作台（2026-10-23）

用户指出此前交付偏离原定大改。唯一规划源仍为本文；本节正式承接
`deliverables/product-strategy/重构总方案-T1实盘决策工作台-2026-10-19.md`
与 `deliverables/product-strategy/prd-t1-trader-workstation-2026-10-19.md` 的 v1 验收。
中心对象是用户的持仓与风险处置，而非不断更换的候选榜。前轮 D1–D3 只是研究页局部交付，不能等同本目标完成。

| 顺序 | 原需求 | 交付单元 | 验收 |
|---|---|---|---|
| M0 | P0-1/2/3/7 | 提醒首屏、全局操作回录、自动风险规则 | 从空库回录→真实服务检查→可复算提醒→处置；无持仓/未检查/检查失败分别呈现 |
| M1 | P0-6、P1-1/4 | 持仓自选合一、四项导航、量化过程退居证据 | 我的持仓单页含持仓/自选；今日/持仓/大盘/消息四项；旧个人数据保留导出，旧路由兼容 |
| M2 | P0-4/5 | 交易日候选稳定与个人命中优先，绑定交付 | 同交易日保留冻结集合和顺序，变化明确增量；下一交易日重新冻结；构造 2 持仓+3 自选命中前五 |
| M3 | P1-2/3 | 提醒频率、忽略与成交回录闭环 | once/daily/always 持久化并作用于提醒；处置不会反复重开；真实成交回录后同步持仓与提醒 |

不修改 §1 项目宪法、策略阈值、cron、ACL 或服务器配置。原方案的 L3 炸板已移出 v1，界面显式标记未上线。
真实持仓和长期噪音率须由用户真实数据验收，禁止用生产样例或短时合成测试冒充；未验证项保持未完成。
实现、测试、审查、CI、合并、已授权阶段部署及公网真实流程均通过后才记录技术交付完成。
执行与缺项记录见 `docs/t1-workstation-delivery.md`。

2026-10-23 实际持仓反馈补充验收：同一仓位多条触发规则在首页合并为一张股票卡，展示股票名、格式化数值与可复核原因。规则越线是风险事实，不能直接替代卖出分析；展示大盘、板块、个股的来源与时效，缺失明确未知。允许用户记录持有观察、减仓退出、做 T 观察方案，选择方案不撤销风险、不自动成交；不能因深亏就称适合做 T 或保证解套，也不能因环境尚可而屏蔽既有硬止损。L1 单独触发保留提醒但不禁用做 T 观察；只有大盘、板块、个股同交易日均有可核验证据走弱时，才突出复核减仓或退出。新增分析只作解释与人工方案，不修改冻结策略阈值。

2026-10-26 补充交付：选择人工方案后提供对应条件操作计划，复用当前行情与最近完成日不复权价位，逐项说明来源与时效；历史高低点仅是观察参考，不是已验证买卖信号。用户明确设定数量、卖出/回补价与实际费用假设后测算两腿净现金及经济成本；数量受当前可卖底仓与板块回补单位约束。计划绑定仓位身份、成本和数量，持仓变化后重算；旧数据不得作为当前可执行价，三维同日走弱暂停做 T 并复核风险。方案和测算不自动成交、不更改冻结阈值、不保证解套。

2026-10-26 模型接口补充：持仓方案可按需调用现有模型 API，以趋势、做 T、风控三个角色先独立评估、再回应同行，最后汇总条件计划、分歧与缺失证据。角色观点由模型生成，不预设看多/看空或多数表决；结构化价位只能引用服务端证据。讨论有并发锁、超时与失败状态，同模型多角色如实标注；保留主链确定性风险规则、评分和人工成交边界，接口凭据不落盘。

2026-10-27 新手流程验收：首屏只展示股票、检查时风险事实和三种处理方式；选定后突出下一步与取消条件，做 T 保留先卖后买两腿。费用试算、完整证据和模型讨论按需展开，不要求用户先理解技术过程。减少重复提示；更新行情同步未手改价位，保留并标明用户设价；暂停时保留真实原因，不能出现界面说暂停却仍能提交的冲突。该交互重构不构成策略有效性或收益验证。

2026-10-27 跟进闭环补充：选择前以三条路的保留目标与代价帮助比较，不伪造个性化推荐；选择后按有效行情展示未到观察价、到价但转弱未确认、破低需复核等事实状态。可见页面沿用一分钟只读刷新，不能将触价推断为信号或无人值守推送。实际卖出必须由当前租户的同仓成交凭据确认；展示成交事实、剩余股数与重新核对入口，不自动认定为某次做 T 的第一腿或把普通买入当回补。

---

## 8. 自我校验(回到核心问题)

任何机制要进项目,先回答这 5 个问题:

1. **策略来源**:经济或市场结构假设是什么?写进 `hypothesis` 字段。
2. **数据消毒**:幸存者偏差、复权口径、停牌涨跌停、财报时间戳是否都对齐?
3. **验证方式**:用 Purged + Embargoed Walk-Forward 或 CPCV,**不要用普通 K 折**。
4. **统计折扣**:试了 N 组参数挑出最好的,Sharpe 必须用 Deflated Sharpe Ratio 折扣。
5. **运行后学习**:学习对象是 IC / 命中率分布 / 特征漂移(KS 检验),**不是 PnL**。漂移触发停用,不触发"调参"。

参考资料:
- López de Prado, *Advances in Financial Machine Learning*, ch.7(Purged CV)和 ch.14(DSR)。
- Bailey & López de Prado, *The Deflated Sharpe Ratio*。
- CPCV 对比:https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4686376

---

## 9. 不允许的操作清单(给小米Pro 看)

- ❌ 写 `datetime.now()` 不带 tz
- ❌ 在策略代码里写字面量阈值
- ❌ 用前复权数据进 ledger / 回测
- ❌ 用 `df.shift(-N)` / 中心化 rolling / `df.mean()` 全期归一化做特征
- ❌ 把 `not_executable` 信号当成"亏损样本"计入胜率
- ❌ 拿事后才知道的指数/板块归类做选股
- ❌ 在 `evaluate` 内部访问磁盘、网络或全局状态
- ❌ 用 LLM 输出直接覆盖打分结果
- ❌ 让权重学习"看到上周亏了就调"(必须有冷却期)
- ❌ 用普通 K 折做时序数据验证

---

## 10. 回测计量口径规范（2026-09 校正）

> 本节是 `reports/pbo-attribution-2026-09-07.md` 归因分析的工程落地。所有回测/账本/门禁必须遵循以下口径，避免 PBO/DSR 读数不可信。

### 10.1 收益聚合

- **单期（一个信号日）**：`total_return = mean(各股收益%)`（等权并行持仓），**不是** `cumprod` 串行复利。
- **跨期（Overall）**：`equity = cumprod(1 + 各期组合收益)`，年化按 `(1+R)^(periods_per_year / n_periods)`，Sharpe 用跨期收益序列 + `sqrt(periods_per_year)` 年化。
- **periods_per_year**：`252.0 / test_period_days`（如 test=30 天 → 8.4），不是固定 252。

### 10.2 T+1 退出语义

`_resolve_exit` / `_resolve_exit_tiered` 必须跳过 entry bar 当日，从第二根 bar 开始检查止损/止盈。与 `variant_account.py` 的 `available_quantity` 机制对齐。

### 10.3 CSCV 诊断强化

`WalkForwardTester.calculate_cscv_pbo` 输出：
- 主口径 `pbo` = mean(λ ≤ 0)（Bailey & López de Prado 原始定义）
- 对照口径 `pbo_strict` = mean(λ < 0)（剔除中性组合）
- `n_lambda_eq_0` / `n_lambda_lt_0` / `lambda_p25/p50/p75/std` 分布明细
- `cscv_reliability`：`"ok"` 或 `"degraded"`（`block_size < 4` 或 `N < 8` 时告警）

### 10.4 Grid Profile 选择

| profile | N 变体 | 用途 | 因子族多样性 |
|---------|--------|------|-------------|
| `stable` | 5 | 上线门禁（保守） | 仅 momentum |
| `stable_plus` | 8 | 功效增强（推荐） | +volume / mean_reversion |
| `exploratory` | 13 | 研究探索 | 全部 |
| `htf_mr` | 8 | T3 方案 A 因子族替换验证（htf+mr 换 mom+tr，不改默认） | htf_mr × 8 |
| `planb_v1/v2/v3` | 各 8 | Plan-B 对照臂（权重配比实验） | planb |

**门禁判定**：`stable` 或 `stable_plus` 的 PBO < 50% 且 DSR > 1.0。`exploratory` / `htf_mr` / `planb_*` 仅用于研究与对照，不参与生产门禁。

### 10.5 涨跌停板块分类

单一事实源：`data/source.py::get_limit_pct`。账本 fallback `_fallback_limit_pct` 必须与之对齐：
- ST：`"*ST" in name or name.endswith("ST")` → 5%
- 创业板/科创板：`300/301/688/689` 前缀 → 20%
- 北交所：`43/83/87/88/920` 前缀 → 30%
- 主板：其他 → 10%

### 10.6 急跌保护（crash_protection）

当 `market_regime="crash"`（大盘暴跌检测）时，`WalkForwardTester` 自动将 `top_n` 减半，降低高β暴露。这是对报告 §4 急跌段全族软肋的工程响应：

- 触发条件：`SystemRiskManager.check_market` 判定 `hs300_change <= market_crash_threshold`
- 回测生效：`WalkForwardTester(crash_protection=True)`，`_run_single_period` 内减半 `selected`
- 生产生效：`--crash-protection` CLI 参数
- 注意：这是**硬编码减半**，不是参数调参；不触碰"冻结调参"红线

---

## 11. 联系点

- 文档同步:任何改动 `architecture.md` 的 PR 必须包含 `docs:` 前缀,Claude 优先审查。
- 阈值变更:阈值 PR 必须附 walk-forward 回测报告,DSR 折扣后 > 1.0 才允许合并。
- 实盘前清单:见 `docs/checklist_before_live.md`。
