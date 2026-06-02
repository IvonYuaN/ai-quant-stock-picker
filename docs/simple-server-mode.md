# 最简单服务器模式

目标只有 4 条：

1. 本地只负责开发和 `git push`
2. 云服务器自动 `git pull`
3. 服务器本地保存 `.env`、数据库和运行结果，不被更新覆盖
4. 你在本地通过 `8080` 看 Dashboard

## 这套模式怎么理解

```text
Mac 本地开发
-> push 到 GitHub
-> 云服务器定时执行 scripts/server_sync_and_run.sh
-> 先 git pull --ff-only
-> 再跑指定 runner（盘中轻量刷新 / 收盘完整跑批）
-> 产出 dist/dashboard/
-> 本地通过 8080 查看
```

关键点：

- `.env` 已在 `.gitignore` 中，不会被 Git 覆盖
- `data/*.db`、`data/*.jsonl`、`dist/`、`logs/` 也不会被 Git 覆盖
- 服务器只要别手改受 Git 管理的代码文件，就可以一直自动更新
- 当前推荐模式是 `eastmoney` 负责最新数据，`/opt/market-data/astocks_qfq.db` 只做历史辅助

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
AQSP_SOURCE=eastmoney
AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_qfq.db
AQSP_ALLOW_ONLINE_FALLBACK=true

AQSP_SYMBOLS=600519,300750,000001,601318,600036
AQSP_WALKFORWARD_SYMBOLS=000915,000921,000923,000930,000932,000937,000938,000950,000951,000958
AQSP_MODE=close
AQSP_LIMIT=10
AQSP_MAX_UNIVERSE=50
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
GLM_API_KEY=

AQSP_NOTIFY=false
AQSP_ENABLE_DEBATE=false
AQSP_DEBATE_ENABLE_LLM=false
AQSP_DEBATE_MAX_ROUNDS=2
AQSP_DEBATE_LANGUAGE=zh-CN
AQSP_DEBATE_ROLES=bull,bear,risk_control,sector_leader,policy_sensitive,northbound
AQSP_ENABLE_AUTO_EVOLUTION=false
```

补充说明：

- `GLM_API_KEY` 用于智谱；`LLM_PROVIDER=glm` 时默认走 `GLM-4.7-Flash`。
- `AQSP_NOTIFY=true` 后，日终 `daily_pipeline.sh` 会自动带 `--notify`。
- `AQSP_SYMBOLS` 给实盘/日报链路用；`AQSP_WALKFORWARD_SYMBOLS` 单独给 walk-forward，用历史库里覆盖完整的票，别混用。
- 选股推荐通知仍受 walk-forward 双门 gate 保护；收盘复盘、监控告警、策略自进化通知不依赖这道 gate。
- `AQSP_ENABLE_DEBATE=false` 表示默认不跑多 agent 讨论；要开就改成 `true`。
- `AQSP_DEBATE_LANGUAGE=zh-CN` 现在是运行时配置，不再写死在代码里。
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
3. 运行 `AQSP_RUNNER_SCRIPT` 指定的脚本，默认 `scripts/daily_pipeline.sh`

如果服务器上存在受 Git 管理的本地改动，它会直接停下，不会乱覆盖。

## 定时任务

推荐拆成两条任务：

1. 盘中推荐，工作日北京时间 `09:40-11:25`、`13:10-14:55` 每 10 分钟执行一次。
2. 收盘复盘，工作日北京时间 `18:00` 执行一次。

这里的含义是：

- `intraday_refresh.sh` 负责你白天看的“推荐”。
- `server_sync_and_run.sh` 默认跑 `daily_pipeline.sh`，里面已经包含收盘复盘、虚拟盘同步、Dashboard 刷新。

```bash
# 北京时间 09:40-11:59 每 10 分钟跑一次盘中推荐
# 北京时间 13:00-14:59 每 10 分钟跑一次盘中推荐
# 北京时间 18:00 跑一次完整收盘复盘
( crontab -l 2>/dev/null | grep -vE 'intraday_refresh\\.sh|server_sync_and_run\\.sh'; \
  echo '*/10 9-11 * * 1-5 AQSP_RUNNER_SCRIPT=scripts/intraday_refresh.sh /bin/bash /opt/aqsp/scripts/server_sync_and_run.sh >> /opt/aqsp/logs/cron.log 2>&1'; \
  echo '*/10 13-14 * * 1-5 AQSP_RUNNER_SCRIPT=scripts/intraday_refresh.sh /bin/bash /opt/aqsp/scripts/server_sync_and_run.sh >> /opt/aqsp/logs/cron.log 2>&1'; \
  echo '0 18 * * 1-5 /bin/bash /opt/aqsp/scripts/server_sync_and_run.sh >> /opt/aqsp/logs/cron.log 2>&1' ) | crontab -
```

`scripts/intraday_refresh.sh` 默认只在交易时段内工作，并且写入单独的盘中 ledger，不污染正式收盘 ledger。

查看：

```bash
crontab -l
```

服务器状态总览：

```bash
bash /opt/aqsp/scripts/server_status.sh
```

首次补齐运行态空文件：

```bash
bash /opt/aqsp/scripts/init_server_runtime.sh
```

异常监控与告警：

```bash
bash /opt/aqsp/scripts/server_monitor.sh
```

默认只推送 `critical` 级别告警；如果要连 `warning` 也推送：

```bash
echo 'AQSP_MONITOR_NOTIFY_WARNINGS=true' >> /opt/aqsp/.env
```

建议每 15 分钟执行一次：

```bash
( crontab -l 2>/dev/null | grep -v 'server_monitor\.sh' ; \
  echo '*/15 * * * 1-5 /bin/bash /opt/aqsp/scripts/server_monitor.sh >> /opt/aqsp/logs/cron.log 2>&1' ) | crontab -
```

## 本地如何看 8080

推荐继续用 SSH 隧道：

```bash
ssh -L 8080:127.0.0.1:8080 root@aqsp-cn
```

然后本地浏览器打开：

```text
http://127.0.0.1:8080
```

如果你已经在服务器上把 Nginx 配成 `127.0.0.1:8080` 指向 Dashboard 目录，这样就够了。

## 不会被覆盖的东西

下面这些默认不会被 `git pull` 覆盖：

- `/opt/aqsp/.env`
- `/opt/aqsp/.venv`
- `/opt/aqsp/data/*.db`
- `/opt/aqsp/data/*.jsonl`
- `/opt/aqsp/logs/`
- `/opt/aqsp/dist/`
- `/opt/market-data/astocks_qfq.db`

## 你平时只做什么

平时只保留这条心智模型：

1. 本地改代码
2. `git push origin main`
3. 服务器自动更新并跑
4. 本地打开 `8080` 看结果
