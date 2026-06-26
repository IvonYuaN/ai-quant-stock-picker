# 最简单服务器模式

目标只有 4 条：

1. 本地只负责开发和 `git push`
2. 云服务器自动 `git pull`
3. 服务器本地保存 `.env`、数据库和运行结果，不被更新覆盖
4. 通过备案域名 `https://lh.ifidy.cn` 看 Dashboard

## 这套模式怎么理解

```text
Mac 本地开发
-> push 到 GitHub
-> 宝塔计划任务执行 scripts/bt_task.sh
-> bt_task.sh 同步代码并运行指定任务
-> 产出 reports/、data/、dist/dashboard/
-> aqsp-dashboard.service 读取落盘结果
-> Nginx / 宝塔反代到 https://lh.ifidy.cn
```

关键点：

- `.env` 已在 `.gitignore` 中，不会被 Git 覆盖
- `data/*.db`、`data/*.jsonl`、`dist/`、`logs/` 也不会被 Git 覆盖
- 服务器只要别手改受 Git 管理的代码文件，就可以一直自动更新
- 当前推荐模式是本地 raw 数据负责生产候选和 ledger；`astocks_qfq.db` 只做展示或历史辅助

## 服务器上需要长期保留的内容

推荐目录：

```text
/opt/aqsp                 # Git 仓库代码
/opt/aqsp/.env            # 服务器自己的配置
/opt/aqsp/.venv           # Python 虚拟环境
/opt/market-data/         # 你的历史数据库
```

`.env` 示例：

```bash
AQSP_SOURCE=sqlite_db
AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_raw.db
AQSP_ALLOW_ONLINE_FALLBACK=false

AQSP_SYMBOLS=
AQSP_WALKFORWARD_SYMBOLS=000915,000921,000923,000930,000932,000937,000938,000950,000951,000958
AQSP_RESEARCH_ENGINE=auto
AQSP_MODE=close
AQSP_LIMIT=10
AQSP_MAX_UNIVERSE=0
AQSP_MIN_AVG_AMOUNT=50000000
AQSP_MAX_DATA_LAG_DAYS=3

AQSP_ENABLE_ONLINE_FACTORS=false

AQSP_LEDGER=data/predictions.jsonl
AQSP_PAPER_LEDGER=data/paper_trades.jsonl
AQSP_REPORT=reports/latest.md
AQSP_OUTPUT_CSV=reports/latest.csv
AQSP_DASHBOARD_HTML=dist/dashboard/index.html
AQSP_DASHBOARD_DB=dist/dashboard/aqsp.db

AQSP_INTRADAY_LEDGER=data/intraday_predictions.jsonl
AQSP_INTRADAY_REPORT=reports/intraday_latest.md
AQSP_INTRADAY_OUTPUT_CSV=reports/intraday_latest.csv
AQSP_INTRADAY_DASHBOARD_HTML=dist/dashboard/index.html
AQSP_INTRADAY_DASHBOARD_DB=dist/dashboard/aqsp.db

AQSP_DEPLOY_DASHBOARD=false

TUSHARE_TOKEN=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
SERVERCHAN_SENDKEY=
GLM_API_KEY=

AQSP_NOTIFY=false
AQSP_NOTIFY_MODE=summary
AQSP_ENABLE_DEBATE=false
AQSP_DEBATE_ENABLE_LLM=false
AQSP_DEBATE_MAX_ROUNDS=2
AQSP_DEBATE_LANGUAGE=zh-CN
AQSP_DEBATE_ROLES=bull,bear,risk_control,sector_leader,policy_sensitive,northbound
AQSP_DEBATE_ROLE_LLM=
AQSP_DEBATE_ROLE_PROVIDERS=
AQSP_DEBATE_ROLE_MODELS=
AQSP_ENABLE_AUTO_EVOLUTION=false
```

补充说明：

- `GLM_API_KEY` 用于智谱；`LLM_PROVIDER=glm` 时默认走 `GLM-4.7-Flash`。
- `SERVERCHAN_SENDKEY` 配好后，收盘总览、监控告警、复盘摘要都可以直接推到 Server酱。
- `AQSP_NOTIFY=true` 后，`bt_task.sh daily` 会在收盘主链路里发送汇总通知。
- 命令入口统一推荐用 `aqsp run`；仓库仍兼容旧别名 `aqsp run-scheduled`，方便服务器老脚本平滑过渡。
- `AQSP_NOTIFY_MODE=summary` 时，收盘链路默认只发 1 条“收盘总览”；如果你想恢复每个步骤各发各的，改成 `fanout`。
- `AQSP_MAX_UNIVERSE=0` 表示短线生产扫描不截断；50/100/300 只能用于本地 smoke test，不能作为上线运行配置。
- `AQSP_SYMBOLS` 给小范围手工观察用；生产日报/短线扫描应保持空值并配合 `AQSP_MAX_UNIVERSE=0` 走全市场可用池。`AQSP_WALKFORWARD_SYMBOLS` 单独给 walk-forward，用历史库里覆盖完整的票，别混用。
- `AQSP_RESEARCH_ENGINE` 现在支持 `auto / builtin / akquant`。当前 `akquant` 已接入原生单窗口执行：AQSP 负责滚动窗口编排、选股和报告，AKQuant 负责窗口内回测撮合；如果服务器没装 `akquant`，仍可按 compat 逻辑自动回退到 builtin。
- `AKShare` 适合做研究补充和字段补数，不建议当短线高频主源；现在运行时会把它放在在线混合源靠后位置，并对全市场实时快照做最小间隔与失败冷却。
- 选股推荐通知仍受 walk-forward 双门 gate 保护；收盘复盘、监控告警、策略自进化通知不依赖这道 gate。
- `AQSP_ENABLE_DEBATE=false` 表示默认不跑多 agent 讨论；要开就改成 `true`。
- `AQSP_DEBATE_LANGUAGE=zh-CN` 现在是运行时配置，不再写死在代码里。
- `AQSP_DEBATE_ROLES` 现在走统一角色注册表，前端展示和后端角色身份共用同一套中文名、英文名、emoji、描述，不会再出现页面和运行时不一致。
- 现在支持角色级运行配置：`AQSP_DEBATE_ROLE_LLM`、`AQSP_DEBATE_ROLE_PROVIDERS`、`AQSP_DEBATE_ROLE_MODELS`。比如可以让 `bull` 走 Agnes、`risk_control` 关闭 LLM、`northbound` 走 GLM。
- 当前多 agent 讨论主链路是多角色规则引擎，LLM 主要用于摘要增强，不会直接改写核心选股分数。
- `AQSP_ENABLE_AUTO_EVOLUTION=true` 后，收盘链路会额外执行一次策略自进化检查。
- `LLM_PROVIDER=agnes` 时会直接走 Agnes AI 官方 OpenAI 兼容端点，默认模型 `agnes-2.0-flash`。
- 如果你改用 `LLM_PROVIDER=siliconflow`，建议同时设置 `SILICONFLOW_FREE_ONLY=true`，只允许免费白名单模型，避免意外扣费。
- 现在支持 provider 专属模型变量：`GLM_MODEL`、`QWEN_MODEL`、`AGNES_MODEL`、`SILICONFLOW_MODEL`、`OPENAI_MODEL`、`ANTHROPIC_MODEL`、`CUSTOM_MODEL`。这样切换 provider 时不会被旧的全局 `LLM_MODEL` 串台。

## 自动更新脚本

仓库内置：

```bash
bash /opt/aqsp/scripts/server_sync_and_run.sh
```

它会做 3 件事：

1. 检查服务器代码目录是否干净
2. `git pull --ff-only origin main`
3. 运行 `AQSP_RUNNER_SCRIPT` 指定的脚本；生产入口由 `bt_task.sh` 显式设置

如果服务器上存在受 Git 管理的本地改动，它会直接停下，不会乱覆盖。

## 宝塔面板计划任务（生产推荐）

生产入口统一放在 **宝塔面板 -> 计划任务**。本地 Mac 的 `launchd` 只保留为历史兼容方案，不再作为生产定时来源。

统一命令入口：

```bash
/bin/bash /opt/aqsp/scripts/bt_task.sh <intraday|midday|daily|coldstart|monitor|news|status>
```

建议在宝塔里配置 **6 条自动任务 + 1 条手动自检命令**：

| 任务名 | 推荐时间 | 宝塔脚本内容 | 作用 |
|---|---:|---|---|
| `AQSP-盘中刷新` | 工作日 `09:35-11:30`、`13:05-14:57` 每 10 分钟 | `/bin/bash /opt/aqsp/scripts/bt_task.sh intraday` | 刷新盘中候选和看板，写独立盘中产物，不污染正式 ledger |
| `AQSP-午盘分析` | 工作日 `12:05` | `/bin/bash /opt/aqsp/scripts/bt_task.sh midday` | 中午固定复核上午走势、候选和大盘状态 |
| `AQSP-消息面雷达` | 工作日 `08:35`，周末 `09:05` | `/bin/bash /opt/aqsp/scripts/bt_task.sh news` | 盘前/周末复核高影响消息、涨价链、政策、风险事件 |
| `AQSP-收盘主链路` | 工作日 `18:00` | `/bin/bash /opt/aqsp/scripts/bt_task.sh daily` | 完整收盘复盘、纸面验证、简报、通知和看板刷新 |
| `AQSP-冷启动补样本` | 工作日 `19:40` | `/bin/bash /opt/aqsp/scripts/bt_task.sh coldstart` | 收盘主链路结束后再补历史库和冷启动样本，避免互斥跳过 |
| `AQSP-服务器监控` | 工作日每 `15` 分钟 | `/bin/bash /opt/aqsp/scripts/bt_task.sh monitor` | 检查数据、运行态、通知通道；默认只推关键异常 |
| `AQSP-状态自检` | 不建议定时，手动点运行即可 | `/bin/bash /opt/aqsp/scripts/bt_task.sh status` | 临时查看 Git、产物、日志、运行态 |

如果宝塔的“每 N 分钟”不能限制交易时段，也可以让 `intraday` 工作日每 10 分钟跑。`scripts/intraday_refresh.sh` 内部会判断交易时段，非交易时段只记“跳过”，不会污染结果。

`daily` 和 `coldstart` 会共用主锁。如果你在 `daily` 还没跑完时手动触发 `coldstart`，日志出现“正常跳过；这是互斥保护，不是失败”是预期行为。生产建议把 `coldstart` 放到 `19:40`，不要放在 `daily` 附近。

手工验证：

```bash
cd /opt/aqsp
/bin/bash scripts/bt_task.sh status
/bin/bash scripts/bt_task.sh news
/bin/bash scripts/bt_task.sh monitor
/bin/bash scripts/bt_task.sh midday
/bin/bash scripts/bt_task.sh daily
```

查看日志：

```bash
tail -120 /opt/aqsp/logs/bt/bt-daily-$(date +%Y-%m-%d).log
tail -120 /opt/aqsp/logs/bt/bt-news-$(date +%Y-%m-%d).log
tail -120 /opt/aqsp/logs/daily/pipeline-$(date +%Y-%m-%d).log
tail -120 /opt/aqsp/logs/monitor/monitor-$(date +%Y-%m-%d).log
```

通知要生效，服务器 `/opt/aqsp/.env` 至少需要：

```bash
AQSP_NOTIFY=true
AQSP_NOTIFY_MODE=summary
SERVERCHAN_SENDKEY=你的Server酱SendKey
```

监控告警如需推送，再单独开启：

```bash
AQSP_MONITOR_NOTIFY=true
```

消息面雷达如果要启用模型复核，再加：

```bash
AQSP_NEWS_ENABLE_LLM_REVIEW=true
AQSP_NEWS_MAX_LLM_REVIEW_EVENTS=3
AQSP_NEWS_SOURCE_TIMEOUT_SECONDS=4
AQSP_NEWS_TASK_TIMEOUT_SECONDS=300
```

注意：选股推荐通知仍受冷启动 + walk-forward 双门保护；收盘复盘、午盘分析、消息面雷达不依赖这道选股 gate。服务器监控现在默认只记日志，只有设置 `AQSP_MONITOR_NOTIFY=true` 才推送。

## crontab 定时任务（兼容）

如果不用宝塔面板，也可以用 `install_server_cron.sh` 安装同一组生产任务。它仍然调用 `bt_task.sh`，不是另一套路由。

直接执行：

```bash
bash /opt/aqsp/scripts/install_server_cron.sh
```

这条脚本会自动安装并去重这些任务：

- 北京时间 `09:35-11:30` 每 10 分钟跑一次盘中推荐
- 北京时间 `12:05` 跑一次午盘回看
- 北京时间 `13:05-14:57` 每 10 分钟跑一次盘中推荐
- 北京时间 `08:35` 工作日跑一次消息面雷达
- 北京时间 `09:05` 周末跑一次消息面雷达
- 北京时间 `18:00` 跑一次完整收盘复盘
- 北京时间 `19:40` 跑一次冷启动补样本
- 北京时间每 `15` 分钟跑一次服务器监控

如果你想暂时关闭某一类任务，可以带环境变量：

```bash
AQSP_ENABLE_INTRADAY_CRON=false bash /opt/aqsp/scripts/install_server_cron.sh
AQSP_ENABLE_NEWS_CRON=false bash /opt/aqsp/scripts/install_server_cron.sh
AQSP_ENABLE_COLDSTART_CRON=false bash /opt/aqsp/scripts/install_server_cron.sh
AQSP_ENABLE_MONITOR_CRON=false bash /opt/aqsp/scripts/install_server_cron.sh
```

## 冷启动自动化

如果你当前目标是只把 `predictions.jsonl` 的冷启动天数稳定累积到 30，而不是跑整套收盘链路，仓库现在内置了两条专用脚本：

```bash
cd /opt/aqsp
python3 scripts/merge_server_ledgers.py
bash scripts/install_coldstart_cron.sh
```

含义：

- `merge_server_ledgers.py`：把服务器本地 `data/ledger.jsonl` 合并进正式 `data/predictions.jsonl`，按 `(signal_date, symbol, thresholds_version, regime, intended_entry)` 去重，并自动补齐 `signal_day_group`。
- `install_coldstart_cron.sh`：旧的独立冷启动安装器。当前生产推荐直接在宝塔里建 `AQSP-冷启动补样本`，时间放到 `19:40`，避免和 `daily` 互斥。

`coldstart_daily.sh` 会按下面顺序寻找 `update_daily.py`：

1. `AQSP_COLDSTART_UPDATE_SCRIPT`
2. `AQSP_SQLITE_DB_PATH` 同目录下的 `update_daily.py`
3. 仓库内 `A股量化分析数据/update_daily.py`

所以像服务器这种 `AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_raw.db` 场景，会自动尝试 `/opt/market-data/update_daily.py`。

如果服务器不是北京时间，可覆盖 cron 时间：

```bash
AQSP_COLDSTART_CRON_SCHEDULE="30 9 * * 1-5" bash /opt/aqsp/scripts/install_coldstart_cron.sh
```

上面这个例子适合服务器时区为 `UTC`，对应北京时间 `17:30`。

`scripts/intraday_refresh.sh` 默认只在交易时段内工作，并且写入单独的盘中 ledger，不污染正式收盘 ledger。

长任务会自动互斥：

- `daily`、`midday` 和 BT 入口的 `intraday` 会先通过 `server_sync_and_run.sh` 共用主锁，避免同步代码和写产物时互相踩踏。
- `news` 只写独立的 `reports/news_catalysts.md` 和通知，不写正式 ledger；为了不被长主链路挡住，默认不抢主锁。
- `coldstart_daily.sh` 也使用主锁，因为它会补正式冷启动 ledger；如果 `daily` 未结束，它会正常跳过。
- `intraday_refresh.sh` 还会使用盘中独立锁，只保护盘中刷新自身；如果主链路正在运行，BT 入口会先正常跳过。
- `server_monitor.sh` 使用独立监控锁，避免 15 分钟监控任务自己重入。
- 如果上一轮还没跑完，新一轮会直接“正常跳过”，这表示互斥保护生效，不是任务失败。

查看：

```bash
crontab -l
```

服务器状态总览：

```bash
bash /opt/aqsp/scripts/server_status.sh
```

服务器联通自检：

```bash
cd /opt/aqsp && .venv/bin/python3 scripts/server_doctor.py
```

如果你要主动探测数据源登录和 LLM 联通：

```bash
cd /opt/aqsp && .venv/bin/python3 scripts/server_doctor.py --probe-auth --probe-llm
```

这个 doctor 会一次性检查：

- `.env`、虚拟环境、数据库、Dashboard、报告文件是否存在
- `baostock` / `tushare` 鉴权状态
- `GLM` / `Agnes` 等已配置 LLM 是否只是“已配置”还是“真实可连”
- 通知通道是否已配置

首次补齐运行态空文件：

```bash
bash /opt/aqsp/scripts/init_server_runtime.sh
```

异常监控与告警：

```bash
bash /opt/aqsp/scripts/server_monitor.sh
```

默认不推送手机告警；如果要开启监控推送，先打开：

```bash
echo 'AQSP_MONITOR_NOTIFY=true' >> /opt/aqsp/.env
```

开启后默认只推送 `critical` 级别告警；如果要连 `warning` 也推送：

```bash
echo 'AQSP_MONITOR_NOTIFY_WARNINGS=true' >> /opt/aqsp/.env
```

如果你已经执行了 `install_server_cron.sh`，监控 cron 也会一并装好，不用单独再配。

## 如何查看 Dashboard

生产入口：

```text
https://lh.ifidy.cn
```

服务器健康检查：

```bash
curl -Ik https://lh.ifidy.cn/_stcore/health
```

Streamlit 只监听 `127.0.0.1:8501`，不要把 8501 端口直接暴露公网。

## 不会被覆盖的东西

下面这些默认不会被 `git pull` 覆盖：

- `/opt/aqsp/.env`
- `/opt/aqsp/.venv`
- `/opt/aqsp/data/*.db`
- `/opt/aqsp/data/*.jsonl`
- `/opt/aqsp/logs/`
- `/opt/aqsp/dist/`
- `/opt/market-data/astocks_raw.db`
- `/opt/market-data/astocks_qfq.db`（仅展示或历史辅助时保留）

## 你平时只做什么

平时只保留这条心智模型：

1. 本地改代码
2. `git push origin main`
3. 服务器自动更新并跑
4. 打开 `https://lh.ifidy.cn` 看结果

## GitHub Actions 降噪

仓库里的 CI 现在做了两层降噪：

- 只有 `src/`、`scripts/`、`tests/`、`pyproject.toml`、CI 自己变更时才会触发主 CI
- 同一分支连续 push 时，旧的 workflow 会自动取消，避免邮箱被重复失败刷屏

这意味着：

- 改文档、改本地笔记、改非关键文件，不会再无意义触发主 CI
- 连续修 bug 并反复 push，只看最后一次结果就够了
