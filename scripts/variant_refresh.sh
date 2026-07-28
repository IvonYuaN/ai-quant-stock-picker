#!/usr/bin/env bash
# 收盘后受限刷新变体实验产物；不写正式 ledger，不触发交易。

set -euo pipefail

export TZ="Asia/Shanghai"
PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
RUNTIME_ROOT="${AQSP_RUNTIME_ROOT:-$PROJECT_ROOT}"
RUNTIME_DATA_ROOT="${AQSP_RUNTIME_DATA_ROOT:-${RUNTIME_ROOT}/data}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_PYTHON_HELPER="${PROJECT_ROOT}/scripts/runtime_python.sh"
if [ ! -f "$RUNTIME_PYTHON_HELPER" ]; then
    RUNTIME_PYTHON_HELPER="${SCRIPT_DIR}/runtime_python.sh"
fi
# shellcheck disable=SC1090
source "$RUNTIME_PYTHON_HELPER"
PYTHON_BIN="$(aqsp_runtime_python "$PROJECT_ROOT")"
LOG_DIR="${AQSP_VARIANT_LOG_DIR:-${RUNTIME_DATA_ROOT}/logs/variants}"
LOG_FILE="${LOG_DIR}/variant-refresh-$(date +%Y-%m-%d).log"

log() {
    mkdir -p "$LOG_DIR"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

is_truthy() {
    [[ "${1:-}" =~ ^(1|true|yes|on)$ ]]
}

resolve_path() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$PROJECT_ROOT" "$1" ;;
    esac
}

if ! aqsp_require_runtime_python "$PYTHON_BIN"; then
    exit 1
fi

DOW="$(date +%u)"
if [ "$DOW" -ge 6 ]; then
    log "周末跳过变体刷新"
    exit 0
fi
if ! "$PYTHON_BIN" - <<'PY'
from aqsp.core.time import is_trading_day, today_shanghai

raise SystemExit(0 if is_trading_day(today_shanghai()) else 1)
PY
then
    log "今日非交易日，跳过变体刷新"
    exit 0
fi

NOW_HM=$((10#$(date +%H%M)))
if ! is_truthy "${AQSP_VARIANT_ALLOW_EARLY:-false}" && [ "$NOW_HM" -lt 2100 ]; then
    log "当前未到北京时间 21:00，跳过变体刷新"
    exit 0
fi

MARKET_DB="$(resolve_path "${AQSP_VARIANT_MARKET_DB:-${AQSP_SQLITE_DB_PATH:-data/astocks_raw.db}}")"
OUTPUT_PATH="$(resolve_path "${AQSP_VARIANT_RESULTS:-data/runtime/variant_results.json}")"
LOCK_PATH="$(resolve_path "${AQSP_VARIANT_REFRESH_LOCK:-data/.locks/variant-results-refresh.lock}")"
CURSOR_PATH="$(resolve_path "${AQSP_VARIANT_CURSOR_PATH:-data/runtime/variant_results_cursor.json}")"
MAX_SYMBOLS="${AQSP_VARIANT_MAX_SYMBOLS:-160}"
MAX_RUNTIME_SECONDS="${AQSP_VARIANT_MAX_RUNTIME_SECONDS:-300}"

if ! [[ "$MAX_SYMBOLS" =~ ^[0-9]+$ ]] || [ "$MAX_SYMBOLS" -lt 121 ]; then
    log "变体股票批次无效(${MAX_SYMBOLS})，使用 160"
    MAX_SYMBOLS="160"
elif [ "$MAX_SYMBOLS" -gt 240 ] && ! is_truthy "${AQSP_VARIANT_ALLOW_HEAVY:-false}"; then
    log "变体股票批次 ${MAX_SYMBOLS} 过大，收紧为 240"
    MAX_SYMBOLS="240"
fi
if ! [[ "$MAX_RUNTIME_SECONDS" =~ ^[0-9]+$ ]] || [ "$MAX_RUNTIME_SECONDS" -le 0 ]; then
    MAX_RUNTIME_SECONDS="300"
elif [ "$MAX_RUNTIME_SECONDS" -gt 360 ] && ! is_truthy "${AQSP_VARIANT_ALLOW_HEAVY:-false}"; then
    log "变体运行时限 ${MAX_RUNTIME_SECONDS} 秒过长，收紧为 360 秒"
    MAX_RUNTIME_SECONDS="360"
fi
if [ ! -f "$MARKET_DB" ]; then
    log "[ERROR] 变体市场库不存在: ${MARKET_DB}"
    exit 1
fi

mkdir -p "$(dirname "$OUTPUT_PATH")" "$(dirname "$LOCK_PATH")" "$(dirname "$CURSOR_PATH")"
log "开始变体刷新：max_symbols=${MAX_SYMBOLS} timeout=${MAX_RUNTIME_SECONDS}s"
if timeout --foreground --signal=TERM --kill-after=15s "${MAX_RUNTIME_SECONDS}s" \
    "$PYTHON_BIN" "$PROJECT_ROOT/scripts/refresh_variant_results_from_market_db.py" \
    --market-db "$MARKET_DB" \
    --output "$OUTPUT_PATH" \
    --max-symbols "$MAX_SYMBOLS" \
    --max-runtime-seconds "$MAX_RUNTIME_SECONDS" \
    --lock-file "$LOCK_PATH" \
    --cursor-file "$CURSOR_PATH" >>"$LOG_FILE" 2>&1; then
    "$PYTHON_BIN" "$PROJECT_ROOT/scripts/write_home_snapshot.py" \
        --task-id variant-refresh >>"$LOG_FILE" 2>&1
    log "变体刷新和首页快照更新完成"
else
    status=$?
    log "[ERROR] 变体刷新失败或超时，保留上一版合格产物，exit=${status}"
    exit "$status"
fi
