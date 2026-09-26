# AQSP 项目总说明文档（最完整版）

> 本文档是「以后任何修改 / 处理都能搞清楚」的总入口。**代码宪法仍是 `docs/architecture.md`（§1-§10），本文件不重复它的条款，只做「全局地图 + 主链 + 审计发现 + 运维」的导航与状态记录。**
> 维护约定（见 `AGENTS.md`）：改代码前先读 `docs/architecture.md` → 本文件 → `docs/agent-operating-boundaries.md` → 对应模块 spec。
> 最后更新：2026-09-26（基于 main `e67f1766`，即 PR #230 已合入并上线 prod）。

---

## 0. 阅读顺序（新人接手）

1. 本文件 §1（定位）+ §3（三机架构）—— 先知道它是什么、跑在哪。
2. `docs/architecture.md` §1（宪法/边界）—— 什么能做、什么禁止。
3. 本文件 §4（代码库地图）+ §5（主链）—— 全局到局部。
4. 改某个子包前，读 `architecture.md` 对应契约小节 + 本文件 §4 里该子包的「关键文件」。
5. 上线前必读本文件 §8（不可变发布）+ §9（cron）+ `AGENTS.md` §6 工作流。

---

## 1. 项目定位（来自 `README.md` / `architecture.md` §1）

- **它是什么**：local-first A 股量化**选股工作台**。**不是交易机器人，不接券商下单接口**。职责=每天基于最新 A 股数据筛选开盘/尾盘候选股，把「选了什么、依据、风险、参考买点/止损/止盈、是否只观察」产出到本地报表 + 静态面板 + 通知渠道，由人最终决策。
- **主运行环境**：本地 Mac / 私有服务器（prod）/ 只读计算节点（runner）。
- **数据位置**：本地私有数据目录、`private_data/`（通达信）、运行时缓存；**不上传**大数据/账本/缓存/私钥/token/日志到 GitHub。
- **正式看板**：FastAPI（API `127.0.0.1:8900`）+ React（前端 `127.0.0.1:5899`）；prod 上 systemd target `aqsp-vibe-research.target` 管两个 service。
- **GitHub 作用**：备份代码、存文档、跑 Actions（CI 卡 `ruff check .` + `pytest -m 'not live'`）。
- **当前重点**：只做 A 股主链。港股/美股/券商接口/截图 OCR 等已记录但**仓主确认前不开发**。

### 主策略家族
RPS 相对强度、放量突破、均线缩量回踩、碗口反弹、低波趋势、早盘打板、尾盘溢价、多因子轮动。选股逻辑来自公开开源量化项目 + A 股常见理论 + 持续 walk-forward 验证。

### 关键红线（详见 §11）
不写未来数据、不裸 `datetime.now()`、阈值不从字面量、不静默失效、不把 LLM 覆盖打分、不塞下单逻辑、不把 secrets 硬编码、不擅推 main / 不擅上生产。

---

## 2. 规模速览（2026-09-26）

| 项 | 规模 |
|---|---|
| 源码 `src/aqsp` | 107,798 行 / 215 .py（含 23 子包 + 顶层文件） |
| 测试 `tests/` | 247 文件 / ~121k 行（`pytest -m 'not live'` 为默认套件，联网用例标 `live`） |
| 顶层子命令 | `cli.py` 22 个 `add_parser` 子命令 |
| 配置 `config/` | 11 文件（含 `thresholds.yaml` 策略阈值单一来源） |
| 脚本 `scripts/` | 68 .py + 47 .sh（含 IC 闭环 4 脚本、不可变发布、cron 安装） |

**最大子包（按行数）**：`web` 24.6k → `data` 17.2k → `briefing` 12.0k → `strategies` 9.9k → `news` 4.2k → `portfolio` 2.5k → `ledger` 2.5k → `backtest` 2.1k → `monitor` 1.6k → `risk` 1.5k → `research` 1.1k → `universe` 1.1k → `regime` 973 → `features` 946 → `execution` 851 → `audit` 755 → `optimizer` 702 → `utils` 629 → `core` 535 → `runtime` 533 → `filters_lethal` 397 → `reports` 229 → `services` 194。

**最大单文件**：`cli.py` 8.8k → `market_context.py` 3.3k → `notify_templates.py` 1.9k → `notification_runtime.py` 719 → `report.py` 654 → `research_engine.py` 645 → `strategy.py` 633 → `walkforward_gate.py` 599 → `runtime_snapshot.py` 505。

---

## 3. 三机架构（本地 Mac / GitHub / 生产服务器）

| 角色 | 主机 | 规格 | 职责 | 关键路径 |
|---|---|---|---|---|
| **本机 Mac**（开发/验证） | 你本地 | — | 写代码、跑测试、提 PR、局部验证 | `/Users/ivon/Documents/AI量化选股` |
| **prod**（只读/可写生产） | `aqsp-server`（Host）/ `8.130.124.238` | 2C/1.6G | 跑主链、API、看板、落日报、单向 pull runner 产物 | `/opt/aqsp-releases/<sha>/` + 软链 `aqsp-scheduler-current`；共享 `/opt/aqsp/data`；宝塔 cron |
| **runner**（只读计算节点） | `aqsp-runner` / `38.147.170.174` | 4C/7.9G，Py3.10.12，SSH 走 31777 | 跑重计算（IC 诊断、5y gate 等），**无 SSH 回连 prod** | `/opt/aqsp-runner/{data,releases,venv}`；靠 `PYTHONPATH=<release>/src` |

**关键事实（已实测）**
- prod 发布软链真实路径 = `/opt/aqsp-releases/aqsp-scheduler-current`（`/opt/aqsp-scheduler-current` 不存在）。判「线上跑哪版」看 `readlink -f .../aqsp-scheduler-current`。
- **runner ↔ prod 单向 pull 模型**：runner 是只读计算节点，绝无 SSH 回连 prod；读取方永远是 prod（拉 runner 的产物）。IC 闭环即此模型（见 §7）。
- prod 自动链路入口 `release_task_entrypoint.sh` 恒设 `AQSP_RUNTIME_DATA_ROOT=/opt/aqsp/data` + `AQSP_PROJECT_ROOT=<release>`（`release_task_entrypoint.sh:68,76`），故「未设 env 回落 /tmp」只在非 entrypoint 入口（裸 CLI / 本地调试）触发——这也是 §11 R5 隐患仅对裸 CLI 生效、prod 自动路径不受影响的原因。
- 本机 Mac **只做开发/验证**；gate 跑批一律放 runner，否则 OOM/时区错位。
- 判红权威 = GitHub Actions（`ruff` 先于 `pytest`）；`skipped≠健康`，不写未来数据，LLM 不覆盖打分，不塞下单逻辑。
- **部署 prod 须老大确认**（AGENTS.md §6.1）；判红权威之外，本地 IP 计数限流、沙箱代理只放行 `github.com` 挡 `api.github.com`（PR 走本机 Clash 代理 `--proxy http://127.0.0.1:7890` + `gh auth token`）。

---

## 4. 代码库全局地图（逐模块职责）

> 行数为 2026-09-26 统计。每条给「职责 + 关键文件:行号」（可直接跳转）。

### 4.1 顶层文件（按行数）
- **`cli.py`** (8.8k)：唯一 CLI 入口，22 子命令（见 §6）。只做参数解析 + 调 service 层，**不写业务逻辑**（AGENTS.md §3.7）。
- **`market_context.py`** (3.3k)：市场状态上下文（成交额/宽度/情绪）聚合，供策略与日报消费。
- **`notify_templates.py`** (1.9k) / **`notification_runtime.py`** (719) / **`notification_style.py`** (212) / **`notifier.py`** (478)：通知模板 + 运行时编排 + 通道（Server酱/Webhook/Telegram/企业微信/飞书）。
- **`report.py`** (654)：通用 Markdown/报表渲染。
- **`research_engine.py`** (645) / **`research/`** (1.1k)：研究/因子探索引擎。
- **`strategy.py`** (633)：策略运行编排（调度各因子打分 → 汇总）。
- **`walkforward_gate.py`** (599)：walk-forward 门禁判据（DSR/PBO/cutoff/滚动窗口）。
- **`runtime_snapshot.py`** (505) / **`runtime/`** (533)：运行时快照（看板自检用）。
- **`paper.py`** (442)：虚拟盘账本落地。
- **`freshness.py`** (287)：数据新鲜度判定（`FreshnessError`）。
- **`config.py`** (292)：配置加载入口。
- **`candidate_quality.py`** (131) / **`goal_switches.py`** (218) / **`internet_strategies.py`** (162) / **`indicators.py`** (111) / **`ratings.py`** / **`models.py`** / **`presentation.py`** (316)：候选质量、目标开关、在线策略、指标、评级、数据模型、展示层。
- **`_constitution_check.py`** (140)：启动期对 `architecture.md` §1 宪法的自检守卫。

### 4.2 子包
- **`web/`** (24.6k, 10 文件)：FastAPI API + 静态 Dashboard 服务。路由在 `web/api*.py`；`web/data_provider.py` 供前端拉数据（其 212/1301 行附近有 `except` 吞异常，见 §11 R6）。`web/` 是 prod 的 `aqsp-vibe-research-api` / `-preview` service 本体。
- **`data/`** (17.2k, 52 文件)：数据源层。`data/source.py` 定义 `DataSource` 抽象（协议）；`data/adjust.py` 的 PIT 复权（点复权因子，回测/ledger 走不复权+PIT，前复权只用于展示）；各 `*_source.py`/`*.py` 为具体源（eastmoney/sqlite/TDX/Baostock/Tushare/东方财富公告股东户数等）。`get_limit_pct`（涨跌停分类）单一来源在 `data/source.py`（architecture.md §9.5）。
- **`briefing/`** (12.0k, 12 文件)：收评/早盘日报。`closing_review.py` 是核心（`review_today`、`_empty_review`、`build_factor_ic_section`、`_factor_ic_runtime_root`）。PR #228/#229/#230 在此收敛。**IC 段读/写 fallback 不同源已在 PR #230 修复**（见 §11）。
- **`strategies/`** (9.9k, 28 文件)：因子与策略。含 momentum / triple_rise / composite / high_tight_flag / mean_reversion / event_driven / 自进化 / 自适应 / 因子挖掘。`thresholds.yaml` 注入点在各策略 `__init__`（AGENTS.md §3.5）。
- **`backtest/`** (2.1k, 4 文件)：回测引擎（不复权+PIT、T+1 退出语义）。`walk_forward.py` 的 `except`（65/70/896）为回测单窗口失败隔离，**非**「吞成 -inf」。
- **`optimizer/`** (702, 6 文件) + `strategies/` 内自进化模块：`param_optimizer.py:80/200/251/261` 有 `except Exception: score = -inf`（**R6 吞异常**，掩真实评估失败，仅记录不修）。
- **`news/`** (4.2k, 4 文件)：新闻/催化。`catalysts.py` 子进程超时（PR #226 修过 PYTHONPATH 泄漏）；`news_catalyst` 子进程超时测试已修。
- **`portfolio/`** (2.5k, 9 文件)：组合构建、相关性/集中度降维、PM 裁决汇总。
- **`ledger/`** (2.5k, 7 文件)：虚拟盘账本 + 战绩对账（debate↔实盘，PR #227 闭环）。`base.py:659` 有 `except`（账本写入容错）。
- **`monitor/`** (1.6k, 4 文件)：`checker.py` 监控（数据源登录/Tushare/GLM/通知通道），产出 gate_run_status；776 行 `except` 为单项检查容错。
- **`risk/`** (1.5k, 5 文件)：熔断、动态止损、集中度上限。
- **`regime/`** (973, 6 文件)：市场状态分类（`hmm_detector.py` 等）。
- **`features/`** (946, 4 文件)：特征（含 `event_calendar.py`，其运行时数据根经 `aqsp.core.runtime.runtime_data_root` 回落 release 根、325 行 `except`）。
- **`execution/`** (851, 4 文件)：虚拟盘执行（**非真实下单**）。
- **`universe/`** (1.1k, 5 文件)：选股池构建（top-300 流动性池等）。
- **`filters_lethal/`** (397, 3-6 文件)：排雷过滤器（跌停保护/公告关键词/股东户数/解禁等）。**历史「从未生效」根因：数据产出方缺位 → 一直空转；PR #225/#226 接入产出链路 + 响亮告警（data_missing 而非静默放行）。**✅ **数据产出方调度缺口已于 PR #234（2026-09-26 合并+发版）收口**：`daily_pipeline._step_refresh_risk_datasources` 每日调度落盘 `pit_cache/{holder_count,announcements,lockup}.csv`，过滤器从此读真实数据（缺数据显 `data_missing`）。
- **`audit/`** (755, 4 文件)：审计工具（红线自检查）。
- **`core/`** (535, 5 文件)：基础件。**`core/time.py::now_shanghai()` 是全项目唯一合法时钟**（AGENTS.md §3.4）；全局裸 `datetime.now()` 已确认仅 `closing_review.py:621` 的注释提及，**无任何真实调用**。
- **`research/`** (1.1k) / **`research_engine.py`**：研究/因子探索。
- **`services/`** (194, 3 文件)：service 层（`walkforward_data.py:46` 有 `except` 容错）。
- **`reports/`** (229, 2 文件)：报告渲染辅助。

### 4.3 配置 `config/`（11 文件）
`thresholds.yaml`（策略阈值**单一来源**，改必升 `version` + walk-forward 报告）、`blacklist.yaml`、`data_sources.yaml`、`evolution_config.yaml`、`factor_config.yaml`、`factor_library.json`、`goal_switches.yaml`、`monitors.yaml`、`news_sources.yaml`、`strategy_sources.yaml`、`trading_holidays.json`。

### 4.4 文档 `docs/`（23 项）
`architecture.md`（宪法，必读）、`AGENTS.md`/`agent-operating-boundaries.md`（协作/边界）、`CURRENT_STATE.md`、`CONSTITUTION.md`、`README.md`、`TROUBLESHOOTING.md`、`daily-operation.md`、`two-node-handoff.md`（runner↔prod 单向 pull）、`walkforward-2026-05.md`、`momentum-diagnosis.md`、`research/`、`archive/`（历史）。

---

## 5. 主链数据流（`scripts/daily_pipeline.py`）

主链 = `bt_task.sh` → `server_sync_and_run.sh` → `daily_pipeline.py::run_pipeline`。步骤清单在 `daily_pipeline.py:1257-1271`：

| # | 步骤 | 函数:行 | 说明 |
|---|---|---|---|
| 1 | 数据更新 | `_step_update_data` :269 | 拉最新 A 股数据进 `data/` |
| 2 | 策略运行 | `_step_run_strategy` :481 | 跑各因子打分 → 候选 |
| 3 | 预测验证 | `_step_validate_predictions` :707 | 校验预测可用性 |
| 4 | 虚拟盘同步 | `_step_sync_paper_trades` :784 | 落虚拟盘账本 |
| 5 | **因子IC回流** | `_step_pull_ic_diagnosis` :608 | best-effort 拉 runner IC 产物（先于收盘复盘，失败/陈旧保留旧产物不阻断） |
| 6 | 收盘复盘 | `_step_closing_review` :674 | 生成日报（含「因子 IC 健康」段） |
| 7 | 自适应学习 | `_step_adaptive_learning` :853 | 样本量门槛 + 冷却期 |
| 8 | 策略自进化 | `_step_auto_evolution` :935 | 子进程隔离（feat/evolution-subprocess-isolation） |
| 9 | 报告生成 | `_step_generate_report` :1060 | Markdown 报告 |
| 10 | Dashboard刷新 | `_step_refresh_dashboard` :1087 | 重建前端缓存 |
| 11 | 数据清理 | `_step_cleanup` :1208 | 过期缓存清理 |

**非交易日分支**（`daily_pipeline.py:1272-1280`）：只跑「报告生成 + Dashboard刷新 + 数据清理」，跳过策略/复盘/学习。

**关键不变量**
- `_runtime_data_root(project_root)` :43 缺省回落 `project_root`（与写侧同源）；设了 `AQSP_RUNTIME_DATA_ROOT` 则用它。
- 步骤失败处理：仅「数据更新」「策略运行」失败才终止；其余（含 IC 回流）best-effort 不阻断（`:1286`）。

---

## 6. CLI 表面（`cli.py`，22 子命令）

`screen`(733) / `run`(756) / `walkforward`(801) / `dashboard`(930, 静态) / `static-dashboard`(936) / `monitor`(942) / `news`(959) / `doctor`(986, 运行就绪诊断) / `sources`(986) / `briefing`(997) / `research`(1008) / `runtime-snapshot`(1015) / `pit`(1023, 查 point-in-time) / `cyq`(1036) / `compare`(1056) / `optimize`(1075) / `discover`(1101) / `mine-factors`(1111) / `evolve`(1118) / `multi-factor`(1129) / `morning`(1139) / `closing-premium`(1154) / `closing-review`(1167)。

各类职责见 `architecture.md` §3-§6 及本文件 §4.1。

---

## 7. 因子 IC 健康诊断闭环（PR #228/#229/#230，已上线 prod `e67f1766`）

**目的**：用滚动 90 天 Spearman IC 监控因子族方向（尤其 momentum 反向），闭环「诊断 → 回流 → 消费 → 日报」。

**4 脚本 + 契约**
1. `scripts/ic_diagnosis.py` (12.8k)：runner 上的滚动 IC 诊断引擎（top-300 流动性池，WF-001 变体参数，as-of = 库内 MAX(trade_date)）。产出 `factor_ic_latest.json` / `report.md` / `ic_history.jsonl` / **`IC_READY`**（4 产物齐全才落 IC_READY）。
2. `scripts/ic_diagnosis_runner.sh` (4.7k)：runner 批跑（负载守卫 MAX_LOAD1=4 等待、硬超时 1800s、`clear IC_READY` on fail）。
3. `scripts/fetch_ic_diagnosis.sh` (6.0k)：prod **单向 pull**。单次 ssh 探测得 `READY_PRESENT/READY_MTIME/JSON_PRESENT/STATUS_VALUE`；exit：0=拉取成功 / 1=不可达 / 2=无结果 / 3=陈旧（`MAX_AGE_HOURS=36`）。
4. `scripts/install_ic_diagnosis_cron.sh` (2.3k)：runner cron `0 2 * * 1-5`（UTC=北京 10:00 工作日），指向软链 `aqsp-scheduler-current`。

**3 个前提（均已就位）**
- ① prod 发版含 `ic_diagnosis.py`（prod `e67f1766` ✓）；
- ② runner 软链同 SHA、release 内 IC 脚本齐备（✓）；
- ③ runner IC cron 已挂载（✓）。

**消费端**：`closing_review.build_factor_ic_section` 读 `$AQSP_RUNTIME_DATA_ROOT/pit_cache/factor_ic/factor_ic_latest.json`，渲染「因子 IC 健康（as-of …, runner 滚动诊断回流）」3 因子表；缺失/损坏/空 → 降级空串（绝不写回打分/排序/下单）。**无信号早退路径 `_empty_review` 已补塞该段（PR #229）。**

**已验证**：prod `aqsp closing-review` 真实落盘 grep 命中「因子 IC 健康」（momentum −0.0604 / triple_rise +0.0383 / composite −0.0204，as-of 2026-09-24）；首个自动触发 09-28（周一）10:00 北京，届时 IC_READY mtime 更新即证「自动自转」。

---

## 8. 不可变发布模型（`scripts/deploy_immutable_release.sh`）

- 每次发布 = 新目录 `/opt/aqsp-releases/<full-sha>/`（git archive 落盘），软链 `aqsp-scheduler-current` → 当前、`aqsp-scheduler-rollback` → 上一版（`:35-36`）。
- 落 `RELEASE_SHA` 到 release 根，供 `/api/health` `release_sha` 自检（防止「部署了但旧代码在跑」）。
- **prod 自动链路**：`release_task_entrypoint.sh` 显式 `export AQSP_RUNTIME_DATA_ROOT=/opt/aqsp/data` + `export AQSP_PROJECT_ROOT=<release>` + `exec timeout 3600 bt_task.sh`（`:183`）。
- **npm-free fast path**（2C/1.6G 防 OOM）：复用当前 release 的 `frontend/node_modules` + `dist`（`cp -a`）新 release 目录，再 `--skip-frontend-build --skip-restart`，最后手工 `systemctl restart aqsp-vibe-research-api/-preview`（PR #229/#230 均走此路径）。
- `INHERIT_DATA=true`（默认）切割时把 `data/` + `reports/` 从 current 继承进新 release。

**判红/回滚**：`/api/health` 看 `release_sha` 与 `readlink -f .../aqsp-scheduler-current` 是否一致；回滚改软链即可。

---

## 9. 三机 cron 清单（北京时间，注明触发脚本）

**prod（宝塔 cron / systemd target）**
- 盘中：主链 `bt_task.sh daily`（`server_sync_and_run.sh` → `daily_pipeline.py`），交易日跑全 11 步。
- 收盘复盘：主链内 `_step_closing_review`（见 §5）。
- 监控：`monitor` 周期检查（脚本 `server_monitor.sh` / `aqsp doctor`）。
- IC 回流：prod 侧 `fetch_ic_diagnosis.sh` 由主链 `_step_pull_ic_diagnosis` 触发（best-effort）。

**runner（`aqsp-runner` crontab，SSH 31777）**
- IC 诊断：`0 2 * * 1-5`（UTC）= 北京 10:00 工作日 → `ic_diagnosis_runner.sh`（PR #228）。
- 重计算（5y gate / T3 等）：按需由你在本机触发，跑批放 runner。

**本机 Mac（launchd `scripts/launchd/*.plist`）**
- `com.aqsp.morning.plist` / `com.aqsp.daily.plist` / `com.aqsp.closing.plist` 三套蓝图（对应盘中/收盘/监控）。
- 安装：`scripts/install_server_cron.sh` / `install_coldstart_cron.sh` / `install_ic_diagnosis_cron.sh`（runner）。

> ⚠️ 生产机 `install_server_cron.sh` 因沙箱限制由你在 Linux 服务器手动执行（AGENTS.md 注）。

---

## 10. 配置与阈值注入（AGENTS.md §3.5）

- 策略阈值**禁止字面量**，一律从 `config/thresholds.yaml` 注入（`strategies/thresholds.py::load_thresholds` 无参默认解析 `<release>/config/thresholds.yaml`，已验证 release 布局安全）。
- 改 `thresholds.yaml` = 红线（需 walk-forward 验证报告），agent 不得自合（AGENTS.md §6.1）。
- 任何「魔法数字」（滑点/手续费/止损倍数）须有出处或 walk-forward 报告链接。

---

## 11. 红线 + 全盘审计发现（2026-09-26 逐行排查结果）

### 11.1 红线（AGENTS.md §5 / architecture.md §9）—— 全库扫描结论
| 红线 | 扫描结果 | 证据 |
|---|---|---|
| R1 未来数据（shift(-N)/中心化 rolling/全期归一化） | **无** | 全 src grep `shift(-N)`/中心化滚动 = 0 |
| R2 裸 `datetime.now()` | **仅注释，无真实调用** | 全 src 仅 `closing_review.py:621` 注释提及；合法时钟唯一 = `core/time.py::now_shanghai` |
| R3 策略阈值字面量 | 受控 | 阈值走 `thresholds.yaml`，`load_thresholds` 解析 release 内 config |
| R4 静默失效 | **存在（见 11.3）** | filters_lethal 历史空转已部分治理 |
| R5 读/写 fallback 不同源 | **已全部收敛（PR #232，commit 785c3210）：13 处 + closing_review 统一走 `aqsp.core.runtime.runtime_data_root`，绝不回落 /tmp** | 见 11.2 |
| R6 异常吞没 | **存在（见 11.4）** | 优化/自进化模块 `except: score=-inf` |
| R7 交易/下单逻辑 | **无** | 全 src 无下单代码 |
| R8 硬编码 secrets | **无** | grep 无 token/password 硬编码 |

### 11.2 R5 `/tmp` 回落不对称（已全部收敛，PR #232 / commit 785c3210）

原模式 `os.environ.get("AQSP_RUNTIME_DATA_ROOT") or tempfile.gettempdir()` 在未设 env 时回落 `/tmp`，而自动链路 entrypoint 恒设 env 走 `/opt/aqsp/data`，导致裸 CLI / 非 entrypoint 入口「写 /tmp、读 repo 根」的静默失效。

- **已全部修复（PR #232，squash commit `785c3210b66f469749e88b60c6032164ab5bed3e`，已发版 prod）**：新增单一来源 `aqsp.core.runtime.runtime_data_root`（设 env 绝对路径用它，否则回落 repo/release 根，**绝不 /tmp**），全库收敛至该 helper：
  - `briefing/closing_review.py` 读侧（委托 helper）；
  - 9 个 data 生产方：`data/dividend_plan.py`、`data/announcement.py`、`data/cls_news.py`、`data/earnings_forecast.py`、`data/lockup.py`、`data/holder_num.py`、`data/longhubang.py`、`data/concept_board.py`、`data/suspend_resume.py`；
  - 3 个 filters_lethal 消费方：`holder_count.py`、`announcement_keyword.py`、`lockup_release.py`；
  - `features/event_calendar.py:89`。
- **实测证据（prod，release 785c3210）**：`runtime_data_root()` 未设 env → 返回 release 根（`/opt/aqsp-releases/785c3210...`），`is /tmp? → False`；设 env=`/opt/aqsp/data` → 返回 `/opt/aqsp/data`；`closing_review._factor_ic_runtime_root()` 未设 env → 返回 release 根。新增 `tests/test_runtime_data_root.py`（5 例）覆盖 env 设/未设/相对路径回落。
- **性质**：多数成对（生产方=消费方）本就同源，不属真实不对称；真正的单点读侧不对称（closing_review）与全部 latent 债已一并消除，全代码库不再有任何 `tempfile.gettempdir()` 作为运行时数据根回落。

### 11.3 R4 静默失效（「有产出方但无读取方 / 过滤器空转」）
- **(A) 排雷过滤器**（filters_lethal）：历史因 `pit_cache` 数据产出方缺位 → 过滤器从未生效。PR #225/#226 接入产出链路 + `FilterResult.data_missing` 响亮告警（单遍收集 + pit_cache 缺省）。**✅ 已收口（PR #234，2026-09-26 合并+发版 prod）**：`daily_pipeline` 新增 best-effort 步骤 `_step_refresh_risk_datasources`，在「数据更新」后调度 `aqsp.data.{holder_num,announcement,lockup}.load()` 落盘 `pit_cache/{holder_count,announcements,lockup}.csv`；过滤器读不到时降级为 `data_missing=True`（响亮）而非静默过。prod 实测落盘 holder_count 4095 / announcements 10819 / lockup 463 行。**仍建议**：日后补「产出方 → 消费方」连通性 CI 断言，防再静默。
- **(B) pit_cache 事件/风险源**：`fetch_*.py` 产出方无调度方；PR-E 是首个读取方但 `enabled=False` 且无调度 → **整条链仍未激活**（待办）。
  - **✅ 数据半边已收口（PR #234）**：同一步骤调度 `aqsp.data.longhubang.load()` 落盘 `pit_cache/longhubang.csv`，与 `lockup.csv` 一起供 `event_calendar.from_cache()` 只读消费（prod 实测 longhubang 299 / lockup 463 行）。
  - 🔴 **策略半边未激活**：`EventDrivenStrategy` 仍 `enabled=False`（walk-forward 门控，不在 `config/*.yaml`）⇒ 即便数据就绪，事件日历也未被任何策略消费。**启用属产品决策，本次未擅翻**；启用即生效（数据已就绪）。

### 11.4 R6 异常吞没（仅记录，未修）
`optimizer/param_optimizer.py:80/200/251/261`（`except Exception: score = -inf` 掩真实评估失败）、`strategies/auto_evolution.py:808/834`、`strategies/adaptive_evolution.py:345/456/477`、`strategies/auto_factor_mining.py:419/451`、`web/data_provider.py:212/1301`、`monitor/checker.py:776`、`ledger/base.py:659`、`features/event_calendar.py:325`、`strategies/event_driven.py:189`。
> 注：`cli.py` 大量 `except Exception as exc`（多带 `as exc` 已记录日志）属 CLI/IO 边界，非「吞成哨兵值」，不算 R6 风险。

---

## 12. 已知问题 / 待办 / 开放分支

**T3 / walk-forward 主线（记忆备查）**
- T3 双窗口验证已收官（09-24）：方案 A（htf+mr 换 mom+tr）判决=**不成立**（方向不一致：3y 持平 / 5y 退步）。引用只引 `outputs/T3_双窗口对比结论_2026-09-24.md` §三/§五，**严禁引 §四**（旧「3y=改善」是 best-of-8 vs single 跨 h 轴误读 #194）。
- R3 alpha 赤字（momentum 族显著反向）属策略面待解决（open issue #199 最高优先），与机制 bug 区分。

**开放 issue**：#199（策略研究：DSR/PBO 双失败=过拟合）、#149（盘中兜底源单波次 0 覆盖）。

**待处理分支**：`chore/docs-cleanup`、`feat/event-driven-real-events`、`feat/event-data-fetchers`。

**静默失效待办**（§11.3）：排雷链「产出方→消费方」CI 连通性断言（防再静默）；`pit_cache` 事件/风险源**策略半边**激活（PR-E `EventDrivenStrategy` 启用 + 调度，数据半边已于 PR #234 收口）。

**最高价值项（记忆标记）**：一组「静默失效」建议一个 PR 切 4 commit（filters_lethal 读取方 + pit_cache 调度方）。**✅ 已于 PR #234（2026-09-26 合并+发版）收口数据产出方调度**；仅余 CI 连通性断言（建议项）与 PR-E 策略启用（产品决策，未擅翻）。

---

## 13. 快速上手（开发/测试/发布）

```bash
# 开发环境（本机）
pip install -e ".[dev]"            # 或 pyproject 对应 extra
pytest -m 'not live'               # 默认套件（排除联网）
pytest tests/test_closing_review.py tests/test_daily_pipeline.py -q  # 定向

# CLI 试用
python -m aqsp.cli screen --symbols 600519,300750 --mode close --limit 20
aqsp run --mode close --source auto --max-universe 100 --notify

# 提交流程（AGENTS.md §6）
#   1) 读 architecture.md + 本文件 §4 对应子包
#   2) 拉分支 feat/<x> / fix/<x>，写代码+测试
#   3) ruff check . + pytest 全绿（CI 本地门禁等价）
#   4) 提 PR（描述含 做了什么/为什么/风险/怎么验证，≤300 行）
#   5) §4 清单自审 → agent 直接 squash 合（CI 绿 + mergeable_state=clean + 未触红线）
#   6) 合并 ≠ 发布；上线 prod/runner 改动须老大授权

# 发布到 prod（agent 自提 PR，部署须老大确认）
bash scripts/deploy_immutable_release.sh --skip-frontend-build --skip-restart  # 在 prod 上
systemctl restart aqsp-vibe-research-api aqsp-vibe-research-preview
curl http://127.0.0.1:8900/api/health   # 看 release_sha == 目标 commit
```

---

## 14. 修改某模块时的「查证清单」（避免重复踩坑）

1. 该模块读写 `pit_cache/` 哪条路径？env 优先还是 /tmp 回落？（见 §11.2）
2. 时钟是否 `now_shanghai()`？有无裸 `datetime.now()`？（R2）
3. 阈值是否走 `thresholds.yaml`？有没有字面量？（R3 / §10）
4. 是否引入 look-ahead（shift(-N)/中心化 rolling/全期归一）？（R1）
5. 异常是否被吞成哨兵值、掩盖真实失败？（R6 / §11.4）
6. 是否静默失效（产出方无人读 / 过滤器空转）？（R4 / §11.3）
7. 是否写未来数据、是否覆盖打分、是否塞下单？（R1/R7/LLM）
8. CI 全绿 + 单测覆盖正常/边界/错误三类？（AGENTS.md §3.8）
9. 上线后 `/api/health` 的 `release_sha` 是否等于目标 commit？

---
*本文档与 `docs/architecture.md`（宪法）、`AGENTS.md`（协作硬约束）、`docs/agent-operating-boundaries.md`（本地/GitHub/服务器职责）互为补充。任何条款冲突以 `architecture.md` §1 为准。*
