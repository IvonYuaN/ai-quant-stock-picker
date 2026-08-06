#!/usr/bin/env bash
# 宝塔面板计划任务统一入口。
# 用法:
#   /bin/bash /opt/aqsp/scripts/bt_task.sh daily
#   /bin/bash /opt/aqsp/scripts/bt_task.sh intraday
#   /bin/bash /opt/aqsp/scripts/bt_task.sh midday
#   /bin/bash /opt/aqsp/scripts/bt_task.sh data-refresh
#   /bin/bash /opt/aqsp/scripts/bt_task.sh coldstart
#   /bin/bash /opt/aqsp/scripts/bt_task.sh variant-refresh
#   /bin/bash /opt/aqsp/scripts/bt_task.sh walkforward-gate
#   /bin/bash /opt/aqsp/scripts/bt_task.sh monitor
#   /bin/bash /opt/aqsp/scripts/bt_task.sh news
#   /bin/bash /opt/aqsp/scripts/bt_task.sh status

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
RUNTIME_ROOT="${AQSP_RUNTIME_ROOT:-$PROJECT_ROOT}"
RUNTIME_DATA_ROOT="${AQSP_RUNTIME_DATA_ROOT:-${RUNTIME_ROOT}/data}"
INITIAL_PROJECT_ROOT="$PROJECT_ROOT"
if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "${PROJECT_ROOT}/.env"
    set +a
    # .env may configure the runtime, but must not redirect this checkout.
    PROJECT_ROOT="$INITIAL_PROJECT_ROOT"
fi
# 计划任务按北京时间解释日期、交易日和时段；不可继承宿主机或 .env 的 TZ。
export TZ="Asia/Shanghai"
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
ACTION="${1:-}"
LOG_DIR="${AQSP_BT_LOGS_DIR:-${RUNTIME_DATA_ROOT}/logs/bt}"
RUN_LOG="${LOG_DIR}/bt-${ACTION}-$(date +%Y-%m-%d).log"
BRANCH="${AQSP_GIT_BRANCH:-main}"
REMOTE="${AQSP_GIT_REMOTE:-origin}"
LOCK_DIR="${AQSP_RUNTIME_LOCK_DIR:-${RUNTIME_DATA_ROOT}/.locks}"
STATE_DIR="${AQSP_RUNTIME_STATE_DIR:-${RUNTIME_DATA_ROOT}/.state}"
HEAVY_SLOT_LOCK_FILE="${LOCK_DIR}/heavy-compute.lock"
HEAVY_SLOT_LOCK_INFO_FILE="${HEAVY_SLOT_LOCK_FILE}/meta.env"
AGENT_RUNS_PATH="${AQSP_AGENT_RUNS_PATH:-${RUNTIME_DATA_ROOT}/runtime/agent_runs.jsonl}"
AGENT_RUN_REGISTRY_SCRIPT="${PROJECT_ROOT}/scripts/agent_run_registry.py"
AGENT_RUN_ID=""
AGENT_RUN_ACTIVE="false"
HEAVY_SLOT_ACQUIRED="false"
GIT_SYNC_LOCK_FILE="${LOCK_DIR}/server-git-sync.lock"
GIT_SYNC_LOCK_INFO_FILE="${GIT_SYNC_LOCK_FILE}/meta.env"
GIT_SYNC_WAIT_SECONDS="${AQSP_GIT_SYNC_WAIT_SECONDS:-180}"
GIT_LOCK_STALE_MINUTES="${AQSP_GIT_LOCK_STALE_MINUTES:-30}"
export AQSP_RUNTIME_PYTHON="$(aqsp_runtime_python "$PROJECT_ROOT")"

log() {
    mkdir -p "$LOG_DIR"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RUN_LOG"
}

usage() {
    cat <<'EOF'
Usage: bt_task.sh <daily|daily-research|intraday|midday|data-refresh|data-refresh-retry|coldstart|variant-refresh|walkforward-gate|monitor|news|status>

BT panel examples:
  /bin/bash /opt/aqsp/scripts/bt_task.sh intraday
  /bin/bash /opt/aqsp/scripts/bt_task.sh daily
  /bin/bash /opt/aqsp/scripts/bt_task.sh daily-research
  /bin/bash /opt/aqsp/scripts/bt_task.sh midday
  /bin/bash /opt/aqsp/scripts/bt_task.sh data-refresh
  /bin/bash /opt/aqsp/scripts/bt_task.sh coldstart
  /bin/bash /opt/aqsp/scripts/bt_task.sh variant-refresh
  /bin/bash /opt/aqsp/scripts/bt_task.sh walkforward-gate
  /bin/bash /opt/aqsp/scripts/bt_task.sh monitor
  /bin/bash /opt/aqsp/scripts/bt_task.sh news

Recommended BT schedule (Asia/Shanghai):
  news      08:35 Mon-Fri trading days only; 09:05 Sat/Sun
  intraday  every 10 min; script gates 09:35-11:30 / 13:05-14:57, Mon-Fri
  midday    12:05 Mon-Fri
  daily     18:00 Mon-Fri
  daily-research 18:20-22:20 every 20 min Mon-Fri; one bounded cursor chunk
  data-refresh 15:35 Mon-Fri; bounded raw daily-data batch before daily research
  data-refresh-retry every 10 min from 15:45-19:30 Mon-Fri; bounded delayed refresh while the source publishes
  coldstart 19:40 Mon-Fri
  variant-refresh 22:30 Mon-Fri; bounded isolated experiment refresh after daily research
  walkforward-gate 22:00 Sat; controlled production evidence only, no threshold apply
  monitor   every 15 min
  status    manual only

Notes:
  "正常跳过/互斥保护" means another AQSP task is still running or the market
  window is closed. It is not a failed run.

Optional env:
  AQSP_RUNNER_TIMEOUT_SECONDS=5400   # 主链路最长 90 分钟
  AQSP_MONITOR_TIMEOUT_SECONDS=120   # 监控默认 2 分钟，硬上限 3 分钟
  AQSP_LOCK_STALE_MINUTES=360        # 无活跃 PID 时，6 小时后视为陈旧锁
EOF
}

if [ -z "$ACTION" ]; then
    usage >&2
    exit 2
fi

sync_code_only() {
    if [ "${AQSP_IMMUTABLE_RELEASE:-false}" = "true" ]; then
        log "immutable release 运行模式：跳过 Git fetch/pull"
        return 0
    fi
    (
        release_git_sync_lock() {
            rm -f "$GIT_SYNC_LOCK_INFO_FILE"
            rmdir "$GIT_SYNC_LOCK_FILE" 2>/dev/null || true
        }

        git_lock_age_minutes() {
            local path="$1"
            local now_epoch mtime
            now_epoch="$(date +%s)"
            mtime="$(stat -c %Y "$path" 2>/dev/null || stat -f %m "$path")"
            echo $(( (now_epoch - mtime) / 60 ))
        }

        load_git_sync_lock_info() {
            if [ -f "$GIT_SYNC_LOCK_INFO_FILE" ]; then
                # shellcheck disable=SC1090
                . "$GIT_SYNC_LOCK_INFO_FILE"
            fi
        }

        git_sync_lock_is_stale() {
            if [ ! -d "$GIT_SYNC_LOCK_FILE" ]; then
                return 1
            fi
            local age_minutes pid=""
            age_minutes="$(git_lock_age_minutes "$GIT_SYNC_LOCK_FILE")"
            load_git_sync_lock_info
            pid="${GIT_SYNC_LOCK_PID:-}"
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                return 1
            fi
            [ "$age_minutes" -ge "$GIT_LOCK_STALE_MINUTES" ]
        }

        acquire_git_sync_lock() {
            mkdir -p "$LOCK_DIR"
            local waited=0
            while ! mkdir "$GIT_SYNC_LOCK_FILE" 2>/dev/null; do
                if git_sync_lock_is_stale; then
                    stale_age="$(git_lock_age_minutes "$GIT_SYNC_LOCK_FILE")"
                    load_git_sync_lock_info
                    log "检测到陈旧 Git 同步锁，自动回收 runner=${GIT_SYNC_LOCK_RUNNER:-unknown} pid=${GIT_SYNC_LOCK_PID:-unknown} age=${stale_age}min started_at=${GIT_SYNC_LOCK_STARTED_AT:-unknown}"
                    rm -rf -- "$GIT_SYNC_LOCK_FILE"
                    continue
                fi
                if [ "$waited" -eq 0 ]; then
                    load_git_sync_lock_info
                    log "Git 同步进行中，等待释放 runner=${GIT_SYNC_LOCK_RUNNER:-unknown} pid=${GIT_SYNC_LOCK_PID:-unknown} started_at=${GIT_SYNC_LOCK_STARTED_AT:-unknown}"
                fi
                if [ "$waited" -ge "$GIT_SYNC_WAIT_SECONDS" ]; then
                    log "等待 Git 同步锁超时 ${GIT_SYNC_WAIT_SECONDS}s，取消本次同步"
                    return 1
                fi
                sleep 2
                waited=$((waited + 2))
            done
            {
                printf 'GIT_SYNC_LOCK_PID=%q\n' "$$"
                printf 'GIT_SYNC_LOCK_RUNNER=%q\n' "bt_task:${ACTION}"
                printf 'GIT_SYNC_LOCK_STARTED_AT=%q\n' "$(date '+%Y-%m-%d %H:%M:%S')"
            } >"$GIT_SYNC_LOCK_INFO_FILE"
            return 0
        }

managed_overlay_allows_dirty_state() {
            local dirty_tracked="$1"
            DIRTY_TRACKED_TEXT="$dirty_tracked" \
            RUNTIME_OVERLAY_MANIFEST_PATH="${AQSP_RUNTIME_OVERLAY_MANIFEST:-${STATE_DIR}/runtime-sync-overlay.json}" \
            python3 - <<'PY'
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

project_root = Path.cwd().resolve()
manifest_path = Path(os.environ["RUNTIME_OVERLAY_MANIFEST_PATH"]).resolve()
if not manifest_path.exists():
    raise SystemExit(1)

try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)

managed_raw = manifest.get("managed_files")
expected_hashes = manifest.get("file_hashes")
if not isinstance(managed_raw, list) or not managed_raw:
    raise SystemExit(1)
if not isinstance(expected_hashes, dict):
    raise SystemExit(1)

managed = set()
for raw_path in managed_raw:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise SystemExit(1)
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise SystemExit(1)
    if relative.as_posix() != raw_path:
        raise SystemExit(1)
    managed.add(raw_path)

dirty_lines = [
    line.rstrip("\n")
    for line in os.environ.get("DIRTY_TRACKED_TEXT", "").splitlines()
    if line.strip()
]
if not dirty_lines:
    raise SystemExit(1)

for line in dirty_lines:
    if len(line) < 4:
        raise SystemExit(1)
    status = line[:2]
    path = line[3:].strip()
    if path not in managed:
        raise SystemExit(1)
    if not any(ch == "M" for ch in status) or any(
        ch not in {" ", "M"} for ch in status
    ):
        raise SystemExit(1)
    expected_hash = str(expected_hashes.get(path) or "").strip()
    if len(expected_hash) != 64 or any(
        ch not in "0123456789abcdefABCDEF" for ch in expected_hash
    ):
        raise SystemExit(1)
    file_path = (project_root / path).resolve()
    try:
        file_path.relative_to(project_root)
    except ValueError:
        raise SystemExit(1)
    if not file_path.is_file():
        raise SystemExit(1)
    if hashlib.sha256(file_path.read_bytes()).hexdigest() != expected_hash:
        raise SystemExit(1)

print(len(dirty_lines))
PY
        }

        acquire_git_sync_lock || exit 1
        trap 'release_git_sync_lock' EXIT

        cd "$PROJECT_ROOT"
        log "开始同步代码: ${REMOTE}/${BRANCH}"

        git update-index --refresh >/dev/null 2>&1 || true
        dirty_tracked="$(git status --porcelain --untracked-files=no)"
        if [ -n "$dirty_tracked" ]; then
            if overlay_match_count="$(managed_overlay_allows_dirty_state "$dirty_tracked" 2>/dev/null)"; then
                log "检测到受控 runtime overlay，跳过 Git 同步后继续运行 count=${overlay_match_count}"
                log "本次跳过 Git fetch/pull；等待仓库回归 clean 后再恢复自动同步"
                return 0
            fi
            log "检测到受 Git 管理的本地修改，拒绝自动覆盖："
            printf '%s\n' "$dirty_tracked" | tee -a "$RUN_LOG"
            exit 1
        fi

        set +e
        git fetch "$REMOTE" "$BRANCH" 2>&1 | tee -a "$RUN_LOG"
        git_fetch_exit_code=${PIPESTATUS[0]}
        set -e
        if [ "$git_fetch_exit_code" -ne 0 ]; then
            log "Git fetch 失败，退出码: ${git_fetch_exit_code}"
            exit "$git_fetch_exit_code"
        fi
        local_head="$(git rev-parse HEAD)"
        remote_head="$(git rev-parse "${REMOTE}/${BRANCH}")"
        if [ "$local_head" != "$remote_head" ]; then
            set +e
            git pull --ff-only "$REMOTE" "$BRANCH" 2>&1 | tee -a "$RUN_LOG"
            git_pull_exit_code=${PIPESTATUS[0]}
            set -e
            if [ "$git_pull_exit_code" -ne 0 ]; then
                log "Git pull 失败，退出码: ${git_pull_exit_code}"
                exit "$git_pull_exit_code"
            fi
        else
            log "代码已是最新"
        fi
    )
}

is_truthy() {
    local value
    value="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
    [[ "$value" =~ ^(1|true|yes|on)$ ]]
}

set_realtime_runner_timeout() {
    local configured="${AQSP_INTRADAY_OUTER_TIMEOUT_SECONDS:-360}"
    if ! [[ "$configured" =~ ^[1-9][0-9]*$ ]]; then
        log "盘中主链超时配置无效(${configured})，使用 360 秒"
        configured="360"
    elif [ "$configured" -gt 420 ]; then
        log "盘中主链超时配置过长(${configured})，收紧为 420 秒"
        configured="420"
    fi
    export AQSP_RUNNER_TIMEOUT_SECONDS="$configured"
}

set_daily_runner_timeout() {
    local configured="${AQSP_DAILY_OUTER_TIMEOUT_SECONDS:-900}"
    if ! [[ "$configured" =~ ^[1-9][0-9]*$ ]]; then
        log "收盘主链超时配置无效(${configured})，使用 900 秒"
        configured="900"
    elif [ "$configured" -gt 1200 ]; then
        log "收盘主链超时配置过长(${configured})，收紧为 1200 秒"
        configured="1200"
    fi
    export AQSP_RUNNER_TIMEOUT_SECONDS="$configured"
}

set_daily_research_runner_timeout() {
    local configured="${AQSP_DAILY_RESEARCH_OUTER_TIMEOUT_SECONDS:-360}"
    if ! [[ "$configured" =~ ^[1-9][0-9]*$ ]]; then
        log "收盘分块超时配置无效(${configured})，使用 360 秒"
        configured="360"
    elif [ "$configured" -gt 480 ]; then
        log "收盘分块超时配置过长(${configured})，收紧为 480 秒"
        configured="480"
    fi
    export AQSP_RUNNER_TIMEOUT_SECONDS="$configured"
}

set_variant_runner_timeout() {
    local configured="${AQSP_VARIANT_OUTER_TIMEOUT_SECONDS:-300}"
    if ! [[ "$configured" =~ ^[1-9][0-9]*$ ]]; then
        configured="300"
    elif [ "$configured" -gt 360 ]; then
        configured="360"
    fi
    export AQSP_RUNNER_TIMEOUT_SECONDS="$configured"
}

ensure_data_refresh_window() {
    local start_hm="${AQSP_DATA_REFRESH_WINDOW_START_HM:-1530}"
    local end_hm="${AQSP_DATA_REFRESH_WINDOW_END_HM:-1750}"
    local now_hm
    if ! [[ "$start_hm" =~ ^[0-9]{3,4}$ && "$end_hm" =~ ^[0-9]{3,4}$ ]]; then
        log "数据刷新窗口配置无效，拒绝运行 start=${start_hm} end=${end_hm}"
        exit 2
    fi
    now_hm=$((10#$(date +%H%M)))
    if [ "$now_hm" -lt "$start_hm" ] || [ "$now_hm" -gt "$end_hm" ]; then
        log "当前时间 ${now_hm} 不在 data-refresh 允许窗口 ${start_hm}-${end_hm}，跳过原始日线刷新"
        exit 0
    fi
}

ensure_data_refresh_retry_window() {
    # 北京时间 15:45：首轮 15:35 刷新结束后立即给 raw rebuild 留出连续窗口。
    local start_hm="${AQSP_DATA_REFRESH_RETRY_WINDOW_START_HM:-1545}"
    local end_hm="${AQSP_DATA_REFRESH_RETRY_WINDOW_END_HM:-1930}"
    local now_hm
    if ! [[ "$start_hm" =~ ^[0-9]{3,4}$ && "$end_hm" =~ ^[0-9]{3,4}$ ]]; then
        log "延迟数据刷新窗口配置无效，拒绝运行 start=${start_hm} end=${end_hm}"
        exit 2
    fi
    now_hm=$((10#$(date +%H%M)))
    if [ "$now_hm" -lt "$start_hm" ] || [ "$now_hm" -gt "$end_hm" ]; then
        log "当前时间 ${now_hm} 不在 data-refresh-retry 允许窗口 ${start_hm}-${end_hm}，跳过延迟原始日线刷新"
        exit 0
    fi
}

sqlite_price_basis_is_invalid() {
    local db_path="$1"
    PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}" \
        "$AQSP_RUNTIME_PYTHON" - "$db_path" <<'AQSP_PRICE_BASIS_PY'
import sys
from pathlib import Path

from aqsp.data.sqlite_db_source import SqliteDbSource

raise SystemExit(0 if SqliteDbSource(Path(sys.argv[1]), cache=None).price_mode() == "invalid" else 1)
AQSP_PRICE_BASIS_PY
}

run_bounded_raw_refresh() {
    local batches="${1:-${AQSP_DATA_REFRESH_BATCHES:-0}}"
    local db_path="${AQSP_SQLITE_DB_PATH:?AQSP_SQLITE_DB_PATH is required}"
    local state_path="${STATE_DIR}/sqlite-refresh-cursor.json"
    local batch_size="${AQSP_DATA_REFRESH_BATCH_SIZE:-120}"
    local runtime_seconds="${AQSP_DATA_REFRESH_MAX_RUNTIME_SECONDS:-480}"
    local query_timeout="${AQSP_DATA_REFRESH_QUERY_TIMEOUT_SECONDS:-4}"
    local target_day
    target_day="$(${AQSP_RUNTIME_PYTHON} - "${STATE_DIR}/raw-rebuild-cursor.json" <<'AQSP_RAW_REBUILD_TARGET_PY'
import json
import sys
from datetime import date

from aqsp.core.time import latest_completed_trading_day

latest = latest_completed_trading_day()
try:
    state = json.loads(open(sys.argv[1], encoding="utf-8").read())
except (OSError, ValueError, TypeError):
    state = {}
candidate = str(state.get("target_day") or "")
try:
    candidate_day = date.fromisoformat(candidate)
except ValueError:
    candidate_day = None
if not bool(state.get("complete")) and candidate_day is not None and candidate_day <= latest:
    print(candidate_day.isoformat())
else:
    print(latest.isoformat())
AQSP_RAW_REBUILD_TARGET_PY
)"
    if sqlite_price_basis_is_invalid "$db_path"; then
        log "现有 SQLite 价格基准无效，转入旁路 raw 重建；正式库保持只读"
        run_python_script "${PROJECT_ROOT}/scripts/rebuild_raw_sqlite_batches.py" \
            --source-db "$db_path" \
            --candidate-db "${AQSP_RAW_REBUILD_DB_PATH:-${db_path}.rebuild}" \
            --state "${STATE_DIR}/raw-rebuild-cursor.json" \
            --target-date "$target_day" \
            --start-date "${AQSP_RAW_REBUILD_START_DATE:-2024-01-01}" \
            --batch-size "${AQSP_RAW_REBUILD_BATCH_SIZE:-16}" \
            --query-timeout-seconds "$query_timeout" \
            --max-runtime-seconds "$runtime_seconds" \
            --batches "$batches" \
            --min-coverage-ratio "${AQSP_RAW_REBUILD_MIN_COVERAGE_RATIO:-0.98}" \
            --activate-active-db
        return
    fi
    run_python_script "${PROJECT_ROOT}/scripts/refresh_sqlite_batch.py" \
        --db "$db_path" \
        --state "$state_path" \
        --batch-size "$batch_size" \
        --universe-limit "${AQSP_DATA_REFRESH_UNIVERSE_LIMIT:-0}" \
        --min-amount "${AQSP_MIN_AVG_AMOUNT:-50000000}" \
        --query-timeout-seconds "$query_timeout" \
        --max-runtime-seconds "$runtime_seconds" \
        --batches "$batches"
}

refresh_home_snapshot_after_data_refresh() {
    local snapshot_script="${PROJECT_ROOT}/scripts/write_home_snapshot.py"
    [ -f "$snapshot_script" ] || return 0
    if run_python_script "$snapshot_script" \
        --date "$(date +%F)" \
        --task-id "$AQSP_RUN_TASK_ID" \
        --output "${AQSP_HOME_SNAPSHOT_PATH:-data/runtime/home_dashboard_snapshot.json}" \
        --index-output "${AQSP_HOME_SNAPSHOT_INDEX_PATH:-data/runtime/home_dashboard_snapshot_index.json}"; then
        log "原始日线批次完成，首页快照已刷新"
    else
        log "[WARN] 原始日线批次完成，但首页快照刷新失败；保留上一版快照"
    fi
}

gate_optional_heavy_task() {
    local status_path="${STATE_DIR}/resource-gate-${ACTION}.json"
    # 0 lets resource_gate reserve 25% of known host memory, bounded by its safe floor/cap.
    local min_memory_mb="${AQSP_HEAVY_MIN_FREE_MEMORY_MB:-0}"
    local max_load_per_cpu="${AQSP_HEAVY_MAX_LOAD_PER_CPU:-0.70}"
    local exit_code
    mkdir -p "$LOG_DIR" "$STATE_DIR"
    set +e
    PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}" \
        "$AQSP_RUNTIME_PYTHON" "${PROJECT_ROOT}/scripts/resource_gate.py" \
        --task "$ACTION" \
        --min-free-memory-mb "$min_memory_mb" \
        --max-load-per-cpu "$max_load_per_cpu" \
        --blocked-lock "${LOCK_DIR}/server-runtime.lock" \
        --blocked-lock "${LOCK_DIR}/intraday-refresh.lock" \
        --status-path "$status_path" >>"$RUN_LOG" 2>&1
    exit_code=$?
    set -e
    if [ "$exit_code" -eq 75 ]; then
        log "主机资源不足或主链仍在运行，跳过可选重任务 ${ACTION}；下一个错峰窗口重试"
        exit 0
    fi
    if [ "$exit_code" -ne 0 ]; then
        log "[ERROR] 资源门禁异常，拒绝启动可选重任务 ${ACTION}，exit=${exit_code}"
        exit "$exit_code"
    fi
}

release_optional_heavy_slot() {
    rm -f "$HEAVY_SLOT_LOCK_INFO_FILE"
    rmdir "$HEAVY_SLOT_LOCK_FILE" 2>/dev/null || true
}

finish_agent_run() {
    local exit_code="$1" status="completed"
    [ "$AGENT_RUN_ACTIVE" = "true" ] || return 0
    if [ "$exit_code" -ne 0 ]; then
        status="failed"
    fi
    PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}" \
        "$AQSP_RUNTIME_PYTHON" "$AGENT_RUN_REGISTRY_SCRIPT" finish \
        --path "$AGENT_RUNS_PATH" \
        --agent-run-id "$AGENT_RUN_ID" \
        --status "$status" \
        --exit-reason "bt_task_exit_${exit_code}" >>"$RUN_LOG" 2>&1 || \
        log "[ERROR] agent 任务审计完成状态写入失败: ${AGENT_RUN_ID}"
    AGENT_RUN_ACTIVE="false"
}

cleanup_task() {
    local exit_code="$1"
    if [ "$HEAVY_SLOT_ACQUIRED" = "true" ]; then
        release_optional_heavy_slot
    fi
    finish_agent_run "$exit_code"
    return "$exit_code"
}

start_agent_run() {
    local deadline_seconds="$1" registry_exit_code
    [ -f "$AGENT_RUN_REGISTRY_SCRIPT" ] || {
        log "[ERROR] 缺少 agent 任务注册器: ${AGENT_RUN_REGISTRY_SCRIPT}"
        exit 1
    }
    AGENT_RUN_ID="bt-task:${ACTION}:$(date +%Y%m%d%H%M%S):$$"
    set +e
    PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}" \
        "$AQSP_RUNTIME_PYTHON" "$AGENT_RUN_REGISTRY_SCRIPT" start \
        --path "$AGENT_RUNS_PATH" \
        --parent-run-id "scheduler:$(date +%F)" \
        --agent-run-id "$AGENT_RUN_ID" \
        --scope "scheduled:${ACTION}" \
        --pid "$$" \
        --deadline-seconds "$deadline_seconds" >>"$RUN_LOG" 2>&1
    registry_exit_code=$?
    set -e
    if [ "$registry_exit_code" -eq 75 ]; then
        log "agent 任务槽位已占用，本次 ${ACTION} 正常跳过"
        exit 0
    fi
    if [ "$registry_exit_code" -ne 0 ]; then
        log "[ERROR] agent 任务注册失败，拒绝运行 ${ACTION}"
        exit "$registry_exit_code"
    fi
    AGENT_RUN_ACTIVE="true"
}

optional_heavy_slot_is_stale() {
    if [ ! -f "$HEAVY_SLOT_LOCK_INFO_FILE" ]; then
        return 1
    fi
    # shellcheck disable=SC1090
    . "$HEAVY_SLOT_LOCK_INFO_FILE"
    [ -n "${HEAVY_SLOT_PID:-}" ] && ! kill -0 "$HEAVY_SLOT_PID" 2>/dev/null
}

acquire_optional_heavy_slot() {
    mkdir -p "$LOCK_DIR"
    if [ -d "$HEAVY_SLOT_LOCK_FILE" ] && optional_heavy_slot_is_stale; then
        # The previous owner has exited. A missing metadata file is deliberately
        # retained so an in-progress atomic acquisition is never stolen.
        rm -f "$HEAVY_SLOT_LOCK_INFO_FILE"
        rmdir "$HEAVY_SLOT_LOCK_FILE" 2>/dev/null || true
    fi
    if ! mkdir "$HEAVY_SLOT_LOCK_FILE" 2>/dev/null; then
        if [ -f "$HEAVY_SLOT_LOCK_INFO_FILE" ]; then
            # shellcheck disable=SC1090
            . "$HEAVY_SLOT_LOCK_INFO_FILE"
            log "重任务槽位已被占用，本次 ${ACTION} 正常跳过；runner=${HEAVY_SLOT_RUNNER:-unknown} pid=${HEAVY_SLOT_PID:-unknown} started_at=${HEAVY_SLOT_STARTED_AT:-unknown}"
        else
            log "重任务槽位初始化中，本次 ${ACTION} 正常跳过；下一个错峰窗口重试"
        fi
        exit 0
    fi
    {
        printf 'HEAVY_SLOT_PID=%q\n' "$$"
        printf 'HEAVY_SLOT_RUNNER=%q\n' "bt_task:${ACTION}"
        printf 'HEAVY_SLOT_STARTED_AT=%q\n' "$(date '+%Y-%m-%d %H:%M:%S')"
    } >"$HEAVY_SLOT_LOCK_INFO_FILE"
    HEAVY_SLOT_ACQUIRED="true"
}

is_market_trading_day() {
    local python_bin="${AQSP_RUNTIME_PYTHON}"
    local target_date="${AQSP_TRADING_DAY_OVERRIDE_DATE:-}"
    if [ ! -x "$python_bin" ]; then
        log "[ERROR] Python 可执行文件不存在，无法检查交易日: $python_bin"
        exit 1
    fi
    PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}" "$python_bin" - "$target_date" <<'AQSP_CALENDAR_PY'
import sys
from datetime import date

from aqsp.core.time import is_trading_day, today_shanghai

raw = sys.argv[1].strip()
target = date.fromisoformat(raw) if raw else today_shanghai()
raise SystemExit(0 if is_trading_day(target) else 1)
AQSP_CALENDAR_PY
}

skip_non_trading_day() {
    if ! is_market_trading_day; then
        log "今日非交易日，跳过 ${ACTION} 任务"
        exit 0
    fi
}

is_calendar_weekend() {
    local python_bin="${AQSP_RUNTIME_PYTHON}"
    local target_date="${AQSP_TRADING_DAY_OVERRIDE_DATE:-}"
    PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}" "$python_bin" - "$target_date" <<'AQSP_WEEKEND_PY'
import sys
from datetime import date

from aqsp.core.time import today_shanghai

raw = sys.argv[1].strip()
target = date.fromisoformat(raw) if raw else today_shanghai()
raise SystemExit(0 if target.isoweekday() >= 6 else 1)
AQSP_WEEKEND_PY
}

skip_weekday_market_holiday() {
    if is_calendar_weekend; then
        return 0
    fi
    skip_non_trading_day
}

should_bridge_intraday_to_midday() {
    if ! is_truthy "${AQSP_INTRADAY_MIDDAY_BRIDGE:-true}"; then
        return 1
    fi
    local dow now_hm marker_dir marker_file
    dow="$(date +%u)"
    if [ "$dow" -ge 6 ]; then
        return 1
    fi
    if ! is_market_trading_day; then
        return 1
    fi
    now_hm=$((10#$(date +%H%M)))
    if ! { [ "$now_hm" -ge 1135 ] && [ "$now_hm" -le 1230 ]; }; then
        return 1
    fi
    marker_dir="${STATE_DIR}"
    marker_file="${marker_dir}/midday-$(date +%Y-%m-%d).done"
    if [ -f "$marker_file" ]; then
        return 1
    fi
    mkdir -p "$marker_dir"
    export AQSP_MIDDAY_MARKER_FILE="$marker_file"
    return 0
}

ensure_intraday_dispatch_window() {
    if ! is_truthy "${AQSP_INTRADAY_ENFORCE_DISPATCH_WINDOW:-true}"; then
        return 0
    fi
    local now_hm
    now_hm=$((10#$(date +%H%M)))
    if { [ "$now_hm" -ge 935 ] && [ "$now_hm" -le 1130 ]; } || \
       { [ "$now_hm" -ge 1135 ] && [ "$now_hm" -le 1230 ]; } || \
       { [ "$now_hm" -ge 1305 ] && [ "$now_hm" -le 1457 ]; }; then
        return 0
    fi
    log "当前不在盘中或午盘桥接时段，跳过 intraday，不抢占收盘主链锁"
    exit 0
}

run_script() {
    local script_path="$1"
    shift || true
    if [ ! -f "$script_path" ]; then
        log "[ERROR] 脚本不存在: $script_path"
        exit 1
    fi
    log "开始运行: $script_path $*"
    set +e
    /bin/bash "$script_path" "$@" 2>&1 | tee -a "$RUN_LOG"
    local runner_exit_code=${PIPESTATUS[0]}
    set -e
    if [ "$runner_exit_code" -ne 0 ]; then
        log "任务执行失败，退出码: ${runner_exit_code}: ${script_path}"
    fi
    return "$runner_exit_code"
}

run_python_script() {
    local script_path="$1"
    shift || true
    if [ ! -f "$script_path" ]; then
        log "[ERROR] 脚本不存在: $script_path"
        exit 1
    fi
    local python_bin="${AQSP_RUNTIME_PYTHON}"
    if ! aqsp_require_runtime_python "$python_bin"; then
        log "[ERROR] 拒绝使用非 release Python 运行任务: ${python_bin}"
        return 1
    fi
    log "开始运行: ${python_bin} ${script_path} $*"
    set +e
    "$python_bin" "$script_path" "$@" 2>&1 | tee -a "$RUN_LOG"
    local runner_exit_code=${PIPESTATUS[0]}
    set -e
    if [ "$runner_exit_code" -ne 0 ]; then
        log "任务执行失败，退出码: ${runner_exit_code}: ${script_path}"
    fi
    return "$runner_exit_code"
}

run_synced_task_with_result() {
    local result_file="${STATE_DIR}/sync-${ACTION}-$(date +%Y%m%d%H%M%S)-$$.env"
    rm -f "$result_file"
    SYNC_TASK_STATUS="unknown"
    SYNC_TASK_SKIPPED="false"
    export AQSP_SYNC_RESULT_FILE="$result_file"
    local sync_exit_code=0
    run_script "${PROJECT_ROOT}/scripts/server_sync_and_run.sh" || sync_exit_code=$?
    unset AQSP_SYNC_RESULT_FILE
    local status="unknown"
    local result_exit_code=""
    if [ -f "$result_file" ]; then
        # shellcheck disable=SC1090
        . "$result_file"
        result_exit_code="${exit_code:-}"
        rm -f "$result_file"
    fi
    SYNC_TASK_STATUS="$status"
    if [ "$sync_exit_code" -eq 0 ] && [ "$status" = "completed" ]; then
        return 0
    fi
    if [ "$sync_exit_code" -eq 0 ] && [ "$status" = "skipped_lock" ]; then
        SYNC_TASK_SKIPPED="true"
        log "同步任务因主链路互斥正常跳过，不写入完成标记"
        return 0
    fi
    log "同步任务未成功完成 status=${status} exit_code=${result_exit_code:-${sync_exit_code:-1}}"
    if [ "$sync_exit_code" -ne 0 ]; then
        return "$sync_exit_code"
    fi
    return 1
}

if [ "${AQSP_IMMUTABLE_RELEASE:-false}" != "true" ] && [ ! -d "${PROJECT_ROOT}/.git" ]; then
    echo "Git repo not found: ${PROJECT_ROOT}" >&2
    exit 1
fi

export AQSP_PROJECT_ROOT="$PROJECT_ROOT"
export TZ="Asia/Shanghai"
trap 'cleanup_task "$?"' EXIT

case "$ACTION" in
    daily)
        skip_non_trading_day
        # Daily research can allocate substantially more memory than a raw-data
        # refresh. Keep it mutually exclusive and fail closed on the small host.
        AQSP_HEAVY_MIN_FREE_MEMORY_MB="${AQSP_DAILY_MIN_FREE_MEMORY_MB:-700}" \
            AQSP_HEAVY_MAX_LOAD_PER_CPU="${AQSP_DAILY_MAX_LOAD_PER_CPU:-0.50}" \
            gate_optional_heavy_task
        acquire_optional_heavy_slot
        set_daily_runner_timeout
        start_agent_run "${AQSP_AGENT_DEADLINE_SECONDS:-${AQSP_RUNNER_TIMEOUT_SECONDS}}"
        export AQSP_RUN_TASK_ID="daily"
        export AQSP_RUNNER_SCRIPT=scripts/daily_pipeline.sh
        run_script "${PROJECT_ROOT}/scripts/server_sync_and_run.sh"
        ;;
    daily-research)
        skip_non_trading_day
        AQSP_HEAVY_MIN_FREE_MEMORY_MB="${AQSP_DAILY_MIN_FREE_MEMORY_MB:-700}" \
            AQSP_HEAVY_MAX_LOAD_PER_CPU="${AQSP_DAILY_MAX_LOAD_PER_CPU:-0.50}" \
            gate_optional_heavy_task
        acquire_optional_heavy_slot
        set_daily_research_runner_timeout
        start_agent_run "${AQSP_AGENT_DEADLINE_SECONDS:-${AQSP_RUNNER_TIMEOUT_SECONDS}}"
        export AQSP_RUN_TASK_ID="daily_research"
        export AQSP_DAILY_RESEARCH_ONLY="true"
        export AQSP_NOTIFY="false"
        export AQSP_GATE_NOTIFY="false"
        export AQSP_RUNNER_SCRIPT=scripts/daily_pipeline.sh
        run_script "${PROJECT_ROOT}/scripts/server_sync_and_run.sh"
        ;;
    data-refresh)
        skip_non_trading_day
        ensure_data_refresh_window
        # This task only writes a bounded raw-data chunk. It has a lower memory
        # reserve than backtests, but remains mutually exclusive with all heavy work.
        AQSP_HEAVY_MIN_FREE_MEMORY_MB="${AQSP_DATA_REFRESH_MIN_FREE_MEMORY_MB:-640}" \
            AQSP_HEAVY_MAX_LOAD_PER_CPU="${AQSP_DATA_REFRESH_MAX_LOAD_PER_CPU:-0.50}" \
            gate_optional_heavy_task
        acquire_optional_heavy_slot
        start_agent_run "${AQSP_AGENT_DEADLINE_SECONDS:-480}"
        export AQSP_RUN_TASK_ID="data_refresh"
        export AQSP_NOTIFY="false"
        export AQSP_GATE_NOTIFY="false"
        sync_code_only
        run_bounded_raw_refresh "${AQSP_DATA_REFRESH_BATCHES:-0}"
        refresh_home_snapshot_after_data_refresh
        ;;
    data-refresh-retry)
        skip_non_trading_day
        ensure_data_refresh_retry_window
        AQSP_HEAVY_MIN_FREE_MEMORY_MB="${AQSP_DATA_REFRESH_MIN_FREE_MEMORY_MB:-640}" \
            AQSP_HEAVY_MAX_LOAD_PER_CPU="${AQSP_DATA_REFRESH_MAX_LOAD_PER_CPU:-0.50}" \
            gate_optional_heavy_task
        acquire_optional_heavy_slot
        start_agent_run "${AQSP_AGENT_DEADLINE_SECONDS:-480}"
        export AQSP_RUN_TASK_ID="data_refresh_retry"
        export AQSP_NOTIFY="false"
        export AQSP_GATE_NOTIFY="false"
        sync_code_only
        run_bounded_raw_refresh "${AQSP_DATA_REFRESH_RETRY_BATCHES:-0}"
        refresh_home_snapshot_after_data_refresh
        ;;
    intraday)
        skip_non_trading_day
        ensure_intraday_dispatch_window
        set_realtime_runner_timeout
        export AQSP_RUN_TASK_ID="intraday"
        export AQSP_NOTIFY="false"
        export AQSP_GATE_NOTIFY="false"
        export AQSP_INTRADAY_NOTIFY="${AQSP_INTRADAY_NOTIFY:-false}"
        if should_bridge_intraday_to_midday; then
            export AQSP_RUN_TASK_ID="midday"
            export AQSP_NOTIFY="false"
            export AQSP_GATE_NOTIFY="false"
            export AQSP_INTRADAY_NOTIFY="${AQSP_INTRADAY_NOTIFY:-false}"
            export AQSP_RUNNER_SCRIPT=scripts/midday_refresh.sh
            if run_synced_task_with_result; then
                if [ "$SYNC_TASK_SKIPPED" = "true" ]; then
                    log "午盘桥接因已有主链路运行而跳过，不写完成标记；后续定时仍可重试"
                    exit 0
                else
                    touch "$AQSP_MIDDAY_MARKER_FILE"
                    log "午盘桥接已完成，今日不再重复触发"
                    exit 0
                fi
            else
                # A failed bridge must not be retried by every 10-minute
                # intraday tick. The dedicated 12:05 midday task remains the
                # retry path and does not consult this bridge marker.
                touch "$AQSP_MIDDAY_MARKER_FILE"
                log "午盘桥接失败，今日不再重复桥接；12:05 午盘任务仍会独立重试"
                exit 1
            fi
        fi
        export AQSP_RUNNER_SCRIPT=scripts/intraday_refresh.sh
        run_synced_task_with_result
        ;;
    midday)
        skip_non_trading_day
        set_realtime_runner_timeout
        export AQSP_RUN_TASK_ID="midday"
        export AQSP_NOTIFY="false"
        export AQSP_GATE_NOTIFY="false"
        export AQSP_INTRADAY_NOTIFY="${AQSP_INTRADAY_NOTIFY:-false}"
        export AQSP_RUNNER_SCRIPT=scripts/midday_refresh.sh
        if run_synced_task_with_result; then
            if [ "$SYNC_TASK_SKIPPED" = "true" ]; then
                log "午盘任务因已有主链路运行而跳过，不写完成标记；后续定时仍可重试"
            else
                marker_file="${AQSP_MIDDAY_MARKER_FILE:-${STATE_DIR}/midday-$(date +%Y-%m-%d).done}"
                mkdir -p "$(dirname "$marker_file")"
                touch "$marker_file"
            fi
        else
            log "午盘任务未真实执行，不写完成标记；后续定时仍可重试"
            exit 1
        fi
        ;;
    coldstart)
        skip_non_trading_day
        gate_optional_heavy_task
        acquire_optional_heavy_slot
        start_agent_run "${AQSP_AGENT_DEADLINE_SECONDS:-900}"
        export AQSP_RUN_TASK_ID="coldstart"
        export AQSP_NOTIFY="false"
        export AQSP_GATE_NOTIFY="false"
        sync_code_only
        run_script "${PROJECT_ROOT}/scripts/coldstart_daily.sh"
        ;;
    variant-refresh)
        skip_non_trading_day
        # Bounded to 240 symbols and 80-row SQLite chunks. The generic 1GB
        # reserve permanently skips this task on the 1.6GB production host.
        AQSP_HEAVY_MIN_FREE_MEMORY_MB="${AQSP_VARIANT_MIN_FREE_MEMORY_MB:-700}" \
            AQSP_HEAVY_MAX_LOAD_PER_CPU="${AQSP_VARIANT_MAX_LOAD_PER_CPU:-0.50}" \
            gate_optional_heavy_task
        acquire_optional_heavy_slot
        start_agent_run "${AQSP_AGENT_DEADLINE_SECONDS:-300}"
        set_variant_runner_timeout
        export AQSP_VARIANT_MAX_RUNTIME_SECONDS="${AQSP_VARIANT_MAX_RUNTIME_SECONDS:-240}"
        export AQSP_RUN_TASK_ID="variant_refresh"
        export AQSP_NOTIFY="false"
        export AQSP_GATE_NOTIFY="false"
        export AQSP_RUNNER_SCRIPT=scripts/variant_refresh.sh
        run_synced_task_with_result
        ;;
    walkforward-gate)
        gate_optional_heavy_task
        acquire_optional_heavy_slot
        start_agent_run "${AQSP_AGENT_DEADLINE_SECONDS:-900}"
        export AQSP_RUN_TASK_ID="walkforward_gate"
        export AQSP_NOTIFY="false"
        export AQSP_GATE_NOTIFY="${AQSP_WALKFORWARD_GATE_NOTIFY:-false}"
        sync_code_only
        run_python_script "${PROJECT_ROOT}/scripts/run_production_walkforward_gate.py" "${@:2}"
        ;;
    monitor)
        skip_weekday_market_holiday
        export AQSP_RUN_TASK_ID="monitor"
        export AQSP_NOTIFY="false"
        export AQSP_GATE_NOTIFY="false"
        sync_code_only
        run_script "${PROJECT_ROOT}/scripts/server_monitor.sh"
        ;;
    news)
        skip_weekday_market_holiday
        export AQSP_RUN_TASK_ID="news"
        export AQSP_NOTIFY="false"
        export AQSP_GATE_NOTIFY="false"
        run_script "${PROJECT_ROOT}/scripts/news_catalysts.sh"
        ;;
    status)
        export AQSP_NOTIFY="false"
        export AQSP_GATE_NOTIFY="false"
        run_script "${PROJECT_ROOT}/scripts/server_status.sh"
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

find "$LOG_DIR" -name "bt-*.log" -mtime +30 -delete 2>/dev/null || true
