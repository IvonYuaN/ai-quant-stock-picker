#!/usr/bin/env bash
# 盘前主链：生成早盘候选并刷新当天首页快照。
set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
RUNTIME_DATA_ROOT="${AQSP_RUNTIME_DATA_ROOT:-${PROJECT_ROOT}/data}"
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

PYTHON_BIN="$(aqsp_runtime_python "$PROJECT_ROOT")"
if ! aqsp_require_runtime_python "$PYTHON_BIN"; then
    echo "[ERROR] 当前 release 没有可用的运行时 Python: $PYTHON_BIN" >&2
    exit 1
fi

if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "${PROJECT_ROOT}/.env"
    set +a
fi

export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}"
export TZ="${TZ:-Asia/Shanghai}"
export AQSP_RUN_TASK_ID="main_chain"
export AQSP_NOTIFY="false"
export AQSP_GATE_NOTIFY="false"

OUTPUT="${AQSP_MORNING_OUTPUT:-${RUNTIME_DATA_ROOT}/runtime/morning_breakout_latest.json}"
LEDGER="${AQSP_LEDGER:-${RUNTIME_DATA_ROOT}/predictions.jsonl}"
SOURCE="${AQSP_MORNING_SOURCE:-online_first}"
POOL="${AQSP_MORNING_POOL:-all}"
MAX_UNIVERSE="${AQSP_MORNING_MAX_UNIVERSE:-300}"
TOP="${AQSP_MORNING_TOP:-5}"

mkdir -p "$(dirname "$OUTPUT")"
"$PYTHON_BIN" -m aqsp morning-breakout \
    --source "$SOURCE" \
    --pool "$POOL" \
    --max-universe "$MAX_UNIVERSE" \
    --top "$TOP" \
    --ledger "$LEDGER" \
    --output "$OUTPUT"

# 盘前必须使用当天日期，不能让默认解析回退到上一交易日。
TODAY="$("$PYTHON_BIN" - <<'PY'
from aqsp.core.time import today_shanghai

print(today_shanghai().isoformat())
PY
)"
"$PYTHON_BIN" "${PROJECT_ROOT}/scripts/write_home_snapshot.py" \
    --date "$TODAY" \
    --task-id main_chain \
    --output "${AQSP_HOME_SNAPSHOT_PATH:-${RUNTIME_DATA_ROOT}/runtime/home_dashboard_snapshot.json}" \
    --index-output "${AQSP_HOME_SNAPSHOT_INDEX_PATH:-${RUNTIME_DATA_ROOT}/runtime/home_dashboard_snapshot_index.json}"
