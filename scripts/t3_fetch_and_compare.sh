#!/usr/bin/env bash
# T3 收口：从 runner 取回 8 个产物小文件（不取 cache.db）+ 跑双窗口对比。
# 前置：4 个 gate_run_* 目录在 runner 上均有 walkforward_gate.json + report.md。
# 用法（需 disable sandbox 以访问网络）：bash scripts/t3_fetch_and_compare.sh
# 口径：htf_mr 变体烘焙 min_total_score=0.1，基线 WF-001 的**生效值**为 0.4。
#   该差异已实测定性为「不构成混杂」（两臂阈值均不咬合 → 选股为纯 top-n 排名）；
#   本脚本仍在结论末尾补注该口径（含「不要用 0.6 做对照」的警告），见步骤 3。
#   判据 = **同 horizon 层内的臂均值对比**（compare 脚本的 §三，2026-09-24 起），
#   3y/5y 两窗口方向一致才判方案 A 成立；原「最佳 vs 基线」口径已标注为不可单独引用。
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

# 3) 自动补注口径说明：min_total_score 差异**已实测定性为不构成混杂**，补注只为留痕
#    （避免后来者重复调查，并警告「不要用 0.6 做对照」——那会引入新的空仓期假象）。
#
# ⚠️ 补注的小节号必须**跟着 compare 脚本的产物走**，不能写死：
#    产物小节是「一 门禁 / 二 逐变体 / 三 同 horizon 分层 / 四 朴素口径 / 五 判决」，
#    「六 解析备注」**只在有解析告警时才输出** ⇒ 写死 `## 六、` 会在健康路径上撞号。
#    这里数产物已有小节数，取下一个中文序号。
_CN_NUM=(零 一 二 三 四 五 六 七 八 九 十)
_existing_secs=$(grep -c '^## ' "$OUT_MD" || true)
_next_sec=$((_existing_secs + 1))
if (( _next_sec >= 1 && _next_sec < ${#_CN_NUM[@]} )); then
  _sec_label="${_CN_NUM[$_next_sec]}"
else
  echo "⚠️ 产物已有 ${_existing_secs} 个小节，超出中文序号表 —— 补注改用「附」，请人工确认小节结构" >&2
  _sec_label="附"
fi
printf '\n## %s、口径说明（自动补注：两臂 min_total_score 差异 = 已核查，无影响）\n' \
  "$_sec_label" >> "$OUT_MD"
cat >> "$OUT_MD" <<'NOTE'

- **实际取值**：基线 `WF-001`（`stable_plus`）**生效阈值 = 0.4**
  （`config/thresholds.yaml` 覆盖 `thresholds.py` 中的 dataclass 默认 0.6）；
  `htf_mr` 8 个变体（WF-H01..H08）在 `feat/t3-htf-mr-swap` 分支中烘焙 `min_total_score = 0.1`。
- **为什么它不构成混杂**：`CompositeStrategy.select_stocks` 的机制是
  `[s for s in ranked if score >= thr][:n]`（`composite.py:287-295`）——
  **阈值只在「通过数 < top_n」时咬合**，否则选股退化为**纯 top-n 排名**。
- **实测证据**（8 快照 × 全池 ~5400 票，走真实选股路径）：
  - `WF-001@0.4` 通过数最少 **215** 票；`htf_mr@0.1` 最少 **5201** 票 ⇒ **两臂均未咬合**；
  - 单快照（2026-09-11）下阈值 0.4 与 0.1 的 **top5/10/15/20 名单完全一致**；
  - `htf_mr` 即便沿用 0.4 仍有 694 票、沿用 0.6 仍有 67 票通过。
- ⇒ **两策略在实跑配置下选出的是同一批票**，阈值差异不改变选股结果 ⇒
  本结论**可以读幅度**，不必退化为「只认方向」。
- ⚠️ **反向警告（重要，别踩）**：阈值本身是个**失真旋钮**——选择率随 regime 漂移约 **7×**
  （≥0.4 的通过数在 215~1480 之间）。
  - **不要**拿 `0.6` 做「等选择性对照」：实测 `htf_mr@0.6` 会在 **2/8** 快照咬合到 **12~19 票**
    （< `top_n=20`），反而制造「选出不足 n 只」的空仓期，与 C4 的空仓/跳过期口径混淆；
  - 正确的对照值是 `0.4`，而 `htf_mr@0.4` 与 `@0.1` 选股**逐位相同** ⇒ 该对照**零信息量，不必跑**。
- 判据仍以 **3y / 5y 两窗口方向一致性**为准（单窗口幅度可能受窗口特性影响）。
- `DSR` 全负属已知 alpha 赤字（R3）：方案 A 成立仅代表「因子族替换方向正确」，
  **不代表样本外已盈利**。

> 裁定依据：`outputs/T3_混杂变量裁定_min_total_score_2026-09-17.md`；
> 可复跑探针：`outputs/_probe_min_score_threshold.py`、`_probe_min_score_drift.py`、
> `_probe_htfmr_distribution.py`、`_probe_confound_final.py`。
NOTE
echo "=== 已补注 min_total_score 口径说明（已核查：不构成混杂）==="
