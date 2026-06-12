#!/usr/bin/env bash
# 简单服务器模式：
# 1. 从 GitHub 快进同步代码
# 2. 保留服务器本地 .env / 数据库 / 产物
# 3. 运行显式指定的任务脚本

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
BRANCH="${AQSP_GIT_BRANCH:-main}"
REMOTE="${AQSP_GIT_REMOTE:-origin}"
RUNNER_SCRIPT="${AQSP_RUNNER_SCRIPT:-}"
LOG_DIR="${PROJECT_ROOT}/logs/deploy"
RUN_LOG="${LOG_DIR}/sync-$(date +%Y-%m-%d).log"
LOCK_DIR="${PROJECT_ROOT}/.locks"
LOCK_FILE="${LOCK_DIR}/server-runtime.lock"
LOCK_INFO_FILE="${LOCK_FILE}/meta.env"
LOCK_STALE_MINUTES="${AQSP_LOCK_STALE_MINUTES:-360}"
RUNNER_TIMEOUT_SECONDS="${AQSP_RUNNER_TIMEOUT_SECONDS:-0}"

log() {
    mkdir -p "$LOG_DIR"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RUN_LOG"
}

lock_age_minutes() {
    local path="$1"
    local now_epoch mtime
    now_epoch="$(date +%s)"
    mtime="$(stat -c %Y "$path" 2>/dev/null || stat -f %m "$path")"
    echo $(( (now_epoch - mtime) / 60 ))
}

load_lock_info() {
    if [ -f "$LOCK_INFO_FILE" ]; then
        # shellcheck disable=SC1090
        . "$LOCK_INFO_FILE"
    fi
}

lock_is_stale() {
    if [ ! -d "$LOCK_FILE" ]; then
        return 1
    fi
    local age_minutes pid=""
    age_minutes="$(lock_age_minutes "$LOCK_FILE")"
    load_lock_info
    pid="${LOCK_PID:-}"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 1
    fi
    [ "$age_minutes" -ge "$LOCK_STALE_MINUTES" ]
}

if [ ! -d "${PROJECT_ROOT}/.git" ]; then
    echo "Git repo not found: ${PROJECT_ROOT}" >&2
    exit 1
fi

if [ -z "${RUNNER_SCRIPT}" ]; then
    echo "AQSP_RUNNER_SCRIPT is required; use scripts/bt_task.sh daily|intraday" >&2
    exit 2
fi

if [[ "${RUNNER_SCRIPT}" = /* ]]; then
    RUNNER_PATH="${RUNNER_SCRIPT}"
else
    RUNNER_PATH="${PROJECT_ROOT}/${RUNNER_SCRIPT}"
fi

cd "$PROJECT_ROOT"

mkdir -p "$LOG_DIR" "$LOCK_DIR"
if [ -d "$LOCK_FILE" ] && lock_is_stale; then
    stale_age="$(lock_age_minutes "$LOCK_FILE")"
    load_lock_info
    log "检测到陈旧主锁，自动回收 runner=${LOCK_RUNNER:-unknown} pid=${LOCK_PID:-unknown} age=${stale_age}min started_at=${LOCK_STARTED_AT:-unknown}"
    rm -rf -- "$LOCK_FILE"
fi
if ! mkdir "$LOCK_FILE" 2>/dev/null; then
    if [ -f "$LOCK_INFO_FILE" ]; then
        load_lock_info
        age_minutes="$(lock_age_minutes "$LOCK_FILE")"
        log "主链路仍在运行，本次任务正常跳过；这是互斥保护，不是失败 runner=${LOCK_RUNNER:-unknown} pid=${LOCK_PID:-unknown} started_at=${LOCK_STARTED_AT:-unknown} age=${age_minutes}min"
    else
        log "主链路仍在运行，本次任务正常跳过；这是互斥保护，不是失败"
    fi
    exit 0
fi
cat >"$LOCK_INFO_FILE" <<EOF
LOCK_PID=$$
LOCK_RUNNER=${RUNNER_SCRIPT}
LOCK_STARTED_AT=$(date '+%Y-%m-%d %H:%M:%S')
EOF
trap 'rm -f "$LOCK_INFO_FILE"; rmdir "$LOCK_FILE"' EXIT

log "开始同步代码: ${REMOTE}/${BRANCH}"

git update-index --refresh >/dev/null 2>&1 || true

DIRTY_TRACKED="$(git status --porcelain --untracked-files=no)"
if [ -n "$DIRTY_TRACKED" ]; then
    log "检测到受 Git 管理的本地修改，拒绝自动覆盖："
    printf '%s\n' "$DIRTY_TRACKED" | tee -a "$RUN_LOG"
    exit 1
fi

git fetch "$REMOTE" "$BRANCH" 2>&1 | tee -a "$RUN_LOG"
LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_HEAD="$(git rev-parse "${REMOTE}/${BRANCH}")"

if [ "$LOCAL_HEAD" != "$REMOTE_HEAD" ]; then
    log "发现新提交，执行快进更新"
    git pull --ff-only "$REMOTE" "$BRANCH" 2>&1 | tee -a "$RUN_LOG"
else
    log "代码已是最新，无需更新"
fi

if [ ! -f "${RUNNER_PATH}" ]; then
    log "运行脚本不存在: ${RUNNER_PATH}"
    exit 1
fi

log "开始运行任务: ${RUNNER_PATH}"
if [ "${RUNNER_TIMEOUT_SECONDS}" -gt 0 ] && command -v timeout >/dev/null 2>&1; then
    log "启用主链路超时保护: ${RUNNER_TIMEOUT_SECONDS}s"
    timeout --foreground "${RUNNER_TIMEOUT_SECONDS}" bash "${RUNNER_PATH}" 2>&1 | tee -a "$RUN_LOG"
    RUNNER_EXIT_CODE=${PIPESTATUS[0]}
elif [ "${RUNNER_TIMEOUT_SECONDS}" -gt 0 ]; then
    log "系统缺少 timeout 命令，跳过主链路超时保护"
    bash "${RUNNER_PATH}" 2>&1 | tee -a "$RUN_LOG"
    RUNNER_EXIT_CODE=${PIPESTATUS[0]}
else
    bash "${RUNNER_PATH}" 2>&1 | tee -a "$RUN_LOG"
    RUNNER_EXIT_CODE=${PIPESTATUS[0]}
fi

if [ "${RUNNER_EXIT_CODE}" -eq 124 ]; then
    log "主链路执行超时，被保护性终止: ${RUNNER_TIMEOUT_SECONDS}s"
    exit "${RUNNER_EXIT_CODE}"
fi
if [ "${RUNNER_EXIT_CODE}" -ne 0 ]; then
    log "主链路执行失败，退出码: ${RUNNER_EXIT_CODE}"
    exit "${RUNNER_EXIT_CODE}"
fi
log "同步与跑批完成"
