#!/usr/bin/env bash
# Local helper: sync walk-forward incident guardrails, then recover the server.
#
# 北京时间任意时刻手工执行；用于 SSH 恢复后的事故收尾，不作为定时任务。

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SSH_TARGET="${AQSP_SSH_TARGET:-aqsp-server}"
HEALTH_URL="${AQSP_DASHBOARD_HEALTH_URL:-https://lh.ifidy.cn/api/health}"
PYTHON_BIN="${AQSP_LOCAL_PYTHON:-python3}"
SSH_CONNECT_TIMEOUT="${AQSP_RECOVER_SSH_CONNECT_TIMEOUT:-8}"
RESULT_FILE="${AQSP_RECOVER_RESULT_FILE:-${PROJECT_ROOT}/.state/walkforward-incident-recovery.env}"

cd "$PROJECT_ROOT"

write_result() {
    mkdir -p "$(dirname "$RESULT_FILE")"
    {
        printf 'status=%s\n' "$1"
        printf 'ssh_target=%s\n' "$SSH_TARGET"
        printf 'health_url=%s\n' "$HEALTH_URL"
    } >"$RESULT_FILE"
}

# This helper is a production incident repair path.  It must never become a
# generic remote runner entrypoint: heavy walk-forward work belongs to the
# registered compute node and is launched through runner_gate.sh.  Keep the
# target fail-closed because an accidental RUNNER_HOST/AQSP_SSH_TARGET reuse
# would otherwise perform a production repair transaction on the wrong host.
if [ "$SSH_TARGET" != "aqsp-server" ]; then
    echo "[blocked] recovery target must be aqsp-server (got: ${SSH_TARGET})"
    write_result "blocked_target_role"
    exit 2
fi

FILES=(
    "scripts/run_production_walkforward_gate.py"
    "scripts/recover_walkforward_incident.sh"
    "scripts/sync_and_recover_walkforward_incident.sh"
    "src/aqsp/web/data_provider.py"
)

echo "[preflight] checking SSH: ${SSH_TARGET}"
if ! ssh -o ConnectTimeout="${SSH_CONNECT_TIMEOUT}" "$SSH_TARGET" 'echo ok' >/dev/null; then
    echo "[blocked] SSH unavailable: ${SSH_TARGET}"
    write_result "blocked_ssh"
    exit 2
fi

echo "[sync] syncing walk-forward guard/recovery files to ${SSH_TARGET}"
"$PYTHON_BIN" scripts/sync_runtime_files_to_server.py "${FILES[@]}"

echo "[recover] running remote recovery script"
ssh -o ConnectTimeout="${SSH_CONNECT_TIMEOUT}" "$SSH_TARGET" \
    'cd /opt/aqsp && bash scripts/recover_walkforward_incident.sh'

echo "[verify] overlay"
"$PYTHON_BIN" scripts/sync_runtime_files_to_server.py --verify-overlay

echo "[verify] dashboard health"
curl -Ik --max-time 12 "$HEALTH_URL"
write_result "completed"
