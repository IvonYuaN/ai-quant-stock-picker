#!/usr/bin/env bash
# Run an ad-hoc / data probe under a hard timeout so a stuck process can never
# silently peg the CPU and break the scheduled pipelines.
#
# Root cause of the 2026-09-03 incident: a bare `python3 - <<'PY' ... PY` probe
# hung and held one core at ~100% for ~1.5 days, which starved HTTP requests ->
# tencent returned empty bodies + eastmoney breaker tripped -> intraday gate
# failed with "stale". Always wrap one-off probes with this instead of running
# them bare.
#
# Usage:
#   scripts/safe_probe.sh 300 python3 - <<'PY'
#   ...probe code...
#   PY
#   scripts/safe_probe.sh 600 ./some_long_probe.sh arg1 arg2
#
# Exit codes:
#   0        command finished within the timeout
#   124      command was killed by the timeout (hung)
#   2        bad invocation (missing args)
set -euo pipefail

if [ "$#" -lt 2 ]; then
    echo "Usage: safe_probe.sh <timeout_sec> <command> [args...]" >&2
    exit 2
fi

TIMEOUT_SEC="$1"
shift

if ! command -v timeout >/dev/null 2>&1; then
    echo "[safe_probe][WARN] 'timeout' not found; running WITHOUT guard (risk!)" >&2
    exec "$@"
fi

echo "[safe_probe] timeout=${TIMEOUT_SEC}s cmd=$*" >&2
exec timeout "$TIMEOUT_SEC" "$@"
