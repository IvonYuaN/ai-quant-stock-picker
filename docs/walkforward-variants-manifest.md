# Walkforward 变体清单 — 2026-06-01

已收录 28 份历史报告（9 份当前格式 / 18 份旧格式 / 1 份新网格报告），去重后识别出 **16 个独立变体**。**路径 A 纯权重网格已废弃**；当前正式使用的是 **WF-B 多维度网格**（lookback/horizon/top_n 维度，已通过双门验证）。

---

## 1. 已有报告 → 反向拆解为变体

### 当前格式报告（新 DSR 公式，PASS/FAIL TL;DR）

#### WF-001：Baseline（momentum + triple_rise, horizon=3）

| 字段 | 值 |
|------|-----|
| 报告路径 | `docs/walkforward-sqlite-pit.md`, `docs/walkforward-baostock-pit.md`, `docs/walkforward-cached.md`, `docs/walkforward-eastmoney.md`, `docs/walkforward-volume.md`, `docs/walkforward-mr.md`, `docs/walkforward-mr-mom-20d.md` |
| 数据源 | 7 份报告均由 commit `cfb859a` 生成。**关键问题：commit message 未记录 `--source` CLI 参数，报告正文亦无数据源字段，会话记录已丢失。** 7 份 TL;DR 数值完全一致（DSR=1.2156, PBO=75%, Sharpe=3.28, TotalReturn=859.13%），但**数值一致只能证明同一参数下的同一次运行被复制 7 份，不能反推数据源**——同一 commit 也可能用不同 `--source` 跑出相同结果（如果数据本身一致）。结论：**实际数据源待 PR-D 实施阶段从 commit 历史/CI 日志/会话记录中考据确认**；当前先按"sqlite_db（最可能）"标注，但不作为 §17 evidence 的硬证据。文件名（baostock-pit/cached/eastmoney 等）是历史残留标签，已确认不代表实际数据源。 |
| 策略组合 | momentum + triple_rise |
| 关键参数 | horizon_days=3, no tiered stop, momentum_weight=0.3, triple_rise_weight=0.3, lookback_days=60, min_total_score=0.1, top_n=10 |
| DSR | 1.2156 |
| PBO | 75.00% |
| 唯一不同 | baseline |

#### WF-002：20d horizon

| 字段 | 值 |
|------|-----|
| 报告路径 | `docs/walkforward-20d.md` |
| 数据源 | sqlite_db（**待 PR-D 实施阶段从 commit/CLI 历史考据确认，与 WF-001 同源问题**） |
| 策略组合 | momentum + triple_rise |
| 关键参数 | **horizon_days=20** |
| DSR | -1.2222 |
| PBO | 75.00% |
| 与 WF-001 的唯一不同 | horizon_days 3 → 20 |

#### WF-003：分级止损

| 字段 | 值 |
|------|-----|
| 报告路径 | `docs/walkforward-triple-tiered.md` |
| 数据源 | sqlite_db（**待 PR-D 实施阶段从 commit/CLI 历史考据确认，与 WF-001 同源问题**） |
| 策略组合 | momentum + triple_rise |
| 关键参数 | **tiered_stop=True**（3.1% 硬止损 + 分级减仓） |
| DSR | 0.0843 |
| PBO | 75.00% |
| 与 WF-001 的唯一不同 | tiered_stop false → true |

### 新格式网格报告（2026-05-30，通过双门）

#### WF-B 网格（已验证，当前正式使用）

报告路径：`outputs/cscv-grid-2026-05-30-hs300-v3.md` / `outputs/cscv-grid-2026-05-30-hs300-v3.json`

数据源：sqlite_db，标的池：sh300，回测区间：2018-01-01 ~ 2024-12-31

**双门结果**：PASS — DSR=1.9174, PBO=24.21%，列间最大非对角相关：0.947（< 0.95，未触发 Path C）

**WF-B 变体清单**（共 11 个，包括 WF-001 作为 baseline）：

| 变体 ID | mom | tr | lb | h | top | Sharpe | Return | MaxDD | DSR | PBO | 状态 |
|---------|-----|----|----|----|-----|--------|--------|-------|-----|-----|------|
| WF-001 | 0.3 | 0.3 | 60 | 3 | 10 | 3.51 | 1048.47% | 41.20% | 1.9174 | 24.21% | ✅ 已跑，PASS |
| WF-B01 | 0.3 | 0.3 | 60 | 3 | 5 | 4.25 | 480.93% | 23.91% | 1.5592 | — | ✅ 已跑 |
| WF-B02 | 0.3 | 0.3 | 60 | 3 | 20 | 4.41 | 9351.99% | 57.13% | 3.7863 | — | ✅ 已跑 |
| WF-B03 | 0.3 | 0.3 | 20 | 3 | 10 | 2.48 | 463.36% | 37.76% | 0.9580 | — | ✅ 已跑 |
| WF-B04 | 0.3 | 0.3 | 120 | 3 | 10 | 2.98 | 667.33% | 42.63% | 1.3918 | — | ✅ 已跑 |
| WF-B05 | 0.3 | 0.3 | 60 | 1 | 10 | 1.85 | 120.90% | 43.96% | 0.2622 | — | ✅ 已跑 |
| WF-B06 | 0.3 | 0.3 | 60 | 10 | 10 | 1.59 | 215.61% | 66.78% | -0.0041 | — | ✅ 已跑 |
| WF-B07 | 0.2 | 0.4 | 40 | 5 | 5 | 2.42 | 181.87% | 34.46% | 0.2113 | — | ✅ 已跑 |
| WF-B08 | 0.4 | 0.2 | 100 | 2 | 15 | 2.87 | 1015.28% | 61.21% | 1.7953 | — | ✅ 已跑 |
| WF-B09 | 0.5 | 0.1 | 80 | 5 | 10 | 2.16 | 454.72% | 54.82% | 0.6420 | — | ✅ 已跑 |
| WF-B10 | 0.1 | 0.5 | 40 | 7 | 10 | 2.42 | 536.23% | 64.40% | 0.7995 | — | ✅ 已跑 |

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

旧格式 18 份的 PBO 异常分布（部分 100%、部分 75%、部分 0%）也反映了非 CSCV 实现的结构性问题，详见 [pbo-audit-2026-05-29.md](file:///Users/ivon/Documents/AI量化选股/docs/pbo-audit-2026-05-29.md)。

### 去重结论

28 份报告 → 16 个独立变体（WF-001/002/003 + WF-B01-B10 + 18 个旧变体归为历史记录）。

WF-001 被 7 份报告引用——这是注记 1 指出的问题，本清单生效后不再发生。

---

## 2. 已废弃：路径 A 纯权重网格

> **⚠️ 路径 A 已废弃**：
> - 原计划的"纯权重网格"（momentum_weight ±0.1/±0.2, triple_rise_weight ±0.1/±0.2）从未实际跑通
> - 纯权重单维度的 T×N 矩阵列间相关性会极高（大概率 > 0.95），触发 Path C，无意义
> - 本清单中原 WF-A01-A10 的 10 个待跑变体现已取消，勿再跑

---

## 3. 制度条款（草稿，待转写进宪法）

> **草稿条款 A（变体白名单）**：未来的任何 walkforward 跑动，必须在本清单中先有对应行。如果跑了清单外的变体，§17 evidence 视作不合规，不计入双门评估。
>
> 新增变体需走 PR 改本清单，附理由（解决什么问题），由仓主审。

> **草稿条款 B（CLI 参数留痕）**：每次 walkforward 跑动的 commit message **必须**记录使用的关键 CLI 参数（至少包括 `--source`、`--horizon-days`、`--tiered-stop`、`--pool`、`--min-total-score`，以及任何被改动的 thresholds.yaml 字段值）。报告正文 TL;DR 同时记录这些参数。
>
> 理由：WF-001 的 7 份重复报告无法考证实际数据源，正是因为 cfb859a 的 commit message 和报告正文都未留痕，会话记录又已丢失。这是结构性问题，不是个例。
>
> 实施载体：可放进 `cli.py` 报告生成逻辑（自动写入 TL;DR）+ 一份 commit message 模板。

本两条均为草稿，不写入 CONSTITUTION.md，等审完后决定是否纳入 §17.7 / §17.8。
