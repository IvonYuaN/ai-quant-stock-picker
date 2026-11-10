#!/usr/bin/env bash
# holder_overlay 收尾：取产物 → 跑预注册判定 → 出结论（一条命令）
#
# 用法：
#   bash scripts/runner/holder_overlay_verdict.sh              # 默认取产物+判定
#   bash scripts/runner/holder_overlay_verdict.sh --no-fetch   # 用本地已有产物
#   bash scripts/runner/holder_overlay_verdict.sh --progress   # 只看跑批进度
#
# 设计意图（2026-11-10）
# --------------------
# 跑批 13 臂串行约 16-19h，收尾时不该再靠临时 grep/scp 拼步骤 ⇒ 把
# 「取 gate json + report.md + 跑预注册判定」固化成一条命令，减少手抖与漏步。
#
# ⚠️ 判定口径全部在 `compare_holder_overlay.py` 里（含**预注册判据**：
#    A 逐臂 ≥5/8 改善且均值>0；B DSR 优于基线 −1.2463；半程拒判）。
#    本脚本只负责取数，**不改判定逻辑**。
set -uo pipefail

RUNNER_HOST="${RUNNER_HOST:-aqsp-runner}"
RUNNER_ROOT="${RUNNER_ROOT:-/opt/aqsp-runner}"
OUT_DIR="${OUT_DIR:-$RUNNER_ROOT/gate_run_holder_overlay}"
BASE_DIR="${BASE_DIR:-$RUNNER_ROOT/gate_run}"     # 生产基线（只读，勿写）
LOCAL_DIR="${LOCAL_DIR:-/tmp/holder_eval}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${PY:-python3}"

FETCH=1
PROGRESS=0
for arg in "$@"; do
  case "$arg" in
    --no-fetch) FETCH=0 ;;
    --progress) PROGRESS=1 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "未知参数: $arg" >&2; exit 2 ;;
  esac
done

log() { printf '[verdict %s] %s\n' "$(date '+%F %T')" "$*"; }
mkdir -p "$LOCAL_DIR"

# ---------------------------------------------------------------------------
# 进度模式：只看跑到第几臂（零写操作）
# ---------------------------------------------------------------------------
if [ "$PROGRESS" = "1" ]; then
  ssh "$RUNNER_HOST" "
    OUT='$OUT_DIR'
    echo '=== 已完成臂 ==='
    grep -E '^grid variant WO-.*: periods=' \$OUT/runner_holder_gate.log 2>/dev/null
    echo '=== 当前臂 ==='
    grep -E 'grid variant WO-.*/13' \$OUT/runner_holder_gate.log 2>/dev/null | tail -1
    echo '=== 状态 ==='
    grep -oE '\"status\": \"[a-z_]+\"' \$OUT/walkforward_production_status.json 2>/dev/null | head -1
    grep -oE 'elapsed=[0-9]+s' \$OUT/walkforward_production_status.json 2>/dev/null | head -1
  "
  exit 0
fi

# ---------------------------------------------------------------------------
# 1) 取产物（只读；缺 report.md 时退回用日志做增量判读）
# ---------------------------------------------------------------------------
HAVE_REPORT=0
if [ "$FETCH" = "1" ]; then
  log "从 runner 取产物 → $LOCAL_DIR"
  for pair in \
    "$BASE_DIR/report.md:baseline_report.md" \
    "$BASE_DIR/walkforward_gate.json:baseline_gate.json" \
    "$OUT_DIR/walkforward_gate.json:candidate_gate.json" \
    "$OUT_DIR/report.md:candidate_report.md" \
    "$OUT_DIR/runner_holder_gate.log:candidate.log"; do
    src="${pair%%:*}"; dst="${pair##*:}"
    if ! scp -q -o BatchMode=yes -o ConnectTimeout=25 "$RUNNER_HOST:${src}" "$LOCAL_DIR/${dst}" 2>/dev/null; then
      # ⚠️ 必须写 ${src} 而不是 $src：紧跟全角括号时 bash 会把「（」并入变量名
      # ⇒ set -u 下报 unbound variable（2026-11-10 实测踩过）。
      log "缺 ${src}（可能尚未写出）"
      rm -f "$LOCAL_DIR/${dst}"
    fi
  done
fi
[ -f "$LOCAL_DIR/candidate_report.md" ] && HAVE_REPORT=1

if [ ! -f "$LOCAL_DIR/baseline_report.md" ]; then
  log "❌ 缺基线 report（$BASE_DIR/report.md）—— 无法对比"
  exit 2
fi

# ---------------------------------------------------------------------------
# 2) 跑预注册判定（候选侧优先用 report，缺则用日志）
# ---------------------------------------------------------------------------
ARGS=(--baseline-report "$LOCAL_DIR/baseline_report.md")
if [ -f "$LOCAL_DIR/baseline_gate.json" ]; then
  ARGS+=(--baseline-gate "$LOCAL_DIR/baseline_gate.json")
fi
if [ -f "$LOCAL_DIR/candidate_gate.json" ]; then
  ARGS+=(--candidate-gate "$LOCAL_DIR/candidate_gate.json")
fi
if [ "$HAVE_REPORT" = "1" ]; then
  ARGS+=(--candidate-report "$LOCAL_DIR/candidate_report.md")
  log "候选侧用 report.md（完整表）"
else
  ARGS+=(--candidate-log "$LOCAL_DIR/candidate.log")
  log "候选侧用日志（增量；仅已完成臂）"
fi

OUT_MD="$LOCAL_DIR/verdict.md"
(cd "$REPO_ROOT" && PYTHONPATH="$REPO_ROOT/src" "$PY" \
  scripts/runner/compare_holder_overlay.py "${ARGS[@]}" --out "$OUT_MD")
log "结论已写出：$OUT_MD"
echo "----------------------------------------------------------------"
sed -n '1,20p' "$OUT_MD" 2>/dev/null
