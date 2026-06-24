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
NOTIFY_WARNINGS="${AQSP_MONITOR_NOTIFY_WARNINGS:-false}"
EXIT_ON_ALERT="${AQSP_MONITOR_EXIT_ON_ALERT:-false}"
QUIET_HEALTHY="${AQSP_MONITOR_QUIET_HEALTHY:-true}"
LOCK_DIR="${PROJECT_ROOT}/.locks"
LOCK_FILE="${LOCK_DIR}/server-monitor.lock"
LOCK_INFO_FILE="${LOCK_FILE}/meta.env"
LOCK_STALE_MINUTES="${AQSP_LOCK_STALE_MINUTES:-360}"
MONITOR_TIMEOUT_SECONDS="${AQSP_MONITOR_TIMEOUT_SECONDS:-0}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RESULT_LOG"
}

lock_age_minutes() {
    local path="$1"
    local now_epoch mtime
    now_epoch="$(date +%s)"
    mtime="$(stat -c %Y "$path" 2>/dev/null || stat -f %m "$path")"
    echo $(( (now_epoch - mtime) / 60 ))
}

load_lock_info() {
    if [ -f "$LOCK_INFO_FILE" ]; then
        # shellcheck disable=SC1090
        . "$LOCK_INFO_FILE"
    fi
}

lock_is_stale() {
    if [ ! -d "$LOCK_FILE" ]; then
        return 1
    fi
    local age_minutes pid=""
    age_minutes="$(lock_age_minutes "$LOCK_FILE")"
    load_lock_info
    pid="${LOCK_PID:-}"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 1
    fi
    [ "$age_minutes" -ge "$LOCK_STALE_MINUTES" ]
}

mkdir -p "$LOG_DIR"
mkdir -p "$LOCK_DIR"

if [ -d "$LOCK_FILE" ] && lock_is_stale; then
    stale_age="$(lock_age_minutes "$LOCK_FILE")"
    load_lock_info
    log "检测到陈旧监控锁，自动回收 runner=${LOCK_RUNNER:-monitor} pid=${LOCK_PID:-unknown} age=${stale_age}min started_at=${LOCK_STARTED_AT:-unknown}"
    rm -rf -- "$LOCK_FILE"
fi
if ! mkdir "$LOCK_FILE" 2>/dev/null; then
    if [ -f "$LOCK_INFO_FILE" ]; then
        load_lock_info
        age_minutes="$(lock_age_minutes "$LOCK_FILE")"
        log "上一轮监控仍在运行，本次监控正常跳过；这是互斥保护，不是失败 runner=${LOCK_RUNNER:-monitor} pid=${LOCK_PID:-unknown} started_at=${LOCK_STARTED_AT:-unknown} age=${age_minutes}min"
    else
        log "上一轮监控仍在运行，本次监控正常跳过；这是互斥保护，不是失败"
    fi
    exit 0
fi
cat >"$LOCK_INFO_FILE" <<EOF
LOCK_PID=$$
LOCK_RUNNER=monitor
LOCK_STARTED_AT=$(date '+%Y-%m-%d %H:%M:%S')
EOF
trap 'rm -f "$LOCK_INFO_FILE"; rmdir "$LOCK_FILE"' EXIT

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

MONITOR_ARGS=( -m aqsp monitor --config "${MONITOR_CONFIG}" --notify )
case "${NOTIFY_WARNINGS,,}" in
    1|true|yes|on) ;;
    *)
        MONITOR_ARGS+=( --notify-critical-only )
        ;;
esac
case "${QUIET_HEALTHY,,}" in
    1|true|yes|on)
        MONITOR_ARGS+=( --quiet-healthy )
        ;;
esac

set +e
if [ "${MONITOR_TIMEOUT_SECONDS}" -gt 0 ] && command -v timeout >/dev/null 2>&1; then
    log "启用监控超时保护: ${MONITOR_TIMEOUT_SECONDS}s"
    timeout --foreground "${MONITOR_TIMEOUT_SECONDS}" "${PYTHON_BIN}" "${MONITOR_ARGS[@]}" "$@" 2>&1 | tee -a "$RESULT_LOG"
    MONITOR_EXIT_CODE=${PIPESTATUS[0]}
elif [ "${MONITOR_TIMEOUT_SECONDS}" -gt 0 ]; then
    log "系统缺少 timeout 命令，跳过监控超时保护"
    "${PYTHON_BIN}" "${MONITOR_ARGS[@]}" "$@" 2>&1 | tee -a "$RESULT_LOG"
    MONITOR_EXIT_CODE=${PIPESTATUS[0]}
else
    "${PYTHON_BIN}" "${MONITOR_ARGS[@]}" "$@" 2>&1 | tee -a "$RESULT_LOG"
    MONITOR_EXIT_CODE=${PIPESTATUS[0]}
fi
set -e

if [ "${MONITOR_EXIT_CODE}" -eq 124 ]; then
    log "监控执行超时，被保护性终止: ${MONITOR_TIMEOUT_SECONDS}s"
fi

log "=========================================="
log "服务器监控结束"
log "退出码: ${MONITOR_EXIT_CODE}"
log "日志文件: ${RESULT_LOG}"
log "=========================================="

find "$LOG_DIR" -name "monitor-*.log" -mtime +30 -delete 2>/dev/null || true

case "${EXIT_ON_ALERT,,}" in
    1|true|yes|on)
        exit "${MONITOR_EXIT_CODE}"
        ;;
    *)
        if [ "${MONITOR_EXIT_CODE}" -ne 0 ]; then
            log "监控告警已由 AQSP 内部通知/去重处理；对外返回 0，避免外层调度重复告警"
        fi
        exit 0
        ;;
esac
