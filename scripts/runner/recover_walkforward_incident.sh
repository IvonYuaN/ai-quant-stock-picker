#!/usr/bin/env bash
# Recover from an accidental production walk-forward overload.
#
# Scope:
# - stop only production walk-forward wrapper/child processes
# - repair stale walk-forward status
# - optionally restart the canonical AQSP research target
#
# 北京时间任意时刻手工执行；不作为定时任务入口。

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
PYTHON_BIN="${AQSP_PYTHON:-${PROJECT_ROOT}/.venv/bin/python3}"
RESTART_RESEARCH="${AQSP_RECOVER_RESTART_RESEARCH:-false}"
STATUS_PATH="${AQSP_WALKFORWARD_STATUS_PATH:-${PROJECT_ROOT}/data/walkforward_production_status.json}"
LOG_DIR="${AQSP_RECOVER_LOG_DIR:-${PROJECT_ROOT}/logs/recovery}"
LOG_FILE="${LOG_DIR}/walkforward-incident-$(date +%Y-%m-%d).log"

log() {
    mkdir -p "$LOG_DIR"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

is_truthy() {
    local value
    value="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
    [[ "$value" =~ ^(1|true|yes|on)$ ]]
}

python_reader() {
    if [ -x "$PYTHON_BIN" ]; then
        printf '%s\n' "$PYTHON_BIN"
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        command -v python3
        return 0
    fi
    return 1
}

status_pids() {
    if [ ! -f "$STATUS_PATH" ]; then
        return 0
    fi
    local reader
    reader="$(python_reader || true)"
    if [ -z "$reader" ]; then
        return 0
    fi
    "$reader" - "$STATUS_PATH" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    payload = json.loads(open(path, encoding="utf-8").read())
except Exception:
    payload = {}
if not isinstance(payload, dict):
    payload = {}
status = str(payload.get("status") or "").strip()
if status not in {"running", "blocked_running", "timeout"}:
    sys.exit(0)
for key in ("child_pid", "pid"):
    value = payload.get(key)
    if isinstance(value, int) and value > 0:
        print(f"{key}\t{value}")
PY
}

proc_cmdline() {
    local pid="$1"
    local cmdline_path="/proc/${pid}/cmdline"
    if [ ! -r "$cmdline_path" ]; then
        return 1
    fi
    tr '\0' ' ' <"$cmdline_path"
}

is_production_wrapper_cmdline() {
    local cmdline="$1"
    [[ "$cmdline" == *"scripts/run_production_walkforward_gate.py"* ]] || return 1
    [[ "$cmdline" != *" --dry-run"* ]] || return 1
    [[ "$cmdline" != *" --repair-only"* ]] || return 1
}

is_production_child_cmdline() {
    local cmdline="$1"
    [[ "$cmdline" == *"-m aqsp walkforward"* ]] || return 1
    [[ "$cmdline" == *"--source sqlite_db"* ]] || return 1
    [[ "$cmdline" == *"--pool all"* ]] || return 1
    [[ "$cmdline" == *"--grid-cscv"* ]] || return 1
    [[ "$cmdline" == *"--symbols-file"* ]] || return 1
}

pid_matches_status_role() {
    local role="$1"
    local pid="$2"
    local cmdline
    cmdline="$(proc_cmdline "$pid" || true)"
    if [ -z "$cmdline" ]; then
        log "跳过 ${role} pid=${pid}: 无法读取 /proc cmdline"
        return 1
    fi
    case "$role" in
        child_pid)
            is_production_child_cmdline "$cmdline"
            ;;
        pid)
            is_production_wrapper_cmdline "$cmdline"
            ;;
        *)
            return 1
            ;;
    esac
}

kill_status_pid() {
    local role="$1"
    local pid="$2"
    if ! kill -0 "$pid" 2>/dev/null; then
        log "未发现 ${role} 进程: pid=${pid}"
        return 0
    fi
    if ! pid_matches_status_role "$role" "$pid"; then
        log "跳过 ${role} pid=${pid}: cmdline 不匹配生产 walk-forward 指纹"
        return 0
    fi
    log "终止 ${role}: pid=${pid}"
    kill "$pid" 2>/dev/null || true
    sleep 3
    if kill -0 "$pid" 2>/dev/null && pid_matches_status_role "$role" "$pid"; then
        log "强制终止 ${role}: pid=${pid}"
        kill -9 "$pid" 2>/dev/null || true
    fi
}

kill_status_pids() {
    local found="false"
    while IFS=$'\t' read -r role pid; do
        [ -n "${role:-}" ] || continue
        found="true"
        kill_status_pid "$role" "$pid"
    done < <(status_pids)
    if [ "$found" != "true" ]; then
        log "status 未记录可恢复的 production walk-forward pid"
    fi
}

cd "$PROJECT_ROOT"

log "开始恢复 production walk-forward 事故"
kill_status_pids

if [ -x "$PYTHON_BIN" ]; then
    log "修复 walk-forward 状态文件"
    "$PYTHON_BIN" scripts/run_production_walkforward_gate.py --repair-only \
        >>"$LOG_FILE" 2>&1 || true
else
    log "跳过状态修复：Python 不存在或不可执行: $PYTHON_BIN"
fi

if is_truthy "$RESTART_RESEARCH"; then
    if command -v systemctl >/dev/null 2>&1; then
        log "重启 aqsp-vibe-research.target"
        if ! systemctl restart aqsp-vibe-research.target; then
            log "[ERROR] aqsp-vibe-research.target 重启失败"
            exit 1
        fi
        sleep 2
        if ! systemctl is-active aqsp-vibe-research.target | tee -a "$LOG_FILE"; then
            log "[ERROR] aqsp-vibe-research.target 重启后未处于 active"
            exit 1
        fi
    else
        log "跳过 dashboard 重启：systemctl 不存在"
    fi
fi

log "恢复脚本完成"
