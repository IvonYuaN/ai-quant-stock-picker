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
当前中心是从市场方向与选股、个股依据、AI讨论到买卖条件和持续监控的完整决策流程；已有持仓与未持仓候选分别建模。成交记账和费用试算属于辅助，不作为策略分析前提。前轮D1–D3及持仓局部交付不能等同本目标完成。

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

#### 盘中扫描与日内候选身份分离

盘中轮转批次按实际请求标的及原有效配置使用独立评分缓存，不能把当日冻结候选代码当成新批次的解析股票池。同批仅复用评分，仍对全部已解析标的取得当前行情；不同批次真实评分。交易日首次最终名单及顺序保留在原 v2 冻结文件，后续实际发现追加独立、幂等的 added 记录，不能因不在本批而标失效；旧未完成最终筛选的记录不得直接用 screened 候选补成 final。

后续批次追加前硬校验阈值版本、价格/金额筛选参数与实际策略权重。已有真实参数指纹时，以当前阈值和各自原运行描述重建核对（精确权重覆盖展示舍入值），同 version 的隐藏参数变更也拒绝混入；行情源自然回退及普通 regime/解释文本变化不单独视作参数修改。调用方未提供阈值而真实指纹冲突时保守拒绝，不猜测变更原因。

新批次快照另存完整阈值配置指纹，即使首次无候选也能核对后续配置；旧 v2 字段缺失仍可读，不补写原完整快照。旧完全空且无配置身份的名单不能追加，明确报告无法核对；已持久 added 的原参数同样约束后续批次。名单读、参数验证与追加在同一名单锁内串行；股票池/交易日重置清除旧游标成功身份。来源时间若带分钟须按上海时区完整时点拒绝未来行情，纯日精度继续如实标 day。

本单元首轮完整回归6285项通过；独立审查发现并修复空名单参数混用与股票池切换游标卡0，最终83项定向回归通过，原P1/P2隔离复现复审通过。生产当前旧v2记录已只读确认finalized=true，尚未运行新盘中批次，不能据此称覆盖恢复。

每批另补当前可见候选（受既有 limit 约束）的真实行情用于跟进，与原批次去重取数；补行情和基准不进入本批评分或覆盖分子。本轮 CSV 旁的 `.scan.json` 仅存请求/扫描/成功/缺失代码与有限的逐代码来源证据，不存历史 K 线。source_time 保留可取得的交易日精度，fetched_at 单列，不能冒充供应商分钟时点。页面只更新真实同代码行情的价格和 provenance，评分、策略与原阈值不变；缺失或缓存降级明确 unknown。

cursor 提交同时核对本轮唯一 CSV 与扫描凭据的精确批次成员，最低有效覆盖沿用既有配置，基准与额外候选均不充数。7/256 成功不得推进或冻结不完整评分；空候选但行情覆盖有效可以推进。提交加锁并幂等，旧批次提交/失败不得回退新批次；保留原冻结文件，不通过删除数据、改阈值或改 cron 绕过。

收盘计算使用独立、按交易日校验身份的固定评分文件，不与盘中稳定名单或轮转批次共用。生产10月27日daily曾在行情读取前误读早盘256只标的的冻结身份而中止，导致选股、讨论与首页writer均未推进；不得删除旧记录或强配指纹恢复。收盘必须解析其完整配置股票池、取得当日行情后独立评分/最终筛选，正式ledger只消费收盘结果；同一收盘配置重试复用，收盘参数或股票池变化仍拒绝混用。盘中原名单及增量文件不改写；首页沿用已有展示冻结，保持同日顺序并刷新安全状态，不把盘中名单合并成正式收盘信号。验收覆盖真实形状的旧256股记录、完整收盘取数、幂等重试、配置冲突、失败不污染原记录与首页保序。本地全仓6539项通过（9 skipped、16 deselected），原定向54项、daily/home/CLI回归218项及新增正式ledger隔离场景通过，独立只读审查无P1/P2阻塞。CI、发布与实际生产策略运行结果另记，不把测试通过当作已补跑当日研究。

收盘计算隔离已随#485于2026-10-28北京时间00:05:21发布81cd7a17。8项CI全绿、CLEAN后squash；公网版本/制品hash/四主入口、独立桌面与320/390px通过，真实1仓的数量/成本/方案未变；API/preview active，可用内存683MiB、swap0。ECS未构建、跑重型测试、改系统配置或重启主机。旧盘中256股冻结保留，close新文件尚不存在：未跨日伪造10月27日执行，也未补跑高负载daily；变体验证仍截至10月26日，不能称研究已恢复或门禁放行。

当前单元#484：首页缺席候选不等于策略失效。本轮未提供同代码新研究时保留原名单、评分、来源时点和原讨论，单列展示“本轮无新研究”，不新增invalidated或把旧证据改成新时点。已有源过期、真实参数变化及风险门禁仍独立生效；明确invalidated与“仅本轮缺席”分开：前者阻止AI生成/采用新的入场条件，并因实质依据变化让已采用观察进入复核；后者只阻止用旧研究采用新条件，不因展示提示本身暂停未变化的已有观察。显式失效后再次缺席仍保留失效状态，不能同时展示“缺席不代表失效”。缺席及恢复属于覆盖元数据，不进入候选实质依据哈希；原讨论保留不等于补成当轮完整研究或放行推荐。验收涵盖连续缺席后恢复、单文件/index一致、原讨论/来源时间保留、真实失效/参数/源过期仍受限、决策面板不把旧候选当作本轮可推进，以及已有观察只在覆盖变化时保持原条件。

#484 本地验收：6549 passed、9 skipped、16 deselected；定向192项后端、1171项前端契约断言、前端typecheck/build及 `ruff check .` 通过。CI/PR/发布和真实线上状态另记；本项不是市场与板块证据、当日选股验证、AI策略质量或完整Goal完成证明。

### 7.2 最终交付 Goal 与缺口（2026-10-27）

§7.1 的 M0–M3 与 #458–#460 是已交付基础链路，不能等同最终完成。按下表持续开发；状态只随实际证据更新。

仓主再次明确主线优先级：**市场与板块大方向 → 选股理由 → AI 讨论路线及分歧 → 买卖条件与失效条件 → 持续监控与变化复核**。以这条链能否帮助新手判断“现在该等什么、为什么、何时需要改策略”为最终交付标准。成交回录/费用/做T两腿核算是辅助事实入口，不以其表单齐全或回归数量替代策略能力；数据源、模型与运行稳定性修复必须服务于上述链路，不能单独宣称交付完成。

2026-10-28 核心交付审计：以下五项是当前验收主轴；后面的 G1–G5 保留为已有持仓子流程记录，不能占据选股研究的总目标。

| 优先级 | 新手要得到的结果 | 已有能力与当前真实缺口 |
|---|---|---|
| D1 大方向 | 看懂市场和哪些板块值得研究、哪些暂缓，并能核对原因与时效 | 指数及方向身份已接入；行业源改为同源dataapi，随#489上线a44ef2e8；公网首页真实行业榜及3只候选+1只实际持仓的板块来源通过；全A宽度仍缺，不能加总行业冒充 |
| D2 选股 | 在上述背景下比较候选的支持、反证和等待条件，解释为什么研究A而暂缓B | 同日名单/评分/逐股证据已保留；现有横向比较偏评分和证据条数，尚缺同背景下的候选取舍 |
| D3 AI讨论 | 用真实证据独立提出意见、质疑与保留分歧，汇总成能理解的策略选择 | 三角色两轮及汇总已实现；#490 已补独立 typed 技术事实、原始 bar 日期/时间与参数身份，刷新现价不覆盖；模型可引用经二次校验的冻结量比/动能/RSI/MACD/KDJ/偏离（实际未计算的指标仍未知）。错误单位/周期、价格挪用、旧依据当前化、未来源数值和冻结指标行动门槛拒绝。真实模型实连与生产新研究产物仍需验证，不把定向/合成结果当策略质量证明 |
| D4 买卖策略 | 解释什么情况下继续等、可以复核买卖、何时原理由失效；已有持仓比较持有/减仓/做T | 已有三路线及有源参考价；日高低/前收不能证明支撑突破或完整买卖策略，持仓目前仅两根完成日raw高低收，仍缺量价窗口等支持，不以固定比例或表单补齐冒充策略 |
| D5 持续监控 | 不开页面也能知道原判断是否成立，关键变化才提醒，并说明应复核哪个条件 | 已有报价谓词和方向/实质依据变化复核；量能、止跌等文字前提尚不能逐项自动核验，下一单元需类型化可测条件，未可测的保留人工核验，不能把触价当全条件成立 |

开发顺序以有实际根因的完整单元推进：本轮先恢复 D1 真实行业事实并收尾研究任务身份；随后修 D3 指标来源/校验，再打通 D2 比较与 D4/D5 条件验证。记录、费用与成交操作不再作为主功能扩展方向，也不要求先填写它们才能分析。每个单元仍须测试、独立审查、CI及自动发布后的真实入口验收；合成证据不能证明真实模型或策略效果。

当前D3单元（issue #490）：为候选冻结技术事实独立保存原来源、源时点、研究日和参数身份；同日更新现价不得改写指标出处，旧记录缺原出处不能用最新报价倒填。数值事实从类型化原输入生成，按指标绑定单位及已知周期，只支持现有动能、量比、RSI、MACD、KDJ与均线偏离，不从展示字符串、纸面止损止盈推导买卖价。模型只接收经当前研究时效核验的冻结事实，能准确引用已有指标并解释支持与反证；未知周期不补造，冻结数值不能升级为当前触发门槛或价格。历史采用依据只在历史比较命名空间内使用，普通报价刷新不制造重选。前端在原证据区区分冻结研究与最新报价，不新增表单负担。验收覆盖严格新旧reader、冻结及索引、错误单位/周期/价格挪用、缺失/未来/过期出处、真实模型有界实连与隔离桌面手机入口；完成后继续D2取舍与D4/D5可测条件，不据此宣称策略主线完成。

D3发布前补充回归：真实bridge在HTTP序列化前保留tuple，技术事实校验与现价哈希剔除同时兼容该内部形状；英文指标名、冻结字样不能豁免错误单位或伪造门槛。原RSI等指标未定义时不把评分fallback零认证成事实，来源存在历史窗口overlay时保留原历史来源说明，评分和价格计算不变。旧研究无出处不补造；消息追加/独立变体等未产出原technical_source的路径仍明确缺失，分钟时点没有原始保留时只显示日精度。真实3.0 Flash隔离实连已读到指标，但部分角色超时、漏单位而被拒，完整讨论稳定性仍未验收；合成桌面/320/390px全流程及原方案持久化通过不等同真实策略质量。

D3来源修复与内部形状回归已随#492/#493于2026-10-29北京时间09:55:10自动发布98f6135d。8项CI全绿、CLEAN、无审查评论后squash；6756项全仓回归及新增测试后的98项定向、1256项前端断言/typecheck/build通过。公网版本/制品、四主入口及隔离桌面/320/390px通过，166份原持仓/候选方案文件哈希未变；API/preview active，可用内存799MiB/swap0。未在ECS构建或跑重型测试，未改env/cron/系统配置或重启主机。一次真实3.0 Flash隔离复核45.05秒失败（两角色超时、一漏指标单位），无重试/生产写入，不称讨论全面稳定。

当前优先修复（issue #495）：公网D1大盘宽度真实性。真实首页同时显示“普跌格局”和上涨100/下跌0；旧同步取数只要返回短页即视为全市场，丢弃总量/股票身份/源时点，并借涨停池日期认证。该无源宽度还进入候选/持仓方向与情绪宽度维度，不能只隐藏页面或调整冰点档位。复用现有严格逐只合同：稳定排序、完整覆盖源total、唯一股票、原始f124、有限资源与源时效；API/缓存读侧、AI及情绪消费同样校验，旧缓存无出处明确未知。涨跌停池时间与宽度时间分开，不新增策略阈值或用指数/行业总和替代全A。验收包括源报5400但仅首100条、重复/漏页/总量漂移、缺时点、旧缓存当天写入、完整真实形状，以及首页/候选/持仓一致未知或有据方向。随后继续issue #494的AI条件逐项监控与跨候选取舍，成交回录保持辅助。

#495本地验收：完整回归6787 passed、9 skipped、16 deselected；最终143项定向、1259项前端断言/typecheck/build、ruff与上传/密钥检查通过。独立真实API及桌面/320/390px流程验证旧100/0缓存不可认证、完整覆盖保留原来源时点、当天写入不续期旧行情，异日涨跌停池计数置未知而不清空合法宽度；采用/离页/变化复核与原方案保留通过。隔离来源和模型均为合成，不代表全A源或真实AI讨论稳定性已恢复。独立只读复审无阻塞P1/P2；CI与公网结果另记。

#495已随#496于2026-10-29北京时间10:49:01自动发布31827bdf。8项CI全绿、CLEAN、无审查评论后squash；公网版本/制品、四入口、真实行业榜、大盘宽度未知降级与桌面/320/390px通过，167份原持仓/候选方案文件哈希未变。源未完整返回时明确缺宽度，不再显示100/0及旧冰点结论；API/preview active，未改env/cron/系统配置或重启主机。发布脚本默认预期前一交易日而盘中snapshot已为10月29日，该日期子检查曾报不一致；真实公网snapshot独立验收通过，不能据默认日期误报宣称当日研究质量已验收。后续继续核心条件与取舍，宽度源恢复/真实模型稳定性仍未验收。

当前D4/D5下一单元（issue #494）：AI条件逐项跟进。先交付未持仓候选的完整纵向链路，并将纯条件目录设计为持仓可复用：当前有源报价比较、既有市场宽度方向及所属行业方向按稳定ID引用；AI自由选择相关前提/失效条件，不固定思考步骤、不从文字推算新阈值、不把冻结技术指标认证成当前量价。采用时冻结原定义、来源价、比较方向和讨论动作身份，延续现有CAS/幂等；旧讨论与旧观察保留原文与报价规则，标注未逐项登记，不自动迁移。后台同轮复用当前事实，返回已满足/未满足/缺依据/人工核验；即使原依据变化需重核，也展示具体项目，价格满足不等于整体前提成立。通知按合同身份及稳定项目状态去重，普通报价/时间/文案刷新不重发，外发前再次核对原冻结来源时效与当前状态。首页只给具体下一步，详情按需展开原文和来源，不加成交、数量、费用表单。验收覆盖模型引用/拒绝未知或矛盾ID、采用冻结/篡改/CAS、离页逐项变化/失败/去重、旧记录兼容及桌面手机流程。候选切片上线后继续持仓买卖/做T适配、真实模型稳定性和同背景跨候选取舍；不能以候选切片替代整个Goal。

#494候选切片本地验收：6845 passed、9 skipped、16 deselected；157项定向、1292项前端断言/typecheck/build、ruff、上传及密钥检查通过。隔离真实API与桌面/320/390px验证采用后离页跟进、到价但市场前提未满足、来源过期逐项未知、依据变化仍可见具体项目、重核不自动覆盖原方案、暂缓不创建参与任务及旧记录兼容；未创建持仓或成交。独立只读复审无剩余P1/P2。单次真实agnes-3.0-flash复核71.27秒，三份有效角色意见、一份技术事实校验拒绝、一份超时，未形成有效汇总；目录四项已送入，但不能宣称真实模型条件选择或完整讨论已验收。没有重试或生产数据写入。CI/发布结果另记；持仓条件适配与同背景跨候选取舍仍未完成。

#494候选切片已随#497在8项CI全绿、CLEAN且无审查评论后squash合并c9ee98fd；一次CI卡在依赖安装，经取消该运行并重跑后全绿。2026-10-29北京时间11:19尝试发布时被idle-window guard正确拒绝：现有盘中研究正在运行，未切换release或重启服务，生产仍31827bdf。暂存制品与私有一致备份保留，待空闲窗口连同下一讨论修复自动发布，不强行打断研究。

#497已于2026-10-29北京时间11:28:49自动发布c9ee98fd。研究任务退出后重试，未强制越过idle guard；公网版本/制品、四入口、四项条件目录和桌面/320/390px通过，API/preview active。167份原文件中166份哈希与11:01基线一致，1份portfolio在首次发布备份前已有变化；全部portfolio与发布前一致备份相符，未发现本次发布改写。服务器DNS及默认交易日子检查曾报错，独立公网验收通过；不能据脚本最终exit0替代公网验证。无npm/vite/重型测试、env/cron/系统配置变更或主机重启。

下一核心修复（#494讨论可用性）：单次真实模型已证明两名角色初轮有效、一名回应超时会让汇总完全跳过且不解释原因。汇总应按不同角色取最新有效意见（成功回应优先，回应失败保留该角色初轮），被拒绝原文不能送进汇总或计入数量；至少两名角色才允许部分汇总，缺失角色/回应状态独立记录并明确展示，不冒充全员完成。候选仍仅complete可采用；持仓原先已有的两份回应可用路径保持，使用初轮补位的部分汇总仅供阅读、不得采用新观察。不足两名角色或汇总失败返回实际原因，原方案保持不变，不增加重试或请求预算。澄清quote_updated仅表示相对采用时的事实变化，不表示时效失效；时效以源时间为准。有源当日涨跌幅和冻结技术指标必须分开核验，避免误拦合法报价事实，也不能放行错值、错单位、错周期或无来源技术阈值。验收以真实失败形状离线回放、不同角色计数、无效内容隔离、部分结果阅读/禁采用和一次有界真实实连为准。

讨论可用性单元本地验收：最终全仓6879 passed、9 skipped、16 deselected；讨论/采用兼容定向183项、报价/技术来源77项通过；前端1308项断言/typecheck/build、ruff、preflight与密钥检查通过。独立真实API及桌面/320/390px验证候选与持仓部分汇总可读、缺席/未回应角色明确、初轮补位不能采用新观察，随后完整讨论仍可采用并离页跟进；原两只合成持仓与成交记录保持。复审发现的板块/指数误当个股及后置历史标识绕过已修，混合主体句保守拒绝，无剩余P1/P2。单次真实agnes-3.0-flash在45.06秒后三角色均超时，明确返回不足两名角色而未调用汇总，无重试/生产写入，原方案保持；本单元不代表真实模型恢复。CI/发布结果另记。

当前D4/D5持仓完整单元：让已采用的持有、减仓和做T条件与候选共用逐项检查能力，回答“价格到了但什么前提还没成立、哪个失效条件已经发生”。模型可自由引用现有四项目录或留空，不增加固定思考步骤、策略阈值、交易表单或费用门槛。新增持仓合同身份独立区分候选，保留已有候选v1定义/摘要和旧持仓；采用时冻结具体动作、来源价格、方向和原文，按原CAS与锁保存。当前有源大盘/行业/报价由持仓适配层认证，不用当前时间替旧源续期。后台即使处于暂停、风险或依据变化状态也返回具体条件，但硬风险、T+1、卖后买顺序、持仓变化优先，不让条件匹配解除约束。未登记的量价文字继续人工复核；触价不等于买卖或成交。通知以合同和稳定项目状态去重并在外发前复核时效，普通数值/时间刷新不重复提示。前端和首页先给一个下一步，再按需展开状态/来源；旧记录明确未逐项登记，不自动迁移。验收包括三路线、缺源/过期、方向变化、合同篡改/CAS、离页与去重、两只合成仓隔离桌面手机流程及一次有界真实模型诊断；合成结果不能替代真实两仓与模型恢复。之后继续同背景跨候选取舍与真实模型可用性，不能以本单元结束Goal。

持仓条件单元本地验收：全仓6908 passed、9 skipped、16 deselected，新增29项条件守卫，前端48文件1358项断言/typecheck/build、ruff/preflight/secret scan通过。隔离两只合成持仓真实API与桌面/320/390px验证三路线、到价仍缺大盘前提、行业失效、源过期未知、tick去重和损坏引用安全展示；原持仓及采用条件不因监控而改写，无实际成交/生产写入。独立只读审查发现的外发重复取价/行业批次cache身份与损坏引用P2均已修复，无余项。真实agnes-3.0-flash单次103.81秒形成partial_initial汇总、自由引用现有条件ID；一份角色因数字行动校验拒绝、一份回应45秒超时，汇总仅供阅读、不可采用。最小生成本地21.35秒/ECS16.07秒成功，不能替代完整讨论验收。CI与自动发布结果另记；跨候选取舍和真实模型稳定性继续保持未完成。

当前D2交付单元：今日候选直接呈现同日取舍，不要求新手先操作指标表或成交表。仅在候选研究日相同且逐票当前大盘方向具有同来源、同源时点、已核验状态时，比较原研究支持、当前板块来源、反证/缺口、同票AI分歧和已采用条件的后台跟进；否则明确无法横向排序。可先研究只表示研究材料与既有门禁允许核对观察条件，不表示应买入；多只均满足时不靠分数或模型投票硬排优先。历史页不借用当前行情，旧研究理由标明冻结身份；量价、收益率及技术指标不在此派生新买卖门槛。指标表降为按需展开，个股页仍负责完整讨论、条件采用与失效核验，成交回录保持辅助。验收需覆盖不同研究日/源时点、缺源、模型部分讨论、门禁阻塞、后台状态及手机端；本地测试与上线状态分开记录。D2上线后继续D3真实模型可用性、D4量价支持与D5长期噪音观察，不能将候选对比当作总Goal完成。

D2本地验证：完整后端6908 passed、9 skipped、16 deselected；前端48文件1376项断言、TypeScript与生产构建通过，ruff、secret scan、preflight通过。独立临时真实API与无头桌面/390/320px验证两只合成候选的同源大盘、各自板块、名字、研究入口、单票接口失败后另一票仍显示、手机无横向溢出；没有生产数据写入。模型讨论来自既有逐票记录，跨候选尚无真实模型汇总；合成来源不能证明生产大盘宽度或策略效果。CI、合并与公网发布另行记录。

D2 已随 #501 于2026-10-29北京时间13:04发布 b7f29ea5；8项CI全绿，公网四入口及隔离桌面/390/320px通过。对比原先排在长指标详情之后，下一界面单元将同日取舍提前到逐票详情前，并让首页按“大盘/板块→候选取舍→AI讨论→已采用方案跟进”呈现。此调整只改善阅读与入口，不扩大评分、模型结论或买卖信号的证据边界。随后优先处理真实模型讨论稳定性、可核验量价条件与离页提醒质量；成交表单仍属辅助。

候选入口调整已随 #502 在八项 CI 全绿、CLEAN、无未决评论后合并 b278922d，并于2026-10-29北京时间13:28:34自动发布。公网版本、index 制品、四项真实路由、桌面/390/320px候选先于方案跟进且无页面横向溢出通过；170份发布前私有备份与发布后文件哈希一致，现有1只真实持仓保留。未在ECS构建或改系统配置。本项只是阅读顺序交付，不代表策略能力完成。

下一完整单元按仓主要求并行开发、串行集成与发布：D3 去除模型请求内重复证据和重复汇总意见，完整两轮审计/原采用证据/门禁仍保留，澄清结构化动作合同且不指定模型思考路径；D4 先为已有持仓接入本地有源、不复权、连续的5/20完成日量价事实，复用日报窗口，缺量/停牌/缺日保留缺口，历史高低只作观察参考，统计不能充当当前触发门槛；D5 复用同轮批量报价并校验待发检查时效，继续按稳定条件身份去重。三线不把成交表单当主功能，不改冻结阈值、cron、ACL或系统配置。

D1 同单元补齐真实入口发现的读取缺口：六个市场接口原来全部结束才呈现，慢的成交额/情绪源会挡住已取得的指数与行业。各接口独立提交成功事实并保留待取、失败、空数据区别；刷新/卸载取消旧请求，旧结果不得覆盖新一轮。首页先显示已取得的大方向依据，不把未完成读取说成缺源，也不把指数替代全A宽度。验收包含一个接口长时间未结束时其余可见、单项失败、重刷竞态、桌面与320/390px，以及持仓量价API→真实模型请求结构和离页通知回归。真实全A来源、完整模型讨论成功率与长期提醒质量仍分别验收。

本单元本地真实链路证据：两只临时持仓、40条合成不复权SQLite日线经原读取器/API送入全部真实模型请求，5/20窗口与API一致，数据与持仓未改。单次agnes-3.0-flash用时120.11秒、无重试，partial且无有效汇总：初轮趋势/风控有效，做T角色引用被拒，趋势回应和汇总各45秒超时。临时缓存时间晚于冻结参考时点，本次背景正确降级unknown；夹具已离线校准，不能将该次结果当作完整背景下的策略质量。后续校验补修区分窗口周期与价格，并拒绝参考价日别/窗口/高低身份混淆，原错误输出仍拒绝，不放宽成完整讨论。

独立临时真实API桌面/390/320px六项纵向流程通过：情绪未完成时指数行业先可见，刷新取消旧轮、单源失败保留其余；两窗口可展开、缺量明确降级，比较路线无需成交/费用。浏览器无错误，模型交互用合成结果，与上述真实实连分别记载；全A宽度、真实完整讨论稳定性与长期提醒仍未完成。

当前策略恢复单元（2026-10-29）：部分卖出后，减仓/做T路线只能借费用试算清除待核对状态，新AI计划分析又会被同一状态隐藏。补齐三路线共用的人工余仓核对动作：页面先展示最新卖出与当前剩余/可卖股份，用户明确确认后按仓位、最新卖出凭据及revision做CAS；不新增成交、费用、不改风险规则、不自动恢复旧观察或试算。确认将旧watch停止并留审计，清除旧讨论/比较；旧试算仍保持旧快照，不伪装为当前。用户以当前余仓重新分析并明确采用新条件后，才能恢复该新方案的离页跟进；已确认卖出跨路线保留，下一笔卖出再次要求核对且优先于旧停止状态显示。同路线保存不替代人工余仓核对。新生成的讨论按其仓位与review_revision校验，不因存在待核对卖出就无差别隐藏；读取分析本身不确认成交。验收覆盖三路线、费用可选、旧watch保持需重核、第二笔卖出/并发/跨租户/清仓重开拒绝及桌面手机全过程。真实3.0 Flash本轮基线45.04秒失败（两角色返回完整JSON但违反动作合同，一角色超时），不把流程修复宣称模型稳定性恢复。

策略恢复本地验证：7019项完整后端回归、214项定向回归、50文件1413项前端断言、类型/构建、ruff/密钥扫描/上传预检通过；独立真实临时API与1440/390/320px三路线卖出后恢复流程通过，独立审查发现的同hold隐式核对、停止状态遮蔽第二笔卖出已修复并复核。来源/模型合成，不改真实持仓，真实模型基线仍失败。CI及发布另记。

模型合同一致性与可恢复诊断单元（2026-10-29）：以当前真实返回离线回放为基线，1000股与5/20历史统计进入动作的输出继续拒绝，不删除、替换或修补模型原答案。生成schema仅提供当前硬约束允许的动作：不可卖/行情缺口不提供卖出，暂停做T不提供回补，无当前来源价不提供买卖；候选被门禁阻塞时仅提供暂缓动作，三路线比较仍完整。服务端独立守卫保持，并显式拒绝动作正文引用ID及独立轮虚构同行，不靠引用ID数字恰好不在价格集合里碰巧拦截。固定安全错误码说明输出哪里不符合要求，不暴露提供商原文、异常字符串或密钥；可读角色保留，拒绝意见不能进入同行/汇总或解除原方案。修正新环境示例默认型号漂移，不改线上配置、不假设提供商支持structured output。验收覆盖生成/解析一致性、1元价格碰撞、同行初轮/交叉轮、真实失败离线回放、失败原因API/桌面手机可读与无重试，最后单次有界真实3.0 Flash实连；不以诊断修复宣称模型全面恢复。

本单元完整回归7118 passed、9 skipped、16 deselected；最终候选引用ID补充后397项定向通过，前端51文件1425项断言/类型/生产构建通过，ruff/上传预检/密钥扫描通过。独立临时真实API与1440/390/320px验证持仓和候选的固定失败原因分区、无重复通用错误、正常角色保留、重试恢复；三路线卖后重核→新分析→人工采用→离页跟进再次通过，合成持仓/成交/规则未被策略流程改写。独立审查发现的候选1元引用碰撞及汇总原因重复已修复，无剩余P1/P2。单次真实3.0 Flash用时45.06秒，三角色均超时，未生成汇总、无重试或生产写入；API与模型请求中的40条合成不复权日线事实一致。UI/合同修复不等于真实讨论恢复。CI、合并及发布另记，Goal继续。

#507已在8项CI全绿、CLEAN、无未决审查后合并并于2026-10-29北京时间17:17:17自动发布753dd2a4。首次idle guard拒绝时未切换或中断研究，任务退出后重试成功；独立公网版本/制品及15组桌面/390/320px模块检查通过，原183租户意图与私有一致备份逐份相符，保留1只真实持仓。GET沿用原行为新增1个空访客身份。API/preview active、可用内存681MiB/swap0；未在ECS构建或改env/cron/系统配置，未重启主机。发布脚本已知#499仍出现日期子检查报错后最终verified；本轮独立公网验收不依赖exit0。研究gate、全A来源与真实模型稳定性仍未完成。

当前D3诊断：先做同一配置的有界最小请求、单角色完整请求与传输阶段对照，区分连接、等待响应头、读取响应及格式校验，不因笼统timeout修改策略边界、增加重试或延长预算。现有14k字符请求确有重复背景，但不能据体积断言根因；原证据/采用身份/两轮审计必须保留。真实接口与合成行情分别标注。

当前D5修复单元：无观察合同时的人工持有方案，检查时L1风险仍有效、外发前报价已经过期，现有发送校验会继续称“当前风险线仍触发”。复用原有来源时间校验，在外发前拒绝已失效的行情事实，下一轮按真实状态重新评估；不能用检查时间给旧报价续期。部分卖出、方案/仓位变化等不依赖行情的复核通知保持可发送，不因缺报价误吞。此单元不改风险规则、策略阈值、计划/成交、cron、ACL或系统配置。验收包含检查时有效/发送时跨时效、缺失/非法/未来源时点、正常风险通知、无行情的成交/方案变化通知，以及拒绝后不占用投递去重资格。

D5本地验证：7135项全仓回归通过（9 skipped、16 deselected），138项跟进/投递/条件定向通过，ruff/上传预检/密钥扫描通过。新增10个场景覆盖源过期/无时点/非法时点、安全豁免以及真实持久投递链：过期先取消且不外发/不消费runtime资格，新鲜检查恢复同一语义键一次投递，重复检查不重发；策略操作未改持仓、成交或规则。独立只读审查无剩余P1/P2。前端源码身份与753dd2a4一致，复用该版本已核验制品，不在ECS构建。CI/发布另记，长期真实提醒噪音未验收。

D3有界对照结果：同一配置最小请求25.38秒；单角色完整请求41.83秒正常返回并通过原校验，连接/TLS约0.13秒；显式SSE单次45秒未收到响应头而取消。直接连接的独立单角色28.72秒返回；随后一次直接连接完整讨论26.83秒结束，三个角色均HTTP200（17.18/22.55/26.80秒），但各自将股数或历史统计写进行动条件而被原规则拒绝，未调用汇总。以上均无自动重试，行情/两仓为临时合成，未改生产数据、型号、代理或预算。一次对照不能证明代理或并发是因果根因，也不能把局部返回成功当完整讨论恢复；后续需同时解决提供商等待波动与生成合同遵循，禁止删除/修补真实拒绝原文凑成功。

#508已在八项CI全绿、CLEAN、无未解决审查后squash并于2026-10-29北京时间17:48:28自动发布26b6d8d6。独立公网版本/前端hash、十五组桌面/320/390px与API合同通过；185份原租户意图与私有一致备份相符，仍1只真实持仓，公网GET沿用既有行为新增1空访客。ECS未构建、跑重型测试、改env/型号/cron/系统配置或重启主机；helper已知#499的日期子检查问题未用exit0掩盖，研究gate仍fail，Goal继续active。

当前D3生成合同单元：针对真实返回中的股数/历史量比写入操作条件与单条观察混用价位，明确解释字段承载已有事实，操作条件承载需核验前提；单条price_ref在condition/invalidation共用，null时不点名其他价位。只对模型请求去重完全等同的顶层/嵌套证据和价位副本，引用保持可回溯；完整解析事实、两轮原文、审计与采用身份保留，差异和旧采用证据不得去除。不改解析器、风控/冻结门槛、型号、代理、预算或重试，不规定模型思考步骤。验收须覆盖原真实拒绝离线回放、正向字段职责、无损/差异保留投影、既有角色/汇总/采用及有界真实完整讨论；提供商等待波动和生成合同分别记录，不以单角色或合成全绿宣称恢复。

本单元本地7162项完整回归通过（9 skipped、16 deselected），206项定向及ruff/预检/密钥扫描通过，独立只读复审无剩余P1/P2。三份旧真实错误原文仍按原合同拒绝；新实连45.06秒partial，一角色36.74秒有效返回、另两角色45秒超时，未进入互评与汇总。请求约14001→12456字符，事实/API一致、持仓/历史库未改，不据此断言延迟根因或稳定性恢复；真实源和两真实仓/长期噪音验收仍未完成，Goal保持active。前端源码未变，复用上一版已核验制品，CI及发布回执另记。

| 顺序 | 用户任务与交付 | 必须证明的结果 | 当前状态 |
|---|---|---|---|
| G1 | 先看懂这只票，再选路 | 无需选方案或填写费率/数量即可读取当前仓位、风险与三维证据；三条路有本仓原因、暂停条件和缺口；选择前模型比较不修改方案或成交 | #461及#464已上线 dd9f8bd4；候选合成证据实连六角色与三路汇总完整通过一次，45秒有界预算及截断诊断生效；长期稳定性与真实持仓质量仍未验收 |
| G2 | 方案符合个人条件并有依据 | 根据可卖底仓、可盯盘时间、资金/风险约束解释适用性；价位/区间和数量有证据或明确保留，不用机械比例凑策略；所有持仓可进入，不以触发提醒为前提 | 唯一持仓工作区、个人条件、版本保护及成交后重核已实现并通过本地桌面/手机流程；已随#467于2026-10-27上线7ef9b559。高低点与费用试算仍不证明策略适用 |
| G3 | 选完有人持续跟进 | 页面外也能识别计划变化、失效、到价待复核；状态持久化，频率/去重有实际验收，旧分析与新事实区分 | 后台跟进、独立通知模式和持久投递已实现；6079项后端测试、887项前端断言及临时真实API桌面/320与390px手机流程通过，已随#467 CI全绿后合并并授权上线7ef9b559。真实持仓与长期噪音观察未完成 |
| G4 | 成交之后继续走完整条路 | 多笔成交、余仓/退出、变仓恢复、人工关联做T两腿及实际费用后才能归因真实效果 | 逐笔凭据、费用、部分/多笔关联与余仓核对已随#469上线；旧成交补证和真实数据观察仍待完成 |
| G5 | 新手能独立使用且服务稳定 | 桌面/手机完整流程、模型实连、源失败/断网/并发/重复操作、已授权发布与回滚；至少两只已有真实持仓和持续噪音观察分别记录 | 未完成；生产已知仅1只真实持仓，不造样例冒充验收 |

G1 的分析读取复用当前 action-plan 与租户仓位校验；增加纯证据比较，只说明当前事实、可核对路径和缺失条件，不预设最佳路线。选择前的模型请求单独标注比较模式，三角色独立/交叉意见后输出三路理由及分歧；无有效结论不能伪装成功。结果与持仓身份、数量、成本、可卖底仓和证据时点绑定，不能覆盖评分、撤销提醒或自动选路。旧模型配置故障须区分提供商不可用与本地解析失败；仅使用真实返回成功的现有模型做有限验证，不自动扫模型或静默改线上配置。

模型迭代不是反复改代码里的型号：运行时以显式配置为准，新增有界、可刷新的提供商目录读取，区分“已列出 / 未列出 / 目录不可用”；目录列出不代表文本能力、费用或推理质量通过验证。用户可在本设备选择目录中的型号，用服务器现有凭据进行本次讨论，偏好按提供商隔离保存且不含凭据；旧偏好失效须明确重选，不静默升级。默认示例对齐已核验目录的 `agnes-3.0-flash`，已有显式旧配置仍需获授权修改。目录故障不阻止确定性持仓分析，不能将模型生命周期故障伪装为策略结论。

G1 汇总恢复（#462）：相同合成证据的单次汇总曾在25秒超时，也曾23.45秒通过校验，不能由短请求或单次成功证明稳定。#464提供有上限、可配置的角色/汇总I/O预算，保留流式进度、取消、单任务租约与无重试；汇总限制展示冗余，保留三路、分歧、缺证与条件，不规定思考步骤或预设路线。提供商长度截断和空内容不能误报为正常完成；须分别记录完整实装编排与失败边界，不能无限延时或用模板补齐。

#464 发布状态：2026-10-27 北京时间14:28，仓主授权后发布 `dd9f8bd4faeca7a5893351ed23dc2195c9ac6088`，运行时型号仍为 `agnes-3.0-flash`，角色/汇总预算45/45秒、各阶段最高60秒；复用本地前端制品，未改env或重启主机。公网桌面/手机、路由、health/version与制品哈希通过；详情见 `docs/position-action-plans.md`。此回执不把候选单次实连升级为长期稳定或策略有效性证明。

#### G2 下一开发单元：每只持仓的唯一处理工作区

目标入口是已有持仓本身。持仓列表的“看处理计划”、提醒卡和该股票研究页均进入同一 `position_id` 工作区，支持深链、刷新和返回原入口；无OPEN提醒也能读、选路及跟进，不能创建假提醒来接入旧接口。自选、已平仓历史与新开仓身份分开。

- **唯一事实源**：同租户portfolio顶层 `position_reviews`，以不可复用的 `position_id` 为键，存个人条件、路线、参数、模型分析、版本及成交核对状态；清仓后保留只读历史，重开新仓不继承。事件只投影当前根状态，旧事件接口适配根存储，不双写。首次迁移只接收同租户同仓身份明确的最近有效旧计划，记来源；有根后绝不回灌旧事件，冲突或身份不明要求重核，不靠猜测选择。已处理/忽略提醒不删除根计划。
- **一致性**：沿用 ae→pf 锁顺序。个人条件、choice-only、试算、兼容事件写入与模型保存均校验 expected_revision、当前仓位快照及风险版本；旧写入拒绝并要求刷新，不能让兼容接口绕过校验。条件变化原子递增revision，旧试算/分析保留为历史并要求重核。无OPEN提醒仍核对该仓实际风险规则的启用、revision、身份和有效时间；无提醒不表示风险解除，缺证明确未知。
- **少量个人条件**：盯盘时间（能/偶尔/不能/未知）、新增资金（不追加/金额上限/未知）、可承受新增损失（金额/未知），可跳过但不把未知写成0。不盯盘使本次做T不宜；能盯盘不能证明适合做T；零追加仍可考虑实际卖出净现金内的回补。风险金额绑定用户确认时价格、份额、时点与市值基准，刷新不重置已发生损失；变仓或重设基准须重新确认，不解除冻结风险规则。
- **数量与价位**：仅从可卖股份、申报单位、用户预算、实际卖出量及已确认费用给可行边界和算式。无可信失效价时不以日高低点、成本或固定比例生成风险仓位；余仓风险须一并说明。价位保留来源、时效与用户设定标记，费用未知不显示净收益，模型说明取舍但不覆盖硬约束。
- **成交之后**：直接读取同租户同position_id的持久成交凭据，不再以event.last_sale为唯一入口；根保存已核对的卖出编号/版本。无提醒仓位部分卖出也进入余仓重核，清仓仅历史可读；做T双腿仍须人工关联，费用假设不当作实际费用。

本单元验收：无提醒持仓可用且不造提醒；未知与0分开；个人条件改变适用性并使旧结果失效；提醒/持仓/研究三入口一份计划；跨租户/清仓重开隔离；并发旧写入拒绝；成交后余仓跟进；数量与费用有依据；模型失败仍能人工选路；独立无头桌面/手机真实临时API流程。当前代码已实施：持仓、提醒、研究三入口共用一份计划；本地临时真实API与独立无头桌面/手机覆盖个人条件、草稿重核、两次部分卖出和清仓归档。模型输出为合成，只验证交互。已于#466 CI全绿后合并ffee7034，并随#467于2026-10-27上线7ef9b559；不宣称G2整体完成。

#### G3 下一开发单元：页面外计划跟进与有据提醒

沿用现有 market-hours monitor 与每租户 LIVE 检查，不新建 cron/daemon，不调用模型。复用本轮已取得的个股行情、大盘/板块上下文和持仓快照，禁止逐仓重复整批取行情。服务端跟进只跟踪已显式保存的价位；未保存高低点不自动变成提醒线。到价仅说明报价位置，不能推断转弱、止跌、成交或做T第一腿完成。

- 根计划新增独立 `followup` 快照与 `followup_mode`（`off`默认、`changes`、`daily`）；默认只在站内记录，外发由用户显式开启。模式保存复用同仓CAS并保留草稿；跟进快照更新不递增人工plan/root版本，不写参数或模型结论。
- 状态覆盖等待、已到保存观察价待复核、低于保存低价需复核、个人/三维暂停、变仓/条件/成交后需重核、行情依据不可用、缺少已保存观察价和清仓。状态带检查与源时点，来源缺失不能被当作风险解除；站内区分最新后台检查与页面即时读取。
- 发送身份绑定同租户同position_id、人工计划/条件、语义状态及频率期，排除报价细微变化、检查时间、模型文本和动态风险高水位。`changes`同一计划同一状态只发一次，即使来回穿越也不刷屏；`daily`同状态每北京时间日期最多一次，`off`与notify=False不消费配额。持续正常等待不发通知。
- 锁内按 ae→pf 核对当前仓位、方案/条件、风险与成交凭据后才写快照；旧检查不覆盖新计划/新成交，发送前再核对。网络期间不持ae/pf锁；内容是检查时事实，有并发时间窗，不承诺实时逐笔监听。
- 复用通知运行时和服务器现有渠道，每租户独立冻结批次/成功凭据。发送前持久化uncertain，成功凭据可恢复崩溃窗口；发送结果未知不重复尝试，明确失败沿用冷却。坏状态失败关闭且显式计入检查失败。已有同仓风险通知覆盖的风险事实不再额外推同义计划消息。
- 新手界面只展示“上次后台看到什么、何时检查、下一步核对什么”，通知默认收起配置；首页汇总需关注的计划，不重复整套风险卡。清仓/重开隔离，页面关闭/重启后恢复；用临时真实API、合成通知渠道验证多轮检查与去重，生产长期噪音仍待运行观察。

本单元本地验收：6079 passed、4 skipped、16 deselected；135项定向回归、887项前端断言、typecheck/build及独立无头真实临时API流程通过。覆盖离页后monitor多轮检查、notify=False不消费配额、反复穿越仅一次合成通知、源失效、首页同仓合并/旧记录标识、清仓停止，以及人工计划/规则并发变更、坏快照与投递不确定窗口。后台详情默认收起，避免与当前方案重复展开。请求/显示新鲜度预算900秒是I/O门禁，不是策略阈值；慢请求发送前还需核对行情源时效。三维未核清暂停独立保留，不被临时个人暂停或源失败覆盖。2026-10-27 15:56已获授权并上线#467（7ef9b559）；公网版本/制品哈希、health、四主路由及桌面/手机页面通过；服务端只读核验1只真实已有持仓的工作区与三路/通知契约，不改持仓或根计划。公网隔离浏览器未使用仓主浏览器身份，只证明公开入口与空租户界面；仓主本人持仓的公网交互、至少两仓及长期提醒噪音仍未验收，不宣称G3整体完成。


#### G4：实际成交与做 T 两腿核对

沿用全局“我操作了”和同仓工作区。先记录已成交事实，再由用户明确关联卖出与买回；普通加仓、方案选择、到价或试算都不能自动成为做T凭据。录入与关联不接交易接口、不覆盖确定性风控。

- 新成交凭据保留不可复用编号、租户、position_id、代码、方向、实际日期、价格、股数、实际总费用（未知为null，核实零费用才为0）及回录时点。沿用portfolio原子保存；旧持仓/closed/operations兼容投影，不从加权成本倒推历史买入，不把方案费率当实际费用。
- 录入入口携带当前仓位身份/版本；锁内校验，原样幂等重试先于CAS，同编号异内容拒绝。旧工作区不能写入同代码重开的新仓。没有明确身份的旧记录不自动归属；补充已有成交证据必须与新增持仓录入区分，避免重复增减股份。
- 人工按成交编号和数量分配关联，支持多笔卖出、部分买回和分批完成；所有有效分配对任一成交的总使用量不得超过该笔股数。同日只有日期不足以证明先卖后买，需用户明确核对顺序。跨清仓重开若关联，必须显式指向两端凭据与不同仓位身份，不能复活旧计划或自动继承个人条件。
- 用Decimal按已匹配股数计算实际毛差价、按成交总股数比例分摊费用；任一实际费用未知则净差价保留未知。未买回数量与卖出现金单独显示，不作为已完成价差或解套收益；显示实际差价不等于整个持仓盈利。关联/费用修改有独立CAS与审计，不物理删除关联依据或幂等凭据。
- 界面先回答“已卖多少、买回多少、还差多少、实际多留下或少了多少现金”，再按需展开成交与分摊。部分完成仍要核对余仓风险；买入后T+1及新成本/份额使旧方案重核。成交、费用和关联变化纳入相关版本，防旧草稿或模型覆盖。

本单元已于2026-10-27北京时间17:02:58随#469自动发布（446ee2e2）。6155项后端测试、908项前端断言、类型/构建及独立临时真实API桌面/320/390px流程通过，最终8项CI全绿后squash。公网版本、制品哈希、health、四主路由、成交弹窗和journal读契约通过；独立浏览器阻断全部写请求，不复用仓主身份。服务器只读核验1个已有租户、1只真实持仓，份额/成本/根计划未变；API/preview active，可用内存802MiB、swap使用0。ECS未构建或跑重型测试，未改模型/env/cron/ACL或重启主机。实际策略效果、仓主真实持仓公网交互及生产两仓仍待验。

旧成交补证列为辅助积压项，不再作为主线交付阻塞；不得重复录入已有数量。仓主2026-10-27纠正优先级：大方向、操作策略、监控、买卖条件、选股和AI讨论是重点，实际成交记账退居辅助。

#### G5 当前主线：从市场方向、选股与 AI 分析到可持续跟踪的策略

产品主线不是持仓表单，而是帮助用户判断市场与板块方向、筛选值得研究的股票、听懂 AI 的证据讨论与分歧，并知道买卖条件及何时复核。目标流程：今日大盘/板块方向 → 候选池与选股理由 → 个股/持仓证据 → AI比较路线、条件与失效原因 → 用户确认观察方案 → 离页监控 → 变化后解释与重核。Issue #484 是保护研究覆盖语义的基础修复，不能替代这条主线，也不单独算产品能力完成。

**后续实现优先级**：

1. **市场与板块**：补齐同一时点、可追溯的大盘宽度、指数/行业与个股归属。分别呈现趋势、广度、板块强弱、来源/时效和未知原因；单个指数有报价不能冒充市场宽度或板块方向。
2. **当日选股与验证**：确认全池扫描、正式名单、理由、AI研究、变体验证确属同一交易日与参数身份。候选解释回答“为何入选、哪些证据支持、还有什么风险”，验证滞后或来源缺失时明确阻止升级，不为页面可见而放宽门禁。
3. **AI讨论**：基于同一批可引用证据，让角色独立看市场、行业、趋势和风险，再相互核对；展示路线取舍、分歧、缺证与结论状态。模型可迭代，模型输出不覆盖评分或硬风险；不以强制思考模板制造确定性。
4. **买卖与方案**：持仓回答继续观察、减仓/退出或做 T 的适用理由、成本与失效条件；未持仓候选回答等待回落、等待确认或暂缓。每个价位必须有当前来源或明确留空，用户确认才成为观察条件。
5. **持续监控**：离页后按既有频率跟踪已采用条件、证据时效与实质变化，去重后说明“发生了什么、为什么需要复核、下一步等什么”；不把触价当作信号或成交。

成交回录、费用和两腿核算只用于个人事实与结果校准，不作为方向判断、AI策略分析或该主线验收的前置条件。

**2026-10-28 当前交付单元：把首页恢复成新手可走通的研究路径。** 入口顺序固定为“大盘/行业 → 当日选股理由 → AI路线与分歧 → 已采用方案持续跟进”；成交回录仍是辅助入口。市场与候选研究按各自真实时点显示，不把当前报价绑定到用户选择的历史研究日。首页只能把供应商给出的 `f124` 转换为行业榜来源时点，不能以接口取数时间冒充。缺少市场宽度时，指数涨跌只描述指数本身，不再据此给出市场偏强/偏弱结论。选股卡直接带用户进入个股选股理由、证据和AI参与路线；已采用策略跟进移到该研究过程之后。验收覆盖指数单源的中性未知、行业榜带源时点/无时点、行业/大盘缺数据、四步锚点导航、桌面与手机页面阅读顺序，并确保候选/AI/跟进既有数据和接口语义不变。

- 当前依据：普通持仓读取使用共享当前市场、板块及可用已完成日证据，不依赖过去是否触发提醒；缓存读取不拉长每分钟刷新，显式刷新有总预算和去重，缺源保留未知，不伪造行业/时点。
- AI：分析独立于费用试算；用同一证据给路线取舍、下一步条件及失效条件，保留角色质疑和分歧。选择前的结构化动作也展示，已选路线不因未填费用而不能分析，暂停时允许解释为何不宜。模型不覆盖评分或风险阈值。
- 人工采用：有来源的AI观察价经用户确认路线与报价比较方向后保存为观察方案，不自动成为买卖信号。绑定模型生成时点、引用、条件、失效条件、仓位和版本；过期/跨仓/并发变化拒绝。观察方案与股数/费用试算分开，净收益未知不妨碍跟踪。
- 监控：沿用现有monitor，在同一持仓根计划读取已采用条件；到价只提示核验，文字中的止跌/转弱不通过报价自行认定。来源失效、个人约束、三维弱势、变仓或原依据变化要说明原因并重核，去重沿用既有通知语义，不新增cron或高频模型循环。
- 新手界面：首屏回答当前处境、为什么、接下来等什么、什么变化要处理；AI结论与观察条件是主动作，过程/费用/成交展开查看。候选研究与持仓策略保留清晰连贯入口，不能以代码评分或免责声明代替操作策略。

本单元实现已通过全仓6198项后端测试及独立临时真实API桌面/320/390px流程；合成证据下真实agnes-3.0-flash六角色及汇总完整通过，79.73秒、无重试，结果仅内存保存。持仓已能不依赖成交/费用分析并采用监控条件，末轮45项守卫回归、前端959断言/构建完成，#471已于17:50:32自动发布25badd83；真实1仓只读核验，源partial（有源指数背景、板块未知），不能声称源全面恢复。未持仓候选尚未贯通，不能以此单元替代完整Goal；至少两仓和长期提醒质量仍需真实数据观察。

当前完整单元：未持仓候选的AI入场条件与持续观察。候选研究页比较等待回落、等待确认、暂缓关注，用户采用后写独立候选观察资源（租户、代码、研究日期、证据版本和模型结果），不得伪造持仓。沿用模型传输/目录但独立定义候选契约；现有monitor同轮发现零持仓且有候选观察的租户，检查到价/依据变化与去重，支持停止，不新增cron。验收覆盖零持仓候选入口、真实模型、采用、离页检查、到价、重复去重、过期拒绝、跨租户与失败恢复，首页展示当前等待或变化。

候选运行依赖修复#472：当前AI讨论角色字段在快照writer/reader间契约不一致，导致2026-10-27首页候选讨论更新失败。独立修复仅接受已有角色模型的四个可选证据字段，保持未知字段/坏类型拒绝与评分/风险门槛；501项定向测试、8项CI全绿后，#473已于18:07:54自动发布5c046195；线上真实364465字节快照、4项讨论可回读，公网版本/哈希/桌面与320/390px通过。不重跑选股或替换生产研究内容，当前API日期索引仍需常规writer刷新，不能称研究运行链已恢复。

候选单元已实现独立资源、三角色讨论/精确引用采用和零持仓后台发现；全仓末轮6247项通过，模型兼容修复104项定向、前端1009断言/构建、独立临时API桌面/320/390px通过。真实agnes-3.0-flash六角色及汇总完整通过52.12秒，无生产写入。采用后以当前条件为主，来源证据与原讨论折叠；部分汇总、not_now或insufficient路线不能采用入场价位。最终单位校验增补后的实连为partial（一个角色引用不存在证据、汇总有效），按既有规则禁止采用，模型稳定性仍需观察；最终CI/发布以回执为准。

候选持续观察稳定性：研究日期、真实参数指纹、评分/策略/技术价位/新闻、讨论结论、风险门禁、来源身份与有效性、三维方向构成实质依据；快照generated_at/stale_after与候选data_fetched_at仅为每次独立核验的时效元数据，不因正常重发要求重跑AI。采用仍精确绑定discussion id/generated_at、资源CAS、原行情时效和原price_ref，不随新报价漂移；实质变化暂停并列明变化类别。旧版依据标识不静默迁移，首次明确要求重核。验收覆盖连续两轮writer刷新和monitor到价去重、实际依据变化、源过期/未来时点、旧记录及模型运行中刷新。

候选后续运行单元：拆开盘中全池扫描进度与日内稳定候选的冻结身份。已定位复用候选分支把256只扫描symbols缩成7只候选，resolved覆盖不足导致cursor不提交；批次symbol指纹又与日内候选冻结身份耦合，不能靠调低覆盖门槛绕过。先写针对性回归，再恢复正常扫描和研究产物刷新。

候选依据消费者与状态解释：候选与持仓显式刷新共享按标的公共板块事实，保留原行业归属/报价时点、TTL与失败冷却；普通GET/monitor只读缓存，零持仓也有有界主动更新入口，不创建观察或持仓。个股研究页分别解释历史策略结果截至日、当日讨论/实验覆盖和原正式推荐资格，历史变体缺参数身份如实未知；不改writer日期和推荐硬门禁。验收包含多仓批缓存供单候选复用、零持仓主动恢复、源失败/未来/过期、原计划不被刷新改写，以及盘中昨日历史结果与今日待验证的桌面/手机流程。


当前完整单元：今日策略会诊与变化复核。首页先给当前市场背景，合并最多三项需关注的持仓/候选，每项股票名、原因、下一条件与主入口；沿用既有风险和观察状态，不以LLM重排评分。长风险卡和原始观察记录默认收起，按需展开查看，零持仓不要求成交记账。来源或读取未知保留缺口，历史研究与当前个人跟进明确区分。

变化复核给AI同仓/同候选的“采用时 vs 现在”可信依据、原路线/条件/失效条件与实质变化。角色可自由比较继续等待、换路线或暂缓的理由，不规定强制思考步骤，不自动改人工计划。旧记录若缺原证据明确缺失，不从当前值倒推。原依据价格仅作对比，新增操作条件只能引用当前有效price_refs；确认后复用原CAS/租户/时效采用与monitor，不要求成交、费用或数量。验收覆盖首页优先关注、采用后变化的针对性复核、再确认后离页持续跟进，正常刷新不重复要求选择；合成流程与生产实际覆盖分别记录。

今日策略会诊/变化复核已随#479/#480于22:55:12发布404556b4；公网桌面/320/390px及真实1仓只读核验通过。市场/板块仍unknown、模型本轮partial有汇总，未称策略质量或Goal完成；下个自然扫描/验证、两真实仓及长期提醒噪音仍待验。下一主线补可信大盘/板块依据与AI讨论稳定性及策略有效性，成交回录继续辅助。

当前依据恢复单元：显式刷新在原8秒总预算内补全A股逐只涨跌宽度，独立源时点来自同批f124，拒绝分页不全/重复/缺源时点，不借情绪池或取数时间。复用既有宽度解释档位，不改评分、策略阈值或风控门禁；新公共缓存供候选/持仓普通读取复用，不在GET新增网络。行业按同源归属有界分页，避免固定前100行业漏匹配；超过预算明确缺失。刷新回执按读侧同一时效要求判断，不把旧/未来源显示成恢复。验收涵盖空缓存显式恢复、坏页/总量漂移/停牌/源过期、部分成功、去重冷却、预算取消及原计划不变。生产源拒绝连接时如实失败，不通过代理或弱化时效掩盖。

当前修复单元：真实讨论的来源时间兼容。此前真实输出使用同一来源时间的ISO分钟格式（例如2026-10-27T10:00）被按无源数字拒绝；候选与持仓共用纯函数时间核验，按可信typed来源接受等价精度呈现，未知/改日/改时/带交易单位仍拒绝。当前动作仅当前来源，历史来源仅历史解释；不靠删数字、放宽事实白名单或反复请求模型换取complete。捕获真实输出离线回放与一轮实连分开验证。大盘/板块另按源探测区分失败与未刷新，不把指数有值解释为市场宽度已齐。

来源修复审查补充：刷新最终回执在网络完成时用上海当前时间重读缓存，避免请求开始时点早于新缓存写入而把成功误报partial；源时点保持原值，过期/未来源仍拒绝。模型只补入可信类型化stock和各index的独立源时点，不从描述文本提取时间授权。549项定向测试已通过，包含空缓存恢复、请求期间时钟前进、行业后续页、部分失败与取消，以及捕获模型输出回放；全仓6531项通过、9 skipped、16 deselected；CI与线上回执另记，不以合成恢复冒充真实来源已恢复。

本轮真实agnes-3.0-flash在合成市场/板块/报价和本地临时资源上完成两轮讨论与有效汇总，用时41.66秒、无重试。一个角色引用了未提供给它的同行身份，被正确拒绝，整体partial；原人工采用条件保持不变。该结果验证真实接口与降级，并不证明讨论全面稳定；生产东财接口探测仍断连，腾讯指数可取，源恢复仍需真实回执。

本单元已随#482于2026-10-27北京时间23:27:35自动发布6e59c59e（8项CI全绿、CLEAN、无未解决审查后squash）。公网版本/前端制品哈希/health/四主路由、独立桌面与320/390px及旧alerts入口通过，无浏览器错误；已有1租户1持仓的身份、份额、成本、方案均未改。真实显式刷新0.25秒返回partial：腾讯指数有源可读、行业0项、宽度未得，个股方向有源；不将指数行情冒充完整市场宽度。API/preview active，可用内存768MiB、swap使用0；ECS未构建、跑重型测试、改模型/env/cron/ACL或重启主机。前端与上一线上树一致，复用已核验本地制品，本次不宣称视觉重设计。

公网业务核验还显示：当日候选5只、研究产物更新14:51，但正式推荐门禁因变体结果仅截至10月26日而未放行。下一主线继续处理可信大盘/板块依据、AI讨论稳定性与当日选股研究/验证的真实运行结果，再以买卖条件、持续监控和变化解释贯通验收；不能把修复接口、显示评分或成交表单作为完成。仍不改门禁来制造推荐，不以合成例子代替策略效果，Goal保持未完成。

当前 Goal 产品重心（2026-10-28 仓主再次纠偏）：核心是大方向判断、选股、AI讨论、买卖条件及持续监控，实际成交回录只辅助状态校准。不能以表单完整、费用计算、提醒数量或测试通过代替决策能力交付。

- 大方向：当前大盘与行业支持参与、谨慎还是等待，给出来源、时效、冲突与未知；指数不能冒充完整市场，缺据不能伪造结论。
- 选股：每只候选解释为什么入选、为什么现在关注、正式资格与研究缺口，区分历史验证和当前机会；未持仓也能研究与观察。
- AI讨论：围绕同份当前证据给出独立观点、相互质疑、关键分歧和汇总结论，回答继续等待、参与或暂缓各自的条件；不强制模型套固定思考步骤，不覆盖确定性评分和硬风控。
- 买卖策略：面向未买与已持仓分别解释何时考虑参与、持有、减仓退出或做T，给可信价位/区间、需核验的前提及失效条件；深亏不自动触发卖出或加仓做T。不填写股数、费用、成交也能得到策略分析。
- 持续监控：离页后持续核验已采用条件和市场/行业/个股变化，只在有意义变化时提示，说明变了什么、为何重要、下一步复核什么；正常刷新不反复要求选路。

交付按这五项共同验收：正常场景、依据不足、观点冲突、条件失效、恢复后的实际页面均需可用。当前先收口离页方向复核，再推进真实市场来源、当日选股/验证产物与真实模型讨论稳定性；不以继续扩充记账流程替代这些主线。f1c03087 首页里程碑已完成公网版本/制品、四主入口、独立桌面及320/390px验收；仍不能代表上述决策质量已全部达标。

下一完整单元：离页后的市场/行业变化复核。既有 monitor 统一收集真实持仓与活跃候选、跨租户去重，只在总预算与共享租约内刷新一次公共证据，再由原检查器消费缓存；失败保持未知且不阻断硬风险检查。方向身份统一比较状态、来源、行业归属、既有宽度档位和有源指数的涨跌方向，采用、后台检查、AI前后对比与页面保持一致；正常数值/时间刷新不导致反复选路。旧依据缺字段时明确重核，不用当前数据倒填原依据。不新增cron、模型循环或策略阈值，不把触价当成信号。验收覆盖零持仓候选、多租户去重、刷新忙/失败/超限、行业或来源同状态变化、指数翻向、同档位正常刷新与通知去重。

本单元最终本地门禁：6572项后端通过、9 skipped/16 live deselected；1181项前端断言/类型/构建通过，ruff、secret扫描和preflight通过。独立审查关闭手动入口方向字段及旧方向身份缺口两项阻塞；旧依据缺失时页面明确无法确认原方案仍成立。临时真实API/隔离桌面与320/390px验证零持仓比较、采用、正常快照重发不漂移、离页到价、行业变更复核和人工再确认；模型/市场为合成，不能代表真实来源恢复或策略效果，CI及生产回执另记。

离页复核单元#488已于2026-10-28北京时间18:34:10自动发布36f12a58，8项CI全绿、CLEAN、无审查评论后squash。公网版本/制品、四主入口及隔离桌面/320/390px通过，现有1只真实持仓和已保存方案前后身份哈希一致；API/preview active、内存可用699MiB/swap0，无主机重启及系统配置修改。Goal未完成。

当前完整单元：当日研究与验证状态可理解。区分行情证据、研究来源任务与该任务运行事件、独立变体刷新和已发布历史结果；writer/API不得把旧盘中失败覆盖主链或收盘研究。source增加可选任务与运行字段，旧快照保守兼容；明确盘中失败仍阻断入场，不因分离展示放行。变体刷新时点、目标截至日、状态、分段进度独立于已发布结果，旧合法产物仍可看历史，最新失败/等待不能被隐藏，completed不等于同日覆盖或策略有效。前端先解释现在能研究什么、还缺什么、下一步等什么，不承诺未核实的调度时间。不改cron/风控/冻结参数，不在ECS补跑重型验证；真实状态与合成流程分开验收。

核心优先单元：恢复真实行业方向进入策略研究。股票归属源可用而行业涨跌仍请求已失效的clist端点，导致AI比较、买卖条件解释和离页监控长期缺板块依据。复用仓库已采用的东财dataapi行业接口及其百分数单位合同，核对同行业匹配、供应商时点、未来/过期/畸形数据与原有有界资源预算；不以缓存写入时刻冒充行情时间，不把行业家数相加冒充全A宽度。真实行业事实同时服务候选、持仓、模型与持续监控，不改评分、风险阈值或自动选路。市场宽度缺口单列，行业恢复不等于大方向整体或Goal完成。当前状态单元只收尾，后续不得以更多运行文案、成交表单替代核心决策证据。

本单元本地验收：6648 passed、9 skipped/16 live deselected；1242项前端断言、typecheck/build、ruff、密钥扫描及preflight通过。独立审查关闭了跨任务runtime来源混用及未知行情状态误放行两项P2；正常运行时点/措辞刷新不改变候选依据身份，失败变化仍须复核。真实本机适配器取得496行业及600000银行Ⅱ（-0.35%，供应商15:39:30），同源ECS只读探测200；临时真实API与隔离桌面/320/390px完整比较/采用/离页/变化复核通过，模型为合成。CI及上线回执另记；不把这些证据升级为AI技术分析质量、全A宽度或完整Goal完成。

核心行业恢复#489已于2026-10-28北京时间19:26:12自动发布a44ef2e8，8项CI全绿、CLEAN、无审查评论后squash。公网版本/前端hash、四主路由、真实行业强弱、隔离桌面/320/390px通过并实际检查截图；正常只读产物writer在复制品生成后，核对原hash、同日期与候选顺序，再按原owner/group/mode发布snapshot/index，新任务身份main_chain不再被旧盘中失败覆盖。真实有界来源刷新取得4/4股票的行业归属与原行情时点（3只候选+1只已有持仓），整体仍为partial：其他市场来源未齐，不伪报全面恢复。166租户/1只真实持仓及已存方案前后身份哈希一致；API/preview active、内存可用747MiB/swap0。ECS未跑npm/vite或重型测试，未改env/型号/cron/ACL/系统配置或重启主机。下一核心修复为issue #490：冻结技术指标的原来源/计算时点及模型准确引用校验，然后继续跨候选取舍和逐项条件监控。Goal继续active，不以本次上线结束。

仓主2026-10-27已持续授权本Goal常规应用版本自动发布，不再逐版询问；按AGENTS.md §6.1合并门禁、本地制品、immutable release及失败回滚执行。Goal不因一次PR、一次上线或合成测试结束；待真实数据的验收项保持未完成。

---

### 2026-10-29 信息架构重排补充

持仓与处理计划页面按新手的实际阅读顺序分成三层：先看“现在怎样”（成本、数量、可卖份额和大盘/板块/个股证据），再看“比较处理路线”（持有观察、减仓退出、做 T 的代价、条件和失效），最后看“持续跟进”（只在有意义变化时复核）。持仓列表只承载身份、行情、盈亏、T+1 和主动作；风险规则移到独立模块并默认收起，只有用户明确展开才读取详细表单。自选、持仓、风险规则、历史成交分别有明确分区，避免同一股票行内同时堆叠事实、分析和设置。

同一信息架构迁移到今日、市场、消息和个股处理入口：页面标题先给任务，分区标题说明“为什么在这里”，主动作保持可见，证据、费用、模型讨论和成交回录按需展开。验收要求为：桌面、390px、320px 无横向溢出；首屏能回答现在怎样/下一步做什么；默认状态不展开长表单；风险触发不直接替代卖出分析；路由和原 API 契约保持不变。

本轮继续收口全站模块边界：删除后来将 `.aq-section` 清成透明无边框的全局覆盖，以唯一的实底、边界和内留白承载真实模块。今日按研究日期→市场方向→候选取舍→已有方案跟进组织，消息证据和历史记录集中在辅助区；大盘按整体（指数/宽度）、行业（涨跌/资金流）、补充观察（情绪/成交/外围）分组；消息先新闻事实，再看催化与概念线索；个股区分所选日研究、参与条件、同日消息和实时行情事件，报价标题与内容同区。

持仓概况只保留一组金额、开放风险和T+1计数，隐私/台账说明折叠到底部，避免手机先滚过大量说明才能看到股票。股票身份与金额、风险事实、主动作分行；自选输入放回独立自选模块，旧 `#watchlist` 链接保留。风险总览和列表共用当前仓位开放提醒，已处理/过期/旧仓提醒不误报待复核，读取失败保留未知，不显示绿色；规则触发不再复述“该卖”，持有/减仓/做T仍从处理计划判断。

本轮验收包含1404项前端断言、类型/构建、301项Dashboard与运行红线测试、ruff、密钥扫描和preflight；独立无头1440/390/320px覆盖六类页面（持仓、今日、大盘、消息、个股、处理计划），检查模块实底与边界、唯一页标题、移动金额区域不重叠、首段持仓位置、旧自选hash、按需规则及提醒失败状态。隔离持仓/行情为合成，复用的公开研究快照仅用于排版；这些验证不代表真实模型、策略效果或Goal完成。CI和公网发布回执另记。

2026-10-30 北京时间10:22:48，#509已按持续授权发布139f5fe3；八项CI成功、已合并tree与候选一致。前端源码树未变，复用含#505模块重排的本地制品，index SHA256 fbd49b05ca7340361829ae22864f6e21ae111f87f5f0c71b7ae9b45543308cc9。切换前等待自然盘中任务退出；首轮浏览器检查遇服务切换短暂502，服务稳定后重新完成五主页面×1440/390/320px共15组公网检查，唯一标题/导航、模块边界及无横向溢出通过，浏览器错误0。原188租户的持仓/成交/人工方案与私有一致备份相符，仍仅1只真实持仓，GET新增空访客按实际计数；API/preview active、可用内存849MiB、swap0。未在ECS构建或跑重型测试，未改配置/cron/ACL、未重启主机。本次模型改动没有新的视觉重设计；真实完整讨论、策略有效性、至少两只已有真实持仓与长期提醒效果仍未完成，Goal继续。

### 2026-10-30 收盘证据更新效率单元（issue #510）

真实 daily 将运行事件 `__RUN__` 和无待办的终态记录送入行情规划，严格批次完整性因哨兵缺价触发串行兜底及逐票重抓。离线两真票例子由1次批量调用放大到6次，返回真票帧相同；此证据不代表138秒都由它导致，也未发现普通事件污染胜率。本单元只修 `scripts/maintenance/daily_pipeline.py` 的取数规划：预测保留真实 pending 行及其 benchmark，纸面同步保留尚未同步的 pending/validated 信号及 open/pending_entry 纸面仓；排除运行事件与不再消费价格的终态。零取价仍调用原验证器，保留 watch_only 转换。原台账、评分、冻结参数、T+1、多源完整性和消费者资格不变。验收覆盖混合状态、同票去重、benchmark 归属、零取价转换、未同步 validated 及活跃纸面仓，之后观察自然 daily，不在 ECS 补跑重型任务。最终本地完整回归7174 passed、9 skipped、16 deselected（196.39秒），162项定向及独立12项审查验证通过；ruff、上传预检、密钥扫描和diff检查通过，CI/部署回执另记。

2026-10-30 北京时间10:43:18，取数规划修复#511经八项CI全绿、CLEAN、0未解决审查后自动发布9cd486e9，合并tree与已测候选一致。自然10:40盘中任务10:42:45成功退出后切换，未force或终止任务；公网版本/制品/API合同、五主页面×1440/390/320px共15组检查通过，原190租户持仓/成交/人工方案与本次私有备份一致，仍1只真实持仓。API/preview active，可用内存836MiB、swap0。未在ECS构建、改模型/env/cron/ACL/系统配置或重启主机。自然daily真实效果仍待观察；真实模型本日三角色HTTP429、1.18秒失败，无重试，不据此猜测账户或并发根因。

### 2026-10-30 决策阅读层级收口

当前移动首页虽然已有模块边界，但两只候选比较的支持/反证/板块/AI/下一观察与选中候选的十余技术指标、评分依据、来源长文本同时默认展开，重复信息仍把研究入口和持续跟进推得很远。本单元保持同日比较结论及逐票支持、反证、下一观察可见；板块详细来源和AI分歧按需展开。选中候选先展示理由、缺口、下一观察和研究主动作，原始指标、评分拆解及行情口径归入同一按需证据区，切换候选不沿用上一票的展开状态。保留原数据、风险/门禁、评分和比较选择URL合同，不裁剪或隐藏关键缺证，不新造交易结论。验收涵盖同日/历史、读取失败、零/一/多候选、切票和对比移除恢复、键盘展开、桌面/320/390px实际渲染；真实模型429另做一次极小诊断，不以反复完整请求替代定位。整体Goal继续，不能以阅读层级收口冒充策略或模型恢复。

### 2026-10-30 全页阅读结构交付

仓主再次指出截图中的规则长列、重复导航和全站堆叠。当前主线已将规则移出股票行，截图旧结构不可当成当前复现；本单元在现有结构上继续完成可见层级：六类页面共用内容目录，主阅读区与补充证据有明确标题、边界和留白。持仓保留概况→股票处境与处理入口→自选→规则设置，规则合并为按股票单层懒加载，展开不改变股票卡布局。今日比较的支持/反证/下一观察常显，板块来源/AI分歧及所选股票技术指标归入按需证据；个股同理保持资格、理由、风险、条件在前。市场主区为整体/行业，补充观察可展开但缺据状态常显；消息事实与传导线索分区。既有路由、历史日期、hash、自选、失败重试和键盘展开保留，不改评分/风险/采用合同。桌面和手机真实渲染、目录深链、切票、单层规则读取与展开前后边界作为验收，不以边框存在或空持仓截图替代有数据的流程。

本单元本地验证：7174项后端通过、9 skipped/16 live deselected（217.69秒），1428项前端断言、类型/本地构建、ruff/上传预检/密钥扫描通过。隔离真实API配合两只合成持仓，在1440/390/320px完成六类页面18组渲染，单层规则懒加载与草稿保留、目录打开折叠目标、板块筛选后的固定持仓锚点、切票重置技术证据、失败/旧提醒/空仓及暗色核验通过。修复审查发现的筛选目录失效和实际交互发现的重复discussion锚点；没有生产持仓写入或真实模型调用。CI和公网回执另记，整体Goal继续。

### 2026-10-30 全站层级发布与独立变化复核

全站层级#512已于北京时间11:20:40发布45c76d03，八项CI成功、CLEAN、合并tree与最终候选一致；公网五页面×1440/390/320px共15组渲染、目录保留研究日期和键盘展开证据通过，原191租户持仓台账与一致备份对比未变，仍1只真实持仓。API/preview active、内存可用688MiB、swap0，无ECS构建/重测、模型/env/cron/ACL/系统配置改动或主机重启。整体Goal继续。

下一核心单元：已采用持仓条件的变化直接解释。现有方向变化只有泛化状态，前后对比依赖再次成功取得AI汇总；在worksheet读侧复用同一纯比较合同，独立返回采用时与当前的大盘/板块/个股、来源、持仓、个人条件和风险差异。不新增GET网络/模型调用，不修改原采用计划或自动选路；普通报价/时点刷新不能误标实质变化，旧依据缺失保持未知，停跟踪/清仓不生成当前策略建议。前端在已采用条件前常显简短变化与要复核什么，详细对比按需展开，模型失败仍可读，避免重复展示。验收覆盖行业走弱、来源失效、普通tick、旧记录缺据、身份隔离、停止/清仓、模型失败及桌面/手机真实交互；不把到价视为成交或解除硬风险。后续继续补纯方向等待条件和真实模型稳定性，不以单项解释收口结束Goal。

本单元本地验证：7185项后端通过、9 skipped/16 live deselected（210.32秒）；123项持仓变化/采用/模型定向回归通过。1447项前端断言、类型与本地构建、ruff/上传预检/密钥扫描通过；独立临时profile、随机端口和AQSP锁下，真实临时API、两只合成持仓覆盖1440/390/320px的8种状态共24组：普通报价、板块转弱、来源过期、旧缺据、身份不符、停止、清仓及模型失败后独立对比。历史默认折叠、键盘展开、摘要先于原条件，无横向溢出或脚本错误，无真实模型请求/生产写入。复审将无变化文案改为中性事实，明确不代表风险解除，并增加持续L1风险组合断言。CI与公网发布回执另记，Goal继续。

### 2026-10-30 独立复核发布与定性等待贯通

#513已于北京时间11:46:22发布501f2a8d，8项CI成功、CLEAN、合并tree与已测候选一致；公网五页面×三尺寸15组和日期/键盘交互通过。194份原台账意图一致，仍1只真实持仓，API/preview active、可用内存827MiB/swap0。未改系统配置或重启主机。

下一单元贯通模型已支持的observe/null价位方向等待：仅允许模型有效汇总中明确登记了目录方向前提或失效条件的纯观察，经用户采用后冻结为持仓v3合同，不捏造价位、数量或成交。卖出/回补仍要求价格与原硬守卫；旧价格合同v2与候选v1保持兼容。后台逐项核验已采用的方向条件，正常条件进展应报告等待/待人工核验，不被原方向快照变化一律挡成“必须重选”；未登记方向、来源/行业身份变化、未知来源、持仓或个人条件变化、风险及做T暂停仍优先复核。定性等待不自动生成买卖任务，满足只提示人工复核、未知不作改善，通知沿用语义去重。验收含有效采用、伪造/空条件/越权拒绝、历史合同兼容、方向进展/失效/缺据/恢复、风险/T+1/成交、桌面手机与公网。Goal继续。

本单元本地验证：7202项后端通过、9 skipped/16 live deselected（212.55秒）；另补市场宽度分类进展/指数身份变化和不可盯盘采用拒绝的定向回归。1482项前端断言、类型/本地构建及ruff通过。两只合成持仓、真实临时API配合独立无头1440/390/320px共18组：每尺寸真实点击采用一次，确认无价格方向下拉、POST comparator严格null、等待/改善/过期/换源/停止/清仓均按合同展示，无横向溢出或脚本错误，无真实模型请求或生产写入。原方向前后对比对已登记进展改为中性说明，不反复要求重选；历史不设价位明确标注，不误报为价位缺失。独立前后端审查无阻塞，标题明确方向变化不等同改善；CI与公网回执另记，Goal继续。

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
