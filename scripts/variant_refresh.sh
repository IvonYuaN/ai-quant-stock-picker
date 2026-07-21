#!/usr/bin/env bash
# Rebuild isolated paper variants from the previous trading day's raw bars.
set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RUNTIME_ROOT="${AQSP_RUNTIME_ROOT:-${PROJECT_ROOT}}"
PYTHON_BIN="${AQSP_RUNTIME_PYTHON:-${PROJECT_ROOT}/.venv/bin/python3}"
DB_PATH="${AQSP_VARIANT_DB:-${RUNTIME_ROOT}/data/cache.db}"
OUTPUT_PATH="${AQSP_VARIANT_RESULTS:-${RUNTIME_ROOT}/data/runtime/variant_results.json}"
SNAPSHOT_PATH="${AQSP_HOME_SNAPSHOT_PATH:-${RUNTIME_ROOT}/data/runtime/home_dashboard_snapshot.json}"
INDEX_PATH="${AQSP_HOME_SNAPSHOT_INDEX_PATH:-${RUNTIME_ROOT}/data/runtime/home_dashboard_snapshot_index.json}"
UNIVERSE_SIZE="${AQSP_VARIANT_UNIVERSE_SIZE:-0}"

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "variant refresh requires release Python: $PYTHON_BIN" >&2
    exit 1
fi
if [[ ! -f "$DB_PATH" ]]; then
    echo "variant refresh database is missing: $DB_PATH" >&2
    exit 1
fi

export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}"
RESET_DATE="$($PYTHON_BIN - <<'PY'
from aqsp.core.time import get_previous_trading_day, today_shanghai

print(get_previous_trading_day(today_shanghai()).isoformat())
PY
)"
TODAY="$($PYTHON_BIN - <<'PY'
from aqsp.core.time import today_shanghai

print(today_shanghai().isoformat())
PY
)"

mkdir -p "$(dirname "$OUTPUT_PATH")"
TMP_OUTPUT="${OUTPUT_PATH}.next.$$"
cleanup() {
    rm -f -- "$TMP_OUTPUT"
}
trap cleanup EXIT

"$PYTHON_BIN" "$PROJECT_ROOT/scripts/run_variant_suite.py" \
    --db "$DB_PATH" \
    --universe-size "$UNIVERSE_SIZE" \
    --start "$RESET_DATE" \
    --end "$RESET_DATE" \
    --output "$TMP_OUTPUT"

VARIANT_TMP="$TMP_OUTPUT" VARIANT_OUTPUT="$OUTPUT_PATH" \
EXPECTED_START_DATE="$RESET_DATE" EXPECTED_END_DATE="$RESET_DATE" \
    "$PYTHON_BIN" - <<'PY'
import json
import os
import stat
from pathlib import Path

from scripts.run_variant_suite import validate_variant_artifact

temporary = Path(os.environ["VARIANT_TMP"])
output = Path(os.environ["VARIANT_OUTPUT"])
expected_date = os.environ["EXPECTED_END_DATE"]
expected_start_date = os.environ["EXPECTED_START_DATE"]
payload = json.loads(temporary.read_text(encoding="utf-8"))
validate_variant_artifact(
    payload,
    expected_end_date=expected_date,
    expected_start_date=expected_start_date,
)
old_mode = stat.S_IMODE(output.stat().st_mode) if output.exists() else 0o640
os.replace(temporary, output)
os.chmod(output, old_mode)
print(
    f"variant artifact installed: {len(payload['variants'])} variants, "
    f"{len(payload['symbols'])} symbols, reset={expected_date}"
)
PY

"$PYTHON_BIN" "$PROJECT_ROOT/scripts/write_home_snapshot.py" \
    --output "$SNAPSHOT_PATH" \
    --index-output "$INDEX_PATH" \
    --date "$TODAY" \
    --task-id variants

echo "variant refresh completed: reset=$RESET_DATE snapshot_date=$TODAY"
