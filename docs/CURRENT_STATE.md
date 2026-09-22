# AQSP 项目现状（Current State）

> **单一事实来源。** 最后核对：2026-09-22（基线 `origin/main` @ `ab6f5fef` / #173）。
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
| `AGENTS.md` | 编码硬约束（类型/测试/PR≤300 行/浏览器调试边界等） |
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
- **事件 / 风险面**（2026-09-08 起，均带测试 + `scripts/fetch_*.py` + 管线接入）：
  - `longhubang.py` 龙虎榜 · `lockup.py` 限售解禁 · `cls_news.py` 财联社快讯 · `concept_board.py` 东财 slist 概念板块。
- **资讯雷达**：`backend/newsradar.py`（12 赛道 / 108 源 RSS）。
- **PIT / 复权**：`industry_pit.py`、`macro_pit.py`、`pit_financial.py`、`pit_policy.py`、`adjust.py`。
- **个股深度 API**（后端 `backend/app.py` + `astock.py`）：`/api/dragon-tiger`、`/api/lockup`、`/api/margin`、`/api/block-trade`、`/api/holders`、`/api/dividend`、`/api/fund-flow`、`/api/blocks`、`/api/hot-concepts`、`/api/investor-qa`。

## 5. LLM / 研报 / 复盘能力（`src/aqsp/briefing/` + `backend/chat.py`）

| 能力 | 实现 |
|---|---|
| 多 Agent 讨论/辩论 | `agent_roles.py`（**9 个 A 股角色**）+ `debate.py` + `debate_tracker.py` + `conclusion.py` |
| 简报 / 研报生成与渲染 | `generator.py` + `renderer.py` + `schema.py` + `templates/` |
| 收盘复盘 | `closing_review.py`（`aqsp closing-review`） |
| 研究引擎 / 报告 | `research_engine.py` + `report.py` |
| AI 对话（合规） | `backend/chat.py` `/api/chat`（五维投研框架，system prompt 强制中立：不荐股/不预测/不给时机） |
| 前端呈现 | `components/aqsp/sections/DiscussionSection.tsx`（多 Agent 委员会结论 + 可展开过程）、`CandidateSection`（讨论复核列 / 研究链） |

🔴 以上全部 **advisory-only**，不回流打分/权重。

## 6. 前端 IA（`frontend/src/lib/ia.ts`）

- 主区：**今日研究** / **市场环境** / **资讯雷达** / **策略实验室** / **结论归档** / **选股绩效**
- 我的：**自选** / **持仓** / **笔记** / **研报**

## 7. 当前进行中

- **T3 双窗口门禁验证**（3y/5y 两臂对比；`compare_t3_dual_window.py` 已修）。进展看 `outputs/T3_接力进展_*.md`；运行时权威记忆在 `.workbuddy/memory/MEMORY.md`。

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

**⚠️ 疑似过期但不得擅动（被代码当默认路径）**
- `docs/walkforward-2026-05.md`：`src/aqsp/cli.py` 的 `aqsp walkforward --report` **默认值**。改默认值是代码变更，须单独 PR。
- `scripts/diagnose_momentum.py` 的 `--output` 默认 `docs/momentum-direction-2026-05-28.md`，而该文件已归档到 `docs/archive/process/`；脚本未改（属代码变更）。
- `src/aqsp/research/summary.py` 的 `absorption_path` 默认 `docs/research_absorption.json`（该文件已移除）；同上，须单独 PR 处理。

**本地分支**
- 35 个本地分支**全部未并入 `origin/main`**（多数对应仍开着的 PR，如 `fix/vibe-acl-covers-all-read-paths`）。**不可批量删除**；仅能逐个核对 PR 状态后清理。

**`outputs/`（阶段产物）**
- 属本地工作区产物，`outputs/` 已被 `.gitignore`；不在仓库历史中，按需本地清理即可。

---

## 9. 给后续 agent 的两条硬规则

1. **吸收任何外部仓库前**，先读 `docs/research/repo_radar.md` + 对应 `*_absorption_*.md`，并核查 AQSP 是否**已实现**该能力（技能 `aqsp-external-repo-intake`）。
2. **别新建重复的阶段文档**；更新本文或对应 Active 文档。历史材料下沉 `docs/archive/`。
