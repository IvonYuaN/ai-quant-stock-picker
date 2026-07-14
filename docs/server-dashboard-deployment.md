# 备案域名前端与数据库部署

现在既然已经有 **备案域名**，默认推荐走标准公网部署：

- 域名：如 `dashboard.yourdomain.com`
- Web：Nginx / Caddy
- 证书：Let's Encrypt / 宝塔证书
- 鉴权：Basic Auth、统一登录、Cloudflare Access 三选一或叠加
- 应用：Streamlit 只监听 `127.0.0.1:8501`

推荐架构分两种:

1. 本地跑策略, 服务器只托管面板
2. 服务器自己定时跑完整策略

这个项目当前没有“本地程序实时连服务器程序”的常驻服务。两边关系本质上是:

- 代码通过 Git 同步
- 结果文件通过 `rsync/ssh` 同步
- 或者服务器自己独立运行一套 `aqsp`

如果你现在是“本地能跑, 服务器也想配起来”, 推荐优先走第 2 种: 服务器独立跑。这样最稳, 不依赖本地机器在线。

## 方案 A: 本地跑, 服务器展示（仅离线归档）

```text
GitHub Actions 定时运行
-> aqsp run 生成候选股、ledger、通知
-> render_dashboard.py 生成静态前端
-> export_dashboard_db.py 生成 SQLite 快照
-> rsync 到你的服务器
```

这条链只生成离线归档快照，不作为生产域名首页。生产域名统一反代当前 Streamlit `127.0.0.1:8501`。

## 方案 B: 服务器独立跑完整任务

```text
服务器宝塔计划任务
-> scripts/bt_task.sh daily
-> scripts/daily_pipeline.sh
-> scripts/daily_pipeline.py
-> 生成 reports/、data/、dist/dashboard/
-> 刷新 dist/dashboard/ 离线归档产物（不发布到公网根目录）
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

推荐的公网结构：

```text
browser
-> https://dashboard.yourdomain.com
-> Nginx / Caddy
-> 127.0.0.1:8501 (streamlit)
```

Nginx 示例:

```nginx
server {
    listen 80;
    server_name dashboard.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name dashboard.yourdomain.com;

    ssl_certificate     /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    auth_basic "AQSP Dashboard";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

静态 `dist/dashboard/` 只用于离线归档和调试，不应配置为生产域名根目录；其中 `index.html` 仅是迁移入口，真实归档位于 `archive.html`。生产 `location /` 必须保持 `proxy_pass http://127.0.0.1:8501`。

仓库中的 `deploy/nginx/aqsp-dashboard.conf` 是生产反代规则的唯一配置源。宝塔站点 `lh.ifidy.cn` 通过 `include /www/server/panel/vhost/nginx/proxy/lh.ifidy.cn/*.conf;` 加载该目录；如果域名仍显示旧静态页面，先检查该目录是否存在此规则，再执行 `/www/server/nginx/sbin/nginx -t && /etc/init.d/nginx reload`，不要重新把 `dist/dashboard/` 配成公网根目录。

该规则还会把历史的 `/dashboard*`、`/dist/dashboard*`、`/beginner*`、`/agent*`、`/agents*` 和归档 HTML 入口统一 `302` 到 `https://lh.ifidy.cn/`。不要拦截 `/static/...`，那是 Streamlit 当前前端资源路径。这些旧入口不能继续落到通用 `location /`，否则旧书签会看起来像第二套看板。

发布后用隔离无头浏览器验收，不使用用户前台浏览器：

```bash
python3 scripts/headless_dashboard_check.py \
  --url https://lh.ifidy.cn \
  --mode browser \
  --require-browser \
  --expect 'AQSP 日期任务研究台' \
  --headless-lock /tmp/aqsp-headless-dashboard.lock
```

不建议直接把 Streamlit 端口裸露到公网。

## Vibe-Research 根路径切换（候选方案）

Vibe-Research 的唯一入口候选配置是 `deploy/nginx/vibe-research-mainline.conf`。
它是与宝塔 `lh.ifidy.cn` server 块同级 include 的 location 片段，不是完整
server 配置。该文件必须替换 `aqsp-dashboard.conf` 后再加载，**不能与
`aqsp-dashboard.conf` 同时 include**，否则两个文件都会声明 `location /`。

路由契约如下：

| 公网路径 | 上游/行为 | 设计约束 |
| --- | --- | --- |
| `/api`、`/api/*` | `127.0.0.1:8900` | 原样保留 URI；健康检查为 `/api/health`；不缓存；保留 Authorization、Cookie、Upgrade 和流式响应 |
| `/`、React 路由、`/assets/*` | `127.0.0.1:5899` | Vite preview 提供构建产物；`/daily-review`、`/paper-research`、`/intel` 必须回到 `index.html` |
| `/dashboard*`、`/beginner*`、`/agent*`、`/agents*`、归档 HTML | `302 /` | 保留 query string；切换期不用 301，避免浏览器永久缓存妨碍回滚 |

缓存策略是保守的：API 和应用壳不使用 Nginx proxy cache，`index.html` 每次重新验证；
`/assets/` 只设置一小时客户端缓存。API 的 `Authorization` 原样交给 FastAPI，公网模式
应设置 `VR_API_KEY`；Nginx 不在仓库中保存密钥。当前后端没有 WebSocket 路由，配置仍保留
HTTP/1.1、Upgrade/Connection 头，且 `/api/chat` 的 NDJSON 流关闭 buffering，未来新增
升级连接时不需要先改入口层。

### 切换前后验收

以下命令只用于服务器运维在确认窗口执行；本次任务不连接服务器、不 reload Nginx。

1. 切换前确认双服务和现有 Streamlit 回滚点：

```bash
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8900/api/health
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:5899/ | grep -F 'Vibe-Research'
curl --fail --silent --show-error --max-time 10 https://lh.ifidy.cn/_stcore/health
curl --silent --show-error --dump-header - --output /dev/null \
  'https://lh.ifidy.cn/dashboard?from=preflight'
```

   最后一条应为旧入口的 `302`，且 `Location` 指向当前根路径。先将
   `aqsp-dashboard.conf` 备份为不以 `.conf` 结尾的文件，确保它仍可恢复。

2. 仅在切换窗口内，把 `vibe-research-mainline.conf` 放入宝塔 include 目录，
   将当前 `aqsp-dashboard.conf` 改名为 `.disabled`，然后先执行配置检查：

```bash
/www/server/nginx/sbin/nginx -t
```

   检查失败时不要 reload，立即恢复旧文件名。检查通过后才由仓主/运维执行
   Nginx reload；本仓库 agent 不执行这一步。

3. 切换后验收唯一入口、React history fallback、API 和旧链接：

```bash
curl --fail --silent --show-error --max-time 10 https://lh.ifidy.cn/ | grep -F 'Vibe-Research'
curl --fail --silent --show-error --max-time 10 https://lh.ifidy.cn/daily-review | grep -F 'Vibe-Research'
curl --fail --silent --show-error --max-time 10 https://lh.ifidy.cn/api/health
curl --silent --show-error --dump-header - --output /dev/null \
  'https://lh.ifidy.cn/dashboard?from=postflight'
curl --silent --show-error --dump-header - --output /dev/null \
  'https://lh.ifidy.cn/beginner?from=postflight'
curl --silent --show-error --dump-header - --output /dev/null \
  'https://lh.ifidy.cn/agent?from=postflight'
```

   `/`、`/daily-review` 和 `/api/health` 应为 `200`；三类旧入口应为 `302` 并保留
   `from=postflight`。公网模式下再用不入库的环境变量发送一个受保护 API 请求，确认
   无 Authorization 时为 `401`，带正确 `Bearer` 时为业务响应；健康检查保持无鉴权 `200`。

4. 失败回滚：先恢复 `aqsp-dashboard.conf`，移出或改名
   `vibe-research-mainline.conf`，执行 `nginx -t` 通过后再 reload；回滚后确认
   `https://lh.ifidy.cn/_stcore/health` 为 `200`。不要把 `5899`/`8900` 直接暴露公网。

## 服务器 `.env` 最小示例

放到 `/opt/aqsp/.env`:

```bash
AQSP_SOURCE=sqlite_db
AQSP_MODE=close
AQSP_LIMIT=10
AQSP_MAX_UNIVERSE=0
AQSP_MIN_AVG_AMOUNT=50000000
AQSP_ENABLE_ONLINE_FACTORS=false
AQSP_ALLOW_ONLINE_FALLBACK=false
AQSP_MAX_DATA_LAG_DAYS=3
AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_raw.db

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
- `AQSP_SQLITE_DB_PATH` 的生产候选/ledger 路径必须是不复权 raw 库；qfq/hfq 库只能用于展示或历史辅助。

## Streamlit 服务启动建议

公网部署时，Streamlit 建议只监听本机回环地址：

```bash
streamlit run src/aqsp/web/dashboard.py --server.address 127.0.0.1 --server.port 8501
```

然后让 Nginx / Caddy 对外提供域名访问。

## 离线归档预览

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

打开 `http://127.0.0.1:8000`。这只是静态归档预览，不是当前 Dashboard；当前 Dashboard 请使用 `bash scripts/start_dashboard.sh` 的 `8501`。
