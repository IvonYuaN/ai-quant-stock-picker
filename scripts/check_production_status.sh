#!/usr/bin/env bash
# AQSP 远程服务器状态检查脚本（无需 deploy-config.env）
# 直接检查 lh.ifidy.cn 的实际部署状态

set -euo pipefail

DOMAIN="lh.ifidy.cn"

echo "========================================"
echo "检查 AQSP 双服务器部署状态"
echo "域名: $DOMAIN"
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"
echo ""

# 颜色输出
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[!]${NC} $1"; }

# 1. 检查域名解析
echo "=== 1. 域名解析 ==="
if command -v dig >/dev/null 2>&1; then
    FRONTEND_IP=$(dig +short "$DOMAIN" | tail -1)
    if [[ -n "$FRONTEND_IP" ]]; then
        log_success "域名解析: $DOMAIN → $FRONTEND_IP"
    else
        log_error "域名解析失败"
        exit 1
    fi
else
    log_warn "dig 命令不可用，使用 ping 检查"
    if ping -c 1 "$DOMAIN" >/dev/null 2>&1; then
        log_success "域名可以访问: $DOMAIN"
    else
        log_error "无法访问域名"
        exit 1
    fi
fi
echo ""

# 2. 检查 HTTPS 访问
echo "=== 2. HTTPS 访问检查 ==="
if curl -sf --max-time 10 "https://$DOMAIN/" | grep -q 'AQSP'; then
    log_success "HTTPS 主页访问正常"
    response_time=$(curl -o /dev/null -s -w '%{time_total}' "https://$DOMAIN/")
    echo "  响应时间: ${response_time}s"
else
    log_error "HTTPS 主页访问失败"
fi
echo ""

# 3. 检查 API 健康端点
echo "=== 3. API 健康检查 ==="
if health_data=$(curl -sf --max-time 10 "https://$DOMAIN/api/health" 2>&1); then
    log_success "API 健康端点正常"
    echo "  响应: $health_data"

    # 尝试解析 JSON
    if command -v jq >/dev/null 2>&1; then
        echo "$health_data" | jq . 2>/dev/null || true
    fi
else
    log_error "API 健康端点失败"
    echo "  错误: $health_data"
fi
echo ""

# 4. 检查 SSL 证书
echo "=== 4. SSL 证书检查 ==="
if command -v openssl >/dev/null 2>&1; then
    cert_info=$(echo | openssl s_client -servername "$DOMAIN" -connect "${DOMAIN}:443" 2>/dev/null | openssl x509 -noout -dates 2>/dev/null || echo "")
    if [[ -n "$cert_info" ]]; then
        log_success "SSL 证书有效"
        echo "$cert_info" | while read line; do echo "  $line"; done

        # 检查证书颁发者
        cert_issuer=$(echo | openssl s_client -servername "$DOMAIN" -connect "${DOMAIN}:443" 2>/dev/null | openssl x509 -noout -issuer 2>/dev/null | cut -d= -f2- || echo "Unknown")
        echo "  颁发者: $cert_issuer"
    else
        log_warn "无法获取 SSL 证书信息"
    fi
else
    log_warn "openssl 命令不可用，跳过证书检查"
fi
echo ""

# 5. 检查响应头
echo "=== 5. HTTP 响应头检查 ==="
headers=$(curl -sI "https://$DOMAIN/" 2>/dev/null || echo "")
if [[ -n "$headers" ]]; then
    log_info "关键响应头："
    echo "$headers" | grep -i "server:" || echo "  Server: (隐藏)"
    echo "$headers" | grep -i "x-frame-options:" || log_warn "  缺少 X-Frame-Options"
    echo "$headers" | grep -i "x-content-type-options:" || log_warn "  缺少 X-Content-Type-Options"
    echo "$headers" | grep -i "strict-transport-security:" || log_warn "  缺少 HSTS"
fi
echo ""

# 6. 检查前端资源
echo "=== 6. 前端资源检查 ==="
if curl -sf --max-time 10 "https://$DOMAIN/assets/" >/dev/null 2>&1; then
    log_success "前端资源目录可访问"
else
    log_info "前端资源目录 /assets/ 返回非 200（正常，可能是目录列表禁用）"
fi
echo ""

# 7. 测试 API 端点（样例）
echo "=== 7. API 端点测试 ==="

# 测试几个公开端点
test_api_endpoint() {
    local endpoint=$1
    local name=$2

    if curl -sf --max-time 5 "https://$DOMAIN${endpoint}" >/dev/null 2>&1; then
        log_success "$name: $endpoint"
    else
        log_warn "$name 不可访问或需要鉴权: $endpoint"
    fi
}

test_api_endpoint "/api/health" "健康检查"
test_api_endpoint "/api/docs" "API 文档"
test_api_endpoint "/api/market/indices" "市场指数"

echo ""

# 8. 网络延迟测试
echo "=== 8. 网络性能测试 ==="
log_info "测试多次请求的响应时间..."

total_time=0
success_count=0
for i in {1..5}; do
    time=$(curl -o /dev/null -s -w '%{time_total}' "https://$DOMAIN/api/health" 2>/dev/null || echo "0")
    if [[ "$time" != "0" ]]; then
        echo "  请求 $i: ${time}s"
        total_time=$(echo "$total_time + $time" | bc)
        success_count=$((success_count + 1))
    fi
done

if [[ $success_count -gt 0 ]]; then
    avg_time=$(echo "scale=3; $total_time / $success_count" | bc)
    log_success "平均响应时间: ${avg_time}s"
else
    log_error "所有请求都失败了"
fi
echo ""

# 9. 架构推断
echo "=== 9. 架构分析 ==="
log_info "基于响应分析当前架构..."

# 检查是否通过反向代理
if echo "$headers" | grep -qi "x-real-ip\|x-forwarded"; then
    log_info "✓ 使用了反向代理（Nginx/Caddy）"
fi

# 检查 API 响应
api_headers=$(curl -sI "https://$DOMAIN/api/health" 2>/dev/null || echo "")
if echo "$api_headers" | grep -qi "fastapi\|uvicorn"; then
    log_info "✓ 后端使用 FastAPI/Uvicorn"
fi

# 检查前端
if curl -s "https://$DOMAIN/" | grep -qi "vite\|react"; then
    log_info "✓ 前端使用 React + Vite"
fi

echo ""
echo "========================================"
echo "检查完成"
echo "========================================"
echo ""
echo "建议的下一步操作："
echo "1. 如果所有检查都通过，系统运行正常"
echo "2. 如果有警告，考虑优化安全头配置"
echo "3. 如果 API 访问失败，检查后端服务器状态"
echo ""
echo "需要检查服务器内部状态，请提供 SSH 访问权限"
