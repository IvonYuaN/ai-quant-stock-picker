#!/usr/bin/env bash
# 事件数据预加载：把「事件 / 风险面」数据源刷进 <runtime>/pit_cache/*.csv。
#
# 消费方 = aqsp.features.event_calendar.EventCalendar.from_cache（只读 pit_cache，绝不联网）。
# 生产闭环 = bt_task.sh event-data → 本脚本 → pit_cache/*.csv → EventCalendar。
# best-effort：单个源失败不阻断其余源；仅当「有尝试且全部失败」时返回非 0。
set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
RUNTIME_ROOT="${AQSP_RUNTIME_ROOT:-$PROJECT_ROOT}"
RUNTIME_DATA_ROOT="${AQSP_RUNTIME_DATA_ROOT:-${RUNTIME_ROOT}/data}"
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

LOG_DIR="${AQSP_EVENT_DATA_LOG_DIR:-${RUNTIME_DATA_ROOT}/logs/event-data}"
mkdir -p "$LOG_DIR"
RESULT_LOG="${LOG_DIR}/event-data-$(date +%Y-%m-%d).log"

log() {
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

export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}"
export TZ="${TZ:-Asia/Shanghai}"
export AQSP_RUN_TASK_ID="${AQSP_RUN_TASK_ID:-event_data}"

# name:relative-script —— 名称仅用于日志；失败不阻断兄弟源。
SOURCES=(
    "lockup:scripts/fetch_lockup.py"
    "longhubang:scripts/fetch_longhubang.py"
    "cls_news:scripts/fetch_cls_news.py"
    "concept_board:scripts/fetch_concept_board.py"
    "event_data:scripts/fetch_event_data.py"
)

log "开始事件数据预加载"
ATTEMPTED=0
FAILED=0
for entry in "${SOURCES[@]}"; do
    name="${entry%%:*}"
    rel="${entry#*:}"
    path="${PROJECT_ROOT}/${rel}"
    if [ ! -f "$path" ]; then
        log "[WARN] 缺少抓取脚本，跳过: ${rel}"
        FAILED=$((FAILED + 1))
        continue
    fi
    ATTEMPTED=$((ATTEMPTED + 1))
    if "$PYTHON_BIN" "$path" >>"$RESULT_LOG" 2>&1; then
        log "[OK] ${name} -> pit_cache"
    else
        log "[WARN] ${name} 预加载失败（best-effort，不阻断其余源）"
        FAILED=$((FAILED + 1))
    fi
done
log "事件数据预加载完成: attempted=${ATTEMPTED} failed=${FAILED}"

if [ "$ATTEMPTED" -gt 0 ] && [ "$FAILED" -eq "$ATTEMPTED" ]; then
    log "[ERROR] 全部事件源预加载失败"
    exit 1
fi
