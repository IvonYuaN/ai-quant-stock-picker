# AQSP 模型接手说明

更新时间：2026-06-03  
当前范围：A 股主链。港股、美股、券商自动交易、截图 OCR 等扩展方向暂不开发，除非仓主明确确认。

这份文档给后续模型/agent 接手项目用。不要从聊天记录猜状态，按本文和仓库文件验证。

## 0. 接手前必须先读

1. `docs/architecture.md`：项目宪法，优先级最高。
2. `AGENTS.md`：编码约束、测试要求、红线。
3. `docs/simple-server-mode.md`：服务器、cron、通知、LLM、doctor、monitor。
4. `tests/README.md`：测试分层。
5. `README.md`：GitHub 展示和用户入口。

硬约束：

- 不自动下单，不接券商交易接口。
- LLM 不参与核心打分，只能用于摘要、解释、辩论文本。
- 不上传 `.env`、token、ledger、数据库、日志、Dashboard runtime 文件。
- 策略阈值从 `config/thresholds.yaml` 注入，改阈值必须升版本并说明验证依据。
- 任何回测/学习不能使用未来数据，不允许 `shift(-N)`、中心化 rolling、全期归一化。
- 服务器结果优先于本地猜测。用户贴服务器输出时，先对照输出定位。

## 1. 当前已实现内容

### 1.1 数据源与数据健康

- `eastmoney`：公网补充源，用于临时最新日线、分时、实时 quote，不作为生产默认依赖。
- `sqlite_db`：私有历史数据库源，生产候选/ledger 路径通常为 `/opt/market-data/astocks_raw.db`；qfq/hfq 只用于展示或历史辅助。
- `tdx_vipdoc`：本地/服务器私有通达信数据源，可做本地全市场历史补充。
- `baostock`：可登录，主要用于历史/PIT 财务补充。
- `tushare`：已接入交易日历、指数成分、财报披露日；需要 `TUSHARE_TOKEN`。
- `akshare`：已接入但短线主链不建议高频依赖，避免接口限流；适合作为研究补充和字段补数。
- `sina`、`tencent`、`efinance`、`mootdx`：作为可选 fallback / 研究数据源。
- `aqsp sources`、`aqsp doctor`、`scripts/server_doctor.py` 能输出数据源 readiness/auth 状态。
- `source_health` 会记录源成功/失败，报告和通知会带数据源健康状态。

### 1.2 主策略与选股链

- 主 CLI：`python3 -m aqsp run` / `python3 -m aqsp.cli run`。
- 主策略框架：`src/aqsp/strategy.py` 多因子评分。
- 已接入策略族：
  - RPS 相对强度 / 动量。
  - 放量突破。
  - 均线缩量回踩。
  - 碗口反弹。
  - 低波趋势。
  - 早盘打板。
  - 尾盘溢价。
  - 多因子轮动。
  - 均值回归、质量、价值、成交量等研究策略模块。
- `internet_strategies.py` 负责部分互联网项目经验映射后的策略信号。
- `config/thresholds.yaml` 是阈值冻结源。
- `screen_universe` 输出 `PickResult`：候选、评分、评级、理由、风险、止损止盈、仓位建议。

### 1.3 风控和主链裁决

- 数据新鲜度：`assert_fresh_data`，数据过期直接失败。
- T+1：`universe.t1_filter` 避免昨日已买标的重复进入。
- 不可成交：ledger 校验中处理涨跌停/停牌，`not_executable` 不进入胜率。
- 排雷过滤：`filters_lethal` 包括公告关键词、股东户数、限售解禁等框架。
- 板块集中度：`portfolio.sector_check`。
- 候选相关性：`portfolio.correlation`。
- Portfolio Manager：`portfolio.manager.apply_portfolio_manager`，会根据多 Agent、板块集中、相关性调整排序/降级。
- 动态止损：`risk.dynamic_stop`。
- 熔断：`risk.circuit_breaker`。

### 1.4 Ledger、验证、学习、自进化

- 正式 ledger：`data/predictions.jsonl`。
- 虚拟盘 ledger：`data/paper_trades.jsonl`。
- 每次 `aqsp run` 先验证到期预测，再追加今日候选。
- 冷启动期内跳过策略衰减告警和激进学习，避免样本太少导致误调。
- `ledger.learner.PerformanceLearner` 会按历史表现计算策略权重。
- `strategies.auto_evolution` 已接入收盘链路，可运行但默认受配置控制。
- `scripts/daily_pipeline.py` 已包含：
  - 数据更新。
  - 策略运行。
  - 收盘复盘。
  - 预测验证。
  - 虚拟盘同步。
  - 自适应学习。
  - 策略自进化。
  - 报告生成。
  - Dashboard 刷新。
  - 数据清理。

### 1.5 报告、Dashboard、通知

- Markdown 报告：`reports/latest.md`。
- CSV 输出：`reports/latest.csv`。
- 每日简报：`reports/briefing.md`。
- 收盘复盘：`reports/closing_review.md`。
- 当前 Dashboard：Streamlit `src/aqsp/web/dashboard.py`（本地 `8501`，生产由 Nginx 反代）。`dist/dashboard/index.html` 和 `aqsp.db` 仅是离线归档产物。
- 通知：
  - Server 酱：`SERVERCHAN_SENDKEY`。
  - Telegram / 企业微信 / 飞书 / 通用 Webhook。
  - 收盘汇总默认 `AQSP_NOTIFY_MODE=summary`，避免 fanout 轰炸。
- 最近修复：
  - 主链输出名称丢失：已修复 `eastmoney` 名称透传，并在 `run/screen/briefing` 做名字兜底。
  - `300750 300750` 这类重复名称：已统一 `presentation.format_symbol_name`。
  - `风险` 标签被终端/复制链吞字：已改为 `风险提示`。
  - 收盘复盘部分 emoji 标题在宝塔终端复制丢字：已去掉关键标题 emoji。

### 1.6 多 Agent 和 LLM

- 角色注册表：`src/aqsp/briefing/agent_roles.py`。
- 辩论主链：`src/aqsp/briefing/debate.py`。
- 支持中文角色描述、英文 ID、emoji、职责说明。
- 默认角色：
  - `bull`
  - `bear`
  - `risk_control`
  - `sector_leader`
  - `policy_sensitive`
  - `northbound`
- 运行时配置：
  - `AQSP_ENABLE_DEBATE`
  - `AQSP_DEBATE_ENABLE_LLM`
  - `AQSP_DEBATE_MAX_ROUNDS`
  - `AQSP_DEBATE_LANGUAGE`
  - `AQSP_DEBATE_ROLES`
  - `AQSP_DEBATE_ROLE_LLM`
  - `AQSP_DEBATE_ROLE_PROVIDERS`
  - `AQSP_DEBATE_ROLE_MODELS`
- Provider 已接入：
  - GLM：默认模型 `glm-4.7-flash`。
  - Agnes：默认模型 `agnes-2.0-flash`。
  - SiliconFlow：保留配置，但可忽略，避免扣费风险。
  - OpenAI / Anthropic / Qwen / DeepSeek / Custom OpenAI-compatible。
- 服务器最近状态：
  - Agnes 连通正常。
  - GLM 可用但可能遇到 429 rate limit。
  - Server 酱可 HTTP 200。

### 1.7 服务器自动化

主要脚本：

- `scripts/server_sync_and_run.sh`：服务器在 clean checkout 时执行 fast-forward 同步；检测到受控 runtime overlay 时只运行任务，不覆盖服务器改动。
- `scripts/sync_runtime_files_to_server.py`：只同步显式文件批次，先备份、校验 SHA256，失败只回滚本批文件，并维护 overlay 来源元数据。
- `scripts/daily_pipeline.sh`：完整收盘跑批。
- `scripts/intraday_refresh.sh`：盘中轻量刷新。
- `scripts/install_server_cron.sh`：安装/去重 cron。
- `scripts/server_monitor.sh`：监控告警。
- `scripts/server_status.sh`：服务器状态概览。
- `scripts/init_server_runtime.sh`：初始化 runtime 空文件。
- `scripts/server_doctor.py`：运行态自检。

当前推荐 cron：

- 工作日北京时间 `09:40-11:59` 每 10 分钟盘中推荐。
- 工作日北京时间 `13:00-14:59` 每 10 分钟盘中推荐。
- 工作日北京时间 `18:00` 收盘完整跑批。
- 工作日每 15 分钟服务器监控。

## 2. 当前服务器参考配置

服务器目录：

```text
/opt/aqsp
/opt/aqsp/.env
/opt/aqsp/.venv
/opt/market-data/astocks_raw.db
```

关键 `.env`：

```bash
AQSP_SOURCE=sqlite_db
AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_raw.db
AQSP_ALLOW_ONLINE_FALLBACK=false
AQSP_SYMBOLS=
AQSP_MODE=close
AQSP_LIMIT=10
AQSP_MAX_UNIVERSE=0
AQSP_MIN_AVG_AMOUNT=50000000
AQSP_MAX_DATA_LAG_DAYS=3
AQSP_NOTIFY=true
AQSP_GATE_NOTIFY=false
AQSP_NOTIFY_MODE=summary
AQSP_ENABLE_DEBATE=false
AQSP_DEBATE_ENABLE_LLM=false
AQSP_ENABLE_AUTO_EVOLUTION=true
LLM_PROVIDER=agnes
AGNES_MODEL=agnes-2.0-flash
GLM_MODEL=glm-4.7-flash
```

服务器验证命令（不覆盖服务器改动）：

```bash
cd /opt/aqsp
git status --short --branch --untracked-files=all
.venv/bin/python scripts/sync_runtime_files_to_server.py --verify-overlay
bash scripts/server_sync_and_run.sh; code=$?; echo "PIPELINE_EXIT_CODE=$code"
```

### 1.8 服务器版本收敛责任

`origin/main` 是提交代码的唯一版本源；服务器 `/opt/aqsp/.state/runtime-sync-overlay.json` 只是受控的显式文件 overlay，不是第二个开发分支。任何 overlay 都必须能回答四个问题：来自哪个 `commit`、同步时 worktree 是否 dirty、对应哪个 `sync_id`、失败时恢复哪个 `backup_path`。

分批收敛顺序：

1. **冻结与盘点**：只读记录本地和服务器 `HEAD`、`git status`、overlay 文件数/更新时间/哈希漂移；不执行 `reset`、`checkout`、`clean`，不删除服务器数据。
2. **运行时产物隔离**：`.env`、`.venv`、数据库、ledger、reports、logs、`.state` 和 `runtime-backups` 不进入代码同步批次；服务器既有 dirty 文件逐项归类，未知项先阻断。
3. **小批同步**：每批只传一个可回滚的功能闭包，使用 `scripts/sync_runtime_files_to_server.py` 的显式文件参数；不要把当前本地 worktree 全量打包上传。
4. **验证与放行**：先看备份路径和 SHA256，再运行 `--verify-overlay`；验证未通过时保持任务阻断，不把“runner 能启动”当作版本收敛完成。
5. **回滚**：以该批次 manifest 的 `backup_path` 和 `managed_files` 为边界恢复，恢复后再次做 overlay 校验；不要用全仓库 `git reset --hard` 或 `git clean` 代替回滚。

2026-07-13 审计基线：服务器 `HEAD` 与 `origin/main` 均为 `eb0166b`，overlay 管理 101 个文件且远端文件哈希与 manifest 一致；但旧 manifest 只有 `managed_files`、`file_hashes`、`updated_at`，缺少来源 commit、同步 ID 和备份指针。首次收敛批次必须重写该 manifest；在此之前，overlay 只能证明“文件相同”，不能证明“来源单一”。

自检命令：

```bash
cd /opt/aqsp && .venv/bin/python3 -m aqsp doctor --probe-auth --probe-llm
```

最近已知服务器结果：

- `PIPELINE_EXIT_CODE=0`。
- 完整跑批 `10/10` 步骤成功。
- `baostock` 登录成功。
- `tushare` token 校验成功。
- `agnes` LLM 探测成功。
- `glm` 可能因为速率限制返回 429。
- 主链候选名称已正常显示：如 `300750 宁德时代`、`600036 招商银行`、`000001 平安银行`、`601318 中国平安`。

## 3. 计划实现内容

### 3.1 短线主链优先级

- 优化数据源路由：短线实时优先 `eastmoney`，但要增加失败 fallback 的可解释报告。
- 增强盘中 `intraday_refresh`：减少重复通知，只推“新晋候选/排名突变/风险突变”。
- 优化 Server 酱通知版式：参考 `ZhuLinsen/daily_stock_analysis`，做更强的信息分层。
- Dashboard 清理重复信息：保留主链摘要、候选证据链、复盘、数据源、监控，不堆旧字段。
- 增加“候选状态生命周期”：新晋、延续、降级、移出、不可成交。
- 对观察池增加“为什么不能执行”的明确原因：评分、风险、PM 降级、数据不足、双门 gate。

### 3.2 数据和回测

- 生产短线扫描保持 `AQSP_SYMBOLS=` 和 `AQSP_MAX_UNIVERSE=0`，小池股票列表只用于本地 smoke test 或手工观察。
- walk-forward 要拆成“短线观察池回测”和“中长线策略回测”，不能混用数据源和标的池。
- 对接更稳定的 PIT 财务披露日和指数成分快照。
- 记录每次 walk-forward 的数据源、标的池、覆盖率、不可成交数、DSR/PBO。
- 修复/复核历史归档中提到的 PBO CSCV 实现偏差，防止 gate 失真。
- 增加真实涨跌停、停牌、复权因子的 point-in-time 审计。

### 3.3 多 Agent 和投资大师链

- 可以参考 `virattt/ai-hedge-fund` 的角色思想，但不能直接让 LLM 改核心分数。
- 增加“人格投资大师”作为解释层/观点层，不进入打分层。
- 增加 `portfolio_manager` 主链最终裁决说明：
  - 组合集中度。
  - 相关性拥挤。
  - 右侧确认。
  - 风险收益比。
  - 是否仅观察。
- 角色级模型分配：
  - 低成本默认 Agnes。
  - GLM 作为中文摘要/补充。
  - 风控角色可关闭 LLM，用规则引擎输出。
- 加入 LLM rate limit 退避和 provider fallback，避免 GLM 429 影响整体跑批。

### 3.4 中长期扩展

这些只记录，不主动开发：

- 中长线基本面/质量/估值策略。
- 行业轮动与政策主题。
- 港股、美股数据源和交易日历。
- QMT/券商接口。
- 私有对象存储同步 ledger。
- 更细粒度 portfolio sizing。

在仓主明确确认前，不要把港美股开发进主链。

## 4. 已知 bug / 风险记录

### 4.1 已修复但要防回退

- `eastmoney` 日线标准化把 `name` 写成 `symbol`，导致报告/briefing 出现 `300750` 或 `300750 300750`。
  - 修复：`eastmoney_source` 保留 payload name；`cli._enrich_pick_names` 做 frame/SQLite 兜底；统一 `presentation.format_symbol_name`。
  - 防回退测试：`tests/test_data_source.py`、`tests/test_cli_notify.py`、`tests/test_integration.py`。
- briefing 主链排序和首位候选不一致。
  - 修复：按 score 排序后生成主链总览。
- auto evolution runtime 失败。
  - 修复：收盘链路已能跑通并输出“当前无需进化”。
- monitor 因可选 cache 缺失触发 stale_data 告警并导致 GitHub 邮件轰炸。
  - 修复：可选 cache 缺失不再作为 critical。
- Server 酱通知内容过碎、重复、噪音大。
  - 修复：收盘总览默认 summary，一条主通知。
- `风险` 标签在部分终端/复制链路显示成 `险`。
  - 修复：统一输出 `风险提示`。

### 4.2 仍需关注

- 服务器数据源最新跑批只获取到 4/5 只标的，可能是 `600519` 当前行情接口/缓存覆盖问题，需要单独查源返回。
- `GLM` 可能出现 429 rate limit，应增加节流和 fallback，不要让它影响主链。
- 当前 walk-forward gate 最近实际结果不通过，短线策略不能因日报跑通就当作可实盘。
- 私有 SQLite 是历史参考，不等于短线最新数据源；短线必须优先新数据。
- reports 目录里有历史旧报告，可能保留了旧格式如 `300750 300750`；不要把旧报告当当前 runtime 结果。
- 宝塔终端/微信复制可能对 emoji/宽字符显示不稳定，服务器日志尽量使用纯文本关键标签。
- Dashboard 前端仍可能有重复内容、信息层级不够干净，后续应继续整理。
- GitHub Actions 可能因为 runner/action 版本变化发 warning，需要定期更新 workflow action 版本。

## 5. 推荐接手路线

### 第一步：确认当前绿线

```bash
git status -sb
ruff check .
python3 -m pytest -q
```

如果全量测试失败，不要先开发新功能，先修 CI。

### 第二步：服务器验证

```bash
cd /opt/aqsp
git status --short --branch --untracked-files=all
.venv/bin/python scripts/sync_runtime_files_to_server.py --verify-overlay
bash scripts/server_sync_and_run.sh; code=$?; echo "PIPELINE_EXIT_CODE=$code"
```

必须检查：

- `PIPELINE_EXIT_CODE=0`
- 跑批 `10/10` 成功
- 报告里的候选名称不是纯代码
- briefing 里的 `风险提示` 完整显示
- Server 酱只收到一条收盘总览，除非显式 `fanout`
- 双门未放行默认不单独推手机；只有显式 `AQSP_GATE_NOTIFY=true` 才允许发送 `通知未放行-YYYY-MM-DD`

### 第三步：优先修用户可见主链

优先级顺序：

1. 数据源可靠性和数据新鲜度。
2. 通知和报告是否清楚、干净、可读。
3. Dashboard 是否减少重复信息。
4. walk-forward / gate 是否可信。
5. 多 Agent 解释层。
6. 新策略和长期扩展。

不要先做炫技型 LLM 或新市场扩展。

## 6. 常用命令

本地：

```bash
python3 -m aqsp.cli screen --symbols 600519,300750 --mode close --limit 20
python3 -m aqsp.cli run --source eastmoney --symbols 600519,300750,000001,601318,600036 --mode close --limit 10 --skip-validation
python3 -m aqsp.cli briefing --ledger data/predictions.jsonl --output reports/briefing.md
python3 -m pytest -q tests/test_briefing.py tests/test_report.py tests/test_closing_review.py
python3 -m pytest -q
```

服务器：

```bash
cd /opt/aqsp && .venv/bin/python3 -m aqsp doctor --probe-auth --probe-llm
cd /opt/aqsp && AQSP_MONITOR_NOTIFY=true bash scripts/server_monitor.sh
cd /opt/aqsp && bash scripts/server_monitor.sh
cd /opt/aqsp && bash scripts/install_server_cron.sh
cd /opt/aqsp && crontab -l
```

Git 推送：

```bash
export HTTP_PROXY="http://127.0.0.1:7890" HTTPS_PROXY="http://127.0.0.1:7890" ALL_PROXY="socks5://127.0.0.1:7890"
git push origin HEAD:main
```

## 7. 交接原则

- 不要问“要不要继续”，如果属于当前链路就直接做完。
- 可逆的小实现细节直接判断，不把选择题丢给仓主。
- 每次交付要包含：代码、测试、推送、服务器验证命令。
- 用户贴服务器输出时，先读完整输出，不要只看前半截。
- 如果遇到同类 bug 反复出现，抽公共函数或公共文档，不要局部打补丁。
- 结果导向：用户关心服务器实际跑出来的内容，不关心本地解释。
