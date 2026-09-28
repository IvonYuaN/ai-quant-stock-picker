#!/usr/bin/env bash
# install_ic_diagnosis_cron.sh — 在计算节点(runner)上执行：挂「工作日 IC 诊断」cron。
#
# 背景（IC 回流调度化）：ic_diagnosis_runner.sh 需要一个调度方，否则永远没人触发。
# 本脚本把它挂到 runner 的 root crontab。与 prod 侧 install_coldstart_cron.sh 同范式：
#   - 幂等：先 grep -vE 过滤旧行再追加，可反复执行不产生重复行；
#   - 指向软链 aqsp-scheduler-current（随 release 升级自动跟进，cron 行本身不改）。
#
# 时序红线（共享业务机 4C，同时跑 ifidy/lanshe 三业务）：
#   - 北京 10:00 = UTC 02:00（runner 时区 UTC，北京−8h）⇒ 默认 schedule `0 2 * * 1-5`；
#   - 完全避开 prod gate 窗口 07:30-17:30 UTC（=北京 15:30-01:30），不抢 gate；
#   - ic_diagnosis_runner.sh 自身带 load 让位（MAX_LOAD1=4）+ 超时硬墙（1800s），
#     双保险避免长期占机器导致业务被杀。
#   - 若 runner 实际时区非 UTC，用 AQSP_IC_CRON_SCHEDULE 覆盖（cron 按 runner 本机时区解释）。
#
# 用法（在 runner 上，发版 + runner_sync 后一键执行）：
#   bash /opt/aqsp-runner/aqsp-scheduler-current/scripts/install_ic_diagnosis_cron.sh
set -euo pipefail

RUNNER_ROOT="${RUNNER_ROOT:-/opt/aqsp-runner}"
RELEASE_LINK="$RUNNER_ROOT/aqsp-scheduler-current"
RUNNER_SCRIPT="$RELEASE_LINK/scripts/ic_diagnosis_runner.sh"
CRON_LOG="${AQSP_IC_CRON_LOG:-$RUNNER_ROOT/logs/ic_diagnosis_cron.log}"
# 默认 UTC `0 2 * * 1-5`（= 北京 10:00，工作日）；时区不同时用 env 覆盖
CRON_SCHEDULE="${AQSP_IC_CRON_SCHEDULE:-0 2 * * 1-5}"

[ -f "$RUNNER_SCRIPT" ] || {
  echo "缺 runner 脚本：$RUNNER_SCRIPT" >&2
  echo "（先在本机跑 runner_sync.sh 把当前 release 同步过去，再执行本脚本）" >&2
  exit 1
}
mkdir -p "$(dirname "$CRON_LOG")"

CRON_LINE="/bin/bash $RUNNER_SCRIPT >> $CRON_LOG 2>&1"

CURRENT_CRONTAB="$(crontab -l 2>/dev/null || true)"
FILTERED_CRONTAB="$(
  printf '%s\n' "$CURRENT_CRONTAB" | grep -vE \
      'scripts/ic_diagnosis_runner\.sh' || true
)"

{
  printf '%s\n' "$FILTERED_CRONTAB"
  printf '%s\n' "$CRON_SCHEDULE $CRON_LINE"
} | sed '/^$/N;/^\n$/D' | crontab -

echo "runner IC 诊断 cron 已安装（schedule=$CRON_SCHEDULE，日志=$CRON_LOG）"
crontab -l
