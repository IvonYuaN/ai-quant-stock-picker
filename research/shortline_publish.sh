#!/bin/bash
# 影子账户产物 → prod 单方向推送（在 runner 上由 cron 调起）。
#
# 为什么单方向：prod 解析不了 aqsp-runner 的主机名，只能 runner 主动推；
# 反过来让 prod 拉是行不通的（这也是估值数据当初走本机 scp -3 中转到 prod 的原因）。
#
# 为什么单独一条 cron、不塞进 cron_wrapper.sh：
#   推送失败不该拖累收盘结算与报告；结算失败也不该让 prod 没人管地停在旧数字上。
#   失败只写 publish.log 并以非 0 退出，prod 侧继续显示上一次成功的产物（前端标「已过期」）。
#
# 前置条件：runner 的 /root/.ssh/id_ed25519.pub 已加入 prod 的 /root/.ssh/authorized_keys。
# 本脚本用 BatchMode=yes —— 不会卡在交互式密码提示上等死（cron 里等输入等于永久挂住）。
set -uo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

HERE=/opt/aqsp-runner/shortline
PY=/opt/aqsp-runner/venv/bin/python
LOG=$HERE/publish.log
# prod 没有 DNS 记录，只能写 IP；可用环境变量覆盖（换机时不必改仓库）。
PROD_HOST="${AQSP_PROD_HOST:-8.130.124.238}"
PROD_DIR="${AQSP_PROD_DATA_DIR:-/opt/aqsp-releases/aqsp-scheduler-current/data}"
KEY="${AQSP_SSH_KEY:-/root/.ssh/id_ed25519}"

cd "$HERE" || exit 1
ts() { date -u +%F' '%T; }

nice -n 15 "$PY" paper_export.py >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
  echo "$(ts) export 失败，未推送" >> "$LOG"
  exit 1
fi

if scp -q -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new \
       -i "$KEY" paper_arena_api.json "root@${PROD_HOST}:${PROD_DIR}/paper_arena.json" >> "$LOG" 2>&1; then
  echo "$(ts) pushed $(stat -c %s paper_arena_api.json)B → prod data/paper_arena.json" >> "$LOG"
else
  echo "$(ts) 推送失败：确认 prod authorized_keys 已含 runner 公钥（当前 key=${KEY}）" >> "$LOG"
  exit 1
fi
