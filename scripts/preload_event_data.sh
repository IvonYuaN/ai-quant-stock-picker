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
# 关键：把 runtime data root 显式传给 Python 子进程（#161 写读同源）。
# 消费者 release_task_entrypoint.sh 以 ${AQSP_RUNTIME_DATA_ROOT:-/opt/aqsp}/data 读
# pit_cache/*.csv；若不在此 export，生产者 fetch_* 里的 os.environ.get 会回落系统
# tempdir，写到 /tmp 而非 /opt/aqsp/data ⇒ 过滤器读不到，排雷链继续空转。
export AQSP_RUNTIME_DATA_ROOT="$RUNTIME_DATA_ROOT"

# name:relative-script —— 名称仅用于日志；失败不阻断兄弟源。
SOURCES=(
    "lockup:scripts/fetch_lockup.py"
    "longhubang:scripts/fetch_longhubang.py"
    "cls_news:scripts/fetch_cls_news.py"
    "concept_board:scripts/fetch_concept_board.py"
    "event_data:scripts/fetch_event_data.py"
)

# ---------------------------------------------------------------------------
# concept_board runner 中继（2026-09-27 根治「板块资金面段永远陈旧」）：
# prod 出口 IP 被东财 push2 全族网络级阻断（curl/requests 均 RemoteDisconnected，
# 09-08 起连续失败 3 周、被 best-effort WARN 静默吞掉）；runner 实测 push2delay /
# 82.push2 均 HTTP 200。故 concept_board 先经 runner 代抓再 rsync 回流本机
# pit_cache（复用 IC 回流先例与 prod→runner 免密）；中继失败回落本地直连（旧行为）。
# 设 CONCEPT_BOARD_RELAY=0 可停用中继。
# ---------------------------------------------------------------------------
CONCEPT_BOARD_RELAY="${CONCEPT_BOARD_RELAY:-1}"
RELAY_HOST="${CONCEPT_BOARD_RELAY_HOST:-root@38.147.170.174}"
RELAY_PORT="${CONCEPT_BOARD_RELAY_PORT:-31777}"
RELAY_ROOT="${CONCEPT_BOARD_RELAY_ROOT:-/opt/aqsp-runner}"
RELAY_TIMEOUT="${CONCEPT_BOARD_RELAY_TIMEOUT:-120}"

fetch_concept_board_via_runner() {
    local relay_ssh="ssh -p ${RELAY_PORT} -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
    # 1) runner 代抓（runner 用自己的 release + venv + runtime root，写 runner pit_cache）
    if ! $relay_ssh "$RELAY_HOST" \
        "PYTHONPATH=${RELAY_ROOT}/aqsp-scheduler-current/src AQSP_RUNTIME_DATA_ROOT=${RELAY_ROOT} timeout ${RELAY_TIMEOUT} ${RELAY_ROOT}/venv/bin/python ${RELAY_ROOT}/aqsp-scheduler-current/scripts/fetch_concept_board.py"; then
        return 1
    fi
    # 2) 回流：runner pit_cache -> 本机 pit_cache（写读同源，消费方只读本机文件）
    rsync -a -e "$relay_ssh" \
        "${RELAY_HOST}:${RELAY_ROOT}/pit_cache/concept_board.csv" \
        "${RUNTIME_DATA_ROOT}/pit_cache/concept_board.csv"
}

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
    if [ "$name" = "concept_board" ] && [ "$CONCEPT_BOARD_RELAY" = "1" ]; then
        if fetch_concept_board_via_runner >>"$RESULT_LOG" 2>&1; then
            log "[OK] ${name} -> pit_cache（经 runner 中继）"
            continue
        fi
        log "[WARN] ${name} runner 中继失败，回落本地直连"
    fi
    if "$PYTHON_BIN" "$path" >>"$RESULT_LOG" 2>&1; then
        log "[OK] ${name} -> pit_cache"
    else
        log "[WARN] ${name} 预加载失败（best-effort，不阻断其余源）"
        FAILED=$((FAILED + 1))
    fi
done
log "事件数据预加载完成: attempted=${ATTEMPTED} failed=${FAILED}"

# ---------------------------------------------------------------------------
# 新鲜度审计（2026-09-27）：concept_board 曾连续失败 3 周无人知（best-effort WARN
# 不响亮）。对全部预载产物做 mtime 审计，陈旧即 [ERROR] 响亮告警（monitors 可捕）；
# 只告警不改退出码——数据陈旧 ≠ 本轮失败。
# ---------------------------------------------------------------------------
FRESH_HOURS="${AQSP_PRELOAD_FRESH_HOURS:-48}"
now_ts="$(date +%s)"
for f in "${RUNTIME_DATA_ROOT}"/pit_cache/*.csv; do
    [ -f "$f" ] || continue
    mtime="$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null || echo 0)"
    age_h=$(( (now_ts - mtime) / 3600 ))
    if [ "$age_h" -gt "$FRESH_HOURS" ]; then
        log "[ERROR] pit_cache/$(basename "$f") 陈旧（${age_h}h 前，超 ${FRESH_HOURS}h）——对应数据源可能持续失败，请排查"
    fi
done

if [ "$ATTEMPTED" -gt 0 ] && [ "$FAILED" -eq "$ATTEMPTED" ]; then
    log "[ERROR] 全部事件源预加载失败"
    exit 1
fi
