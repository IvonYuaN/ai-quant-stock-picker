#!/usr/bin/env bash
# AQSP 双服务器部署脚本
# 功能：自动部署前端服务器和数据服务器
#
# 使用方法：
# 1. 配置 deploy-config.env 文件
# 2. 运行: bash scripts/deploy_dual_servers.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 加载配置文件
CONFIG_FILE="${PROJECT_ROOT}/deploy-config.env"
if [[ ! -f "$CONFIG_FILE" ]]; then
    log_error "配置文件不存在: $CONFIG_FILE"
    log_info "请从 deploy-config.env.example 复制并配置"
    exit 1
fi

source "$CONFIG_FILE"

# 验证必需的配置变量
: "${FRONTEND_SERVER:?请在 $CONFIG_FILE 中设置 FRONTEND_SERVER}"
: "${FRONTEND_USER:?请在 $CONFIG_FILE 中设置 FRONTEND_USER}"
: "${BACKEND_SERVER:?请在 $CONFIG_FILE 中设置 BACKEND_SERVER}"
: "${BACKEND_USER:?请在 $CONFIG_FILE 中设置 BACKEND_USER}"
: "${DEPLOY_PATH:=/opt/aqsp}"
: "${GITHUB_REPO:?请在 $CONFIG_FILE 中设置 GITHUB_REPO}"

# SSH 配置
FRONTEND_SSH="${FRONTEND_USER}@${FRONTEND_SERVER}"
BACKEND_SSH="${BACKEND_USER}@${BACKEND_SERVER}"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"

# 检查 SSH 连接
check_ssh_connection() {
    local host=$1
    local name=$2

    log_info "检查 ${name} SSH 连接: $host"
    if ssh $SSH_OPTS "$host" "echo 'SSH OK'" >/dev/null 2>&1; then
        log_success "${name} SSH 连接正常"
        return 0
    else
        log_error "${name} SSH 连接失败: $host"
        return 1
    fi
}

# 在远程服务器上执行命令
remote_exec() {
    local host=$1
    shift
    ssh $SSH_OPTS "$host" "$@"
}

# 部署数据服务器（后端）
deploy_backend() {
    log_info "=========================================="
    log_info "开始部署数据服务器 (后端)"
    log_info "=========================================="

    check_ssh_connection "$BACKEND_SSH" "数据服务器" || return 1

    # 检查并克隆/更新代码
    log_info "同步代码到数据服务器..."
    remote_exec "$BACKEND_SSH" bash <<EOF
set -euo pipefail

if [[ -d "$DEPLOY_PATH" ]]; then
    echo "目录已存在，执行 git pull..."
    cd "$DEPLOY_PATH"
    git fetch origin
    git reset --hard origin/main
    git pull --ff-only origin main
else
    echo "克隆代码仓库..."
    sudo mkdir -p "$DEPLOY_PATH"
    sudo chown -R \$(whoami):\$(whoami) "$DEPLOY_PATH"
    git clone "$GITHUB_REPO" "$DEPLOY_PATH"
    cd "$DEPLOY_PATH"
fi

echo "当前版本: \$(git rev-parse --short HEAD)"
EOF

    # 安装后端依赖
    log_info "安装后端依赖..."
    remote_exec "$BACKEND_SSH" bash <<EOF
set -euo pipefail
cd "$DEPLOY_PATH"

# 创建虚拟环境（如果不存在）
if [[ ! -d ".venv" ]]; then
    echo "创建 Python 虚拟环境..."
    python3 -m venv .venv
fi

# 安装依赖
echo "安装 Python 依赖..."
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[data,api]"
EOF

    # 配置环境变量
    log_info "配置数据服务器环境变量..."
    if [[ -f "${PROJECT_ROOT}/.env.backend" ]]; then
        log_info "上传 .env.backend 到数据服务器..."
        scp $SSH_OPTS "${PROJECT_ROOT}/.env.backend" "${BACKEND_SSH}:${DEPLOY_PATH}/.env"
    else
        log_warn ".env.backend 文件不存在，跳过环境变量配置"
        log_info "请手动在数据服务器上配置 ${DEPLOY_PATH}/.env"
    fi

    # 安装 systemd 服务（仅后端）
    log_info "配置数据服务器 systemd 服务..."
    remote_exec "$BACKEND_SSH" sudo bash <<EOF
set -euo pipefail
cd "$DEPLOY_PATH"

# 创建 systemd 环境文件目录
mkdir -p /etc/aqsp

# 复制环境文件
if [[ -f "${DEPLOY_PATH}/.env" ]]; then
    cp "${DEPLOY_PATH}/.env" /etc/aqsp/vibe-research.env
fi

# 安装 systemd 服务（仅后端）
bash scripts/install_vibe_research_systemd.sh \\
    --env-file /etc/aqsp/vibe-research.env \\
    --backend-only \\
    --no-start
EOF

    # 重启后端服务
    log_info "重启数据服务器后端服务..."
    remote_exec "$BACKEND_SSH" sudo systemctl restart aqsp-vibe-research-api.service
    remote_exec "$BACKEND_SSH" sudo systemctl enable aqsp-vibe-research-api.service

    # 验证后端服务
    log_info "验证数据服务器后端服务..."
    sleep 3
    if remote_exec "$BACKEND_SSH" "curl -sf http://127.0.0.1:8900/api/health" >/dev/null; then
        log_success "数据服务器后端服务运行正常"
    else
        log_error "数据服务器后端服务启动失败"
        remote_exec "$BACKEND_SSH" "sudo systemctl status aqsp-vibe-research-api.service"
        return 1
    fi

    log_success "数据服务器部署完成"
}

# 部署前端服务器
deploy_frontend() {
    log_info "=========================================="
    log_info "开始部署前端服务器"
    log_info "=========================================="

    check_ssh_connection "$FRONTEND_SSH" "前端服务器" || return 1

    # 检查并克隆/更新代码
    log_info "同步代码到前端服务器..."
    remote_exec "$FRONTEND_SSH" bash <<EOF
set -euo pipefail

if [[ -d "$DEPLOY_PATH" ]]; then
    echo "目录已存在，执行 git pull..."
    cd "$DEPLOY_PATH"
    git fetch origin
    git reset --hard origin/main
    git pull --ff-only origin main
else
    echo "克隆代码仓库..."
    sudo mkdir -p "$DEPLOY_PATH"
    sudo chown -R \$(whoami):\$(whoami) "$DEPLOY_PATH"
    git clone "$GITHUB_REPO" "$DEPLOY_PATH"
    cd "$DEPLOY_PATH"
fi

echo "当前版本: \$(git rev-parse --short HEAD)"
EOF

    # 构建前端
    log_info "构建前端应用..."
    remote_exec "$FRONTEND_SSH" bash <<EOF
set -euo pipefail
cd "$DEPLOY_PATH/frontend"

# 安装依赖
echo "安装 npm 依赖..."
npm install

# 构建
echo "构建前端..."
npm run build
EOF

    # 配置环境变量
    log_info "配置前端服务器环境变量..."
    if [[ -f "${PROJECT_ROOT}/.env.frontend" ]]; then
        log_info "上传 .env.frontend 到前端服务器..."
        scp $SSH_OPTS "${PROJECT_ROOT}/.env.frontend" "${FRONTEND_SSH}:${DEPLOY_PATH}/.env"
    else
        log_warn ".env.frontend 文件不存在，跳过环境变量配置"
    fi

    # 安装 systemd 服务（仅前端）
    log_info "配置前端服务器 systemd 服务..."
    remote_exec "$FRONTEND_SSH" sudo bash <<EOF
set -euo pipefail
cd "$DEPLOY_PATH"

# 创建 Python 虚拟环境（前端服务的健康检查脚本需要）
if [[ ! -d ".venv" ]]; then
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
fi

# 创建 systemd 环境文件目录
mkdir -p /etc/aqsp

# 复制环境文件
if [[ -f "${DEPLOY_PATH}/.env" ]]; then
    cp "${DEPLOY_PATH}/.env" /etc/aqsp/vibe-research.env
fi

# 安装 systemd 服务（仅前端）
bash scripts/install_vibe_research_systemd.sh \\
    --env-file /etc/aqsp/vibe-research.env \\
    --frontend-only \\
    --no-start
EOF

    # 更新 Nginx 配置
    log_info "更新前端服务器 Nginx 配置..."

    # 获取数据服务器地址（内网或公网）
    BACKEND_HOST="${BACKEND_SERVER_INTERNAL:-$BACKEND_SERVER}"

    remote_exec "$FRONTEND_SSH" sudo bash <<EOF
set -euo pipefail

# 备份现有配置
NGINX_CONF_DIR="/www/server/panel/vhost/nginx/proxy/${FRONTEND_DOMAIN}"
if [[ -d "\$NGINX_CONF_DIR" ]]; then
    cp "\$NGINX_CONF_DIR/aqsp-dashboard.conf" "\$NGINX_CONF_DIR/aqsp-dashboard.conf.backup.\$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
fi

# 创建配置目录
mkdir -p "\$NGINX_CONF_DIR"

# 从代码仓库复制新配置
cp "$DEPLOY_PATH/deploy/nginx/aqsp-dashboard-dual-server.conf" "\$NGINX_CONF_DIR/aqsp-dashboard.conf"

# 替换数据服务器地址
sed -i "s/DATA_SERVER_IP/${BACKEND_HOST}/g" "\$NGINX_CONF_DIR/aqsp-dashboard.conf"

# 测试 Nginx 配置
echo "测试 Nginx 配置..."
/www/server/nginx/sbin/nginx -t

# 重载 Nginx
echo "重载 Nginx..."
/etc/init.d/nginx reload
EOF

    # 重启前端服务
    log_info "重启前端服务器前端服务..."
    remote_exec "$FRONTEND_SSH" sudo systemctl restart aqsp-vibe-research-preview.service
    remote_exec "$FRONTEND_SSH" sudo systemctl enable aqsp-vibe-research-preview.service

    # 验证前端服务
    log_info "验证前端服务器前端服务..."
    sleep 3
    if remote_exec "$FRONTEND_SSH" "curl -sf http://127.0.0.1:5899/ | grep -q 'AQSP'"; then
        log_success "前端服务器前端服务运行正常"
    else
        log_error "前端服务器前端服务启动失败"
        remote_exec "$FRONTEND_SSH" "sudo systemctl status aqsp-vibe-research-preview.service"
        return 1
    fi

    log_success "前端服务器部署完成"
}

# 端到端健康检查
health_check() {
    log_info "=========================================="
    log_info "执行端到端健康检查"
    log_info "=========================================="

    # 检查后端健康
    log_info "检查数据服务器健康..."
    if remote_exec "$BACKEND_SSH" "curl -sf http://127.0.0.1:8900/api/health"; then
        log_success "数据服务器健康检查通过"
    else
        log_error "数据服务器健康检查失败"
        return 1
    fi

    # 检查前端健康
    log_info "检查前端服务器健康..."
    if remote_exec "$FRONTEND_SSH" "curl -sf http://127.0.0.1:5899/ | grep -q 'AQSP'"; then
        log_success "前端服务器健康检查通过"
    else
        log_error "前端服务器健康检查失败"
        return 1
    fi

    # 检查前端到后端连通性
    log_info "检查前端到后端连通性..."
    if remote_exec "$FRONTEND_SSH" "curl -sf https://${FRONTEND_DOMAIN}/api/health"; then
        log_success "前端到后端连通性正常"
    else
        log_error "前端无法访问后端 API"
        return 1
    fi

    # 检查公网访问
    if [[ -n "${FRONTEND_DOMAIN:-}" ]]; then
        log_info "检查公网访问..."
        if curl -sf "https://${FRONTEND_DOMAIN}/api/health" >/dev/null 2>&1; then
            log_success "公网访问正常: https://${FRONTEND_DOMAIN}"
        else
            log_warn "公网访问失败，请检查域名解析和防火墙配置"
        fi
    fi

    log_success "健康检查全部通过"
}

# 显示部署信息
show_deployment_info() {
    log_info "=========================================="
    log_info "部署完成信息"
    log_info "=========================================="
    echo ""
    echo "数据服务器 (后端):"
    echo "  - 服务器: ${BACKEND_SERVER}"
    echo "  - FastAPI: http://${BACKEND_SERVER}:8900"
    echo "  - 健康检查: curl http://127.0.0.1:8900/api/health"
    echo "  - 服务状态: sudo systemctl status aqsp-vibe-research-api.service"
    echo "  - 日志: sudo journalctl -u aqsp-vibe-research-api.service -f"
    echo ""
    echo "前端服务器:"
    echo "  - 服务器: ${FRONTEND_SERVER}"
    echo "  - React: http://${FRONTEND_SERVER}:5899"
    echo "  - 公网访问: https://${FRONTEND_DOMAIN}"
    echo "  - 服务状态: sudo systemctl status aqsp-vibe-research-preview.service"
    echo "  - 日志: sudo journalctl -u aqsp-vibe-research-preview.service -f"
    echo ""
    echo "查看完整日志:"
    echo "  数据服务器: ssh ${BACKEND_SSH} 'tail -f ${DEPLOY_PATH}/logs/api.log'"
    echo "  前端服务器: ssh ${FRONTEND_SSH} 'tail -f ${DEPLOY_PATH}/logs/frontend.log'"
    echo ""
}

# 主函数
main() {
    local deploy_target="${1:-all}"

    case "$deploy_target" in
        backend)
            deploy_backend
            ;;
        frontend)
            deploy_frontend
            ;;
        all)
            deploy_backend
            deploy_frontend
            health_check
            show_deployment_info
            ;;
        check)
            health_check
            ;;
        *)
            echo "用法: $0 [backend|frontend|all|check]"
            echo ""
            echo "  backend   - 仅部署数据服务器"
            echo "  frontend  - 仅部署前端服务器"
            echo "  all       - 部署所有服务器（默认）"
            echo "  check     - 仅执行健康检查"
            exit 1
            ;;
    esac
}

main "$@"
