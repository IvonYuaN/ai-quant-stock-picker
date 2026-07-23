#!/usr/bin/env bash
# Rebuild isolated paper variants from an explicitly selected data partition.
# Production defaults to paper_realtime; historical backtests must opt in.
set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RUNTIME_ROOT="${AQSP_RUNTIME_ROOT:-${PROJECT_ROOT}}"
PYTHON_BIN="${AQSP_RUNTIME_PYTHON:-${AQSP_PYTHON:-${VIBE_RESEARCH_PYTHON_BIN:-}}}"
if [[ -z "$PYTHON_BIN" && -n "${AQSP_RUNTIME_VENV_DIR:-}" ]]; then
    PYTHON_BIN="${AQSP_RUNTIME_VENV_DIR}/bin/python"
fi
if [[ -z "$PYTHON_BIN" && -n "${AQSP_VIBE_VENV_DIR:-}" ]]; then
    PYTHON_BIN="${AQSP_VIBE_VENV_DIR}/bin/python"
fi
if [[ -z "$PYTHON_BIN" && -x "${PROJECT_ROOT}/.venv-vibe-research/bin/python" ]]; then
    PYTHON_BIN="${PROJECT_ROOT}/.venv-vibe-research/bin/python"
fi
if [[ -z "$PYTHON_BIN" ]]; then
    PYTHON_BIN="$(command -v python3 || true)"
fi
DB_PATH="${AQSP_VARIANT_DB:-${AQSP_SQLITE_DB_PATH:-${RUNTIME_ROOT}/data/cache.db}}"
OUTPUT_PATH="${AQSP_VARIANT_RESULTS:-${RUNTIME_ROOT}/data/runtime/variant_results.json}"
SNAPSHOT_PATH="${AQSP_HOME_SNAPSHOT_PATH:-${RUNTIME_ROOT}/data/runtime/home_dashboard_snapshot.json}"
INDEX_PATH="${AQSP_HOME_SNAPSHOT_INDEX_PATH:-${RUNTIME_ROOT}/data/runtime/home_dashboard_snapshot_index.json}"
UNIVERSE_SIZE="${AQSP_VARIANT_UNIVERSE_SIZE:-0}"
VARIANT_NICE="${AQSP_VARIANT_NICE:-10}"
RUN_MODE="${AQSP_VARIANT_RUN_MODE:-paper_realtime}"
case "$RUN_MODE" in
    paper_realtime) WORKLOAD="live_short" ;;
    backtest_historical) WORKLOAD="historical" ;;
    *) echo "unsupported AQSP_VARIANT_RUN_MODE: $RUN_MODE" >&2; exit 1 ;;
esac

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "variant refresh requires release Python: $PYTHON_BIN" >&2
    exit 1
fi
if [[ ! -f "$DB_PATH" ]]; then
    echo "variant refresh database is missing: $DB_PATH" >&2
    exit 1
fi
if [[ ! "$VARIANT_NICE" =~ ^[0-9]+$ ]] || (( VARIANT_NICE > 19 )); then
    echo "variant refresh nice must be an integer from 0 to 19: $VARIANT_NICE" >&2
    exit 1
fi

export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}"
CALENDAR_CANDIDATE_DATE="$($PYTHON_BIN - <<'PY'
from aqsp.core.time import today_shanghai

print(today_shanghai().isoformat())
PY
)"
RESET_DATE="$(
    AQSP_VARIANT_DB="$DB_PATH" CALENDAR_CANDIDATE_DATE="$CALENDAR_CANDIDATE_DATE" \
        VARIANT_RUN_MODE="$RUN_MODE" VARIANT_WORKLOAD="$WORKLOAD" \
        "$PYTHON_BIN" - <<'PY'
import os
import sqlite3
from pathlib import Path


db_path = Path(os.environ["AQSP_VARIANT_DB"])
candidate = os.environ["CALENDAR_CANDIDATE_DATE"]
run_mode = os.environ["VARIANT_RUN_MODE"]
workload = os.environ["VARIANT_WORKLOAD"]
compact_candidate = candidate.replace("-", "")
with sqlite3.connect(db_path) as conn:
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if {"daily_qfq", "stocks"} <= tables:
        if run_mode == "paper_realtime":
            raise SystemExit("paper_realtime 要求 ohlcv(raw,live_short)，不得读取 daily_qfq")
        row = conn.execute(
            """
            SELECT MAX(trade_date)
            FROM daily_qfq
            WHERE trade_date != 'SKIP' AND trade_date <= ?
            """,
            (compact_candidate,),
        ).fetchone()
        value = str(row[0] or "")
        if len(value) == 8:
            print(f"{value[:4]}-{value[4:6]}-{value[6:8]}")
        else:
            raise SystemExit("raw daily_qfq 没有不晚于日历候选日的交易日")
    elif "ohlcv" in tables:
        row = conn.execute(
            """
            SELECT MAX(date)
            FROM ohlcv
            WHERE price_mode = 'raw' AND workload = ? AND date <= ?
            """,
            (workload, candidate),
        ).fetchone()
        value = str(row[0] or "")
        if value:
            print(value)
        else:
            raise SystemExit("raw ohlcv 没有不晚于日历候选日的交易日")
    else:
        raise SystemExit("数据库缺少 raw ohlcv 或 daily_qfq/stocks 表")
PY
)"
PREVIOUS_RESET_DATE="$(
    AQSP_VARIANT_DB="$DB_PATH" RESET_DATE="$RESET_DATE" \
        VARIANT_RUN_MODE="$RUN_MODE" VARIANT_WORKLOAD="$WORKLOAD" \
        "$PYTHON_BIN" - <<'PY'
import os
import sqlite3
from pathlib import Path


db_path = Path(os.environ["AQSP_VARIANT_DB"])
reset_date = os.environ["RESET_DATE"]
run_mode = os.environ["VARIANT_RUN_MODE"]
workload = os.environ["VARIANT_WORKLOAD"]
compact_reset = reset_date.replace("-", "")
with sqlite3.connect(db_path) as conn:
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if {"daily_qfq", "stocks"} <= tables:
        if run_mode == "paper_realtime":
            raise SystemExit("paper_realtime 要求 ohlcv(raw,live_short)，不得读取 daily_qfq")
        row = conn.execute(
            """
            SELECT MAX(trade_date)
            FROM daily_qfq
            WHERE trade_date != 'SKIP' AND trade_date < ?
            """,
            (compact_reset,),
        ).fetchone()
        value = str(row[0] or "")
        print(f"{value[:4]}-{value[4:6]}-{value[6:8]}" if len(value) == 8 else "")
    elif "ohlcv" in tables:
        row = conn.execute(
            """
            SELECT MAX(date)
            FROM ohlcv
            WHERE price_mode = 'raw' AND workload = ? AND date < ?
            """,
            (workload, reset_date),
        ).fetchone()
        print(str(row[0] or ""))
    else:
        raise SystemExit("数据库缺少 raw ohlcv 或 daily_qfq/stocks 表")
PY
)"
TODAY="$($PYTHON_BIN - <<'PY'
from aqsp.core.time import today_shanghai

print(today_shanghai().isoformat())
PY
)"

# The paper suite is refreshed at the latest completed raw trade date, while
# the dashboard snapshot remains dated today and must not present history as
# a formal live recommendation.

mkdir -p "$(dirname "$OUTPUT_PATH")"
TMP_OUTPUT="${OUTPUT_PATH}.next.$$"
cleanup() {
    rm -f -- "$TMP_OUTPUT"
}
trap cleanup EXIT

nice -n "$VARIANT_NICE" "$PYTHON_BIN" "$PROJECT_ROOT/scripts/run_variant_suite.py" \
    --db "$DB_PATH" \
    --universe-size "$UNIVERSE_SIZE" \
    --start "$RESET_DATE" \
    --end "$RESET_DATE" \
    --run-mode "$RUN_MODE" \
    --output "$TMP_OUTPUT"

VARIANT_TMP="$TMP_OUTPUT" VARIANT_OUTPUT="$OUTPUT_PATH" \
EXPECTED_START_DATE="$RESET_DATE" EXPECTED_END_DATE="$RESET_DATE" \
EXPECTED_RUN_MODE="$RUN_MODE" \
EXPECTED_PREVIOUS_DATE="$PREVIOUS_RESET_DATE" \
    "$PYTHON_BIN" - <<'PY'
import json
import os
import stat
from pathlib import Path

from scripts.run_variant_suite import (
    attach_previous_variant_holdings,
    validate_variant_artifact,
    validate_previous_variant_baseline,
)

temporary = Path(os.environ["VARIANT_TMP"])
output = Path(os.environ["VARIANT_OUTPUT"])
expected_date = os.environ["EXPECTED_END_DATE"]
expected_start_date = os.environ["EXPECTED_START_DATE"]
payload = json.loads(temporary.read_text(encoding="utf-8"))
previous_payload = None
if output.exists():
    try:
        candidate = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        candidate = None
    if isinstance(candidate, dict):
        previous_payload = candidate
payload = attach_previous_variant_holdings(
    payload,
    previous_payload,
    expected_previous_date=os.environ.get("EXPECTED_PREVIOUS_DATE", ""),
)
validate_previous_variant_baseline(
    payload,
    previous_payload,
    expected_previous_date=os.environ.get("EXPECTED_PREVIOUS_DATE", ""),
)
validate_variant_artifact(
    payload,
    expected_end_date=expected_date,
    expected_start_date=expected_start_date,
    expected_run_mode=os.environ["EXPECTED_RUN_MODE"],
)
old_mode = stat.S_IMODE(output.stat().st_mode) if output.exists() else 0o640
os.replace(temporary, output)
os.chmod(output, old_mode)
print(
    f"variant artifact installed: {len(payload['variants'])} variants, "
    f"{len(payload['symbols'])} symbols, reset={expected_date}"
)
PY

AQSP_SQLITE_DB_PATH="$DB_PATH" "$PYTHON_BIN" "$PROJECT_ROOT/scripts/write_home_snapshot.py" \
    --output "$SNAPSHOT_PATH" \
    --index-output "$INDEX_PATH" \
    --date "$TODAY" \
    --task-id variants

echo "variant refresh completed: mode=$RUN_MODE workload=$WORKLOAD reset=$RESET_DATE snapshot_date=$TODAY"
