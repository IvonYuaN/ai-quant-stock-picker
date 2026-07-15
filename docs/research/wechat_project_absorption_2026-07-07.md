# WeChat Project Absorption Review - 2026-07-07

本记录用于判断三篇微信文章提到的项目/工作流是否适合吸收到 AQSP。判断口径分两层：Horizon 这类信息雷达优先看通知/信息摄入价值；回测优化和因子系统文章优先沉淀为工程经验。

## Sources

- https://mp.weixin.qq.com/s/i2ujNnrR63VFHsVSkf4wXg
- https://mp.weixin.qq.com/s/p92DPgfelUrNT1UJFAlA2w
- https://mp.weixin.qq.com/s/TvQCCqld6jFsloPVAHz27A
- https://github.com/Thysrael/Horizon

## 1. Horizon AI News Radar

文章指向 `Thysrael/Horizon`。截至 2026-07-07，GitHub API 显示该仓库为 MIT、Python、约 7.9k stars。核心链路是多源抓取、去重、LLM 评分过滤、背景补充、结构化简报、Webhook/Email/MCP 输出。

判断：作为 AQSP 的通知与早间信息摄入层非常实用。它不需要替代选股引擎，而是补上“每天先知道哪些宏观、行业、公司消息值得看”的前置雷达。信息会影响选股判断，关键是把这种影响结构化、可追溯、可复核，而不是让一段 LLM 摘要直接覆盖技术/量价分数。

可吸收为通知能力：

- RSS/RSSHub 作为财经新闻补充源，接到 `aqsp.data.news_source.NewsSource`，只进入 `CatalystReport`。
- 去重、评分阈值、来源分组、最大条数等配置形态，可映射到 `NewsCatalystConfig`。
- 背景补充和摘要适合 `briefing` 展示层，但必须保留原文链接、来源和时间戳。
- Webhook/Email 模板思想可参考现有 notifier，不需要引入 Horizon 服务。
- 早间/盘前通知可以增加“今日财经雷达”区块：只列高分消息、关联标的/行业、影响方向、原文链接。
- 对用户自选股、候选池、重点行业做定向 RSS/新闻 watchlist，比全市场新闻泛读更有实际价值。
- 新闻影响应进入候选判断链路：`CatalystEvent -> 候选上下文 -> 风险/催化剂标签 -> portfolio/briefing 优先级`。
- 对强负面信息，可以触发“复核/阻塞/降优先级”状态；对强正面信息，可以触发“加入观察/提高复核优先级”状态。
- 只有可验证、带时间戳、带来源链接、可复现的结构化事件，才允许影响候选排序或风险提示。

不吸收：

- Twitter/Playwright 抓取能力，不符合本项目浏览器调试边界。
- OpenBB 作为运行时依赖；体量大，应保持为可选研究候选。
- MCP 服务形态；当前 AQSP 是单用户研究工作台，不对外提供服务。
- LLM 分数不能裸进入选股核心分；如果要影响候选优先级，必须先落成结构化事件、来源、时间戳、置信度和可解释标签。

建议落点：

- 小 PR：新增 `RssNewsSource`，优先用标准库或轻量解析，不新增大依赖；输出统一新闻 DataFrame schema。
- 小 PR：在 `news/catalysts.py` 增加跨来源 URL/title 去重和 `source_group` 字段。
- 配置层：新增 `config/news_sources.yaml`，记录 RSS 源、类别、启用状态、最大条数。
- 通知层：在 briefing/notifier 中增加“财经雷达”摘要区，明确哪些信息支持、反对或要求复核当前候选。
- 判断层：在 portfolio/briefing 中消费结构化 `CatalystEvent`，形成 `supports / opposes / needs_review` 三类判断，不直接生成交易指令。

## 2. Backtest Optimization Case

文章是性能优化案例，不是可定位的开源仓库。它的价值主要是经验：profiling、缓存不变数据、数组索引替代热点字典拼键、组合未变化时复用估值、每步与原结果对齐。

可吸收：

- 回测/研究脚本先 profiling 再优化，避免凭感觉改热点。
- 宽表和行情索引构建结果可以缓存，但缓存 key 必须包含数据源、日期范围、字段、复权口径和数据版本。
- 对 walk-forward/参数搜索场景，复用不变市场数据；策略纯函数和 look-ahead 检查不能放松。

不吸收：

- “组合未变化就不重算估值”只适合决策不依赖当期估值的场景；AQSP 若用于止损、止盈或风控触发，不能照搬。
- 不为了提速引入全局单例缓存；缓存必须可失效、可测试、可复现。

建议落点：

- 文档层：加入性能优化原则到未来 walk-forward/研究脚本 PR 描述。
- 工具层：如后续参数搜索变慢，先加 benchmark fixture，再做缓存或索引化。

## 3. A-share Factor Backtest System

文章描述一套个人 A 股因子回测系统：Tushare/MongoDB 数据采集、本地存储、WorldQuant 风格表达式 AST、安全算子、横截面多空回测、FastAPI + ECharts 可视化。它的价值主要是经验：数据源会卡脖子、内存/结果存储要提前规划、表达式执行必须做安全边界、可视化应围绕研究闭环而不是炫技。

可吸收：

- 表达式 AST 白名单解析是可复用方向，适合未来研究沙箱，不进入生产评分主链。
- 按表达式字段做按需加载、自动计算 max lookback、限制前端即时回测数据量，适合本项目大样本研究。
- 结果索引只保存摘要，明细按 job_id 延迟加载，适合 walk-forward 和 dashboard 归档。

不吸收：

- Tushare 中转、内部 URL patch、MongoDB 作为默认存储；这些会增加运维和数据授权风险。
- WorldQuant 多空回测默认参数不能直接套 A 股候选池；本项目不是多空交易系统，也不下单。
- FastAPI 服务化界面不是当前方向；现有 Streamlit/dashboard 已够用。

建议落点：

- 研究层：未来如果做 factor sandbox，先定义 `FactorExpression`、白名单算子和合成数据测试。
- 数据层：继续优先 SQLite/Parquet，本地缓存必须有 freshness、schema 和 point-in-time 约束。
- 展示层：结果索引和按 job_id 延迟加载可以进入 dashboard backlog。

## Final Decision

可以吸收，而且 Horizon 作为通知层优先级应该提高：

1. `notification/news radar substrate`: RSS/RSSHub + 去重 + 新闻重要性过滤 + 盘前/日报通知，并结构化影响候选复核优先级。
2. `research performance substrate`: profiling-first、缓存可失效、热点循环去对象创建。
3. `factor sandbox substrate`: AST 白名单表达式、按需字段加载、max lookback、结果索引。

第 1 项可以直接排一个小实现 PR；第 2、3 项先作为工程经验和后续研究沙箱原则保存。禁止吸收为交易执行或自动调参逻辑；允许新闻信息以结构化、可追溯方式影响候选复核、风险标记和组合优先级。

## Implementation Status

- 2026-07-07: 第 1 项已接入 runtime。新增 RSS/RSSHub 配置入口和 `RssNewsSource`，新闻事件进入 `CatalystReport`、`market_context`、portfolio 预排序和 briefing 展示。
- 2026-07-07: 第 2 项保持经验层。后续只在 walk-forward/参数搜索出现真实瓶颈时，以 profiling-first、小步验证、可失效缓存方式实现。
- 2026-07-07: 第 3 项已开始落底。新增安全因子表达式底座 `aqsp.research.factor_expression`，只支持白名单函数、提取字段依赖和 max lookback；当前只用于研究沙箱，不进入生产评分主链。
