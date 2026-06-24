#!/usr/bin/env bash
# 冷启动专用每日任务：
# 1. 更新 sqlite 历史库
# 2. 运行 aqsp.cli run 追加到 predictions ledger
# 3. 输出当前冷启动进度

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
DATE="$(date +%Y-%m-%d)"
LOG_DIR="${AQSP_COLDSTART_LOG_DIR:-${PROJECT_ROOT}/logs/coldstart}"
RUN_LOG="${LOG_DIR}/coldstart-${DATE}.log"
LOCK_DIR="${PROJECT_ROOT}/.locks"
# 与 scripts/server_sync_and_run.sh 共用同一把锁，避免冷启动与日终主链路并发。
LOCK_FILE="${LOCK_DIR}/server-runtime.lock"
LOCK_INFO_FILE="${LOCK_FILE}/meta.env"
LOCK_STALE_MINUTES="${AQSP_LOCK_STALE_MINUTES:-360}"

log() {
    mkdir -p "$LOG_DIR"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RUN_LOG"
}

resolve_path() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$PROJECT_ROOT" "$1" ;;
    esac
}

detect_sqlite_price_mode() {
    local path_lc
    path_lc="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
    if [[ "$path_lc" == *raw* || "$path_lc" == *unadjust* ]]; then
        printf 'raw\n'
        return 0
    fi
    if [[ "$path_lc" == *qfq* || "$path_lc" == *hfq* ]]; then
        printf 'qfq\n'
        return 0
    fi
    printf 'unknown\n'
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

first_existing_path() {
    for candidate in "$@"; do
        if [ -n "$candidate" ] && [ -f "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
fi

export TZ="${TZ:-Asia/Shanghai}"
export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}"

PYTHON_BIN="${AQSP_PYTHON:-${PROJECT_ROOT}/.venv/bin/python3}"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3)"
fi

SQLITE_DB_PATH="$(resolve_path "${AQSP_COLDSTART_DB_PATH:-${AQSP_SQLITE_DB_PATH:-A股量化分析数据/astocks_raw.db}}")"
RUNTIME_SQLITE_DB_PATH="$(resolve_path "${AQSP_SQLITE_DB_PATH:-A股量化分析数据/astocks_raw.db}")"
UPDATE_SCRIPT_HINT="${AQSP_COLDSTART_UPDATE_SCRIPT:-}"
PROJECT_UPDATE_SCRIPT="$(resolve_path "scripts/update_sqlite_daily.py")"
SQLITE_UPDATE_SCRIPT="$(dirname "$SQLITE_DB_PATH")/update_daily.py"
REPO_UPDATE_SCRIPT="$(resolve_path "A股量化分析数据/update_daily.py")"
UPDATE_SCRIPT="$(
    first_existing_path \
        "$UPDATE_SCRIPT_HINT" \
        "$PROJECT_UPDATE_SCRIPT" \
        "$SQLITE_UPDATE_SCRIPT" \
        "$REPO_UPDATE_SCRIPT" \
        || true
)"
if [ -z "$UPDATE_SCRIPT" ]; then
    UPDATE_SCRIPT="${UPDATE_SCRIPT_HINT:-$PROJECT_UPDATE_SCRIPT}"
fi
LEDGER_PATH="$(resolve_path "${AQSP_LEDGER:-data/predictions.jsonl}")"
REPORT_PATH="$(resolve_path "${AQSP_COLDSTART_REPORT:-outputs/recommendations-${DATE}.md}")"
CSV_PATH="$(resolve_path "${AQSP_COLDSTART_OUTPUT_CSV:-outputs/recommendations-${DATE}.csv}")"
LIMIT="${AQSP_LIMIT:-10}"

mkdir -p \
    "${PROJECT_ROOT}/data" \
    "${PROJECT_ROOT}/outputs" \
    "${PROJECT_ROOT}/logs" \
    "$LOCK_DIR" \
    "$(dirname "$LEDGER_PATH")" \
    "$(dirname "$REPORT_PATH")" \
    "$(dirname "$CSV_PATH")"

if [ -d "$LOCK_FILE" ] && lock_is_stale; then
    stale_age="$(lock_age_minutes "$LOCK_FILE")"
    load_lock_info
    log "检测到陈旧主锁，自动回收 runner=${LOCK_RUNNER:-unknown} pid=${LOCK_PID:-unknown} age=${stale_age}min started_at=${LOCK_STARTED_AT:-unknown}"
    rm -rf -- "$LOCK_FILE"
fi

if ! mkdir "$LOCK_FILE" 2>/dev/null; then
    if [ -f "$LOCK_INFO_FILE" ]; then
        load_lock_info
        log "主链路仍在运行，本次冷启动正常跳过；这是互斥保护，不是失败 runner=${LOCK_RUNNER:-unknown} pid=${LOCK_PID:-unknown} started_at=${LOCK_STARTED_AT:-unknown}"
    else
        log "主链路仍在运行，本次冷启动正常跳过；这是互斥保护，不是失败"
    fi
    exit 0
fi
cat >"$LOCK_INFO_FILE" <<EOF
LOCK_PID=$$
LOCK_RUNNER=scripts/coldstart_daily.sh
LOCK_STARTED_AT=$(date '+%Y-%m-%d %H:%M:%S')
EOF
trap 'rm -f "$LOCK_INFO_FILE"; rmdir "$LOCK_FILE"' EXIT

DOW="$(date +%u)"
if [ "$DOW" -ge 6 ]; then
    log "周末跳过冷启动任务"
    exit 0
fi

if ! "${PYTHON_BIN}" - <<'AQSP_CALENDAR_PY'
from aqsp.core.time import is_trading_day, today_shanghai
raise SystemExit(0 if is_trading_day(today_shanghai()) else 1)
AQSP_CALENDAR_PY
then
    log "今日非交易日，跳过冷启动任务"
    exit 0
fi

# 冷启动使用 sqlite 日线库补正式 predictions ledger。
# 开盘中 baostock 可能已返回当天未收盘日线；默认必须等收盘后再跑。
COLDSTART_ALLOW_INTRADAY="${AQSP_COLDSTART_ALLOW_INTRADAY:-false}"
NOW_HM=$((10#$(date +%H%M)))
if [[ ! "${COLDSTART_ALLOW_INTRADAY,,}" =~ ^(1|true|yes|on)$ ]] && [ "$NOW_HM" -lt 1530 ]; then
    log "当前仍在收盘前，跳过冷启动；盘中请使用 bt_task.sh intraday"
    exit 0
fi

if [ ! -f "$UPDATE_SCRIPT" ]; then
    log "[ERROR] update_daily.py 不存在: $UPDATE_SCRIPT"
    log "[ERROR] 已尝试: ${UPDATE_SCRIPT_HINT:-<unset>} | ${PROJECT_UPDATE_SCRIPT} | ${SQLITE_UPDATE_SCRIPT} | ${REPO_UPDATE_SCRIPT}"
    exit 1
fi

if [ ! -f "$SQLITE_DB_PATH" ]; then
    log "[ERROR] sqlite 历史库不存在: $SQLITE_DB_PATH"
    exit 1
fi

SQLITE_PRICE_MODE="${AQSP_COLDSTART_PRICE_MODE:-$(detect_sqlite_price_mode "$SQLITE_DB_PATH")}"
if [ "$SQLITE_PRICE_MODE" = "unknown" ]; then
    log "[ERROR] 无法从 sqlite 路径判断 price_mode: $SQLITE_DB_PATH"
    exit 1
fi
if [ "${AQSP_SOURCE:-}" = "sqlite_db" ] && [ "$SQLITE_DB_PATH" != "$RUNTIME_SQLITE_DB_PATH" ]; then
    log "[ERROR] 冷启动 sqlite 路径与运行时不一致: coldstart=$SQLITE_DB_PATH runtime=$RUNTIME_SQLITE_DB_PATH"
    exit 1
fi
if [ "${AQSP_SOURCE:-}" = "sqlite_db" ] && [ "$SQLITE_PRICE_MODE" != "raw" ]; then
    log "[ERROR] sqlite_db 运行时要求 coldstart 更新 raw 历史库，当前 price_mode=$SQLITE_PRICE_MODE path=$SQLITE_DB_PATH"
    exit 1
fi

cd "$PROJECT_ROOT"

log "=========================================="
log "冷启动日跑开始"
log "项目目录: ${PROJECT_ROOT}"
log "Python: ${PYTHON_BIN}"
log "更新脚本: ${UPDATE_SCRIPT}"
log "历史库: ${SQLITE_DB_PATH}"
log "price_mode: ${SQLITE_PRICE_MODE}"
log "=========================================="

UPDATE_ARGS=("${SQLITE_DB_PATH}")
if [ "$(basename "$UPDATE_SCRIPT")" = "update_sqlite_daily.py" ]; then
    UPDATE_ARGS+=(--price-mode "$SQLITE_PRICE_MODE")
    UPDATE_ARGS+=(--sleep-seconds "${AQSP_COLDSTART_UPDATE_SLEEP_SECONDS:-0.05}")
    if [ -n "${AQSP_COLDSTART_BACKFILL_START_DATE:-}" ]; then
        UPDATE_ARGS+=(--start-date "${AQSP_COLDSTART_BACKFILL_START_DATE}")
        if [[ "${AQSP_COLDSTART_BACKFILL_FORCE:-false}" =~ ^(1|true|yes|on)$ ]]; then
            UPDATE_ARGS+=(--force-from-start)
        fi
    fi
fi
"${PYTHON_BIN}" -u "${UPDATE_SCRIPT}" "${UPDATE_ARGS[@]}" 2>&1 | tee -a "$RUN_LOG"

"${PYTHON_BIN}" -u -m aqsp.cli run \
    --source sqlite_db \
    --limit "$LIMIT" \
    --report "$REPORT_PATH" \
    --output-csv "$CSV_PATH" \
    --ledger "$LEDGER_PATH" \
    --skip-validation \
    --benchmark-symbol "" 2>&1 | tee -a "$RUN_LOG"

LEDGER_PATH_FOR_PROGRESS="$LEDGER_PATH" "${PYTHON_BIN}" - <<'PY' 2>&1 | tee -a "$RUN_LOG"
import os

from aqsp.cli import COLD_START_MIN_DAYS, _count_independent_signal_days

ledger = os.environ["LEDGER_PATH_FOR_PROGRESS"]
print(f"冷启动: {_count_independent_signal_days(ledger)}/{COLD_START_MIN_DAYS}")
PY

log "冷启动日跑完成"
