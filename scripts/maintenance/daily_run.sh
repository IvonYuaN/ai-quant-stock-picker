#!/usr/bin/env bash
# 每日收盘后执行：选股 -> ledger -> briefing -> diagnosis -> 日志
# 由 macOS launchd 在工作日 16:00 触发（北京时间 16:00）
set -e

if [ "${AQSP_ALLOW_LEGACY_ENTRY:-0}" != "1" ]; then
    echo "daily_run.sh is a legacy local entry. Use scripts/bt_task.sh daily in production, or set AQSP_ALLOW_LEGACY_ENTRY=1 for local smoke runs." >&2
    exit 2
fi

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-$HOME/Documents/AI量化选股}"
cd "$PROJECT_ROOT"

DATE=$(date +%Y-%m-%d)
LOG_DIR="${AQSP_LOG_DIR:-$PROJECT_ROOT/logs/daily}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/run-$DATE.log"

DOW=$(date +%u)
if [ "$DOW" -ge 6 ]; then
    echo "[$(date)] 周末跳过" >> "$LOG"
    exit 0
fi

export PATH="/Library/Frameworks/Python.framework/Versions/3.11/bin:/usr/local/bin:/opt/homebrew/bin:$HOME/Library/Python/3.11/bin:$PATH"
export PYTHONPATH="$PROJECT_ROOT/src:$PROJECT_ROOT:${PYTHONPATH:-}"
if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "${PROJECT_ROOT}/.env"
    set +a
    echo "[$(date)] 已加载 .env 配置" >> "$LOG"
fi
PYTHON_BIN="${AQSP_PYTHON:-/Library/Frameworks/Python.framework/Versions/3.11/bin/python3}"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3)"
fi
if ! "${PYTHON_BIN}" - <<'AQSP_CALENDAR_PY' >/dev/null 2>&1
from aqsp.core.time import is_trading_day, today_shanghai
raise SystemExit(0 if is_trading_day(today_shanghai()) else 1)
AQSP_CALENDAR_PY
then
    echo "[$(date)] 今日非交易日，跳过" >> "$LOG"
    exit 0
fi
RUN_TASK_ID="${AQSP_RUN_TASK_ID:-}"
if [ "$RUN_TASK_ID" != "daily" ]; then
    echo "[$(date)] 拒绝 legacy daily_run：AQSP_RUN_TASK_ID=${RUN_TASK_ID:-<empty>}，仅允许 daily" >> "$LOG"
    exit 0
fi
export AQSP_RUN_TASK_ID="daily"
ENFORCE_DAILY_WINDOW="${AQSP_ENFORCE_DAILY_WINDOW:-true}"
NOW_HM=$((10#$(date +%H%M)))
DAILY_WINDOW_START_HM="${AQSP_DAILY_WINDOW_START_HM:-1730}"
DAILY_WINDOW_END_HM="${AQSP_DAILY_WINDOW_END_HM:-2300}"
case "${ENFORCE_DAILY_WINDOW,,}" in
    1|true|yes|on)
        if [ "$NOW_HM" -lt "$DAILY_WINDOW_START_HM" ] || [ "$NOW_HM" -gt "$DAILY_WINDOW_END_HM" ]; then
            echo "[$(date)] 当前时间 ${NOW_HM} 不在 legacy daily_run 允许窗口 ${DAILY_WINDOW_START_HM}-${DAILY_WINDOW_END_HM}，跳过" >> "$LOG"
            exit 0
        fi
        ;;
esac

LOCK_PATH="${AQSP_ENTRY_LOCK_PATH:-/tmp/aqsp-entry.lock}"
LOCK_WAIT_SECONDS="${AQSP_ENTRY_LOCK_WAIT_SECONDS:-5}"
DAILY_TIMEOUT_SECONDS="${AQSP_DAILY_TIMEOUT_SECONDS:-5400}"
MAX_DAILY_TIMEOUT_SECONDS=7200
LOCK_STALE_SECONDS="${AQSP_ENTRY_LOCK_STALE_SECONDS:-21600}"
LOCK_KIND=""
LOCK_META_PATH="${LOCK_PATH}/meta.env"
LOCK_OWNER_PID="$$"
LOCK_OWNER_START_TIME="$(ps -p "$$" -o lstart= 2>/dev/null | sed 's/^ *//')"
lock_owner_is_alive() {
    local pid="$1" expected_start="$2" actual_start
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    actual_start="$(ps -p "$pid" -o lstart= 2>/dev/null | sed 's/^ *//')"
    [ -n "$expected_start" ] && [ "$actual_start" = "$expected_start" ]
}
lock_is_stale() {
    local owner_pid owner_start lock_age now
    [ -d "$LOCK_PATH" ] || return 1
    owner_pid="$(awk -F= '$1 == "owner_pid" {print $2; exit}' "$LOCK_META_PATH" 2>/dev/null)"
    owner_start="$(awk -F= '$1 == "owner_start_time" {$1=""; sub(/^=/, ""); print; exit}' "$LOCK_META_PATH" 2>/dev/null)"
    if [ -n "$owner_pid" ] && [ -n "$owner_start" ]; then
        lock_owner_is_alive "$owner_pid" "$owner_start" && return 1
        return 0
    fi
    now="$(date +%s)"
    lock_age=$((now - $(stat -f %m "$LOCK_PATH" 2>/dev/null || echo "$now")))
    [ "$lock_age" -ge "$LOCK_STALE_SECONDS" ]
}
recover_stale_lock() {
    local quarantine="${LOCK_PATH}.stale.$$.$RANDOM"
    lock_is_stale || return 1
    mv "$LOCK_PATH" "$quarantine" 2>/dev/null || return 1
    rm -f "$quarantine/meta.env" 2>/dev/null || true
    rmdir "$quarantine" 2>/dev/null || true
    return 0
}
owns_mkdir_lock() {
    [ "$(awk -F= '$1 == "owner_pid" {print $2; exit}' "$LOCK_META_PATH" 2>/dev/null)" = "$LOCK_OWNER_PID" ] || return 1
    [ "$(awk -F= '$1 == "owner_start_time" {$1=""; sub(/^=/, ""); print; exit}' "$LOCK_META_PATH" 2>/dev/null)" = "$LOCK_OWNER_START_TIME" ]
}
cleanup_lock() {
    if [ "$LOCK_KIND" = "flock" ]; then
        flock -u 9 2>/dev/null || true
        exec 9>&- || true
    elif [ "$LOCK_KIND" = "mkdir" ]; then
        if owns_mkdir_lock; then
            rm -f "$LOCK_META_PATH" 2>/dev/null || true
            rmdir "$LOCK_PATH" 2>/dev/null || true
        fi
    fi
}
trap cleanup_lock EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
if ! [[ "$LOCK_WAIT_SECONDS" =~ ^[0-9]+$ ]] || [ "$LOCK_WAIT_SECONDS" -gt 60 ]; then
    LOCK_WAIT_SECONDS=5
fi
if ! [[ "$DAILY_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || [ "$DAILY_TIMEOUT_SECONDS" -le 0 ]; then
    DAILY_TIMEOUT_SECONDS=5400
fi
if [ "$DAILY_TIMEOUT_SECONDS" -gt "$MAX_DAILY_TIMEOUT_SECONDS" ]; then
    DAILY_TIMEOUT_SECONDS="$MAX_DAILY_TIMEOUT_SECONDS"
fi
if ! [[ "$LOCK_STALE_SECONDS" =~ ^[0-9]+$ ]] || [ "$LOCK_STALE_SECONDS" -le 0 ]; then
    LOCK_STALE_SECONDS=21600
fi
acquire_lock() {
    local waited=0
    if command -v flock >/dev/null 2>&1 && [ ! -d "$LOCK_PATH" ]; then
        exec 9>"$LOCK_PATH"
        while ! flock -n 9; do
            [ "$waited" -ge "$LOCK_WAIT_SECONDS" ] && return 1
            sleep 1
            waited=$((waited + 1))
        done
        LOCK_KIND=flock
        return 0
    fi
    while ! mkdir "$LOCK_PATH" 2>/dev/null; do
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
    if wait "$child_pid"; then status=0; else status=$?; fi
    kill "$watchdog_pid" 2>/dev/null || true
    wait "$watchdog_pid" 2>/dev/null || true
    if [ -f "$timeout_marker" ]; then
        rm -f "$timeout_marker"
        return 124
    fi
    return "$status"
}
if ! acquire_lock; then
    echo "[$(date)] 另一个入口正在运行，锁等待超时 ${LOCK_WAIT_SECONDS}s，跳过" >> "$LOG"
    exit 0
fi
export AQSP_SOURCE="${AQSP_SOURCE:-auto}"
export AQSP_SYMBOLS="${AQSP_SYMBOLS:-}"
export AQSP_MODE="${AQSP_MODE:-close}"
export AQSP_LIMIT="${AQSP_LIMIT:-10}"
export AQSP_MAX_UNIVERSE="${AQSP_MAX_UNIVERSE:-0}"
export AQSP_MIN_AVG_AMOUNT="${AQSP_MIN_AVG_AMOUNT:-50000000}"
export AQSP_ENABLE_ONLINE_FACTORS="${AQSP_ENABLE_ONLINE_FACTORS:-false}"
export AQSP_MAX_DATA_LAG_DAYS="${AQSP_MAX_DATA_LAG_DAYS:-3}"
export AQSP_LEDGER="${AQSP_LEDGER:-data/predictions.jsonl}"
export AQSP_PAPER_LEDGER="${AQSP_PAPER_LEDGER:-data/paper_trades.jsonl}"
export AQSP_REPORT="${AQSP_REPORT:-reports/latest.md}"
export AQSP_OUTPUT_CSV="${AQSP_OUTPUT_CSV:-reports/latest.csv}"
export AQSP_BRIEFING_REPORT="${AQSP_BRIEFING_REPORT:-reports/briefing-$DATE.md}"
export AQSP_DIAGNOSIS="${AQSP_DIAGNOSIS:-reports/runtime-diagnosis.md}"
export AQSP_DASHBOARD_HTML="${AQSP_DASHBOARD_HTML:-dist/dashboard/index.html}"
export AQSP_DASHBOARD_DB="${AQSP_DASHBOARD_DB:-dist/dashboard/aqsp.db}"
case "$AQSP_LEDGER" in /*) ;; *) export AQSP_LEDGER="$PROJECT_ROOT/$AQSP_LEDGER" ;; esac
case "$AQSP_PAPER_LEDGER" in /*) ;; *) export AQSP_PAPER_LEDGER="$PROJECT_ROOT/$AQSP_PAPER_LEDGER" ;; esac
case "$AQSP_REPORT" in /*) ;; *) export AQSP_REPORT="$PROJECT_ROOT/$AQSP_REPORT" ;; esac
case "$AQSP_OUTPUT_CSV" in /*) ;; *) export AQSP_OUTPUT_CSV="$PROJECT_ROOT/$AQSP_OUTPUT_CSV" ;; esac
case "$AQSP_BRIEFING_REPORT" in /*) ;; *) export AQSP_BRIEFING_REPORT="$PROJECT_ROOT/$AQSP_BRIEFING_REPORT" ;; esac
case "$AQSP_DIAGNOSIS" in /*) ;; *) export AQSP_DIAGNOSIS="$PROJECT_ROOT/$AQSP_DIAGNOSIS" ;; esac
case "$AQSP_DASHBOARD_HTML" in /*) ;; *) export AQSP_DASHBOARD_HTML="$PROJECT_ROOT/$AQSP_DASHBOARD_HTML" ;; esac
case "$AQSP_DASHBOARD_DB" in /*) ;; *) export AQSP_DASHBOARD_DB="$PROJECT_ROOT/$AQSP_DASHBOARD_DB" ;; esac

run_daily_pipeline() {
{
    echo "=== aqsp run @ $(date) ==="
    echo "source=$AQSP_SOURCE symbols=$AQSP_SYMBOLS mode=$AQSP_MODE"
    echo "python=$PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"
    set +e
    "$PYTHON_BIN" -m aqsp run \
        --source "$AQSP_SOURCE" \
        --symbols "$AQSP_SYMBOLS" \
        --mode "$AQSP_MODE" \
        --limit "$AQSP_LIMIT" \
        --max-universe "$AQSP_MAX_UNIVERSE" \
        --min-avg-amount "$AQSP_MIN_AVG_AMOUNT" \
        --max-data-lag-days "$AQSP_MAX_DATA_LAG_DAYS" \
        --ledger "$AQSP_LEDGER" \
        --report "$AQSP_REPORT" \
        --output-csv "$AQSP_OUTPUT_CSV" 2>&1
    RUN_STATUS=$?
    set -e
    if [ "$RUN_STATUS" -eq 2 ]; then
        echo "aqsp run returned 2: circuit breaker active; continue briefing/diagnosis for visibility."
    elif [ "$RUN_STATUS" -ne 0 ]; then
        exit "$RUN_STATUS"
    fi

    AQSP_NOTIFY_LEVEL_RESOLVED="$("$PYTHON_BIN" scripts/resolve_notify_level.py --ledger "$AQSP_LEDGER" --field level 2>/dev/null || echo info)"
    AQSP_NOTIFY_HEALTH_LABEL="$("$PYTHON_BIN" scripts/resolve_notify_level.py --ledger "$AQSP_LEDGER" --field label 2>/dev/null || echo unknown)"
    AQSP_NOTIFY_SOURCE_ROUTE="$("$PYTHON_BIN" scripts/resolve_notify_level.py --ledger "$AQSP_LEDGER" --field route 2>/dev/null || echo unknown)"
    export AQSP_NOTIFY_LEVEL_RESOLVED
    export AQSP_NOTIFY_HEALTH_LABEL
    export AQSP_NOTIFY_SOURCE_ROUTE
    echo "notify_level=$AQSP_NOTIFY_LEVEL_RESOLVED source_health=$AQSP_NOTIFY_HEALTH_LABEL route=$AQSP_NOTIFY_SOURCE_ROUTE"

    echo ""
    echo "=== aqsp briefing @ $(date) ==="
    if [ "$AQSP_NOTIFY_LEVEL_RESOLVED" = "critical" ]; then
        echo "critical notify level detected; generate briefing and diagnosis, suppress normal push notification."
        "$PYTHON_BIN" -m aqsp briefing \
            --ledger "$AQSP_LEDGER" \
            --output "$AQSP_BRIEFING_REPORT" 2>&1
    else
        "$PYTHON_BIN" -m aqsp briefing \
            --ledger "$AQSP_LEDGER" \
            --output "$AQSP_BRIEFING_REPORT" \
            --notify 2>&1
    fi

    echo ""
    echo "=== ledger 当前行数 ==="
    wc -l "$AQSP_LEDGER" 2>/dev/null || echo "ledger not found"

    echo ""
    echo "=== outputs ==="
    ls -lh "$AQSP_REPORT" "$AQSP_OUTPUT_CSV" "$AQSP_BRIEFING_REPORT" 2>/dev/null || true

    echo ""
    echo "=== runtime diagnosis @ $(date) ==="
    "$PYTHON_BIN" scripts/diagnose_runtime.py | tee "$AQSP_DIAGNOSIS"
} >> "$LOG" 2>&1
}

export -f run_daily_pipeline
export DATE LOG PYTHON_BIN
if run_with_timeout "$DAILY_TIMEOUT_SECONDS" /bin/bash -c 'run_daily_pipeline'; then
    :
else
    RUN_STATUS=$?
    echo "[$(date)] daily_run 超时或失败 (exit=${RUN_STATUS}, timeout=${DAILY_TIMEOUT_SECONDS}s)" >> "$LOG"
    exit "$RUN_STATUS"
fi

echo "[$(date)] daily_run done, log: $LOG"
