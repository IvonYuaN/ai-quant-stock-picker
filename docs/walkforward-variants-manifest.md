# Walkforward 变体清单 — 2026-05-29

已收录 27 份历史报告（9 份当前格式 / 18 份旧格式），去重后识别出 **5 个独立变体**。计划新增 **10 个变体**（路径 A 网格）。

---

## 1. 已有报告 → 反向拆解为变体

### 当前格式报告（新 DSR 公式，PASS/FAIL TL;DR）

#### WF-001：Baseline（momentum + triple_rise, horizon=3）

| 字段 | 值 |
|------|-----|
| 报告路径 | `docs/walkforward-sqlite-pit.md`, `docs/walkforward-baostock-pit.md`, `docs/walkforward-cached.md`, `docs/walkforward-eastmoney.md`, `docs/walkforward-volume.md`, `docs/walkforward-mr.md`, `docs/walkforward-mr-mom-20d.md` |
| 数据源 | sqlite_db（7 份均为 sqlite_db，原始 source 标签不同但实际读同一数据库） |
| 策略组合 | momentum + triple_rise |
| 关键参数 | horizon_days=3, no tiered stop |
| DSR | 1.2156 |
| PBO | 75.00% |
| 唯一不同 | baseline |
| 备注 | 7 份报告文件名不同但回测配置完全一致（注记 1 已确认） |

#### WF-002：20d horizon

| 字段 | 值 |
|------|-----|
| 报告路径 | `docs/walkforward-20d.md` |
| 数据源 | sqlite_db |
| 策略组合 | momentum + triple_rise |
| 关键参数 | **horizon_days=20** |
| DSR | -1.2222 |
| PBO | 75.00% |
| 与 WF-001 的唯一不同 | horizon_days 3 → 20 |

#### WF-003：分级止损

| 字段 | 值 |
|------|-----|
| 报告路径 | `docs/walkforward-triple-tiered.md` |
| 数据源 | sqlite_db |
| 策略组合 | momentum + triple_rise |
| 关键参数 | **tiered_stop=True**（3.1% 硬止损 + 分级减仓） |
| DSR | 0.0843 |
| PBO | 75.00% |
| 与 WF-001 的唯一不同 | tiered_stop false → true |

### 旧格式报告（旧 DSR 公式，❌ emoji TL;DR）

以下 18 份报告来自早期会话，使用旧 DSR 公式（返回概率而非 z-statistic），旧 thresholds 配置。**不纳入当前双门评估**，仅作研究记录。

| 变体 ID | 报告路径 | 标的数 | PBO | 备注 |
|---------|----------|--------|-----|------|
| WF-L01 | `docs/walkforward-2026-05-27-real.md` | 90 | — | 早期调试 |
| WF-L02 | `docs/walkforward-2026-05.md` | 20 | — | 早期调试 |
| WF-L03 | `docs/walkforward-debug-2026-05-28-min01.md` | 83 | 75% | min_total_score=0.01 调试 |
| WF-L04 | `docs/walkforward-debug-2026-05-28-min03.md` | 84 | 75% | min_total_score=0.03 调试 |
| WF-L05 | `docs/walkforward-failures.md` | — | 0% | Sina 阻断，数据不完整 |
| WF-L06 | `docs/walkforward-final-check.md` | 116 | 100% | 旧 momentum-only |
| WF-L07 | `docs/walkforward-final.md` | 113 | 100% | 旧 momentum-only |
| WF-L08 | `docs/walkforward-fix2-min01.md` | 111 | 75% | min_total_score=0.01 |
| WF-L09 | `docs/walkforward-fix2-min04.md` | 97 | 75% | min_total_score=0.04 |
| WF-L10 | `docs/walkforward-multifactor.md` | 108 | 100% | 旧 multi-factor |
| WF-L11 | `docs/walkforward-regime-filter.md` | 114 | 100% | regime 过滤 |
| WF-L12 | `docs/walkforward-regime-final.md` | 143 | 100% | regime 过滤 |
| WF-L13 | `docs/walkforward-regime-fixed.md` | 135 | 75% | regime 修复 |
| WF-L14 | `docs/walkforward-regime-v2.md` | 137 | 75% | regime v2 |
| WF-L15 | `docs/walkforward-regime-v3.md` | 133 | 75% | regime v3 |
| WF-L16 | `docs/walkforward-rsi-fix-min01.md` | 98 | 75% | RSI 修复 |
| WF-L17 | `docs/walkforward-v2.md` | 111 | 100% | 旧 v2 |
| WF-L18 | `docs/walkforward-v5.md` | 156 | 100% | 旧 v5 |

### 去重结论

27 份报告 → 5 个独立变体（WF-001/002/003 + 18 个旧变体归为历史记录）。

WF-001 被 7 份报告引用——这是注记 1 指出的问题，本清单生效后不再发生。

---

## 2. 待跑变体清单（路径 A 网格预估）

基于路径 A（[pbo-fix-path-2026-05-29.md](file:///Users/ivon/Documents/AI量化选股/docs/pbo-fix-path-2026-05-29.md)）的参数网格：当前活跃权重基点 ±0.1 步长。

当前活跃维度：momentum_weight, triple_rise_weight, min_total_score。

quality.enabled / value.enabled 维持 false，不纳入网格。

| 变体 ID | 数据源 | 策略组合 | 与 WF-001 的参数差异 | 状态 |
|---------|--------|----------|---------------------|------|
| WF-A01 | sqlite_db | momentum + triple_rise | momentum_weight 基点 -0.1 | 待跑 |
| WF-A02 | sqlite_db | momentum + triple_rise | momentum_weight 基点 +0.1 | 待跑 |
| WF-A03 | sqlite_db | momentum + triple_rise | triple_rise_weight 基点 -0.1 | 待跑 |
| WF-A04 | sqlite_db | momentum + triple_rise | triple_rise_weight 基点 +0.1 | 待跑 |
| WF-A05 | sqlite_db | momentum + triple_rise | min_total_score 基点 -0.1 | 待跑 |
| WF-A06 | sqlite_db | momentum + triple_rise | min_total_score 基点 +0.1 | 待跑 |
| WF-A07 | sqlite_db | momentum + triple_rise | momentum -0.1, triple_rise +0.1 | 待跑 |
| WF-A08 | sqlite_db | momentum + triple_rise | momentum +0.1, triple_rise -0.1 | 待跑 |
| WF-A09 | sqlite_db | momentum + triple_rise | momentum -0.1, min_total_score -0.1 | 待跑 |
| WF-A10 | sqlite_db | momentum + triple_rise | triple_rise -0.1, min_total_score +0.1 | 待跑 |

网格说明：
- 单维度变动 6 个（A01-A06）+ 双维度变动 4 个（A07-A10）= 10 个变体
- 加上 WF-001（baseline）= 共 11 个数据点构建 T×N 矩阵
- 若列间相关性 > 0.95，触发 Path C fallback

---

## 3. 制度条款（草稿，待转写进宪法）

> **未来的任何 walkforward 跑动，必须在本清单中先有对应行**。如果跑了清单外的变体，§17 evidence 视作不合规，不计入双门评估。
>
> 新增变体需走 PR 改本清单，附理由（解决什么问题），由仓主审。

本条款为草稿，不写入 CONSTITUTION.md，等审完 #7 后决定是否纳入 §17.7 或 §17.8。
