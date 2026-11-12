#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选因子快速验证：龙虎榜三变体（作业面 IC）+ macd_hist 组合级 A/B。

设计意图：一次遍历价格数据，同时回答两个问题，避免两次 30 分钟级重算。

一、龙虎榜三变体（相对上一版的关键改进）
--------------------------------------
上一版用「净买入 / 流通市值」的 20 日**和**，被作业面筛否（池内 t=−0.54）。
本版改用**成交额归一**并区分窗口：
- ``lhb_net5``  = Σ(BILLBOARD_NET_AMT) / Σ(ACCUM_AMOUNT)，近 5 个交易日
- ``lhb_net20`` = 同上，近 20 个交易日
- ``lhb_freq20``= 近 20 日上榜次数（关注度代理）
🔴 一律**滞后 1 个交易日**（龙虎榜收盘后才发布，否则前视）。
🔴 数据里的 ``D1/D5/D10/D20_CLOSE_ADJCHRATE`` 是**未来收益**，**绝不进因子**。

二、macd_hist 组合级 A/B
----------------------
`macd_hist` 在作业面 IC 为 +0.0237（t=+1.63，池内 63% 正），是现有因子里最好的，
但它是**加权维度**而非 overlay ⇒ 要看加入后的**组合收益**，不能只看 IC。
做法：在同一池内，把 macd 以 w ∈ {0.1, 0.2, 0.3} 并入 composite 后重排取 top-n，
与 w=0（生产口径）对比每期收益。

⚠️ 组合收益是**毛收益**（未扣成本/滑点），非 gate 口径；仅用于判断"值不值得进 gate"。
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


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3:
        return float("nan")
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    ra = ra - ra.mean()  # 重新赋值：to_numpy() 可能是只读视图
    rb = rb - rb.mean()
    denom = np.sqrt((ra**2).sum() * (rb**2).sum())
    return float((ra * rb).sum() / denom) if denom > 0 else float("nan")


def _forward_return(close: pd.Series, horizon: int) -> pd.Series:
    v = close.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    if len(v) > horizon:
        out[:-horizon] = v[horizon:] / v[:-horizon] - 1.0
    return pd.Series(out, index=close.index)


def load_lhb(cache: Path) -> dict[str, pd.DataFrame]:
    """symbol(6) → DataFrame(index=date, columns=[net, amt])，按日期升序。"""
    p = cache / "longhubang_history.csv"
    if not p.exists():
        return {}
    cols = {"TRADE_DATE", "SECURITY_CODE", "BILLBOARD_NET_AMT", "ACCUM_AMOUNT"}
    df = pd.read_csv(p, usecols=lambda c: c in cols)
    df["d"] = df["TRADE_DATE"].astype(str).str.slice(0, 10)
    df["net"] = pd.to_numeric(df["BILLBOARD_NET_AMT"], errors="coerce")
    df["amt"] = pd.to_numeric(df["ACCUM_AMOUNT"], errors="coerce")
    df = df.dropna(subset=["net"])
    out: dict[str, pd.DataFrame] = {}
    for code, g in df.groupby(df["SECURITY_CODE"].astype(str).str.zfill(6)):
        agg = g.groupby("d")[["net", "amt"]].sum().sort_index()
        out[code] = agg
    return out


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
        print("❌ step < horizon：截面重叠会虚高 t，拒绝")
        return 2

    from aqsp.strategies.composite import CompositeStrategy
    from aqsp.strategies.macd_hist import MACDHistStrategy
    from aqsp.strategies.thresholds import load_thresholds
    from aqsp.strategies.base import StrategyConfig

    th = load_thresholds(str(REPO_ROOT / "config" / "thresholds.yaml"))
    base = CompositeStrategy(thresholds=th)
    thr = float(th.composite.min_total_score)
    macd = MACDHistStrategy(StrategyConfig(name="macd_hist", enabled=True), th)

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    px = pd.read_sql(
        "SELECT ts_code, trade_date, open, high, low, close, volume FROM daily_qfq WHERE trade_date >= ?",
        con, params=(args.start.replace("-", "")[:4] + "0101",),
    )
    con.close()
    px["date"] = px["trade_date"]
    px = px.sort_values(["ts_code", "trade_date"])
    frames = {ts: g.reset_index(drop=True) for ts, g in px.groupby("ts_code")}
    fwd = {ts: _forward_return(df["close"], args.horizon) for ts, df in frames.items()}
    dates = sorted(px["trade_date"].unique())
    rebal = [d for d in dates if args.start.replace("-", "") <= d <= args.end.replace("-", "")][:: args.step]
    lhb = load_lhb(Path(args.pit_cache))
    print(f"[init] 票 {len(frames)}｜截面 {len(rebal)}｜龙虎榜 {len(lhb)} 只")

    rows: list[dict] = []
    weights = [float(w) for w in args.weights.split(",") if w.strip()]
    port: list[dict] = []

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
        mh = macd.calculate_score(data)
        # 🔴 必须按 composite **降序**取池头：否则 pool[:n] 只是代码顺序的前 n 只，
        # 基线变成"随机 10 只"（2026-11-12 实测踩过：得出"基线每期 +2.77%"的荒谬值）。
        pool = sorted(
            (s for s in data if s in labels and b.get(s, 0) >= thr),
            key=lambda s: -b[s],
        )

        # ---------------- 龙虎榜三变体 ----------------
        i_now = dates.index(d) if d in dates else None
        if i_now is not None and lhb:
            d_prev = dates[max(0, i_now - 1)]        # 滞后 1 个交易日
            w5 = dates[max(0, i_now - 5)]
            w20 = dates[max(0, i_now - 20)]
            v5, v20, vf = {}, {}, {}
            for s in data:
                agg = lhb.get(s.split(".")[0])
                if agg is None:
                    continue
                for lo, hi, tag in ((w5, d_prev, 5), (w20, d_prev, 20)):
                    sub = agg.loc[(agg.index >= lo) & (agg.index <= hi)]
                    if sub.empty:
                        continue
                    amt = float(sub["amt"].sum())
                    if amt <= 0:
                        continue
                    val = float(sub["net"].sum()) / amt
                    (v5 if tag == 5 else v20)[s] = val
                cnt = int(((agg.index >= w20) & (agg.index <= d_prev)).sum())
                if cnt:
                    vf[s] = float(cnt)
            for name, sc in (("lhb_net5", v5), ("lhb_net20", v20), ("lhb_freq20", vf)):
                act = [s for s in data if s in labels and abs(sc.get(s, 0.0)) > 1e-12]
                if len(act) >= 100:
                    rows.append({"date": d, "factor": name, "scope": "all", "n": len(act),
                                 "ic": _spearman(np.array([sc[s] for s in act]),
                                                 np.array([labels[s] for s in act]))})
                pact = [s for s in pool if abs(sc.get(s, 0.0)) > 1e-12]
                if len(pact) >= 30:
                    rows.append({"date": d, "factor": name, "scope": "pool", "n": len(pact),
                                 "ic": _spearman(np.array([sc[s] for s in pact]),
                                                 np.array([labels[s] for s in pact]))})

        # ---------------- macd 组合级 A/B ----------------
        # composite = 加权平均；生产口径只有 mom(0.3)+pvc(0.3) ⇒ 分母 0.6。
        # 加入 macd 后分母 0.6+w，按同一归一化口径重算，避免"只加不减"的口径漂移。
        n_sel = args.top_n
        base_sel = pool[:n_sel]
        if len(base_sel) == n_sel:
            base_ret = float(np.mean([labels[s] for s in base_sel]))
            for w in weights:
                denom = 0.3 + 0.3 + w
                blended = {
                    s: (0.3 * mom.get(s, 0.5) + 0.3 * pvc.get(s, 0.5) + w * mh.get(s, 0.5)) / denom
                    for s in pool
                }
                sel = sorted(pool, key=lambda s: -blended[s])[:n_sel]
                ret = float(np.mean([labels[s] for s in sel]))
                port.append({"date": d, "w": w, "ret_base": base_ret, "ret_add": ret,
                             "diff": ret - base_ret,
                             "changed": len(set(base_sel) - set(sel))})

    if rows:
        df = pd.DataFrame(rows)
        print("\n[龙虎榜三变体] 作业面 IC")
        print("| 因子 | 全市场 IC | t | 池内 IC | 池内 t | 池内正占比 | 判读 |")
        print("|---|---|---|---|---|---|---|")
        for name, g in df.groupby("factor"):
            o = []
            for scope in ("all", "pool"):
                v = g[g["scope"] == scope]["ic"].dropna().to_numpy()
                if len(v) >= 2:
                    m, sd = float(v.mean()), float(v.std(ddof=1))
                    o.append((m, m / (sd / np.sqrt(len(v))) if sd > 0 else float("nan"),
                              float((v > 0).mean())))
                else:
                    o.append((float("nan"),) * 3)
            (ma, ta, _), (mp, tp, hit) = o
            verdict = ("✅ 有效" if abs(tp) >= 2 and abs(mp) >= 0.01
                       else "⚠️ 边缘" if abs(tp) >= 1 and abs(mp) >= 0.01 else "❌ 无效")
            print(f"| {name} | {ma:+.4f} | {ta:+.2f} | {mp:+.4f} | {tp:+.2f} | {hit:.0%} | {verdict} |")

    if port:
        pf = pd.DataFrame(port)
        # 护栏：10 日持仓的每期收益若超过 ±8%，几乎一定是选股口径写错了
        # （2026-11-12 实测：pool 未排序 ⇒ 基线"每期 +2.77%"，荒谬）
        base_mean = float(pf["ret_base"].mean())
        if abs(base_mean) > 0.08:
            print(
                f"\n⚠️ 基线每期收益 {base_mean:+.2%} 超出合理区间 ⇒ "
                "选股口径可能有问题，本次 macd 结论**不可用**（先查 pool 是否按分数降序）"
            )
        print(f"\n[macd 组合 A/B] n={args.top_n}（毛收益，未扣成本）")
        for w, g in pf.groupby("w"):
            d_ = g["diff"].to_numpy()
            m, sd = float(d_.mean()), float(d_.std(ddof=1))
            tt = m / (sd / np.sqrt(len(d_))) if sd > 0 else float("nan")
            ppy = 250.0 / args.step
            sh = (g["ret_add"].mean() / g["ret_add"].std(ddof=1) * np.sqrt(ppy)
                  if g["ret_add"].std(ddof=1) > 0 else float("nan"))
            sh0 = (g["ret_base"].mean() / g["ret_base"].std(ddof=1) * np.sqrt(ppy)
                   if g["ret_base"].std(ddof=1) > 0 else float("nan"))
            print(f"  w={w:g}: 截面 {len(g)} | 基线每期 {g['ret_base'].mean():+.4%} → "
                  f"加入后 {g['ret_add'].mean():+.4%} | Δ={m:+.4%} t={tt:+.2f} | "
                  f"粗Sharpe {sh0:+.2f}→{sh:+.2f} | 换掉 {g['changed'].mean():.1f}/{args.top_n}")
        print("  ⚠️ 毛收益未扣成本；Δ 需显著为正且换手可控才值得进 gate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
