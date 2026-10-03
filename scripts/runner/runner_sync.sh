#!/usr/bin/env bash
# runner_sync.sh — 在 prod（在线服务机）上执行：把数据 / 代码 / warm cache 单向同步到计算节点(runner)。
#
# 双机分工（详见 docs/two-node-handoff.md）：
#   prod   = 在线服务机（2C/1.6G）：只跑盘中调度 / 落库 / monitor / 日报，永不跑重算力。
#   runner = 计算节点：只跑 walkforward gate / 证据脚本 / 数据回填 / pytest，绝不写生产数据。
#   前提：gate 是纯离线计算，输入 (raw sqlite + 代码 SHA + 参数) 一致 ⇒ 在哪台跑结果一致。
#
# 用法（在 prod 上）：
#   RUNNER_HOST=root@<ip> [RUNNER_ROOT=/opt/aqsp-runner] [SYNC_CACHE=1] bash scripts/runner_sync.sh
#
# 前置：把 prod 的 /root/.ssh/id_ed25519.pub 加进 runner 的 ~/.ssh/authorized_keys（单向免密）。
set -euo pipefail

# 默认指向已建好的计算节点（4C/8G，Ubuntu 22.04，仅开放 31777）
RUNNER_HOST="${RUNNER_HOST:-root@38.147.170.174}"
RUNNER_PORT="${RUNNER_PORT:-31777}"
RUNNER_ROOT="${RUNNER_ROOT:-/opt/aqsp-runner}"
GATE_DIR="${GATE_DIR:-/opt/aqsp/data/gate_run}"
# warm cache 只在首次建节点时传一次；之后 runner 自己的 cache 会持续保温，
# 日常同步靠 rsync 增量（只传变化块）即可，别再传这 894MB。
SYNC_CACHE="${SYNC_CACHE:-0}"
RELEASE_LINK="${RELEASE_LINK:-/opt/aqsp-releases/aqsp-scheduler-current}"

RSYNC_SSH="ssh -p ${RUNNER_PORT} -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
log() { printf '[sync %s] %s\n' "$(date '+%F %T')" "$*"; }

command -v rsync >/dev/null || { echo "缺少 rsync"; exit 1; }

# 0) 连通性 + 目录
if ! $RSYNC_SSH "$RUNNER_HOST" "mkdir -p '$RUNNER_ROOT/data' '$RUNNER_ROOT/gate_run' '$RUNNER_ROOT/scripts'"; then
  echo "无法免密登录 $RUNNER_HOST。请先把 prod 的 /root/.ssh/id_ed25519.pub 加入 runner 的 authorized_keys。"
  exit 1
fi

# 1) 代码：以 prod 当前生效 release 的 SHA 命名，保证两端口径严格一致
SHA="$(basename "$(readlink -f "$RELEASE_LINK")")"
log "release sha=$SHA"

# 🔴 切链必须与「rsync 成功 + 目标树完整」绑定（2026-10-03 修复）。
# 事故背景：85f596e7 于 10-01 03:21 同步进 runner 后**残缺**（`src/aqsp` 仅 166 个
# .py、整个 `src/aqsp/data/` 子包缺失，正常应 222 个），而 `aqsp-scheduler-current`
# 软链仍指向它 ⇒ 10-02 02:00 的 IC 以 `ModuleNotFoundError: No module named
# 'aqsp.data'` 1 秒崩溃。旧代码里 `ln -sfn` 是**下一条独立命令**（rsync 中断也会照跑），
# 且 `RELEASE_SHA` 写的是**目标** SHA ⇒ 日志显示 3805ed3e 而实跑的是残缺的 85f596e7。
# 现在：①rsync 非 0 立即退出、绝不切链；②切链前在 runner 侧做**完整性体检**；
#      ③体检不过则拒绝切链并报错（宁可不同步，也不指向残缺 release）。
log "同步代码 → $RUNNER_ROOT/releases/$SHA/"
if ! rsync -aP --delete -e "$RSYNC_SSH" \
  --exclude '.git/' --exclude 'node_modules/' --exclude '__pycache__/' --exclude '*.pyc' \
  "$RELEASE_LINK/" "$RUNNER_HOST:$RUNNER_ROOT/releases/$SHA/"; then
  log "❌ rsync 失败（rc=$?）：**不切链**（避免软链指向残缺 release）"
  exit 1
fi

# 完整性体检：关键子包必须存在 + .py 数与源端一致（少一个文件都可能让 import 崩）
log "校验 $SHA 完整性…"
if ! $RSYNC_SSH "$RUNNER_HOST" "
  set -e
  R='$RUNNER_ROOT/releases/$SHA'
  n=\$(find \"\$R/src\" -name '*.py' 2>/dev/null | wc -l)
  # 关键子包：aqsp.data 被 ic_diagnosis / factor_ic 直接 import，缺了必崩
  for sub in src/aqsp/data src/aqsp/strategies src/aqsp/core; do
    [ -d \"\$R/\$sub\" ] || { echo \"MISSING:\$sub\"; exit 3; }
  done
  [ \"\$n\" -ge 200 ] || { echo \"TOO_FEW_PY:\$n\"; exit 4; }
  echo \"OK py=\$n\"
"; then
  log "❌ $SHA 完整性体检不过（缺关键子包或 .py 过少）⇒ **拒绝切链**"
  log "   修法：重跑本脚本（rsync -aP 可续传）；必要时先在 runner 上删除该残缺 release 目录再重跑"
  exit 1
fi

# 切链前先把「旧 current」记到 rollback 软链（2026-10-03 修复）。
# 🔴 为什么必须在这里维护：prod 侧 `deploy_immutable_release.sh:switch_links()` 会自动维护
#    rollback，但**本脚本此前完全不碰它** ⇒ runner 的 `aqsp-scheduler-rollback` 是历史手工
#    建的软链，一旦指向的 release 被清理就**永久悬空**（实测 2026-10-03 指向早已删除的
#    `0697d3e4`）⇒ **真要回滚时才发现回滚链是断的**，属「回滚能力假象」。
#    与 current 的切链顺序：先记 rollback（指向旧 current），再切 current。
if $RSYNC_SSH "$RUNNER_HOST" "[ -L '$RUNNER_ROOT/aqsp-scheduler-current' ]"; then
  OLD=$($RSYNC_SSH "$RUNNER_HOST" "readlink -f '$RUNNER_ROOT/aqsp-scheduler-current'")
  if [ -n "$OLD" ] && [ "$OLD" != "$RUNNER_ROOT/releases/$SHA" ] && [ -d "$OLD" ]; then
    $RSYNC_SSH "$RUNNER_HOST" "ln -sfn '$OLD' '$RUNNER_ROOT/aqsp-scheduler-rollback'"
    log "rollback 软链 → $(basename "$OLD")"
  fi
fi

$RSYNC_SSH "$RUNNER_HOST" "ln -sfn '$RUNNER_ROOT/releases/$SHA' '$RUNNER_ROOT/aqsp-scheduler-current'"
printf '%s\n' "$SHA" > /tmp/aqsp_release_sha
rsync -aP -e "$RSYNC_SSH" /tmp/aqsp_release_sha "$RUNNER_HOST:$RUNNER_ROOT/RELEASE_SHA"
log "✅ 已切链 → $SHA（rsync 成功 + 完整性体检通过）"

# 2) 数据：直接 rsync 文件本体（不用 sqlite backup，避免在 1.6G 机器上再吃一份内存）
#    ⚠️ 请在盘后落库完成之后、无写入时执行；runner 端会做 integrity_check 兜底。
RAW_DB="$(readlink -f /opt/market-data/astocks_raw.db)"
log "同步数据 $(basename "$RAW_DB")"
rsync -aP --partial --inplace -e "$RSYNC_SSH" "$RAW_DB" "$RUNNER_HOST:$RUNNER_ROOT/data/astocks_raw.db"
if [ -f "${RAW_DB}-wal" ]; then
  rsync -aP -e "$RSYNC_SSH" "${RAW_DB}-wal" "$RUNNER_HOST:$RUNNER_ROOT/data/astocks_raw.db-wal"
fi

# 3) warm cache（~894MB；带上可让 runner 直接跳过取数阶段，只重算 grid）
if [ "$SYNC_CACHE" = "1" ] && [ -f "$GATE_DIR/walkforward_raw_production_cache.db" ]; then
  log "同步 warm cache"
  rsync -aP --partial --inplace -e "$RSYNC_SSH" \
    "$GATE_DIR/walkforward_raw_production_cache.db" "$RUNNER_HOST:$RUNNER_ROOT/gate_run/"
fi

# 4) 标的池 + 分析脚本（gate 用的 symbols 快照，保证两台同池）
for f in evidence_symbols.txt extract_gate_summary.py stop_loss_exit_evidence.py; do
  src="$GATE_DIR/$f"
  if [ -f "$src" ]; then
    rsync -aP -e "$RSYNC_SSH" "$src" "$RUNNER_HOST:$RUNNER_ROOT/gate_run/$f"
  fi
done

# 5) 新鲜度探针：对比 prod / runner 两边 raw 库的 MAX(trade_date)。
#    runner 库一旦滞后，生产 gate 会立刻被 blocked_cutoff 守卫秒退（requested end
#    不得超库内 MAX(trade_date)），所以同步刚完成就要验证「数据真的过去了」，
#    落后超过阈值（AQSP_SYNC_STALE_DAYS，默认 7 天＝每周一次同步的周期余量）
#    时输出 [ALERT][runner_sync] 告警行供 monitors 捕捉。
PROBE_PY='import sqlite3, sys
try:
    with sqlite3.connect(sys.argv[1]) as conn:
        row = conn.execute("SELECT MAX(trade_date) FROM daily_qfq").fetchone()
except sqlite3.Error:
    print("")
else:
    print(str(row[0] or "").strip().replace("-", "")[:8])'

read_max_day_local() {
  # $1 = sqlite 路径；打印 YYYYMMDD（读不到/表缺失时打印空串）
  python3 - "$1" <<<"$PROBE_PY"
}

read_max_day_runner() {
  # 远端同样用 python3 标准库读库（不依赖 runner 装没装 sqlite3 CLI）
  $RSYNC_SSH "$RUNNER_HOST" "python3 - '$RUNNER_ROOT/data/astocks_raw.db'" <<<"$PROBE_PY"
}

STALE_DAYS="${AQSP_SYNC_STALE_DAYS:-7}"
PROD_DAY="$(read_max_day_local "$RAW_DB")"
if RUNNER_DAY="$(read_max_day_runner)"; then :; else RUNNER_DAY=""; fi

if [ -z "$PROD_DAY" ]; then
  log "[ALERT][runner_sync] 新鲜度探针失败：prod 库 MAX(trade_date) 不可读（$RAW_DB），请人工检查"
elif [ -z "$RUNNER_DAY" ]; then
  log "[ALERT][runner_sync] 新鲜度探针失败：无法从 $RUNNER_HOST 读取 runner 库 MAX(trade_date)，请人工检查 runner"
else
  LAG="$(python3 -c 'import sys
from datetime import datetime


def _day(raw):
    try:
        return datetime.strptime(raw.strip().replace("-", "")[:8], "%Y%m%d").date()
    except ValueError:
        return None


prod_day, runner_day = _day(sys.argv[1]), _day(sys.argv[2])
print("" if prod_day is None or runner_day is None else (prod_day - runner_day).days)' \
    "$PROD_DAY" "$RUNNER_DAY")"
  if [ -z "$LAG" ]; then
    log "[ALERT][runner_sync] 新鲜度探针异常：MAX(trade_date) 无法解析 prod=$PROD_DAY runner=$RUNNER_DAY"
  elif [ "$LAG" -gt "$STALE_DAYS" ]; then
    log "[ALERT][runner_sync] runner 数据不新鲜：runner=$RUNNER_DAY prod=$PROD_DAY 落后 ${LAG} 天（阈值 ${STALE_DAYS} 天）——gate 将被 blocked_cutoff 秒退，请检查 runner 同步链路"
  else
    log "新鲜度探针通过：runner=$RUNNER_DAY prod=$PROD_DAY 落后 ${LAG} 天（阈值 ${STALE_DAYS} 天）"
  fi
fi

log "完成：代码 sha=$SHA，数据 $(basename "$RAW_DB")，cache=$SYNC_CACHE"
# 注意：runner_gate.sh 随 release 代码同步（位于 releases/$SHA/scripts/ 下），
# $RUNNER_ROOT/scripts/ 不是本脚本的同步目标（历史上该目录从未被填充过）。
log "下一步：在 runner 上跑  bash $RUNNER_ROOT/aqsp-scheduler-current/scripts/runner_gate.sh"
