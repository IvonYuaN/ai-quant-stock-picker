#!/usr/bin/env bash
# gate 实验跑批**启动前自检**（2026-08-08 新增）
#
# 🔴 为什么有这个脚本：2026-08-08 invert gate 连续失败 4 次，
#    每次根因都是「没先读代码就改参数」：
#      ① 不知道 --cache-path 会让 symbols 从 4412 涨到 5164（更慢）
#      ② 不知道 --symbols-cache-path 只控制缓存读取、**不控制规模**
#      ③ 不知道限规模会撞 MIN_PRODUCTION_GATE_SYMBOLS(=3000) 下限被 BLOCK
#      ④ 不知道 lab release 重建时 data 软链会被 rm -rf 删掉 → DB BLOCK
#
# ★ 用法：起跑前先跑这个，它会替你把"规模/超时/输入"核对一遍。
set -uo pipefail

FAIL=0
ok()   { printf '  ✓ %s\n' "$1"; }
bad()  { printf '  ✗ %s\n' "$1"; FAIL=1; }
warn() { printf '  ⚠ %s\n' "$1"; }

LAB="${1:?用法: gate_preflight.sh <lab_release_dir> [max_symbols] [min_symbols] [experiment]}"
MAXS="${2:-}"
MINS="${3:-}"
EXPERIMENT="${4:-}"

echo "=== gate 跑批启动前自检 ==="
echo "  lab = $LAB"

# ── 1. lab release 完整性 ──────────────────────────────────────────
echo "[1] lab release"
if [ ! -d "$LAB" ]; then bad "lab 目录不存在：$LAB"; else
  PY_N=$(find "$LAB/src/aqsp" -name '*.py' 2>/dev/null | wc -l | tr -d ' ')
  if [ "$PY_N" -ge 200 ]; then ok ".py = $PY_N"
  else bad ".py 只有 $PY_N 个（<200，release 不完整）"; fi
fi

# ── 2. data 软链（今天被 rm -rf 删掉过 ⇒ DB BLOCK）──────────────────
echo "[2] 数据可见性"
if [ -e "$LAB/data/astocks_raw.db" ]; then
  SZ=$(stat -c %s "$LAB/data/astocks_raw.db" 2>/dev/null || stat -f %z "$LAB/data/astocks_raw.db" 2>/dev/null || echo 0)
  if [ "${SZ:-0}" -gt 1000000 ]; then ok "db 可读（$((SZ/1024/1024)) MB）"
  else bad "db 太小（$SZ bytes）—— 软链可能指向空库"; fi
else
  bad "data/astocks_raw.db 不可见（★ 重建 lab 时 data 是软链，rm -rf 会删掉它）"
fi

# ── 3. 规模是否会撞门禁下限 ────────────────────────────────────────
echo "[3] 规模 vs 门禁下限"
GATE_SH="$LAB/scripts/runner/run_production_walkforward_gate.py"
if [ ! -f "$GATE_SH" ]; then bad "找不到 gate 脚本"; else
  MIN_GATE=$(grep -oE "MIN_PRODUCTION_GATE_SYMBOLS = [0-9]+" "$GATE_SH" 2>/dev/null | grep -oE "[0-9]+")
  [ -z "$MIN_GATE" ] && MIN_GATE=$(PYTHONPATH="$LAB/src:$LAB" python3 -c "
from aqsp.walkforward_gate import MIN_PRODUCTION_GATE_SYMBOLS as m; print(m)" 2>/dev/null || echo 3000)
  ok "生产门禁下限 MIN_PRODUCTION_GATE_SYMBOLS = ${MIN_GATE:-3000}"

  if [ -n "$MAXS" ]; then
    if [ -n "$MINS" ] && [ "$MAXS" -lt "$MINS" ]; then
      if [ "$EXPERIMENT" = "1" ]; then
        warn "max($MAXS) < min($MINS) ⇒ 已声明 --experiment ⇒ 可跑，但结果**不是门禁结论**"
      else
        bad "max($MAXS) < min($MINS) ⇒ 会被 BLOCK。加 --experiment 才能跑（结果不是门禁结论）"
      fi
    else
      ok "max($MAXS) >= min($MINS) ⇒ 不触发 BLOCK"
    fi
    if [ "$MAXS" -lt "${MIN_GATE:-3000}" ] && [ "$EXPERIMENT" != "1" ]; then
      bad "max($MAXS) < 门禁下限 ${MIN_GATE:-3000} ⇒ 必须显式 --experiment"
    fi
  else
    warn "未指定 max-symbols ⇒ 将使用全量（★ 实测 5164 只，runner 上 3y 跑不完）"
  fi
fi

# ── 4. cache 的真实影响（今天踩过：cache 会改变 symbols 规模）──────
echo "[4] cache"
if grep -q "coverage_mode\|cache" "$GATE_SH" 2>/dev/null; then
  warn "gate 会做覆盖预检并生成 symbols 文件 ⇒ cache 可能改变入选规模（实测 4412 → 5164）"
  warn "⇒ ★ 判规模只认 stdout 里的「使用 symbols 文件标的池: N 只」，别靠猜"
fi

# ── 5. 运行环境 ────────────────────────────────────────────────────
echo "[5] 运行环境"
command -v python3 >/dev/null 2>&1 && ok "python3 可用" || bad "python3 缺失"
if [ -n "${AQSP_SQLITE_DB_PATH:-}" ]; then ok "AQSP_SQLITE_DB_PATH=$AQSP_SQLITE_DB_PATH"
else warn "未设 AQSP_SQLITE_DB_PATH（gate 通常用 --db 显式传）"; fi

echo
if [ "$FAIL" = "0" ]; then
  echo "=== ✅ 自检通过，可以起跑 ==="
  echo "★ 起跑后 60 秒内必查这三行（缺一说明参数没生效）："
  echo "   grep -E 'EXPERIMENT|使用 symbols|timeout auto' <stdout.log>"
else
  echo "=== ❌ 自检未通过，先修上面标 ✗ 的项 ==="
fi
exit "$FAIL"