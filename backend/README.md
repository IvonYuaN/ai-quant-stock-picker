# Vibe-Research Backend

A股数据层 + 可插拔 AI 层。行情研究接口只返回客观数据；持仓和研报是本地自托管数据，不上传、不下单、不推荐、不预测。

## 安装

```bash
# 在仓库根目录执行
python3 -m venv .venv
.venv/bin/pip install -e ".[api,dev]"
```

> 行情 + 研报只需 `fastapi / uvicorn / requests`（秒装、必可用）。
> 一致预期 / 新闻 / 公告需 `akshare`，K线 / 财务需 `mootdx`；未装时对应端点返回 501 + 安装提示，不影响其余功能。

## 1. HTTP API（给网页前端 + 系统 AI）

```bash
.venv/bin/python -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8900
```

| 端点 | 说明 | 依赖 |
|---|---|---|
| `GET /api/health` | 健康检查 | — |
| `GET /api/indices` | 大盘指数实时行情 | stdlib |
| `GET /api/quote?codes=600519,000858` | 实时行情（PE/PB/市值/涨跌停…） | stdlib |
| `GET /api/valuation?code=600519` | 完整估值（前向PE/PEG/消化年数） | requests+akshare |
| `GET /api/valuation/percentile?code=600519` | 估值历史分位（近5年·百度股市通） | akshare |
| `GET /api/financials?code=600519` | 财务关键指标（同花顺摘要，最新报告期，前端个股页用） | akshare |
| `GET /api/reports?code=600519` | 个股研报列表（含 PDF 链接） | requests |
| `GET /api/announcements?code=600519` | 近期公告（东财） | requests |
| `GET /api/news?code=600519` | 个股新闻 | akshare |
| `GET /api/kline?code=600519` | K线（`data` 保持 bars 数组；`meta` 标记来源、价格展示口径、as-of 与取数时点） | mootdx，日/周/月无数据时使用腾讯前复权展示备源；不用于回测/评分 |
| `GET /api/finance?code=600519` | 季报财务快照（mootdx，前端未用 / 备用） | mootdx |
| **资金面·筹码·信号（v3.3）** | `/api/margin` · `/block-trade` · `/holders` · `/dividend` · `/fund-flow` · `/dragon-tiger` · `/lockup` · `/blocks` · `/hot-concepts` · `/investor-qa` · `/industry` | requests |
| `GET /api/market/overview` · `/api/radar` | 市场情绪+板块资金 · 资讯雷达 | akshare / stdlib |
| `POST /api/chat` | 系统 AI 对话（function calling，AI 自己调数据工具） | requests |
| `GET /api/portfolio` | 读取本地持仓与浮动盈亏 | stdlib |
| `POST/DELETE /api/portfolio/*` | 修改/删除本地持仓、清仓记录；`POST /refresh` 强制刷新行情 | stdlib |
| `GET/POST /api/aqsp/factor-definitions` | 读取或保存当前租户的因子草稿与不可变公式版本 | 本地租户存储 |
| `GET/POST/DELETE /api/aqsp/signal-definitions*` | 管理当前租户的日线条件信号；监控已可固定并评估所选修订，策略/回测尚未连接消费者 | 本地租户存储 |
| `POST /api/price-observation-rules` (`rule_type=signal`) | 把启用的条件信号修订固定到租户观察规则；后台以 raw 日线 + PIT 因子检查并写现有观察提醒账本 | `astocks_raw.db` + PIT factor cache |
| `POST /api/aqsp/factor-definitions/{id}/versions/{n}/trial` | 本机 raw SQLite OHLCV 的有限分层收益与逐日 IC 诊断试算；结果不晋级 | `astocks_raw.db` |
| `GET /api/aqsp/auction-benchmark` | 读取 Fuyao/同花顺竞价风向标名单；未配置时显式返回 `source_unavailable` | `FUYAO_API_KEY` + requests |
| `GET /api/aqsp/abnormal-deviation` | 读取 3/10/30 日交易所偏移观察；依赖 raw SQLite 日线、指数与完整 PIT 因子 | 本地只读数据 |
| `GET/POST/DELETE /api/myreports*` | 读取、上传、删除本地研报文件 | stdlib |
| `POST /api/radar/refresh` | 强制刷新 RSS 缓存，会访问外部 RSS 源 | stdlib |

> 上表为主要端点；完整路由清单见 `app.py`。要更全量的 A 股数据（打板 / ETF期权 / 全市场行业排名等），用根目录 [`a-stock-data/`](../a-stock-data/SKILL.md) 工具箱。

### 持仓开仓日期与 T+1 提醒

- `POST /api/portfolio/holding` 的 `opened_at` 缺省或为 `null` 时采用上海当日；
  显式日期必须是有效的 `YYYY-MM-DD` 且不晚于上海当日，否则返回 400，持仓文件不变。
- `POST /api/live-risk/check` 先逐票根据开仓日期生成 T+1 标记，再应用 `holding_days`
  手动覆盖。空映射或仅覆盖部分代码，不会解除其余当日新仓的 T+1 限制。
  覆盖值必须为非负整数，字符串、小数、布尔值等返回 422。
- 无开仓日期的旧持仓继续按老仓兼容；已存但格式错误或晚于上海当日的日期返回 409，
  指明需修正的股票代码，并在写入提醒、外发通知之前停止。
- 此标记只区分当日新仓与老仓，不是实际持仓交易日数，也不计算加仓后的分批可卖数量。
- 持仓页同步展示今日新仓、往日开仓、日期待核实状态；日期选择限制到北京时间当日。
  录入或清仓提交失败时保留表单，成功后再清空，避免服务端拒绝日期后丢失输入。

### 未持仓候选观察

- `POST /api/candidate-observations/analyze` 要求本人填写问题和观察期限，并可补本人假设与反方理由；AI 只把它们作为个人上下文，不能当市场证据或改确定性评分。
- `GET /api/candidate-observations` 读取当前租户的观察与暂缓/不考虑决定；`POST` 采用明确的价格条件、失效条件与来源标识；`POST /decision` 以当前候选快照和 revision 保存人工决定；`POST /{id}/stop` 以版本号停止观察；`PUT /{id}/review` 记录人工结果、过程评价、经验和可选成交凭据编号。
- 记录保存在独立的 `candidate_observations.json`，与持仓、评分和成交台账分离。采用时固化 AI 分析/证据快照；实际成交只引用现有 `trade_journal` 凭据编号，不复制成交事实。
- 现有 monitor 轮次读取活跃观察并使用带来源时点的行情检查；股票转为真实持仓时，候选观察收束并记录 `position_id`。不要求页面保持打开，不自动下单。
- 采用时服务端核验租户内完整 AI 分析、候选快照身份、条件内容、分析生成时点和实时行情时点；分析与行情过期或候选快照变化时拒绝。AI 输出仍是待人工判断的材料，不改分数、风控或持仓。
- 到价通过现有通知运行时按观察 ID 与采用版本去重。监控轮次返回行情缺失/过期原因；到价只提醒人工复核，不自动交易。
- 复盘写入使用观察 revision 做 CAS；成交凭据必须存在于当前租户、当前股票的真实成交账本。研究快照的 100 条滚动分析保留不影响已采用记录的复盘快照。
- 暂缓/不考虑决定同样保存候选快照、个人问题/期限/假设/反方和审计历史；只有当前快照可写，已有活跃观察或真实持仓时拒绝写入冲突决定。采用同一快照的观察条件后，原决定转为 `adopted`，不删除历史。

### 通用单标的价格观察

- `GET/POST /api/price-observation-rules` 读取或创建当前租户的代码、1–5 条价格条件、AND/OR 关系、提示级别、再次提醒冷却和本人备注；旧单条件请求继续兼容。`PATCH /api/price-observation-rules/{id}` 以 `expected_revision` 暂停或恢复。每租户最多启用 50 条，重复活动规则与当前持仓标的会被拒绝。
- 规则独立存于 `price_observation_rules.json`，不混入候选或持仓对象；规则变更使用租户隔离存储、文件锁、原子替换和 revision CAS。人工检查只接受有来源且通过 `position_quote_issue` 新鲜度门禁的行情。标的后来成为持仓时，活动规则在该轮写入 `transferred` 与 `position_id` 审计并结束候选观察，不能恢复；后续由持仓工作区管理。
- 评估复用现有 LIVE monitor 轮次和同批报价，命中写入既有 `alert_events.json`，保留规则身份/版本、报价来源/时点，并支持原收件箱处置；暂停规则会同步收束其 OPEN 到价事件，重复暂停可修复事件同步中断。外发通知不由这类观察规则触发；到价只表示价格条件成立，不代表买卖信号或成交。
- 全市场涨停/炸板/跌停观察规则单独存于租户 `market_event_rules.json`；复用东财异常池文件缓存，并要求缓存不超过 5 分钟、实际交易日为今天。陈旧/缺数只记录跳过，不解除 OPEN 事件；有效触发写入同一 `alert_events.json`，但不进入持仓分组或外部通知队列。
- 行情过期、来源缺失或无效时记录跳过原因，不生成命中，也不关闭此前提醒。冷却只抑制同一规则重新越线后的站内重复提醒；策略规则、个股指标信号、全市场扫描和规则级 Webhook 尚未实现。

### 个人决策索引

- `GET /api/aqsp/decision-history` 是只读投影，可按 `symbol`、`kind`、`status`、`since` 和 `limit` 筛选候选决定/观察、持仓根计划和正式信号复盘。
- 返回的每类记录保留原领域 ID、来源快照身份和原页面链接；此接口不写入、不合并持仓/候选/正式复盘模型，也不复制成交事实。
- 正式信号复盘仍使用历史 `reviews.jsonl` 单用户存储；当当前租户不是 `local` 时，该来源返回 `available=false` 和原因，避免跨租户泄漏。
- 一个来源损坏时保留其他可读取来源，并单独标出来源错误。

### 因子公式研究边界

- 因子定义保存在当前租户的 `factor_definitions.json`；编辑创建不可变版本，revision 使用 CAS，当前只有 `draft` 状态可写。
- 诊断试算只读取本机 `sqlite_db` 且 `price_mode=raw` 的 OHLCV，需显式给 20–200 个代码、最多 120 个自然日窗口、1–20 个持有交易日和 2–10 层分位。未来收益只作为验证标签回映到过去的信号日期，不进入表达式输入。
- 当前字段范围不含财务 PIT enrichment；样本不完整、来源/价基不合规或窗口不足会返回阻断状态。试算记录标为 `preview_only`/`blocked`，不改候选分数、不产生正式 OOS gate，也不能晋级或运行策略。
- 新版 `rank()` 是同交易日的截面排序；`ts_rank()` 与 `ts_*` 窗口按单只股票的历史交易日滚动。前端只允许白名单表达式，不执行 Python 属性或任意函数。

`/api/chat` 请求体：
```json
{
  "messages": [{"role": "user", "content": "茅台估值贵不贵？"}],
  "context": "本页上下文（可空）",
  "llm": {"baseURL": "https://api.deepseek.com", "apiKey": "sk-…", "model": "deepseek-chat"}
}
```
`llm` 由前端从本地配置随请求带上，后端不持久化 key。

## 访问边界

本地自托管时不设置任何安全变量即可使用，保留本机 Ollama/CLI 等能力。公网模式建议同时设置：

```bash
export VR_PUBLIC_MODE=1
export VR_API_KEY="$(openssl rand -hex 32)"
export VR_ALLOW_ORIGINS="https://your-frontend-host"
```

设置 `VR_API_KEY` 也会自动进入公网模式。公网模式下除 `/api/health` 外所有 `/api/*` 都要求 `Authorization: Bearer <VR_API_KEY>`，包括 portfolio、chat、研报上传/删除和 RSS 强制刷新；未配置 key 时接口失败关闭。`VR_ALLOW_ORIGINS` 只接受明确的源，公网模式会忽略 `*`；前后端同源部署可不配置它。公网仍应通过 HTTPS 反向代理，并只暴露代理入口。

`POST /api/chat` 会向请求提供的 OpenAI 兼容 `baseURL` 发起请求。公网模式会阻止云元数据、内网、本机地址及其 IPv4-mapped IPv6 写法（例如 `::ffff:127.0.0.1`），本地模式仍允许本机模型地址。

竞价风向标只在服务端配置 `FUYAO_API_KEY` 后读取 Fuyao/同花顺名单。未配置或来源不可用会返回明确状态，不会以其他榜单补位。名单会尝试关联本地 SQLite raw 日线；当日开盘至收盘只比较同日 raw 价格，跨日结果要求 PIT 因子齐全。全市场 9:25 竞价扫描和追高风险统计仍未接入。

多日偏移只读取已完成的本机 `sqlite_db` raw 日线，按个股代码选择优先指数并显式标记回退；股票跨日收益使用端点日期 PIT 因子，缺 raw bar、基准日期或因子时不计算该窗口。响应标注数据日/freshness/覆盖数；滞后数据只作历史观察，阻断状态不等于“无异动”。当前不外推盘中实时偏移。

## 2. MCP Server（给 Claude Code / 高手 agent）

零第三方依赖，复用同一套数据工具。挂进 Claude Code：

```bash
cd backend
claude mcp add vibe-research -- \
  "$(pwd)/../.venv/bin/python" "$(pwd)/mcp_server.py"
```

挂上后，你的 agent 直接拥有 `query_quote / query_valuation / query_reports / query_news` 四个工具，
用你自己的订阅额度调数据、多步分析——无需 API key、不占本产品成本。

### 完整 A 股数据工具箱（随仓库自带）

MCP 的 4 个工具是「零配置、开箱即用」的常用项。若 agent 需要更全的 A 股数据（龙虎榜 / 融资融券 / 大宗交易 / 股东户数 / 分红 / 资金流 / 解禁 / 概念板块 / 打板情绪 / ETF 期权 / 互动易 / 全市场行业排名 …共 **40 个端点**），本仓库根目录**自带完整数据源** [`a-stock-data/`](../a-stock-data/SKILL.md)（a-stock-data v3.3）：

- 要调哪个接口，直接看 [`a-stock-data/SKILL.md`](../a-stock-data/SKILL.md)——每个端点都有 copy-paste 即用的代码（内嵌全部调用逻辑，零第三方数据封装依赖，东财接口已内置限流防封）。
- 运行依赖：`pip install mootdx requests pandas stockstats`（自包含，v3.0 起已移除 akshare）。
- 上游与更新：[github.com/simonlin1212/a-stock-data](https://github.com/simonlin1212/a-stock-data)（不更新也能一直用，自带的是固定可用快照）。
- 分工：**MCP 4 工具** = 网页 / 轻量常用；**自带数据源 40+ 端点** = agent 深度自助调研的全量工具箱。二者同源，按需取用。

## 合规

- 数据端点只返回客观行情/研报/财报/新闻，不含任何建议、排名、预测。
- `/api/chat` 的 system prompt 内置中立红线：不荐股、不预测涨跌、不给买卖时机、不构成投资建议。
- 分析结论一律由用户配置的模型 / agent 给出，本产品只提供数据与工具。
