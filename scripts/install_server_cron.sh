#!/usr/bin/env bash
# 安装简单服务器模式 cron：
# 1. 北京时间 09:40-11:59 每 10 分钟运行盘中刷新
# 2. 北京时间 13:00-14:59 每 10 分钟运行盘中刷新
# 3. 北京时间 18:00 运行收盘同步 + 全量跑批
# 4. 北京时间每 15 分钟运行一次监控

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
CRON_LOG="${AQSP_CRON_LOG:-${PROJECT_ROOT}/logs/cron.log}"
ENABLE_INTRADAY="${AQSP_ENABLE_INTRADAY_CRON:-true}"
ENABLE_DAILY="${AQSP_ENABLE_DAILY_CRON:-true}"
ENABLE_MONITOR="${AQSP_ENABLE_MONITOR_CRON:-true}"

mkdir -p "$(dirname "$CRON_LOG")"

emit_jobs() {
    if [[ "${ENABLE_INTRADAY,,}" =~ ^(1|true|yes|on)$ ]]; then
        echo '*/10 9-11 * * 1-5 AQSP_RUNNER_SCRIPT=scripts/intraday_refresh.sh /bin/bash '"${PROJECT_ROOT}"'/scripts/server_sync_and_run.sh >> '"${CRON_LOG}"' 2>&1'
        echo '*/10 13-14 * * 1-5 AQSP_RUNNER_SCRIPT=scripts/intraday_refresh.sh /bin/bash '"${PROJECT_ROOT}"'/scripts/server_sync_and_run.sh >> '"${CRON_LOG}"' 2>&1'
    fi

    if [[ "${ENABLE_DAILY,,}" =~ ^(1|true|yes|on)$ ]]; then
        echo '0 18 * * 1-5 /bin/bash '"${PROJECT_ROOT}"'/scripts/server_sync_and_run.sh >> '"${CRON_LOG}"' 2>&1'
    fi

    if [[ "${ENABLE_MONITOR,,}" =~ ^(1|true|yes|on)$ ]]; then
        echo '*/15 * * * 1-5 /bin/bash '"${PROJECT_ROOT}"'/scripts/server_monitor.sh >> '"${CRON_LOG}"' 2>&1'
    fi
}

CURRENT_CRONTAB="$(crontab -l 2>/dev/null || true)"
FILTERED_CRONTAB="$(
    printf '%s\n' "$CURRENT_CRONTAB" | grep -vE \
        'AQSP_RUNNER_SCRIPT=scripts/intraday_refresh\.sh|/scripts/server_sync_and_run\.sh|/scripts/server_monitor\.sh' || true
)"

{
    printf '%s\n' "$FILTERED_CRONTAB"
    emit_jobs
} | sed '/^$/N;/^\n$/D' | crontab -

echo "AQSP cron installed for ${PROJECT_ROOT}"
crontab -l
