#!/usr/bin/env bash
# 简单服务器模式：
# 1. 从 GitHub 快进同步代码
# 2. 保留服务器本地 .env / 数据库 / 产物
# 3. 运行指定任务脚本（默认每日跑批）

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
BRANCH="${AQSP_GIT_BRANCH:-main}"
REMOTE="${AQSP_GIT_REMOTE:-origin}"
RUNNER_SCRIPT="${AQSP_RUNNER_SCRIPT:-scripts/daily_pipeline.sh}"
LOG_DIR="${PROJECT_ROOT}/logs/deploy"
RUN_LOG="${LOG_DIR}/sync-$(date +%Y-%m-%d).log"

log() {
    mkdir -p "$LOG_DIR"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RUN_LOG"
}

if [ ! -d "${PROJECT_ROOT}/.git" ]; then
    echo "Git repo not found: ${PROJECT_ROOT}" >&2
    exit 1
fi

if [[ "${RUNNER_SCRIPT}" = /* ]]; then
    RUNNER_PATH="${RUNNER_SCRIPT}"
else
    RUNNER_PATH="${PROJECT_ROOT}/${RUNNER_SCRIPT}"
fi

cd "$PROJECT_ROOT"

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
bash "${RUNNER_PATH}" 2>&1 | tee -a "$RUN_LOG"
log "同步与跑批完成"
