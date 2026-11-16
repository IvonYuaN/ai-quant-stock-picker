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


# ---------------------------------------------------------------------------
# 前端工件投影（runner 探针 --json-out 调用；CI 用合成 rows 回归其正确性）
# ---------------------------------------------------------------------------
# 探针 records 里 regime 名带机器前缀（bear_熊…），前端证据表要纯中文；映射在此。
REGIME_LABEL = {
    "bear_熊": "熊",
    "sideways_震荡": "震荡",
    "bull_牛": "牛",
}

# 02 节固定文案（研究结论，非行情数据）。工件透传给前端，前端 available=true 时全用之，
# 缺失时回退前端内嵌快照（同一组文案），故此处为单一事实源。
_POSITIONING = "选股筛选工具：不承诺正超额，只要求「不是垃圾策略」"
_CRITERION = "多数 regime / 多数年度下，相对同截面池等权的超额「不显著为负」（Newey-West t > −1.96，双侧 95%）"


def _to_float(value: object) -> float:
    """CSV 读回全是字符串；NaN/空 一律给 nan（前端再决定显示 — ）。"""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")


def _flag(value: object) -> bool:
    """CSV 里布尔存成 'True'/'False'；宽松解析，非真值一律 False。"""
    return str(value).strip().lower() in ("true", "1", "yes")


def _fmt_pct(x: float) -> str:
    return f"{x:+.2%}" if math.isfinite(x) else "—"


def _fmt_t(x: float) -> str:
    return f"{x:+.2f}" if math.isfinite(x) else "—"


def build_verdict_artifact(
    rows: List[dict],
    as_of: str,
    source: str = "regime_robustness_probe.py",
) -> Dict:
    """把探针 `--out` CSV 的 rows 投影成前端 `/api/aqsp/filter-verdict` 的工件。

    纯函数、零联网、零重算：只读探针已算好的数值列（excess_mean/excess_t_nw/
    not_sig_neg），做「中文 regime 名 + 百分比/t 格式化 + viable/cond 推导」。
    前端 `FilterVerdict` 接口字段与此一一对应；`available` 由后端端点负责，
    本函数只产「工件本体」。

    口径（对齐探针 main() 的判决逻辑，用 records 里已落地的字段重推，不重算统计量）：
    - viable = ≥2/3 报告的 regime 不显著为负，且「基线A_整体」不显著为负；
    - conditioning_ok（cond_pass）= 每个报告的 regime 下**所有**方向都不显著为负。
    """
    regime_rows = [r for r in rows if r.get("view") == "基线A_分regime"]
    overall_rows = [r for r in rows if r.get("view") == "基线A_整体"]
    cond_rows = [r for r in rows if r.get("view") == "条件化_方向×regime"]

    # evidence：regime 轴，按 (horizon, regime) 稳定排序，保证前端渲染顺序确定。
    evidence = [
        {
            "horizon": int(float(r.get("horizon", 0) or 0)),
            "regime": REGIME_LABEL.get(str(r.get("regime")), str(r.get("regime"))),
            "excess": _fmt_pct(_to_float(r.get("excess_mean"))),
            "t": _fmt_t(_to_float(r.get("excess_t_nw"))),
            "ok": _flag(r.get("not_sig_neg")),
        }
        for r in regime_rows
    ]
    # regime 固定展示序（对齐前端 02 节「熊/震荡/牛」），不依赖 Unicode 码位排序；
    # 未知 regime 排最后、按名称稳定。
    _ORDER = {"熊": 0, "震荡": 1, "牛": 2}
    evidence.sort(key=lambda e: (e["horizon"], _ORDER.get(e["regime"], 99), e["regime"]))

    def _h(r: dict) -> int:
        return int(float(r.get("horizon", 0) or 0))

    horizons = sorted({_h(r) for r in regime_rows} | { _h(r) for r in overall_rows })

    # viable：**逐 horizon 判、取交集**（逐位对齐探针 main() 的 per-horizon 判决）。
    # 探针口径：viable_h = (报告 regime>0) and (显著负 regime ≤ 1/3) and (该 h 整体不显著为负)。
    # 跨 horizon 混合会放宽（允许更多 regime 显著负）⇒ 必须分组判，避免与探针判决漂移。
    viable = bool(horizons)
    for H in horizons:
        rh = [r for r in regime_rows if _h(r) == H]
        n_rep = len(rh)
        n_neg = sum(1 for r in rh if not _flag(r.get("not_sig_neg")))
        oh = [r for r in overall_rows if _h(r) == H]
        overall_ok = bool(oh) and _flag(oh[0].get("not_sig_neg"))
        viable = viable and (n_rep > 0 and n_neg <= max(1, n_rep // 3) and overall_ok)

    # conditioning_ok（保守）：所有报告的 cond 行 not_sig_neg=True 且非空。
    # 探针 main() 按「3 regime 全方向不显著为负」累积，且某 regime 截面不足即 False；
    # CSV 的 cond 行只含截面≥MIN_SECTIONS 的方向，故「全部报告行 ok」已是保守下界。
    # 该轴前端只驱动一句提示文案（非生死判据），保守即可，不硬编码 regime 数。
    cond_pass = bool(cond_rows) and all(_flag(r.get("not_sig_neg")) for r in cond_rows)

    dynamic_note = (
        "各 regime 最优方向漂移 ⇒ 不做动态切换（过拟合风险）"
        if not cond_pass
        else "各 regime 各方向均不显著为负"
    )

    return {
        "viable": bool(viable),
        "baseline": BASELINE,
        "positioning": _POSITIONING,
        "criterion": _CRITERION,
        "dynamicSwitching": False,
        "dynamicNote": dynamic_note,
        "conditioning_ok": bool(cond_pass),
        "evidence": evidence,
        "as_of": as_of,
        "source": source,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe-csv", help="regime_robustness_probe.py --out 的 CSV")
    ap.add_argument("--out", help="判决 JSON 输出路径（缺省打印 stdout）")
    ap.add_argument(
        "--emit-verdict", action="store_true",
        help="配 --probe-csv：额外把 build_verdict_artifact 的前端工件（verdict_artifact）一并写出/打印",
    )
    ap.add_argument("--as-of", default="unknown",
                   help="verdict 工件的 as_of（= runner 探针最新交易日，YYYY-MM-DD）")
    ap.add_argument("--source", default="filter_criterion_guard",
                   help="verdict 工件的来源标注（供前端 02 节展示）")
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
        # 前端 02 节工件投影：给一份探针 CSV 即可离线一键生成 pit_cache/filter_verdict.json
        # （无需重跑探针），供 /api/aqsp/filter-verdict 读取、前端动态渲染。
        # 向后兼容：不加 --emit-verdict 时输出形状不变。
        if args.emit_verdict:
            summary["verdict_artifact"] = build_verdict_artifact(
                rows, as_of=args.as_of, source=args.source
            )
    out = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(out + "\n")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
