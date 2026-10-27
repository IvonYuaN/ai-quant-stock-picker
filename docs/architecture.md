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

### 7.2 最终交付 Goal 与缺口（2026-10-27）

§7.1 的 M0–M3 与 #458–#460 是已交付基础链路，不能等同最终完成。按下表持续开发；状态只随实际证据更新。

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

下一开发单元：为已经计入持仓的旧成交补充逐笔证据，明确与新增成交区分；补证不得再次增减股份或改变平均成本，不能从旧平均成本伪造历史买入。沿用租户、仓位身份、幂等、版本和审计保护，并核对补证对关联额度及现金归因的影响。

仓主2026-10-27已持续授权本Goal常规应用版本自动发布，不再逐版询问；按AGENTS.md §6.1合并门禁、本地制品、immutable release及失败回滚执行。Goal不因一次PR、一次上线或合成测试结束；待真实数据的验收项保持未完成。

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
