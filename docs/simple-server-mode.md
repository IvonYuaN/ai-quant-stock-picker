# 最简单服务器模式

目标只有 4 条：

1. 本地只负责开发、验证并发布一个明确 commit
2. 云服务器只接受不可变 release，不直接在运行目录 `git pull`
3. 服务器本地保存 `.env`、数据库和运行结果，不被更新覆盖
4. 通过备案域名 `https://lh.ifidy.cn` 看 Dashboard

## 这套模式怎么理解

```text
本地开发与验证
-> 发布明确 commit 到服务器不可变 release
-> aqsp-scheduler-current 原子切换
-> 宝塔计划任务执行 release 下的 scripts/bt_task.sh
-> 产出 /opt/aqsp/data 下的 reports、快照和日志
-> aqsp-vibe-research.target 提供 React + FastAPI 看板
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

### 发布目录与磁盘清理契约

生产发布使用不可变目录时，目录关系必须保持如下结构：

```text
/opt/aqsp-releases/<commit>                 # 只读代码 release
/opt/aqsp-releases/aqsp-scheduler-current -> <commit>  # 当前 release
/opt/aqsp-releases/aqsp-scheduler-rollback -> <commit> # 唯一回滚 release
/opt/aqsp/data/                   # ledger、快照、报告、日志等运行产物
/opt/aqsp-vibe-venv/              # 共享解释器，禁止随 release 清理
/opt/aqsp/data/walkforward_raw_production_cache.db  # 历史 raw，禁止清理
```

运行产物不得写入 release。入口 `scripts/release_task_entrypoint.sh` 会把相对
路径统一解析到 `/opt/aqsp/data`，显式绝对路径若不在该目录下会直接失败。

服务器清理先执行只读审计：

```bash
cd /opt/aqsp
python3 scripts/check_runtime_storage.py --env-file /etc/aqsp/vibe-research.env --json
```

只有审计通过后，才允许显式执行清理：

```bash
python3 scripts/check_runtime_storage.py --env-file /etc/aqsp/vibe-research.env --apply
```

清理脚本只删除 `/opt/aqsp-releases/` 下未被 `aqsp-scheduler-current` 或
`aqsp-scheduler-rollback` 指向的直接子目录，
不会删除 `/opt/aqsp/data`、共享 venv、历史 raw 数据或 symlink 指向的目录；
缺少任一 release 链接、路径污染或 current/rollback 指向同一版本时会拒绝操作。
不要使用 `git clean -fd` 代替这条检查。

生产 release 若使用独立运行时，建议在服务器 `.env` 明确指定，所有
`bt_task`、盘中、消息、冷启动和 daily 入口都会复用这一解释器：

```bash
AQSP_RUNTIME_VENV_DIR=/opt/aqsp/.venv-vibe-research
```

入口优先级为 `AQSP_PYTHON`、`AQSP_RUNTIME_VENV_DIR`、
`AQSP_VIBE_VENV_DIR`、自动发现的 `.venv-vibe-research`，最后才回退到
`.venv`。这样代码 checkout、日历检查和任务 Python 不会静默分叉。

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
AQSP_GATE_NOTIFY=false
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
- `AQSP_GATE_NOTIFY=true` 才允许把“双门未放行”单独推到手机；默认关闭，避免冷启动/未过 gate 时反复打扰。
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

## 发布与运行入口

生产发布统一使用 `scripts/deploy_immutable_release.sh`。它从指定远端 ref 构建新 release，先通过前端构建、release 一致性、调度审计，再原子切换
`/opt/aqsp-releases/aqsp-scheduler-current`，重启 API/React 服务并验证公网 health、路由和快照契约。视觉浏览器检查在本地隔离无头环境执行，不把服务器是否安装 Chromium 作为发布门槛。

宝塔只负责调用当前 release 的 `scripts/bt_task.sh`，不负责拉代码，也不应直接调用旧的
`daily_pipeline.sh`、`server_sync_and_run.sh` 或任何历史脚本。服务器 `/opt/aqsp` 的脏 staging
checkout 不是发布来源；如 release 验收失败，保留 rollback symlink，不覆盖运行数据。

## 宝塔面板计划任务（生产推荐）

生产入口统一放在 **宝塔面板 -> 计划任务**。本地 Mac 的 `launchd` 只保留为历史兼容方案，不再作为生产定时来源。

统一命令入口：

```bash
/bin/bash /opt/aqsp/scripts/bt_task.sh <intraday|midday|daily|daily-research|data-refresh|data-refresh-retry|coldstart|variant-refresh|monitor|news|status>
```

建议在宝塔里配置 **10 条自动任务 + 1 条手动自检命令**：

| 任务名 | 推荐时间 | 宝塔脚本内容 | 作用 |
|---|---:|---|---|
| `AQSP-盘中刷新` | 工作日 `09:35-11:30`、`13:05-14:57` 每 10 分钟 | `/bin/bash /opt/aqsp/scripts/bt_task.sh intraday` | 刷新盘中候选和看板，写独立盘中产物，不污染正式 ledger |
| `AQSP-午盘分析` | 工作日 `12:05` | `/bin/bash /opt/aqsp/scripts/bt_task.sh midday` | 中午固定复核上午走势、候选和大盘状态 |
| `AQSP-消息面雷达` | 工作日 `08:35`，周末 `09:05` | `/bin/bash /opt/aqsp/scripts/bt_task.sh news` | 盘前/周末复核高影响消息、涨价链、政策、风险事件 |
| `AQSP-日线数据分块刷新` | 工作日 `15:35` | `/bin/bash /opt/aqsp/scripts/bt_task.sh data-refresh` | 在 480 秒硬预算内顺序更新多个 120 只原始日线小块，记录游标；仅允许北京时间 `15:30-17:50`，与重任务互斥 |
| `AQSP-日线延迟重试` | 工作日 `15:45-19:30` 每 10 分钟 | `/bin/bash /opt/aqsp/scripts/bt_task.sh data-refresh-retry` | 首轮刷新结束后立即续跑，上游收盘日线延迟发布时在窗口内分批续跑；与首轮和其他重任务共用资源门禁 |
| `AQSP-收盘主链路` | 工作日 `18:00` | `/bin/bash /opt/aqsp/scripts/bt_task.sh daily` | 完整收盘复盘、纸面验证、简报、通知和看板刷新 |
| `AQSP-收盘研究分块` | 工作日 `20:00,20:20,20:40,21:00,21:20,21:40,22:00,22:20` | `/bin/bash /opt/aqsp/scripts/bt_task.sh daily-research` | 日线延迟刷新结束后，每次只研究一个 cursor 分块，刷新报告和首页；不重复纸面同步、通知或学习，避开变体刷新 |
| `AQSP-冷启动补样本` | 工作日 `19:40` | `/bin/bash /opt/aqsp/scripts/bt_task.sh coldstart` | 收盘主链路结束后再补历史库和冷启动样本，避免互斥跳过 |
| `AQSP-变体刷新` | 工作日 `22:30` | `/bin/bash /opt/aqsp/scripts/bt_task.sh variant-refresh` | 在最后一批收盘研究的最长退出时间后运行，受限轮转刷新变体实验；不写正式 ledger |
| `AQSP-服务器监控` | 工作日每 `15` 分钟 | `/bin/bash /opt/aqsp/scripts/bt_task.sh monitor` | 检查数据、运行态、通知通道；默认只推关键异常 |
| `AQSP-状态自检` | 不建议定时，手动点运行即可 | `/bin/bash /opt/aqsp/scripts/bt_task.sh status` | 临时查看 Git、产物、日志、运行态 |

如果宝塔的“每 N 分钟”不能限制交易时段，也可以让 `intraday` 工作日每 10 分钟跑。宝塔外层不要再写第二套时间判断或无条件打印“Successful”；`bt_task.sh` 和 `scripts/intraday_refresh.sh` 会统一判断交易日/交易时段，并把真实失败码返回给调度器，非交易时段只记“跳过”，不会污染结果。

`daily` 和 `coldstart` 会共用主锁。如果你在 `daily` 还没跑完时手动触发 `coldstart`，日志出现“正常跳过；这是互斥保护，不是失败”是预期行为。生产建议把 `coldstart` 放到 `19:40`，不要放在 `daily` 附近。

raw 重建只有在覆盖率达标并原子激活正式数据库后，才会写入前一交易日的完整盘中名单缓存。盘中实时名单解析超时时，只能使用这份已验证缓存，不得退回不完整大盘池。

每个目标交易日使用独立的 `astocks_raw.db.rebuild.<target_day>` 候选库；正式库切换后保留旧目标库作为可恢复备份，禁止下一轮任务在服务仍读取的候选文件上原地重建。

`daily`、`daily-research`、`data-refresh`、`data-refresh-retry`、`coldstart`、`variant-refresh` 和 `walkforward-gate` 启动前会读取服务器实时负载、可用内存、总内存和主链锁，并抢占同一把 `data/.locks/heavy-compute.lock` 槽位锁。资源不足或已有重任务时只写 `data/.state/resource-gate-<task>.json` 并正常跳过，保留上一版产物，等待下一个错峰窗口；不会和盘中、收盘主链或其他重任务争抢资源。两次日线任务每批只处理 `AQSP_DATA_REFRESH_BATCH_SIZE` 个标的，逐标的超时 4 秒；首轮和延迟重试默认都在同一 `480` 秒总预算内连续处理小批并持续写入游标。收盘研究分块默认每次只处理 `AQSP_DAILY_RESEARCH_BATCH_SIZE=10` 只，最长 `360` 秒、硬上限 `480` 秒，成功才推进 cursor；首页会显示真实的已研究数量和覆盖率。刷新池只包含沪市主板、深市主板与创业板，排除 ST、退市和科创板，按板块交错轮转，绝不把全市场读进内存或退化为成交额头部。它用独立的 `AQSP_DATA_REFRESH_MIN_FREE_MEMORY_MB=640` 和 `AQSP_DATA_REFRESH_MAX_LOAD_PER_CPU=0.50` 门槛，适配 2C/1.6GB 服务器。变体刷新固定 `240` 股票上限、`80` 条 SQL 分块、`480` 秒内部预算、`510` 秒外层超时和低 CPU 优先级，并用 `AQSP_VARIANT_MIN_FREE_MEMORY_MB=700`、`AQSP_VARIANT_MAX_LOAD_PER_CPU=0.50` 门槛，避免通用 1GB 保留量在 1.6GB 主机上永久阻塞 v2 产物。其它重任务未配置时，内存保留量按总内存的 `25%` 自动计算，最低 `768MB`、最高 `4096MB`；每核 1 分钟负载默认上限 `0.70`。服务器 `.env` 可用 `AQSP_HEAVY_MIN_FREE_MEMORY_MB` 覆盖自动值，用 `AQSP_HEAVY_MAX_LOAD_PER_CPU` 调整负载门槛；状态文件会记录实际采用的内存门槛。直接运行 production walk-forward 也会检查可用内存，默认低于 `768MB` 拒绝启动，可用 `--min-free-memory-mb` 按主机配置调整。变体市场库查询按 `80` 只股票分块，避免单条大 SQL 占用过高；发布前至少 `80%` 的变体必须拥有不同的持仓签名，每只当前持仓必须带同日 MACD、KDJ、量比和 ATR 证据，每一只换仓股票的说明必须点名并给出技术指标。

`check_scheduler.py` 同时审计系统 crontab 和宝塔实际任务目录 `/www/server/cron`。它会把直跑旧脚本、绕过 `bt_task.sh` 的重任务、缺失的必需动作、以及同一重任务的多个宝塔包装任务判为失败；工作日/周末两条消息面任务属于允许的错峰窗口。不可变发布会以 `AQSP_SCHEDULER_STRICT_SCHEDULE=true` 阻断这类调度错误，不会因为当天尚未生成日志而误阻断。该检查只读，不会自行删除任何计划任务。

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
AQSP_GATE_NOTIFY=false
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

如果你当前目标是只把 `predictions.jsonl` 的冷启动天数稳定累积到 30，而不是跑整套收盘链路，使用宝塔任务的统一入口：

```bash
cd /opt/aqsp
python3 scripts/merge_server_ledgers.py
/bin/bash /opt/aqsp/scripts/bt_task.sh coldstart
```

含义：

- `merge_server_ledgers.py`：把服务器本地 `data/ledger.jsonl` 合并进正式 `data/predictions.jsonl`，按 `(signal_date, symbol, thresholds_version, regime, intended_entry)` 去重，并自动补齐 `signal_day_group`。
- 宝塔任务 `AQSP-冷启动补样本`：`/bin/bash /opt/aqsp/scripts/bt_task.sh coldstart`，北京时间 `19:40`；禁止再使用 `coldstart_daily.sh` 直连 cron。

`coldstart_daily.sh` 会按下面顺序寻找历史库更新脚本：

1. `AQSP_COLDSTART_UPDATE_SCRIPT`
2. 仓库内 `scripts/update_sqlite_daily.py`
3. `AQSP_SQLITE_DB_PATH` 同目录下的 `update_daily.py`
4. 仓库内 `A股量化分析数据/update_daily.py`

所以像服务器这种 `AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_raw.db` 场景，会默认使用仓库内受测的 `scripts/update_sqlite_daily.py`；只有显式覆盖或仓库脚本不存在时，才会回退到 `/opt/market-data/update_daily.py`。

仅在从旧 system cron 迁移时，可显式安装统一入口：

```bash
AQSP_INSTALL_SYSTEM_CRON=true AQSP_COLDSTART_CRON_SCHEDULE="40 11 * * 1-5" \
  bash /opt/aqsp/scripts/install_coldstart_cron.sh
```

上面这个例子适合服务器时区为 `UTC`，对应北京时间 `19:40`，并会移除旧的直连冷启动任务。

`scripts/intraday_refresh.sh` 默认只在交易时段内工作，并且写入单独的盘中 ledger，不污染正式收盘 ledger。

长任务会自动互斥：

- `daily`、`midday` 和 BT 入口的 `intraday` 会通过当前 release 的运行锁互斥，避免写产物时互相踩踏；不会在任务中同步代码。
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
curl -Ik https://lh.ifidy.cn/api/health
```

React 只监听 `127.0.0.1:5899`，FastAPI 只监听 `127.0.0.1:8900`，不要把应用端口直接暴露公网。
旧 Streamlit `8501` 仅保留在 `docs/server-dashboard-deployment.md` 的历史回滚流程中。

## 不会被覆盖的东西

下面这些始终属于运行数据边界，不会被 release 切换覆盖：

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

1. 本地改代码并完成验证
2. 发布一个明确 commit：`scripts/deploy_immutable_release.sh --branch <branch> --ref <commit>`
3. 确认 current/rollback、调度、health 和数据新鲜度
4. 打开 `https://lh.ifidy.cn` 看结果

## GitHub Actions

CI 仅保留手动入口，不参与服务器发布、调度或公网验收；线上是否成功以不可变 release 和服务器证据为准。
