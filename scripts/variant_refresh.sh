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

# AQSP_VARIANT_DB and AQSP_SQLITE_DB_PATH are raw daily databases.  The older
# AQSP_VARIANT_MARKET_DB may point to a qfq research database and is retained
# only as the final legacy fallback.
MARKET_DB="$(resolve_path "${AQSP_VARIANT_DB:-${AQSP_SQLITE_DB_PATH:-${AQSP_VARIANT_MARKET_DB:-data/astocks_raw.db}}}")"
OUTPUT_PATH="$(resolve_path "${AQSP_VARIANT_RESULTS:-data/runtime/variant_results.json}")"
LOCK_PATH="$(resolve_path "${AQSP_VARIANT_REFRESH_LOCK:-data/.locks/variant-results-refresh.lock}")"
CURSOR_PATH="$(resolve_path "${AQSP_VARIANT_CURSOR_PATH:-data/runtime/variant_results_cursor.json}")"
STATUS_PATH="$(resolve_path "${AQSP_VARIANT_REFRESH_STATUS:-data/runtime/variant_refresh_status.json}")"
FOCUS_SNAPSHOT="$(resolve_path "${AQSP_HOME_SNAPSHOT_PATH:-data/runtime/home_dashboard_snapshot.json}")"

write_waiting_status() {
    "$PYTHON_BIN" "$PROJECT_ROOT/scripts/refresh_variant_results_from_market_db.py" \
        --market-db "$MARKET_DB" \
        --output "$OUTPUT_PATH" \
        --status-file "$STATUS_PATH" \
        --status-only waiting \
        --status-message "$1" >/dev/null 2>&1 || true
}

refresh_home_snapshot() {
    if ! "$PYTHON_BIN" "$PROJECT_ROOT/scripts/write_home_snapshot.py" \
        --task-id variant-refresh >>"$LOG_FILE" 2>&1; then
        log "[WARN] 变体状态已写入，但首页快照刷新失败"
    fi
}

DOW="$(date +%u)"
if [ "$DOW" -ge 6 ]; then
    log "周末跳过变体刷新"
    write_waiting_status "周末不运行变体，等待下一个交易日错峰窗口。"
    refresh_home_snapshot
    exit 0
fi
if ! "$PYTHON_BIN" - <<'PY'
from aqsp.core.time import is_trading_day, today_shanghai

raise SystemExit(0 if is_trading_day(today_shanghai()) else 1)
PY
then
    log "今日非交易日，跳过变体刷新"
    write_waiting_status "今日非交易日，等待下一个交易日错峰窗口。"
    refresh_home_snapshot
    exit 0
fi

NOW_HM=$((10#$(date +%H%M)))
if ! is_truthy "${AQSP_VARIANT_ALLOW_EARLY:-false}" && [ "$NOW_HM" -lt 2100 ]; then
    log "当前未到北京时间 21:00，跳过变体刷新"
    write_waiting_status "当前未到北京时间 21:00，等待收盘后错峰运行。"
    refresh_home_snapshot
    exit 0
fi
MAX_SYMBOLS="${AQSP_VARIANT_MAX_SYMBOLS:-600}"
MAX_RUNTIME_SECONDS="${AQSP_VARIANT_MAX_RUNTIME_SECONDS:-480}"
NICE_LEVEL="${AQSP_VARIANT_NICE_LEVEL:-15}"
PROFILE_BATCH_SIZE="${AQSP_VARIANT_PROFILE_BATCH_SIZE:-6}"
MAX_STAGE_BATCHES="${AQSP_VARIANT_MAX_STAGE_BATCHES:-4}"
MIN_PUBLISHED_VARIANTS=24

if ! [[ "$MAX_SYMBOLS" =~ ^[0-9]+$ ]] || [ "$MAX_SYMBOLS" -lt 600 ]; then
    log "变体股票批次无效(${MAX_SYMBOLS})，使用 600"
    MAX_SYMBOLS="600"
elif [ "$MAX_SYMBOLS" -gt 600 ] && ! is_truthy "${AQSP_VARIANT_ALLOW_HEAVY:-false}"; then
    log "变体股票批次 ${MAX_SYMBOLS} 过大，收紧为 600"
    MAX_SYMBOLS="600"
fi
if ! [[ "$MAX_RUNTIME_SECONDS" =~ ^[0-9]+$ ]] || [ "$MAX_RUNTIME_SECONDS" -le 0 ]; then
    MAX_RUNTIME_SECONDS="480"
elif [ "$MAX_RUNTIME_SECONDS" -lt 480 ]; then
    log "变体运行时限 ${MAX_RUNTIME_SECONDS} 秒不足以完成四段发布，提升为 480 秒"
    MAX_RUNTIME_SECONDS="480"
elif [ "$MAX_RUNTIME_SECONDS" -gt 480 ]; then
    log "变体运行时限 ${MAX_RUNTIME_SECONDS} 秒过长，收紧为 480 秒"
    MAX_RUNTIME_SECONDS="480"
fi
if ! [[ "$NICE_LEVEL" =~ ^[0-9]+$ ]] || [ "$NICE_LEVEL" -lt 10 ] || [ "$NICE_LEVEL" -gt 19 ]; then
    log "变体 CPU 优先级无效(${NICE_LEVEL})，使用低优先级 15"
    NICE_LEVEL="15"
fi
if ! [[ "$PROFILE_BATCH_SIZE" =~ ^[0-9]+$ ]] || [ "$PROFILE_BATCH_SIZE" -lt 1 ] || [ "$PROFILE_BATCH_SIZE" -gt 12 ]; then
    log "变体策略批次无效(${PROFILE_BATCH_SIZE})，使用 6"
    PROFILE_BATCH_SIZE="6"
fi
if ! [[ "$MAX_STAGE_BATCHES" =~ ^[0-9]+$ ]] || [ "$MAX_STAGE_BATCHES" -lt 1 ] || [ "$MAX_STAGE_BATCHES" -gt 4 ]; then
    log "变体分段次数无效(${MAX_STAGE_BATCHES})，使用 4"
    MAX_STAGE_BATCHES="4"
fi
# The runtime artifact validator rejects fewer than 24 diverse variants. Keep a
# misconfigured small profile batch from silently staging forever across
# changing trading dates, while retaining the same bounded number of stages.
MIN_PROFILE_BATCH_SIZE=$(( (MIN_PUBLISHED_VARIANTS + MAX_STAGE_BATCHES - 1) / MAX_STAGE_BATCHES ))
if [ "$PROFILE_BATCH_SIZE" -lt "$MIN_PROFILE_BATCH_SIZE" ]; then
    log "变体策略批次 ${PROFILE_BATCH_SIZE} 无法在 ${MAX_STAGE_BATCHES} 段内形成 ${MIN_PUBLISHED_VARIANTS} 个合格变体，提升为 ${MIN_PROFILE_BATCH_SIZE}"
    PROFILE_BATCH_SIZE="$MIN_PROFILE_BATCH_SIZE"
fi
# The Python entrypoint performs a read-only SQLite integrity/table preflight.
# Keep the shell check minimal so a zero-byte fallback is reported there instead
# of silently consuming a full bounded runtime window.
if [ ! -s "$MARKET_DB" ]; then
    log "[WARN] 变体市场库不存在或为空: ${MARKET_DB}；由 Python 预检写入状态产物"
fi

mkdir -p "$(dirname "$OUTPUT_PATH")" "$(dirname "$LOCK_PATH")" "$(dirname "$CURSOR_PATH")" "$(dirname "$STATUS_PATH")"
log "开始变体刷新：max_symbols=${MAX_SYMBOLS} profiles=${PROFILE_BATCH_SIZE} batches=${MAX_STAGE_BATCHES} timeout=${MAX_RUNTIME_SECONDS}s nice=${NICE_LEVEL}"
started_at="$(date +%s)"
for batch_index in $(seq 1 "$MAX_STAGE_BATCHES"); do
    elapsed=$(( $(date +%s) - started_at ))
    remaining=$(( MAX_RUNTIME_SECONDS - elapsed - 15 ))
    if [ "$remaining" -le 0 ]; then
        log "变体总预算已耗尽，保留 staging 等待下个错峰窗口"
        break
    fi
    if timeout --foreground --signal=TERM --kill-after=15s "${remaining}s" \
        nice -n "$NICE_LEVEL" "$PYTHON_BIN" "$PROJECT_ROOT/scripts/refresh_variant_results_from_market_db.py" \
        --market-db "$MARKET_DB" \
        --output "$OUTPUT_PATH" \
        --max-symbols "$MAX_SYMBOLS" \
        --profile-batch-size "$PROFILE_BATCH_SIZE" \
        --max-runtime-seconds "$remaining" \
        --lock-file "$LOCK_PATH" \
        --cursor-file "$CURSOR_PATH" \
        --focus-snapshot "$FOCUS_SNAPSHOT" \
        --status-file "$STATUS_PATH" >>"$LOG_FILE" 2>&1; then
        :
    else
        status=$?
        log "[ERROR] 变体分段 ${batch_index} 失败或超时，保留产物与 staging，exit=${status}"
        refresh_home_snapshot
        exit "$status"
    fi
    if "$PYTHON_BIN" "$PROJECT_ROOT/scripts/check_variant_results.py" "$OUTPUT_PATH" >>"$LOG_FILE" 2>&1; then
        "$PYTHON_BIN" "$PROJECT_ROOT/scripts/write_home_snapshot.py" \
            --task-id variant-refresh >>"$LOG_FILE" 2>&1
        log "变体刷新和首页快照更新完成"
        exit 0
    fi
done
refresh_home_snapshot
log "变体首轮仍在分段构建，首页已更新状态快照"
