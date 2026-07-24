#!/usr/bin/env bash
# 午盘回看：
# 1. 利用上午收盘后的最新快照做一次中午回看
# 2. 复用 intraday 产物链路，不污染正式收盘 ledger
# 3. 默认只在工作日 11:35-12:30 运行

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
LOG_DIR="${AQSP_LOG_ROOT:-${PROJECT_ROOT}/logs}/midday"
RESULT_LOG="${LOG_DIR}/midday-$(date +%Y-%m-%d).log"

log() {
    mkdir -p "$LOG_DIR"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RESULT_LOG"
}

if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "${PROJECT_ROOT}/.env"
    set +a
fi
PYTHON_BIN="$(aqsp_runtime_python "$PROJECT_ROOT")"
aqsp_require_runtime_python "$PYTHON_BIN"

is_truthy() {
    local value
    value="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
    [[ "$value" =~ ^(1|true|yes|on)$ ]]
}

DOW=$(date +%u)
if [ "$DOW" -ge 6 ]; then
    log "周末(周${DOW})，跳过午盘回看"
    exit 0
fi

export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}"
if ! "${PYTHON_BIN}" - <<'AQSP_CALENDAR_PY'
from aqsp.core.time import is_trading_day, today_shanghai
raise SystemExit(0 if is_trading_day(today_shanghai()) else 1)
AQSP_CALENDAR_PY
then
    log "今日非交易日，跳过午盘回看"
    exit 0
fi

REQUIRE_WINDOW="${AQSP_MIDDAY_REQUIRE_WINDOW:-true}"
NOW_HM=$((10#$(date +%H%M)))
if is_truthy "$REQUIRE_WINDOW"; then
    if ! { [ "$NOW_HM" -ge 1135 ] && [ "$NOW_HM" -le 1230 ]; }; then
        log "当前不在午盘回看时段，跳过"
        exit 0
    fi
fi

export AQSP_INTRADAY_REQUIRE_MARKET_HOURS=false
export AQSP_RUN_TASK_ID="midday"
export AQSP_NOTIFY="false"
export AQSP_GATE_NOTIFY="false"
export AQSP_NOTIFY_TITLE_LABEL="${AQSP_NOTIFY_TITLE_LABEL:-午盘分析}"
export AQSP_INTRADAY_NOTIFY="false"
export AQSP_INTRADAY_ALLOW_NOTIFY="false"

log "开始午盘回看，复用盘中观察链路"
/bin/bash "${PROJECT_ROOT}/scripts/intraday_refresh.sh" 2>&1 | tee -a "$RESULT_LOG"
