#!/usr/bin/env bash
# 备用/迁移用 system cron 安装脚本：
# 生产定时默认统一由宝塔计划任务管理；直接运行本脚本不会写 crontab。
# 如确需迁移到 system cron，必须显式设置 AQSP_INSTALL_SYSTEM_CRON=true。
# 1. 北京时间 09:00-11:59 每 10 分钟触发盘中刷新（精确时间由脚本门控）
# 2. 北京时间 12:05 运行一次午盘回看
# 3. 北京时间 13:00-14:59 每 10 分钟触发盘中刷新（精确时间由脚本门控）
# 4. 北京时间 08:35 和周末 09:05 运行消息面雷达
# 5. 北京时间 18:00 运行收盘同步 + 全量跑批
# 6. 北京时间 19:40 运行冷启动补样本，避开收盘主链路
# 7. 北京时间每 15 分钟运行一次监控

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
CRON_LOG="${AQSP_CRON_LOG:-${PROJECT_ROOT}/logs/cron.log}"
INSTALL_SYSTEM_CRON="${AQSP_INSTALL_SYSTEM_CRON:-false}"
ENABLE_INTRADAY="${AQSP_ENABLE_INTRADAY_CRON:-true}"
ENABLE_MIDDAY="${AQSP_ENABLE_MIDDAY_CRON:-true}"
ENABLE_DAILY="${AQSP_ENABLE_DAILY_CRON:-true}"
ENABLE_MONITOR="${AQSP_ENABLE_MONITOR_CRON:-true}"
ENABLE_NEWS="${AQSP_ENABLE_NEWS_CRON:-true}"
ENABLE_COLDSTART="${AQSP_ENABLE_COLDSTART_CRON:-true}"
ENABLE_WALKFORWARD_GATE="${AQSP_ENABLE_WALKFORWARD_GATE_CRON:-true}"

mkdir -p "$(dirname "$CRON_LOG")"

if [[ ! "${INSTALL_SYSTEM_CRON,,}" =~ ^(1|true|yes|on)$ ]]; then
    cat <<EOF
AQSP system cron install skipped.
生产定时统一使用宝塔计划任务：/bin/bash ${PROJECT_ROOT}/scripts/bt_task.sh <action>
如确需迁移到 system cron，请显式设置 AQSP_INSTALL_SYSTEM_CRON=true 后重跑。
EOF
    exit 0
fi

emit_jobs() {
    if [[ "${ENABLE_INTRADAY,,}" =~ ^(1|true|yes|on)$ ]]; then
        echo '*/10 9-11 * * 1-5 /bin/bash '"${PROJECT_ROOT}"'/scripts/bt_task.sh intraday >> '"${CRON_LOG}"' 2>&1'
        echo '*/10 13-14 * * 1-5 /bin/bash '"${PROJECT_ROOT}"'/scripts/bt_task.sh intraday >> '"${CRON_LOG}"' 2>&1'
    fi

    if [[ "${ENABLE_MIDDAY,,}" =~ ^(1|true|yes|on)$ ]]; then
        echo '5 12 * * 1-5 /bin/bash '"${PROJECT_ROOT}"'/scripts/bt_task.sh midday >> '"${CRON_LOG}"' 2>&1'
    fi

    if [[ "${ENABLE_DAILY,,}" =~ ^(1|true|yes|on)$ ]]; then
        echo '0 18 * * 1-5 /bin/bash '"${PROJECT_ROOT}"'/scripts/bt_task.sh daily >> '"${CRON_LOG}"' 2>&1'
    fi

    if [[ "${ENABLE_COLDSTART,,}" =~ ^(1|true|yes|on)$ ]]; then
        echo '40 19 * * 1-5 /bin/bash '"${PROJECT_ROOT}"'/scripts/bt_task.sh coldstart >> '"${CRON_LOG}"' 2>&1'
    fi

    if [[ "${ENABLE_WALKFORWARD_GATE,,}" =~ ^(1|true|yes|on)$ ]]; then
        echo '0 22 * * 6 /bin/bash '"${PROJECT_ROOT}"'/scripts/bt_task.sh walkforward-gate >> '"${CRON_LOG}"' 2>&1'
    fi

    if [[ "${ENABLE_NEWS,,}" =~ ^(1|true|yes|on)$ ]]; then
        echo '35 8 * * 1-5 /bin/bash '"${PROJECT_ROOT}"'/scripts/bt_task.sh news >> '"${CRON_LOG}"' 2>&1'
        echo '5 9 * * 6,0 /bin/bash '"${PROJECT_ROOT}"'/scripts/bt_task.sh news >> '"${CRON_LOG}"' 2>&1'
    fi

    if [[ "${ENABLE_MONITOR,,}" =~ ^(1|true|yes|on)$ ]]; then
        echo '*/15 * * * 1-5 /bin/bash '"${PROJECT_ROOT}"'/scripts/bt_task.sh monitor >> '"${CRON_LOG}"' 2>&1'
    fi
}

CURRENT_CRONTAB="$(crontab -l 2>/dev/null || true)"
FILTERED_CRONTAB="$(
    printf '%s\n' "$CURRENT_CRONTAB" | grep -vE \
        'AQSP_RUNNER_SCRIPT=scripts/intraday_refresh\.sh|AQSP_RUNNER_SCRIPT=scripts/midday_refresh\.sh|/scripts/server_sync_and_run\.sh|/scripts/server_monitor\.sh|/scripts/bt_task\.sh (daily|intraday|midday|coldstart|walkforward-gate|monitor|news)' || true
)"

{
    printf '%s\n' "$FILTERED_CRONTAB"
    emit_jobs
} | sed '/^$/N;/^\n$/D' | crontab -

echo "AQSP cron installed for ${PROJECT_ROOT}"
crontab -l
