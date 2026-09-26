#!/usr/bin/env bash
# ic_diagnosis_runner.sh — 在计算节点(runner)上执行：滚动窗口因子 IC 诊断。
#
# 用法（在 runner 上，由 cron 触发）：
#   [WINDOW_DAYS=90] [MAX_SYMBOLS=300] [STEP=10] [EXTRA_FACTORS=htf,mr,volume,rps] \
#   [TIMEOUT_SEC=1800] bash /opt/aqsp-runner/aqsp-scheduler-current/scripts/ic_diagnosis_runner.sh
#
# 与 runner_gate.sh 同范式：
#   - 共享业务机 load 守卫（runner 同时跑着 ifidy/lanshe 线上业务，load 高时让位）；
#   - release/venv/数据 前置校验；
#   - **超时硬墙**（默认 1800s）：滚动诊断本应分钟级，超时即杀、不长期占机器
#     （红线：避免大量长期占用导致业务被杀）；
#   - 成功才写 IC_READY 标记（供 prod 侧 fetch_ic_diagnosis.sh 判新鲜度），
#     失败/超时则删旧 IC_READY，绝不遗留陈旧标记骗过 prod。
#
# 结果不主动回传：runner 落盘本地（pit_cache/factor_ic/），由 prod 用
# fetch_ic_diagnosis.sh 主动 pull（runner 不需要任何连回生产的 SSH 权限）。
set -euo pipefail

RUNNER_ROOT="${RUNNER_ROOT:-/opt/aqsp-runner}"
RELEASE="${RELEASE:-$RUNNER_ROOT/aqsp-scheduler-current}"
VENV="${VENV:-$RUNNER_ROOT/venv}"
DATA="${DATA:-$RUNNER_ROOT/data/astocks_raw.db}"
OUT_DIR="${OUT_DIR:-$RUNNER_ROOT/pit_cache/factor_ic}"

# 诊断口径（可 env 覆盖）
WINDOW_DAYS="${WINDOW_DAYS:-90}"
MAX_SYMBOLS="${MAX_SYMBOLS:-300}"
STEP="${STEP:-10}"
EXTRA_FACTORS="${EXTRA_FACTORS:-}"
TIMEOUT_SEC="${TIMEOUT_SEC:-1800}"   # 超时硬墙，防长期占用
LOAD_GUARD="${LOAD_GUARD:-1}"
MAX_LOAD1="${MAX_LOAD1:-4}"          # 1 分钟 load 上限（共享机让位红线）
LOAD_GUARD_WAIT_SEC="${LOAD_GUARD_WAIT_SEC:-300}"
LOADAVG_PATH="${LOADAVG_PATH:-/proc/loadavg}"

log() { printf '[ic-diag %s] %s\n' "$(date '+%F %T')" "$*"; }
mkdir -p "$OUT_DIR"

# 0) 共享业务机守卫：load 高就让位给业务，连库都不要碰。
if [ "$LOAD_GUARD" = "1" ]; then
  guard_deadline=$(( $(date +%s) + LOAD_GUARD_WAIT_SEC ))
  while :; do
    load1="$(cut -d' ' -f1 "$LOADAVG_PATH")"
    if awk -v l="$load1" -v m="$MAX_LOAD1" 'BEGIN { exit !(l <= m) }'; then
      log "load1=$load1 ≤ $MAX_LOAD1，继续"
      break
    fi
    if [ "$(date +%s)" -ge "$guard_deadline" ]; then
      log "load1=$load1 > $MAX_LOAD1，让位业务，跳过本轮 IC 诊断"
      rm -f "$OUT_DIR/IC_READY"
      exit 0
    fi
    log "load1=$load1 > $MAX_LOAD1，等 30s 再判"
    sleep 30
  done
fi

# 1) 前置校验：代码 / 数据 / venv（与 runner_gate 同）
[ -d "$RELEASE" ] || { log "缺 release：$RELEASE（先跑 runner_sync.sh）"; exit 1; }
[ -f "$DATA" ] || { log "缺数据：$DATA（先跑 runner_sync.sh）"; exit 1; }
[ -x "$VENV/bin/python" ] || { log "缺 venv：$VENV"; exit 1; }

PY="$VENV/bin/python"
export PYTHONPATH="$RELEASE/src:$RELEASE"
export PREFILTERED_SYMBOLS=1
export AQSP_SQLITE_ALLOW_EMPTY_SYMBOLS=1
export AQSP_IC_EXTRA_FACTORS="$EXTRA_FACTORS"

log "release sha = $(cat "$RUNNER_ROOT/RELEASE_SHA" 2>/dev/null || echo unknown)"
log "窗口 window=$WINDOW_DAYS symbols≤$MAX_SYMBOLS step=$STEP extra=[$EXTRA_FACTORS] timeout=${TIMEOUT_SEC}s"

# 2) 跑 IC 诊断（带超时硬墙；超时/失败都不留 IC_READY）。
#    timeout 命令缺失（如 macOS 无 coreutils 时）自动降级为无超时直跑，
#    并在日志里标注——生产 runner 为 GNU/Linux，timeout 恒在。
rc=0
if command -v timeout >/dev/null 2>&1; then
  timeout "$TIMEOUT_SEC" "$PY" "$RELEASE/scripts/ic_diagnosis.py" \
    --db "$DATA" \
    --output-dir "$OUT_DIR" \
    --window-days "$WINDOW_DAYS" \
    --max-symbols "$MAX_SYMBOLS" \
    --step "$STEP" \
    --extra-factors "$EXTRA_FACTORS" \
    >>"$OUT_DIR/ic_run.log" 2>&1 || rc=$?
else
  log "⚠️ 无 timeout 命令，无超时墙直跑（仅开发环境可接受）"
  "$PY" "$RELEASE/scripts/ic_diagnosis.py" \
    --db "$DATA" \
    --output-dir "$OUT_DIR" \
    --window-days "$WINDOW_DAYS" \
    --max-symbols "$MAX_SYMBOLS" \
    --step "$STEP" \
    --extra-factors "$EXTRA_FACTORS" \
    >>"$OUT_DIR/ic_run.log" 2>&1 || rc=$?
fi

if [ "$rc" -eq 0 ] && [ -f "$OUT_DIR/IC_READY" ]; then
  asof="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["as_of"])' \
    "$OUT_DIR/factor_ic_latest.json" 2>/dev/null || echo '?')"
  log "IC 诊断完成 → $OUT_DIR（as-of=${asof}）"
  log "已标记 IC_READY（prod 可用 fetch_ic_diagnosis.sh 拉取）"
else
  # 失败/超时：清掉陈旧 IC_READY，防 prod 拉到旧产物当新的
  rm -f "$OUT_DIR/IC_READY"
  log "IC 诊断未成功（rc=$rc），已清理旧 IC_READY，跳过回传；exit 0 不阻断"
fi
exit 0
