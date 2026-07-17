#!/bin/bash
# 尾盘溢价策略 wrapper 脚本
# 每天 14:30 北京时间由 launchd 触发
set -euo pipefail

# ============================ 环境 ============================

export LANG="en_US.UTF-8"
export TZ="Asia/Shanghai"
export PATH="/Library/Frameworks/Python.framework/Versions/3.11/bin:/usr/local/bin:/opt/homebrew/bin:$HOME/Library/Python/3.11/bin:$PATH"

PROJECT_ROOT="/Users/ivon/Documents/AI量化选股"
LOG_DIR="${PROJECT_ROOT}/logs/launchd"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/closing-$(date +%Y-%m-%d).log"

LOCK_FILE="${AQSP_ENTRY_LOCK_PATH:-/tmp/aqsp-entry.lock}"
LOCK_WAIT_SECONDS="${AQSP_ENTRY_LOCK_WAIT_SECONDS:-5}"
RUN_TIMEOUT_SECONDS="${AQSP_CLOSING_TIMEOUT_SECONDS:-3600}"
MAX_RUN_TIMEOUT_SECONDS=7200
LOCK_STALE_SECONDS="${AQSP_ENTRY_LOCK_STALE_SECONDS:-21600}"
ERROR_FILE="/tmp/aqsp-closing-error.flag"
LOCK_META_PATH="${LOCK_FILE}/meta.env"
LOCK_OWNER_PID="$$"
LOCK_OWNER_START_TIME="$(ps -p "$$" -o lstart= 2>/dev/null | sed 's/^ *//')"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

cleanup() {
    if [ "${LOCK_KIND:-}" = "flock" ]; then
        flock -u 9 2>/dev/null || true
        exec 9>&- || true
    elif [ "${LOCK_KIND:-}" = "mkdir" ]; then
        if owns_mkdir_lock; then
            rm -f "$LOCK_META_PATH" 2>/dev/null || true
            rmdir "$LOCK_FILE" 2>/dev/null || true
        fi
    fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if ! [[ "$LOCK_WAIT_SECONDS" =~ ^[0-9]+$ ]] || [ "$LOCK_WAIT_SECONDS" -gt 60 ]; then
    LOCK_WAIT_SECONDS=5
fi
if ! [[ "$RUN_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || [ "$RUN_TIMEOUT_SECONDS" -le 0 ]; then
    RUN_TIMEOUT_SECONDS=3600
fi
if [ "$RUN_TIMEOUT_SECONDS" -gt "$MAX_RUN_TIMEOUT_SECONDS" ]; then
    RUN_TIMEOUT_SECONDS="$MAX_RUN_TIMEOUT_SECONDS"
fi
if ! [[ "$LOCK_STALE_SECONDS" =~ ^[0-9]+$ ]] || [ "$LOCK_STALE_SECONDS" -le 0 ]; then
    LOCK_STALE_SECONDS=21600
fi

lock_owner_is_alive() {
    local pid="$1" expected_start="$2" actual_start
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    actual_start="$(ps -p "$pid" -o lstart= 2>/dev/null | sed 's/^ *//')"
    [ -n "$expected_start" ] && [ "$actual_start" = "$expected_start" ]
}
lock_is_stale() {
    local owner_pid owner_start lock_age now
    [ -d "$LOCK_FILE" ] || return 1
    owner_pid="$(awk -F= '$1 == "owner_pid" {print $2; exit}' "$LOCK_META_PATH" 2>/dev/null)"
    owner_start="$(awk -F= '$1 == "owner_start_time" {$1=""; sub(/^=/, ""); print; exit}' "$LOCK_META_PATH" 2>/dev/null)"
    if [ -n "$owner_pid" ] && [ -n "$owner_start" ]; then
        lock_owner_is_alive "$owner_pid" "$owner_start" && return 1
        return 0
    fi
    now="$(date +%s)"
    lock_age=$((now - $(stat -f %m "$LOCK_FILE" 2>/dev/null || echo "$now")))
    [ "$lock_age" -ge "$LOCK_STALE_SECONDS" ]
}
recover_stale_lock() {
    local quarantine="${LOCK_FILE}.stale.$$.$RANDOM"
    lock_is_stale || return 1
    mv "$LOCK_FILE" "$quarantine" 2>/dev/null || return 1
    rm -f "$quarantine/meta.env" 2>/dev/null || true
    rmdir "$quarantine" 2>/dev/null || true
    return 0
}
owns_mkdir_lock() {
    [ "$(awk -F= '$1 == "owner_pid" {print $2; exit}' "$LOCK_META_PATH" 2>/dev/null)" = "$LOCK_OWNER_PID" ] || return 1
    [ "$(awk -F= '$1 == "owner_start_time" {$1=""; sub(/^=/, ""); print; exit}' "$LOCK_META_PATH" 2>/dev/null)" = "$LOCK_OWNER_START_TIME" ]
}

acquire_lock() {
    local waited=0
    if command -v flock >/dev/null 2>&1 && [ ! -d "$LOCK_FILE" ]; then
        exec 9>"$LOCK_FILE"
        while ! flock -n 9; do
            [ "$waited" -ge "$LOCK_WAIT_SECONDS" ] && return 1
            sleep 1
            waited=$((waited + 1))
        done
        LOCK_KIND=flock
        return 0
    fi
    while ! mkdir "$LOCK_FILE" 2>/dev/null; do
        recover_stale_lock || true
        [ "$waited" -ge "$LOCK_WAIT_SECONDS" ] && return 1
        sleep 1
        waited=$((waited + 1))
    done
    LOCK_KIND=mkdir
    printf 'owner_pid=%s\nowner_start_time=%s\n' "$LOCK_OWNER_PID" "$LOCK_OWNER_START_TIME" > "$LOCK_META_PATH"
}

run_with_timeout() {
    local timeout_seconds="$1"
    shift
    if command -v setsid >/dev/null 2>&1; then
        setsid "$@" &
    else
        perl -e 'setpgrp(0, 0); exec @ARGV' "$@" &
    fi
    local child_pid=$!
    local timeout_marker="${TMPDIR:-/tmp}/aqsp-timeout.$$.$child_pid"
    rm -f "$timeout_marker"
    (
        sleep "$timeout_seconds"
        kill -0 "$child_pid" 2>/dev/null || exit 0
        : > "$timeout_marker"
        kill -TERM "-$child_pid" 2>/dev/null || true
        sleep 5
        kill -KILL "-$child_pid" 2>/dev/null || true
    ) &
    local watchdog_pid=$!
    local status=0
    if wait "$child_pid"; then
        status=0
    else
        status=$?
    fi
    kill "$watchdog_pid" 2>/dev/null || true
    wait "$watchdog_pid" 2>/dev/null || true
    if [ -f "$timeout_marker" ]; then
        rm -f "$timeout_marker"
        return 124
    fi
    return "$status"
}

if ! acquire_lock; then
    log "另一个入口正在运行，锁等待超时 ${LOCK_WAIT_SECONDS}s，退出"
    exit 0
fi

rm -f "$ERROR_FILE"
log "===== 尾盘溢价策略开始 ====="

cd "$PROJECT_ROOT"

# ============================ Python ============================

VENV_DIR="${PROJECT_ROOT}/.venv"
if [ -d "$VENV_DIR" ] && [ -f "${VENV_DIR}/bin/activate" ]; then
    source "${VENV_DIR}/bin/activate"
    log "已激活虚拟环境: ${VENV_DIR}"
else
    log "虚拟环境不存在，使用系统 Python"
fi

PYTHON_BIN="${VENV_DIR:+${VENV_DIR}/bin/}python3"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3)"
fi
log "Python: ${PYTHON_BIN} ($("${PYTHON_BIN}" --version 2>&1))"

# ============================ 配置 ============================

[ -f .env ] && { set -a; source .env; set +a; }

export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}"
export AQSP_MODE="closing"
export AQSP_RUN_TASK_ID="closing"

# ============================ 执行 ============================

log "运行尾盘溢价策略 (timeout=${RUN_TIMEOUT_SECONDS}s)..."
if run_with_timeout "$RUN_TIMEOUT_SECONDS" "${PYTHON_BIN}" -m aqsp closing-premium --notify 2>&1; then
    :
else
    RUN_STATUS=$?
    log "尾盘策略运行失败 (exit ${RUN_STATUS})"
    touch "$ERROR_FILE"
    exit "$RUN_STATUS"
fi

log "===== 尾盘溢价策略完成 ====="

# 日志轮转(保留30天)
find "$LOG_DIR" -name "closing-*.log" -mtime +30 -delete 2>/dev/null || true
