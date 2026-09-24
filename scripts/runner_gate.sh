#!/usr/bin/env bash
# runner_gate.sh — 在计算节点(runner)上执行：校验同步产物 → 跑 walkforward gate → 回传 prod。
#
# 用法（在 runner 上）：
#   [GRID_PROFILE=stable_plus] [LOOKBACK_YEARS=3] [BATCH_SIZE=500] \
#   bash /opt/aqsp-runner/aqsp-scheduler-current/scripts/runner_gate.sh
#   （不需要 BACK_HOST：结果不回传，由 prod 用 runner_fetch.sh 主动 pull）
#
# 关键参数：
#   BATCH_SIZE   prod 是 200（内存逼出来的），runner 有余量可开 500~1000 提速
#   TIMEOUT_SEC  默认 57600（16h）。实测（2026-09-19，3y/stable_plus/20 期，逐次时间戳可查）：
#                门禁跑的是 **9 次串行完整 walk-forward**（1 次 prelude + stable_plus 的 8 个
#                变体；cli.py::_run_walkforward_grid_cscv 里是 `for variant in variants:`），
#                每次约 1h16m（20 期，≈3.8 min/期），节奏全程平稳，合计约 11.5h。
#                旧默认 36000（10h）在第 8 次 walkforward 的第 18 期被杀、无 gate 产物。
#                ⇒ 瓶颈是「9 次串行回测」这个总量：降 batch 无效（每次的 20 期是固定的），
#                  减变体不行（N=8 已是 MIN_CSCV_VARIANTS 下限），故只能加超时。
#                  16h 对约 11.5h 的实际需求留约 40% 余量。
#                （参考 2026-09-09：19 期/变体、约 49min/变体，9 次约 7h41m，旧预算内跑完。）
#   END_DATE     必须 ≤ 库内 MAX(trade_date)，否则父脚本 BLOCK
#   MAX_LOAD1    1 分钟 load 上限（默认 4）。runner 是共享业务机，超限让位并跳过本轮。
#   LOAD_GUARD=0 关闭让位守卫（仅诊断用；生产排期必须开着）
#
# 建议在 cron 里再套 nice/ionice，进一步降低对业务的抢占：
#   nice -n 19 ionice -c2 -n7 bash .../runner_gate.sh
set -euo pipefail

RUNNER_ROOT="${RUNNER_ROOT:-/opt/aqsp-runner}"
RELEASE="$RUNNER_ROOT/aqsp-scheduler-current"
VENV="${VENV:-$RUNNER_ROOT/venv}"
DATA="$RUNNER_ROOT/data/astocks_raw.db"
OUT="$RUNNER_ROOT/gate_run"

GRID_PROFILE="${GRID_PROFILE:-stable_plus}"
LOOKBACK_YEARS="${LOOKBACK_YEARS:-3}"
TIMEOUT_SEC="${TIMEOUT_SEC:-57600}"
BATCH_SIZE="${BATCH_SIZE:-500}"        # prod 只能 200（内存逼的），runner 8G 可开 500
MIN_MEMORY_GIB="${MIN_MEMORY_GIB:-4}"  # 预检阈值，runner 8G 无压力；prod 只能 1.5
END_DATE="${END_DATE:-}"

# 共享业务机守卫。runner 上同时跑着 ifidy/lanshe 三个线上业务（PM2），AQSP 只是租户，
# 所以 load 高时必须让位。跳过 ≠ 失败，故 exit 0 并把原因写进 skip.log，
# 供 prod 侧 fetch 时发现"这周没跑"而不是"跑了但没结果"。
LOAD_GUARD="${LOAD_GUARD:-1}"
MAX_LOAD1="${MAX_LOAD1:-4}"                       # 1 分钟 load 上限
LOAD_GUARD_WAIT_SEC="${LOAD_GUARD_WAIT_SEC:-300}" # 峰值宽限：先等再判，避免一次瞬时抖动白丢一周
LOADAVG_PATH="${LOADAVG_PATH:-/proc/loadavg}"     # 可覆盖，便于测试

# 结果不主动回传：runner 落盘本地，由 prod 用 runner_fetch.sh 主动 pull
# （runner 因此不需要任何连回生产的 SSH 权限）
#
# 跑完写一个 READY 标记，供 prod 侧 fetch 判断"有新结果"
MARK_READY="${MARK_READY:-1}"
BACK_DIR="${BACK_DIR:-/opt/aqsp/data/gate_run}"

log() { printf '[gate %s] %s\n' "$(date '+%F %T')" "$*"; }
mkdir -p "$OUT"

# 0) 共享业务机守卫：load 高就让位给 ifidy/lanshe，不要跟业务抢盘。
#    放在最前面：盒子忙的时候连 release/DB 都不要去碰。
if [ "$LOAD_GUARD" = "1" ]; then
  guard_deadline=$(( $(date +%s) + LOAD_GUARD_WAIT_SEC ))
  while :; do
    load1="$(cut -d' ' -f1 "$LOADAVG_PATH")"
    if awk -v l="$load1" -v m="$MAX_LOAD1" 'BEGIN { exit !(l <= m) }'; then
      log "load1=$load1 ≤ $MAX_LOAD1，继续"
      break
    fi
    if [ "$(date +%s)" -ge "$guard_deadline" ]; then
      printf '%s load1=%s > %s，让位于业务，跳过本轮\n' \
        "$(date '+%F %T')" "$load1" "$MAX_LOAD1" >> "$OUT/skip.log"
      log "load1=$load1 > $MAX_LOAD1，让位于业务，跳过本轮（见 $OUT/skip.log）"
      exit 0
    fi
    log "load1=$load1 > $MAX_LOAD1，等待 30s 后重试"
    sleep 30
  done
fi

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
