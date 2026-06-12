#!/usr/bin/env bash
# 宝塔面板计划任务统一入口。
# 用法:
#   /bin/bash /opt/aqsp/scripts/bt_task.sh daily
#   /bin/bash /opt/aqsp/scripts/bt_task.sh intraday
#   /bin/bash /opt/aqsp/scripts/bt_task.sh midday
#   /bin/bash /opt/aqsp/scripts/bt_task.sh coldstart
#   /bin/bash /opt/aqsp/scripts/bt_task.sh monitor
#   /bin/bash /opt/aqsp/scripts/bt_task.sh news
#   /bin/bash /opt/aqsp/scripts/bt_task.sh status

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
ACTION="${1:-}"
LOG_DIR="${PROJECT_ROOT}/logs/bt"
RUN_LOG="${LOG_DIR}/bt-${ACTION}-$(date +%Y-%m-%d).log"
BRANCH="${AQSP_GIT_BRANCH:-main}"
REMOTE="${AQSP_GIT_REMOTE:-origin}"

log() {
    mkdir -p "$LOG_DIR"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RUN_LOG"
}

usage() {
    cat <<'EOF'
Usage: bt_task.sh <daily|intraday|midday|coldstart|monitor|news|status>

BT panel examples:
  /bin/bash /opt/aqsp/scripts/bt_task.sh intraday
  /bin/bash /opt/aqsp/scripts/bt_task.sh daily
  /bin/bash /opt/aqsp/scripts/bt_task.sh midday
  /bin/bash /opt/aqsp/scripts/bt_task.sh coldstart
  /bin/bash /opt/aqsp/scripts/bt_task.sh monitor
  /bin/bash /opt/aqsp/scripts/bt_task.sh news

Recommended BT schedule (Asia/Shanghai):
  news      08:35 Mon-Fri; 09:05 Sat/Sun
  intraday  every 10 min; script gates 09:35-11:30 / 13:05-14:57, Mon-Fri
  midday    12:05 Mon-Fri
  daily     18:00 Mon-Fri
  coldstart 19:40 Mon-Fri
  monitor   every 15 min
  status    manual only

Notes:
  "正常跳过/互斥保护" means another AQSP task is still running or the market
  window is closed. It is not a failed run.

Optional env:
  AQSP_RUNNER_TIMEOUT_SECONDS=5400   # 主链路最长 90 分钟
  AQSP_MONITOR_TIMEOUT_SECONDS=600   # 监控最长 10 分钟
  AQSP_LOCK_STALE_MINUTES=360        # 无活跃 PID 时，6 小时后视为陈旧锁
EOF
}

if [ -z "$ACTION" ]; then
    usage >&2
    exit 2
fi

sync_code_only() {
    cd "$PROJECT_ROOT"
    log "开始同步代码: ${REMOTE}/${BRANCH}"

    git update-index --refresh >/dev/null 2>&1 || true
    local dirty_tracked
    dirty_tracked="$(git status --porcelain --untracked-files=no)"
    if [ -n "$dirty_tracked" ]; then
        log "检测到受 Git 管理的本地修改，拒绝自动覆盖："
        printf '%s\n' "$dirty_tracked" | tee -a "$RUN_LOG"
        exit 1
    fi

    git fetch "$REMOTE" "$BRANCH" 2>&1 | tee -a "$RUN_LOG"
    local local_head remote_head
    local_head="$(git rev-parse HEAD)"
    remote_head="$(git rev-parse "${REMOTE}/${BRANCH}")"
    if [ "$local_head" != "$remote_head" ]; then
        git pull --ff-only "$REMOTE" "$BRANCH" 2>&1 | tee -a "$RUN_LOG"
    else
        log "代码已是最新"
    fi
}

is_truthy() {
    [[ "${1,,}" =~ ^(1|true|yes|on)$ ]]
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

run_script() {
    local script_path="$1"
    shift || true
    if [ ! -f "$script_path" ]; then
        log "[ERROR] 脚本不存在: $script_path"
        exit 1
    fi
    log "开始运行: $script_path $*"
    /bin/bash "$script_path" "$@" 2>&1 | tee -a "$RUN_LOG"
}

run_synced_task_with_result() {
    local result_file="${PROJECT_ROOT}/.state/sync-${ACTION}-$(date +%Y%m%d%H%M%S)-$$.env"
    rm -f "$result_file"
    export AQSP_SYNC_RESULT_FILE="$result_file"
    run_script "${PROJECT_ROOT}/scripts/server_sync_and_run.sh"
    unset AQSP_SYNC_RESULT_FILE
    if [ -f "$result_file" ]; then
        # shellcheck disable=SC1090
        . "$result_file"
        rm -f "$result_file"
    else
        status="unknown"
    fi
    [ "${status:-unknown}" = "completed" ]
}

if [ ! -d "${PROJECT_ROOT}/.git" ]; then
    echo "Git repo not found: ${PROJECT_ROOT}" >&2
    exit 1
fi

export AQSP_PROJECT_ROOT="$PROJECT_ROOT"
export TZ="${TZ:-Asia/Shanghai}"

case "$ACTION" in
    daily)
        export AQSP_RUNNER_SCRIPT=scripts/daily_pipeline.sh
        run_script "${PROJECT_ROOT}/scripts/server_sync_and_run.sh"
        ;;
    intraday)
        if should_bridge_intraday_to_midday; then
            export AQSP_RUNNER_SCRIPT=scripts/midday_refresh.sh
            if run_synced_task_with_result; then
                touch "$AQSP_MIDDAY_MARKER_FILE"
                log "午盘桥接已完成，今日不再重复触发"
            else
                log "午盘桥接未真实执行，不写完成标记；后续定时仍可重试"
            fi
            exit 0
        fi
        export AQSP_RUNNER_SCRIPT=scripts/intraday_refresh.sh
        run_synced_task_with_result || true
        ;;
    midday)
        export AQSP_RUNNER_SCRIPT=scripts/midday_refresh.sh
        if run_synced_task_with_result; then
            marker_file="${AQSP_MIDDAY_MARKER_FILE:-${PROJECT_ROOT}/.state/midday-$(date +%Y-%m-%d).done}"
            mkdir -p "$(dirname "$marker_file")"
            touch "$marker_file"
        else
            log "午盘任务未真实执行，不写完成标记；后续定时仍可重试"
        fi
        ;;
    coldstart)
        sync_code_only
        run_script "${PROJECT_ROOT}/scripts/coldstart_daily.sh"
        ;;
    monitor)
        run_script "${PROJECT_ROOT}/scripts/server_monitor.sh"
        ;;
    news)
        run_script "${PROJECT_ROOT}/scripts/news_catalysts.sh"
        ;;
    status)
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
