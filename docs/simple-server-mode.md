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
-> 再跑 scripts/daily_pipeline.sh
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

AQSP_DEPLOY_DASHBOARD=false

TUSHARE_TOKEN=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
GLM_API_KEY=
```

## 自动更新脚本

仓库内置：

```bash
bash /opt/aqsp/scripts/server_sync_and_run.sh
```

它会做 3 件事：

1. 检查服务器代码目录是否干净
2. `git pull --ff-only origin main`
3. 运行 `scripts/daily_pipeline.sh`

如果服务器上存在受 Git 管理的本地改动，它会直接停下，不会乱覆盖。

## 定时任务

推荐只保留一个主任务，工作日北京时间 18:00 执行：

```bash
( crontab -l 2>/dev/null; echo '0 18 * * 1-5 /bin/bash /opt/aqsp/scripts/server_sync_and_run.sh >> /opt/aqsp/logs/cron.log 2>&1' ) | crontab -
```

查看：

```bash
crontab -l
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
