# AQSP 双服务器部署快速开始

## 架构说明

```
用户浏览器
    ↓
https://lh.ifidy.cn (前端服务器 - Nginx)
    ↓
React Dashboard (127.0.0.1:5899)
    ↓ /api/*
数据服务器 FastAPI (IP:8900)
    ↓
SQLite + 定时任务
```

## 第一步：配置部署信息

在本地项目根目录创建 `deploy-config.env`：

```bash
cd ~/Documents/AI量化选股
cp deploy-config.env.example deploy-config.env
nano deploy-config.env
```

填写你的服务器信息：

```bash
# 前端服务器（托管 React Dashboard）
FRONTEND_SERVER=your-frontend-server-ip
FRONTEND_USER=root
FRONTEND_DOMAIN=lh.ifidy.cn

# 数据服务器（托管 FastAPI + 数据处理）
BACKEND_SERVER=your-backend-server-ip
BACKEND_SERVER_INTERNAL=10.0.0.x  # 如果有内网地址（推荐）
BACKEND_USER=root

# 仓库地址
GITHUB_REPO=https://github.com/yourusername/AI量化选股.git
```

## 第二步：准备服务器环境配置

创建两个服务器的环境配置文件：

**数据服务器配置** (`.env.backend`):

```bash
# 数据源配置
AQSP_SOURCE=sqlite_db
AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_raw.db
AQSP_ALLOW_ONLINE_FALLBACK=false

# CORS 配置（允许前端服务器域名）
VR_ALLOW_ORIGINS=https://lh.ifidy.cn

# API Key 鉴权（推荐）
VR_API_KEY=your_secure_random_api_key_here
VR_PUBLIC_MODE=1

# 其他配置
AQSP_MODE=close
AQSP_LIMIT=10
AQSP_NOTIFY=true
SERVERCHAN_SENDKEY=your_sendkey

# Tushare
TUSHARE_TOKEN=your_token
```

**前端服务器配置** (`.env.frontend`):

```bash
# 前端服务器不需要数据源配置
NODE_ENV=production
VITE_API_URL=http://127.0.0.1:8900
```

## 第三步：一键部署

```bash
# 部署所有服务器
bash scripts/deploy_dual_servers.sh all

# 或者分别部署
bash scripts/deploy_dual_servers.sh backend   # 先部署数据服务器
bash scripts/deploy_dual_servers.sh frontend  # 再部署前端服务器
```

脚本会自动：
1. SSH 连接到两台服务器
2. 克隆/更新代码
3. 安装依赖
4. 配置 systemd 服务
5. 更新 Nginx 配置
6. 启动服务
7. 执行健康检查

## 第四步：验证部署

```bash
# 执行健康检查
bash scripts/check_dual_servers_health.sh
```

手动验证：

```bash
# 1. 检查数据服务器
ssh root@your-backend-server
curl http://127.0.0.1:8900/api/health
sudo systemctl status aqsp-vibe-research-api.service

# 2. 检查前端服务器
ssh root@your-frontend-server
curl http://127.0.0.1:5899/
curl https://lh.ifidy.cn/api/health
sudo systemctl status aqsp-vibe-research-preview.service

# 3. 浏览器访问
open https://lh.ifidy.cn
```

## 第五步：配置定时任务（数据服务器）

SSH 登录数据服务器，在宝塔面板配置计划任务：

| 任务名 | 时间 | 命令 |
|--------|------|------|
| AQSP-收盘主链路 | 工作日 18:00 | `/bin/bash /opt/aqsp/scripts/bt_task.sh daily` |
| AQSP-盘中刷新 | 工作日 09:35-14:57 每10分钟 | `/bin/bash /opt/aqsp/scripts/bt_task.sh intraday` |
| AQSP-冷启动补样本 | 工作日 19:40 | `/bin/bash /opt/aqsp/scripts/bt_task.sh coldstart` |
| AQSP-服务器监控 | 每15分钟 | `/bin/bash /opt/aqsp/scripts/bt_task.sh monitor` |

## 日常更新流程

### 本地开发
```bash
# 1. 本地开发和测试
cd ~/Documents/AI量化选股
# ... 修改代码 ...

# 2. 提交并推送
git add .
git commit -m "your changes"
git push origin main
```

### 更新服务器

**自动更新**（推荐）：
```bash
# 在本地执行，自动更新两台服务器
bash scripts/deploy_dual_servers.sh all
```

**手动更新数据服务器**：
```bash
ssh root@your-backend-server
cd /opt/aqsp
git pull origin main
sudo systemctl restart aqsp-vibe-research-api.service
```

**手动更新前端服务器**：
```bash
ssh root@your-frontend-server
cd /opt/aqsp
git pull origin main
cd frontend
npm install  # 如有新依赖
npm run build
sudo systemctl restart aqsp-vibe-research-preview.service
```

## 常见问题排查

### 前端无法访问后端 API

1. 检查数据服务器 FastAPI 是否运行：
```bash
ssh root@your-backend-server
curl http://127.0.0.1:8900/api/health
```

2. 检查网络连通性：
```bash
ssh root@your-frontend-server
ping your-backend-server-ip
telnet your-backend-server-ip 8900
```

3. 检查防火墙规则（数据服务器）：
```bash
sudo ufw status
sudo ufw allow from your-frontend-server-ip to any port 8900
```

4. 检查 Nginx 配置（前端服务器）：
```bash
cat /www/server/panel/vhost/nginx/proxy/lh.ifidy.cn/aqsp-dashboard.conf | grep upstream
```

### CORS 错误

检查数据服务器 `.env` 配置：
```bash
ssh root@your-backend-server
grep VR_ALLOW_ORIGINS /opt/aqsp/.env
```

确保包含前端域名：`VR_ALLOW_ORIGINS=https://lh.ifidy.cn`

### 502 Bad Gateway

1. 检查后端服务状态
2. 查看后端日志：
```bash
ssh root@your-backend-server
tail -f /opt/aqsp/logs/api.log
sudo journalctl -u aqsp-vibe-research-api.service -f
```

## 监控和维护

### 查看服务状态
```bash
# 本地执行健康检查
bash scripts/check_dual_servers_health.sh

# 远程查看日志
ssh root@your-backend-server 'tail -f /opt/aqsp/logs/bt/bt-daily-$(date +%Y-%m-%d).log'
ssh root@your-frontend-server 'sudo journalctl -u aqsp-vibe-research-preview.service -f'
```

### 重启服务
```bash
# 数据服务器
ssh root@your-backend-server 'sudo systemctl restart aqsp-vibe-research-api.service'

# 前端服务器
ssh root@your-frontend-server 'sudo systemctl restart aqsp-vibe-research-preview.service'
```

## 安全建议

1. **使用内网连接**：如果两台服务器在同一 VPC，使用内网 IP 通信
2. **配置防火墙**：数据服务器 8900 端口只允许前端服务器 IP 访问
3. **启用 API Key**：在数据服务器配置 `VR_API_KEY`
4. **定期备份**：备份数据服务器的 `/opt/aqsp/data/` 目录

## 更多文档

- 完整部署方案：[docs/dual-server-deployment.md](./dual-server-deployment.md)
- 架构说明：[docs/architecture.md](./architecture.md)
- 最简部署模式：[docs/simple-server-mode.md](./simple-server-mode.md)
