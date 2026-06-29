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
    echo "=== dashboard refresh @ $(date) ==="
    "$PYTHON_BIN" scripts/open_dashboard.py \
        --csv "$AQSP_OUTPUT_CSV" \
        --ledger "$AQSP_LEDGER" \
        --paper-ledger "$AQSP_PAPER_LEDGER" \
        --output "$AQSP_DASHBOARD_HTML" \
        --db "$AQSP_DASHBOARD_DB" \
        --render-only 2>&1

    echo ""
    echo "=== runtime diagnosis @ $(date) ==="
    "$PYTHON_BIN" scripts/diagnose_runtime.py | tee "$AQSP_DIAGNOSIS"
} >> "$LOG" 2>&1

echo "[$(date)] daily_run done, log: $LOG"
