#!/usr/bin/env bash
#
# ============================================================================
#  T3 双窗口验证 —— Runner「分阶段接力」编排脚本（安全 / 自包含 / 可重复执行）
# ============================================================================
#
#  ⚠️ 红线声明（务必先读）：
#  ---------------------------------------------------------------------------
#  本脚本【不包含】任何「部署 / 同步 prod」的动作，也绝不触碰线上业务数据。
#  它只做：探活 → 产物就绪守卫 →（可选）补库 → 取回 4 个 gate_run 目录
#          → 本地对比生成结论 → 追加口径补注。
#  任何「部署 / 同步到 prod」都必须由老大显式确认后单独执行。
#  ---------------------------------------------------------------------------
#
#  设计要点（2026-09-17 修订）：
#   - **阶段化 + 幂等 + 零副作用前置**：gate 产物没齐就干净退出（exit 0），
#     绝不 scp 半成品、绝不跑对比脚本。
#   - **补库默认关闭**（T3_DO_BACKFILL=0）：gate 窗口用 `--end 2026-09-09` 显式钉死，
#     不依赖 09-10/09-11 数据；且 2 路 gate 正在读 astocks_raw.db，
#     此时写库会诱发 SQLite BUSY 风险。需补库时显式 T3_DO_BACKFILL=1。
#   - **补库命令必须带 `--symbols` 接线**：漏写会回落 `_sync_stock_list_compat`
#     触发「全宇宙回填」（update_sqlite_daily.py:579-585），已硬编码保护。
#   - **取回收敛到单一实现**：本脚本的「取回 + 对比」阶段**复用**
#     `scripts/t3_fetch_and_compare.sh`（只取 8 个小文件、跳过 ~350MB/个 的 cache.db），
#     不再自行 `scp -r` 整个目录，避免重复实现与无谓的 GB 级传输。
#   - 本脚本额外负责：探活、四产物就绪守卫、（可选）补库、**强制追加口径补注**。
#
#  用法：
#    bash scripts/t3_resume_runner.sh                  # 产物未齐→只报进度
#    T3_DO_BACKFILL=1 bash scripts/t3_resume_runner.sh # 产物齐后顺带补库
#
#  备注：本机经 CodeBuddy 沙箱执行时，ssh/scp/nc 需绕过沙箱。
# ============================================================================

set -u

# ----------------------------- 变量定义区 -----------------------------
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RUNNER_HOST="aqsp-runner"
RUNNER_ROOT="/opt/aqsp-runner"
RUNNER_DB="/opt/aqsp-runner/data/astocks_raw.db"
RUNNER_GATE_DIR="/opt/aqsp-runner"

LOCAL_RESUME="data/backfill_resume_20260912.txt"
RUNNER_RESUME="/tmp/backfill_resume_20260912.txt"

RUNNER_IP="38.147.170.174"
RUNNER_PORT="31777"

GATE_DIRS=(gate_run_5y_server gate_run_wf001_3y gate_run_htf_mr_3y gate_run_htf_mr_5y)

LOCAL_OUT="${PROJECT_ROOT}/outputs"
LOCAL_RESUME_ABS="${PROJECT_ROOT}/${LOCAL_RESUME}"

# 补库开关（默认关：见文件头「设计要点」）
T3_DO_BACKFILL="${T3_DO_BACKFILL:-0}"

# ----------------------------- 步骤 0：探活 -----------------------------
echo "[步骤0] 探活 runner ${RUNNER_IP}:${RUNNER_PORT} ..."
if ! nc -z -w 5 -G 5 "${RUNNER_IP}" "${RUNNER_PORT}"; then
  echo "runner 仍离线，未接力"
  exit 0
fi
echo "[步骤0] runner 在线。"

# ------------------- 步骤 1：产物就绪守卫（未齐则零副作用退出） -------------------
echo "[步骤1] 检查 4 个 gate_run 产物是否齐备 ..."
READY_REPORT="$(ssh "${RUNNER_HOST}" "cd ${RUNNER_GATE_DIR} 2>/dev/null || exit 9
for d in ${GATE_DIRS[*]}; do
  if [ -s \"\$d/walkforward_gate.json\" ] && [ -s \"\$d/report.md\" ]; then
    echo \"READY \$d\"
  elif [ -d \"\$d\" ]; then
    echo \"PENDING \$d\"
  else
    echo \"ABSENT \$d\"
  fi
done
echo \"---progress---\"
for d in ${GATE_DIRS[*]}; do
  n=\$(grep -c 'streaming period' \"\$d/launch.log\" \"\$d/run.log\" 2>/dev/null | awk -F: '{s+=\$2} END{print s+0}')
  p=\$(grep -oh 'streaming period [0-9]*/[0-9]*' \"\$d/launch.log\" \"\$d/run.log\" 2>/dev/null | tail -1)
  echo \"\$d: \${p:-no-streaming-line} (periods_seen=\${n})\"
done")" || { echo "[步骤1] 无法读取 runner 产物目录，中止。"; exit 1; }

echo "${READY_REPORT}"

if [ "$(echo "${READY_REPORT}" | grep -c '^READY ')" -ne "${#GATE_DIRS[@]}" ]; then
  echo "[步骤1] 产物未齐（见上），本轮不取回、不对比，零副作用退出。"
  exit 0
fi
echo "[步骤1] 4 个产物齐备。"

# ------------------- 步骤 2（可选）：runner 补库 -------------------
if [ "${T3_DO_BACKFILL}" = "1" ]; then
  echo "[步骤2] scp 补库清单 -> ${RUNNER_HOST}:${RUNNER_RESUME}"
  if [ ! -f "${LOCAL_RESUME_ABS}" ]; then
    echo "[步骤2] 错误：本机清单不存在 ${LOCAL_RESUME_ABS}，中止。"
    exit 1
  fi
  scp "${LOCAL_RESUME_ABS}" "${RUNNER_HOST}:${RUNNER_RESUME}" \
    || { echo "[步骤2] 推送清单失败，中止。"; exit 1; }

  echo "[步骤2] ssh runner 执行补库（带 --symbols，避免全宇宙回填）"
  ssh "${RUNNER_HOST}" "cd ${RUNNER_ROOT} && PYTHONUNBUFFERED=1 python3 scripts/update_sqlite_daily.py \"${RUNNER_DB}\" \
    --target-date 2026-09-11 \
    --symbols \"\$(cat ${RUNNER_RESUME})\" \
    --start-date 2023-01-01 \
    --fill-history-gaps \
    --sleep-seconds 0.3 \
    --allow-partial-target-coverage \
    --price-mode raw" \
    || { echo "[步骤2] 补库执行失败，中止。"; exit 1; }
else
  echo "[步骤2] 跳过补库（T3_DO_BACKFILL=0；gate 窗口已 --end 2026-09-09 钉死，不依赖新数据）。"
fi

# ------------------------- 步骤 3+4：取回 + 双窗口对比（复用轻量脚本） -------------------------
# 复用 scripts/t3_fetch_and_compare.sh：只取 8 个产物小文件（walkforward_gate.json + report.md），
# 跳过每个约 350MB 的 cache.db（四个目录合计 ~1.4G），并直接调 compare_t3_dual_window.py。
echo "[步骤3+4] 调用 scripts/t3_fetch_and_compare.sh（轻量取回 + 对比）"
( cd "${PROJECT_ROOT}" && bash scripts/t3_fetch_and_compare.sh ) \
  || { echo "[步骤3+4] 取回或对比失败，中止。"; exit 1; }

CONCLUSION="${LOCAL_OUT}/T3_双窗口对比结论_$(date +%F).md"
[ -f "${CONCLUSION}" ] || { echo "[步骤3+4] 结论文件未生成，中止。"; exit 1; }

# ------------------------- 步骤 5：追加「口径补注」（强制） -------------------------
echo "[步骤5] 在结论文件末尾追加口径补注"
if grep -q "口径补注" "${CONCLUSION}"; then
  echo "[步骤5] 已存在口径补注段落，跳过（幂等）。"
else
  cat >> "${CONCLUSION}" <<'EOF'

---

## 附录：口径补注（⚠️ 必读，判读结论前先看这一段）

1. **`min_total_score` 混杂变量**：htf_mr 变体在替换因子族的同时，把
   `min_total_score` 由 **0.6 降到 0.1**（`_apply_walkforward_grid_variant` 的
   `strategy_mix=="htf_mr"` 分支）。⇒ 两策略**选股池/选择性不同**，
   Sharpe / 收益之差**混入了「门槛放宽」效应，不能纯归因于因子族替换**。

2. **判据 = 方向一致性，不是绝对幅度**：只有在 3y 与 5y 两个不重叠窗口上
   htf_mr 相对 WF-001 的改善**方向一致**，方案 A（htf+mr 换 mom+tr）才成立。

3. **边际 / 方向不一致时的必须补跑**：判「混合」或方向不一致时，下结论前须补
   **等选择性对照**——即 htf_mr 因子权重不变、但 `min_total_score` 恢复 0.6，
   单独隔离「因子族替换」这一个变量，再比一次。未做对照前不得宣布方案成立/否决。

4. **DSR 全负属已知 alpha 赤字（R3）**：方案 A 成立仅代表「因子族替换方向正确」，
   **不代表样本外已盈利**。

5. 本脚本**不含任何部署 / 同步 prod 步骤**；部署须经老大确认后单独执行。
EOF
fi

echo "[完成] 接力结束。结论文件：${CONCLUSION}"
echo "[完成] 提醒：本脚本不含部署/同步 prod，部署须老大确认。"
