#!/usr/bin/env bash
# T3 四跑批重新执行（runner 端，顺序执行，避免 4C/7.9G 过载）
#
# 背景：原 4 跑批被 09-12 VM 关机杀死，未写出 walkforward_gate.json / report.md。
#       T3 双窗口对比（compare_t3_dual_window.py）因此无产物可取。
#
# 关键设计：全部从 htf-mr-swap release 的 src/ 执行（PYTHONPATH 指向它）。
#   htf-mr-swap 是「部分 release」：只有 config/ data/ src/，没有 scripts/。
#   原 htf_mr 跑批就是用「完整 release 的 gate 脚本 + PYTHONPATH=htf-mr-swap/src」跑的，
#   本脚本复刻同一口径：gate 脚本取自完整 release（6011d633），但 aqsp 代码取自 htf-mr-swap。
#   该 src 同时含 stable_plus（WF-001）与 htf_mr 两个 profile，
#   因此「因子族 + min_total_score」是 WF-001 与 htf_mr 之间的【唯一】差异变量——
#   WF-001 用 --grid-profile stable_plus，htf_mr 用 --grid-profile htf_mr（htf_mr 内部烘焙 min_total_score=0.1）。
#   这正是 T3 方案 A「htf+mr 换 mom+tr」受控对照所需的口径。
#
# 红线：本脚本只在 runner 跑计算，不部署 / 不同步 prod。
set -u
R=/opt/aqsp-runner
SRC_REL="$R/releases/htf-mr-swap"            # 提供 aqsp 代码（含 htf_mr profile）
VENV="$R/venv"
PY="$VENV/bin/python"
# 修补版 gate 脚本（/tmp 提取自 feat/t3-htf-mr-swap 分支，已：①choices 加 htf_mr ②内联 --benchmark-symbol "" 跳过指数表获取）
SCRIPT="$R/t3_gate_script.py"
DB3="$R/data/astocks_raw.db"
DB5="$R/data/astocks_raw_full.db"
MASTER="$R/t3_relaunch.log"
export PYTHONPATH="$SRC_REL/src:$SRC_REL"
export AQSP_SQLITE_ALLOW_EMPTY_SYMBOLS=1
export PYTHONUNBUFFERED=1
export PYTHONHASHSEED=0

log(){ printf '[t3 %s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$MASTER"; }

run_one(){
  local name="$1" profile="$2" ly="$3" db="$4" endarg="$5"
  local OUT="$R/$name"
  mkdir -p "$OUT"
  # 清掉被杀跑批的残留，确保从头算、避免脏 cache 导致非确定性
  rm -f "$OUT/cache.db" "$OUT/launch.log" "$OUT/run.log" "$OUT/status.json" \
        "$OUT/symbols.json" "$OUT/walkforward_gate.json" "$OUT/report.md" "$OUT/RESULT_READY" 2>/dev/null
  log "=== START $name (profile=$profile lookback=${ly}y db=$(basename "$db") ${endarg:-no-end}) ==="
  cd "$SRC_REL" || { log "cd $SRC_REL 失败"; return 3; }
  "$PY" "$SCRIPT" \
    --grid-profile "$profile" \
    --lookback-years "$ly" \
    --db "$db" \
    --stream-batch-size 500 \
    --min-memory-gib 4 \
    --min-symbols 3000 \
    --timeout-seconds 90000 \
    --gate-path "$OUT/walkforward_gate.json" \
    --report "$OUT/report.md" \
    --cache-path "$OUT/cache.db" \
    --log "$OUT/run.log" \
    --status-path "$OUT/status.json" \
    --symbols-cache-path "$OUT/symbols.json" \
    $endarg \
    >> "$OUT/launch.log" 2>&1
  local rc=$?
  if [ -s "$OUT/walkforward_gate.json" ] && [ -s "$OUT/report.md" ]; then
    log "=== DONE $name rc=$rc (gate.json + report.md OK) ==="
  else
    log "=== FAIL $name rc=$rc (缺失产物；tail launch.log) ==="
    tail -15 "$OUT/launch.log" | tee -a "$MASTER"
  fi
}

log "T3 relaunch begin; runner RELEASE_SHA=$(cat "$R/RELEASE_SHA" 2>/dev/null) SRC_REL=$SRC_REL"
run_one gate_run_5y_server  stable_plus 5 "$DB5" "--end 2026-04-30"
run_one gate_run_wf001_3y   stable_plus 3 "$DB3" ""
run_one gate_run_htf_mr_3y  htf_mr     3 "$DB3" ""
run_one gate_run_htf_mr_5y  htf_mr     5 "$DB5" "--end 2026-04-30"
log "T3 relaunch ALL DONE"
date -u '+%Y-%m-%dT%H:%M:%SZ' > "$R/t3_relaunch_DONE"
