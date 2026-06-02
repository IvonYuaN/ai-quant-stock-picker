# 私有服务器前端与数据库部署

推荐架构分两种:

1. 本地跑策略, 服务器只托管面板
2. 服务器自己定时跑完整策略

这个项目当前没有“本地程序实时连服务器程序”的常驻服务。两边关系本质上是:

- 代码通过 Git 同步
- 结果文件通过 `rsync/ssh` 同步
- 或者服务器自己独立运行一套 `aqsp`

如果你现在是“本地能跑, 服务器也想配起来”, 推荐优先走第 2 种: 服务器独立跑。这样最稳, 不依赖本地机器在线。

## 方案 A: 本地跑, 服务器只展示

```text
GitHub Actions 定时运行
-> aqsp run 生成候选股、ledger、通知
-> render_dashboard.py 生成静态前端
-> export_dashboard_db.py 生成 SQLite 快照
-> rsync 到你的服务器
```

也可以把上面的 `GitHub Actions` 换成你的本地 Mac 定时任务; 关键是最后一步把 `dist/dashboard/` 同步到服务器。

## 方案 B: 服务器独立跑完整任务

```text
服务器 cron / 宝塔计划任务
-> scripts/daily_pipeline.sh
-> daily_pipeline.py
-> 生成 reports/、data/、dist/dashboard/
-> 可选: 再把 dist/dashboard/ 发布到 Nginx 目录
```

GitHub 仓库只保存代码、配置模板和公开研究元数据。`reports/`、`data/predictions.jsonl`、`data/cache.db`、`dist/dashboard/` 不提交。

## GitHub Variables

进入 `Settings -> Secrets and variables -> Actions -> Variables`:

- `AQSP_SYMBOLS`: 例如 `600519,300750,000001`
- `AQSP_MODE`: `close`
- `AQSP_LIMIT`: 例如 `10`
- `AQSP_MAX_DATA_LAG_DAYS`: 例如 `3`
- `AQSP_DEPLOY_DASHBOARD`: `true`

Variables 会在日志里明文出现,只放非敏感配置。

## GitHub Secrets

进入 `Settings -> Secrets and variables -> Actions -> Secrets`:

- `AQSP_DEPLOY_HOST`: 服务器 IP 或域名
- `AQSP_DEPLOY_PORT`: SSH 端口,默认 `22`
- `AQSP_DEPLOY_USER`: 部署用户
- `AQSP_DEPLOY_PATH`: 服务器目录,例如 `/var/www/aqsp`
- `AQSP_DEPLOY_SSH_KEY`: 私钥内容,只给部署目录写权限
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` 或其他通知渠道
- `GITHUB_TOKEN` / `GITEE_TOKEN` / `TUSHARE_TOKEN`: 需要时再加

Secrets 用于敏感信息。GitHub 官方文档说明 Actions secret 会加密存储,并通过 `secrets` context 显式传给 workflow。

## 服务器准备

若你走“服务器独立跑”，先把仓库部署到例如 `/opt/aqsp`，再在服务器写 `.env`。

```bash
sudo adduser aqsp
sudo mkdir -p /var/www/aqsp
sudo chown -R aqsp:aqsp /var/www/aqsp
```

把 GitHub Secrets 中 `AQSP_DEPLOY_SSH_KEY` 对应的公钥写入:

```bash
sudo -u aqsp mkdir -p /home/aqsp/.ssh
sudo -u aqsp chmod 700 /home/aqsp/.ssh
sudo -u aqsp tee -a /home/aqsp/.ssh/authorized_keys < aqsp_deploy.pub
sudo -u aqsp chmod 600 /home/aqsp/.ssh/authorized_keys
```

Nginx 示例:

```nginx
server {
    listen 80;
    server_name your-domain.example;
    root /var/www/aqsp;
    index index.html;

    location / {
        try_files $uri $uri/ =404;
    }

    location = /aqsp.db {
        add_header Cache-Control "no-store";
    }
}
```

如果不想公开访问,用 Nginx Basic Auth、VPN、Cloudflare Access 或仅内网访问。

## 服务器 `.env` 最小示例

放到 `/opt/aqsp/.env`:

```bash
AQSP_SOURCE=auto
AQSP_MODE=close
AQSP_LIMIT=10
AQSP_MAX_UNIVERSE=100
AQSP_MIN_AVG_AMOUNT=50000000
AQSP_ENABLE_ONLINE_FACTORS=false
AQSP_ALLOW_ONLINE_FALLBACK=true
AQSP_MAX_DATA_LAG_DAYS=3

AQSP_LEDGER=data/predictions.jsonl
AQSP_PAPER_LEDGER=data/paper_trades.jsonl
AQSP_REPORT=reports/latest.md
AQSP_OUTPUT_CSV=reports/latest.csv
AQSP_DASHBOARD_HTML=dist/dashboard/index.html
AQSP_DASHBOARD_DB=dist/dashboard/aqsp.db

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
WECHAT_WEBHOOK_URL=
FEISHU_WEBHOOK_URL=
GENERIC_WEBHOOK_URL=

TUSHARE_TOKEN=
AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_qfq.db

# 只有“跑批机”要把面板再发布到另一台 Web 服务器时才开启
AQSP_DEPLOY_DASHBOARD=false
AQSP_DEPLOY_HOST=
AQSP_DEPLOY_PORT=22
AQSP_DEPLOY_USER=
AQSP_DEPLOY_PATH=
AQSP_DEPLOY_SSH_KEY_PATH=/home/aqsp/.ssh/aqsp_deploy
```

说明:

- `AQSP_DEPLOY_DASHBOARD=false` 表示服务器本机生成完 `dist/dashboard/` 就结束。
- `AQSP_DEPLOY_DASHBOARD=true` 表示这台服务器还会继续把 `dist/dashboard/` 推送到另一台机器。
- `AQSP_ALLOW_ONLINE_FALLBACK=false` 表示 `auto/local_first` 只允许本地数据源，不再意外回退到公网接口。

## 本地预览

```bash
python3 scripts/render_dashboard.py \
  --csv reports/close.csv \
  --ledger data/predictions.jsonl \
  --output dist/dashboard/index.html

python3 scripts/export_dashboard_db.py \
  --csv reports/close.csv \
  --ledger data/predictions.jsonl \
  --db dist/dashboard/aqsp.db

python3 -m http.server 8000 -d dist/dashboard
```

打开 `http://127.0.0.1:8000`。
