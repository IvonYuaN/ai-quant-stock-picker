#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bp（账面市值比）的组合级 A/B：把 bp 并入 composite 后是否改善组合收益。

为什么单独做
------------
2026-11-12 基本面筛选：`bp` 在**作业面**（2f 达标池）IC +0.0276、t=1.40、正占比 56%，
是四个基本面因子中唯一接近门槛的（`ep`/`roe`/`profit_yoy` 池内 |t|<1）。
它与 `macd_hist`（池内 t=1.63）是仅剩的两个边缘候选，都必须过**组合级**这一关。

口径
----
生产 composite 只用 momentum(0.3) + pvc(0.3)，归一化分母 0.6。
加入 bp 权重 w 后分母为 0.6+w，**同口径重算**（不能只加不减，否则是口径漂移）：
    score = (0.3*mom + 0.3*pvc + w*bp) / (0.6 + w)
bp 按交易日取 `1/PB_MRQ`，带 200 天陈旧度上限；池仍由**生产口径**（w=0）定义，
只比较"池内怎么排"。

⚠️ 毛收益，未扣成本；换手率同时输出，供判断是否值得进 gate。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "research"))

from fundamentals_pool_ic import _latest, load_valuation  # noqa: E402


def _forward_return(close: pd.Series, horizon: int) -> pd.Series:
    v = close.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    if len(v) > horizon:
        out[:-horizon] = v[horizon:] / v[:-horizon] - 1.0
    return pd.Series(out, index=close.index)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--pit-cache", default="pit_cache")
    ap.add_argument("--start", default="2023-12-01")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--horizon", type=int, default=10)
    ap.add_argument("--step", type=int, default=10)
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--weights", default="0.1,0.2,0.3")
    args = ap.parse_args(argv)
    if args.step < args.horizon:
        print("❌ step < horizon")
        return 2

    from aqsp.strategies.composite import CompositeStrategy
    from aqsp.strategies.thresholds import load_thresholds

    th = load_thresholds(str(REPO_ROOT / "config" / "thresholds.yaml"))
    base = CompositeStrategy(thresholds=th)
    thr = float(th.composite.min_total_score)

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    px = pd.read_sql(
        "SELECT ts_code, trade_date, open, high, low, close, volume FROM daily_qfq"
        " WHERE trade_date >= ?",
        con, params=(args.start.replace("-", "")[:4] + "0101",),
    )
    con.close()
    px = px.sort_values(["ts_code", "trade_date"])
    px["date"] = px["trade_date"]
    frames = {ts: g.reset_index(drop=True) for ts, g in px.groupby("ts_code")}
    fwd = {ts: _forward_return(df["close"], args.horizon) for ts, df in frames.items()}
    dates = sorted(px["trade_date"].unique())
    rebal = [d for d in dates if args.start.replace("-", "") <= d <= args.end.replace("-", "")][:: args.step]

    _, bp_map = load_valuation(Path(args.pit_cache))
    print(f"[init] 票 {len(frames)}｜截面 {len(rebal)}｜bp 覆盖 {len(bp_map)} 只")
    weights = [float(w) for w in args.weights.split(",") if w.strip()]
    rows: list[dict] = []

    for d in rebal:
        data, labels = {}, {}
        for ts, df in frames.items():
            idx = int(np.searchsorted(df["trade_date"].to_numpy(), d, side="right"))
            if idx < 60:
                continue
            data[ts] = df.iloc[:idx]
            r = fwd[ts].iloc[idx - 1]
            if np.isfinite(r):
                labels[ts] = float(r)
        if len(data) < 500:
            continue
        b = base.calculate_score(data)
        mom = base.momentum_strategy.calculate_score(data)
        pvc = base.price_volume_corr_strategy.calculate_score(data)
        iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        bpv = {s: _latest(bp_map.get(s.split(".")[0]), iso, 200) for s in data}

        pool = sorted((s for s in data if s in labels and b.get(s, 0) >= thr), key=lambda s: -b[s])
        n = args.top_n
        base_sel = pool[:n]
        if len(base_sel) < n:
            continue
        base_ret = float(np.mean([labels[s] for s in base_sel]))
        for w in weights:
            usable = [s for s in pool if bpv.get(s) is not None]
            if len(usable) < n:
                continue
            denom = 0.6 + w
            blended = {
                s: (0.3 * mom.get(s, 0.5) + 0.3 * pvc.get(s, 0.5)
                    + w * float(bpv[s])) / denom
                for s in usable
            }
            sel = sorted(usable, key=lambda s: -blended[s])[:n]
            rows.append({"date": d, "w": w, "ret_base": base_ret,
                         "ret_add": float(np.mean([labels[s] for s in sel])),
                         "changed": len(set(base_sel) - set(sel))})

    if not rows:
        print("❌ 无有效截面")
        return 2
    df = pd.DataFrame(rows)
    bm = float(df["ret_base"].mean())
    if abs(bm) > 0.08:
        print(f"\n⚠️ 基线每期 {bm:+.2%} 超合理区间 ⇒ 口径可疑，结论不可用")
    print(f"\n[bp 组合 A/B] n={args.top_n}（毛收益，未扣成本）")
    for w, g in df.groupby("w"):
        diff = (g["ret_add"] - g["ret_base"]).to_numpy()
        m, sd = float(diff.mean()), float(diff.std(ddof=1))
        t = m / (sd / np.sqrt(len(diff))) if sd > 0 else float("nan")
        ppy = 250.0 / args.step
        sh = g["ret_add"].mean() / g["ret_add"].std(ddof=1) * np.sqrt(ppy) if g["ret_add"].std(ddof=1) > 0 else float("nan")
        sh0 = g["ret_base"].mean() / g["ret_base"].std(ddof=1) * np.sqrt(ppy) if g["ret_base"].std(ddof=1) > 0 else float("nan")
        print(f"  w={w:g}: 截面 {len(g)} | 基线 {g['ret_base'].mean():+.4%} → 加入后 "
              f"{g['ret_add'].mean():+.4%} | Δ={m:+.4%} t={t:+.2f} | "
              f"粗Sharpe {sh0:+.2f}→{sh:+.2f} | 换掉 {g['changed'].mean():.1f}/{args.top_n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
