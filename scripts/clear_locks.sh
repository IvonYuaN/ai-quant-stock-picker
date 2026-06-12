#!/usr/bin/env bash
# Safely inspect or clear stale AQSP lock directories.
#
# Default mode is conservative: remove only lock directories older than
# AQSP_LOCK_STALE_MINUTES. Use AQSP_CLEAR_LOCKS_FORCE=true only after checking
# that no AQSP bt/daily/coldstart task is running.

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
LOCK_DIR="${PROJECT_ROOT}/.locks"
STALE_MINUTES="${AQSP_LOCK_STALE_MINUTES:-360}"
FORCE="${AQSP_CLEAR_LOCKS_FORCE:-false}"
NOW_EPOCH="$(date +%s)"

is_truthy() {
    [[ "${1,,}" =~ ^(1|true|yes|on)$ ]]
}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

if [ ! -d "$LOCK_DIR" ]; then
    log "锁目录不存在，无需清理: $LOCK_DIR"
    exit 0
fi

if ! [[ "$STALE_MINUTES" =~ ^[0-9]+$ ]]; then
    log "[ERROR] AQSP_LOCK_STALE_MINUTES 必须是分钟整数: $STALE_MINUTES"
    exit 2
fi

removed=0
kept=0

load_lock_info() {
    local info_file="$1"
    LOCK_PID=""
    LOCK_RUNNER=""
    LOCK_STARTED_AT=""
    if [ -f "$info_file" ]; then
        # shellcheck disable=SC1090
        . "$info_file"
    fi
}

while IFS= read -r -d '' lock_path; do
    lock_name="$(basename "$lock_path")"
    mtime="$(stat -c %Y "$lock_path" 2>/dev/null || stat -f %m "$lock_path")"
    age_minutes=$(( (NOW_EPOCH - mtime) / 60 ))
    info_file="${lock_path}/meta.env"
    load_lock_info "$info_file"
    lock_pid="${LOCK_PID:-}"
    lock_runner="${LOCK_RUNNER:-unknown}"
    lock_started_at="${LOCK_STARTED_AT:-unknown}"

    if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null && ! is_truthy "$FORCE"; then
        kept=$((kept + 1))
        log "保留活跃锁: $lock_name runner=${lock_runner} pid=${lock_pid} started_at=${lock_started_at} age=${age_minutes}min"
    elif is_truthy "$FORCE" || [ "$age_minutes" -ge "$STALE_MINUTES" ]; then
        rm -rf -- "$lock_path"
        removed=$((removed + 1))
        log "已清理锁: $lock_name runner=${lock_runner} pid=${lock_pid:-unknown} started_at=${lock_started_at} age=${age_minutes}min"
    else
        kept=$((kept + 1))
        log "保留近期锁: $lock_name runner=${lock_runner} pid=${lock_pid:-unknown} started_at=${lock_started_at} age=${age_minutes}min"
    fi
done < <(find "$LOCK_DIR" -maxdepth 1 -type d -name "*.lock" -print0)

log "清理完成: removed=${removed} kept=${kept} stale_after=${STALE_MINUTES}min force=${FORCE}"
