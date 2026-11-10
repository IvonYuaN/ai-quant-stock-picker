#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""holder_concentration 因子诊断（只读）：
1) holder 分数分布 + 与 2 因子 composite 分的相关性（是否真正交/是否塌缩到 0.5）
2) 纯 holder 选 top-N 的前向收益（信号单独是否成立）
3) overlay 设计：2 因子 top-100 内按 holder 重排取 top-50 的前向收益
   （真突破假说：holder 作为正交确认/过滤层，而非 1/3 等权混入）

用于判断「真突破」应走 blend 还是 overlay/filter 集成路线。
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
from aqsp.strategies.holder_concentration import HolderConcentrationStrategy
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
    # 🔴 关键约束：holder_count.csv 只有 4 个季度（2025-09-30 ~ 2026-06-30）。
    # 估值日 < 2025-09-30 时 holder 因子无数据 → 全标 0.5（污染结果）。
    # 故只取「有真实 holder 数据」且 fwd20 仍存在（≤ 最新季度 - 约 20 交易日）的窗口。
    cand = [d for d in all_dates if "20250930" <= d <= "20260530"]
    step = max(1, len(cand) // 8)
    val_dates = cand[::step][:8]
    print("valuation dates (holder-data-valid window):", val_dates)

    frames = {ts: df for ts, df in px.groupby("ts_code")}

    def build_data(V):
        d = {}
        for ts, df in frames.items():
            sub = df[df["trade_date"] <= V]
            if len(sub) >= 60:
                d[ts] = sub
        return d

    holder_strat = HolderConcentrationStrategy(
        StrategyConfig(name="holder_concentration"), th
    )
    cs2 = CompositeStrategy(thresholds=th_disabled)
    cs3 = CompositeStrategy(thresholds=th)

    def fwd_of(top, V):
        rows = []
        for s in top:
            if (s, V) in fwd_idx.index:
                r = fwd_idx.loc[(s, V)]
                rows.append((r["fwd5"], r["fwd10"], r["fwd20"]))
        return np.array(rows, dtype=float)

    def summarize(name, per_date):
        parts = [p for p in per_date if p is not None and len(p) > 0]
        if not parts:
            print(f"  {name}: (no data)")
            return
        arr = np.concatenate(parts)  # (total_selected, 3)
        mean = arr.mean(axis=0)
        print(
            f"  {name:34}: fwd5={mean[0]*100:+6.2f}%  fwd10={mean[1]*100:+6.2f}%  "
            f"fwd20={mean[2]*100:+6.2f}%  (n={len(arr)} 票·日)"
        )

    pure_holder, overlay, blend2, blend3 = [], [], [], []
    holder_dist, corr_with_2f = [], []
    # 按估值日记录均值，供显著性检验（overlay vs 2f 配对）
    overlay_bydate, blend2_bydate = [], []
    for V in val_dates:
        data = build_data(V)
        if len(data) < 100:
            continue
        # holder 分数（单独）
        h_scores = holder_strat.calculate_score(data)
        # 2 因子 composite 总分
        s2 = cs2.calculate_score(data)
        # 3 因子 composite 总分
        s3 = cs3.calculate_score(data)

        hv = np.array(list(h_scores.values()))
        holder_dist.append(hv)
        # holder 与 2f 分的 spearman 相关（正交性）
        common = [s for s in h_scores if s in s2]
        if len(common) > 10:
            hr = pd.Series({s: h_scores[s] for s in common})
            sr = pd.Series({s: s2[s] for s in common})
            if hr.nunique() > 1 and sr.nunique() > 1:
                rho = hr.corr(sr, method="spearman")
                if not pd.isna(rho):
                    corr_with_2f.append(rho)

        ranked_h = sorted(h_scores, key=lambda s: (-h_scores[s], s))
        ranked_2 = sorted(s2, key=lambda s: (-s2[s], s))
        ranked_3 = sorted(s3, key=lambda s: (-s3[s], s))

        # 纯 holder top-50
        pure_holder.append(fwd_of(ranked_h[:N_SELECT], V))
        # blend 3f top-50
        blend3.append(fwd_of(ranked_3[:N_SELECT], V))
        # blend 2f top-50
        blend2.append(fwd_of(ranked_2[:N_SELECT], V))
        # overlay: 2f top-100 内按 holder 重排取 top-50
        top100_2 = set(ranked_2[:100])
        overlay_pool = [s for s in ranked_h if s in top100_2]
        ov = fwd_of(overlay_pool[:N_SELECT], V)
        overlay.append(ov)
        if len(ov) > 0:
            overlay_bydate.append(ov.mean(axis=0))
            blend2_bydate.append(fwd_of(ranked_2[:N_SELECT], V).mean(axis=0))

    # 分布与相关性
    if holder_dist:
        allh = np.concatenate(holder_dist)
        print(f"\n=== holder 分数分布（{len(allh)} 只·估值日）===")
        print(
            f"  min={allh.min():.3f} p10={np.percentile(allh,10):.3f} "
            f"median={np.median(allh):.3f} p90={np.percentile(allh,90):.3f} "
            f"max={allh.max():.3f}  mean={allh.mean():.3f}  std={allh.std():.3f}"
        )
        print(f"  比例 in [0.4,0.6] (近中性): {((allh>=0.4)&(allh<=0.6)).mean()*100:.0f}%")
    if corr_with_2f:
        print(f"\n=== holder 分 vs 2f composite 分 Spearman 相关（正交性）===")
        print(f"  各估值日: {[f'{c:.2f}' for c in corr_with_2f]}")
        print(f"  均值: {np.mean(corr_with_2f):.3f}  → 接近 0 = 真正交")

    print(f"\n=== 三种集成路线的 top-{N_SELECT} 前向收益（按估值日均值）===")
    summarize("2f blend (mom+pvc)", blend2)
    summarize("3f blend (+holder 1/3等权)", blend3)
    summarize("pure holder (仅筹码集中)", pure_holder)
    summarize("overlay (2f top100 内按holder重排)", overlay)

    # 显著性：overlay vs 2f 配对（按估值日均值差分）
    if overlay_bydate and blend2_bydate and len(overlay_bydate) >= 2:
        A = np.array(blend2_bydate)   # (dates,3)
        B = np.array(overlay_bydate)  # (dates,3)
        diff = B - A
        print(f"\n=== overlay vs 2f 配对显著性（按 {len(diff)} 个估值日均值差分）===")
        labels = ["fwd5", "fwd10", "fwd20"]
        for j, lab in enumerate(labels):
            d = diff[:, j]
            mean = d.mean()
            sd = d.std(ddof=1)
            t = mean / (sd / np.sqrt(len(d))) if sd > 0 else np.nan
            win = (d > 0).mean() * 100
            print(
                f"  {lab:6}: Δmean={mean*100:+.3f}%  t={t:+.2f}  "
                f"胜率(overlay>2f)={win:.0f}%  (n={len(d)} 日)"
            )


if __name__ == "__main__":
    main()
