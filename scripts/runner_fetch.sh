#!/usr/bin/env bash
# runner_fetch.sh — 在 prod（在线服务机）上执行：把计算节点的 gate 结果拉回本地。
#
# 为什么是 prod 主动 pull 而不是 runner 回传：这样 runner 不需要任何连回生产的 SSH 权限，
# 权限面最小（prod → runner 单向），符合「runner 只读、不碰生产」的红线。
#
# 用法（在 prod 上）：
#   bash scripts/runner_fetch.sh                 # 拉取到 /opt/aqsp/data/gate_run/
#   DRY_RUN=1 bash scripts/runner_fetch.sh       # 只看远端有什么，不拉
#
# 拉回后的文件名带 runner 前缀，避免覆盖 prod 自己那轮的结果（人工核对后再改名生效）。
set -euo pipefail

RUNNER_HOST="${RUNNER_HOST:-root@38.147.170.174}"
RUNNER_PORT="${RUNNER_PORT:-31777}"
RUNNER_ROOT="${RUNNER_ROOT:-/opt/aqsp-runner}"
GATE_DIR="${GATE_DIR:-/opt/aqsp/data/gate_run}"
DRY_RUN="${DRY_RUN:-0}"

RSYNC_SSH="ssh -p ${RUNNER_PORT} -o BatchMode=yes"
log() { printf '[fetch %s] %s\n' "$(date '+%F %T')" "$*"; }

# 1) 远端产出清单
log "远端 $RUNNER_HOST:$RUNNER_ROOT/gate_run 产出："
$RSYNC_SSH "$RUNNER_HOST" "ls -l '$RUNNER_ROOT/gate_run' 2>/dev/null || echo '(空)'"
$RSYNC_SSH "$RUNNER_HOST" "echo -n '远端代码 SHA: '; cat '$RUNNER_ROOT/RELEASE_SHA' 2>/dev/null || echo unknown"
log "prod 代码 SHA: $(basename "$(readlink -f /opt/aqsp-releases/aqsp-scheduler-current)")"

if [ "$DRY_RUN" = "1" ]; then
  log "DRY_RUN=1，未拉取"
  exit 0
fi

# 2) 拉取结果文件（带 runner. 前缀，绝不覆盖 prod 自己的结果）
mkdir -p "$GATE_DIR"
for f in report.md walkforward_gate.json gate_summary.md; do
  if $RSYNC_SSH "$RUNNER_HOST" "test -f '$RUNNER_ROOT/gate_run/$f'"; then
    rsync -aP -e "$RSYNC_SSH" "$RUNNER_HOST:$RUNNER_ROOT/gate_run/$f" "$GATE_DIR/runner.$f"
    log "已拉取 → $GATE_DIR/runner.$f"
  else
    log "远端缺 $f（gate 可能还没跑完）"
  fi
done

# 3) 若 gate.json 已到位，本地直接出一份摘要
if [ -f "$GATE_DIR/runner.walkforward_gate.json" ] && [ -f "$GATE_DIR/extract_gate_summary.py" ]; then
  /opt/aqsp-vibe-venv/bin/python "$GATE_DIR/extract_gate_summary.py" \
    --gate "$GATE_DIR/runner.walkforward_gate.json" \
    --report "$GATE_DIR/runner.report.md" \
    --output "$GATE_DIR/runner_summary.md" || log "摘要生成失败（可忽略）"
  [ -f "$GATE_DIR/runner_summary.md" ] && log "摘要：$GATE_DIR/runner_summary.md"
fi

log "完成。⚠️ 文件带 runner. 前缀，核对无误后再决定是否覆盖 prod 自己的 gate 结果。"
