# AQSP 项目现状（Current State）

> **单一事实来源。** 最后核对：2026-11-25（基线 `origin/main` @ `f3eb146e` / #677；本日新增数据诚实性 #650–#677）。历史核对：2026-11-24（`d660a31f` / #620）、2026-09-22（`ab6f5fef` / #173）。
> 本项目由**多个 agent 并行开发**，历史上积累了互相矛盾的阶段文档与任务清单。本文用于回答「**现在到底是什么样**」。
> 维护规则：本文只写「当前是什么」；历史/过程材料一律下沉 `docs/archive/`，不在此堆叠。

---

## 1. 定位与硬红线

- **local-first A 股量化「选股研究工作台」**：产出候选股、证据链、风险提示、Portfolio Manager 裁决、复盘与通知，供人决策。**不是交易机器人，不接券商下单。**
- 红线（`docs/CONSTITUTION.md` / `docs/architecture.md` §1.2/§5/§8）：
  - 不下单、不接券商、不引入交易执行；
  - **LLM 只作通知附件，不参与选股打分，不进 `ledger` 权重学习**；
  - 不写未来数据（无 `shift(-N)`、无中心化 rolling、无全期归一化）；回测走不复权 + PIT 复权因子；
  - 策略阈值走 `config/thresholds.yaml`，禁止字面量魔法数。

## 2. 权威边界文档（改代码前必读）

| 文件 | 作用 |
|---|---|
| `docs/CONSTITUTION.md` | 项目宪法（最高准则，16 条不可让步条款） |
| `docs/architecture.md` | 架构、边界、模块契约、PR 顺序 |
| `AGENTS.md` | 编码硬约束（类型/测试/浏览器调试边界等） |
| `docs/agent-operating-boundaries.md` | 本地 / GitHub / 服务器 / 公网四层职责边界 |

## 3. 运行时拓扑（四层）

| 层 | 入口 | 边界 |
|---|---|---|
| 本地 Mac | 开发 / 单测 / ruff | 不存生产密钥；重跑批一律放 runner |
| GitHub | 代码 / 文档 / 测试 / 可复现配置 | 不传 `.env`、token、ledger、私有库、运行报告 |
| 云服务器 `/opt/aqsp` | FastAPI `127.0.0.1:8900` + Vite preview `127.0.0.1:5899` | 宝塔/systemd；不放源码开发 |
| 公网 `lh.ifidy.cn` | Nginx → `5899`(React) / `8900`(API) | 旧 Streamlit `8501` = 历史回滚路径，**非当前入口** |

## 4. 数据面（`src/aqsp/data/`）

- **行情多源**：`source_factory` = mootdx / 腾讯 / 新浪 / akshare / baostock / efinance / sqlite_db / tdx_vipdoc。
- **事件 / 风险面**（已实现取数 + 测试 + `scripts/fetch_*.py`）：
  - `longhubang.py` 龙虎榜 · `lockup.py` 限售解禁 · `cls_news.py` 财联社快讯 · `concept_board.py` 东财 slist 概念板块 · `dividend_plan.py` 分红送转 · `earnings_forecast.py` 业绩预告 · `suspend_resume.py` 停复牌。
  - **消费方**：`aqsp.features.event_calendar.EventCalendar.from_cache()` **只读** `$AQSP_RUNTIME_DATA_ROOT/pit_cache/*.csv`（`lockup.csv` / `longhubang.csv`，与各 Source 的 `_default_cache_path()` 同规则，不联网）；`strategies/event_driven.py` 经它取真实事件（默认 `enabled=False`）。
  - **调度（已接线）**：`scripts/preload_event_data.sh` 逐源预加载（best-effort）→ `bt_task.sh event-data` → 由 `install_server_cron.sh` 登记为**工作日 08:20 盘前**（`AQSP_ENABLE_EVENT_DATA_CRON`，`check_scheduler.py` 已纳入 `SCHEDULED_ACTIONS`）。
- **资讯雷达**：`backend/newsradar.py`（12 赛道 / 108 源 RSS）。
- **PIT / 复权**：`industry_pit.py`、`macro_pit.py`、`pit_financial.py`、`pit_policy.py`、`adjust.py`。
- **个股深度 API**（后端 `backend/app.py` + `astock.py`）：`/api/dragon-tiger`、`/api/lockup`、`/api/margin`、`/api/block-trade`、`/api/holders`、`/api/dividend`、`/api/fund-flow`、`/api/blocks`、`/api/hot-concepts`、`/api/investor-qa`。
  - 其中 `/api/dragon-tiger`、`/api/lockup` 与 `src/aqsp/data/longhubang.py`、`lockup.py` 是**同一东财接口的两套实现**（`RPT_DAILYBILLBOARD_DETAILSNEW` / `RPT_LIFT_STAGE`）；`astock.py` 刻意不与 aqsp 解耦，**东财改列名时两侧需同步**。

## 5. LLM / 研报 / 复盘能力（`src/aqsp/briefing/` + `backend/chat.py`）

| 能力 | 实现 |
|---|---|
| 多 Agent 讨论/辩论 | `agent_roles.py`（**9 个 A 股角色**）+ `debate.py` + `debate_tracker.py` + `conclusion.py` |
| 简报 / 研报生成与渲染 | `generator.py` + `schema.py` + `templates/`（`renderer.py` 仅测试引用，生产未接） |
| 收盘复盘 | `closing_review.py`（`aqsp closing-review`） |
| 研究引擎 / 报告 | `research_engine.py` + `report.py` |
| AI 对话（合规） | `backend/chat.py` `/api/chat`（五维投研框架，system prompt 强制中立：不荐股/不预测/不给时机） |
| 前端呈现 | `components/aqsp/sections/DiscussionSection.tsx`（多 Agent 委员会结论 + 可展开过程）、`CandidateSection`（讨论复核列 / 研究链） |

🔴 以上全部 **advisory-only**，不回流打分/权重。

## 6. 前端 IA（`frontend/src/lib/ia.ts`）

- 主区：**今日研究** / **市场环境** / **资讯雷达** / **策略实验室** / **结论归档** / **选股绩效**
- 我的：**自选** / **持仓** / **笔记** / **研报**

## 7. 当前进行中

- **T3 双窗口门禁验证 —— 已结案（2026-09-24）：判「方案 A 不成立」。**
  3y 与 5y 两臂 8×8 变体全部跑完。**判据 = 同 `horizon` 层内的臂均值对比**（`compare_t3_dual_window.py`，
  2026-09-24 修，见 issue #194）：3y 同层 **持平**（ΔSharpe=+0.039，落在噪声带内）、
  5y **基线反超**（+1.01 vs +0.61）⇒ 方向不一致，不判成立。
  ⚠️ 旧判据「最佳 htf_mr vs 单个基线」**跨了 `h` 轴**（`WF-H07` 是 h=10、基线是 h=3），
  曾据此误判 3y「改善」；该口径在产物里保留但**显式标注为不可单独引用**。
  进展看 `outputs/T3_接力进展_*.md` 与 `outputs/T3_双窗口对比结论_*.md`；运行时权威记忆在 `.workbuddy-ai/memory/MEMORY.md`。

### 7.1 策略启用决策：`event_driven`（事件驱动）— 暂不启用

- **现状**：能力已完整 —— `strategies/event_driven.py` + `features/event_calendar.py`，事件数据由 `bt_task.sh event-data` 盘前刷入 `$AQSP_RUNTIME_DATA_ROOT/pit_cache/`（PR #183/#184/#185/#186，2026-09-23）。但 `StrategyConfig(enabled=False)`，且**不在 `config/strategy_sources.yaml` 策略来源清单**。
- **决策（2026-09-23）：暂不启用，保持 `enabled=False`。**
  - 依据：宪法红线（`strategies/__init__.py:69`、`event_driven.py:146` 均注明）——**未经 walk-forward 双门验证不上线**。T3 双窗口门禁验证已于 2026-09-24 结案（**不成立**，见 §7 首条），该路线关闭。
  - 事件面已通过 `GET /api/events` + 个股研究页「事件面」区块（PR #187）向用户展示；**数据有价值 ≠ 打分已验证**。
- **启用前置条件（全部满足后才开，缺一不可）**：
  1. 换一个因子族候选并通过双窗口门禁且结果可复核（原 T3 方案 A 已判否）；
  2. 加入 `config/strategy_sources.yaml` 策略来源清单；
  3. 若需独立阈值/权重段，按 AGENTS §3.5 更新 `thresholds.yaml` 并附验证报告、升 `version`；
  4. `StrategyConfig(enabled=True)` 由单独 PR 提交并附依据。

## 8. 过期 / 断链登记（多 agent 漂移遗留，逐次清理）

**已归档（2026-09-22 清理）**
- 根目录 4 份互相矛盾的任务清单 → `docs/archive/process/`：
  `CODEX任务清单.md` / `Codex深度问题修复清单.md` / `修正后的任务清单.md` / `最终修正版任务清单.md`
- `docs/BEGINNER_DASHBOARD_INTEGRATION.md`（自述"已不再渲染旧新手看板"）→ `docs/archive/process/`
- Pending Review 4 份的判定结果：
  - **留 Active**：`DASHBOARD_GUIDE.md`（被 `src/aqsp/web/README.md` 引用，内容与 `deploy/nginx/aqsp-dashboard.conf` 的 302/5899/8900 一致）、`STRATEGY_HEALTH_INTEGRATION.md`（`src/aqsp/monitor/strategy_health.py` 存在，阈值与 API 同源码一致，有 `tests/test_strategy_health.py` 覆盖）
  - **归档 → `docs/archive/process/`**：`model-handoff.md`（2026-06-03 接手快照，已被本文取代）、`FETCHER_USAGE.md`（描述非当前数据面 `MultiSourceFetcher`，且"Tushare 为主源"与 `create_default_fetcher()` 的占位实现不符）

**断链引用**（文档里提到、但 `docs/` 顶层已不存在的文件；已逐处修正）
- **已归档文件**，引用改为归档路径：
  - `walkforward-variants-manifest.md` → `docs/archive/experiments/walkforward-variants-manifest.md`
  - `momentum-direction-2026-05-28.md` → `docs/archive/process/momentum-direction-2026-05-28.md`（原引用在 `docs/archive/experiments/walkforward-failures.md`）
- **确认已移除**（全仓不存在，引用处标注"已移除"）：
  - `CONSTITUTION-IMPLEMENTATION.md`、`user_data_dir.md`、`research_absorption.md`、`open_source_quant_research.md`、`chromium_browser_vs_google_chrome.md`、`DOUBLE-GATE-DESIGN.md`
  - `research_absorption.md` / `open_source_quant_research.md` 由 `docs/open_source_research.md` 与 `docs/secret-and-upload-policy.md` 引用，已改为现状说明。同批失效的还有 `docs/research_pipeline.md`、`docs/source_level_absorption.md`、`docs/research_absorption.json`、`data/open_source_research.jsonl`，以及 `scripts/collect_open_source_research.py` / `absorb_research_findings.py` / `validate_research_registries.py` —— 整套旧「开源采集 → 吸收」流水线已被 `docs/research/repo_radar.md` + `scripts/collect_research_registry.py` 取代。
  - ⚠️ 其余引用点集中在 `outputs/`（已被 `.gitignore`，不在仓库跟踪内），按「历史产物不改造」处理，**未改动**。

**代码里的默认路径（2026-11-24 逐项复核，别再当成待修缺口）**

| 代码位置 | 默认路径 | 真实状态 | 结论 |
|---|---|---|---|
| `src/aqsp/cli.py:854` `aqsp walkforward --report` | `docs/walkforward-2026-05.md` | **文件仍存在**，git 有历史 | ✅ 不是缺口，勿动 |
| `scripts/diagnose_momentum.py:244` `--output` | `docs/momentum-direction-2026-05-28.md` | 已归档到 `docs/archive/process/` | ⚠️ 唯一真过期项；改动价值低，未动 |
| `src/aqsp/research/summary.py:147` `absorption_path` | `docs/research_absorption.json` | 文件不存在，但代码 `if absorption_file.exists() else []` **显式处理缺失**，`tests/test_research_summary.py:70,98` 专门覆盖 | ✅ 可选输入设计，勿改成必填 |

> 🔴 11-24 教训：Explore agent 和本文件旧版把这三项全列成"须单独 PR 的缺口"，实际只有 1 项弱真。
> 判断默认路径是否过期，**必须同时验三件事**：文件是否真不存在 + 代码读到缺失时是否显式处理 + 是否有测试覆盖。
> 「默认值指向不存在的文件」本身不等于 bug —— 可选输入是合法设计。

**孤儿 / 死代码（2026-09-22 后端只读审计发现，证据见本机 `outputs/后端审计_2026-09-22.md`，未入库）**

处置状态（2026-09-23，分支 `chore/prune-dead-modules`）：
- ✅ **已删** `scripts/quick_start.py`（289 行）：导入**不存在**的 `aqsp.strategies.regime_adaptive`，且全仓无人调用（能力已迁到 `aqsp/regime/*`）。删前 grep 自查确认仅审计文档引用，无活引用。
- ✅ **已删** `src/aqsp/strategies/param_version_manager.py`：**全仓 0 引用**（连测试都没有）。删前 grep 自查确认仅审计文档引用，无活引用。
- ✅ **已修复** `src/aqsp/core/`、`src/aqsp/runtime/` 缺 `__init__.py` 的脆弱点：两包已补含 docstring 的 `__init__.py`，不再依赖 PEP 420 隐式命名空间包（其余 23 个子包均有）。
- ⏸️ **保留** 12 个仅测试引用、生产不可达的模块：`risk/unified_risk.py`（619 行三层风控，**生产无调用**，别把它当成"风控三层已生效"）、`risk/stop_loss.py`、`strategies/adaptive_evolution.py`、`briefing/renderer.py` 等。这些是**能力未接线**，不是死代码，留待后续接线 PR 处置，本次一律不删。

**本地分支**
- 历史积累的 `codex/*` 分支多数对应已关闭/已 squash 的 PR，**不可按 `--merged` 批量删**（全 squash 合并不留祖先链）。逐个以 PR `merged_at` + 内容在 `origin/main` 的对应为准。
- 2026-11-24 已清理 4 条：`feat/filter-verdict-cli`、`codex/tickflow-personal-operating-system`（功能全在 main，删）、`codex/strategy-watch-workflow`（含未入库的 tickflow roadmap 文档，保留）、`feature/holder-concentration-factor`（#541 负结果实验唯一 git 历史，保留）。另有 `codex/*` 历史分支未动。

**数据诚实性（2026-11-24 起，PR #616–#677 共 53 个）**

验收口径：任何展示面在「取数失败 / 该日确实为空 / 有数据」三态下必须可区分；
**刷新失败不得静默沿用旧值**；后端说不可用时必须把它的 `reason` 透出来。

已治理（按 PR 顺序）：
- `#616` `buildEmotion` fail-closed：6 个 count 任一缺失/非法 ⇒ 整块 unavailable，不渲染「涨停 0 家」。
- `#617` 海外指数涨跌幅缺数 → `changeClass(null)` 留空样式 + 「—」，不染成「涨」。
- `#618` 未结算信号收益缺数 → 同上，不显示成盈利。
- `#619` 市场风险条缺数 → 新增 `formatSignedRatio`，不显示成「零风险」。
- `#627` 指数页两段取数**各自结算**（`safe.ts:settleSection`）：原来只有「两段都失败」才报错，
  单段失败会停在「正在读取…」永远转圈且吞掉原因。同 PR 修 KPI「最高 0.0 分」
  （`daily-view.ts:topCandidateScore`，全缺分返回 `null` → 「最高分未取得」）。
- `#628` 策略页市场面四态（`market-risk-status.ts`）：原来把「正在读 / 请求失败 /
  后端 `available:false`」折成一句「暂无数据」，丢掉了后端给的
  「样本不足（需 ≥65 个交易日且 ≥300 只票）」。同 PR 统一 `趋势20/广度/回撤252`
  走 `formatSignedRatio`/`formatRate`（与 M4 同口径）。
- `#629` 筛选器判读三态（`filter-verdict-status.ts`）：`.catch(() => {})` 把「读取失败」
  误诊成「runner 工件未就绪」——误诊比不诊断更糟，会把用户引去查 runner。
- `#630` 六页刷新失败提示（`refresh-warning.ts` + `SnapshotRefreshWarning`）：
  provider 每 60s 轮询且刻意不清空 data，页面原来只处理 `error && !data`，
  刷新失败时静默沿用旧快照。
- `#631` 后端 `ic-dual-verdict` 降级补 `reason`：四种「没有数」（未回流 / 读失败 /
  按龄陈旧 / 源库冻死）原来完全不可区分，且读失败时 `stale=False` 是假陈述。
- `#633` 研报页下载/删除失败「什么都不发生」（无 `.catch` 的裸 promise）：改具名 handler +
  删除失败重新读列表核对。
- `#634` 研报**索引**损坏被当成「没有研报」：`_load_index` 把 `JSONDecodeError` 与
  「文件不存在」一起吞成 `[]`，下一次上传会把空列表写回索引 ⇒ 旧研报全部失联。
  新增 `IndexUnreadable`（→500，文案含「不代表没有研报」）；`save_report` 改为
  先读索引再落盘；`delete_report` 删不掉时**不动索引**并抛错。
- `#635` 持仓**账本**损坏被当成「0 持仓」：`portfolio._load` 同样吞 `JSONDecodeError`，
  而 `get_portfolio()` 回填名称时会 `_load()`+`_save()` ⇒ **一次普通行情刷新**就把空账本
  覆盖掉真实仓位（数据丢失级）。新增 `PortfolioCorrupt` fail-closed + 全局 500 处理器；
  刻意不自动改名（自动改名会让下一次读取变静默空账本）。
- `#636` 事件面**读失败**被误诊成「缓存没生成」：`/api/events` fail-soft 回 200 + 空结构，
  而空结构只有 `has_*_data:false`。新增 `available`/`reason` + `eventsAvailability()`；
  缺缓存仍标 `available:true`（端点读成功了）。同 PR 修两处「失败后仍说暂无/正在读取」。
- `#637` 信号页把「没拿到」渲染成 0：`data.overall?.observations ?? 0` 在台账尚未可用时
  把标题写成「0 条」，而页面下方写着「台账尚未可用」。改「条数未知」+ `?? "—"` 同口径。
- `#638` 事件面「缺缓存」与「没事件」共用一句话：前端此前完全没消费 `has_*_data`，
  于是「哪一面没预加载」和「该股确实没有这类事件」渲染成同一句。新增
  `eventsFaceGaps()` 具名缺面 + `eventsEmptyDetail()` 按缺面数分档。
- `#640` 影子账户把「超额未知」渲染成「无超额」：`exc_mean` 可空
  （`paper_ledger.py` 在「有结算笔数但这些笔没有基准超额」时留 `None`），
  原判定 `(row.exc_mean ?? 0) > 0` 落到最后分支 → 标签写「无超额」（确定的否定），
  同一行的数字列却显示「—」。新增 `paper-arena-view.ts` 六态 `sleeveVerdict`
  + `unknownExcessCount` 在汇总卡披露「另有 N 个策略超额未知，未计入」。
- `#642` 成本未知的平仓成交被写成「0 元成本 / 0% 收益」（issue #641）：`_apply_close`
  在 `cost == 0`（legacy 补录、账本无对应持仓）时算 `pnl = price * shares`、
  `pnl_pct = 0.0`，`realized_pnl` 又把这些伪造值一起加总 ⇒ **已实现盈亏量级错误**。
  写入侧改 `None`；读取侧 `_with_known_cost` 纠正老账本（零迁移）；`realized_pnl`
  只累加有据成交 + `realized_pnl_unknown_count`；前端 `normalizeClosed` 改
  `asNullableNumber`（`asNumber` 会把 null 折回 0）、成本价 0 显示「—」。
- `#643` 「台账有信号但还没结算」被写成「没有信号记录」：`performance_payload()`
  的 `settled.empty` 分支把 `_unavailable()` 整包铺开、只改 `available`，于是
  `freshness` 仍是占位（`latest_signal_date: ""` ⇒ 页面写「最新信号日 未记录」，
  而 `signal_date` 就在 `rows` 里）、`overall` 仍是 `None`（⇒「条数未知」，
  而 0 条是**确定的零**）、`reason` 仍带「还没有已结算的信号」（available=True
  却带着不可用原因）。改为真实 `_freshness(rows, path)` + `overall` 零值 +
  `reason: ""`；前端新增 `strategyEmptyNotice()` 区分「有信号缺结算」与「没有记录」。
- `#644` 监控页**读取失败后仍显示「正在读取」**：`{error ? …}{!followups ? <LoadingState/>}`
  —— 失败时 `followups` 仍是 `null`，两句同时出现且永远转圈（与 M10 同类，只是没走
  `settleSection`）。新增 `safe.ts::readState()` 把「失败优先于正在读」写进函数；
  失败态补「这不代表没有需要跟进的方案」+ 重试按钮。
- `#645` 共享的时点格式化**没固定时区**：`formatAqspTime`（`useAqspSnapshot.tsx:142`）
  的 `toLocaleString("zh-CN", {...})` 少了 `timeZone`，走**浏览器本地时区** ⇒ 同一份快照
  在 UTC 机器（runner 就是 UTC）上显示的时刻差 8 小时，而这类时点正是「这份数据是什么
  时候的」的唯一依据（刷新提示 / 快照生成 / 候选冻结 / 研究任务记录，6 个调用点共用）。
  同 PR 补 `ReportsPage`（研报时间）与 `NotesPage`（笔记时间）两处。
  实现下沉到 `lib/format.ts`（纯函数，契约测试可直接求值），旧路径改 `export { … }`
  保持 6 个调用点不变。
  ⚠️ **反向验证的时区陷阱**：去掉 `timeZone` 后在本机（`TZ=Asia/Shanghai`）测试**全绿**，
  只有 `TZ=UTC npm test` 才转红 —— 时区类断言必须在 UTC 下复跑。
- `#646` 收评 6 段把「读失败」写成「还没生成」：6 个 `build_*_section` 在
  **文件不存在 / 读失败 / 表头残缺 / 读到了但没可用行**四种情况下一律 `return ""`，
  端点再折成 `available: false`，面板只能猜一句「盘后预载未落盘或数据源缺缓存」
  —— 误诊比不诊断更糟，它把「文件坏了 / 权限不对 / 磁盘满」引向了「等下一次调度」。
  builder 加 keyword-only out-param `diagnostics`（不传则零副作用，返回值仍是 `str`，
  上百处调用与测试不受影响）落 `SECTION_OK/MISSING/UNREADABLE/MALFORMED/EMPTY`；
  端点透出 `state`+`reason`，非 `ok` 必有原因；前端 `sectionGapCopy()` 按五态取徽标与
  说明（未生成 / 读取失败 / 产物残缺 / 无内容 / 状态未知），`allSectionsGapNotice()`
  汇总时优先点名故障。老后端没有 `state` ⇒ `unknown`，**不猜成 `missing`**。
- `#647`（CI，非展示面）前端契约断言**跑两遍时区**：`TZ=UTC` + `TZ=Asia/Shanghai`。
  `#645` 的坑是「本机 CST 全绿、只有 UTC 才红」，而 CI 原来靠 `ubuntu-latest` 默认
  UTC 才碰巧有效（镜像默认值 ≠ 契约）。两个方向各只能覆盖一半，必须都跑。
- `#648` 板块资金流把「没取到」渲染成「持平 0.00%」，且**一个坏单元格能把整页打成 500**：
  `_sectors()` 原来每列都写 `round(float(row.get(col, 0) or 0), 2)` ——
  ① `float(nan or 0)` **兜不住 NaN**（NaN 是真值）⇒ payload 里出现 NaN ⇒ starlette 的
  `JSONResponse.render` 是 `json.dumps(..., allow_nan=False)` ⇒ `ValueError` ⇒
  整个 `/api/market/overview` 500；② 整列缺失时「这一列没了」被渲染成「全板块 0.00%」；
  ③ `str(nan)` 得到字面量 `"nan"`，缺名字的板块会以「nan」上榜。
  改为 `_flow_num`/`_flow_round2`（NaN/inf/非数一律 `None`）+ `_flow_text`（NaN→空串）；
  `net` 不可用的行**整行不进榜**（它是排序键，编 0 会冒充「资金持平」）；
  有行却一行都用不上 ⇒ 记 `source_health` 错误说明是源侧结构问题。
  前端 `SectorFlow.pct` / `SectorFlowRow.pct` 改 `number | null`，交给已有的
  `formatSignedPct`（null → 「—」）与 `changeClass`（null → 不着色），**页面禁 `pct ?? 0`**。
- `#649` 板块资金「净流入前列 / 净流出前列」**按位置切片会重叠**：原写法
  `sectors.slice(0,6)` / `sectors.slice(-6).reverse()`，行数 ≥12 时没问题；一旦少于
  12 行，同一板块会同时出现在两个榜里 —— 一个**正净额**的板块被标成「资金净流出」。
  `#648` 起后端会把净额不可用的行整行剔除，数据源部分降级时行数就会变少，这条路径
  因此变得可达。改 `sectorFlowHeadTail()` **按净额符号分区**（流入榜只收 `net > 0`、
  流出榜只收 `net < 0`，`net === 0` 两边都不进），顺带修正语义。
  ⚠️ 原 `marketViewContract.sectorsTopTakesHead` 断言「3 行全进流入榜」，而 fixture 里
  净额 −9.8 亿的「地产」就在其中 —— **该断言本身在编码这个 bug**，已改为只收 `net > 0`。
- `#650` **响应边界兜底**：`#648` 只修了 `market._sectors()` 一处，但「一个 NaN 打挂
  整个端点」这条路径在后端 93 个端点里每一个都重新开着 —— 任何新写的
  `round(float(x), 2)` 都能把它打开（`float(v or 0)` 兜不住 NaN，NaN 是真值）。
  新增 `backend/json_safe.py`：`scrub_non_finite()` 深递归把非有限浮点映射成 `null`
  （= 未知，前端已有 `null → 「—」` 如实呈现），`SafeJSONResponse` 挂
  `FastAPI(default_response_class=...)` 一处接线覆盖全部「返回 dict」的端点。
  **这是兜底网不是免修许可证**：命中一律记 warning 带字段路径（`data.sectors[1].pct`），
  逐点的 `_flow_num` / `asNullableNumber` 仍要写（它们能给「为什么没有」的语义）。
  干净 payload **原样返回同一对象**（先做一次廉价探测，零复制零分配）。
  同 PR 修 `_events_jsonable` 的两个隐含假设：原实现只遍历**顶层 key** 且只认 NaN
  （不认 ±inf），能用纯粹是因为当前 5 个事件 dataclass 恰好扁平 —— 巧合不是契约。
- `#651` **涨跌配色口径分叉**：9 处页面手写 `x >= 0 ? "aq-tone-up" : "aq-tone-down"`，
  两个确定的错：① **`0` 被染成「涨」** —— `format.ts::changeClass` 的约定是「0 为中性」，
  `+0.00%` 染红等于把「没有变化」说成「上涨」；② **`null` 被染成「涨」** ——
  JS 里 `null >= 0 === true`，而 `#648`/`#650` 之后后端把「没取到」落成 `null` 是常态，
  于是同一行**数字列写「—」、颜色却报涨**，比不显示更糟：它给了一个不存在的方向。
  `BacktestPage` 更是同一文件里两种口径并存（局部 `tone()` 对 0 中性，第 205 行手写对 0 判涨）。
  统一走 `changeClass`（顺带把重复的符号拼接换成 `formatSignedPct`，删掉 `BacktestPage` 的重复实现）；
  新增**全仓 `.tsx` 扫描**守卫 `noPageHandRollsDirectionTone` —— 这个三元表达式没有任何
  合法用法（唯一正确实现在 `changeClass` 内部且是 `value > 0`，不被该正则命中），
  另配 `directionToneSitesUseChangeClass` 正向断言（防「整列删掉」蒙混）与
  `directionToneScanCoversFiles`（防路径走错导致的零命中假绿）。
- `#652` **金额单位错配：4.71 亿被渲染成「0.0 亿」，柱状图每根柱子零宽**。两个源的
  金额单位都不是元，而前端一律按元算（`api.ts` 的 `SectorFlow.net` 注释就写着「元」，
  `chart-options.ts` 是 `row.net / 1e8` + 轴标签「净额(亿)」，两页都走 `formatYi()`）：
  同花顺行业资金流的流入/流出/净额是**亿元**（实测 2026-11-25 农产品加工 `-4.71`、
  养殖业 `-2.77`），东财概念板块 f62 是元但 `ConceptBoardItem` 存**万元**
  （`concept_board.py` ÷1e4，收评 `board_fund` 段按万元读同一份 CSV，那条契约不动）。
  于是列表把 4.71 亿净流出写成「-0.0 亿」（看着像资金持平）、图表 `4.71/1e8 = 0.00`
  让**每根柱子宽度为 0** —— 一个看起来合理的错答案比空白更糟。
  修法在**出口一次性换成元**（`market._YI_TO_YUAN` / `_flow_yuan`、
  `info_stream` 的 `main_net_inflow` ×1e4），前端一行不动：它本来就是按元写的，
  是后端违反了自家契约。守卫写两条量级断言（`net / 1e8` 必须还原源里的亿元值、
  `formatYi` 口径必须是「2.7 亿」），外加「换算不改排序 / 不把未知洗成 0 /
  净占比是百分数不能跟着乘」。⚠️ 教训：**单位是契约的一部分，跨层共用同一个
  `formatYi` 时，谁的单位错了都不会报错，只会安静地给出一个像真的错数。**
- `#653` **非零净额被抹成「0.0 亿」**：`formatYi` 固定 `toFixed(1)` 后加「亿」，
  于是 −288.3 万元渲染成「**-0.0 亿**」—— 负号 + 零，自相矛盾；而同一行旁边还有
  红绿配色，颜色说「流出」、数字说「资金持平」，两个相反结论并排。
  实测可达（东财 bkzj 554 个概念板块）：**19 个板块**主力净额落在
  `0 < |x| < 1000 万元`（天津自贸区 −288.3 万 / 草甘膦 +291.7 万 / 资源开采概念 −955.5 万）。
  改成分档 `|v| ≥ 1 亿 → 亿(1位)`、`|v| ≥ 1 万 → 万(0位)`、否则 `元`；
  **恰好 0 仍给「0.0 亿」**（0 是真零，不失真，既有口径不动）。
  同口径修 `sectorFlowOption` 的提示框（原来也是 `toFixed(2) + " 亿"`，
  288 万的柱子提示「0.00 亿」）。守卫用 14 个非零探针断言
  **输出不以 0 开头**（`/^-?0(\.0+)?\s/`），比逐个写死期望值更难绕过。
- `#654` **推送把「已否证 / 未过门」的策略当推荐发**（老大 11-25 附截图：「这种通知
  全都都是失效的完全没必要出现 还影响视觉 我只需要有用的」）。截图那条「今日推荐」
  四个模式（P4 未过门 + P1/P2/P6 已否证）各带 2–3 只票。11-24 的处理是把判决词
  印在每行上，只解决**可读性**，没解决**该不该推**：四行里三行写着否证，读起来的净效果
  是「今天有四个模式在选股」，与事实（0 个过门）相反。
  改为**按状态拦截**：`mode_card.PUSHABLE_STATES = ("passed",)` + `is_pushable(sid)`，
  四个推荐出口（`render_picks_only` / `render_phase_card` / `render_daily_footer` /
  `render_settlement_card`）统一走同一道闸；全不可推时返回空串，调用方已有的
  `if not body: return 0` 直接静默。**静默必须留正证据**：日志区分「今日无成交」与
  「N 笔成交全落在未过门/已否证模式上（过门 0/6）」。收盘复盘里「明日 watchlist =
  上述涨停票次日再盯二板」这句行动建议改为中性事实（二板接力已否证，不作次日买入依据）。
  ⚠️ 契约反转：旧测试守的正是「未过门也要推」（`test_picks_only_sorts_rejected_last`），
  改契约时保留每个测试原本要守的东西（参数印出 / note 原样 / 未成交归并 / 截断 /
  footer / 结算口径），做法是 `monkeypatch` 该模式为 `passed`；另新增
  `test_no_mode_is_pushable_today`（当前 0 个过门 ⇒ 四个出口全空，**硬编码**而非从注册表
  推导，推导式断言会自己验自己）与 `test_gate_blocks_every_non_passed_state`
  （`unverified`/`rejected`/`control` 逐一验证被拦 —— `control` 是负向对照，推出去
  等于让老大按对照组下单；随后切回 `passed` 验证仍能推，闸门不是永久关闭）。
  ⚠️ 影响：当前 0 个模式过门 ⇒ **盘中「今日推荐」推送会停止**，这是预期效果不是故障。
- `#655`/`#658` **打板卡片的 B 格措辞**（⚠️ 第一版改错了，过程保留）：
  第一版把「可买」改成「B格(收盘封死·当日买不到)」，依据是「B 格 = 收盘封死，那个价买不到」。
  **这是错的** —— 两个层被混在一起：
  - **回测** B 格 = `收盘 ≥ 涨停 × 0.998`（日线只能这么判）⇒ 收盘价确实买不到，
    这是回测收益属于语义前视的原因（`executable_entry_probe.py` / `board_portfolio_sim.py`）；
  - **实盘检测** B 格 = `open 没顶死 + 摸板`（实时报价能判）⇒ 集合竞价确实成交了、
    盘中存在可买时点（`paper_strategies.py` 的 B 格说明）。
  实盘卡片显示的是**实盘检测**结果，所以「买不到」才是假陈述。而策略被否证的原因是
  **按可成交的「摸板即买」口径算收益为负**（首板 −0.94%/t−16.5 … 4板+ −2.80%/t−8.0），
  不是买不到 —— 把否证原因写成「买不到」是另一种不诚实。
  最终文案：`B格盘中可排进` / `B格(盘中可排进)` / `✅ 新增触板`（去掉「可买」的歧义，
  但不反过来说「买不到」）；判决由复盘结尾「二板接力已否证，不作次日买入依据」承担。
  守卫 `test_board_cards_state_fill_feasibility_not_a_buy_recommendation`（三件事：
  不出现「可买」、不出现「买不到」、复盘必须同时给判决）+ 源码级
  `test_no_source_uses_the_wrong_bcell_wording`（扫 `research/*.py`，`B格可买` 与
  `B格收盘封死` 都无合法用法）。
  ⚠️ 教训：**「可成交」与「有效」是两个维度**，一个维度的事实不能用来解释另一个维度的
  结论。当时照抄了 `test_mode_card.py` 里「B 格的定义是收盘封死在涨停」这句（那是回测口径的
  简写），没去读 `paper_strategies.py` 的完整说明，于是把正确的卡片改成了错误的。
  ⚠️ 反向验证踩坑：锚点用 `s.count("新增触板") == 1` 失败 —— 日志文案「本轮既无新增触板」
  也含这四个字。**锚点必须带上下文（`L.append("✅ 新增触板: "`）。**
- `#659` **复盘卡把 20 只触板票全列**：`--recap --dry-run` 打真实数据才看出来 ——
  2026-11-25 当天 B 格 **22 只** + A 格 5 只，一条复盘消息 **30 行**。
  `mode_card.MAX_ROWS` 的注释早就写明「企业微信 markdown 一屏大概 20 行，超了老大要
  滑屏才看得完，等于没推」—— **盘中卡片守了，复盘卡漏了同一个约束**。
  新增 `MAX_RECAP_ROWS = 8` + `_recap_rows()`：按**封单金额降序**取前 8，
  其余折成「…还有 N 只（按封单降序取前 8）」。按封单排序是因为封单厚度才是
  「排不排得进 / 封得牢不牢」的判断依据；截断必须写明还有多少与口径（静默丢弃更糟）。
- `#660` **影子账户摘要列六个已否证模式的笔数**：`P1 5笔(6持) 超t- ｜ P2 16笔…`
  六个模式全是未过门/已否证，这行既不是推荐也不构成判断依据（t 值还缺失），
  只是把判决结果又当成绩汇报一遍。与 `#654` 同一道闸：只列 `is_pushable` 的 sleeve。
  **全部不可推时不写「尚无信号」** —— 那是假陈述（明明有 388 笔成交），
  实际是「有成交但模式都不过门」。净值与敞口构成照旧保留（真实风险，不是模式成绩）。
- `#656` **静默无法解释「为什么没推」**：`#654` 之后「今天 0 次推送」变成常态，
  而 `push_health.json` 只有 `ok_count`/`fail_count`，于是「非盘中」「无新信号」
  「有成交但模式都不过门」三种情况全是 `ok_count:0, fail_count:0`，**完全不可区分**。
  新增 `SKIP_CODES` 固定枚举（`no_signal`/`no_fill`/`no_pushable`/`duplicate`/`other`）
  + `_record_skip(code)`，写 `skip_count`/`skip_reasons`/`last_skip_reason`；
  **静默不计入 `fail_count`**（混在一起会让这个数字失去意义：一天上百次静默 =
  上百次「推送失败」，然后就没人看了）；`_silence_reason(picks)` 区分「今天真没成交」
  与「成交了但模式都不过门」；`--health` 补两行人类可读摘要。
  `_load_health` 跨天重置改为「保留结构、清空计数」（原来直接赋新 dict，
  新增字段会被漏掉 —— `skip_reasons` 第一次实现就这么丢过一次）。

**金额 / 比率单位契约（`#652`/`#653` 的根因台账，改动前先查这张表）**

🔴 单位是契约的一部分：跨层共用同一个 `formatYi` / `formatSignedPct` 时，
**谁的单位错了都不报错、不崩、不进日志**，只安静给出一个像真的错数。

| 数据源 / 字段 | 源里的单位 | 内部/出口 | 备注 |
|---|---|---|---|
| 同花顺行业资金流：流入/流出/净额 | **亿元** | 出口换**元**（`_YI_TO_YUAN`） | 实测农产品加工 `-4.71`、养殖业 `-2.77` |
| 东财 bkzj `f62`（主力净额） | **元** | `ConceptBoardItem` 存**万元**（÷1e4）；`info_stream` 出口再 ×1e4 回元 | 收评 `board_fund` 段按万元读同一份 CSV |
| 东财 bkzj `f3` / `f184` | ×100 | ÷100 → 百分数 | |
| 东财 clist `f6`（成交额）/ `f20`（总市值）/ `f21`（流通市值） | **元** | 元 | 直接喂 `formatYi` |
| 东财 push2ex `amount` / `ltsz` | **元** | 元 | 实测 723435472（7.23 亿）/ 7132689321（71.3 亿） |
| 东财 datacenter 龙虎榜净额 | **元** | ÷1e4 存**万元** | |
| `market_risk` 的 `trend_20`/`trend_60`/`breadth`/`drawdown_252` | **比率**（0~1） | 比率 | 走 `formatSignedRatio`，不是百分数 |
| 涨停四池 `seal_rate`/`break_rate`/`promotion_rate` | **比率**（0~1） | 比率 | 走 `formatRate` |
| `SectorFlow.net`（接口契约） | **元** | 元 | `api.ts` 注释即契约；前端 `formatYi` / `÷1e8` 都按元 |

⚠️ 写测试夹具时**量级必须真实**：`净额: 100.0`（=100 亿写成 100）、`"1.25e8"`（=1.25 亿亿）
这类夹具会让「单位错了」在测试里完全看不出来（`#652` 顺手修了两处）。

- `#663` **龙虎榜缺数渲染成 0，且 `'-'` 会打挂端点**：实测 2026-11-24 东财 datacenter，
  **可转债 `123283` 的 `TURNOVERRATE` 返回 `None`**（东财对部分标的不给该列）。
  原写法 `round(float(v or 0), 2)` 两个后果：① `None or 0` = 0 ⇒「未披露」变成
  「换手率 **0.00%**」「净买 **0 万元**」（上了龙虎榜的票换手率不可能是 0）；
  ② 东财用 `'-'` 占位时 `'-' or 0` 仍是 `'-'` ⇒ `float('-')` 抛 `ValueError`
  ⇒ 整个 `/api/stock/dragon-tiger` **500**（反向验证时实际打出）。
  新增 `_billboard_wan` / `_billboard_pct`（复用 `_numf`）：缺失/非数/`'-'`/NaN/±inf 一律 `None`。
  ⚠️ 该端点目前**没有前端消费者**（前端展示的是事件日历的 `recent_longhubang`，另一个数据源），
  但端点活着、可被直接调用，且修的是崩溃级问题，所以照常修；**未部署**（prod 需仓主授权）。
  ⚠️ 教训：扫 `or 0` 时要同时看「显示成 0」和「抛异常」两种后果 —— 后者更严重但更难发现。
- `#665` **复盘卡把已否证策略的执行口径当事实推出去**：`#654` 只给推荐类出口加了
  `mode_card.is_pushable` 闸门，复盘卡走的是另一条渲染路径（`build_recap` → `_recap_rows`
  → `_row`），仍在推 P1/P2/P6 的内容。2026-11-25 15:12 真实卡片每行都是
  `…·B格盘中可排进·封单6646万 T+1兑现·炸板-5%` —— 前半段是盘口事实、后半段是**已否证策略
  的执行计划**，读者只记住后半句。改三处：① 新增 `_recap_row`，复盘行只留
  代码/名称/现价/几板/是否一字/封单（盘中卡 `_row()` 语义不动）；② 标题「封二板」→
  「今日触板」（行里既有 1 板也有 5 板，标题与数据自相矛盾，且卡片免责句本来就写「当日触板事实」）；
  ③ 敞口标签「（P1+P2+P6）」改从 `paper_ledger.BOARD_SIDS` 派生，常量取不到就不印括号。
  真实卡片 18 行 → 16 行。已同步 runner 推送链路（`_bak/20261125g/`，md5 `84d5e408`→`7077db39`）。
  ⚠️ 顺带发现 `build_markdown()` / `_row()` 已不在推送路径（盘中出口是
  `mode_card.render_picks_only`），但**没有闸门** —— 谁把它接回去就会重演 #654 之前的状态，
  已登记 `#666`。
- `#667` **影子账户桥接测试写死日期，到期当天自己变红**：`tests/test_paper_arena_bridge.py`
  的 fixture 写 `generated_at = 2026-11-20T15:12:00+08:00`，而
  `paper_arena_bridge.STALE_AFTER_DAYS = 5` ⇒ 2026-11-25 当天越线，
  `test_good_payload_passes_through_verbatim` 断言 `stale is False` 失败。
  **红的是 `verify (shard 0)`，看起来像刚提交的改动坏了** —— 我这次先怀疑自己的改动，
  拉日志才排除。改为 `_stamp(days_ago)` 相对当前时间算（用 `now_shanghai()`，不裸
  `datetime.now()`），「多旧算旧」变成显式入参；新增「过期只标 `stale`、`available` 仍 True」
  一条。刻意不测「正好等于阈值」的边界（`timespec="seconds"` 截断微秒会让
  `> timedelta(days=N)` 判 True，变成靠运气过的线）。
  ⚠️ 同类风险已扫一遍：`test_performance_bridge` / `test_ic_dual_verdict_endpoint` /
  `test_aqsp_api` 的新鲜度断言都用相对时间或 `stale_after=2099-…`，**只有这一处**是
  「过去日期 + 断言新鲜」的危险方向。
- `#669` **复盘卡没有解释「为什么今天没有推荐」**：`#654` 闸门下当前 0/6
  过门 ⇒ 盘中「今日推荐」永远为空，`render_daily_footer` 全不可推时返回空串（对的），
  而**复盘卡是他当天唯一会收到的推送** —— 于是「闸门拦住了」和「链路坏了」长得一模一样。
  新增 `mode_card.render_gate_line()`：有模式过门 ⇒ 空串；全部不过门 ⇒
  `> 模式过门 0/6：其余未过门/已否证，盘中不发推荐`，接在复盘免责句之后。
  一行，不印笔数/t 值/模式成绩。这是 `#662`（健康状态没有展示面）的**轻量落点**：
  不新增展示面、不动 prod；`#662` 的 web 面板仍需仓主排期，保持 open。
  2026-11-25 runner 真机 dry-run 复核：17 行，事实字段一个不少。
  runner 同步 `_bak/20261125h/`：mode_card `ff786258`→`d54c6fb2`、
  shortline_cloud `7077db39`→`d7e902d0`。
- `#671` **结算失败把旧净值当今天的印出去**：`arena_close()` 内部 `except` 只 `print`
  就吞异常，调用方无从得知；`main()` 照常组卡，`_arena_brief()` 照常读账本 ⇒
  「今日结算失败、净值是几天前的」被印在**日期为今天**的卡片上。违反 GOAL 明写的
  「刷新失败不得静默沿用旧值」。三处接线：`arena_close() -> bool`（marker 只在成功后写）、
  `_arena_brief(stale=False)` 在净值后印 `（今日结算失败，净值/敞口为上次结果）`
  （加说明、不抹数字；`mix` 与净值同源，所以免责同时覆盖敞口）、
  `main()` 透成 `_arena_brief(stale=not close_ok)`。
  ⚠️ `test_main_wires_close_result_into_brief` 是**源码守卫**：单测只守函数本身，
  main 漏接的话线上照样静默沿用旧值，而单测全绿。反向验证 3 次逐条转红。
  runner 同步 `_bak/20261125i/`，shortline_cloud `d7e902d0`→`37a2f70b`；
  真机 dry-run 复核正常路径**不误报** stale。
- `#673` **`--health` 拿日志行数冒充 cron 触发次数**：原打印「今日 cron 触发 N 次」，
  N = `cron.log` 里含今日日期的行数 —— 卡片正文 `## 打板收盘复盘 2026-11-25` 含日期被算成触发，
  大量 `[cloud] …静默` 行不含日期一次都没算。今天报 53 次，既不是触发数也不是下界，
  而这段代码的注释写着「cron 是否真在跑（区分无信号 vs 系统没运行）」⇒ 会误导存活判断。
  改用健康文件里每次出口都记的三个精确计数：
  `今日推送出口 N 次（成功 A / 失败 B / 静默 C）；最近: <last>`；
  日志只保留「文件不存在 ⇒ 无法核对」的存在性检查。runner 真机复核 `23 = 6 + 0 + 17` 严格对账。
  runner 同步 `_bak/20261125j/`，shortline_cloud `37a2f70b`→`0fba643b`。
- `#675` **Web 把账本口径的统计显著性当策略过门判决**：runner 真实产物 P1
  `exc=+2.03 q=0.00`、P6 `exc=+2.93 q=0.00`，前端判「扛住多重检验」且 tone=ok（绿），
  KPI 印「扛住多重检验的策略 2/6」；而 `paper_strategies` 是 P1/P2/P3/P5/P6=`rejected`、
  P4=`unverified` ⇒ 推送闸门 **0/6 过门**，两边直接相反。
  根因：账本按「检测到信号时已封死也照买」记账（`#574`），封死那一下的收盘价买不到，
  那批超额含语义前视，正是 P1/P6 被否证的原因 —— 与「可成交 ≠ 有效」（`#655`/`#658`）
  同类错误。只改展示口径不动记账：label 去判决词（`超额显著` / `超额为正·未过FDR`）、
  tone 从绿降中性、为正两态带 detail 说明边界；KPI 改「账本口径超额显著」并印
  「不代表策略过门」。`不拦买入` 说明保留。
  前端契约 61 文件 / 1780 断言全绿，新增 7 条（含 2 条源码守卫），反向验证 3 次逐条转红。
  ⚠️ 纯前端改动，**不动 runner；prod 部署需仓主授权**。
- `#677` **影子账户净值没标数据截止日**：`paper_arena_bridge` 已透 `as_of`、
  `PaperArenaPayload` 也声明了，但 `BacktestPage` 一次都没渲染 —— `stale` 阈值 5 天，
  中间 1~4 天用户看到净值却不知道是哪天的。周末/长假不该误报过期，所以不做
  「早于今天就警告」，改为在「当前总净值」卡印 `数据截至 <as_of>`；
  缺日期给显式态「未取得（无法判断这些数字是哪天的）」，不显示空占位。
  不动后端桥接 / stale 阈值 / 账本。前端契约 1783 断言全绿，新增 3 条源码守卫，
  反向验证 2 次逐条转红。⚠️ 纯前端，**prod 部署需仓主授权**。

⚠️ **待议（生产不可达，未改）**：`research/paper_report.py:452` 的说明文案仍写
「打板只买 B 格……说明确实存在可买时点，**照买**」—— 与 `#654`/`#658` 同一条问题
（拿已否证策略当行动建议）。但该模块生成的 `paper_report.html` 在后端/前端**没有任何
消费者**（`/api/aqsp/paper-arena` 读的是 `paper_export.py` 的 `paper_arena_api.json`），
按「生产不可达模块只标注不硬改」处理；接线前必须先改掉这句。

防复发守卫：
- `frontend/src/lib/data-honesty-pages.test.ts`——页面层源码守卫（含接线断言：
  6 页必须挂 `SnapshotRefreshWarning`、不许页面内联重复文案、候选清单非空前置、
  事件面空态必须被 `failed` 门槛拦住）。
- 三态/四态一律建模成 lib 层 discriminated union 再让页面消费，断言写在这些纯函数上
  （`settleSection` / `topCandidateScore` / `marketRiskStatus` / `filterVerdictFallback` /
  `shouldShowRefreshWarning` / `sidebarDataStatus` / `eventsAvailability`），比在 JSX 里堆 `??` 可测得多。
- 后端降级路径**必须带可辨识 `reason`**，且要有「遍历所有降级分支断言无空 reason」的测试
  （`test_ic_dual_verdict_endpoint.py::test_every_unavailable_response_carries_reason`、
  `test_closing_review_section_state.py::test_every_non_ok_state_carries_a_reason`）。
- 后端 fail-soft 的 builder 若要区分「为什么没数据」，用 **keyword-only `diagnostics`
  out-param**（`build_*_section(..., diagnostics=dict)`），**不要改返回值类型** ——
  返回值被上游渲染与上百处测试当 `str` 消费，改类型会牵连一整片；out-param 不传即零副作用。
- 后端响应边界有**非有限浮点兜底**（`backend/json_safe.py` 的 `SafeJSONResponse`，
  挂在 `FastAPI(default_response_class=...)`）：NaN / ±inf → `null` + warning 带字段路径。
  它保证「一个坏单元格不会让整页 500」，但**不代表逐点可以不写守卫** ——
  逐点守卫给的是「为什么没有」的语义，兜底层只能给 `null`。新增端点不需要为它做任何事。
- 前端契约断言在 CI 里跑**两遍不同时区**（`TZ=UTC` + `TZ=Asia/Shanghai`，见 `ci.yml`
  的 `Frontend contracts (UTC)` / `(Asia/Shanghai)`）：时区类断言**每个环境只能覆盖一半** ——
  `#645` 就是「本机 CST 全绿、只有 UTC 才转红」，反方向（写死北京时区、只在东八区成立）
  同样存在。job 级 `TZ: UTC` 是显式钉住，不依赖 runner 镜像的默认值。

🔴 已核实**不是**缺口（勿重复排查）：
- `frontend/src/data/sectors.json` 的 17 个 `verified:false` 空骨架——前端零 import。
- `chart-options.ts` 的 `?? 0`——在 `.filter(changePct != null)` 之后的 TS 收窄。
- `PositionActionPlan` 的 `scenario.realized_pnl?.toFixed()`——`position_action_plan.py:143`
  限定 `choice ∈ {reduce, t_watch}`，`realized_pnl` 只在 `reduce` 有值，`t_watch` 已被三元短路 ⇒ 不可达。
- `AlertEvent.pnl_pct` 的 `asNumber` 兜 0——后端 `SellReminder.pnl_pct` 必填 float，不可达。
- `buildBreadth` 的 `s.flat ?? 0` / `industryTotal ?? 0`——前一行已严格校验 / 仅用于「是否加后缀」。
- `backend/astock.py:individual_info` 双源全失败返回 `{}`——`/api/info` 前端零调用（不可达）。
- `backend/astock.py:kline` 备源失败返回 `[]`——`/api/kline` 前端零调用（不可达）。
- `backend/market.py:_write_cache` / `_refresh_in_background` 静默失败——缓存写失败只会导致
  下次重算；SWR 旧值语义由下游时间戳承担，属有意设计。
- `backend/cli_runtime.py:_pump` 读线程异常后放 EOF 哨兵——随后 `proc.wait` 会拿到退出码，
  非零必抛；且该层不直接面向展示。
- `backend/alert_events.py` / `backend/watchlist.py` 的损坏处理——**已是对的范式**：
  另存 `.corrupt` 保留现场后再返回空。
- `catalyst-sector-view.ts:22` 的 `warnings?.length || 0`——消费端写的是
  `{warningCount ? <span>…</span> : null}`，0 时**不渲染任何东西**，不会显示「0 项告警」。
- `StockDetailDrawer.tsx:155` / `StockResearchPage.tsx:246` 的 `quote.turnover_pct.toFixed(2)`
  ——后端 `astock._parse_gtimg` 的 `num(i)` 恒返回 `float`（`float(vals[i]) if vals[i] else 0.0`，
  `ValueError`/`IndexError` 都兜成 `0.0`），所以 `.toFixed()` 不会抛。
  ⚠️ 实测 8 只票（含亏损股 `pe` 为负值）腾讯行情**没有**空串字段，`turnover_pct`/`pe_ttm`/
  `pb`/`vol_ratio` 全部有值 ⇒ 该 `num()` 把「缺失折成 0.0」的语义在真实数据上**未观测到**
  （`float | None` 化的爆炸半径覆盖 `Quote` 全部消费点，没有可达性证据不动）。
  同理 `market.py:391` 的 `round((astock._numf(p.get("p")) or 0) / 1000, 2)`——
  `_numf` 只把非 `int/float` 归 `None`，push2ex 是 JSON（JSON 没有 NaN）⇒ 走不到 NaN。
- `src/aqsp/data/concept_board.py::_to_float` 把 `None`/`""`/非数折成 `0.0`——
  **实测 2026-11-25 东财 bkzj 全 554 个概念板块，f3/f62/f184/f104/f105 全部是数值**，
  没有 `None`、没有字符串占位 ⇒ 折 0 的路径在真实数据上未观测到，不改。
  （它的兄弟问题「金额单位」是真缺口，已由 #652 修。）
- `CandidateSection.tsx:669` 的 `index.history_window ?? 0`——`aqsp_bridge.py:928`
  恒写 `len(dates)`（int），且 `types/aqsp.ts:284` 声明为非空 `number`，不可达。
- `performance-view.ts:168` 的 `asRecord(record.status_counts) ?? {}`——`statusCounts`
  **全仓无任何消费方**（只在 :72 定义、:168 计算），是死字段，不构成展示面。
- `DiscussionSection.tsx:203` 的 `disagreement_points?.length ?? 0`——`aqsp_bridge.py:1766`
  用 `_list_like(item.get(..., []))` 恒产出 tuple，序列化时也在字段列表内，不会缺键。
- `paper_arena_bridge.py:166` 的 `trading_days … or 0`——`research/paper_export.py:102`
  恒写 `trading_days`，只有本仓以外的老产物才会缺这个键。
- `OperationRecorder.tsx:102` 的 `holding?.cost ?? 0`——`portfolio._apply_close:396`
  在持仓存在时**用 `holding["cost"]` 覆盖**客户端传的 cost，所以那个 0 到不了账本；
  只有「补历史成交、本就没有持仓记录」的 legacy 路径会用到它（该路径另见下方已知问题）。
- `BacktestPage.tsx:77` 的 `Math.round(value).toLocaleString("zh-CN")` 没带 `timeZone`
  —— 那是**金额**（number，非日期），`timeZone` 对它无意义；带时区的日期格式化在
  `#645` 已全部补齐（全仓仅剩这一处无 `timeZone` 的 `toLocaleString`，即本项）。
- `SectorFlow.firms` 的 `_num()` 缺值兜 0——`firms` **全仓无消费方**
  （只在 `market-view.ts` 的 `toSectorRow` 透传一次，没有任何渲染/判断读它），
  不构成展示面。同结构里真正被展示的 `pct` 已在 `#648` 改成可空。

方法：grep `?? 0` 展示面 → 三重验证（类型可空 + 后端契约 + **可达性**）→ 复用现成 helper →
反向验证（临时破坏实现看测试红不红，破坏方式须「语义坏但类型合法」）→ 独立 PR。
已靠「可达性验证」拦下上列十余项伪缺口（每一项都追到了后端契约或消费端）。

🔴 已知未修（已登记 issue #641，勿重复审计）：
- ~~`backend/portfolio.py:_apply_close` 在「成本未知」（`cost == 0`，只有 legacy
  「补历史成交、无持仓记录」路径会走到）时，仍写入 `pnl = (price - 0) * shares`
  与 `pnl_pct = 0.0` ⇒ 平仓记录显示「收益率 0%」且 `realized_pnl` 被抬高整笔成交额。~~
  **已修（PR #642）**：写入侧 `pnl`/`pnl_pct` 在 `cost <= 0` 时写 `None`；读取侧
  `_with_known_cost` 把老账本里已写坏的记录一并纠正（不需要数据迁移）；
  `realized_pnl` 只累加有据成交，另出 `realized_pnl_unknown_count`；
  前端 `normalizeClosed` 改用 `asNullableNumber`（`asNumber` 会把 null 折回 0，白改）。

**`outputs/`（阶段产物）**
- 属本地工作区产物，`outputs/` 已被 `.gitignore`；不在仓库历史中，按需本地清理即可。

---

## 9. 给后续 agent 的两条硬规则

1. **吸收任何外部仓库前**，先读 `docs/research/repo_radar.md` + 对应 `*_absorption_*.md`，并核查 AQSP 是否**已实现**该能力（技能 `aqsp-external-repo-intake`）。
2. **别新建重复的阶段文档**；更新本文或对应 Active 文档。历史材料下沉 `docs/archive/`。
