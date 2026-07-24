#!/usr/bin/env bash
# 冷启动专用每日任务：
# 1. 更新 sqlite 历史库
# 2. 运行 aqsp.cli run 追加到 predictions ledger
# 3. 输出当前冷启动进度

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_PYTHON_HELPER="${PROJECT_ROOT}/scripts/runtime_python.sh"
if [ ! -f "$RUNTIME_PYTHON_HELPER" ] && [ -f "${SCRIPT_DIR}/runtime_python.sh" ]; then
    RUNTIME_PYTHON_HELPER="${SCRIPT_DIR}/runtime_python.sh"
fi
if [ ! -f "$RUNTIME_PYTHON_HELPER" ]; then
    echo "[ERROR] 缺少运行时 Python 解析器: ${RUNTIME_PYTHON_HELPER}" >&2
    exit 1
fi
# shellcheck disable=SC1090
source "$RUNTIME_PYTHON_HELPER"
export TZ="${TZ:-Asia/Shanghai}"
DATE="$(date +%Y-%m-%d)"
LOG_DIR="${AQSP_COLDSTART_LOG_DIR:-${AQSP_LOG_ROOT:-${PROJECT_ROOT}/logs}/coldstart}"
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

is_truthy() {
    local value
    value="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
    [[ "$value" =~ ^(1|true|yes|on)$ ]]
}

if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
fi

COLDSTART_MARKER_DATE="${AQSP_TRADING_DAY_OVERRIDE_DATE:-$DATE}"
COLDSTART_MARKER_FILE="${AQSP_COLDSTART_MARKER_FILE:-${PROJECT_ROOT}/.state/coldstart-${COLDSTART_MARKER_DATE}.done}"

export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}"

PYTHON_BIN="$(aqsp_runtime_python "$PROJECT_ROOT")"
aqsp_require_runtime_python "$PYTHON_BIN"

SQLITE_DB_PATH="$(resolve_path "${AQSP_COLDSTART_DB_PATH:-${AQSP_SQLITE_DB_PATH:-A股量化分析数据/astocks_raw.db}}")"
RUNTIME_SQLITE_DB_PATH="$(resolve_path "${AQSP_SQLITE_DB_PATH:-A股量化分析数据/astocks_raw.db}")"
UPDATE_SCRIPT_HINT="${AQSP_COLDSTART_UPDATE_SCRIPT:-}"
TARGET_DATE="${AQSP_COLDSTART_TARGET_DATE:-}"
RUN_AS_OF="$(
    TARGET_DATE_FOR_RUN="${TARGET_DATE:-}" \
    "${PYTHON_BIN}" - <<'PY'
import os
from datetime import date

from aqsp.core.time import get_previous_trading_day, is_trading_day, today_shanghai

target = os.environ.get("TARGET_DATE_FOR_RUN", "").strip()
if target:
    print(date.fromisoformat(target).isoformat())
else:
    today = today_shanghai()
    print((today if is_trading_day(today) else get_previous_trading_day(today)).isoformat())
PY
)"
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
HANDOFF_STATUS_PATH="$(resolve_path "${AQSP_COLDSTART_HANDOFF_STATUS_PATH:-data/coldstart_handoff_status.json}")"
REPORT_PATH="$(resolve_path "${AQSP_COLDSTART_REPORT:-outputs/recommendations-${RUN_AS_OF}.md}")"
CSV_PATH="$(resolve_path "${AQSP_COLDSTART_OUTPUT_CSV:-outputs/recommendations-${RUN_AS_OF}.csv}")"
LIMIT="${AQSP_LIMIT:-10}"
# sqlite 只负责历史库更新；新增候选生成走 live_short 适配源，避免历史源覆盖不完整时阻断冷启动。
COLDSTART_RUNTIME_SOURCE="${AQSP_COLDSTART_SOURCE:-online_first}"
case "$COLDSTART_RUNTIME_SOURCE" in
    auto|local_first|online_first|multi|eastmoney|sina|tencent|mootdx)
        ;;
    *)
        log "[ERROR] 冷启动筛选源 ${COLDSTART_RUNTIME_SOURCE} 不属于 live_short 实时源；拒绝使用历史源生成候选"
        exit 1
        ;;
esac
# 与 before-live 运行覆盖门槛一致：冷启动只补可筛选流动性池，避免退市/异常尾部代码阻断样本累积。
COLDSTART_MAX_UNIVERSE="${AQSP_COLDSTART_MAX_UNIVERSE:-${AQSP_MAX_UNIVERSE:-3000}}"
if ! [[ "$COLDSTART_MAX_UNIVERSE" =~ ^[0-9]+$ ]] || [ "$COLDSTART_MAX_UNIVERSE" -le 0 ]; then
    COLDSTART_MAX_UNIVERSE="3000"
fi
COLDSTART_MIN_TARGET_COVERAGE="${AQSP_COLDSTART_MIN_TARGET_COVERAGE:-3000}"

mkdir -p \
    "${PROJECT_ROOT}/data" \
    "${PROJECT_ROOT}/outputs" \
    "${PROJECT_ROOT}/logs" \
    "$LOCK_DIR" \
    "$(dirname "$LEDGER_PATH")" \
    "$(dirname "$HANDOFF_STATUS_PATH")" \
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
{
    printf 'LOCK_PID=%q\n' "$$"
    printf 'LOCK_RUNNER=%q\n' "scripts/coldstart_daily.sh"
    printf 'LOCK_STARTED_AT=%q\n' "$(date '+%Y-%m-%d %H:%M:%S')"
} >"$LOCK_INFO_FILE"
trap 'rm -f "$LOCK_INFO_FILE"; rmdir "$LOCK_FILE"' EXIT
rm -f "$COLDSTART_MARKER_FILE"

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
if ! is_truthy "$COLDSTART_ALLOW_INTRADAY" && [ "$NOW_HM" -lt 1530 ] && { [ -z "$TARGET_DATE" ] || [ "$TARGET_DATE" = "$DATE" ]; }; then
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
log "运行数据日: ${RUN_AS_OF}"
log "筛选数据源: ${COLDSTART_RUNTIME_SOURCE}"
log "=========================================="

UPDATE_ARGS=("${SQLITE_DB_PATH}")
if [ "$(basename "$UPDATE_SCRIPT")" = "update_sqlite_daily.py" ]; then
    UPDATE_ARGS+=(--price-mode "$SQLITE_PRICE_MODE")
    UPDATE_ARGS+=(--target-date "$RUN_AS_OF")
    UPDATE_ARGS+=(--sleep-seconds "${AQSP_COLDSTART_UPDATE_SLEEP_SECONDS:-0.05}")
    if [ -n "${AQSP_COLDSTART_BACKFILL_START_DATE:-}" ]; then
        UPDATE_ARGS+=(--start-date "${AQSP_COLDSTART_BACKFILL_START_DATE}")
        if [[ "${AQSP_COLDSTART_BACKFILL_FORCE:-false}" =~ ^(1|true|yes|on)$ ]]; then
            UPDATE_ARGS+=(--force-from-start)
        fi
        if [[ "${AQSP_COLDSTART_FILL_HISTORY_GAPS:-false}" =~ ^(1|true|yes|on)$ ]]; then
            UPDATE_ARGS+=(--fill-history-gaps)
        fi
    fi
fi

coldstart_target_coverage() {
    SQLITE_DB_PATH_FOR_COVERAGE="$SQLITE_DB_PATH" \
    TARGET_DATE_FOR_COVERAGE="$RUN_AS_OF" \
    "${PYTHON_BIN}" - <<'PY'
import os
import sqlite3

db_path = os.environ["SQLITE_DB_PATH_FOR_COVERAGE"]
target = os.environ.get("TARGET_DATE_FOR_COVERAGE", "").replace("-", "")
if not target:
    print(0)
    raise SystemExit(0)
try:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT ts_code) FROM daily_qfq WHERE trade_date = ?",
            (target,),
        ).fetchone()
    print(int((row or [0])[0] or 0))
except Exception:
    print(0)
PY
}

coldstart_signal_progress() {
    LEDGER_PATH_FOR_COLDSTART_STATUS="$LEDGER_PATH" \
    "${PYTHON_BIN}" - <<'PY'
import os

from aqsp.ledger.runtime import cold_start_min_days, count_independent_signal_days

ledger = os.environ["LEDGER_PATH_FOR_COLDSTART_STATUS"]
target = cold_start_min_days()
current = count_independent_signal_days(ledger)
print(f"{current}/{target}")
PY
}

write_coldstart_handoff_status() {
    local progress="$1"
    local reason="$2"
    HANDOFF_STATUS_PATH_FOR_COLDSTART="$HANDOFF_STATUS_PATH" \
    COLDSTART_PROGRESS_FOR_STATUS="$progress" \
    COLDSTART_REASON_FOR_STATUS="$reason" \
    COLDSTART_RUN_AS_OF_FOR_STATUS="$RUN_AS_OF" \
    "${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

from aqsp.core.time import now_shanghai

path = Path(os.environ["HANDOFF_STATUS_PATH_FOR_COLDSTART"])
payload = {
    "status": "ready",
    "progress": os.environ["COLDSTART_PROGRESS_FOR_STATUS"],
    "as_of": os.environ["COLDSTART_RUN_AS_OF_FOR_STATUS"],
    "updated_at": now_shanghai().isoformat(timespec="seconds"),
    "next_step": "run_production_walkforward_gate",
    "next_command": "bash scripts/bt_task.sh walkforward-gate",
    "blocker": os.environ["COLDSTART_REASON_FOR_STATUS"],
    "advisory_boundary": "research_only_no_auto_trading",
}
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

COLDSTART_SIGNAL_PROGRESS="$(coldstart_signal_progress || true)"
if [[ "$COLDSTART_SIGNAL_PROGRESS" =~ ^([0-9]+)/([0-9]+)$ ]] \
    && [ "${BASH_REMATCH[1]}" -ge "${BASH_REMATCH[2]}" ]; then
    log "冷启动样本已达标: ${COLDSTART_SIGNAL_PROGRESS}，跳过本次冷启动追加"
    log "冷启动后续: 样本门已关闭；交接给 bt_task.sh walkforward-gate 运行生产 walk-forward 双门 gate（DSR/PBO），不在冷启动任务里自动启动重型回测"
    write_coldstart_handoff_status "$COLDSTART_SIGNAL_PROGRESS" "冷启动样本门已达标；下一交易时段按实时数据生成研究候选；生产 walk-forward 双门 gate（DSR/PBO）单独复核；组合保护仅限制纸面动作"
    log "冷启动日跑完成"
    mkdir -p "$(dirname "$COLDSTART_MARKER_FILE")"
    : >"$COLDSTART_MARKER_FILE"
    exit 0
fi

TARGET_COVERAGE="$(coldstart_target_coverage)"
if [ "$TARGET_COVERAGE" -ge "$COLDSTART_MIN_TARGET_COVERAGE" ]; then
    log "目标日 ${RUN_AS_OF} 已有 ${TARGET_COVERAGE} 个标的，跳过重复历史库更新"
else
    set +e
    "${PYTHON_BIN}" -u "${UPDATE_SCRIPT}" "${UPDATE_ARGS[@]}" 2>&1 | tee -a "$RUN_LOG"
    UPDATE_EXIT_CODE=${PIPESTATUS[0]}
    set -e
    if [ "$UPDATE_EXIT_CODE" -ne 0 ]; then
        TARGET_COVERAGE_AFTER_UPDATE="$(coldstart_target_coverage)"
        log "[ERROR] 当天历史库更新失败，拒绝发布候选: exit=${UPDATE_EXIT_CODE} target=${RUN_AS_OF} coverage=${TARGET_COVERAGE_AFTER_UPDATE}/${COLDSTART_MIN_TARGET_COVERAGE}"
        exit "$UPDATE_EXIT_CODE"
    fi
fi

set +e
"${PYTHON_BIN}" -u -m aqsp.cli run \
    --source "$COLDSTART_RUNTIME_SOURCE" \
    --limit "$LIMIT" \
    --report "$REPORT_PATH" \
    --output-csv "$CSV_PATH" \
    --ledger "$LEDGER_PATH" \
    --max-universe "$COLDSTART_MAX_UNIVERSE" \
    --as-of "$RUN_AS_OF" \
    --skip-validation \
    --benchmark-symbol "" 2>&1 | tee -a "$RUN_LOG"
CLI_EXIT_CODE=${PIPESTATUS[0]}
set -e
if [ "$CLI_EXIT_CODE" -eq 2 ]; then
    log "冷启动筛选被组合保护正常阻塞；历史库已更新，本次不追加新增候选"
elif [ "$CLI_EXIT_CODE" -ne 0 ]; then
    log "[ERROR] 冷启动筛选失败，退出码: $CLI_EXIT_CODE"
    exit "$CLI_EXIT_CODE"
fi

LEDGER_PATH_FOR_PROGRESS="$LEDGER_PATH" "${PYTHON_BIN}" - <<'PY' 2>&1 | tee -a "$RUN_LOG"
import os

from aqsp.ledger.runtime import cold_start_min_days, count_independent_signal_days

ledger = os.environ["LEDGER_PATH_FOR_PROGRESS"]
print(f"冷启动: {count_independent_signal_days(ledger)}/{cold_start_min_days()}")
PY

COLDSTART_SIGNAL_PROGRESS="$(coldstart_signal_progress || true)"
if [[ "$COLDSTART_SIGNAL_PROGRESS" =~ ^([0-9]+)/([0-9]+)$ ]] \
    && [ "${BASH_REMATCH[1]}" -ge "${BASH_REMATCH[2]}" ]; then
    write_coldstart_handoff_status "$COLDSTART_SIGNAL_PROGRESS" "冷启动刚达标；下一交易时段按实时数据生成研究候选；生产 walk-forward 双门 gate（DSR/PBO）单独复核；组合保护仅限制纸面动作"
    log "冷启动后续: 样本门刚达标；下一交易时段继续生成研究候选，组合保护不阻断研究展示"
fi

log "冷启动日跑完成"
mkdir -p "$(dirname "$COLDSTART_MARKER_FILE")"
: >"$COLDSTART_MARKER_FILE"
