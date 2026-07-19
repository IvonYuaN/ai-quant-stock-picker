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
# This lock is also used by sync_runtime_files_to_server.py; keep the path shared.
LOCK_FILE="${LOCK_DIR}/server-runtime.lock"
LOCK_INFO_FILE="${LOCK_FILE}/meta.env"
GIT_SYNC_LOCK_FILE="${LOCK_DIR}/server-git-sync.lock"
GIT_SYNC_LOCK_INFO_FILE="${GIT_SYNC_LOCK_FILE}/meta.env"
GIT_SYNC_WAIT_SECONDS="${AQSP_GIT_SYNC_WAIT_SECONDS:-180}"
GIT_LOCK_STALE_MINUTES="${AQSP_GIT_LOCK_STALE_MINUTES:-30}"
LOCK_STALE_MINUTES="${AQSP_LOCK_STALE_MINUTES:-360}"
RUNNER_TIMEOUT_SECONDS="${AQSP_RUNNER_TIMEOUT_SECONDS:-0}"
RUN_RESULT_FILE="${AQSP_SYNC_RESULT_FILE:-}"
STATE_DIR="${PROJECT_ROOT}/.state"
DIRTY_STATE_FILE="${STATE_DIR}/server-sync-dirty.env"
RUNTIME_OVERLAY_MANIFEST="${AQSP_RUNTIME_OVERLAY_MANIFEST:-${STATE_DIR}/runtime-sync-overlay.json}"
IMMUTABLE_RELEASE="${AQSP_IMMUTABLE_RELEASE:-false}"

log() {
    mkdir -p "$LOG_DIR"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RUN_LOG"
}

write_result() {
    local status="$1"
    local exit_code="${2:-}"
    if [ -n "$RUN_RESULT_FILE" ]; then
        mkdir -p "$(dirname "$RUN_RESULT_FILE")"
        {
            printf 'status=%s\n' "$status"
            printf 'runner=%s\n' "$RUNNER_SCRIPT"
            if [ -n "$exit_code" ]; then
                printf 'exit_code=%s\n' "$exit_code"
            fi
        } >"$RUN_RESULT_FILE"
    fi
}

dirty_state_hash() {
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%s' "$1" | sha256sum | awk '{print $1}'
        return 0
    fi
    if command -v shasum >/dev/null 2>&1; then
        printf '%s' "$1" | shasum -a 256 | awk '{print $1}'
        return 0
    fi
    printf 'nohash\n'
}

dirty_state_count() {
    printf '%s\n' "$1" | awk 'NF { count += 1 } END { print count + 0 }'
}

load_dirty_state() {
    if [ -f "$DIRTY_STATE_FILE" ]; then
        # shellcheck disable=SC1090
        . "$DIRTY_STATE_FILE"
    fi
}

write_dirty_state() {
    mkdir -p "$STATE_DIR"
    {
        printf 'PREV_DIRTY_HASH=%q\n' "$1"
        printf 'PREV_DIRTY_COUNT=%q\n' "$2"
        printf 'PREV_DIRTY_UPDATED_AT=%q\n' "$(date '+%Y-%m-%d %H:%M:%S')"
    } >"$DIRTY_STATE_FILE"
}

managed_overlay_allows_dirty_state() {
    local dirty_tracked="$1"
    DIRTY_TRACKED_TEXT="$dirty_tracked" \
    RUNTIME_OVERLAY_MANIFEST_PATH="$RUNTIME_OVERLAY_MANIFEST" \
    python3 - <<'PY'
from __future__ import annotations

import json
import os
import hashlib
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
    actual_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise SystemExit(1)

print(len(dirty_lines))
PY
}

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
        printf 'GIT_SYNC_LOCK_RUNNER=%q\n' "${RUNNER_SCRIPT}"
        printf 'GIT_SYNC_LOCK_STARTED_AT=%q\n' "$(date '+%Y-%m-%d %H:%M:%S')"
    } >"$GIT_SYNC_LOCK_INFO_FILE"
    return 0
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

if [ "${IMMUTABLE_RELEASE}" != "true" ] && [ ! -d "${PROJECT_ROOT}/.git" ]; then
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
    write_result "skipped_lock"
    exit 0
fi
{
    printf 'LOCK_PID=%q\n' "$$"
    printf 'LOCK_RUNNER=%q\n' "${RUNNER_SCRIPT}"
    printf 'LOCK_STARTED_AT=%q\n' "$(date '+%Y-%m-%d %H:%M:%S')"
} >"$LOCK_INFO_FILE"
trap 'rm -f "$LOCK_INFO_FILE"; rmdir "$LOCK_FILE"' EXIT

log "开始同步代码: ${REMOTE}/${BRANCH}"

if [ "$IMMUTABLE_RELEASE" = "true" ]; then
    log "immutable release 运行模式：跳过 Git fetch/pull，仅执行 release 内任务"
else
if ! acquire_git_sync_lock; then
    log "无法取得 Git 同步锁，本次任务阻断"
    write_result "sync_lock_failed" 1
    exit 1
fi
trap 'release_git_sync_lock; rm -f "$LOCK_INFO_FILE"; rmdir "$LOCK_FILE"' EXIT

git update-index --refresh >/dev/null 2>&1 || true

DIRTY_TRACKED="$(git status --porcelain --untracked-files=no)"
SKIP_GIT_SYNC="false"
if [ -n "$DIRTY_TRACKED" ]; then
    DIRTY_HASH="$(dirty_state_hash "$DIRTY_TRACKED")"
    DIRTY_COUNT_NOW="$(dirty_state_count "$DIRTY_TRACKED")"
    load_dirty_state
    if OVERLAY_MATCH_COUNT="$(managed_overlay_allows_dirty_state "$DIRTY_TRACKED" 2>/dev/null)"; then
        log "检测到受控 runtime overlay，跳过 Git 同步后继续运行 count=${OVERLAY_MATCH_COUNT} manifest=${RUNTIME_OVERLAY_MANIFEST}"
        write_dirty_state "$DIRTY_HASH" "$DIRTY_COUNT_NOW"
        SKIP_GIT_SYNC="true"
    elif [ "${DIRTY_HASH:-}" = "${PREV_DIRTY_HASH:-}" ]; then
        log "检测到受 Git 管理的本地修改，仍未清理；明细未变化 count=${DIRTY_COUNT_NOW} hash=${DIRTY_HASH}"
        write_result "blocked_dirty" 1
        exit 1
    else
        log "检测到受 Git 管理的本地修改，拒绝自动覆盖："
        printf '%s\n' "$DIRTY_TRACKED" | tee -a "$RUN_LOG"
        write_dirty_state "$DIRTY_HASH" "$DIRTY_COUNT_NOW"
        write_result "blocked_dirty" 1
        exit 1
    fi
fi
if [ "${SKIP_GIT_SYNC}" = "true" ]; then
    log "本次跳过 Git fetch/pull；等待仓库回归 clean 后再恢复自动同步"
else
    rm -f "$DIRTY_STATE_FILE"

    set +e
    git fetch "$REMOTE" "$BRANCH" 2>&1 | tee -a "$RUN_LOG"
    GIT_FETCH_EXIT_CODE=${PIPESTATUS[0]}
    set -e
    if [ "$GIT_FETCH_EXIT_CODE" -ne 0 ]; then
        log "Git fetch 失败，退出码: ${GIT_FETCH_EXIT_CODE}"
        write_result "sync_failed" "$GIT_FETCH_EXIT_CODE"
        exit "$GIT_FETCH_EXIT_CODE"
    fi
    LOCAL_HEAD="$(git rev-parse HEAD)"
    REMOTE_HEAD="$(git rev-parse "${REMOTE}/${BRANCH}")"

    if [ "$LOCAL_HEAD" != "$REMOTE_HEAD" ]; then
        log "发现新提交，执行快进更新"
        set +e
        git pull --ff-only "$REMOTE" "$BRANCH" 2>&1 | tee -a "$RUN_LOG"
        GIT_PULL_EXIT_CODE=${PIPESTATUS[0]}
        set -e
        if [ "$GIT_PULL_EXIT_CODE" -ne 0 ]; then
            log "Git pull 失败，退出码: ${GIT_PULL_EXIT_CODE}"
            write_result "sync_failed" "$GIT_PULL_EXIT_CODE"
            exit "$GIT_PULL_EXIT_CODE"
        fi
    else
        log "代码已是最新，无需更新"
    fi
fi

release_git_sync_lock
fi
trap 'rm -f "$LOCK_INFO_FILE"; rmdir "$LOCK_FILE"' EXIT

if [ ! -f "${RUNNER_PATH}" ]; then
    log "运行脚本不存在: ${RUNNER_PATH}"
    write_result "missing_runner"
    exit 1
fi

log "开始运行任务: ${RUNNER_PATH}"
if [ "${RUNNER_TIMEOUT_SECONDS}" -gt 0 ] && command -v timeout >/dev/null 2>&1; then
    log "启用主链路超时保护: ${RUNNER_TIMEOUT_SECONDS}s"
    set +e
    timeout --foreground "${RUNNER_TIMEOUT_SECONDS}" bash "${RUNNER_PATH}" 2>&1 | tee -a "$RUN_LOG"
    RUNNER_EXIT_CODE=${PIPESTATUS[0]}
    set -e
elif [ "${RUNNER_TIMEOUT_SECONDS}" -gt 0 ]; then
    log "系统缺少 timeout 命令，跳过主链路超时保护"
    set +e
    bash "${RUNNER_PATH}" 2>&1 | tee -a "$RUN_LOG"
    RUNNER_EXIT_CODE=${PIPESTATUS[0]}
    set -e
else
    set +e
    bash "${RUNNER_PATH}" 2>&1 | tee -a "$RUN_LOG"
    RUNNER_EXIT_CODE=${PIPESTATUS[0]}
    set -e
fi

if [ "${RUNNER_EXIT_CODE}" -eq 124 ]; then
    log "主链路执行超时，被保护性终止: ${RUNNER_TIMEOUT_SECONDS}s"
    write_result "timeout" "$RUNNER_EXIT_CODE"
    exit "${RUNNER_EXIT_CODE}"
fi
if [ "${RUNNER_EXIT_CODE}" -ne 0 ]; then
    log "主链路执行失败，退出码: ${RUNNER_EXIT_CODE}"
    write_result "failed" "$RUNNER_EXIT_CODE"
    exit "${RUNNER_EXIT_CODE}"
fi
log "同步与跑批完成"
write_result "completed" 0