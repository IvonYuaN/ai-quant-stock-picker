# 私有服务器前端与数据库部署

推荐架构:

```text
GitHub Actions 定时运行
-> aqsp run 生成候选股、ledger、通知
-> render_dashboard.py 生成静态前端
-> export_dashboard_db.py 生成 SQLite 快照
-> rsync 到你的服务器
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
