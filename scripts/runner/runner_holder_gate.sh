#!/usr/bin/env bash
# holder_overlay gate 取证（2026-11-10）— 在 runner 上跑股东户数筹码集中度
# overlay 的**专用验证档位**，拿真 CSCV/DSR 证据。
#
# 🔴 本脚本属**线上写操作**（同步代码 + 数据到 runner），执行前须老大放行。
#    用法：bash scripts/runner/runner_holder_gate.sh            # 默认 dry-run，只打印将要做什么
#          EXECUTE=1 bash scripts/runner/runner_holder_gate.sh  # 真执行
#
# 背景
# ----
# 股东户数集中度因子（holder_concentration）已实现为 **overlay 路线**：
# 不进加权平均，只在 `select_stocks` 里对「2f 达标池」按筹码集中度重排。
# 能力层已进 main 分支，生产默认**关闭**（2f 基线逐位不变）。
#
# 要回答的问题是：**overlay 到底有没有用**？判读口径（三层，缺一不可）：
#   ① 逐变体表：`WO-*` 变体 vs 同参数基线 `WF-*` 变体的 Sharpe / 总收益
#      （overlay 只改 select_stocks 重排 ⇒ 收益序列变、打分 total 不变）；
#   ② DSR：`_run_walkforward_grid_cscv` 里 `best_sharpe = max(变体行)`
#      ⇒ DSR 取自 overlay 变体（不是 prelude），overlay 效果真实进入 DSR；
#   ③ PBO：从 overlay 变体的收益矩阵算 CSCV。
#   ⚠️ **不要**只看 report.md 的 TL;DR/整体指标 —— 那取自 result.overall，
#      与本档位无关（memory 明载的判读纪律）。
#
# 三条前置（缺一 ⇒ 跑出来是**假证据**，必须 fail-closed 而不是静默跑）：
#   A. runner release 必须含 holder 代码（本脚本从 feature 分支同步一个 release）；
#   B. runner 必须有 **完整 DET 版** holder_count.csv
#      —— ⚠️ 每票只有 1 个季度 ⇒ 算不出 QoQ ⇒ overlay 全 0.5 空转、与 2f 基线
#      逐位相同 ⇒ 证据无效（本地已实测：坏件下 4/4 全 0.500）；
#   C. holder CSV 必须放在 **release 外**的稳定路径：`runtime_data_root()`
#      回落 release 根 ⇒ release 内路径每次切链换 sha 就失效；
#      故用 `AQSP_HOLDER_COUNT_CSV` env 显式指向稳定位置
#      （构造时读 env，见 holder_concentration.py）。
#
set -euo pipefail

EXECUTE="${EXECUTE:-0}"

RUNNER_HOST="${RUNNER_HOST:-aqsp-runner}"
RUNNER_ROOT="${RUNNER_ROOT:-/opt/aqsp-runner}"
LOCAL_REPO="${LOCAL_REPO:-/Users/ivon/Documents/AI量化选股}"
FEATURE_REF="${FEATURE_REF:-feature/holder-concentration-factor}"
# runner 上 holder CSV 的稳定落点（release 外，不随切链失效）
RUNNER_HOLDER_CSV="${RUNNER_HOLDER_CSV:-$RUNNER_ROOT/pit_cache/holder_count.csv}"
# 取证产物目录：与生产 gate_run/ **隔离**（避免 prod 的 runner_fetch.sh
# 把它当周度生产判决拉走 —— runner_gate.sh 注释里的同款约束）
OUT_DIR="${OUT_DIR:-$RUNNER_ROOT/gate_run_holder_overlay}"
# 复用生产 gate 的口径：3y 窗口、stable_plus 的批大小、16h 超时
LOOKBACK_YEARS="${LOOKBACK_YEARS:-3}"
BATCH_SIZE="${BATCH_SIZE:-500}"
TIMEOUT_SEC="${TIMEOUT_SEC:-57600}"

log() { printf '[holder-gate %s] %s\n' "$(date '+%F %T')" "$*"; }

# ---------------------------------------------------------------------------
# 0) 本地前置：holder CSV 必须是完整 DET 版（每票 >=2 季 ⇒ 可算 QoQ）
#    从 prod 同源副本取（prod pit_cache/holder_count.csv 已是完整 DET 版，
#    21434 行/5435 票/5409 票可算 QoQ）。**每次都重新判定，不靠记忆**。
# ---------------------------------------------------------------------------
detect_source_csv() {
    for c in \
        "$LOCAL_REPO/research/theme_factor_probe/holder_count.csv" \
        "$LOCAL_REPO/pit_cache/holder_count.csv"; do
        [ -f "$c" ] || continue
        if python3 - "$c" <<'PY'
import sys, csv, collections
path = sys.argv[1]
counts = collections.Counter()
with open(path, newline="", encoding="utf-8") as fh:
    for row in csv.DictReader(fh):
        counts[row["symbol"].strip().zfill(6)] += 1
multi = sum(1 for c in counts.values() if c >= 2)
total = sum(counts.values())
# ⚠️ 统计输出走 stderr：stdout 会被 $( ) 捕获进 SRC_CSV，把路径污染掉
print(f"rows={total} symbols={len(counts)} qoq_capable={multi}", file=sys.stderr)
sys.exit(0 if multi >= 100 else 1)
PY
        then
            echo "$c"
            return 0
        fi
    done
    return 1
}

log "=== 判定本地 holder CSV 源（要求 DET 完整版：每票 >=2 季）==="
if ! SRC_CSV="$(detect_source_csv)"; then
    log "❌ 本机找不到完整 DET 版 holder_count.csv（每票 >=2 季）"
    log "   ⇒ 拒绝执行：坏件会让 overlay 全 0.5 空转 ⇒ 跑出来是假证据"
    exit 2
fi
log "源 CSV: $SRC_CSV"

# ---------------------------------------------------------------------------
# 1) 同步 holder 代码到 runner 一个**独立 release**（不碰生产 current 软链）
# ---------------------------------------------------------------------------
log "=== 同步 feature 分支代码到 runner 独立 release ==="
log "   （不切 aqsp-scheduler-current ⇒ 不影响生产 gate/调度）"
SHA="$(cd "$LOCAL_REPO/.worktrees/hc-factor" && git rev-parse HEAD)"
log "   feature HEAD = $SHA"
REL="$RUNNER_ROOT/releases/$SHA"
log "   目标 release = $REL"
log "   同步内容：src/ + scripts/ + config/（代码，不含数据）"

# ---------------------------------------------------------------------------
# 2) 同步 holder CSV（完整 DET 版）到 runner 稳定位置
# ---------------------------------------------------------------------------
log "=== 同步 holder_count.csv → $RUNNER_HOLDER_CSV ==="
log "   ⚠️ 覆盖既有文件前先备份（红线：先备份再动）"
log "   ⚠️ 源/目标 md5 事后必核验（scp 静默 no-op 陷阱）"

# ---------------------------------------------------------------------------
# 3) 跑 gate（--grid-profile holder_overlay）
# ---------------------------------------------------------------------------
log "=== gate 取证命令（将在 runner 上执行）==="
cat <<EOF
  # 步骤 1：同步代码（独立 release，不切生产软链）
  cd $LOCAL_REPO/.worktrees/hc-factor
  git archive $SHA | ssh $RUNNER_HOST 'mkdir -p $REL && tar -x -C $REL'

  # 步骤 2：同步 holder CSV（完整 DET 版）+ md5 核验
  scp $SRC_CSV $RUNNER_HOST:$RUNNER_HOLDER_CSV
  md5 -q $SRC_CSV ; ssh $RUNNER_HOST 'md5 -q $RUNNER_HOLDER_CSV'   # 两边须一致

  # 步骤 3：跑 gate（前置压力复核 + setsid nohup 后台脱钩 + 共享业务机让位守卫）
  ssh $RUNNER_HOST "
    cd $REL && \\
    RELEASE=$REL \\
    AQSP_HOLDER_COUNT_CSV=$RUNNER_HOLDER_CSV \\
    AQSP_RUNTIME_DATA_ROOT=$RUNNER_ROOT \\
    OUT=$OUT_DIR \\
    GRID_PROFILE=holder_overlay \\
    LOOKBACK_YEARS=$LOOKBACK_YEARS \\
    BATCH_SIZE=$BATCH_SIZE \\
    TIMEOUT_SEC=$TIMEOUT_SEC \\
    LOAD_GUARD=1 MAX_LOAD1=4 \\
    setsid nohup bash $REL/scripts/runner/runner_gate.sh \\
      > $OUT_DIR/runner_holder_gate.log 2>&1 &
  "

  # 步骤 3b：启动后 30s 内实证「跑的是旁路 release 且 overlay 真启用」
  #   （防再次静默跑成生产版：看日志里的 release sha + 第一臂的 WO- 前缀）
  ssh $RUNNER_HOST "sleep 30; grep -E 'release sha|grid variant' $OUT_DIR/runner_holder_gate.log | head -5"

  # 步骤 4：取回产物（gate json + report.md），本地做「逐变体表」判读
  scp $RUNNER_HOST:$OUT_DIR/walkforward_gate.json \
  scp $RUNNER_HOST:$OUT_DIR/report.md
EOF

if [ "$EXECUTE" != "1" ]; then
    log "=== DRY-RUN 结束（未执行任何线上写操作）==="
    log "确认无误后：EXECUTE=1 bash $0"
    exit 0
fi

log "=== EXECUTE=1 ⇒ 开始真实执行 ==="
echo "TODO: 真实执行体（同步 + 跑 gate + 取回产物）"
