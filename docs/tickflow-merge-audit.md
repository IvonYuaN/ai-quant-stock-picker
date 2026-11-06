# 阶段 A 复刻分支 × origin/main 合流对账审计

> **性质**：只读审计文档。本审计未执行任何 `git add` / `commit` / `push` / `checkout` / `merge` / `rebase` / `reset` / 部署 / 连服务器 / 全量 pytest / 启动服务或浏览器；唯一写入的文件就是本文件 `docs/tickflow-merge-audit.md`。
> **审计日期**：2026-11-06　**工作树**：`codex/tickflow-personal-operating-system`
> **审计起点 HEAD**：`233c45e318995aef52c32d736bfe93ae9ac872fa`（§1/§2 的差异统计与对账数据基于此时点）
> **当前 HEAD**：`922809b714d8c52fbbc2b41529acb3b3775cbb89` = `fix(data): 不完整日线不得当作完整市场日（P0 fail-closed）`，审计期间由队友提交，见 **§2.4**
> **origin/main**：`a5ea1774ab8010384873827fdcf0311534b2e91a`（#487–#515）
> **共同祖先**：`9106ac7a414c911e70e0419c70bd0434be215c6d` = `docs: record G4 production acceptance and remaining evidence gap (#470)`
> **计数**：`git rev-list --left-right --count HEAD...origin/main` → `7  37`（HEAD 侧 7 个、main 侧 37 个独有提交；team-lead 简述的「5 个」以本实证为准）
> **冲突集复核**：在 `922809b7` 上重跑 `git merge-tree --write-tree --name-only HEAD origin/main`，仍为 **33 个文件且清单逐项不变**（P0 提交只改了已在清单中的 `backend/app.py` / `frontend/src/lib/api.ts` 与 10 个新增文件），故 §2 对账表继续有效；仅 `app.py`(+39) / `api.ts`(+15) 的 HEAD 侧 numstat 增加，详见 §2.4。
> **规划依据**：`docs/architecture.md` §1 宪法、§7.2 Goal 与整合次序（行 683–825，阶段 A→B→C 次序在行 801–807）、§8/§9；`docs/tickflow-aqsp-integration.md` §5（行 200–209）；`AGENTS.md` §4/§5/§6.1；`docs/agent-operating-boundaries.md` §3/§7；`docs/data-refresh-readiness.md`（P0 数据完整性证据）。

> **审计基准说明**：本文所有 `git diff` / `git show` / `git log --find-object` 均基于三个 **commit 对象**（`HEAD`、`origin/main`、`9106ac7a`），不读取工作树文件，因此不受并行子任务在工作树产生的脏改影响。审计期间 `git status` 出现的 `backend/factor_*.py`、`backend/strategy_backtests.py` 等 `M` 文件为队友并行写入，非本审计改动，也不在本次对账范围内；本审计唯一写入文件为 `docs/tickflow-merge-audit.md`。

**本文所有结论可复现的实证命令**（均在仓库根执行，`BASE=9106ac7a`）：

```bash
git merge-base HEAD origin/main
git log --oneline --no-merges $BASE..HEAD
git log --oneline --no-merges $BASE..origin/main
git diff --stat $BASE HEAD
git diff --stat $BASE origin/main
git merge-tree --write-tree --name-only HEAD origin/main     # 冲突清单 + Auto-merging/CONFLICT 明细
git diff --numstat $BASE HEAD -- <file>
git diff --numstat $BASE origin/main -- <file>
git cat-file -e $BASE:<file>                                # 文件是否在 base 存在
git diff --numstat HEAD origin/main -- <file>               # 两侧版本差异
git show HEAD:<file> | grep -nE '^(def|class|async def|export)'   # 符号清单
git log --all --find-object=$(git rev-parse HEAD:<file>) --format='%h %s'  # blob 来源
git show <commit> | git patch-id --stable                   # 是否 cherry-pick
git diff -U0 $BASE HEAD -- backend/app.py | grep -oE '@app\.(get|post|put|delete|patch)\("[^"]+"'
```

---

## 0. 结论摘要

1. **两条线不是对称的独立开发**。`git patch-id` 证明 HEAD 侧 `5c76defb` 与 main 侧 `25badd83`(#471) 完全一致（`cb59be7cb661`）；`git log --find-object` 证明 HEAD 侧 6 个 add/add 冲突文件的 blob 精确出现在 main 线 #471/#477/#479/#488 历史中。**HEAD 分支 = main #470 + cherry-pick #471 旧快照 + 阶段 A 复刻大落库（`7caf6d57`）+ 监控指标目录（`233c45e3`）+ P0 数据完整性守卫（`922809b7`，审计期间追加，见 §2.4）；main = #470 → #515 的 37 个持续演进提交**。因此 main 侧对同一批个人决策模块是「更新、更完整的超集」，阶段 A 侧对这些文件的版本是「旧快照」。
2. **冲突 33 个文件**（7 个 add/add、26 个 content），另有 **14 个「两侧都改但 git 自动合并成功」的隐性语义冲突文件**。
3. **逐项建议统计**：保留 main **14** / 保留阶段 A **0** / 合并两者 **18** / 迁移后退役 **1**。
   - 「保留阶段 A = 0」是实质结论而非遗漏：HEAD 侧没有任何一个冲突文件的版本应整体覆盖 main——其改动要么来自 #471 旧快照（main 已超越），要么是需要叠加到 main 上的阶段 A 新增能力（合并），要么是需要迁移的旧契约（退役）。
   - 「退役 = 1」只统计文件级判定；`backend/app.py` 的 6 个旧端点、`frontend/src/lib/api.ts` 的旧候选观察客户端、`CandidateSection.tsx` 的本地观察面板等「文件内子项退役」已在建议列逐条写明，不重复计数。
4. **最关键 3 个风险**：① `/api/candidate-observations` 在两侧是**形状完全不兼容的两套 API**；② 阶段 A 按宪法**尚未封口**，合流不得借机删减 main 的个人决策能力；③ 合流后的**租户落盘状态**（两套候选观察存储）无自动迁移路径。
5. **P0 数据完整性守卫（team-lead 2026-11-06 追加，合流前必须处理）**：本机 `A股量化分析数据/astocks_raw.db` 全表 `MAX(trade_date)=20261102` 仅由 1 只票（`000001.SZ`）拉高——5,738 只票中 2,818 停在 20260930、1,061 停在 20260911、1,327 停在 20260529。阶段 A 侧 `src/aqsp/data/sqlite_db_source.py` 的 `get_latest_daily_date()` 原样返回该 MAX，10 个消费者会把它当「完整市场日」；审计期间 `922809b7` 已在阶段 A 侧改为横截面覆盖 fail-closed（最新日覆盖不达 `AQSP_MIN_MARKET_DAILY_COVERAGE` 默认 0.8 即返回 `None`），但 **main 侧 `check_data_freshness.py` / IC 诊断 / 双窗判决等仍以源库 `MAX(trade_date)` 为口径且无覆盖门槛**。合流后两套口径会在同一进程给出矛盾结论，必须统一，详见 **§2.4**。
6. **建议第一步**：① 先固化 P0 守卫——保留 `922809b7` 的 `daily_coverage.py` + `sqlite_db_source.py` fail-closed 改动、`/api/aqsp/data-coverage` 端点与前端卡片，把横截面覆盖门槛推广到 main 侧全部 `MAX(trade_date)` 消费者、统一「市场日」口径；② 再做 API 契约去重（定死以 main 的 `candidate_observation_api.py` 为准、退役 HEAD 侧 app.py 的 6 个同前缀端点与 4 个重复 `strategy-*` 端点）；③ 之后才动存储迁移与前端。

---

## 1. 两条线的真实差异

### 1.1 分叉与拓扑（实证）

- 共同祖先 `9106ac7a` 就是 main 的 #470；`git rev-list --count 9106ac7a..origin/main` = 37，即 main 从 #471 一路到 #515。
- HEAD 侧 7 个独有提交（`git log --oneline --no-merges 9106ac7a..HEAD`；起点 6 个 + 审计期间新增 1 个 P0 提交）：

  | 提交 | 说明 | 实证性质 |
  |---|---|---|
  | `5c76defb` | feat(positions): connect AI strategy conditions to persistent monitoring | **= main `25badd83`(#471) 的 cherry-pick**（patch-id `cb59be7cb661` 相同） |
  | `fe19a1da` | docs: map tickflow capabilities to aqsp roadmap | 阶段 A 文档映射 |
  | `b3418cef` | docs: map full tickflow integration into personal workflow | 阶段 A 文档映射 |
  | `d7f1f587` | feat(candidates): connect AI entry review to monitoring | 与 main `3f5adb07`(#474) 相似但 patch-id 不同（`26fce44d6106` vs `44015e0c9950`），为本线独立改写 |
  | `7caf6d57` | feat(tickflow): 落库阶段 A 完整复刻工作台与验收证据 | 阶段 A 主体：+29,962 行的大部分 |
  | `233c45e3` | feat(monitor): 把单一 RSI 观察扩为受限日线指标目录 | 阶段 A 监控补齐（审计起点 HEAD） |
  | `922809b7` | fix(data): 不完整日线不得当作完整市场日（P0 fail-closed） | 审计期间由队友提交：新增 `src/aqsp/data/daily_coverage.py` 覆盖门槛、`get_latest_daily_date()` 改 fail-closed、`/api/aqsp/data-coverage` 端点与 `/data` 页卡片；12 文件 +777/-12。见 **§2.4** |

- main 侧 37 个提交（`git log --oneline --no-merges 9106ac7a..origin/main`）按主题：#471–#480 持仓/候选策略证据与监控首段、#481–#490 研究链与行业依据、#491–#500 冻结技术证据/AI 前提逐项跟踪、#501–#510 同日权衡与六类决策页重排、#511–#515 模型拒绝分类/定性等待/来源时效收敛。
- **关键判定**：`git log --all --find-object=<HEAD blob>` 对 6 个 add/add 文件全部命中 main 历史：

  | 文件 | HEAD blob 在 main 历史中的引入提交 |
  |---|---|
  | `backend/position_strategy_evidence.py` | `5e0b5eb1`(#477)、`25badd83`(#471) |
  | `backend/position_strategy_watch.py` | `71903a97`(#479) |
  | `backend/tests/test_position_strategy_evidence.py` | `71903a97`(#479) |
  | `backend/tests/test_position_strategy_watch.py` | `71903a97`(#479) |
  | `frontend/src/lib/strategy-watch.ts` | `36f12a58`(#488) |
  | `frontend/src/lib/strategy-watch.test.ts` | `36f12a58`(#488) |

  唯一例外 `backend/candidate_discussion.py`：HEAD blob 仅由 `7caf6d57` 引入，main 历史中不存在 `run_candidate_discussion` / `_personal_context` / `_snapshot_id`（`git log origin/main -- ... | git show ... | grep` 无 HIT），是阶段 A 侧独立实现。

### 1.2 两侧各自新增的能力/页面/模块（实证：`git diff --name-status $BASE <ref> | grep '^A'`）

**HEAD（阶段 A 复刻）新增 46 个非测试文件** —— 主题是「TickFlow 量化工作台复刻」：

- 后端研究与回测：`factor_definitions.py`、`factor_trials.py`、`factor_validation.py`、`signal_definitions.py`、`signal_scans.py`、`strategy_definitions.py`、`strategy_scans.py`、`strategy_scan_worker.py`、`strategy_backtests.py`、`backtest_run_worker.py`、`backtest_runs.py`、`research_variant_decisions.py`、`decision_history.py`、`price_observation_rules.py`、`market_event_rules.py`、`abnormal_deviation.py`、`abnormal_history.py`、`auction_benchmark.py`、`stock_levels.py`、`stock_research.py`、`daily_data_refresh.py`(+worker)、`candidate_observations.py`、`candidate_observation_delivery.py`、`candidate_discussion.py`、`position_strategy_evidence.py`、`position_strategy_watch.py`。
- 前端页面：`AbnormalMovesPage.tsx`、`DataWorkbenchPage.tsx`、`MonitorWorkbenchPage.tsx`、`ResearchWorkbenchPage.tsx`(886 行)、`ReviewWorkbenchPage.tsx`、`StrategyWorkbenchPage.tsx`、`SignalLibraryPage.tsx`；个股面板 `StockKlinePanel` / `StockFinancialsPanel` / `StockLevelsPanel` / `StockResearchAnalysisPanel`；视图库 `research-workbench-view.ts`、`stock-*-view.ts`、`strategy-workbench-view.ts`、`strategy-watch.ts`。
- 核心包：`src/aqsp/research/strategy_evidence.py`、`strategy_expression.py`、`src/aqsp/data/daily_universe.py`、`scripts/data/backfill_adjustment_factors.py`。
- 基础设施：`frontend/src/router.tsx` 新增 11 条路由（`/screener` `/signals` `/watchlist` `/factors` `/backtest` `/regime` `/abnormal` `/indices` `/data` `/monitor` `/review`）；`backend/app.py` 新增约 70 个 `/api/aqsp/*` 路由；`docs/tickflow-aqsp-integration.md`（217 行证据映射）。
- P0 守卫（`922809b7` 追加，详见 §2.4）：`src/aqsp/data/daily_coverage.py`（只读覆盖摘要 + `market_date_is_usable()`）、`GET /api/aqsp/data-coverage`（`backend/app.py:3289`）、`frontend/src/lib/data-coverage-view.ts` 与 `/data` 页「日线完整性」卡片，以及 `tests/test_daily_coverage.py`、`tests/test_market_daily_coverage_gate.py`、`backend/tests/test_data_coverage_api.py`、`frontend/src/lib/data-coverage-view.test.ts` 四项回归。
- 规模（审计起点 `233c45e3`）：`199 files changed, 29962 insertions(+), 870 deletions(-)`；当前 HEAD（`922809b7`）：`206 files changed, 30727 insertions(+), 870 deletions(-)`（`git diff --stat 9106ac7a HEAD`）。

**main（个人决策工作流）新增 40 个非测试文件** —— 主题是「G5 决策闭环的证据、模型契约与页面重排」：

- 候选链：`candidate_observation.py`(615 行)、`candidate_observation_api.py`(120 行 APIRouter)、`candidate_monitor.py`、`candidate_technical_evidence.py`、`candidate_discussion.py`(453 行重写)、`model_contract.py`、`model_evidence_projection.py`、`model_source_time.py`、`industry_source_contract.py`。
- 持仓链：`position_price_path.py`、`position_strategy_evidence.py`(361 行)、`position_strategy_watch.py`(193 行)、`strategy_conditions.py`、`strategy_direction.py`、`strategy_market_source.py`、`strategy_review_context.py`。
- 前端组件/库：`CandidateObservationPanel.tsx`、`CandidateObservationList.tsx`、`CandidateConditionChecks.tsx`、`PositionSaleReview.tsx`、`PositionPricePathEvidence.tsx`、`ResearchValidationContext.tsx`、`StrategyReviewContextPanel.tsx`、`TodayStrategyBoard.tsx`、`DiscussionFailures.tsx`、`PageOutline.tsx`；`candidate-observation.ts`、`candidate-conditions.ts`、`research-validation-context.ts`、`discussion-failures.ts`、`market-load.ts`、`today-strategy.ts` 等。
- 运行脚本：`scripts/intraday_window.sh`、`scripts/maintenance/intraday_window.sh`、`src/aqsp/web/research_status.py`；`.env.example` 把 `AGNES_MODEL` 从 `agnes-2.0-flash` 改为 `agnes-3.0-flash`（`git diff $BASE origin/main -- .env.example`）。
- 规模：`188 files changed, 18989 insertions(+), 1054 deletions(-)`。

### 1.3 「不冲突但语义重复」清单（不属 33 个冲突文件，但属阶段 C 删并候选）

两侧各自新增、git 不会报冲突，但**是同一用户任务的两套实现**，合流后必须按 `docs/architecture.md` 行 794–799 与 `docs/tickflow-aqsp-integration.md` 行 106–111 先审计再删并：

| 用户任务 | 阶段 A 侧 | main 侧 | 备注 |
|---|---|---|---|
| 候选观察存储/离页监控 | `backend/candidate_observations.py`(514 行) + `candidate_observation_delivery.py` + `candidate_discussion.py` | `backend/candidate_observation.py`(615 行) + `candidate_observation_api.py` + `candidate_monitor.py` + `candidate_technical_evidence.py` | **身份不同**：HEAD 用 `observation_id`，main 用 `symbol`；见 §3 状态迁移 |
| 监控规则 | `backend/price_observation_rules.py`、`market_event_rules.py`、`signal_definitions.py` | `backend/strategy_conditions.py`、`strategy_direction.py`、`candidate_monitor.py` | 共用 `live_risk_service` 轮次，须防重复检查/重复通知 |
| 策略证据 | `src/aqsp/research/strategy_evidence.py`、`strategy_expression.py` | `backend/strategy_review_context.py`、`model_evidence_projection.py`、`position_price_path.py` | 证据身份/时点/价基需统一 |
| 个股研究证据面 | `StockResearchAnalysisPanel.tsx`(283 行)、`StockLevelsPanel`、`StockFinancialsPanel`、`StockKlinePanel` | `ResearchValidationContext.tsx`、`CandidateObservationPanel.tsx`、`PageOutline.tsx` | 同一 `/stock/:code` 页并存展示 |
| 工作台页面 | 7 个 `*WorkbenchPage.tsx` + `AbnormalMovesPage` | #512「重排六类决策页面」的既有页面 | 路由不同，入口重复 |
| 回测/因子任务状态 | `backtest_runs.py`、`strategy_backtests.py`、`strategy_scans.py` | `research_status.py`、gate 状态 | 任务身份/锁/进度需统一 |

---

## 2. 逐冲突文件对账表（33 个，全覆盖）

**判定口径**
- **保留 main**：该文件 main 侧是同一演进线的更新/超集，阶段 A 侧为 #471 旧快照；直接取 main 版本。
- **保留阶段 A**：阶段 A 侧版本整体更合适、应覆盖 main（本审计结论为 0 项）。
- **合并两者**：两侧各加了不同能力，必须都保留（或一方为基底补另一方）。
- **迁移后退役**：阶段 A 侧实现是旧契约，把其独有语义迁到 main 契约后删除阶段 A 侧实现。

「依据」列中的行号均为 `git show <ref>:<file> | grep -n` 或 `git diff` hunk 头实际输出；命令型依据为可直接复跑的命令。

### 2.1 表 A：7 个 add/add 冲突（两侧各自「新建」同名文件）

| # | 文件 | main 侧改了什么 | 阶段 A 侧改了什么 | 功能重复 | 建议 | 依据（路径:行号 / 命令） |
|---|---|---|---|---|---|---|
| 1 | `backend/candidate_discussion.py` | 453 行：`CandidateOpinion/CandidateRoute`、`parse`、`schema_for`、`run_discussion`、`stream_discussion`（多角色流式讨论），由 `candidate_observation_api.py:74/93` 调用 | 259 行：`CandidateAction/CandidateSummary/CandidateDiscussionError`、`_snapshot_id`、`_personal_context`、`run_candidate_discussion`（单次分析），由 HEAD `app.py:2674-2677` 的 `POST /api/candidate-observations/analyze` 调用 | **是**（同一「候选 AI 分析」任务的两代实现） | **迁移后退役** | `git log --all --find-object=$(git rev-parse HEAD:backend/candidate_discussion.py)` → 仅 `7caf6d57`；main 侧 `candidate_observation_api.py:13,74,93,105`；HEAD 侧 `app.py:2671,2694,2753,2768,2784,2798`；`docs/tickflow-aqsp-integration.md:109`。执行：保留 main 实现；把 HEAD 的 `_personal_context`/`_snapshot_id`/`save_decision`/`save_review` 逐项核对迁移到 `candidate_observation.py`/`candidate_monitor.py` 后，退役 HEAD 侧独立实现及 `/analyze`、`/decision`、`/{id}/review` 等端点；迁移前不删任何落盘决定/复盘历史 |
| 2 | `backend/position_strategy_evidence.py` | 361 行：在 HEAD 全部符号外多出 `_sector_fact`、`load_cached_strategy_sectors`、`enrich_strategy_context`（共享行业证据缓存/增强） | 215 行：`StrategyEvidenceLimits`、`load_strategy_context`、`refresh_strategy_sources` 等 | **是**（main 是超集） | **保留 main** | `git diff --numstat HEAD origin/main` = `172/26`；`git log origin/main --find-object=e08ceb313a80` → `5e0b5eb1`(#477)、`25badd83`(#471)；符号清单对比 |
| 3 | `backend/position_strategy_watch.py` | 193 行：`StrategyWatchInput`、`_discussion`、`_validate_result`、`adopt_strategy_watch`、`stop_strategy_watch`，校验更严 | 155 行：同名同序符号 | **是**（main 超集） | **保留 main** | `git diff --numstat HEAD origin/main` = `53/15`；`git log origin/main --find-object=2c38d4ec40d5` → `71903a97`(#479) |
| 4 | `backend/tests/test_position_strategy_evidence.py` | 441 行：含 HEAD 全部用例 + `monitor_projection_matches_read_projection`、completed windows、price path、volume 契约等 | 253 行：15 个用例 | **是**（main 超集） | **保留 main** | `git diff --numstat HEAD origin/main` = `191/3`；`git log origin/main --find-object=bc2fcb7a76e4` → `71903a97`(#479) |
| 5 | `backend/tests/test_position_strategy_watch.py` | 335 行：含 HEAD 全部 11 个用例 + 12 个（position review 对比、legacy 身份、source change 等） | 175 行：11 个用例 | **是**（main 超集） | **保留 main** | `git diff --numstat HEAD origin/main` = `160/0`；`git log origin/main --find-object=9b9a1bdd9e25` → `71903a97`(#479) |
| 6 | `frontend/src/lib/strategy-watch.ts` | 128 行：含 HEAD 6 个导出 + `directionWatchAction`、`directionStrategyWatch`、`strategyDirectionKey`、`discussionSynthesisNotice`；`strategyWatchIssue`/`strategyWatchInput` 的 `comparator` 增加 `null` | 69 行：`WATCH_COMPARATORS`、`discussionSourceKey`、`discussionStrategyCurrent`、`strategyWatchIssue`、`strategyWatchInput`、`strategyWatchState` | **是**（main 超集，但**改了函数签名**） | **保留 main**（同步迁移调用方签名） | `git diff --numstat HEAD origin/main` = `68/9`；`git log origin/main --find-object=c9f6f438cb0a` → `36f12a58`(#488)。注意：合流后 `CandidateSection.tsx`/`PositionActionPlan.tsx` 等旧调用点须改成 `comparator: ... \| null` 否则 TS 报错 |
| 7 | `frontend/src/lib/strategy-watch.test.ts` | 114 行：`strategyWatchContract` + `strategyWatchTransportContract` 更完整 | 88 行：同两个契约常量，断言更少 | **是**（main 超集） | **保留 main** | `git diff --numstat HEAD origin/main` = `29/3`；`git log origin/main --find-object=3c54209ffeac` → `36f12a58`(#488) |

### 2.2 表 B：26 个 content 冲突（base 中已存在，两侧都改）

> ⚠️ **P0 交叉引用**：#8 `backend/app.py`、#10 `backend/live_risk_service.py`、#24 `frontend/src/lib/api.ts` 同时承载 §2.4 的数据完整性守卫改动（`/api/aqsp/data-coverage` 端点、`get_latest_daily_date()` 的 `None` 处理、`dataCoverage` 客户端）。按本表取值时**必须一并保留这些 P0 改动**，详见 §2.4。

| # | 文件 | main 侧改了什么 | 阶段 A 侧改了什么 | 功能重复 | 建议 | 依据（路径:行号 / 命令） |
|---|---|---|---|---|---|---|
| 8 | `backend/app.py` | +74/0：`from candidate_observation_api import router`、`app.include_router(...)`；新增 `POST /api/positions/{id}/strategy-evidence`、`/sale-review`、`/strategy-watch`、`/strategy-watch/stop` 及 3 个请求模型（`StrategyWatchIn` 等） | +1559/30：约 70 个 `/api/aqsp/*` 阶段 A 路由（策略定义/扫描、因子、信号、回测、数据源、监控指标目录、个股研究、决策历史）；6 个 `/api/candidate-observations*` 端点；4 个与 main **同名**的 `/api/positions/{id}/strategy-*` 端点（旧快照） | **部分，且最危险**：两套 `/api/candidate-observations` 与 4 个同 URL 端点 | **合并两者**（含定点退役） | main diff hunk `@@ -52,6 +52,7` / `@@ -131,6 +132,7` / `@@ -948,6 +950,19` / `@@ -961,6 +976,24` / `@@ -1013,6 +1046,47`；`git diff -U0 $BASE HEAD -- backend/app.py \| grep -oE '@app\....'` 得 ~70 条；HEAD `app.py:2671,2694,2753,2768,2784,2798`；main `candidate_observation_api.py:13`。执行：保留 main 的 include_router + 4 端点 + sale-review；保留阶段 A 的 ~70 个 `/api/aqsp/*`；**退役** HEAD 的 6 个 `/api/candidate-observations*` 端点与 4 个重复 `strategy-*` 端点定义（否则同 URL 双注册，FastAPI 后注册者覆盖且行为不可控） |
| 9 | `backend/aqsp_bridge.py` | +74/6：新增 `_parse_technical_basis`（冻结技术依据解析，对应 `AqspTechnicalBasis`） | +20/0：阶段 A 快照字段（无新增 def） | 否（互补） | **合并两者** | 符号清单：main 仅 `_parse_technical_basis`；`frontend/src/types/aqsp.ts:12-23`（main）；`src/aqsp/web/home_snapshot.py` 新增 `HomeSnapshotTechnicalBasis` |
| 10 | `backend/live_risk_service.py` | +127/19：`_tenant_folders`、`_collect_strategy_codes`、`_refresh_shared_strategy_sources`（共享大盘/行业来源刷新） | +459/1：`_indicator_snapshots`、`_signal_snapshots`、`fail_all`/`block`（阶段 A 日线指标/信号监控） | 否（互补） | **合并两者** | 两侧新增符号清单；注意**共用同一 monitor 轮次**，合并后须保证每租户每轮只检查一次、通知去重沿用既有 `alert_events` 语义（见 `docs/tickflow-aqsp-integration.md:148`） |
| 11 | `backend/market.py` | +77/47：`breadth_label`、`_valid_breadth`、`_breadth_date`、`sanitize_market_overview`、`_sentiment`（市场宽度来源/时效校验） | +103/6：`ShortTermEmotionSnapshot`、`_pool_stock_rows`、`get_short_term_emotion_snapshot`（涨停/炸板/跌停三池） | 否（互补） | **合并两者** | 两侧新增符号清单；HEAD 的三池是 `/abnormal` 数据源，main 的宽度校验是决策页消费门禁，缺一不可 |
| 12 | `backend/position_context_source.py` | +79/18：`PositionContextSourceConfig(IndustrySourceLimits)`、`_parse_industries`（来源契约与解析），`fetch_position_sectors` 签名增强 | +48/0：`_combine_rows`、`fetch_position_sectors`（#471 旧快照） | **是**（main 超集，同名函数符号完全一致） | **保留 main** | 符号清单对比：`_combine_rows`/`fetch_position_sectors` 两侧同名；main 多出 config/parse 类 |
| 13 | `backend/position_discussion.py` | +199/45：`model_review_context`、`ModelHTTPError`、`upstream_failure`、`_model_failure`、`_synthesis_inputs`、`call_model(..., system)`（模型失败分类/汇总契约） | +9/9：`call_model` 签名微调（#471 旧快照） | **是**（main 超集） | **保留 main** | 两侧新增符号清单；main 对应 #507/#515「模型拒绝分类」 |
| 14 | `backend/position_followup.py` | +176/5：`condition_facts`、`watch_contract`、`strategy_direction_progress`、`strategy_condition_checks`（已采用条件逐项跟进） | +30/3：旧快照微调 | **是**（main 超集） | **保留 main** | 两侧新增符号清单 |
| 15 | `backend/position_plan_service.py` | +92/10：`refresh_strategy_evidence`（新版）+ `confirm_position_sale_review` | +32/3：`refresh_strategy_evidence`（#471 旧版） | **是**（main 超集） | **保留 main** | 两侧新增符号清单；main `app.py` 的 `/sale-review` 端点依赖 `confirm_position_sale_review` |
| 16 | `backend/tests/test_position_action_plan.py` | +19/9：含 HEAD 的 `test_action_plan_joint_weakness_expires_when_quote_or_sector_recovers` + 新增 `test_action_plan_requires_sale_review_when_holding_saved_again_without_scenario` | +10/7：同上旧快照用例 | **是**（main 超集） | **保留 main** | 两侧新增测试函数清单 |
| 17 | `docs/architecture.md` | +237/2：`#### 盘中扫描与日内候选身份分离`、G5 标题改写、`### 2026-10-29 信息架构重排补充`、`### 2026-10-30 ...`（#510 等 7 个交付段） | +95/2：`#### G5 当前主线`、`### TickFlow 外部系统研究与 AQSP 整合方向（2026-10-30）`、`#### 完整产品面映射`、`#### 当前删并清单与证据门槛`（含行 801–807 的 A→B→C 次序、行 809–823 阶段 A 验收记录） | **部分**（G5 标题两侧都加） | **合并两者**（需审查） | 两侧新增标题清单；现有工作树 `docs/architecture.md:10-34`(§1)、`:683-825`(§7.2/整合次序)、`:829-858`(§8/§9)。执行：以 main 的最新交付状态为正文，完整保留阶段 A 的 TickFlow 整合方向、A→B→C 次序与阶段 A 未封口记录；**不得改 §1 宪法**（`AGENTS.md:156,181`）；本文件改动按 `AGENTS.md:916` 用 `docs:` 前缀并交审查 agent 优先审查 |
| 18 | `docs/position-action-plans.md` | +165/0：`## D3 模型请求去重与输出合同`、`## D4 真实接口验收边界`、`### #471 发布回执`、`### #474/#475/#476/#477/#479/#480 回执`、`### #506 发布回执` 等 | +31/0：`## G5 策略分析、人工采用与持续监控`、`## TickFlow 研究回执与整合原则`、`## 全量吸收与个人操作系统路线` | 否（都是各自回执） | **合并两者** | 两侧新增标题清单；按时间顺序保留**全部**回执，不删任一方发布证据 |
| 19 | `frontend/src/components/aqsp/AlertInbox.tsx` | 1/1：第 112 行空持仓文案改为「当前没有持仓需要检查…可以先从候选分析参与条件，保存观察后由后…」 | 3/3：第 29 行 `threshold_at != null` 空值守卫；第 103 行把 `price_observation`/`market_event` 排除出历史；第 112 行旧版空持仓文案 | **是**（第 112 行同处） | **合并两者** | HEAD diff hunk `@@ -29,7 +29,7` / `@@ -103,7 +103,7` / `@@ -112,7 +112,7`；main diff hunk `@@ -112,7 +112,7`。执行：保留 HEAD 的 null 守卫与事件类型排除，采用 main 的文案 |
| 20 | `frontend/src/components/aqsp/PositionActionPlan.tsx` | +49/18：含 `calculationDraftRequiresReview`（同签名）+ 更多阅读层级改动 | +45/12：`calculationDraftRequiresReview`（同签名，#471 旧快照） | **是**（main 超集） | **保留 main** | 两侧新增符号清单（同名函数签名完全一致） |
| 21 | `frontend/src/components/aqsp/PositionDiscussionPanel.tsx` | +75/27：组件同签名，内部增强 | +63/26：组件同签名（#471 旧快照） | **是**（main 超集） | **保留 main** | 两侧新增组件签名清单 |
| 22 | `frontend/src/components/aqsp/sections/CandidateSection.tsx` | +122/50：`historical`、`generatedAt`、`ownershipKnown`、`ownedHoldings` 等字段与契约重构 | +290/3：`candidateError`、本地 `function CandidateObservationPanel(...)`（阶段 A 候选观察面板） | **部分** | **合并两者**（含局部退役） | 两侧新增符号清单；注意 HEAD 的本地 `CandidateObservationPanel` 与 main 独立组件 `frontend/src/components/aqsp/CandidateObservationPanel.tsx` 重复 → 按阶段 C 迁移到 main 组件后退役本地副本；保留 main 字段契约 |
| 23 | `frontend/src/index.css` | +253/23：`.aq-page-outline*`、`.aq-evidence-fold`、`.aq-discussion-failures` 等阅读层级样式 | +391/5：`.aq-strategy-*`、`.aq-strategy-watch-price` 等阶段 A 工作台样式 | 否（类名互不重叠） | **合并两者** | 两侧新增选择器抽样（`git diff -U0 ... \| grep '^\+\.'`）；两组样式整体追加即可 |
| 24 | `frontend/src/lib/api.ts` | +169/15：个人决策 API（候选观察契约、`technical_basis` 等） | +1425/5：约 70 个 `/api/aqsp/*` 阶段 A 客户端 | **部分**（候选观察客户端） | **合并两者**（含局部退役） | `git diff --numstat`；两侧新增函数清单（`git diff -U0 ... \| grep '^+  [a-zA-Z].*('`）。执行：保留两组函数；候选观察相关以 main 契约为准，退役 HEAD 的 `/analyze`、`/decision`、`/{id}/review` 客户端调用，避免指向已退役端点 |
| 25 | `frontend/src/lib/catalyst-sector-view.test.ts` | +2/1：`streamMountsCatalystIndependentlyOfNewsAvailability` 断言 | +19/5：叙事聚合、`affected_symbols` 链接/非法代码、LEGACY_ROUTES/NAV_LINES 断言 | 否（互补） | **合并两者** | 两侧新增断言清单；两组断言都保留 |
| 26 | `frontend/src/pages/InfoStreamPage.tsx` | +61/42：板块催化独立于新闻可用性挂载、页面重排 | +42/1：`InfoStreamNarrativeSection`（叙事主线聚合） | 否（互补） | **合并两者** | 两侧新增符号清单；main 的挂载改动与 HEAD 的叙事组件不同区域 |
| 27 | `frontend/src/pages/MarketPage.tsx` | +66/34：渐进加载/市场方向（对应 `market-load.ts`） | +30/63：`EmotionBlock`、`MarketPage({ mode })` 重构，供 `/regime`、`/indices` 复用 | 否（互补） | **合并两者** | 两侧新增符号清单；**注意** `router.tsx` 的 `/regime`、`/indices` 依赖 HEAD 的 `mode` 参数，合并时不得丢签名 |
| 28 | `frontend/src/pages/StockResearchPage.tsx` | +24/13：`PageOutline`、`CandidateObservationPanel`、`ResearchValidationContext`、`useWorkspaceSnapshot`、所选日研究摘要分区 | +17/1：`StockKlinePanel`/`StockFinancialsPanel`/`StockLevelsPanel`/`StockResearchAnalysisPanel` 挂载、`currentResearch` | 否（互补） | **合并两者** | HEAD `app.py` 无对应；HEAD diff `@@ -50,6 +50,10`（4 个 import）、`@@ -198,6 +203,10`；main diff `@@ -2,6 +2,7`、`@@ -42,13 +42,15`、`@@ -175,30 +177,40` |
| 29 | `frontend/src/pages/my/PositionPlanPage.tsx` | +37/11：`PageOutline`、`PositionSaleReview`、`PositionPricePathEvidence`、`LoadingState/SectionHeader/StatePanel` + `refreshEvidence` | +18/7：`refreshEvidence` + 返回链接/按钮（#471 旧快照） | **是**（main 超集） | **保留 main** | 两侧新增内容清单；`refreshEvidence` 两侧同名同逻辑，main 另加 3 个组件 |
| 30 | `frontend/src/types/aqsp.ts` | +25/0：`AqspTechnicalBasis` 接口、`AqspCandidate.technical_basis`/`coverage_note`、`AqspVariantSuite` 刷新事实注释 | +6/0：`AqspVariantSuite` 的 `refresh_status`/`refresh_freshness`/`refresh_generated_at`/`refresh_message`/`profiles_staged`/`profiles_total` | **部分**（同在 `AqspVariantSuite` 区域） | **合并两者** | main diff `@@ -12,10 +12,23`、`@@ -132,6 +145,13`；HEAD diff `@@ -132,6 +132,12`。执行：`AqspTechnicalBasis` 全取 main；`AqspVariantSuite` 两批字段都保留 |
| 31 | `scripts/analysis/write_home_snapshot.py` | +69/21：`_candidate_technical_basis`、`_snapshot_candidate`、`_variant_refresh_facts`（冻结技术依据写入快照） | +70/15：`_variant_refresh_status`、`_variant_refresh_status_error`（阶段 A 变体刷新状态） | 否（互补） | **合并两者** | 两侧新增函数清单；**必须与 #9/#33 的 `HomeSnapshotTechnicalBasis`/`AqspTechnicalBasis` 契约同步**，否则写入快照与前端读取字段不一致 |
| 32 | `src/aqsp/monitor/live_positions.py` | +3/3：小幅修正 | +19/3：`_has_monitor_store`（阶段 A 监控 store 存在性判定） | 否 | **合并两者** | 两侧新增符号清单；HEAD 独有 `_has_monitor_store`，保留之并采纳 main 的 3 行修正（需核对是否同处） |
| 33 | `src/aqsp/web/home_snapshot.py` | +216/14：`HomeSnapshotTechnicalFact`、`HomeSnapshotTechnicalBasis`、`technical_basis_from_dict`（冻结技术证据读时校验） | +33/0：阶段 A 快照字段（无新增顶层符号） | 否（互补） | **合并两者** | 两侧新增符号清单；main 为主，保留 HEAD 的 33 行字段；与 `docs/tickflow-aqsp-integration.md:58` 的「冻结技术证据」一致 |

### 2.3 表 C：14 个「两侧都改但 AUTO-MERGE 成功」的隐性冲突（必须回归验证）

来源：`git merge-tree --write-tree --name-only HEAD origin/main | awk '/^Auto-merging /{am[$2]=1} /^CONFLICT .*Merge conflict in /{cf[$NF]=1} END{for(f in am) if(!(f in cf)) print f}' | sort`

| 文件 | 风险点 | 建议 |
|---|---|---|
| `frontend/src/router.tsx` | **最高危**：HEAD 加 11 条路由与 8 个 lazy 页；main 把 `/my/holdings` 的 `renderRiskSettings` 从 `(code) => ...` 改为 `(code, expanded) => <RiskRuleEditor code expanded={expanded}/>`。auto-merge 会保留 HEAD 的**旧单参调用**，而 main 的 `HoldingsPage.tsx`(+58) 期望双参 → `expanded` 恒为 undefined，行为静默退化 | 手工核对：`git diff $BASE HEAD -- frontend/src/router.tsx` 与 `$BASE origin/main`；采用 main 的 `renderRiskSettings` 双参调用，同时保留 HEAD 的 11 条新路由 |
| `backend/astock.py` | 两侧分别改数据源层（HEAD +119、main +145） | 逐 hunk 核对，重点查是否同一取数函数被两侧重写 |
| `backend/position_route_review.py` | 两侧小改 | 核对旧路由兼容语义 |
| `src/aqsp/cli.py` | HEAD +101、main +154，两侧都加子命令 | 核对子命令名是否撞名、参数解析是否互相覆盖 |
| `frontend/src/components/aqsp/TodayWorkspace.tsx` | HEAD +2、main +61 | main 的六类决策页重排为主，核对 HEAD 的 2 行是否被覆盖 |
| `frontend/src/lib/market-view.ts` | HEAD +3、main +58 | main 的渐进加载为主 |
| `backend/tests/test_aqsp_api.py`、`test_position_discussion.py`、`test_position_followup.py` | 两侧都改断言 | 合并后定向跑这些测试 |
| `frontend/src/lib/position-workspace.test.ts` | 两侧都改 | 定向跑 |
| `tests/test_aqsp_bridge.py`、`test_home_snapshot.py`、`test_live_position_monitor.py`、`test_write_home_snapshot.py` | 与 #9/#31/#32/#33 契约直接相关 | 合并后**必须**定向跑，验证快照字段与桥接解析一致 |

### 2.4 P0 数据完整性守卫：合流前必须处理的正确性守卫

**事实与证据**（`docs/data-refresh-readiness.md`，2026-11-06 15:20 只读诊断；本审计用 `git show` / `git grep` / `git diff --numstat` 复核）：

- 本机 `A股量化分析数据/astocks_raw.db`（2.4 GB）实测 `daily_qfq` 14,151,621 行 / 5,738 只票；全表 `MAX(trade_date)=20261102`，但逐票 `MAX(trade_date)` 分布为：20261102 **1 只**（`000001.SZ`）、20260930 **2,818 只**、20260911 **1,061 只**、20260529 **1,327 只**、更早 531 只。2026-10-01 之后每个交易日恰好 1 行且全部是 `000001.SZ`。即全表 MAX 被单票拉高，**逐票覆盖根本没刷新**。
- `src/aqsp/data/sqlite_db_source.py` 的 `get_latest_daily_date()` 原实现为 `SELECT MAX(trade_date) FROM daily_qfq WHERE ts_code NOT IN (<指数代码>)`（排除指数，但**不要求横截面覆盖**），单只票即可把市场级 as-of 报成 20261102；`backend/live_risk_service.py:77` 等消费者会把它当完整市场日。这属于 **PIT 正确性缺陷**，不是单纯「数据陈旧」展示问题。
- 审计期间 `922809b7` 已在**阶段 A 侧**改为 fail-closed：只有最新日横截面覆盖达到 `AQSP_MIN_MARKET_DAILY_COVERAGE`（默认 0.8，必须显式设环境变量才能放宽）才返回该日，否则返回 `None` 并写 warning（覆盖数 / 总数 / 哨兵标的）。新增 `src/aqsp/data/daily_coverage.py` 的 `summarize_daily_coverage()` 与 `market_date_is_usable()`；真实库实测返回 `None`（最大日 20261102、覆盖 1/5728 ≈ 0.0175%）。`git show HEAD:src/aqsp/data/sqlite_db_source.py | sed -n '259,284p'` 可见当前实现。
- 可复现命令：`git show 922809b7 -- src/aqsp/data/sqlite_db_source.py src/aqsp/data/daily_coverage.py`；`git grep -n 'get_latest_daily_date' HEAD -- '*.py'`；`git grep -n 'MAX(trade_date)' origin/main -- '*.py'`。

**两条线各自受影响的文件**（`git diff --numstat 9106ac7a <ref> -- <file>`：`MAIN=` 空表示 main 侧相对 base 未改该文件）

| 文件 | 线 / 相对 base 改动 | 在 P0 中的角色 | 合流动作 |
|---|---|---|---|
| `src/aqsp/data/sqlite_db_source.py` | 阶段 A `264/16`；main **未改** | 缺陷源头 `get_latest_daily_date()`；`922809b7` 在此加 `min_market_daily_coverage_ratio()`、import `daily_coverage`、fail-closed 返回 `None` | **必须保留阶段 A 版**（该文件不在 33 个冲突文件内，合流方向 A 下自动保留；方向 B 需显式带上） |
| `src/aqsp/data/daily_coverage.py` | 阶段 A `132/0`；main **未改** | 只读覆盖摘要 + `market_date_is_usable()` 门槛 | **必须保留阶段 A 版** |
| `backend/app.py`（冲突 #8） | 阶段 A `1598/30`（含 P0 +39）；main `74/0` | `GET /api/aqsp/data-coverage` 端点（`app.py:3289`）调用覆盖判定 | 合并时**保留该端点**，不与 main 的 4 个持仓端点冲突 |
| `frontend/src/lib/api.ts`（冲突 #24） | 阶段 A `1440/5`（含 P0 +15）；main `169/15` | `DataCoveragePayload`（`api.ts:68`）、`dataCoverage()`（`api.ts:2100`） | 合并时**保留**这两个新增导出 |
| `frontend/src/lib/data-coverage-view.ts`、`data-coverage-view.test.ts`、`frontend/src/pages/DataWorkbenchPage.tsx` | 阶段 A 新增；main 无 | 完整性判定与 `/data` 页「日线完整性」卡片，不完整时绝不渲染成可用 | **保留阶段 A 版**（main 无同名文件，不冲突） |
| `backend/tests/test_data_coverage_api.py`、`tests/test_daily_coverage.py`、`tests/test_market_daily_coverage_gate.py` | 阶段 A 新增；main 无 | 守卫回归（真实库 `None`、端点、前端契约） | **保留并纳入合流后定向验证** |
| `backend/live_risk_service.py`（冲突 #10） | 阶段 A `459/1`；main `127/19` | **交叉点**：阶段 A 侧在 `:77`、`:198` 消费 `get_latest_daily_date()`，`:207` 用 `get_latest_daily_dates()`；`:77-82` 的 `None` 处理是 fail-closed 生效的关键 | 按对账表 #10「合并两者」处理时**必须保证 `:77`/`:198` 的 `None` 分支与阶段 A 指标监控消费点都保留**，否则守卫形同虚设 |
| `backend/abnormal_deviation.py:380`、`abnormal_history.py:38`、`auction_benchmark.py:80`、`daily_data_refresh.py:148`、`signal_scans.py:203`、`strategy_backtests.py:144`、`strategy_scans.py:178` | 阶段 A 新增；main 无 | 其余 7 个 `get_latest_daily_date()` 生产调用点（连同上两项共 8 文件 / 9 处；`team-lead` 文档计数为 10 个消费者） | 合流后随阶段 A 版保留，**逐个确认 `None` 分支仍存在** |
| `src/aqsp/data/source.py:134` | 阶段 A 新增接口；main 无 | `DataSource.get_latest_daily_date()` 抽象签名 | 保留 |
| `scripts/data/check_data_freshness.py` | **main 侧**（`origin/main`，阶段 A 无 `get_latest_daily_date` 引用） | 源库 `MAX(trade_date)` 与最后收盘交易日比对，是 main 侧「数据自身最新日」权威口径（`backend/app.py:2703,2764` 明确写「与 `check_data_freshness.py` 同口径」） | **未加覆盖门槛**：合流后须接入 `market_date_is_usable()`，否则与阶段 A 侧 fail-closed 结论矛盾 |
| `backend/app.py:2703,2760-2765`（main 侧） | **main 侧** | 双窗判决 `as_of_b` 口径 + `DUAL_MAX_LAG_TRADING_DAYS` 护栏，只检查 MAX 落后天数、不检查横截面覆盖 | 同上，须补覆盖门槛 |
| `scripts/analysis/ic_diagnosis.py:47`、`scripts/analysis/ic_dual_verdict.py` | **main 侧** | IC 诊断 as-of / 双窗 B 窗右端由库 `MAX(trade_date)` 截断 | 同上；`ic_diagnosis.py:47` 是裸 `SELECT MAX(trade_date) FROM daily_qfq` |
| `scripts/data/backfill_akshare.py:126,137`、`backfill_index_to_sqlite.py:139`、`update_sqlite_daily.py:594-601`、`import_tdx_vipdoc_to_sqlite.py:89`、`refresh_sqlite_batch.py:47` | **main 侧** | 回补 / 导入 / 刷新链里逐票或全表 `MAX(trade_date)` 判定 | 逐项核对是否把「单票 MAX」当成全市场就绪；`refresh_sqlite_batch.py` 的 `eligible_coverage_pct` 是正确方向但需与守卫统一 |
| `backend/aqsp_bridge.py:60,87,363-383,669-688,784`（冲突 #9） | **main 侧** | snapshot freshness（`is_stale`、`_require_current_snapshot_fresh`、`_snapshot_component_freshness`），基于时间戳而非横截面覆盖 | 与阶段 A 侧市场日守卫是两套独立维度，合流后须明确区分「快照新鲜」与「行情横截面完整」，不得互相替代 |
| `backend/market.py`（冲突 #11）、`docs/architecture.md`（冲突 #17） | 两侧都改 | main 的 `_valid_breadth`/`sanitize_market_overview` 与阶段 A 的 `ShortTermEmotionSnapshot` 都隐含市场日前提 | 统一口径后回归；`architecture.md` 合并时把本 P0 纳入阶段 A 验收记录 |

**合流判定（P0，必须在任何 API/存储联调之前处理）**：

1. **守卫只在阶段 A 侧存在**。`git grep -n 'get_latest_daily_date' origin/main -- '*.py'` **零命中**，即 main 侧根本不调用该方法；合流方向 A（把 main 合入阶段 A 分支）下 `sqlite_db_source.py` / `daily_coverage.py` 不在 33 个冲突文件内，会**自动保留**。若改走方向 B（阶段 A cherry-pick 到 main），必须显式把这两个文件与 4 个回归测试一起带上，否则守卫丢失。
2. **main 侧存在同类缺陷且无守卫**。main 用 `check_data_freshness.py` / IC 诊断 / 双窗判决这一整套「源库 `MAX(trade_date)`」口径（`git grep -n 'MAX(trade_date)' origin/main -- '*.py'` 命中 10+ 处），同样会被单票拉高。**合流后同一进程会出现两套矛盾结论**：阶段 A 侧 `get_latest_daily_date()` 返回 `None`（fail-closed），main 侧仍可能判 20261102 为「数据最新日」。因此必须把 `daily_coverage.py` 的横截面覆盖门槛推广到 main 侧全部 `MAX(trade_date)` 消费者，并统一「市场日 / 数据就绪」的单一事实源，而不是只保留阶段 A 侧补丁。
3. **冲突文件是交叉点**：#8 `backend/app.py`、#24 `frontend/src/lib/api.ts` 承载 P0 新端点/客户端，#10 `backend/live_risk_service.py` 承载 `None` 处理，#11 `backend/market.py`、#9 `backend/aqsp_bridge.py` 承载 main 侧等价判定。**按 §2 对账表解决这些冲突时（#10 的建议是「合并两者」），不得因纳入 main 侧改动而丢掉 P0 改动**——main 版不含 `:77/:198` 的阶段 A 指标监控消费点，合并结果必须同时含两组能力且 `None` 分支完整。
4. **P0 不等于数据已修复**。`docs/data-refresh-readiness.md` §6.4/§7 明确仍未执行且需仓主授权：runner 全量 universe 回补并核对 `eligible_coverage_pct=1`、PIT 复权因子覆盖核验、查明并清理 `000001.SZ` 哨兵行写入者、数据齐备后重跑阶段 A 真实数据验收。守卫只是让系统在数据不完整时 fail-closed，**不构成任何策略有效性或行情就绪证据**。
5. **归属红线**：本项落在 §4「未来数据 / PIT 正确性」与 `docs/architecture.md` §1.3.1（「任何一个数字都要回答在 t 时刻能不能拿到」）；裸 `MAX(trade_date)` 把单票当日数据当作全市场 as-of，等价于用不完整横截面构造 as-of，属于必须拦截的未来数据/完整性缺陷。

---

## 3. 依赖与合并顺序

### 3.1 必须先做（阻塞后续一切联调，严格串行）

> **P0 —— 先于以下 1–4，且必须最先固化（team-lead 2026-11-06 追加，详见 §2.4）**：数据完整性守卫。先保留 `922809b7` 的 `src/aqsp/data/daily_coverage.py` + `src/aqsp/data/sqlite_db_source.py` fail-closed 改动、`GET /api/aqsp/data-coverage` 端点与 `/data` 页卡片；再把横截面覆盖门槛推广到 main 侧全部 `MAX(trade_date)` 消费者（`scripts/data/check_data_freshness.py`、`scripts/analysis/ic_diagnosis.py:47`、`ic_dual_verdict.py`、`backend/app.py:2703,2764`、回补/导入脚本），统一「市场日 = 通过覆盖门槛的日期」的**单一事实源**。此步未完成前，任何 as-of / 新鲜度 / 回测前置校验都可能建立在「单票即全市场」的错误前提上；且它与第 1 步并列阻塞——解决 #8/#10/#24 冲突时**不得**因「保留 main」而丢掉 P0 改动（尤其 #10 的 `None` 处理）。验证：`python -m pytest -q tests/test_daily_coverage.py tests/test_market_daily_coverage_gate.py backend/tests/test_data_coverage_api.py`，加隔离真实 API 冒烟 `COVERAGE_GATE=PASS`；真实库 `get_latest_daily_date()` 应返回 `None`。

1. **API schema / 路由去重（次优先，与 P0 并列阻塞）** —— 决定唯一契约后再写任何调用方。
   - `/api/candidate-observations`：main 用 `APIRouter(prefix='/api/candidate-observations')`（`candidate_observation_api.py:13`），子路径按 `/{symbol}/discussion`、`/{symbol}/adopt`、`/{symbol}/stop`；阶段 A 用 `app.py` 内嵌的 `/analyze`、`/decision`、`/{observation_id}/stop`、`/{observation_id}/review`。**两者形状不兼容且同前缀**，同时注册会互相吞路径（尤其 `/{symbol}/stop` vs `/{observation_id}/stop`）。
   - 4 个 `/api/positions/{id}/strategy-evidence|strategy-watch|strategy-watch/stop` 在两侧 app.py 各定义一次 → 必须只留 main 版（其 `StrategyWatchIn` 请求模型与 main 的 `position_strategy_watch.py` 配套）。
   - 决策：**以 main 契约为准**，退役 HEAD 侧 6 个候选观察端点与 4 个重复端点；阶段 A 的 ~70 个 `/api/aqsp/*` 路由与 main 无命名冲突，全部保留。
2. **数据契约对齐**（紧随 1，串行）：`AqspTechnicalBasis`（main `frontend/src/types/aqsp.ts:12-23`）↔ `HomeSnapshotTechnicalBasis`（main `src/aqsp/web/home_snapshot.py`）↔ `_parse_technical_basis`（main `backend/aqsp_bridge.py`）↔ 写入侧（`write_home_snapshot.py` 两侧函数）四处字段名/精度/来源时点必须一致；`strategy-watch.ts` 的 `comparator: ... | null` 签名变更须同步所有调用点。
3. **状态迁移**（依赖 1、2）：阶段 A 的 `backend/candidate_observations.py`（`observation_id` 身份，含 `save_decision` / `save_review` / `list_decisions` / `check_observations` / `candidate_observation_delivery.py`）→ main 的 `backend/candidate_observation.py`（`symbol` 身份，`workspace` / `freeze` / `adopt` / `stop`）。落盘在 `VR_DATA_DIR` 租户目录（缺省 `~/.vibe-research`，见 `docs/architecture.md:811`），**必须先备份、写迁移脚本、逐租户对账、可回滚**，确认 `decision`/`review` 历史无损后才可退役旧存储。此步不可与 1 并行。
4. **监控消费者归一**（依赖 1、3）：`live_risk_service` 合并后两侧规则（`price_observation_rules` / `market_event_rules` / `signal_definitions` vs `candidate_monitor` / `strategy_conditions`）共用同一轮次，必须保证每租户每轮一次检查、`alert_events` 去重语义不变，不新增 cron/daemon（`docs/architecture.md:823`、`docs/tickflow-aqsp-integration.md:148`）。

### 3.2 可以后做

- 前端页面视觉整合：`index.css` 两组样式并存、`PageOutline` 与阶段 A 工作台页并存、个股研究页两套证据面并存。
- `docs/architecture.md` / `docs/position-action-plans.md` 回执合并（不阻塞代码，但需在合并提交前完成）。
- §1.3 的「语义重复」删并：按 `docs/architecture.md:792-799` 与 `docs/tickflow-aqsp-integration.md:106-111`，**先做 route→API→service→storage→task→tests 逐项依赖审计，再迁移对账，最后退役**；不得凭文件名/页面相似直接删。
- 长期：统一任务身份/锁/进度、研究版本→PIT/OOS→人工晋级→冻结运行→漂移观察→人工退役（`docs/architecture.md:807`）。

### 3.3 必须串行 / 宪法约束

- **串行链**：契约定型 → 存储迁移 → 调用方迁移 → 定向测试 → typecheck/build → 完整验证（单独排期）→ 部署（仓主授权 + health/数据契约检查）。任何一步未过不得进入下一步（`docs/agent-operating-boundaries.md:100,105`）。
- **宪法次序红线**：`docs/architecture.md:801-807` 与 `docs/tickflow-aqsp-integration.md:8,200-209` 明确「A 未封口不进入 B/C」「前一阶段未验收不得跳到后一阶段」。而 `docs/tickflow-aqsp-integration.md:129-159` 的阶段 A 能力矩阵仍大量标注「部分适配 / 未实现 / 首段」，行 161 自述「该表目前是完整缺口基线，不是完成声明」。
  → **阶段 A 尚未封口**。因此本次合流只能定位为「代码层面的对账与共存」，**不得**借合流删除 main 的个人决策能力、**不得**把阶段 A 未封口项标成已完成、**不得**用合流代替阶段 A 验收门。

---

## 4. 红线检查（`AGENTS.md` §5 + 守卫点）

| 红线 / 守卫 | 合流过程中会碰到的位置 | 守卫点与依据 |
|---|---|---|
| **未来数据** | 阶段 A 新增 `strategy_backtests.py`、`factor_trials.py`、`factor_validation.py`、`signal_scans.py`、`src/aqsp/backtest/custom_strategy.py`、`strategy_portfolio.py`；main 新增 `position_price_path.py` 窗口计算 | `backend/strategy_backtests.py:136-163`（逐标的 `outcome_as_of` 覆盖门禁）、`:208`（`price_mode != "raw"` 直接阻断）；`src/aqsp/research/strategy_evidence.py:93-94`（`price_mode must be raw`）；`docs/architecture.md:851-856`（禁 `shift(-N)`/中心化 rolling/全期归一化）；`docs/tickflow-aqsp-integration.md:50`（PIT horizon 纠偏）。合流后须重跑「红线静态扫描」确认两侧新增代码无 `shift(-N)` 静态负向偏移 |
| **数据完整性 / 市场日横截面覆盖（P0）** | 阶段 A `src/aqsp/data/sqlite_db_source.py` 的 `get_latest_daily_date()`（8 文件 9 处生产调用）与 10 个消费者；main 侧 `scripts/data/check_data_freshness.py`、`scripts/analysis/ic_diagnosis.py:47`、`ic_dual_verdict.py`、`backend/app.py:2703,2764`、回补/导入脚本等裸 `MAX(trade_date)` 口径 | `docs/data-refresh-readiness.md` §2–3/§6（5,738 票逐票 MAX 分布、单票 `000001.SZ` 把全表 MAX 拉到 20261102）；`922809b7` 的 `src/aqsp/data/daily_coverage.py` + `AQSP_MIN_MARKET_DAILY_COVERAGE`（默认 0.8，须显式放宽）+ `market_date_is_usable()` fail-closed；`backend/live_risk_service.py:77-82` 的 `None` 分支；`GET /api/aqsp/data-coverage`（`backend/app.py:3289`）。归属 `docs/architecture.md` §1.3.1「任何一个数字都要回答在 t 时刻能不能拿到」——裸 MAX 等价于用不完整横截面构造 as-of，必须拦截。合流后 main 侧同口径消费者须补同一门槛，详见 §2.4 |
| **LLM 覆盖打分** | `backend/candidate_discussion.py`（两侧）、`position_discussion.py`、`backend/stock_research.py` 四维报告 | `docs/architecture.md:22`（LLM 不参与选股打分）、`:855`；`docs/tickflow-aqsp-integration.md:46`（词面护栏：交易动作/目标价/仓位措辞整份草稿失败关闭）；main `candidate_observation_api.py` 的 `discuss_candidate` docstring「no automatic candidate intent adoption」、`adopt_candidate`「never a buy order」；`docs/architecture.md:748`（模型不覆盖评分或风险阈值） |
| **看到亏损调参** | 合流后 `learner` / 研究变体自动分层 / 策略健康 | `docs/architecture.md:30`（冻结优先）、`:539-556`（最低样本量 + 30 天冷却）、`:837`（学习对象是 IC/命中率/特征漂移，不是 PnL）；`docs/tickflow-aqsp-integration.md:54`（「不改变自动研究分层、运行权重或正式策略状态」）；`docs/architecture.md:910`（crash_protection 是硬编码减半，不是调参） |
| **下单逻辑** | 两套 `adopt` 端点、`/api/positions/{id}/sale-review`、`strategy-watch` 采用 | `docs/architecture.md:21`（不自动下单、不接券商）、`:671`（执行与缺项记录）；`docs/agent-operating-boundaries.md:67-72`（禁「立即买入/执行开仓」措辞，「我已卖出」只打开成交回录）；main `candidate_observation_api.py` 的 `AdoptInput` 需 `operation_id` + `expected_version` + `expected_evidence_version`，属人工确认而非订单 |
| **secrets / API key** | `VR_API_KEY`、`FUYAO_API_KEY`、`llm.apiKey`、`.env.example` 的 `AGNES_MODEL` 变更 | `backend/app.py:118,165,172`（`VR_API_KEY` 从 env 读 + 租户解析）；`backend/auction_benchmark.py:147`（`os.environ.get("FUYAO_API_KEY")`，未配置返回 `source_unavailable`，不硬编码）；`backend/README.md:44,121`；main `.env.example` 只改模型名、**未写入密钥**；合流验证须跑 `python scripts/check_no_secrets.py`（`docs/agent-operating-boundaries.md:51`） |
| **冻结阈值** | 阶段 A 回测/扫描任务、gate 状态 | `docs/tickflow-aqsp-integration.md:14`（任务不写正式 thresholds/ledger/生产 gate）；`src/aqsp/research/strategy_evidence.py:49-50,155-156,182-183`（`human_review_required=True` / `promotion_authorized=False` 强制值）；`AGENTS.md:157`（改 `config/thresholds.yaml` 需 walk-forward 报告，agent 不得自行合并） |
| **PIT / 不复权** | 价基、复权因子、跨日价位、指数身份 | `backend/strategy_backtests.py:208`；`src/aqsp/research/strategy_evidence.py:93-94`；`docs/tickflow-aqsp-integration.md:24-26`（指数身份纠错、86 条旧观察作废、`freshness=stale`）；`docs/architecture.md:64,106,850`；`auction_benchmark` 跨日结果要求两日 PIT 因子齐全（`backend/README.md:121`） |
| **租户隔离** | 两套存储的 `VR_DATA_DIR` 租户目录、两套 monitor 消费者 | `backend/app.py:172`（`_tenant.resolve_tenant_id`）；`docs/tickflow-aqsp-integration.md:22`（tenant 隔离/幂等/404 验收）、`:124`（非 local 租户复盘显式不可用）；`docs/architecture.md:707,711`（同租户同 `position_id` 核对） |
| **人工确认** | `adopt` / `strategy-watch` / `sale-review` 写路径 | main `candidate_observation_api.py` 的 `AdoptInput`/`StopInput`（`expected_version`、`expected_evidence_version`、`operation_id`）；main `app.py` 的 `StrategyWatchIn`（`expected_position`、`expected_revision`、`expected_analysis_basis_id`）；`docs/architecture.md:749`（有来源 AI 观察价经用户确认后才保存）；`docs/architecture.md:708`（ae→pf 锁顺序 + CAS，旧写入拒绝） |
| **并行 / 服务器边界（本审计）** | 本次合流全过程 | `docs/agent-operating-boundaries.md:93-105`：只读审查模式、不得操作前台/生产、重型测试不得与多子任务并行；`:53` 部署链；`AGENTS.md:160`（合并 ≠ 发布，线上变更需仓主单独授权）。**本审计未执行上述任何受限动作** |

---

## 5. 最小合流路径（建议命令序列，本审计**未执行**）

> 方向选择：建议 **A —— 把 `origin/main` 合入阶段 A 分支**，这样保留阶段 A 的完整历史与逐项验收证据（`docs/architecture.md:803` 要求每项可追溯），冲突解决时以 main 为「更新侧」按 §2 对账表取值。若长期决定以 main 为主干维护，可改走 B（把 `7caf6d57`/`233c45e3`/`922809b7` 的净新增 cherry-pick 到 main），但冲突集合相同，不减少工作量；**方向 B 必须显式带上 `src/aqsp/data/daily_coverage.py`、`src/aqsp/data/sqlite_db_source.py` 与 4 个 P0 回归测试，否则守卫丢失**（§2.4）。

```bash
# ---- 步骤 0：前置（可回滚的锚点）----
git status -s -b                       # 注意：当前工作树有未跟踪 uv.lock，先确认处置，不纳入合并
git rev-parse HEAD                     # 记录 922809b7...（应含 P0 提交；若仍为 233c45e3，先确认 922809b7 是否已在本分支）
git branch backup/before-tickflow-merge 922809b7   # 回滚锚点（仅本地，不 push）

# ---- 步骤 1：发起合并但不提交 ----
git switch -c merge/tickflow-stageA-into-main-line   # 或直接在现分支操作
git merge --no-commit --no-ff origin/main

# 验证 1：冲突清单与本文 §2 的 33 个文件逐一核对
git diff --name-only --diff-filter=U | sort
git merge-tree --write-tree --name-only HEAD origin/main | head -40

# ---- 步骤 2：按 §2 对账表逐文件取值 ----
# 保留 main（14 个）：
git restore --source=origin/main --staged --worktree -- <file>
# 保留阶段 A（0 个，本审计无此项；如后续判定需要）：
# git restore --source=HEAD --staged --worktree -- <file>
# 合并两者（18 个）：手工编辑后
git add <file>
# 迁移后退役（1 个）：保留 main 的 candidate_discussion.py，删除阶段 A 独立实现

# ---- 步骤 3：API 契约去重（必须在任何联调前）----
# 编辑 backend/app.py：删除 HEAD 侧 6 个 /api/candidate-observations* 端点
# 与 4 个重复的 /api/positions/{id}/strategy-* 端点；保留 main 的
# include_router + 4 端点 + /sale-review；保留 ~70 个 /api/aqsp/* 路由
# 验证 3：
python - <<'PY'
import re
s = open('backend/app.py').read()
paths = re.findall(r'@app\.(?:get|post|put|delete|patch)\("([^"]+)"', s)
dups = {p for p in paths if paths.count(p) > 1}
print('duplicate route literals:', dups)
assert not dups, '存在重复注册的路由字面量'
PY
grep -n 'candidate-observations' backend/app.py backend/candidate_observation_api.py | head -30

# ---- 步骤 4：数据契约与状态迁移（串行，单独 PR 更稳妥）----
# 4a 核对三处 TechnicalBasis 字段一致：
git show origin/main:frontend/src/types/aqsp.ts | grep -n 'AqspTechnicalBasis' -A 12
git show origin/main:src/aqsp/web/home_snapshot.py | grep -n 'class HomeSnapshotTechnicalBasis' -A 12
git show origin/main:backend/aqsp_bridge.py | grep -n '_parse_technical_basis' -A 12
# 4b 先备份 VR_DATA_DIR 租户目录，再写迁移脚本（observation_id -> symbol），
#    逐租户对账 decision/review 条数，保留旧文件直到验收通过；脚本需自带回滚。

# ---- 步骤 5：验证（不得跑全量 pytest / 不得连服务器 / 不得部署）----
ruff check .                                    # CI 门禁同款（.github/workflows/ci.yml:84）
# P0 守卫回归（§2.4）：确认合并后守卫未被 main 侧覆盖、真实库仍 fail-closed
git grep -n 'market_date_is_usable' -- src/aqsp/data/sqlite_db_source.py src/aqsp/data/daily_coverage.py
git grep -n 'SELECT MAX(trade_date)' -- src/aqsp/data/sqlite_db_source.py   # 应无「裸 MAX 直接返回」残留
python -m pytest -q \
  tests/test_daily_coverage.py tests/test_market_daily_coverage_gate.py \
  backend/tests/test_data_coverage_api.py
python -m pytest -q \
  backend/tests/test_position_action_plan.py \
  backend/tests/test_position_strategy_evidence.py \
  backend/tests/test_position_strategy_watch.py \
  backend/tests/test_api.py
python -m pytest -q \
  tests/test_aqsp_bridge.py tests/test_home_snapshot.py \
  tests/test_live_position_monitor.py tests/test_write_home_snapshot.py
npm --prefix frontend test                      # = tsc --noEmit + node scripts/run-contracts.mjs
                                                # （扫描全部 *.test.ts，含 strategy-watch / catalyst-sector-view 契约；不支持按文件过滤）
npm --prefix frontend run build                 # = tsc -b && vite build
python scripts/check_no_secrets.py
# 宪法完整性：确认 §1 与共同祖先逐字一致（AGENTS.md:156,181 禁止改 §1）
python - <<'PY'
import subprocess
base = subprocess.check_output(['git', 'show', '9106ac7a:docs/architecture.md'], text=True)
cur = open('docs/architecture.md').read()
sec = lambda s: s.split('## 1. 项目宪法')[1].split('## 2.')[0]
assert sec(base) == sec(cur), '§1 宪法被改动，必须人工审查且不得自行合并'
print('§1 宪法未改动')
PY
# 文档同步检查（AGENTS.md §4）
git diff --name-only 9106ac7a -- docs/ AGENTS.md

# ---- 步骤 6：提交（按 AGENTS.md §6.1）----
# 改了 docs/architecture.md（非 §1）→ 用 docs: 前缀、交审查 agent 优先审查、
# CI 全绿 + mergeable_state=clean + squash；PR 描述含「做了什么/为什么/风险/怎么验证」。
# 合并 ≠ 发布：任何部署/重启/cron/ACL 变更须仓主单独授权（AGENTS.md:160）。
```

**回滚（分层，按当前所处阶段选一）**

```bash
# 阶段 A：仍在解决冲突、未提交
git merge --abort
git status -s -b            # 确认回到 922809b7 且工作树干净（uv.lock 除外）

# 阶段 B：已提交、未推送（破坏性，执行前必须确认无未提交工作）
git status --porcelain      # 必须为空
git reset --hard 922809b7   # 或 git reset --hard backup/before-tickflow-merge

# 阶段 C：已推送（不改写历史，用反向提交）
git revert -m 1 <merge-commit-sha>

# 阶段 D：已发布（按 docs/agent-operating-boundaries.md §3 / docs/architecture.md:825）
# 切回上一个 immutable release 制品；不因纯文档提交重启线上服务。
```

**每步验证要点**：步骤 1 后冲突文件数必须 = 33；步骤 3 后 `app.py` 无重复路由字面量且 `/api/candidate-observations` 只由 `candidate_observation_api.py` 提供；步骤 5 后定向测试全绿、typecheck/build 通过、`check_no_secrets.py` 通过、`docs/architecture.md` §1 无 diff；完整全量测试与公网验收按 `docs/agent-operating-boundaries.md:100,105` 单独排期，不与并行子任务同时跑。

---

## 6. 证据限制（明确标注）

1. **本审计为静态只读**：未运行任何 pytest / ruff / typecheck / build，未启动服务、浏览器或连接服务器，未部署。所有「通过/可用」类判断均引用仓库内既有回执文档，不代表本次复核过运行时。合流后的路由注册顺序、租户文件锁、异步 worker 行为**无法静态验证**，必须用隔离真实 API（`VR_DATA_DIR` + 独立 uvicorn）+ 独立无头桌面/390px 流程验收（`docs/agent-operating-boundaries.md:28-33,100`）。
2. **动态行情无法从仓库验证**：腾讯/新浪/东财/Fuyao 实时源当前可用性、盘中真实命中、`monitor` 正向触发、全市场 fresh 覆盖、竞价风向标真实响应，仓库内均标为「未验收」（`docs/tickflow-aqsp-integration.md:16,20,22,30,38,149`；`docs/architecture.md:821` 明确「600519 截至 2026-09-11，API 阻断为陈旧」）。不得把链路冒烟当成行情有效或策略有效性证明。
3. **模型效果无法从仓库验证**：真实模型完整分析质量、45/45 秒预算稳定性、提供商目录、长期提醒噪音、公网真实模型稳定性，均为未验收项（`docs/tickflow-aqsp-integration.md:40,44,46,58`；`docs/architecture.md:689,693,699,755`）。合流不得把合成测试结果升级为生产效果证明。
4. **本地 raw 日线陈旧 + 覆盖不完整导致的证据限制（重点，P0，详见 §2.4）**：
   - `A股量化分析数据/astocks_raw.db` 约 2.4–2.59 GB；`astocks_raw.db.update_stamp.json` 内容为
     `{"finished_at":"2026-11-02T17:57:01+08:00","target_day":"2026-11-02","db_max_trade_date":"2026-11-02","updated_rows":18,"processed":"1/1"}` —— 11-02 只更新了 **18 行**。
   - `docs/data-refresh-readiness.md` §2 的逐票 `MAX(trade_date)` 实测（5,738 只票）：20261102 **1 只**（`000001.SZ`）、20260930 **2,818 只**、20260911 **1,061 只**、20260529 **1,327 只**、更早 531 只；2026-10-01 之后每个交易日恰好 1 行且全部是 `000001.SZ`。这与 `docs/tickflow-aqsp-integration.md:12`「4,995 universe 仅 1 个 fresh、4,994 逐代码 stale、状态 `partial`」及行 10/24/26/38/121/149「截至 2026-09-30、滞后 33 天、`freshness=stale`」一致——文档说的是**逐票覆盖**，不是全表 MAX。
   - **根因是「全表 MAX 被单票拉高」而非「整个库停在 9 月」**：`get_latest_daily_date()` 原样返回 `SELECT MAX(trade_date)`，会把 20261102 当完整市场日；审计期间 `922809b7` 已在阶段 A 侧改为覆盖门槛 fail-closed，真实库现返回 `None`，但 main 侧 `MAX(trade_date)` 口径（§2.4）无此守卫。
   - **结论**：`db_max_trade_date` / 全表 MAX **绝不等于全市场覆盖新鲜**；仓库内所有基于本地 raw 的偏移/异动/回测样本只能作为历史链路证据，**不能作为当前行情信号、正向命中、数据就绪或策略有效性证据**。合流验收若需「当前有效」结论，须先完成 runner 全 universe 回补（`eligible_coverage_pct=1`）、PIT 复权因子覆盖核验、查明并清理 `000001.SZ` 哨兵行、数据齐备后重跑阶段 A 验收（`docs/data-refresh-readiness.md` §6.4/§7，均需仓主授权，本轮未执行）。
5. **阶段 A 未封口**：`docs/tickflow-aqsp-integration.md:129-159` 的叶子能力矩阵大量「未实现/部分适配」，行 161 自述「完整缺口基线，不是完成声明」；`docs/architecture.md:815-821` 也列出多项首段缺口。按 §3.3 宪法约束，本次合流**不等于**阶段 A 验收通过，也不授权进入阶段 C 删并。
6. **未验证项保持未完成**：生产已知仅 1 只真实持仓、至少两仓的公网交互、长期提醒噪音、真实成交归因、G5 整体完成状态（`docs/architecture.md:693,739,755`）——本次对账不提供任何这些项的证据。
7. **不冲突文件的运行时风险未被穷举**：§2.3 的 14 个隐性冲突文件与 §1.3 的语义重复清单只做了静态差异比对，未下钻到全部 route→API→service→storage→background task→tests 调用链；实际删并前须按 `docs/tickflow-aqsp-integration.md:104,795` 补齐逐项引用后再定迁移/兼容方案。
