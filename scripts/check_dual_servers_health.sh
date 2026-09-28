#!/usr/bin/env bash
# AQSP 双服务器健康检查脚本
# 功能：检查前端服务器和数据服务器的运行状态和连通性
#
# 使用方法：
# 1. 配置 deploy-config.env 文件
# 2. 运行: bash scripts/check_dual_servers_health.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[!]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

log_section() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}$1${NC}"
    echo -e "${CYAN}========================================${NC}"
}

# 加载配置文件
CONFIG_FILE="${PROJECT_ROOT}/deploy-config.env"
if [[ -f "$CONFIG_FILE" ]]; then
    source "$CONFIG_FILE"
    FRONTEND_SERVER="${FRONTEND_SERVER:-}"
    FRONTEND_USER="${FRONTEND_USER:-root}"
    BACKEND_SERVER="${BACKEND_SERVER:-}"
    BACKEND_USER="${BACKEND_USER:-root}"
    FRONTEND_DOMAIN="${FRONTEND_DOMAIN:-}"
    DEPLOY_PATH="${DEPLOY_PATH:-/opt/aqsp}"
else
    log_warn "配置文件不存在: $CONFIG_FILE"
    log_info "将仅执行本地检查"
    FRONTEND_SERVER=""
    BACKEND_SERVER=""
fi

# SSH 配置
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 -o BatchMode=yes"
FRONTEND_SSH="${FRONTEND_USER}@${FRONTEND_SERVER}"
BACKEND_SSH="${BACKEND_USER}@${BACKEND_SERVER}"

# 计数器
TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0

# 执行检查并记录结果
run_check() {
    local check_name="$1"
    local check_cmd="$2"

    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

    if eval "$check_cmd" >/dev/null 2>&1; then
        log_success "$check_name"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
        return 0
    else
        log_error "$check_name"
        FAILED_CHECKS=$((FAILED_CHECKS + 1))
        return 1
    fi
}

# 在远程服务器上执行命令
remote_exec() {
    local host=$1
    shift
    ssh $SSH_OPTS "$host" "$@" 2>/dev/null
}

# 检查远程服务器 SSH 连接
check_remote_ssh() {
    local host=$1
    local name=$2

    log_section "检查 ${name} SSH 连接"

    if run_check "${name} SSH 连接" "ssh $SSH_OPTS '$host' 'echo OK'"; then
        # 获取服务器信息
        local os_info=$(remote_exec "$host" "cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'" || echo "Unknown")
        local uptime=$(remote_exec "$host" "uptime -p" || echo "Unknown")
        local load=$(remote_exec "$host" "uptime | awk -F'load average:' '{print \$2}'" || echo "Unknown")

        echo "  系统: $os_info"
        echo "  运行时间: $uptime"
        echo "  负载:$load"
    fi
}

# 检查数据服务器（后端）
check_backend_server() {
    log_section "数据服务器健康检查"

    if [[ -z "$BACKEND_SERVER" ]]; then
        log_warn "未配置数据服务器地址，跳过远程检查"
        return
    fi

    check_remote_ssh "$BACKEND_SSH" "数据服务器"

    # 检查代码目录
    run_check "代码目录存在" "remote_exec '$BACKEND_SSH' 'test -d $DEPLOY_PATH'"

    # 检查 Python 虚拟环境
    run_check "Python 虚拟环境" "remote_exec '$BACKEND_SSH' 'test -d $DEPLOY_PATH/.venv'"

    # 检查环境配置文件
    run_check "环境配置文件" "remote_exec '$BACKEND_SSH' 'test -f $DEPLOY_PATH/.env'"

    # 检查 systemd 服务状态
    if run_check "FastAPI systemd 服务" "remote_exec '$BACKEND_SSH' 'sudo systemctl is-active aqsp-vibe-research-api.service'"; then
        # 获取服务详细状态
        local service_status=$(remote_exec "$BACKEND_SSH" "sudo systemctl status aqsp-vibe-research-api.service | grep 'Active:' | awk '{print \$2, \$3}'" || echo "Unknown")
        echo "  服务状态: $service_status"
    fi

    # 检查端口监听
    run_check "FastAPI 端口 8900 监听" "remote_exec '$BACKEND_SSH' 'ss -tlnp | grep -q :8900'"

    # 检查 API 健康端点
    if run_check "FastAPI 健康端点" "remote_exec '$BACKEND_SSH' 'curl -sf http://127.0.0.1:8900/api/health'"; then
        local health_response=$(remote_exec "$BACKEND_SSH" "curl -s http://127.0.0.1:8900/api/health")
        echo "  响应: $health_response"
    fi

    # 检查数据文件
    log_info "检查数据文件..."
    remote_exec "$BACKEND_SSH" "ls -lh $DEPLOY_PATH/data/*.jsonl 2>/dev/null | tail -5" || log_warn "  未找到 ledger 文件"

    # 检查日志文件
    log_info "最近的 API 日志（最后 5 行）:"
    remote_exec "$BACKEND_SSH" "tail -5 $DEPLOY_PATH/logs/api.log 2>/dev/null" || log_warn "  日志文件不存在"
}

# 检查前端服务器
check_frontend_server() {
    log_section "前端服务器健康检查"

    if [[ -z "$FRONTEND_SERVER" ]]; then
        log_warn "未配置前端服务器地址，跳过远程检查"
        return
    fi

    check_remote_ssh "$FRONTEND_SSH" "前端服务器"

    # 检查代码目录
    run_check "代码目录存在" "remote_exec '$FRONTEND_SSH' 'test -d $DEPLOY_PATH'"

    # 检查前端构建产物
    run_check "前端构建产物" "remote_exec '$FRONTEND_SSH' 'test -d $DEPLOY_PATH/frontend/dist'"

    # 检查 systemd 服务状态
    if run_check "React systemd 服务" "remote_exec '$FRONTEND_SSH' 'sudo systemctl is-active aqsp-vibe-research-preview.service'"; then
        local service_status=$(remote_exec "$FRONTEND_SSH" "sudo systemctl status aqsp-vibe-research-preview.service | grep 'Active:' | awk '{print \$2, \$3}'" || echo "Unknown")
        echo "  服务状态: $service_status"
    fi

    # 检查端口监听
    run_check "React 端口 5899 监听" "remote_exec '$FRONTEND_SSH' 'ss -tlnp | grep -q :5899'"

    # 检查前端页面
    if run_check "React 前端页面" "remote_exec '$FRONTEND_SSH' 'curl -sf http://127.0.0.1:5899/ | grep -q AQSP'"; then
        echo "  前端页面加载正常"
    fi

    # 检查 Nginx 配置
    log_info "检查 Nginx 配置..."
    if remote_exec "$FRONTEND_SSH" "test -f /www/server/panel/vhost/nginx/proxy/${FRONTEND_DOMAIN}/aqsp-dashboard.conf"; then
        log_success "Nginx 配置文件存在"

        # 检查配置中的后端地址
        local backend_in_nginx=$(remote_exec "$FRONTEND_SSH" "grep 'upstream aqsp_backend' -A 3 /www/server/panel/vhost/nginx/proxy/${FRONTEND_DOMAIN}/aqsp-dashboard.conf | grep 'server' | awk '{print \$2}'" || echo "未配置")
        echo "  后端地址配置: $backend_in_nginx"
    else
        log_warn "Nginx 配置文件不存在"
    fi

    # 检查 Nginx 状态
    run_check "Nginx 服务运行" "remote_exec '$FRONTEND_SSH' 'sudo systemctl is-active nginx'"

    # 检查最近的前端日志
    log_info "最近的前端日志（最后 5 行）:"
    remote_exec "$FRONTEND_SSH" "tail -5 $DEPLOY_PATH/logs/frontend.log 2>/dev/null" || log_warn "  日志文件不存在"
}

# 检查跨服务器连通性
check_connectivity() {
    log_section "跨服务器连通性检查"

    if [[ -z "$FRONTEND_SERVER" ]] || [[ -z "$BACKEND_SERVER" ]]; then
        log_warn "未配置服务器地址，跳过连通性检查"
        return
    fi

    # 前端服务器到数据服务器的连通性
    log_info "检查前端服务器到数据服务器的网络连通性..."
    if run_check "前端→数据服务器 ping" "remote_exec '$FRONTEND_SSH' 'ping -c 1 -W 2 $BACKEND_SERVER'"; then
        local ping_time=$(remote_exec "$FRONTEND_SSH" "ping -c 3 $BACKEND_SERVER | grep 'avg' | awk -F'/' '{print \$5}'" || echo "N/A")
        echo "  平均延迟: ${ping_time}ms"
    fi

    # 前端服务器到数据服务器 API 端口的连通性
    run_check "前端→数据服务器 8900 端口" "remote_exec '$FRONTEND_SSH' 'timeout 3 bash -c \"</dev/tcp/$BACKEND_SERVER/8900\"'"

    # 前端服务器通过 Nginx 访问后端 API
    if [[ -n "$FRONTEND_DOMAIN" ]]; then
        run_check "前端通过域名访问后端 API" "remote_exec '$FRONTEND_SSH' 'curl -sf https://${FRONTEND_DOMAIN}/api/health'"
    fi
}

# 检查公网访问
check_public_access() {
    log_section "公网访问检查"

    if [[ -z "$FRONTEND_DOMAIN" ]]; then
        log_warn "未配置公网域名，跳过公网访问检查"
        return
    fi

    # 检查域名解析
    log_info "检查域名解析: $FRONTEND_DOMAIN"
    if command -v dig >/dev/null 2>&1; then
        local ip=$(dig +short "$FRONTEND_DOMAIN" | tail -1)
        if [[ -n "$ip" ]]; then
            log_success "域名解析: $FRONTEND_DOMAIN → $ip"
        else
            log_error "域名解析失败"
        fi
    else
        log_warn "dig 命令不可用，跳过域名解析检查"
    fi

    # 检查 HTTPS 访问
    if run_check "HTTPS 访问主页" "curl -sf --max-time 10 'https://${FRONTEND_DOMAIN}/' | grep -q 'AQSP'"; then
        local response_time=$(curl -o /dev/null -s -w '%{time_total}' "https://${FRONTEND_DOMAIN}/" || echo "N/A")
        echo "  响应时间: ${response_time}s"
    fi

    # 检查 API 健康端点
    if run_check "HTTPS API 健康检查" "curl -sf --max-time 10 'https://${FRONTEND_DOMAIN}/api/health'"; then
        local health_data=$(curl -s "https://${FRONTEND_DOMAIN}/api/health")
        echo "  健康检查响应: $health_data"
    fi

    # 检查 SSL 证书
    log_info "检查 SSL 证书..."
    if command -v openssl >/dev/null 2>&1; then
        local cert_info=$(echo | openssl s_client -servername "$FRONTEND_DOMAIN" -connect "${FRONTEND_DOMAIN}:443" 2>/dev/null | openssl x509 -noout -dates 2>/dev/null)
        if [[ -n "$cert_info" ]]; then
            echo "$cert_info" | while read line; do echo "  $line"; done
        else
            log_warn "无法获取 SSL 证书信息"
        fi
    fi
}

# 检查本地代码状态
check_local_status() {
    log_section "本地代码状态"

    cd "$PROJECT_ROOT"

    # Git 状态
    log_info "Git 状态:"
    local current_branch=$(git branch --show-current)
    local current_commit=$(git rev-parse --short HEAD)
    local commit_message=$(git log -1 --pretty=format:'%s')

    echo "  分支: $current_branch"
    echo "  提交: $current_commit"
    echo "  消息: $commit_message"

    # 检查未提交的更改
    if git diff --quiet && git diff --cached --quiet; then
        log_success "没有未提交的更改"
    else
        log_warn "有未提交的更改"
        git status --short | head -10
    fi

    # 检查是否需要推送
    local ahead=$(git rev-list --count @{u}..HEAD 2>/dev/null || echo "0")
    if [[ "$ahead" -gt 0 ]]; then
        log_warn "有 $ahead 个本地提交未推送到远程"
    else
        log_success "本地代码与远程同步"
    fi
}

# 生成健康报告摘要
print_summary() {
    log_section "健康检查摘要"

    echo ""
    echo "总检查项: $TOTAL_CHECKS"
    echo -e "${GREEN}通过: $PASSED_CHECKS${NC}"
    echo -e "${RED}失败: $FAILED_CHECKS${NC}"
    echo ""

    local pass_rate=$((PASSED_CHECKS * 100 / TOTAL_CHECKS))

    if [[ $pass_rate -ge 90 ]]; then
        log_success "系统健康状态: 优秀 (${pass_rate}%)"
    elif [[ $pass_rate -ge 70 ]]; then
        log_warn "系统健康状态: 良好 (${pass_rate}%)"
    elif [[ $pass_rate -ge 50 ]]; then
        log_warn "系统健康状态: 一般 (${pass_rate}%)"
    else
        log_error "系统健康状态: 需要关注 (${pass_rate}%)"
    fi

    echo ""

    if [[ $FAILED_CHECKS -gt 0 ]]; then
        echo "建议操作:"
        echo "  1. 检查失败项的日志文件"
        echo "  2. 重启相关服务"
        echo "  3. 查看系统资源使用情况"
        echo ""
        echo "查看日志命令:"
        [[ -n "$BACKEND_SERVER" ]] && echo "  数据服务器: ssh ${BACKEND_SSH} 'sudo journalctl -u aqsp-vibe-research-api.service -f'"
        [[ -n "$FRONTEND_SERVER" ]] && echo "  前端服务器: ssh ${FRONTEND_SSH} 'sudo journalctl -u aqsp-vibe-research-preview.service -f'"
    fi
}

# 主函数
main() {
    echo ""
    echo "AQSP 双服务器健康检查"
    echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"

    check_local_status
    check_backend_server
    check_frontend_server
    check_connectivity
    check_public_access
    print_summary
}

main "$@"
