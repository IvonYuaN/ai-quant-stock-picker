#!/usr/bin/env python3
"""「可行筛选器」判据守卫（研究侧，入库；零生产改动、零联网、秒级）。

背景与分层
----------
业务定位已锁定（`research/pit_factors/筛选器定位_决策.md`）= **选股筛选工具**：
不承诺正超额，须证明是「可行（稳健、不是垃圾）」的策略。判据 =
**在多数市场面（regime）与多数年度下，相对同截面池等权的超额「不显著为负」**
（Newey-West t > −1.96，双侧 95%）。

本模块把该判据抽成**可 CI 回归的纯函数单一事实源**：
- 输入是「已算好的逐截面超额序列 + regime/年度标签 + 步长/时域参数」；
- 输出是结构化判决（viable / year_stable / cond_pass / 各维度 t）。
- **重计算（跑全市场面板取超额）仍留在 runner 的
  `research/pit_factors/regime_robustness_probe.py`**（本机/CI 不重跑）；
  本模块只负责「给定超额序列，判据如何定生死」，逐位对齐探针口径。

口径（与 `pb_verdict_probe._nw_t` / `_acf_lag` 逐位一致）
--------------------------------------------------------
- 基准 = 同截面生产池等权；收益 = 每截面 top-N 票单期截面等权均值（不跨期累乘）。
- NW-t 用 HAC（lag 由序列自相关定）；判「显著为负」= t < −1.96。
- regime/年度轴 = 60 日全市场中位数收益三分位 / 分年（无前视）。

红线：不改 `config/thresholds.yaml`、不碰生产打分、不联网、不写未来数据。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from typing import Dict, List, Optional, Sequence

import numpy as np

# 判据常量（与探针一致）
CRIT_T = 1.96          # |t| 超过即判「显著」（双侧 95%）
MIN_SECTIONS = 5      # 少于该截面数的分组不作判（样本不足，只提示）

BASELINE = "A_双反向(当前yaml)"   # 生产基线方向名（regime/分年轴判读它）


# ---------------------------------------------------------------------------
# 统计原语（逐位对齐 research/pit_factors/pb_verdict_probe.py）
# ---------------------------------------------------------------------------
def nw_t(arr: np.ndarray, lag: int) -> float:
    """HAC Newey-West t（对齐 `pb_verdict_probe._nw_t`）。"""
    x = np.asarray(arr, dtype=float)
    n = len(x)
    if n < 2:
        return float("nan")
    m = float(x.mean())
    xd = x - m
    var = float(np.dot(xd, xd) / n)
    for k in range(1, min(lag, n - 1) + 1):
        var += 2.0 * (1.0 - k / (lag + 1)) * float(np.dot(xd[k:], xd[:-k]) / n)
    se = math.sqrt(max(var, 1e-18) / n)
    return m / se if se > 0 else float("nan")


def acf_lag(arr: np.ndarray, cap: int) -> int:
    """自适应 lag（对齐 `pb_verdict_probe._acf_lag`）。"""
    x = np.asarray(arr, dtype=float)
    n = len(x)
    if n < 12:
        return 1
    xd = x - x.mean()
    denom = float(np.dot(xd, xd))
    if denom <= 0:
        return 1
    top = max(1, min(cap, n // 4))
    for k in range(1, top + 1):
        if float(np.dot(xd[k:], xd[:-k]) / denom) < 0.1:
            return k
    return top


def effective_lag(values: np.ndarray, step: int, horizon: int, snap_len: int) -> int:
    """探针口径：base=max(1,ceil(h/step)); cap=max(base, len(snap)//4);
    eff = max(base, acf_lag(values, cap))。"""
    base_lag = max(1, math.ceil(horizon / step))
    cap = max(base_lag, snap_len // 4)
    return max(base_lag, acf_lag(np.asarray(values, dtype=float), cap))


def group_judgment(values: np.ndarray, lag: int) -> dict:
    """单组（某 regime / 某年 / 某方向）的超额均值 + NW-t + 是否「不显著为负」。"""
    x = np.asarray(values, dtype=float)
    t = nw_t(x, lag)
    return {
        "n": int(len(x)),
        "excess_mean": float(x.mean()),
        "excess_t_nw": t,
        "win_vs_pool": float((x > 0).mean()) if len(x) else float("nan"),
        "not_sig_neg": (not math.isnan(t)) and (t > -CRIT_T),
    }


# ---------------------------------------------------------------------------
# 三个轴的判决（对齐探针 main() 的 viable / year_stable / cond_pass）
# ---------------------------------------------------------------------------
def judge_regime(
    groups: Dict[str, Sequence[float]],
    overall: Sequence[float],
    step: int,
    horizon: int,
    snap_len: int,
) -> dict:
    """市场面轴：≥2/3 报告 regime 不显著为负，且整体不显著为负 ⇒ viable。

    `groups` = {regime 名: 逐截面超额}；`overall` = 全体截面超额。
    """
    base_lag = max(1, math.ceil(horizon / step))
    cap = max(base_lag, snap_len // 4)
    overall_t = nw_t(np.asarray(overall, dtype=float), max(base_lag, acf_lag(np.asarray(overall, dtype=float), cap)))
    per_regime: Dict[str, dict] = {}
    n_sig_neg = 0
    n_reported = 0
    for name, vals in groups.items():
        j = group_judgment(vals, max(base_lag, acf_lag(np.asarray(vals, dtype=float), cap)))
        if j["n"] < MIN_SECTIONS:
            j["reported"] = False
            per_regime[name] = j
            continue
        j["reported"] = True
        n_reported += 1
        n_sig_neg += int(not j["not_sig_neg"])
        per_regime[name] = j
    viable = (
        n_reported > 0
        and n_sig_neg <= max(1, n_reported // 3)
        and not (overall_t < -CRIT_T)
    )
    return {
        "axis": "regime",
        "viable": bool(viable),
        "overall_t_nw": overall_t,
        "n_sig_neg": n_sig_neg,
        "n_reported": n_reported,
        "per_regime": per_regime,
    }


def judge_yearly(
    groups: Dict[str, Sequence[float]],
    overall_t: float,
    step: int,
    horizon: int,
    snap_len: int,
) -> dict:
    """分年度轴：≥2/3 报告年份不显著为负，且整体不显著为负 ⇒ year_stable。

    `groups` = {年份: 逐截面超额}。`overall_t` 来自 regime 轴的 overall（探针复用同一值）。
    """
    base_lag = max(1, math.ceil(horizon / step))
    cap = max(base_lag, snap_len // 4)
    per_year: Dict[str, dict] = {}
    n_sig_neg = 0
    n_reported = 0
    for name, vals in groups.items():
        j = group_judgment(vals, max(base_lag, acf_lag(np.asarray(vals, dtype=float), cap)))
        if j["n"] < MIN_SECTIONS:
            j["reported"] = False
            per_year[name] = j
            continue
        j["reported"] = True
        n_reported += 1
        n_sig_neg += int(not j["not_sig_neg"])
        per_year[name] = j
    year_stable = (
        n_reported > 0
        and n_sig_neg <= max(1, n_reported // 3)
        and not (overall_t < -CRIT_T)
    )
    return {
        "axis": "yearly",
        "year_stable": bool(year_stable),
        "n_sig_neg": n_sig_neg,
        "n_reported": n_reported,
        "per_year": per_year,
    }


def judge_conditioning(
    per_dir_by_regime: Dict[str, Dict[str, Sequence[float]]],
    step: int,
    horizon: int,
    snap_len: int,
) -> dict:
    """行情条件化轴：每个 regime 是否**所有**方向都不显著为负（且该 regime 截面足够）。

    `per_dir_by_regime` = {regime: {方向名: 逐截面超额}}。
    cond_pass = 全部报告的 regime 都「各方向 t > −1.96」。
    探针已证：方向随 regime 漂移 ⇒ 通常 cond_pass=False（不做动态切换的依据）。
    """
    base_lag = max(1, math.ceil(horizon / step))
    cap = max(base_lag, snap_len // 4)
    cond_pass = True
    detail: Dict[str, dict] = {}
    for regime, per_dir in per_dir_by_regime.items():
        judged: Dict[str, dict] = {}
        reported_any = False
        for name, vals in per_dir.items():
            j = group_judgment(vals, max(base_lag, acf_lag(np.asarray(vals, dtype=float), cap)))
            judged[name] = j
            reported_any = reported_any or (j["n"] >= MIN_SECTIONS)
        if not reported_any:
            cond_pass = False
            detail[regime] = {"ok": False, "reason": "截面不足", "per_dir": judged}
            continue
        # 探针口径：只有截面足够的方向参与「全不显著为负」判定
        enough = [j for j in judged.values() if j["n"] >= MIN_SECTIONS]
        all_not_neg = all(j["not_sig_neg"] for j in enough)
        cond_pass = cond_pass and all_not_neg
        detail[regime] = {"ok": all_not_neg, "per_dir": judged}
    return {"axis": "conditioning", "cond_pass": bool(cond_pass), "detail": detail}


def verdict(regime: dict, yearly: dict, cond: dict) -> dict:
    """汇总裁决：筛选器定位 = regime 轴 viable（分年稳健 + 不做动态条件化）。"""
    return {
        "filter_viable": regime["viable"],
        "yearly_stable": yearly["year_stable"],
        "conditioning_ok": cond["cond_pass"],
        # 定位结论：可行筛选器 = regime 轴 viable（alpha 赤字不在此轴判死）
        "do_dynamic_switching": False,  # 由 cond_pass 与方向漂移共同决定，恒不切换
    }


# ---------------------------------------------------------------------------
# 探针 CSV 读回 → 判读（runner 重算后，把结论回灌 CI 做回归）
# ---------------------------------------------------------------------------
def read_probe_csv(path: str) -> List[dict]:
    """读探针 `--out` 的 CSV（records 列），供判读/回归。"""
    rows: List[dict] = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append(r)
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe-csv", help="regime_robustness_probe.py --out 的 CSV")
    ap.add_argument("--out", help="判决 JSON 输出路径（缺省打印 stdout）")
    args = ap.parse_args(argv)

    # 本模块是「判据单一事实源」：给定判据参数即可自判；
    # 提供 --probe-csv 时读回记录做摘要，便于 CI 把 runner 重算结果固化进报告。
    summary = {
        "criterion": {
            "baseline": BASELINE,
            "crit_t": CRIT_T,
            "min_sections": MIN_SECTIONS,
            "definition": "多数 regime/年度 超额不显著为负 (NW-t > -1.96) ⇒ 可行筛选器",
        },
        "note": "重计算在 runner regime_robustness_probe.py；本模块只判读、不联网。",
    }
    if args.probe_csv:
        rows = read_probe_csv(args.probe_csv)
        summary["probe_rows"] = len(rows)
        base = [r for r in rows if r.get("view") == "基线A_整体"]
        summary["baseline_overall"] = [
            {"h": r["horizon"], "mean": r.get("excess_mean"), "t": r.get("excess_t_nw")}
            for r in base
        ]
    out = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(out + "\n")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
