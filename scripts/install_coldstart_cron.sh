#!/usr/bin/env bash
# 受控迁移 system cron 到统一 BT 入口。
# 生产默认由宝塔计划任务管理；仅显式迁移时才写 crontab。

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
CRON_LOG="${AQSP_CRON_LOG:-${PROJECT_ROOT}/logs/cron.log}"
CRON_SCHEDULE="${AQSP_COLDSTART_CRON_SCHEDULE:-40 19 * * 1-5}"
INSTALL_SYSTEM_CRON="${AQSP_INSTALL_SYSTEM_CRON:-false}"
RUNNER_SCRIPT="${PROJECT_ROOT}/scripts/bt_task.sh"

mkdir -p "$(dirname "$CRON_LOG")"

if [[ ! "${INSTALL_SYSTEM_CRON,,}" =~ ^(1|true|yes|on)$ ]]; then
    echo "AQSP coldstart system cron install skipped; production uses BT Panel bt_task.sh coldstart"
    exit 0
fi

if [ ! -f "$RUNNER_SCRIPT" ]; then
    echo "BT task runner not found: $RUNNER_SCRIPT" >&2
    exit 1
fi

CURRENT_CRONTAB="$(crontab -l 2>/dev/null || true)"
FILTERED_CRONTAB="$(
    printf '%s\n' "$CURRENT_CRONTAB" | grep -vE \
        '/scripts/coldstart_daily\.sh|/scripts/bt_task\.sh coldstart|AQSP_COLDSTART_CRON' || true
)"

{
    printf '%s\n' "$FILTERED_CRONTAB"
    printf '%s /bin/bash %s coldstart >> %s 2>&1\n' \
        "$CRON_SCHEDULE" "$RUNNER_SCRIPT" "$CRON_LOG"
} | sed '/^$/N;/^\n$/D' | crontab -

echo "AQSP coldstart cron installed for ${PROJECT_ROOT}"
crontab -l
