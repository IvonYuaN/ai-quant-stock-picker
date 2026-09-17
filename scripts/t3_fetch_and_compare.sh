#!/usr/bin/env bash
# T3 收口：从 runner 取回 8 个产物小文件（不取 cache.db）+ 跑双窗口对比。
# 前置：4 个 gate_run_* 目录在 runner 上均有 walkforward_gate.json + report.md。
# 用法（需 disable sandbox 以访问网络）：bash scripts/t3_fetch_and_compare.sh
# 注意：htf_mr 变体烘焙了 min_total_score=0.1（原 0.6），与因子族换装耦合——
#   对比结论须看 3y/5y 方向一致性，人工补注混杂变量后再交付。
set -u
RUNNER_HOST=aqsp-runner
RUNNER_BASE=/opt/aqsp-runner
LOCAL_BASE=/Users/ivon/Documents/AI量化选股/outputs
DIRS="gate_run_5y_server gate_run_wf001_3y gate_run_htf_mr_3y gate_run_htf_mr_5y"

# 1) 取回 8 个产物小文件（跳过 ~1.4G 的 cache.db）
for d in $DIRS; do
  mkdir -p "$LOCAL_BASE/$d"
  for f in walkforward_gate.json report.md; do
    if scp -o BatchMode=yes -o ConnectTimeout=20 "$RUNNER_HOST:$RUNNER_BASE/$d/$f" "$LOCAL_BASE/$d/" 2>/dev/null; then
      echo "OK  $d/$f"
    else
      echo "MISSING $d/$f  (产物尚未写出，等下一轮重跑本脚本)"; exit 1
    fi
  done
done

# 2) 双窗口对比
cd /Users/ivon/Documents/AI量化选股
OUT_MD=outputs/T3_双窗口对比结论_$(date +%F).md
PYTHONUNBUFFERED=1 python3 scripts/compare_t3_dual_window.py \
  --wf001_5y  outputs/gate_run_5y_server \
  --wf001_3y  outputs/gate_run_wf001_3y \
  --htf_mr_3y outputs/gate_run_htf_mr_3y \
  --htf_mr_5y outputs/gate_run_htf_mr_5y \
  --out "$OUT_MD"
echo "=== compare 写出: $OUT_MD ==="

# 3) 自动补注混杂变量（htf_mr 变体烘焙 min_total_score=0.1，原 WF-001 基线为 0.6）
#    该阈值放宽与「因子族换装(htf+mr 换 mom+tr)」耦合，可能部分贡献方向改善；
#    故判决以 3y/5y 方向一致性为准，幅度不可直接读作纯因子族增益。
cat >> "$OUT_MD" <<'NOTE'

## 五、混杂变量提示（自动补注）

- ⚠️ `htf_mr` 8 个变体（WF-H01..H08）在 `feat/t3-htf-mr-swap` 分支中**烘焙了 `min_total_score=0.1`**，
  而基线 `WF-001`（stable_plus）使用的是 `min_total_score=0.6`。阈值放宽与「因子族换装」同时发生，
  属**混杂变量**：方向改善可能部分来自选股门槛放松，而非纯因子族增益。
- 判据铁律：**仅当 3y 与 5y 两窗口方向一致**才判方案 A 成立；幅度差异不 interpret 为纯因子族效应。
- 若需剥离该混杂，须另跑一组 `htf_mr` 但 `min_total_score` 固定在 0.6 的对照（当前未跑）。
NOTE
echo "=== 已补注 min_total_score 混杂提示 ==="
