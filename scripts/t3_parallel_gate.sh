#!/usr/bin/env bash
# T3 parallel relaunch: run all 4 gate walks concurrently (separate dirs/DBs).
# Kills sequential bottleneck; VM has 7.9GB RAM / 4 cores, each run ~400MB RSS.
#
# 2026-09-17 FIX: 每路必须带独立 --lock-path。
# 根因：wrapper 的 production lock 是 mkdir 型全局互斥（PROJECT_ROOT/.locks/
# walkforward-production.lock，meta.json 记 pid）。4 路同用默认锁 → 竞态：赢家写 meta，
# 输家读到 active 即 BLOCK 退出。首次并行跑批因此丢掉 gate_run_wf001_3y
# （12:08:47 BLOCK: lock exists, held by pid 6164=htf_mr_3y）。
# 四路本就设计为并发 + 各自独立产物目录，故各给独立锁路径是正解。
set -u
R=/opt/aqsp-runner
SRC_REL="$R/releases/htf-mr-swap"          # aqsp code (has htf_mr + stable_plus profiles)
VENV="$R/venv"
PY="$VENV/bin/python"
SCRIPT="$R/t3_gate_script.py"              # patched: choices+=htf_mr, injects --benchmark-symbol ""
DB3="$R/data/astocks_raw.db"
DB5="$R/data/astocks_raw_full.db"
MASTER="$R/t3_parallel.log"
export PYTHONPATH="$SRC_REL/src:$SRC_REL"
export AQSP_SQLITE_ALLOW_EMPTY_SYMBOLS=1
export PYTHONUNBUFFERED=1
export PYTHONHASHSEED=0
mkdir -p "$R/.locks"

log(){ printf '[t3p %s] %s\n' "$(date -u '+%F %T')" "$*" | tee -a "$MASTER"; }

run_one(){
  local name="$1" profile="$2" ly="$3" db="$4" endarg="$5"
  local OUT="$R/$name"
  mkdir -p "$OUT"
  rm -f "$OUT/cache.db" "$OUT/launch.log" "$OUT/run.log" "$OUT/status.json" \
        "$OUT/symbols.json" "$OUT/walkforward_gate.json" "$OUT/report.md" "$OUT/RESULT_READY" 2>/dev/null
  log "=== START $name (profile=$profile lookback=${ly}y db=$(basename "$db") ${endarg:-no-end}) ==="
  cd "$SRC_REL" || { log "cd $SRC_REL 失败"; return 3; }
  "$PY" "$SCRIPT" \
    --grid-profile "$profile" --lookback-years "$ly" --db "$db" \
    --stream-batch-size 500 --min-memory-gib 1 --min-symbols 3000 \
    --timeout-seconds 90000 \
    --gate-path "$OUT/walkforward_gate.json" --report "$OUT/report.md" \
    --cache-path "$OUT/cache.db" --log "$OUT/run.log" \
    --status-path "$OUT/status.json" --symbols-cache-path "$OUT/symbols.json" \
    --lock-path "$R/.locks/walkforward-$name.lock" \
    $endarg >> "$OUT/launch.log" 2>&1
  local rc=$?
  if [ -s "$OUT/walkforward_gate.json" ] && [ -s "$OUT/report.md" ]; then
    log "=== DONE $name rc=$rc (gate.json + report.md OK) ==="
    date -u '+%Y-%m-%dT%H:%M:%SZ' > "$OUT/RESULT_READY"
  else
    log "=== FAIL $name rc=$rc (缺失产物；tail launch.log) ==="
    tail -15 "$OUT/launch.log" | tee -a "$MASTER"
  fi
}

log "T3 PARALLEL begin; RELEASE_SHA=$(cat "$R/RELEASE_SHA" 2>/dev/null) SRC_REL=$SRC_REL"
run_one gate_run_5y_server  stable_plus 5 "$DB5" "--end 2026-04-30" &
P1=$!
run_one gate_run_wf001_3y   stable_plus 3 "$DB3" "--end 2026-09-09" &
P2=$!
run_one gate_run_htf_mr_3y  htf_mr     3 "$DB3" "--end 2026-09-09" &
P3=$!
run_one gate_run_htf_mr_5y  htf_mr     5 "$DB5" "--end 2026-04-30" &
P4=$!
log "launched 4 jobs pids=$P1 $P2 $P3 $P4"
wait $P1 $P2 $P3 $P4
log "T3 PARALLEL ALL DONE"
date -u '+%Y-%m-%dT%H:%M:%SZ' > "$R/t3_parallel_DONE"
