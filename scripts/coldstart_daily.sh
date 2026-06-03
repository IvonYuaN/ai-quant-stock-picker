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

UPDATE_SCRIPT="$(resolve_path "${AQSP_COLDSTART_UPDATE_SCRIPT:-A股量化分析数据/update_daily.py}")"
SQLITE_DB_PATH="$(resolve_path "${AQSP_COLDSTART_DB_PATH:-${AQSP_SQLITE_DB_PATH:-A股量化分析数据/astocks_qfq.db}}")"
LEDGER_PATH="$(resolve_path "${AQSP_LEDGER:-data/predictions.jsonl}")"
REPORT_PATH="$(resolve_path "${AQSP_COLDSTART_REPORT:-outputs/recommendations-${DATE}.md}")"
CSV_PATH="$(resolve_path "${AQSP_COLDSTART_OUTPUT_CSV:-outputs/recommendations-${DATE}.csv}")"
LIMIT="${AQSP_LIMIT:-10}"

mkdir -p \
    "${PROJECT_ROOT}/data" \
    "${PROJECT_ROOT}/outputs" \
    "${PROJECT_ROOT}/logs" \
    "$(dirname "$LEDGER_PATH")" \
    "$(dirname "$REPORT_PATH")" \
    "$(dirname "$CSV_PATH")"

DOW="$(date +%u)"
if [ "$DOW" -ge 6 ]; then
    log "周末跳过冷启动任务"
    exit 0
fi

if [ ! -f "$UPDATE_SCRIPT" ]; then
    log "[ERROR] update_daily.py 不存在: $UPDATE_SCRIPT"
    exit 1
fi

if [ ! -f "$SQLITE_DB_PATH" ]; then
    log "[ERROR] sqlite 历史库不存在: $SQLITE_DB_PATH"
    exit 1
fi

cd "$PROJECT_ROOT"

log "=========================================="
log "冷启动日跑开始"
log "项目目录: ${PROJECT_ROOT}"
log "Python: ${PYTHON_BIN}"
log "更新脚本: ${UPDATE_SCRIPT}"
log "历史库: ${SQLITE_DB_PATH}"
log "=========================================="

"${PYTHON_BIN}" "${UPDATE_SCRIPT}" "${SQLITE_DB_PATH}" 2>&1 | tee -a "$RUN_LOG"

"${PYTHON_BIN}" -m aqsp.cli run \
    --source sqlite_db \
    --limit "$LIMIT" \
    --report "$REPORT_PATH" \
    --output-csv "$CSV_PATH" \
    --ledger "$LEDGER_PATH" \
    --skip-validation \
    --benchmark-symbol "" 2>&1 | tee -a "$RUN_LOG"

"${PYTHON_BIN}" - <<'PY' 2>&1 | tee -a "$RUN_LOG"
from aqsp.cli import COLD_START_MIN_DAYS, _count_independent_signal_days

ledger = "data/predictions.jsonl"
print(f"冷启动: {_count_independent_signal_days(ledger)}/{COLD_START_MIN_DAYS}")
PY

log "冷启动日跑完成"
