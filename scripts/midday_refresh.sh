#!/usr/bin/env bash
# 午盘回看：
# 1. 利用上午收盘后的最新快照做一次中午回看
# 2. 复用 intraday 产物链路，不污染正式收盘 ledger
# 3. 默认只在工作日 11:35-12:30 运行

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
LOG_DIR="${PROJECT_ROOT}/logs/midday"
RESULT_LOG="${LOG_DIR}/midday-$(date +%Y-%m-%d).log"

log() {
    mkdir -p "$LOG_DIR"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RESULT_LOG"
}

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
export AQSP_NOTIFY_TITLE_LABEL="${AQSP_NOTIFY_TITLE_LABEL:-午盘分析}"
export AQSP_INTRADAY_NOTIFY="false"
export AQSP_INTRADAY_ALLOW_NOTIFY="false"

log "开始午盘回看，复用盘中观察链路"
/bin/bash "${PROJECT_ROOT}/scripts/intraday_refresh.sh" 2>&1 | tee -a "$RESULT_LOG"
