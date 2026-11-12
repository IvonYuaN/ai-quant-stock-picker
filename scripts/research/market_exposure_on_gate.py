#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场择时曝光对 gate 各臂的影响（**用修正后的等权指数**重做）。

## 为什么重做
2026-11-12 之前跑过一版，结论是「择时只降回撤、不提 Sharpe」。但那一版的
市场状态序列用的是 `(1 + 截面中位日收益).cumprod()` —— **错误口径**
（同窗口实测：等权买入持有 +2.30%、median 累乘 **−71.89%**）。
错误的市场序列会同时污染「什么时候减少曝光」这个决策本身，
所以旧结论**不可引用**，必须用 `aqsp.backtest.excess.equal_weight_curve` 重算。

## 方法
对每个臂、每个 gate 周期：
1. 读 `window`（起止日）与 `result.total_return`（引擎已实现，不重算）；
2. 算该期**开始前一交易日**为止的市场动量：等权指数近 `L` 个交易日涨跌幅；
3. 规则：动量 > 0 则全额曝光，否则按变体降到 `0`（空仓）或 `0.5`（半仓）；
4. 对比 年化 / Sharpe / 最大回撤。

🔴 无前视：市场动量只用**期开始之前**的数据；期收益是引擎产出的既成事实。

用法：
    python scripts/research/market_exposure_on_gate.py \\
        --arms-dir /tmp/holder_eval/arms --db "A股量化分析数据/astocks_raw.db" --L 20
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from aqsp.backtest.excess import equal_weight_curve, load_arm_periods  # noqa: E402

TRADING_DAYS = 250.0


def _market_momentum(curve: pd.Series, period_start: str, lookback: int) -> float | None:
    """截至 ``period_start`` **之前**（不含当日）的 lookback 日涨跌幅。"""
    key = period_start.replace("-", "")
    idx = curve.index.to_numpy()
    pos = int(np.searchsorted(idx, key, side="left"))  # 第一个 >= 起始日的下标
    if pos < lookback + 1:
        return None
    return float(curve.iloc[pos - 1] / curve.iloc[pos - 1 - lookback] - 1)


def _stats(rets: list[float], days: list[int]) -> dict:
    r = np.array(rets, dtype=float)
    if len(r) < 3:
        return {}
    eq = np.cumprod(1 + r)
    years = sum(days) / 365.0
    ann = float(eq[-1] ** (1 / years) - 1) if years > 0 else float("nan")
    per_year = len(r) / years if years > 0 else 1.0
    sd = float(r.std(ddof=1))
    sharpe = float(r.mean() / sd * np.sqrt(per_year)) if sd > 0 else float("nan")
    peak = np.maximum.accumulate(eq)
    mdd = float((eq / peak - 1).min())
    return {"ann": ann, "sharpe": sharpe, "mdd": mdd, "n": len(r)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arms-dir", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--sample", type=int, default=800)
    ap.add_argument("--L", type=int, default=20, help="市场动量回看交易日数")
    args = ap.parse_args(argv)

    arms = load_arm_periods(args.arms_dir)
    if not arms:
        print("❌ 未找到逐期记录")
        return 2
    curve = equal_weight_curve(args.db, sample=args.sample)
    if curve.empty:
        print("❌ 行情库不可用")
        return 2

    for run, arm_map in arms.items():
        print(f"\n### run {run.replace('_', ' ~ ')}｜市场状态=等权指数 {args.L} 日动量")
        print("| 臂 | 基线年化 | 空仓年化 | 半仓年化 | 基线Sharpe | 空仓 | 半仓 | 基线MDD | 空仓 | 半仓 | 空仓期数 |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        agg: dict[str, list[float]] = {"base": [], "off": [], "half": []}
        for arm, rows in sorted(arm_map.items()):
            rets, days = [], []
            off_rets, half_rets = [], []
            n_off = 0
            for r in rows:
                mom = _market_momentum(curve, r["start"], args.L)
                if mom is None:
                    continue
                d = (pd.Timestamp(r["end"]) - pd.Timestamp(r["start"])).days + 1
                rets.append(r["ret"])
                days.append(d)
                if mom > 0:
                    off_rets.append(r["ret"])
                    half_rets.append(r["ret"])
                else:
                    n_off += 1
                    off_rets.append(0.0)
                    half_rets.append(r["ret"] * 0.5)
            b = _stats(rets, days)
            o = _stats(off_rets, days)
            h = _stats(half_rets, days)
            if not b or not o or not h:
                continue
            agg["base"].append(b["sharpe"])
            agg["off"].append(o["sharpe"])
            agg["half"].append(h["sharpe"])
            print(
                f"| {arm} | {b['ann']:+.2%} | {o['ann']:+.2%} | {h['ann']:+.2%} | "
                f"{b['sharpe']:+.2f} | {o['sharpe']:+.2f} | {h['sharpe']:+.2f} | "
                f"{b['mdd']:.1%} | {o['mdd']:.1%} | {h['mdd']:.1%} | {n_off}/{len(rets)} |"
            )
        if agg["base"]:
            print(
                f"\n小结（{len(agg['base'])} 臂均值）：Sharpe 基线 {np.mean(agg['base']):+.2f}"
                f"｜空仓 {np.mean(agg['off']):+.2f}｜半仓 {np.mean(agg['half']):+.2f}"
            )
    print("\n⚠️ 这是**事后叠加**（不改引擎）：期收益已是既成事实，"
          "只验证「按市场动量减曝光」这条规则在这些期上的效果，不构成策略结论。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
