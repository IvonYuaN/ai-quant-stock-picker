#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 overlay 路线的真实 select_stocks 路径（只读）：
- enabled（overlay on）vs disabled（纯 2f）所选 top-N 的前向收益
- TD-0 不变量：calculate_detailed_scores[s]["total"] == calculate_score(s)
- holder 辅助列存在性
"""
import os
import sqlite3
import numpy as np
import pandas as pd

os.environ["AQSP_HOLDER_COUNT_CSV"] = (
    "/Users/ivon/Documents/AI量化选股/research/theme_factor_probe/holder_count.csv"
)

from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.thresholds import load_thresholds
from aqsp.strategies.base import StrategyConfig

DB = "/Users/ivon/Documents/AI量化选股/A股量化分析数据/astocks_raw.db"
YAML = "/Users/ivon/Documents/AI量化选股/.worktrees/hc-factor/config/thresholds.yaml"
N_SELECT = 50
SEED = 20261110


def sym_to_ts(code6):
    c = str(code6).zfill(6)
    return c + (".SZ" if c[0] in "03" else ".SH")


def main():
    th = load_thresholds(YAML)
    th_disabled = th.with_overrides("holder_concentration", {"enabled": False})

    hc = pd.read_csv(os.environ["AQSP_HOLDER_COUNT_CSV"])
    by_sym = hc.groupby("symbol").size()
    keep = [sym_to_ts(s) for s in by_sym[by_sym >= 2].index]
    rng = np.random.default_rng(SEED)
    if len(keep) > 2000:
        keep = list(rng.choice(keep, 2000, replace=False))
    print(f"universe: {len(keep)} symbols")

    con = sqlite3.connect(DB)
    q = (
        "SELECT ts_code, trade_date, open, high, low, close, volume, amount "
        f"FROM daily_qfq WHERE ts_code IN ({','.join('?' * len(keep))}) "
        "AND trade_date >= '20230101'"
    )
    px = pd.read_sql(q, con, params=keep)
    con.close()
    px["trade_date"] = px["trade_date"].astype(str)
    px["date"] = pd.to_datetime(px["trade_date"]).dt.strftime("%Y-%m-%d")
    px = px.sort_values(["ts_code", "trade_date"])

    g = px.groupby("ts_code")
    for h in (5, 10, 20):
        px[f"fwd{h}"] = g["close"].shift(-h) / px["close"] - 1
    fwd_idx = px.set_index(["ts_code", "trade_date"])[["fwd5", "fwd10", "fwd20"]]

    all_dates = sorted(px["trade_date"].unique())
    cand = [d for d in all_dates if "20250930" <= d <= "20260530"]
    step = max(1, len(cand) // 8)
    val_dates = cand[::step][:8]
    print("valuation dates:", val_dates)

    frames = {ts: df for ts, df in px.groupby("ts_code")}

    def build_data(V):
        d = {}
        for ts, df in frames.items():
            sub = df[df["trade_date"] <= V]
            if len(sub) >= 60:
                d[ts] = sub
        return d

    cs_on = CompositeStrategy(thresholds=th)
    cs_off = CompositeStrategy(thresholds=th_disabled)

    # TD-0 不变量抽查（首个有数据的估值日）
    V0 = val_dates[0]
    d0 = build_data(V0)
    sc_on = cs_on.calculate_score(d0)
    det_on = cs_on.calculate_detailed_scores(d0)
    mism = [s for s in sc_on if abs(sc_on[s] - det_on[s]["total"]) > 1e-9]
    has_aux = all("holder_concentration" in det_on[s] for s in det_on)
    print(f"\n[TD-0] total≡calculate_score 不一致数: {len(mism)} / {len(sc_on)}")
    print(f"[TD-0] holder_concentration 辅助列存在: {has_aux}")

    agg_on, agg_off, overlaps = [], [], []
    for V in val_dates:
        data = build_data(V)
        sel_on = cs_on.select_stocks(data, n=N_SELECT)
        sel_off = cs_off.select_stocks(data, n=N_SELECT)

        def fwd_of(sel):
            rows = []
            for s in sel:
                if (s, V) in fwd_idx.index:
                    r = fwd_idx.loc[(s, V)]
                    rows.append((r["fwd5"], r["fwd10"], r["fwd20"]))
            return np.array(rows, dtype=float)

        r_on, r_off = fwd_of(sel_on), fwd_of(sel_off)
        if len(r_on) and len(r_off):
            agg_on.append(r_on.mean(axis=0))
            agg_off.append(r_off.mean(axis=0))
            overlaps.append(len(set(sel_on) & set(sel_off)))

    A = np.array(agg_off)
    B = np.array(agg_on)
    print(f"\n=== overlay(on) vs 2f(off) top-{N_SELECT} 前向收益（按估值日均值）===")
    labels = ["fwd5", "fwd10", "fwd20"]
    for j, lab in enumerate(labels):
        d = B[:, j] - A[:, j]
        mean = d.mean()
        sd = d.std(ddof=1) if len(d) > 1 else 0.0
        t = mean / (sd / np.sqrt(len(d))) if sd > 0 else np.nan
        win = (d > 0).mean() * 100
        print(
            f"  {lab:6}: 2f={A[:, j].mean()*100:+6.2f}%  overlay={B[:, j].mean()*100:+6.2f}%  "
            f"Δ={mean*100:+.3f}%  t={t:+.2f}  胜率={win:.0f}%  (n={len(d)} 日)"
        )
    if overlaps:
        print(
            f"\n=== 选股重叠 ===\n  平均重叠 top-{N_SELECT}: "
            f"{np.mean(overlaps):.0f}/{N_SELECT} ({np.mean(overlaps)/N_SELECT*100:.0f}%)"
        )


if __name__ == "__main__":
    main()
