#!/usr/bin/env bash
# 服务器监控脚本：
# 1. 加载 .env
# 2. 执行 aqsp monitor
# 3. 记录监控日志，异常时自动通知

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
VENV_DIR="${PROJECT_ROOT}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python3"
LOG_DIR="${PROJECT_ROOT}/logs/monitor"
RESULT_LOG="${LOG_DIR}/monitor-$(date +%Y-%m-%d).log"
MONITOR_CONFIG="${AQSP_MONITOR_CONFIG:-config/monitors.yaml}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RESULT_LOG"
}

mkdir -p "$LOG_DIR"

if [ ! -f "$PYTHON_BIN" ]; then
    log "[ERROR] Python 可执行文件不存在: $PYTHON_BIN"
    exit 1
fi

if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
    log "已加载 .env 配置"
fi

export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}"
export TZ="${TZ:-Asia/Shanghai}"

cd "$PROJECT_ROOT"

log "=========================================="
log "AQSP 服务器监控开始"
log "项目目录: ${PROJECT_ROOT}"
log "配置文件: ${MONITOR_CONFIG}"
log "=========================================="

set +e
"${PYTHON_BIN}" -m aqsp monitor --config "${MONITOR_CONFIG}" --notify "$@" 2>&1 | tee -a "$RESULT_LOG"
MONITOR_EXIT_CODE=$?
set -e

log "=========================================="
log "服务器监控结束"
log "退出码: ${MONITOR_EXIT_CODE}"
log "日志文件: ${RESULT_LOG}"
log "=========================================="

find "$LOG_DIR" -name "monitor-*.log" -mtime +30 -delete 2>/dev/null || true

exit "${MONITOR_EXIT_CODE}"
