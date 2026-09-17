#!/usr/bin/env bash
# T3 补位重跑：gate_run_wf001_3y（3y 对照组 WF-001 基线）
#
# 背景：2026-09-17 12:08 的四路并行跑批中，本路被全局 production lock 竞态杀掉
#   （BLOCK: production walk-forward lock exists: /opt/.locks/walkforward-production.lock，
#    holder=pid 6164 即 htf_mr_3y）。缺本路 → compare_t3_dual_window.py 无法运行。
# 对策：本路单独跑，并用独立 --lock-path，与在跑的 3 路互不干扰。
# 参数与 t3_parallel_gate.sh 的 run_one 完全一致（同一 src release / 同一 --end），
# 唯一差异 = 独立 lock 路径；可复用上次遗留的 symbols.json。
set -u
R=/opt/aqsp-runner
SRC_REL="$R/releases/htf-mr-swap"
OUT="$R/gate_run_wf001_3y"
PYBIN="$R/venv/bin/python"
SCRIPT="$R/t3_gate_script.py"
LOG="$R/t3_wf001_3y_resume.log"

export PYTHONPATH="$SRC_REL/src:$SRC_REL"
export AQSP_SQLITE_ALLOW_EMPTY_SYMBOLS=1
export PYTHONUNBUFFERED=1
export PYTHONHASHSEED=0

log(){ printf '[t3w %s] %s\n' "$(date -u '+%F %T')" "$*" | tee -a "$LOG"; }

mkdir -p "$OUT" "$R/.locks"

# 幂等守卫（必须在任何 rm 清理之前）：已有同目录 gate 进程在跑则不重复拉起、不动它的产物
RUNNING="$(pgrep -f "t3_gate_script.py .*gate_run_wf001_3y" 2>/dev/null | tr '\n' ' ')"
if [ -n "${RUNNING}" ]; then
  log "已存在 wf001_3y gate 进程（pid=${RUNNING}），本脚本不重复拉起、不清理其产物。"
  exit 0
fi

# 清掉上次失败留下的半成品状态；保留 symbols.json 以复用股票池扫描
rm -f "$OUT/status.json" "$OUT/walkforward_gate.json" "$OUT/report.md" "$OUT/RESULT_READY" 2>/dev/null
if [ -s "$OUT/symbols.json" ]; then
  log "reuse existing symbols.json ($(stat -c %s "$OUT/symbols.json") bytes)"
else
  log "symbols.json 缺失，将由脚本重建"
fi

log "=== START gate_run_wf001_3y (profile=stable_plus lookback=3y db=astocks_raw.db end=2026-09-09, own lock) ==="
cd "$SRC_REL" || { log "cd $SRC_REL 失败"; exit 3; }

"$PYBIN" "$SCRIPT" \
  --grid-profile stable_plus --lookback-years 3 \
  --db "$R/data/astocks_raw.db" \
  --stream-batch-size 500 --min-memory-gib 1 --min-symbols 3000 \
  --timeout-seconds 90000 \
  --gate-path "$OUT/walkforward_gate.json" --report "$OUT/report.md" \
  --cache-path "$OUT/cache.db" --log "$OUT/run.log" \
  --status-path "$OUT/status.json" --symbols-cache-path "$OUT/symbols.json" \
  --lock-path "$R/.locks/walkforward-wf001-3y.lock" \
  --end 2026-09-09 >> "$OUT/launch.log" 2>&1
rc=$?

if [ -s "$OUT/walkforward_gate.json" ] && [ -s "$OUT/report.md" ]; then
  log "=== DONE gate_run_wf001_3y rc=$rc (gate.json + report.md OK) ==="
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$OUT/RESULT_READY"
else
  log "=== FAIL gate_run_wf001_3y rc=$rc (缺失产物) ==="
  tail -15 "$OUT/launch.log" | tee -a "$LOG"
fi
