#!/usr/bin/env bash
# 宝塔面板计划任务统一入口。
# 用法:
#   /bin/bash /opt/aqsp/scripts/bt_task.sh daily
#   /bin/bash /opt/aqsp/scripts/bt_task.sh intraday
#   /bin/bash /opt/aqsp/scripts/bt_task.sh midday
#   /bin/bash /opt/aqsp/scripts/bt_task.sh coldstart
#   /bin/bash /opt/aqsp/scripts/bt_task.sh walkforward-gate
#   /bin/bash /opt/aqsp/scripts/bt_task.sh monitor
#   /bin/bash /opt/aqsp/scripts/bt_task.sh news
#   /bin/bash /opt/aqsp/scripts/bt_task.sh variants
#   /bin/bash /opt/aqsp/scripts/bt_task.sh status

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
INITIAL_PROJECT_ROOT="$PROJECT_ROOT"
if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "${PROJECT_ROOT}/.env"
    set +a
    # .env may configure the runtime, but must not redirect this checkout.
    PROJECT_ROOT="$INITIAL_PROJECT_ROOT"
fi
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
LOG_DIR="${PROJECT_ROOT}/logs/bt"
RUN_LOG="${LOG_DIR}/bt-${ACTION}-$(date +%Y-%m-%d).log"
BRANCH="${AQSP_GIT_BRANCH:-main}"
REMOTE="${AQSP_GIT_REMOTE:-origin}"
LOCK_DIR="${PROJECT_ROOT}/.locks"
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
Usage: bt_task.sh <daily|intraday|midday|coldstart|variants|walkforward-gate|monitor|news|status>

BT panel examples:
  /bin/bash /opt/aqsp/scripts/bt_task.sh intraday
  /bin/bash /opt/aqsp/scripts/bt_task.sh daily
  /bin/bash /opt/aqsp/scripts/bt_task.sh midday
  /bin/bash /opt/aqsp/scripts/bt_task.sh coldstart
  /bin/bash /opt/aqsp/scripts/bt_task.sh walkforward-gate
  /bin/bash /opt/aqsp/scripts/bt_task.sh monitor
  /bin/bash /opt/aqsp/scripts/bt_task.sh news
  /bin/bash /opt/aqsp/scripts/bt_task.sh variants

Recommended BT schedule (Asia/Shanghai):
  news      08:35 Mon-Fri trading days only; 09:05 Sat/Sun
  intraday  every 10 min; script gates 09:35-11:30 / 13:05-14:57, Mon-Fri
  midday    12:05 Mon-Fri
  daily     18:00 Mon-Fri
  coldstart 19:40 Mon-Fri
  variants   21:30 Mon-Fri, after coldstart; isolated paper research only
  walkforward-gate manual/controlled after coldstart reaches 30/30
  monitor   every 15 min
  status    manual only

Notes:
  "正常跳过/互斥保护" means another AQSP task is still running or the market
  window is closed. It is not a failed run.

Optional env:
  AQSP_RUNNER_TIMEOUT_SECONDS=5400   # 主链路最长 90 分钟
  AQSP_VARIANT_TIMEOUT_SECONDS=1800  # 变体研究最长 30 分钟
  AQSP_MONITOR_TIMEOUT_SECONDS=600   # 监控最长 10 分钟
  AQSP_LOCK_STALE_MINUTES=360        # 无活跃 PID 时，6 小时后视为陈旧锁
EOF
}

if [ -z "$ACTION" ]; then
    usage >&2
    exit 2
fi

sync_code_only() {
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
            RUNTIME_OVERLAY_MANIFEST_PATH="${AQSP_RUNTIME_OVERLAY_MANIFEST:-${PROJECT_ROOT}/.state/runtime-sync-overlay.json}" \
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
    marker_dir="${PROJECT_ROOT}/.state"
    marker_file="${marker_dir}/midday-$(date +%Y-%m-%d).done"
    if [ -f "$marker_file" ]; then
        return 1
    fi
    mkdir -p "$marker_dir"
    export AQSP_MIDDAY_MARKER_FILE="$marker_file"
    return 0
}

wait_for_coldstart_completion() {
    local today wait_seconds interval elapsed log_path
    today="${AQSP_TRADING_DAY_OVERRIDE_DATE:-$(date +%Y-%m-%d)}"
    wait_seconds="${AQSP_COLDSTART_WAIT_SECONDS:-600}"
    interval="${AQSP_COLDSTART_WAIT_INTERVAL_SECONDS:-5}"
    if [[ ! "$wait_seconds" =~ ^[0-9]+$ ]] || [[ ! "$interval" =~ ^[1-9][0-9]*$ ]]; then
        log "[ERROR] 冷启动等待配置无效: wait=${wait_seconds}s interval=${interval}s"
        return 2
    fi
    log_path="${AQSP_COLDSTART_LOG_DIR:-${PROJECT_ROOT}/logs/coldstart}/coldstart-${today}.log"
    elapsed=0
    while :; do
        if [ -f "$log_path" ] && grep -q "冷启动日跑完成" "$log_path"; then
            log "冷启动已完成，允许刷新 paper_realtime 变体: ${log_path}"
            return 0
        fi
        if [ "$elapsed" -ge "$wait_seconds" ]; then
            log "[ERROR] 冷启动在 ${wait_seconds}s 内未完成，拒绝运行 variants，避免复用过期产物"
            return 1
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
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
    if [ "$ACTION" = "variants" ] && command -v timeout >/dev/null 2>&1; then
        local variant_timeout="${AQSP_VARIANT_TIMEOUT_SECONDS:-1800}"
        local kill_after="${AQSP_RUNNER_KILL_AFTER_SECONDS:-15}"
        if [[ ! "$variant_timeout" =~ ^[1-9][0-9]*$ ]] || [[ ! "$kill_after" =~ ^[1-9][0-9]*$ ]]; then
            log "[ERROR] variants 超时配置必须是正整数: timeout=${variant_timeout}s kill_after=${kill_after}s"
            return 2
        fi
        log "启用 variants 超时保护: ${variant_timeout}s，终止宽限 ${kill_after}s"
        timeout --foreground --signal=TERM --kill-after="${kill_after}s" \
            "$variant_timeout" /bin/bash "$script_path" "$@" 2>&1 | tee -a "$RUN_LOG"
    else
        /bin/bash "$script_path" "$@" 2>&1 | tee -a "$RUN_LOG"
    fi
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
    local result_file="${PROJECT_ROOT}/.state/sync-${ACTION}-$(date +%Y%m%d%H%M%S)-$$.env"
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
export TZ="${TZ:-Asia/Shanghai}"

case "$ACTION" in
    daily)
        skip_non_trading_day
        export AQSP_RUN_TASK_ID="daily"
        export AQSP_RUNNER_SCRIPT=scripts/daily_pipeline.sh
        run_script "${PROJECT_ROOT}/scripts/server_sync_and_run.sh"
        ;;
    intraday)
        skip_non_trading_day
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
        export AQSP_RUN_TASK_ID="midday"
        export AQSP_NOTIFY="false"
        export AQSP_GATE_NOTIFY="false"
        export AQSP_INTRADAY_NOTIFY="${AQSP_INTRADAY_NOTIFY:-false}"
        export AQSP_RUNNER_SCRIPT=scripts/midday_refresh.sh
        if run_synced_task_with_result; then
            if [ "$SYNC_TASK_SKIPPED" = "true" ]; then
                log "午盘任务因已有主链路运行而跳过，不写完成标记；后续定时仍可重试"
            else
                marker_file="${AQSP_MIDDAY_MARKER_FILE:-${PROJECT_ROOT}/.state/midday-$(date +%Y-%m-%d).done}"
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
        export AQSP_RUN_TASK_ID="coldstart"
        export AQSP_NOTIFY="false"
        export AQSP_GATE_NOTIFY="false"
        sync_code_only
        run_script "${PROJECT_ROOT}/scripts/coldstart_daily.sh"
        ;;
    variants)
        skip_non_trading_day
        wait_for_coldstart_completion
        export AQSP_RUN_TASK_ID="variants"
        export AQSP_NOTIFY="false"
        export AQSP_GATE_NOTIFY="false"
        if [ "${AQSP_IMMUTABLE_RELEASE:-false}" = "true" ]; then
            run_script "${PROJECT_ROOT}/scripts/variant_refresh.sh"
        else
            sync_code_only
            run_script "${PROJECT_ROOT}/scripts/variant_refresh.sh"
        fi
        ;;
    walkforward-gate)
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
