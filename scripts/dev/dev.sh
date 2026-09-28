#!/usr/bin/env bash
# 开发环境启动脚本
# 功能：检查环境、安装依赖、并行启动 FastAPI + Vite

set -euo pipefail

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1"
}

# 获取项目根目录（脚本所在目录的父目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ============================================
# 环境检查
# ============================================

check_python() {
    log_info "检查 Python 环境..."

    if ! command -v python3 &> /dev/null; then
        log_error "Python 3 未安装"
        echo "请安装 Python 3.10 或更高版本：https://www.python.org/downloads/"
        exit 1
    fi

    PYTHON_VERSION=$(python3 --version | awk '{print $2}')
    PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

    if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
        log_error "Python 版本过低：$PYTHON_VERSION（需要 >= 3.10）"
        exit 1
    fi

    log_success "Python $PYTHON_VERSION"
}

check_node() {
    log_info "检查 Node.js 环境..."

    if ! command -v node &> /dev/null; then
        log_error "Node.js 未安装"
        echo "请安装 Node.js 18 或更高版本：https://nodejs.org/"
        exit 1
    fi

    NODE_VERSION=$(node --version | sed 's/v//')
    NODE_MAJOR=$(echo "$NODE_VERSION" | cut -d. -f1)

    if [ "$NODE_MAJOR" -lt 18 ]; then
        log_error "Node.js 版本过低：$NODE_VERSION（推荐 >= 18）"
        exit 1
    fi

    log_success "Node.js $NODE_VERSION"
}

# ============================================
# 依赖安装
# ============================================

install_python_deps() {
    log_info "检查 Python 依赖..."

    # 检查 FastAPI 是否已安装
    if ! python3 -c "import fastapi" &> /dev/null; then
        log_warn "Python 依赖缺失，开始安装..."
        pip install -e ".[api,dev,data]" || {
            log_error "Python 依赖安装失败"
            exit 1
        }
        log_success "Python 依赖安装完成"
    else
        log_success "Python 依赖已就绪"
    fi
}

install_node_deps() {
    log_info "检查 Node.js 依赖..."

    if [ ! -d "frontend/node_modules" ]; then
        log_warn "Node.js 依赖缺失，开始安装..."
        cd frontend
        npm install || {
            log_error "Node.js 依赖安装失败"
            exit 1
        }
        cd "$PROJECT_ROOT"
        log_success "Node.js 依赖安装完成"
    else
        log_success "Node.js 依赖已就绪"
    fi
}

# ============================================
# 服务启动
# ============================================

# 临时文件用于进程ID
BACKEND_PID_FILE="/tmp/aqsp_backend_$$.pid"
FRONTEND_PID_FILE="/tmp/aqsp_frontend_$$.pid"

cleanup() {
    log_info "停止服务..."

    if [ -f "$BACKEND_PID_FILE" ]; then
        BACKEND_PID=$(cat "$BACKEND_PID_FILE")
        if kill -0 "$BACKEND_PID" 2>/dev/null; then
            kill "$BACKEND_PID" 2>/dev/null || true
            log_success "后端服务已停止"
        fi
        rm -f "$BACKEND_PID_FILE"
    fi

    if [ -f "$FRONTEND_PID_FILE" ]; then
        FRONTEND_PID=$(cat "$FRONTEND_PID_FILE")
        if kill -0 "$FRONTEND_PID" 2>/dev/null; then
            kill "$FRONTEND_PID" 2>/dev/null || true
            log_success "前端服务已停止"
        fi
        rm -f "$FRONTEND_PID_FILE"
    fi

    exit 0
}

trap cleanup SIGINT SIGTERM EXIT

start_backend() {
    log_info "启动后端服务（FastAPI on :8900）..."

    # 检查端口是否被占用
    if lsof -Pi :8900 -sTCP:LISTEN -t >/dev/null 2>&1; then
        log_warn "端口 8900 已被占用"
        read -p "是否终止占用进程并继续？[y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            lsof -ti :8900 | xargs kill -9 2>/dev/null || true
            sleep 1
        else
            log_error "无法启动后端服务"
            exit 1
        fi
    fi

    # 启动 FastAPI
    cd "$PROJECT_ROOT"
    python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8900 --reload > /tmp/aqsp_backend_$$.log 2>&1 &
    echo $! > "$BACKEND_PID_FILE"

    # 等待后端启动
    local retry=0
    while [ $retry -lt 30 ]; do
        if curl -s http://127.0.0.1:8900/api/health > /dev/null 2>&1; then
            log_success "后端服务启动成功"
            return 0
        fi
        sleep 0.5
        retry=$((retry + 1))
    done

    log_error "后端服务启动超时"
    log_info "查看日志：tail -f /tmp/aqsp_backend_$$.log"
    exit 1
}

start_frontend() {
    log_info "启动前端服务（Vite on :5899）..."

    # 检查端口是否被占用
    if lsof -Pi :5899 -sTCP:LISTEN -t >/dev/null 2>&1; then
        log_warn "端口 5899 已被占用"
        read -p "是否终止占用进程并继续？[y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            lsof -ti :5899 | xargs kill -9 2>/dev/null || true
            sleep 1
        else
            log_error "无法启动前端服务"
            exit 1
        fi
    fi

    # 启动 Vite
    cd "$PROJECT_ROOT/frontend"
    npm run dev > /tmp/aqsp_frontend_$$.log 2>&1 &
    echo $! > "$FRONTEND_PID_FILE"

    # 等待前端启动
    local retry=0
    while [ $retry -lt 30 ]; do
        if curl -s http://127.0.0.1:5899 > /dev/null 2>&1; then
            log_success "前端服务启动成功"
            return 0
        fi
        sleep 0.5
        retry=$((retry + 1))
    done

    log_error "前端服务启动超时"
    log_info "查看日志：tail -f /tmp/aqsp_frontend_$$.log"
    exit 1
}

# ============================================
# 主流程
# ============================================

main() {
    echo ""
    echo "╔════════════════════════════════════╗"
    echo "║   AI 量化选股 - 开发环境启动      ║"
    echo "╔════════════════════════════════════╝"
    echo ""

    # 环境检查
    check_python
    check_node
    echo ""

    # 依赖安装
    install_python_deps
    install_node_deps
    echo ""

    # 启动服务
    start_backend
    start_frontend
    echo ""

    # 显示访问信息
    echo "╔════════════════════════════════════╗"
    echo "║          🎉 启动成功！            ║"
    echo "╚════════════════════════════════════╝"
    echo ""
    echo "  前端：http://127.0.0.1:5899"
    echo "  后端：http://127.0.0.1:8900"
    echo "  API文档：http://127.0.0.1:8900/docs"
    echo ""
    echo "  按 Ctrl+C 停止所有服务"
    echo ""

    # 保持运行
    wait
}

main
