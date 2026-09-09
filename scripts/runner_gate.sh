#!/usr/bin/env bash
# runner_gate.sh — 在计算节点(runner)上执行：校验同步产物 → 跑 walkforward gate → 回传 prod。
#
# 用法（在 runner 上）：
#   [GRID_PROFILE=stable_plus] [LOOKBACK_YEARS=3] [BATCH_SIZE=500] \
#   BACK_HOST=root@8.130.124.238 bash /opt/aqsp-runner/scripts/runner_gate.sh
#
# 关键参数：
#   BATCH_SIZE   prod 是 200（内存逼出来的），runner 有余量可开 500~1000 提速
#   TIMEOUT_SEC  stable_plus = 8 variant × 19 期 = 152 期，必须 ≥ 36000（prod 曾因 14400 超时白跑）
#   END_DATE     必须 ≤ 库内 MAX(trade_date)，否则父脚本 BLOCK
set -euo pipefail

RUNNER_ROOT="${RUNNER_ROOT:-/opt/aqsp-runner}"
RELEASE="$RUNNER_ROOT/aqsp-scheduler-current"
VENV="${VENV:-$RUNNER_ROOT/venv}"
DATA="$RUNNER_ROOT/data/astocks_raw.db"
OUT="$RUNNER_ROOT/gate_run"

GRID_PROFILE="${GRID_PROFILE:-stable_plus}"
LOOKBACK_YEARS="${LOOKBACK_YEARS:-3}"
TIMEOUT_SEC="${TIMEOUT_SEC:-36000}"
BATCH_SIZE="${BATCH_SIZE:-500}"        # prod 只能 200（内存逼的），runner 8G 可开 500
MIN_MEMORY_GIB="${MIN_MEMORY_GIB:-4}"  # 预检阈值，runner 8G 无压力；prod 只能 1.5
END_DATE="${END_DATE:-}"

# 结果不主动回传：runner 落盘本地，由 prod 用 runner_fetch.sh 主动 pull
# （runner 因此不需要任何连回生产的 SSH 权限）
#
# 跑完写一个 READY 标记，供 prod 侧 fetch 判断"有新结果"
MARK_READY="${MARK_READY:-1}"
BACK_DIR="${BACK_DIR:-/opt/aqsp/data/gate_run}"

log() { printf '[gate %s] %s\n' "$(date '+%F %T')" "$*"; }
mkdir -p "$OUT"

# 1) 前置校验：代码 / 数据 / venv
[ -d "$RELEASE" ] || { echo "缺 release：$RELEASE（先跑 runner_sync.sh）"; exit 1; }
[ -f "$DATA" ] || { echo "缺数据：$DATA（先跑 runner_sync.sh）"; exit 1; }
[ -x "$VENV/bin/python" ] || { echo "缺 venv：$VENV（见 docs/two-node-handoff.md §3）"; exit 1; }

PY="$VENV/bin/python"
export PYTHONPATH="$RELEASE/src:$RELEASE"
export PREFILTERED_SYMBOLS=1
export AQSP_SQLITE_ALLOW_EMPTY_SYMBOLS=1

log "release sha = $(cat "$RUNNER_ROOT/RELEASE_SHA" 2>/dev/null || echo unknown)"
$PY - "$DATA" <<'PY'
import sqlite3, sys
con = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
print("integrity:", con.execute("PRAGMA integrity_check").fetchone()[0])
print("rows     :", con.execute("SELECT COUNT(*) FROM daily_qfq").fetchone()[0])
print("range    :", con.execute("SELECT MIN(trade_date), MAX(trade_date) FROM daily_qfq").fetchone())
PY

# 2) 窗口：默认留空 = 让父脚本按 --lookback-years 自行推导（与 prod 口径逐位一致）。
#    若要显式指定，END_DATE 必须 ≤ 库内 MAX(trade_date)，否则父脚本 BLOCK。
WINDOW_ARGS=()
if [ -n "$END_DATE" ]; then
  WINDOW_ARGS=(--start "$START_DATE" --end "$END_DATE")
fi
log "窗口=${WINDOW_ARGS[*]:-自动(lookback=${LOOKBACK_YEARS}y)} profile=$GRID_PROFILE batch=$BATCH_SIZE"

# 3) 跑 gate（前台跑，cron 友好；要离线跑自己套 setsid nohup）
"$PY" "$RELEASE/scripts/run_production_walkforward_gate.py" \
  --grid-profile "$GRID_PROFILE" \
  --lookback-years "$LOOKBACK_YEARS" \
  --stream-batch-size "$BATCH_SIZE" \
  ${WINDOW_ARGS[@]+"${WINDOW_ARGS[@]}"} \
  --db "$DATA" \
  --min-memory-gib "$MIN_MEMORY_GIB" --min-symbols 3000 \
  --timeout-seconds "$TIMEOUT_SEC" \
  --status-path "$OUT/walkforward_production_status.json" \
  --gate-path "$OUT/walkforward_gate.json" \
  --cache-path "$OUT/walkforward_raw_production_cache.db" \
  --report "$OUT/report.md" \
  --log "$OUT/run.log" \
  --lock-path "$OUT/walkforward-production.lock" \
  --symbols-cache-path "$OUT/walkforward_production_symbols.json"

# 4) 出摘要
if [ -f "$OUT/extract_gate_summary.py" ]; then
  "$PY" "$OUT/extract_gate_summary.py" \
    --gate "$OUT/walkforward_gate.json" --report "$OUT/report.md" \
    --output "$OUT/gate_summary.md" || true
  log "摘要：$OUT/gate_summary.md"
fi

# 5) 落 READY 标记（不回传，等 prod 用 runner_fetch.sh 来拉）
if [ "$MARK_READY" = "1" ] && [ -f "$OUT/walkforward_gate.json" ]; then
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$OUT/RESULT_READY"
  log "已标记 RESULT_READY（prod 可用 runner_fetch.sh 拉取）"
fi
log "完成"
