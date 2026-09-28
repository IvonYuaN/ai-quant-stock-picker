# AQSP 双服务器分离部署方案

## 架构概述

```text
┌─────────────────────┐         ┌──────────────────────┐
│  前端服务器 (A)      │         │  数据服务器 (B)       │
│                     │         │                      │
│  ┌───────────────┐  │         │  ┌────────────────┐  │
│  │ Nginx (443)   │  │         │  │ FastAPI (8900) │  │
│  │  ↓            │  │  HTTPS  │  │                │  │
│  │ React (5899)  │──┼────────→│  │ 数据处理        │  │
│  └───────────────┘  │         │  │ SQLite         │  │
│                     │         │  │ 定时任务        │  │
└─────────────────────┘         └──────────────────────┘
         ↑
         │
    用户浏览器
 https://lh.ifidy.cn
```

## 服务器职责分工

### 前端服务器 (Server A)
- **域名**: `lh.ifidy.cn` (公网访问入口)
- **服务**: 
  - Nginx (宝塔面板管理，HTTPS证书)
  - React Dashboard (端口 5899)
- **职责**: 
  - 托管前端页面
  - 反向代理 `/api/*` 请求到数据服务器
  - SSL/TLS 终结
  - 用户认证 (Basic Auth / API Key)

### 数据服务器 (Server B)
- **内网访问**: 前端服务器通过内网或VPN访问
- **服务**: 
  - FastAPI (端口 8900)
  - SQLite 数据库
  - 定时任务 (宝塔计划任务)
- **职责**: 
  - 数据采集和处理
  - 选股计算
  - API 接口服务
  - 报告生成

## 配置要点

### 1. 前端服务器配置

#### Nginx 配置 (`/www/server/panel/vhost/nginx/proxy/lh.ifidy.cn/aqsp-dashboard.conf`)

```nginx
# API 请求代理到数据服务器
location ^~ /api/ {
    # 替换为数据服务器的实际地址
    proxy_pass http://DATA_SERVER_IP:8900;
    
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header Authorization $http_authorization;
    proxy_set_header Cookie $http_cookie;
    
    proxy_read_timeout 300s;
    proxy_send_timeout 60s;
    proxy_connect_timeout 10s;
    proxy_request_buffering off;
    proxy_buffering off;
    proxy_cache off;
}

location = /api/health {
    proxy_pass http://DATA_SERVER_IP:8900/api/health;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_buffering off;
    proxy_cache off;
}

location = /api {
    proxy_pass http://DATA_SERVER_IP:8900/api;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Authorization $http_authorization;
    proxy_buffering off;
    proxy_cache off;
}

# 前端资源走本地
location ^~ /assets/ {
    proxy_pass http://127.0.0.1:5899;
    proxy_cache off;
    add_header Cache-Control "public, max-age=3600" always;
}

location / {
    proxy_pass http://127.0.0.1:5899;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_buffering off;
    proxy_cache off;
    proxy_intercept_errors on;
    error_page 404 =200 /index.html;
    add_header Cache-Control "no-cache" always;
}
```

**配置变量说明：**
- `DATA_SERVER_IP`: 数据服务器的IP地址（内网IP或公网IP）
- 如果使用内网，确保两台服务器网络互通
- 如果使用公网，建议配置防火墙只允许前端服务器IP访问8900端口

#### 前端环境变量 (`/opt/aqsp/.env` on Server A)

```bash
# 前端服务器不需要数据源配置，只负责展示
NODE_ENV=production

# API 地址（Vite构建时使用，运行时由Nginx代理）
VITE_API_URL=http://127.0.0.1:8900
```

#### systemd 服务配置

前端服务器只需要运行 React 服务：

```bash
# 安装前端服务
sudo /opt/aqsp/scripts/install_vibe_research_systemd.sh \
  --env-file /etc/aqsp/vibe-research.env \
  --frontend-only \
  --no-start

# 启动前端服务
sudo systemctl start aqsp-vibe-research-preview.service
sudo systemctl enable aqsp-vibe-research-preview.service
```

### 2. 数据服务器配置

#### FastAPI CORS 配置

数据服务器需要允许前端服务器的域名跨域访问。

编辑 `/opt/aqsp/.env`:

```bash
# 允许前端服务器域名
VR_ALLOW_ORIGINS=https://lh.ifidy.cn

# 如果需要 API Key 鉴权（推荐）
VR_API_KEY=your_secure_api_key_here
VR_PUBLIC_MODE=1

# 数据源配置
AQSP_SOURCE=sqlite_db
AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_raw.db
AQSP_ALLOW_ONLINE_FALLBACK=false

AQSP_MODE=close
AQSP_LIMIT=10
AQSP_MAX_UNIVERSE=0
AQSP_MIN_AVG_AMOUNT=50000000
AQSP_MAX_DATA_LAG_DAYS=3

# Ledger 和报告路径
AQSP_LEDGER=data/predictions.jsonl
AQSP_PAPER_LEDGER=data/paper_trades.jsonl
AQSP_REPORT=reports/latest.md
AQSP_OUTPUT_CSV=reports/latest.csv
AQSP_DASHBOARD_HTML=dist/dashboard/index.html
AQSP_DASHBOARD_DB=dist/dashboard/aqsp.db

# 通知配置
AQSP_NOTIFY=true
AQSP_NOTIFY_MODE=summary
SERVERCHAN_SENDKEY=your_serverchan_key

# Tushare Token
TUSHARE_TOKEN=your_tushare_token
```

#### systemd 服务配置

数据服务器只需要运行 FastAPI 服务：

```bash
# 安装后端服务
sudo /opt/aqsp/scripts/install_vibe_research_systemd.sh \
  --env-file /etc/aqsp/vibe-research.env \
  --backend-only \
  --no-start

# 启动后端服务
sudo systemctl start aqsp-vibe-research-api.service
sudo systemctl enable aqsp-vibe-research-api.service
```

#### 宝塔计划任务

数据服务器负责所有定时任务：

| 任务名 | 时间 | 命令 | 说明 |
|--------|------|------|------|
| AQSP-盘中刷新 | 工作日 09:35-11:30, 13:05-14:57 每10分钟 | `/bin/bash /opt/aqsp/scripts/bt_task.sh intraday` | 盘中数据刷新 |
| AQSP-午盘分析 | 工作日 12:05 | `/bin/bash /opt/aqsp/scripts/bt_task.sh midday` | 午盘分析 |
| AQSP-消息面雷达 | 工作日 08:35 | `/bin/bash /opt/aqsp/scripts/bt_task.sh news` | 消息面扫描 |
| AQSP-收盘主链路 | 工作日 18:00 | `/bin/bash /opt/aqsp/scripts/bt_task.sh daily` | 收盘复盘 |
| AQSP-冷启动补样本 | 工作日 19:40 | `/bin/bash /opt/aqsp/scripts/bt_task.sh coldstart` | 补充历史数据 |
| AQSP-服务器监控 | 每15分钟 | `/bin/bash /opt/aqsp/scripts/bt_task.sh monitor` | 服务监控 |

### 3. 网络和安全配置

#### 防火墙规则

**数据服务器 (Server B) 防火墙：**

```bash
# 只允许前端服务器访问 8900 端口
sudo ufw allow from FRONTEND_SERVER_IP to any port 8900 proto tcp

# 或使用 iptables
sudo iptables -A INPUT -p tcp -s FRONTEND_SERVER_IP --dport 8900 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 8900 -j DROP
```

#### 内网通信（推荐）

如果两台服务器在同一云服务商：
1. 使用云服务商的VPC/内网功能
2. 前端服务器通过内网IP访问数据服务器
3. 数据服务器的8900端口不对公网开放

#### API Key 鉴权（推荐）

数据服务器配置 API Key：

```bash
# 数据服务器 .env
VR_API_KEY=your_secure_random_key_here
VR_PUBLIC_MODE=1
```

前端服务器 Nginx 添加 API Key：

```nginx
location ^~ /api/ {
    proxy_pass http://DATA_SERVER_IP:8900;
    proxy_set_header Authorization "Bearer your_secure_random_key_here";
    # ... 其他配置
}
```

## 部署流程

### 首次部署

**1. 数据服务器 (Server B) 部署：**

```bash
# SSH 登录数据服务器
ssh user@data-server

# 克隆代码
cd /opt
sudo git clone https://github.com/yourusername/AI量化选股.git aqsp
sudo chown -R $USER:$USER /opt/aqsp

# 安装依赖
cd /opt/aqsp
python3 -m venv .venv
source .venv/bin/pip install -e ".[data,api]"

# 配置环境变量
cp .env.example .env
nano .env  # 编辑配置

# 安装并启动后端服务
sudo scripts/install_vibe_research_systemd.sh \
  --env-file /etc/aqsp/vibe-research.env \
  --backend-only

sudo systemctl start aqsp-vibe-research-api.service

# 验证服务
curl http://127.0.0.1:8900/api/health

# 配置宝塔计划任务（按上表配置）
```

**2. 前端服务器 (Server A) 部署：**

```bash
# SSH 登录前端服务器
ssh user@frontend-server

# 克隆代码
cd /opt
sudo git clone https://github.com/yourusername/AI量化选股.git aqsp
sudo chown -R $USER:$USER /opt/aqsp

# 安装前端依赖
cd /opt/aqsp/frontend
npm install
npm run build

# 安装并启动前端服务
cd /opt/aqsp
sudo scripts/install_vibe_research_systemd.sh \
  --env-file /etc/aqsp/vibe-research.env \
  --frontend-only

sudo systemctl start aqsp-vibe-research-preview.service

# 配置 Nginx（修改 DATA_SERVER_IP）
sudo nano /www/server/panel/vhost/nginx/proxy/lh.ifidy.cn/aqsp-dashboard.conf

# 测试 Nginx 配置
sudo /www/server/nginx/sbin/nginx -t

# 重载 Nginx
sudo /etc/init.d/nginx reload

# 验证
curl https://lh.ifidy.cn/api/health
```

### 日常更新

**本地开发机：**

```bash
# 开发完成后推送代码
git add .
git commit -m "your changes"
git push origin main
```

**数据服务器自动更新：**

数据服务器的 `bt_task.sh` 会在每次定时任务执行前自动 `git pull`，无需手动操作。

**前端服务器更新：**

```bash
# SSH 登录前端服务器
ssh user@frontend-server

cd /opt/aqsp
git pull origin main

cd frontend
npm install  # 如有新依赖
npm run build

# 重启前端服务
sudo systemctl restart aqsp-vibe-research-preview.service
```

## 健康检查

### 检查脚本

```bash
#!/bin/bash
# scripts/check_dual_servers.sh

FRONTEND_URL="https://lh.ifidy.cn"
BACKEND_URL="http://DATA_SERVER_IP:8900"

echo "=== 前端服务器检查 ==="
curl -sI "$FRONTEND_URL/" | grep "HTTP"
curl -s "$FRONTEND_URL/" | grep -o "AQSP" | head -1

echo -e "\n=== 后端服务器检查 ==="
curl -s "$BACKEND_URL/api/health" | jq .

echo -e "\n=== 前端到后端连通性检查 ==="
curl -s "$FRONTEND_URL/api/health" | jq .

echo -e "\n=== 数据服务器任务状态 ==="
ssh user@data-server "tail -20 /opt/aqsp/logs/bt/bt-daily-\$(date +%Y-%m-%d).log"
```

### 常见问题排查

**1. 前端访问正常，但 API 返回 502/504**
- 检查数据服务器 FastAPI 服务是否运行
- 检查网络连通性（ping、telnet）
- 检查防火墙规则

**2. API 返回 CORS 错误**
- 检查数据服务器 `VR_ALLOW_ORIGINS` 配置
- 确保包含前端域名 `https://lh.ifidy.cn`

**3. API 返回 401 Unauthorized**
- 检查 Nginx 是否正确传递 Authorization 头
- 检查数据服务器 `VR_API_KEY` 配置

## 安全建议

1. **使用内网通信**: 优先使用云服务商VPC内网，避免公网暴露8900端口
2. **配置 API Key**: 启用 `VR_API_KEY` 鉴权
3. **最小权限原则**: 防火墙只允许必要的IP和端口
4. **定期更新**: 及时更新依赖和安全补丁
5. **日志监控**: 监控异常访问和错误日志

## 备份策略

**数据服务器需要备份：**
- `/opt/aqsp/data/*.jsonl` (Ledger数据)
- `/opt/aqsp/data/*.db` (缓存数据库)
- `/opt/market-data/astocks_raw.db` (历史数据)
- `/opt/aqsp/.env` (配置文件)

**前端服务器不需要备份数据**，只需保持代码同步。

## 监控指标

**数据服务器：**
- FastAPI 服务运行状态
- 定时任务执行状态
- 数据库大小和更新时间
- API 响应时间

**前端服务器：**
- React 服务运行状态
- Nginx 访问日志
- HTTPS 证书有效期

**整体：**
- 端到端健康检查 (`/api/health`)
- 用户访问延迟
- 错误率监控
