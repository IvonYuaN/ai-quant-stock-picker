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
rsync -aP --delete -e "$RSYNC_SSH" \
  --exclude '.git/' --exclude 'node_modules/' --exclude '__pycache__/' --exclude '*.pyc' \
  "$RELEASE_LINK/" "$RUNNER_HOST:$RUNNER_ROOT/releases/$SHA/"
$RSYNC_SSH "$RUNNER_HOST" "ln -sfn '$RUNNER_ROOT/releases/$SHA' '$RUNNER_ROOT/aqsp-scheduler-current'"
printf '%s\n' "$SHA" > /tmp/aqsp_release_sha
rsync -aP -e "$RSYNC_SSH" /tmp/aqsp_release_sha "$RUNNER_HOST:$RUNNER_ROOT/RELEASE_SHA"

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

log "完成：代码 sha=$SHA，数据 $(basename "$RAW_DB")，cache=$SYNC_CACHE"
log "下一步：在 runner 上跑  bash $RUNNER_ROOT/scripts/runner_gate.sh"
