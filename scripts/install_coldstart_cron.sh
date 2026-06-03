#!/usr/bin/env bash
# 安装冷启动专用 cron：
# 1. 工作日北京时间 17:30 更新 sqlite 历史库
# 2. 随后运行 aqsp.cli run 累积 predictions ledger

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
CRON_LOG="${AQSP_CRON_LOG:-${PROJECT_ROOT}/logs/cron.log}"
CRON_SCHEDULE="${AQSP_COLDSTART_CRON_SCHEDULE:-30 17 * * 1-5}"
RUNNER_SCRIPT="${AQSP_COLDSTART_RUNNER:-${PROJECT_ROOT}/scripts/coldstart_daily.sh}"

mkdir -p "$(dirname "$CRON_LOG")"

if [ ! -f "$RUNNER_SCRIPT" ]; then
    echo "coldstart runner not found: $RUNNER_SCRIPT" >&2
    exit 1
fi

CURRENT_CRONTAB="$(crontab -l 2>/dev/null || true)"
FILTERED_CRONTAB="$(
    printf '%s\n' "$CURRENT_CRONTAB" | grep -vE \
        '/scripts/coldstart_daily\.sh|AQSP_COLDSTART_CRON' || true
)"

{
    printf '%s\n' "$FILTERED_CRONTAB"
    printf '%s /bin/bash %s >> %s 2>&1\n' \
        "$CRON_SCHEDULE" "$RUNNER_SCRIPT" "$CRON_LOG"
} | sed '/^$/N;/^\n$/D' | crontab -

echo "AQSP coldstart cron installed for ${PROJECT_ROOT}"
crontab -l
