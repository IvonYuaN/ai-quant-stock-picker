#!/usr/bin/env bash
# 盘中轻量刷新：
# 1. 仅生成盘中候选，不污染正式收盘 ledger
# 2. 刷新当前 Dashboard 展示
# 3. 默认只在交易时段内运行

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
VENV_DIR="${PROJECT_ROOT}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python3"
LOG_DIR="${PROJECT_ROOT}/logs/intraday"
RESULT_LOG="${LOG_DIR}/intraday-$(date +%Y-%m-%d).log"
LOCK_DIR="${PROJECT_ROOT}/.locks/intraday-refresh.lock"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RESULT_LOG"
}

is_truthy() {
    local value
    value="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
    [[ "$value" =~ ^(1|true|yes|on)$ ]]
}

resolve_path() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$PROJECT_ROOT" "$1" ;;
    esac
}

mkdir -p "$LOG_DIR" "${PROJECT_ROOT}/.locks"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log "已有盘中刷新任务在运行，跳过"
    exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

if [ ! -d "$VENV_DIR" ]; then
    log "[ERROR] 虚拟环境不存在: $VENV_DIR"
    exit 1
fi

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
export AQSP_RUN_TASK_ID="intraday"
export AQSP_NOTIFY="false"
export AQSP_GATE_NOTIFY="false"

DOW=$(date +%u)
if [ "$DOW" -ge 6 ]; then
    log "周末(周${DOW})，跳过盘中刷新"
    exit 0
fi

if ! "${PYTHON_BIN}" - <<'AQSP_CALENDAR_PY'
from aqsp.core.time import is_trading_day, today_shanghai
raise SystemExit(0 if is_trading_day(today_shanghai()) else 1)
AQSP_CALENDAR_PY
then
    log "今日非交易日，跳过盘中刷新"
    exit 0
fi

REQUIRE_MARKET_HOURS="${AQSP_INTRADAY_REQUIRE_MARKET_HOURS:-true}"
NOW_HM=$((10#$(date +%H%M)))
if is_truthy "$REQUIRE_MARKET_HOURS"; then
    if ! { [ "$NOW_HM" -ge 935 ] && [ "$NOW_HM" -le 1130 ]; } && \
       ! { [ "$NOW_HM" -ge 1305 ] && [ "$NOW_HM" -le 1457 ]; }; then
        log "当前不在盘中刷新时段，跳过"
        exit 0
    fi
fi

INTRADAY_SOURCE="${AQSP_INTRADAY_SOURCE:-${AQSP_SOURCE:-eastmoney}}"
INTRADAY_MODE="${AQSP_INTRADAY_MODE:-open}"
INTRADAY_LIMIT="${AQSP_INTRADAY_LIMIT:-${AQSP_LIMIT:-10}}"
INTRADAY_MAX_UNIVERSE="${AQSP_INTRADAY_MAX_UNIVERSE:-${AQSP_MAX_UNIVERSE:-0}}"
INTRADAY_MIN_AVG_AMOUNT="${AQSP_INTRADAY_MIN_AVG_AMOUNT:-${AQSP_MIN_AVG_AMOUNT:-50000000}}"
INTRADAY_MAX_DATA_LAG_DAYS="${AQSP_INTRADAY_MAX_DATA_LAG_DAYS:-1}"
INTRADAY_ALLOW_NOTIFY="${AQSP_INTRADAY_ALLOW_NOTIFY:-false}"
INTRADAY_NOTIFY="${AQSP_INTRADAY_NOTIFY:-false}"

INTRADAY_LEDGER="$(resolve_path "${AQSP_INTRADAY_LEDGER:-data/intraday_predictions.jsonl}")"
INTRADAY_REPORT="$(resolve_path "${AQSP_INTRADAY_REPORT:-reports/intraday_latest.md}")"
INTRADAY_OUTPUT_CSV="$(resolve_path "${AQSP_INTRADAY_OUTPUT_CSV:-reports/intraday_latest.csv}")"
INTRADAY_DASHBOARD_HTML="$(resolve_path "${AQSP_INTRADAY_DASHBOARD_HTML:-dist/dashboard/index.html}")"
INTRADAY_DASHBOARD_DB="$(resolve_path "${AQSP_INTRADAY_DASHBOARD_DB:-dist/dashboard/aqsp.db}")"
PAPER_LEDGER="$(resolve_path "${AQSP_PAPER_LEDGER:-data/paper_trades.jsonl}")"

mkdir -p \
    "$(dirname "$INTRADAY_LEDGER")" \
    "$(dirname "$INTRADAY_REPORT")" \
    "$(dirname "$INTRADAY_OUTPUT_CSV")" \
    "$(dirname "$INTRADAY_DASHBOARD_HTML")" \
    "$(dirname "$INTRADAY_DASHBOARD_DB")"

rm -f "$INTRADAY_LEDGER" "$INTRADAY_REPORT" "$INTRADAY_OUTPUT_CSV"

cd "$PROJECT_ROOT"

log "=========================================="
log "AI量化选股 - 盘中刷新开始"
log "项目目录: ${PROJECT_ROOT}"
log "Python: ${PYTHON_BIN}"
log "数据源: ${INTRADAY_SOURCE}"
log "=========================================="

START_TIME=$(date +%s)
NOTIFY_ARGS=()
if is_truthy "$INTRADAY_ALLOW_NOTIFY" && is_truthy "$INTRADAY_NOTIFY"; then
    NOTIFY_ARGS=(--notify)
elif is_truthy "$INTRADAY_NOTIFY"; then
    log "盘中通知未显式放行，忽略 AQSP_INTRADAY_NOTIFY=true"
fi

set +e
"${PYTHON_BIN}" -m aqsp run \
    --source "${INTRADAY_SOURCE}" \
    --mode "${INTRADAY_MODE}" \
    --limit "${INTRADAY_LIMIT}" \
    --max-universe "${INTRADAY_MAX_UNIVERSE}" \
    --min-avg-amount "${INTRADAY_MIN_AVG_AMOUNT}" \
    --max-data-lag-days "${INTRADAY_MAX_DATA_LAG_DAYS}" \
    --benchmark-symbol "" \
    --ledger "${INTRADAY_LEDGER}" \
    --report "${INTRADAY_REPORT}" \
    --output-csv "${INTRADAY_OUTPUT_CSV}" \
    --skip-validation \
    "${NOTIFY_ARGS[@]}" 2>&1 | tee -a "$RESULT_LOG"
RUN_EXIT_CODE=$?
set -e

if [ "$RUN_EXIT_CODE" -ne 0 ] && [ "$RUN_EXIT_CODE" -ne 2 ]; then
    log "[ERROR] 盘中选股失败，退出码: ${RUN_EXIT_CODE}"
    exit "$RUN_EXIT_CODE"
fi

if [ "$RUN_EXIT_CODE" -eq 2 ]; then
    log "盘中选股触发熔断，仅刷新观察展示，不新增正式待复核"
fi

"${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/open_dashboard.py" \
    --render-only \
    --no-open-browser \
    --csv "${INTRADAY_OUTPUT_CSV}" \
    --ledger "${INTRADAY_LEDGER}" \
    --paper-ledger "${PAPER_LEDGER}" \
    --output "${INTRADAY_DASHBOARD_HTML}" \
    --db "${INTRADAY_DASHBOARD_DB}" 2>&1 | tee -a "$RESULT_LOG"

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

log "=========================================="
log "盘中刷新结束"
log "退出码: ${RUN_EXIT_CODE}"
log "耗时: ${DURATION}秒"
log "Dashboard: ${INTRADAY_DASHBOARD_HTML}"
log "报告: ${INTRADAY_REPORT}"
log "CSV: ${INTRADAY_OUTPUT_CSV}"
log "=========================================="

find "$LOG_DIR" -name "intraday-*.log" -mtime +15 -delete 2>/dev/null || true

exit 0
