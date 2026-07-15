#!/usr/bin/env bash
# BaoTa wrapper: trigger the repository-owned gate and preserve its exit code.
# The wrapper deliberately has no second market-hours gate; bt_task.sh owns it.
set -u

PATH=/bin:/sbin:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin:~/bin
export PATH

status=0
/bin/bash /opt/aqsp/scripts/bt_task.sh intraday || status=$?
if [ "$status" -ne 0 ]; then
    echo "AQSP盘中刷新失败，退出码=$status"
    exit "$status"
fi

echo "AQSP盘中刷新成功"
