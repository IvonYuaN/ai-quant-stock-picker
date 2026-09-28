# Docker 部署指南

本目录包含 AI 量化选股项目的完整 Docker 化部署方案。

## 📋 目录结构

```
.
├── Dockerfile.backend          # 后端 Python/FastAPI 镜像
├── Dockerfile.frontend         # 前端 React/Nginx 镜像
├── docker-compose.yml          # Docker Compose 编排配置
├── .dockerignore              # Docker 构建排除文件
└── deploy/docker/
    ├── nginx.conf             # Nginx 反向代理配置
    └── README.md              # 本文档
```

## 🚀 快速开始

### 前置条件

- Docker Engine 20.10+
- Docker Compose 2.0+
- 至少 4GB 可用内存
- 至少 10GB 可用磁盘空间

### 1. 环境配置

复制环境变量模板并根据需要修改：

```bash
cp .env.example .env
```

**必填配置项：**

- `AQSP_SQLITE_DB_PATH`: SQLite 数据库路径（不复权 raw 库）
- LLM API Keys（至少配置一个）:
  - `GLM_API_KEY`: 智谱 GLM（推荐，免费）
  - `QWEN_API_KEY`: 通义千问（新用户 500 万 tokens）
  - `DEEPSEEK_API_KEY`: DeepSeek（付费，2 元/百万 tokens）

**可选配置：**

- `VR_API_KEY`: 公网模式 API 密钥
- `VR_PUBLIC_MODE`: 是否启用公网模式（0=本地，1=公网）
- 通知渠道：Telegram、飞书、钉钉等

### 2. 构建镜像

```bash
# 构建所有服务
docker-compose build

# 或单独构建
docker-compose build backend
docker-compose build frontend
```

### 3. 启动服务

```bash
# 启动所有服务（后台运行）
docker-compose up -d

# 查看日志
docker-compose logs -f

# 查看特定服务日志
docker-compose logs -f backend
docker-compose logs -f frontend
```

### 4. 访问应用

- **前端界面**: http://localhost
- **后端 API**: http://localhost:8900
- **健康检查**: http://localhost:8900/api/health

### 5. 停止服务

```bash
# 停止服务
docker-compose stop

# 停止并删除容器
docker-compose down

# 停止并删除容器、网络、卷（谨慎使用！）
docker-compose down -v
```

## 📦 镜像架构

### Backend (Dockerfile.backend)

**多阶段构建：**

1. **Builder 阶段**
   - 基于 `python:3.10-slim`
   - 安装编译依赖（gcc、g++）
   - 创建虚拟环境
   - 安装 Python 依赖：`[data,api,dev]` extras

2. **Production 阶段**
   - 轻量级运行时镜像
   - 非 root 用户运行（`aqsp` 用户）
   - 复制虚拟环境和应用代码
   - 健康检查：`/api/health` 端点
   - 暴露端口：8900

**镜像大小优化：**
- 使用 slim 基础镜像
- 多阶段构建分离编译和运行时
- `--no-cache-dir` 减少 pip 缓存
- 清理 apt 缓存

### Frontend (Dockerfile.frontend)

**多阶段构建：**

1. **Builder 阶段**
   - 基于 `node:20-alpine`
   - `npm ci` 安装依赖
   - 构建生产环境静态文件

2. **Production 阶段**
   - 基于 `nginx:1.25-alpine`
   - 复制自定义 nginx 配置
   - 复制构建产物
   - 健康检查：HTTP GET /
   - 暴露端口：80

**特性：**
- Gzip 压缩
- 静态资源缓存（1 年）
- 反向代理到后端 `/api/*`
- 安全头（X-Frame-Options, X-XSS-Protection 等）

## 🔧 配置详解

### docker-compose.yml

#### Backend 服务

**环境变量分类：**

1. **API 配置**
   - `VR_API_KEY`: API 密钥（公网模式必填）
   - `VR_PUBLIC_MODE`: 公网模式开关
   - `VR_ALLOW_ORIGINS`: CORS 白名单

2. **AQSP 核心配置**
   - `AQSP_MODE`: open（开盘前）或 close（收盘后）
   - `AQSP_LIMIT`: 候选池大小（默认 10）
   - `AQSP_SOURCE`: 数据源类型（sqlite_db）

3. **LLM 配置**
   - `LLM_PROVIDER`: glm/qwen/deepseek/openai 等
   - 对应 API Keys 和模型名称

4. **通知配置**
   - 支持 Telegram、飞书、钉钉等多种渠道

**数据卷映射：**

```yaml
volumes:
  - ./data:/app/data                      # 数据库、缓存、台账
  - ./reports:/app/reports                # 研究报告输出
  - ./logs:/app/logs                      # 日志文件
  - ./pit_cache:/app/pit_cache            # Point-in-time 缓存
  - ./A股量化分析数据:/app/A股量化分析数据  # 量化数据
  - ./config:/app/config:ro               # 配置文件（只读）
  - ./.env:/app/.env:ro                   # 环境变量（只读）
```

**健康检查：**
- 检查 `/api/health` 端点
- 间隔 30 秒，超时 10 秒
- 启动宽限期 40 秒
- 失败重试 3 次

#### Frontend 服务

**依赖关系：**
```yaml
depends_on:
  backend:
    condition: service_healthy
```
确保后端健康后才启动前端。

**Nginx 配置亮点：**
- API 请求代理到 `backend:8900`
- WebSocket 支持（Upgrade 头）
- 静态资源长期缓存
- Gzip 压缩

### 网络配置

```yaml
networks:
  aqsp-network:
    driver: bridge
```

服务间通过桥接网络通信，前端通过服务名 `backend` 访问后端。

## 🔒 安全最佳实践

### 1. 非 root 用户运行

后端使用专用用户 `aqsp`（UID 1000）运行，限制容器权限。

### 2. 只读挂载

配置文件和环境变量以只读方式挂载（`:ro`），防止容器内修改。

### 3. 环境变量管理

- 敏感信息（API Keys）存储在 `.env` 文件
- `.env` 文件不应提交到版本控制（已在 `.gitignore`）
- 生产环境建议使用 Docker Secrets

### 4. 网络隔离

后端和前端运行在隔离的 Docker 网络中，只暴露必要端口。

### 5. 安全头

Nginx 配置包含安全响应头：
- `X-Frame-Options: SAMEORIGIN`
- `X-Content-Type-Options: nosniff`
- `X-XSS-Protection: 1; mode=block`

## 📊 监控与日志

### 查看日志

```bash
# 实时日志
docker-compose logs -f

# 最近 100 行
docker-compose logs --tail=100

# 特定服务
docker-compose logs -f backend
```

### 健康检查状态

```bash
# 查看容器状态
docker-compose ps

# 健康检查详情
docker inspect aqsp-backend | jq '.[0].State.Health'
```

### 资源使用

```bash
# 实时资源监控
docker stats

# 特定容器
docker stats aqsp-backend aqsp-frontend
```

## 🐛 故障排查

### 后端启动失败

**症状**: 容器反复重启

**排查步骤**:

1. 查看日志：
   ```bash
   docker-compose logs backend
   ```

2. 检查数据库路径：
   ```bash
   # .env 文件中的 AQSP_SQLITE_DB_PATH 是否正确
   # 数据库文件是否存在且可读
   ls -lh ./data/astocks_raw.db
   ```

3. 检查依赖安装：
   ```bash
   docker-compose exec backend pip list
   ```

4. 手动测试：
   ```bash
   docker-compose exec backend python -c "import fastapi; print('OK')"
   ```

### 前端无法访问后端

**症状**: 前端界面加载，但 API 调用失败

**排查步骤**:

1. 检查后端健康状态：
   ```bash
   curl http://localhost:8900/api/health
   ```

2. 检查 Nginx 配置：
   ```bash
   docker-compose exec frontend nginx -t
   ```

3. 查看 Nginx 日志：
   ```bash
   docker-compose logs frontend | grep -i error
   ```

4. 测试网络连通性：
   ```bash
   docker-compose exec frontend ping backend
   ```

### 数据卷权限问题

**症状**: 无法写入数据文件

**解决方案**:

```bash
# 修改宿主机目录权限
chmod 755 data reports logs pit_cache

# 或修改所有者（UID 1000 对应容器内 aqsp 用户）
sudo chown -R 1000:1000 data reports logs pit_cache
```

### 内存不足

**症状**: 容器被 OOM Killer 杀死

**解决方案**:

1. 增加 Docker 内存限制：
   ```yaml
   # docker-compose.yml
   services:
     backend:
       mem_limit: 2g
       memswap_limit: 2g
   ```

2. 减少后端 worker 数量：
   ```yaml
   command: ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8900", "--workers", "1"]
   ```

## 🚀 生产环境部署

### 1. 使用生产级 WSGI 服务器

当前使用 uvicorn 单 worker，生产环境建议：

```yaml
# docker-compose.prod.yml
services:
  backend:
    command: ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8900", "--workers", "4"]
```

### 2. 添加反向代理（可选）

在 `docker-compose.yml` 外层添加 Nginx/Traefik 反向代理：

```yaml
services:
  nginx-proxy:
    image: nginx:1.25-alpine
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - /etc/letsencrypt:/etc/letsencrypt:ro
      - ./nginx-proxy.conf:/etc/nginx/nginx.conf:ro
```

### 3. 使用 Docker Secrets

```bash
# 创建 secret
echo "your_api_key" | docker secret create glm_api_key -

# docker-compose.yml
services:
  backend:
    secrets:
      - glm_api_key
    environment:
      - GLM_API_KEY_FILE=/run/secrets/glm_api_key

secrets:
  glm_api_key:
    external: true
```

### 4. 配置自动重启策略

```yaml
services:
  backend:
    restart: always  # 生产环境使用 always
  frontend:
    restart: always
```

### 5. 启用日志驱动

```yaml
services:
  backend:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

## 📈 性能优化

### 1. 镜像构建缓存

利用 Docker 层缓存加速构建：

```bash
# 使用 BuildKit
DOCKER_BUILDKIT=1 docker-compose build

# 或设置环境变量
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1
```

### 2. 多阶段构建优化

当前 Dockerfile 已采用多阶段构建，进一步优化：

```dockerfile
# 分离依赖安装和代码复制
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[data,api,dev]"
COPY . .  # 代码变化不会重新安装依赖
```

### 3. 使用 .dockerignore

确保 `.dockerignore` 排除不必要文件，减少构建上下文。

### 4. 预加载数据

```bash
# 在构建时预加载静态数据
docker-compose run --rm backend python scripts/preload_data.py
```

## 🔄 更新与维护

### 更新应用代码

```bash
# 1. 拉取最新代码
git pull

# 2. 重新构建镜像
docker-compose build

# 3. 重启服务（零停机）
docker-compose up -d

# 4. 清理旧镜像
docker image prune -f
```

### 备份数据

```bash
# 备份数据卷
docker run --rm \
  -v "$(pwd)/data:/source:ro" \
  -v "$(pwd)/backups:/backup" \
  alpine tar czf /backup/data-$(date +%Y%m%d).tar.gz -C /source .

# 恢复数据
docker run --rm \
  -v "$(pwd)/data:/target" \
  -v "$(pwd)/backups:/backup:ro" \
  alpine tar xzf /backup/data-20260928.tar.gz -C /target
```

### 数据库迁移

```bash
# 进入后端容器
docker-compose exec backend bash

# 运行迁移脚本
python scripts/migrate_db.py
```

## 📚 常用命令速查

```bash
# 构建
docker-compose build                    # 构建所有服务
docker-compose build --no-cache         # 无缓存构建

# 启动/停止
docker-compose up -d                    # 后台启动
docker-compose down                     # 停止并删除容器
docker-compose restart                  # 重启服务

# 日志
docker-compose logs -f                  # 实时日志
docker-compose logs --tail=100 backend  # 最近 100 行

# 执行命令
docker-compose exec backend bash        # 进入后端容器
docker-compose exec backend python      # 运行 Python
docker-compose run --rm backend pytest  # 运行测试

# 清理
docker-compose down -v                  # 删除卷（危险！）
docker system prune -a                  # 清理所有未使用资源
```

## 🆘 获取帮助

- **GitHub Issues**: [项目 Issues 页面]
- **文档**: 查看 `docs/` 目录
- **健康检查**: http://localhost:8900/api/health

## 📝 变更日志

- **2026-09-28**: 初始版本
  - 多阶段构建优化
  - 健康检查配置
  - 完整环境变量支持
  - 生产级安全配置
