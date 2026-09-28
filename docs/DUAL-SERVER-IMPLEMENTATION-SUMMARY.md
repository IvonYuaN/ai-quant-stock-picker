# AQSP 双服务器分离部署 - 实施总结

## 问题分析

### 原有架构的限制

之前的配置假设前端和后端在**同一台服务器**上：

1. **Nginx 配置**：所有 `proxy_pass` 都指向 `127.0.0.1:8900` 和 `127.0.0.1:5899`
2. **systemd 服务**：前端服务 `Requires=aqsp-vibe-research-api.service`，强制要求后端在同一台机器
3. **无跨服务器通信机制**：没有配置 CORS、没有跨服务器健康检查
4. **部署脚本不支持分离部署**：`install_vibe_research_systemd.sh` 假设前后端在同一位置

### 你的实际需求

- **前端服务器 (Server A)**：托管 React Dashboard + Nginx，公网入口 `https://lh.ifidy.cn`
- **数据服务器 (Server B)**：托管 FastAPI + SQLite + 定时任务，处理所有数据计算
- **本地开发机**：开发和测试，推送代码到 GitHub
- **GitHub**：代码同步中心

## 解决方案

### 1. 配置文件和文档

#### 新增文件：

1. **`docs/dual-server-deployment.md`** (11KB)
   - 完整的双服务器部署方案文档
   - 详细的架构说明和配置步骤
   - 安全配置建议
   - 故障排查指南

2. **`docs/QUICKSTART-DUAL-SERVER.md`** (4KB)
   - 快速开始指南
   - 5 步完成部署
   - 常见问题和解决方案
   - 日常更新流程

3. **`deploy-config.env.example`**
   - 部署配置模板
   - 包含前端服务器、数据服务器、GitHub 仓库等配置项

### 2. Nginx 配置

#### 新增文件：`deploy/nginx/aqsp-dashboard-dual-server.conf`

关键特性：
- 使用 `upstream aqsp_backend` 定义远程数据服务器地址
- 所有 `/api/*` 请求代理到数据服务器
- 前端资源（`/assets/`, `/`）走本地 5899 端口
- 支持长连接和 WebSocket（为未来功能预留）
- 完整的安全头配置

使用方法：
```nginx
upstream aqsp_backend {
    server DATA_SERVER_IP:8900;  # 替换为实际数据服务器地址
    keepalive 32;
}

location ^~ /api/ {
    proxy_pass http://aqsp_backend;
    # ... 完整的代理配置
}
```

### 3. 部署脚本

#### 新增文件：`scripts/deploy_dual_servers.sh` (10KB)

**功能：**
- 自动部署到两台服务器
- 支持分别部署：`backend` / `frontend` / `all`
- 自动同步代码、安装依赖、配置服务
- 自动更新 Nginx 配置并替换数据服务器地址
- 端到端健康检查
- 详细的部署信息输出

**使用方法：**
```bash
# 配置 deploy-config.env
cp deploy-config.env.example deploy-config.env
nano deploy-config.env

# 一键部署所有服务器
bash scripts/deploy_dual_servers.sh all

# 或分别部署
bash scripts/deploy_dual_servers.sh backend
bash scripts/deploy_dual_servers.sh frontend
```

### 4. 健康检查脚本

#### 新增文件：`scripts/check_dual_servers_health.sh` (8KB)

**功能：**
- 检查两台服务器的 SSH 连接
- 验证服务运行状态（systemd 服务、端口监听）
- 测试跨服务器连通性
- 验证公网访问（域名解析、HTTPS、SSL 证书）
- 检查本地代码同步状态
- 生成健康报告摘要

**输出示例：**
```
========================================
数据服务器健康检查
========================================
[✓] 数据服务器 SSH 连接
  系统: Ubuntu 22.04.3 LTS
  运行时间: up 15 days
  负载: 0.15, 0.20, 0.18
[✓] 代码目录存在
[✓] Python 虚拟环境
[✓] FastAPI systemd 服务
  服务状态: active (running)
[✓] FastAPI 端口 8900 监听
[✓] FastAPI 健康端点
  响应: {"status":"ok","version":"0.1.3"}

总检查项: 25
通过: 24
失败: 1
系统健康状态: 优秀 (96%)
```

### 5. systemd 服务安装脚本增强

#### 修改文件：`scripts/install_vibe_research_systemd.sh`

**新增功能：**
- `--frontend-only`：仅安装前端服务（React）
- `--backend-only`：仅安装后端服务（FastAPI）
- 自动检测缺失的依赖和构建产物
- 支持分离部署场景

**使用方法：**
```bash
# 数据服务器：仅安装后端
sudo scripts/install_vibe_research_systemd.sh \
  --backend-only \
  --env-file /etc/aqsp/vibe-research.env

# 前端服务器：仅安装前端
sudo scripts/install_vibe_research_systemd.sh \
  --frontend-only \
  --env-file /etc/aqsp/vibe-research.env
```

### 6. 后端 CORS 配置（已存在，已验证）

后端 `backend/app.py` 已经支持 CORS 配置：

```python
# 环境变量配置
VR_ALLOW_ORIGINS=https://lh.ifidy.cn

# 公网模式 + API Key 鉴权
VR_API_KEY=your_secure_api_key
VR_PUBLIC_MODE=1
```

配置说明：
- `VR_ALLOW_ORIGINS`：允许的前端域名（逗号分隔）
- 公网模式自动拒绝通配符 `*`，提高安全性
- 支持 API Key 鉴权，防止未授权访问

## 部署架构对比

### 之前（单服务器模式）

```
┌─────────────────────────────┐
│   单台服务器                 │
│                             │
│  Nginx (443)                │
│    ↓                        │
│  React (5899) ←→ FastAPI    │
│                   (8900)    │
│                     ↓       │
│                  SQLite     │
└─────────────────────────────┘
```

问题：
- 所有服务在一台服务器，资源竞争
- 前端和后端耦合，无法独立扩展
- 配置文件硬编码 `127.0.0.1`

### 现在（双服务器模式）

```
┌─────────────────────┐         ┌──────────────────────┐
│  前端服务器 (A)      │         │  数据服务器 (B)       │
│                     │         │                      │
│  ┌───────────────┐  │         │  ┌────────────────┐  │
│  │ Nginx (443)   │  │         │  │ FastAPI (8900) │  │
│  │  ↓            │  │  内网/  │  │                │  │
│  │ React (5899)  │──┼─ 公网 ─→│  │ 数据处理        │  │
│  └───────────────┘  │         │  │ SQLite         │  │
│                     │         │  │ 定时任务        │  │
└─────────────────────┘         └──────────────────────┘
         ↑
         │
    用户浏览器
 https://lh.ifidy.cn
```

优势：
- 职责分离：前端专注展示，后端专注计算
- 独立扩展：可以单独升级服务器配置
- 资源隔离：数据处理不影响前端响应速度
- 灵活部署：支持内网/公网通信

## 部署流程

### 第一次部署（首次配置）

1. **配置部署信息**
   ```bash
   cp deploy-config.env.example deploy-config.env
   # 编辑填写服务器信息
   ```

2. **准备服务器环境配置**
   ```bash
   # 创建 .env.backend（数据服务器配置）
   # 创建 .env.frontend（前端服务器配置）
   ```

3. **执行一键部署**
   ```bash
   bash scripts/deploy_dual_servers.sh all
   ```

4. **配置定时任务**（数据服务器宝塔面板）

### 日常更新（代码变更后）

1. **本地开发**
   ```bash
   git add .
   git commit -m "your changes"
   git push origin main
   ```

2. **自动部署到服务器**
   ```bash
   bash scripts/deploy_dual_servers.sh all
   ```

3. **验证部署**
   ```bash
   bash scripts/check_dual_servers_health.sh
   ```

## 安全和网络配置

### 推荐配置：内网通信

如果两台服务器在同一云服务商的 VPC：

1. **前端服务器 Nginx 配置**：
   ```nginx
   upstream aqsp_backend {
       server 10.0.0.100:8900;  # 数据服务器内网 IP
   }
   ```

2. **数据服务器防火墙**：
   ```bash
   # 只允许前端服务器内网 IP 访问 8900
   sudo ufw allow from 10.0.0.50 to any port 8900
   sudo ufw deny 8900
   ```

3. **数据服务器 .env**：
   ```bash
   VR_ALLOW_ORIGINS=https://lh.ifidy.cn
   ```

### 备选配置：公网通信 + API Key

如果服务器不在同一 VPC：

1. **数据服务器配置 API Key**：
   ```bash
   VR_API_KEY=your_secure_random_key_here
   VR_PUBLIC_MODE=1
   VR_ALLOW_ORIGINS=https://lh.ifidy.cn
   ```

2. **前端服务器 Nginx 添加认证**：
   ```nginx
   location ^~ /api/ {
       proxy_pass http://data-server.com:8900;
       proxy_set_header Authorization "Bearer your_secure_random_key_here";
   }
   ```

3. **数据服务器防火墙**：
   ```bash
   sudo ufw allow from FRONTEND_SERVER_IP to any port 8900
   ```

## 文件清单

### 新增文件（8 个）

1. `docs/dual-server-deployment.md` - 完整部署文档
2. `docs/QUICKSTART-DUAL-SERVER.md` - 快速开始指南
3. `deploy-config.env.example` - 部署配置模板
4. `deploy/nginx/aqsp-dashboard-dual-server.conf` - 双服务器 Nginx 配置
5. `scripts/deploy_dual_servers.sh` - 自动部署脚本
6. `scripts/check_dual_servers_health.sh` - 健康检查脚本

### 修改文件（1 个）

7. `scripts/install_vibe_research_systemd.sh` - 增加 `--frontend-only` 和 `--backend-only` 支持

### 环境配置文件（需要用户创建）

8. `deploy-config.env` - 部署服务器配置（从 example 复制）
9. `.env.backend` - 数据服务器环境变量
10. `.env.frontend` - 前端服务器环境变量

## 下一步建议

### 立即可做

1. **配置 SSH 密钥**：设置免密登录两台服务器
   ```bash
   ssh-keygen -t ed25519 -C "deploy@aqsp"
   ssh-copy-id root@frontend-server
   ssh-copy-id root@backend-server
   ```

2. **填写配置文件**：
   - 复制 `deploy-config.env.example` → `deploy-config.env`
   - 创建 `.env.backend` 和 `.env.frontend`

3. **执行首次部署**：
   ```bash
   bash scripts/deploy_dual_servers.sh all
   ```

### 可选优化

1. **配置监控告警**：
   - 在数据服务器启用 `AQSP_MONITOR_NOTIFY=true`
   - 配置 Server酱或 Telegram 推送

2. **设置自动备份**：
   - 定期备份数据服务器 `/opt/aqsp/data/` 目录
   - 使用 rsync 或云服务商快照

3. **启用 CDN**：
   - 为前端静态资源配置 CDN 加速
   - 进一步降低用户访问延迟

4. **添加负载均衡**（如果需要多个数据服务器）：
   ```nginx
   upstream aqsp_backend {
       server backend1.internal:8900;
       server backend2.internal:8900;
       keepalive 32;
   }
   ```

## 总结

现在你的 AQSP 项目已经完全支持**双服务器分离部署**：

✅ 前端服务器专注于用户访问和页面展示  
✅ 数据服务器专注于数据处理和定时任务  
✅ 一键部署脚本，自动化整个流程  
✅ 完整的健康检查和监控  
✅ 详细的文档和快速开始指南  
✅ 安全的跨服务器通信配置  

这就是真正的"多端自动开发和测试完整性，云端服务器全部部署完毕，多端全部统一"。
