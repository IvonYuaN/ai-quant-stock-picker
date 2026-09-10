#!/usr/bin/env bash
# gate_finalize.sh — 计算节点(runner)侧 gate 收尾（常开，不依赖本地 Mac）。
#
# 触发：cron 每 15 分钟轮询；cron 条目与本脚本各持一把 flock（锁文件不同，避免自锁）。
#
# 设计要点（2026-09-10 事故修复）：
#   1) 单实例：cron 外层 flock + 脚本内层 flock，杜绝"每 15 分钟叠加一个进程"。
#      事故当天曾累积 16+ 个证据进程抢满 8GB 内存互相 OOM，最终 0 产出。
#   2) 步骤哨兵：SUMMARY_DONE / EVIDENCE_DONE 各自落盘，FINALIZED **只在两步都完成后**
#      才写。旧版把 FINALIZED 写在耗时步骤之前，下一轮 cron 读不到就误判"未完成"再拉起一个。
#   3) 异步 + 断点续跑：证据三档（current_soft/hard_8pct/hold_3d）各起一个后台进程，
#      单档结果原子落 json；三档齐全后合并成 md。任一档被 OOM 杀掉也不丢已完成档位。
#   3b) 证据用**独立 cache**（evidence_cache.db）：证据脚本会写 cache，与 gate 共用
#      生产缓存时并发读写会互相污染（同配置曾出现 −13.75%/−12.58%/−12.34% 三种读数）。
#   4) legacy 兼容：旧版同步阻塞实例仍在跑时本轮只等待、不重复启动；若 md 已由其产出且
#      含"汇总对比"段落，直接补记 EVIDENCE_DONE，避免白跑 ~1.5h。
#
# 用法（cron 自动，无需手动）：
#   */15 * * * * flock -xn /tmp/aqsp-gate-finalize.lock -c /opt/aqsp-runner/gate_run/gate_finalize.sh >> /opt/aqsp-runner/gate_run/finalize.log 2>&1
set -uo pipefail

RUNNER_ROOT="${RUNNER_ROOT:-/opt/aqsp-runner}"
OUT="$RUNNER_ROOT/gate_run"
RELEASE="$RUNNER_ROOT/aqsp-scheduler-current"
VENV="${VENV:-$RUNNER_ROOT/venv}"
PY="$VENV/bin/python"
LOCK="${LOCK:-/tmp/aqsp-gate-finalize.lock}"
INNER_LOCK="$LOCK.inner"
PARTS="$OUT/evidence_parts"
# 证据用独立 cache：证据脚本会**写** cache（已实测 mtime 变化）。若与 gate 共用
# walkforward_raw_production_cache.db，多个证据进程并发读写同一库会互相污染
# （2026-09-10 事故中同配置现 −13.75%/−12.58%/−12.34% 三种读数）。独立库同时
# 保证证据可复现、且不污染 gate 的生产缓存。
EVIDENCE_CACHE="$OUT/evidence_cache.db"
STOPS=(current_soft hard_8pct hold_3d)
START="2023-09-09"
END="2026-09-08"

log() { printf '[finalize %s] %s\n' "$(date '+%F %T')" "$*" >&2; }

# 0) 单实例锁（cron 外层 flock 之外的第二道保险；锁文件与外层不同，避免自己锁自己）
exec 9>"$INNER_LOCK" || { log "无法打开锁文件 $INNER_LOCK"; exit 1; }
if ! flock -xn 9; then log "已有 finalize 在跑（$INNER_LOCK 未释放），跳过"; exit 0; fi

# 1) 前置：gate 跑完
[ -f "$OUT/RESULT_READY" ] || { log "RESULT_READY 未出（gate 进行中），跳过"; exit 0; }
[ -x "$PY" ] || { log "缺 venv：$PY"; exit 1; }

# 2) 运行环境（与 runner_gate.sh 同构；证据脚本不收 --db，须显式指 db 路径）
export PYTHONPATH="$RELEASE/src:$RELEASE"
export PREFILTERED_SYMBOLS=1
export AQSP_SQLITE_ALLOW_EMPTY_SYMBOLS=1
export AQSP_SQLITE_DB_PATH="$RUNNER_ROOT/data/astocks_raw.db"

# 3) 抽数（幂等，已完成则跳过）
if [ -f "$OUT/SUMMARY_DONE" ] && [ -s "$OUT/gate_summary.md" ]; then
  : # 已完成，静默跳过
elif [ -f "$OUT/extract_gate_summary.py" ] && [ -f "$OUT/walkforward_gate.json" ]; then
  if "$PY" "$OUT/extract_gate_summary.py" \
    --gate "$OUT/walkforward_gate.json" --report "$OUT/report.md" \
    --output "$OUT/gate_summary.md"; then
    date -u '+%Y-%m-%dT%H:%M:%SZ' > "$OUT/SUMMARY_DONE"
    log "抽数完成 -> gate_summary.md"
  else
    log "extract 失败(非致命)"
  fi
fi

# 4) 证据（item 3 红线项独立样本验证：止损 8% vs 持有 3 天）
if [ -f "$OUT/EVIDENCE_DONE" ]; then
  : # 已完成，静默跳过
elif [ ! -f "$OUT/stop_loss_exit_evidence.py" ] || [ ! -f "$OUT/evidence_symbols.txt" ]; then
  log "证据脚本/标的池缺失，跳过"
else
  # 4a) legacy 兼容：旧版同步阻塞实例仍在跑 → 只等，不重复启动
  legacy="$(ps -eo args | grep "stop_loss_exit_evidence.py" | grep -v grep | grep -v -e "--only" || true)"
  if [ -n "$legacy" ]; then
    log "legacy 同步实例仍在跑，本轮不启动新档位（等它产出 md）"
  elif [ -s "$OUT/stop_loss_evidence.md" ] && grep -q "汇总对比" "$OUT/stop_loss_evidence.md"; then
    log "检测到已产出的 stop_loss_evidence.md（legacy 实例产出），补记 EVIDENCE_DONE"
    date -u '+%Y-%m-%dT%H:%M:%SZ' > "$OUT/EVIDENCE_DONE"
  else
    mkdir -p "$PARTS"
    for name in "${STOPS[@]}"; do
      part="$PARTS/$name.json"
      pidfile="$PARTS/$name.pid"
      [ -s "$part" ] && continue
      if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null; then
        continue
      fi
      nohup "$PY" "$OUT/stop_loss_exit_evidence.py" \
        --start "$START" --end "$END" \
        --cache-path "$EVIDENCE_CACHE" \
        --symbols-file "$OUT/evidence_symbols.txt" \
        --only "$name" --json-out "$part" --output /dev/null \
        >>"$PARTS/$name.log" 2>&1 9>&- &
      # 9>&- 必须带：否则后台档位进程会继承本脚本 flock 持有的 fd 9，
      # 父进程退出后锁仍被子进程持有，后续 cron 全部判"已有 finalize 在跑"而跳过，
      # 三档跑完也永远不会触发合并（2026-09-10 实测踩到）。
      echo $! >"$pidfile"
      log "启动档位 $name (pid $!)"
    done

    # 4b) 三档齐全才合并，避免产出半张表被当成完整证据
    ready=1
    for name in "${STOPS[@]}"; do [ -s "$PARTS/$name.json" ] || ready=0; done
    if [ "$ready" -eq 1 ]; then
      if "$PY" "$OUT/stop_loss_exit_evidence.py" \
        --from-json "$PARTS/current_soft.json" "$PARTS/hard_8pct.json" "$PARTS/hold_3d.json" \
        --output "$OUT/stop_loss_evidence.md"; then
        date -u '+%Y-%m-%dT%H:%M:%SZ' > "$OUT/EVIDENCE_DONE"
        log "证据合并完成 -> stop_loss_evidence.md"
      else
        log "evidence 合并失败(非致命)"
      fi
    else
      log "证据档位未齐全，等待下一轮"
    fi
  fi
fi

# 5) FINALIZED：两步都完成才写（旧版"先写标记再跑耗时步骤"的误标记已由哨兵取代）
if [ -f "$OUT/SUMMARY_DONE" ] && [ -f "$OUT/EVIDENCE_DONE" ]; then
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$OUT/FINALIZED"
  log "finalize 完成 -> gate_summary.md / stop_loss_evidence.md"
else
  log "finalize 进行中：summary=$([ -f "$OUT/SUMMARY_DONE" ] && echo done || echo pending) evidence=$([ -f "$OUT/EVIDENCE_DONE" ] && echo done || echo pending)"
fi
