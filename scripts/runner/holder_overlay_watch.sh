#!/usr/bin/env bash
# holder_overlay 跑批守护：盯到 gate 结束 → 自动跑收尾判读 → 落结论。
#
# 用法（本机）：
#   bash scripts/runner/holder_overlay_watch.sh            # 前台守护（可 nohup 后台）
#   MAX_HOURS=14 POLL_SEC=300 bash scripts/runner/holder_overlay_watch.sh
#
# 为什么存在
# ----------
# gate 跑批约 8~12h（8 臂串行），此前每轮都要人工探针问「跑完没」——
# 属于纯等待开销。本脚本把「等 + 收尾」自动化：跑完立即出 verdict.md，
# 期间只在状态变化时写一行日志。
#
# 退出码：0=已出结论；2=超时/异常退出（未出结论）
set -uo pipefail

RUNNER_HOST="${RUNNER_HOST:-aqsp-runner}"
OUT_DIR="${OUT_DIR:-/opt/aqsp-runner/gate_run_holder_overlay_v3}"
LOCAL_DIR="${LOCAL_DIR:-/tmp/holder_eval}"
POLL_SEC="${POLL_SEC:-300}"
# ⚠️ 必须是**整数小时**：bash 算术展开不支持小数
# （实测 MAX_HOURS=0.001 会报 "invalid arithmetic operator" 并让 deadline 未定义）
MAX_HOURS="${MAX_HOURS:-14}"
case "$MAX_HOURS" in
  ''|*[!0-9]*) echo "MAX_HOURS 必须是整数小时，收到：$MAX_HOURS" >&2; exit 2 ;;
esac
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

log() { printf '[watch %s] %s\n' "$(date '+%F %T')" "$*"; }
mkdir -p "$LOCAL_DIR"

deadline=$(( $(date +%s) + MAX_HOURS * 3600 ))
last=""
while :; do
  # 读状态（ssh 失败不致命，计一次瞬断）
  raw="$(ssh -o ConnectTimeout=20 -o BatchMode=yes "$RUNNER_HOST" \
    "python3 -c \"
import json
try:
    d=json.load(open('$OUT_DIR/walkforward_production_status.json'))
    print(d.get('status'), d.get('grid_profile'), d.get('detail',''))
except Exception:
    print('no_status')
\" 2>/dev/null" 2>/dev/null)"

  if [ -z "$raw" ]; then
    log "ssh 无响应（网络抖动），${POLL_SEC}s 后重试"
  else
    status="$(echo "$raw" | awk '{print $1}')"
    if [ "$status" != "$last" ]; then
      log "状态：$raw"
      last="$status"
    fi
    case "$status" in
      completed|failed|blocked|no_status)
        # ⚠️ 必须写 ${status} 而不是 $status：紧跟全角括号时 bash 把「（」并入
        # 变量名 ⇒ set -u 下报 unbound variable，收尾逻辑根本不执行
        # （2026-11-12 实测：gate 09:40 跑完，守护在这里崩掉，结论没生成）。
        log "跑批终止（status=${status}），开始收尾判读"
        OUT_DIR="$OUT_DIR" LOCAL_DIR="$LOCAL_DIR" \
          bash "$REPO_ROOT/scripts/runner/holder_overlay_verdict.sh" \
          && log "✅ 结论已出：$LOCAL_DIR/verdict.md" \
          || log "⚠️ 收尾脚本返回非 0，见 $LOCAL_DIR"
        exit 0
        ;;
    esac
  fi

  if [ "$(date +%s)" -ge "$deadline" ]; then
    log "❌ 超过 MAX_HOURS=${MAX_HOURS}h 仍未结束，退出（未出结论）"
    exit 2
  fi
  sleep "$POLL_SEC"
done
