#!/usr/bin/env bash
# 安全发版入口：先切到目标 commit，再用该 commit 自带的发版脚本执行。
#
# 为什么需要这一层：
#   deploy_immutable_release.sh 的 prod 前端构建防护（assert_frontend_build_policy，
#   位于主流程入口）只有在**脚本本身是目标版本**时才存在。2026-11-06 的事故就是
#   只 git fetch 却没有 checkout，于是用旧脚本（无防护）在 2G 机器上跑了
#   npm ci + tsc + vite build，把 MemAvailable 榨干，sshd 无法 fork，SSH 失联 25 分钟。
#
# 本脚本强制三件事：
#   1. 目标 commit 必须先在本地存在（fetch 后 cat-file 校验）
#   2. 工作区 checkout 到该 commit，确保 scripts/ 与要发布的代码版本一致
#   3. prod 目标一律 --skip-frontend-build（前端产物复用现有 release）
#
# 用法：
#   bash scripts/deploy/deploy_safe.sh <full-or-short-sha> [prod|runner]
set -euo pipefail

COMMIT="${1:?usage: deploy_safe.sh <commit> [prod|runner]}"
TARGET="${2:-${AQSP_DEPLOY_TARGET:-prod}}"
REPO_ROOT="${AQSP_REPO_ROOT:-/opt/aqsp}"
REMOTE="${AQSP_GIT_REMOTE:-origin}"
BRANCH="${AQSP_GIT_BRANCH:-main}"

log() { printf '[deploy_safe %s] %s\n' "$(date '+%F %T')" "$*"; }
die() { printf '[deploy_safe] ERROR: %s\n' "$*" >&2; exit 1; }

cd "$REPO_ROOT" || die "repo root not found: $REPO_ROOT"

log "fetch $REMOTE/$BRANCH"
git fetch "$REMOTE" "refs/heads/${BRANCH}:refs/remotes/${REMOTE}/${BRANCH}" 2>/dev/null || git fetch "$REMOTE" "$BRANCH"
git cat-file -e "${COMMIT}^{commit}" 2>/dev/null || die "commit not found locally: $COMMIT"

FULL_SHA="$(git rev-parse "${COMMIT}^{commit}")"
log "target commit $FULL_SHA"

# 关键一步：让工作区（尤其 scripts/）与目标 commit 一致。
# 未跟踪文件不会被删除，prod 上的 .state/ 与 backup_* 目录安全。
log "checkout $FULL_SHA"
git checkout -q "$FULL_SHA" || die "checkout failed (dirty tree?)"

DEPLOY_SH="$REPO_ROOT/scripts/deploy/deploy_immutable_release.sh"
[ -f "$DEPLOY_SH" ] || die "deploy script missing after checkout: $DEPLOY_SH"

# 断言：切完之后脚本里必须带 prod 前端构建防护，否则说明目标版本过旧，拒绝执行。
if ! grep -q "assert_frontend_build_policy" "$DEPLOY_SH"; then
    die "target $FULL_SHA lacks assert_frontend_build_policy; refusing to deploy (would allow frontend build on small ECS)"
fi

ARGS=()
if [ "$TARGET" = "prod" ]; then
    # 小 ECS 只跑后端；前端产物由 build host 预备，这里复用 current release。
    ARGS+=(--skip-frontend-build)
    log "prod target: forcing --skip-frontend-build"
fi

log "exec deploy script (target=$TARGET args=${ARGS[*]:-none})"
exec env AQSP_DEPLOY_TARGET="$TARGET" bash "$DEPLOY_SH" "${ARGS[@]}"
