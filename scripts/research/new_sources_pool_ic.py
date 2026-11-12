#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三类新数据源的「作业面 IC」筛选（资金流 / 事件 / 解禁）。

背景
----
2026-11-12 全因子筛选结论：现有 11 个因子在**作业面（2f 达标池内）无一 |t|≥2**，
最好的 macd_hist 也只有 t=1.63。而它们在全市场 IC 上普遍显著（pvc t=5.14 等）
⇒ **瓶颈是因子供给，不是权重**。故转向新数据源。

本脚本对三个新源各构造 1 个因子，用与 `holder_ic_probe.py` **同一套口径**
（同窗口、同 horizon、同池定义、同 t 检验）给出「全市场 IC vs 池内 IC」。

因子口径（**PIT 是硬约束**）
--------------------------
1. ``lhb_net``：``(BUY_AMT - SELL_AMT) / FREE_MARKET_CAP`` 近 20 个交易日的和。
   🔴 龙虎榜是**收盘后**发布 ⇒ 必须**滞后 1 个交易日**才能用（否则前视）。
2. ``forecast``：业绩预告按 **``NOTICE_DATE``（披露日）** 生效——不是报告期
   （同 holder 的教训：用季末日 = 前视 1~4 个月）。正向类型 +1 / 负向 −1，
   在 60 个自然日内衰减（线性）。
3. ``lockup``：未来 30 个自然日内解禁股数 / 总股本，取负号（供给压力）。
   ⚠️ 解禁计划是**提前公告**的公开信息，因此在 as_of 使用未来解禁日**不构成前视**；
   但本数据源不含「公告日」，故这一条属**假设**，结论须标注。

用法
----
    python scripts/research/new_sources_pool_ic.py \
        --db "A股量化分析数据/astocks_raw.db" --pit-cache pit_cache \
        --start 2023-12-01 --end 2026-09-30 --horizon 10 --step 10
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

_POS_TYPES = ("预增", "略增", "扭亏", "续盈", "减亏")
_NEG_TYPES = ("预减", "略减", "首亏", "续亏", "增亏")


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3:
        return float("nan")
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    ra -= ra.mean()
    rb -= rb.mean()
    denom = np.sqrt((ra**2).sum() * (rb**2).sum())
    return float((ra * rb).sum() / denom) if denom > 0 else float("nan")


def _forward_return(close: pd.Series, horizon: int) -> pd.Series:
    v = close.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    if len(v) > horizon:
        out[:-horizon] = v[horizon:] / v[:-horizon] - 1.0
    return pd.Series(out, index=close.index)


def _norm_date(s: pd.Series) -> pd.Series:
    return s.astype(str).str.slice(0, 10)


def load_lhb(cache: Path) -> dict[str, list[tuple[str, float]]]:
    """symbol(6) → [(trade_date, net_ratio)] 升序。"""
    p = cache / "longhubang_history.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p, usecols=lambda c: c in {
        "TRADE_DATE", "SECURITY_CODE", "BILLBOARD_BUY_AMT", "BILLBOARD_SELL_AMT",
        "FREE_MARKET_CAP", "BILLBOARD_DEAL_AMT"})
    df["d"] = _norm_date(df["TRADE_DATE"])
    buy = pd.to_numeric(df.get("BILLBOARD_BUY_AMT"), errors="coerce")
    sell = pd.to_numeric(df.get("BILLBOARD_SELL_AMT"), errors="coerce")
    cap = pd.to_numeric(df.get("FREE_MARKET_CAP"), errors="coerce")
    net = (buy - sell) / cap.replace(0, np.nan)
    df = df.assign(net=net).dropna(subset=["net"])
    out: dict[str, list[tuple[str, float]]] = {}
    for code, g in df.groupby(df["SECURITY_CODE"].astype(str).str.zfill(6)):
        gg = g.groupby("d")["net"].sum().sort_index()
        out[code] = list(zip(gg.index, gg.to_numpy()))
    return out


def load_forecast(cache: Path) -> dict[str, list[tuple[str, float]]]:
    """symbol(6) → [(notice_date, signal)] 升序；signal ∈ {+1,-1}。"""
    p = cache / "forecast_history.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p, usecols=lambda c: c in {
        "NOTICE_DATE", "REPORT_DATE", "SECURITY_CODE", "PREDICT_TYPE"})
    df["d"] = _norm_date(df["NOTICE_DATE"])
    t = df["PREDICT_TYPE"].astype(str)
    sig = pd.Series(0.0, index=df.index)
    sig[t.str.contains("|".join(_POS_TYPES), na=False)] = 1.0
    sig[t.str.contains("|".join(_NEG_TYPES), na=False)] = -1.0
    df = df.assign(sig=sig)
    df = df[df["sig"] != 0]
    out: dict[str, list[tuple[str, float]]] = {}
    for code, g in df.groupby(df["SECURITY_CODE"].astype(str).str.zfill(6)):
        gg = g.sort_values("d").groupby("d")["sig"].last()
        out[code] = list(zip(gg.index, gg.to_numpy()))
    return out


def load_lockup(cache: Path) -> dict[str, list[tuple[str, float]]]:
    """symbol(6) → [(free_date, ratio)]，ratio = 解禁股数/总股本。"""
    p = cache / "lockup_history.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p, usecols=lambda c: c in {
        "FREE_DATE", "SECURITY_CODE", "CURRENT_FREE_SHARES", "FREE_SHARES", "NON_FREE_SHARES"})
    df["d"] = _norm_date(df["FREE_DATE"])
    cur = pd.to_numeric(df.get("CURRENT_FREE_SHARES"), errors="coerce")
    free = pd.to_numeric(df.get("FREE_SHARES"), errors="coerce")
    nonfree = pd.to_numeric(df.get("NON_FREE_SHARES"), errors="coerce")
    total = free + nonfree
    df = df.assign(ratio=cur / total.replace(0, np.nan))
    out: dict[str, list[tuple[str, float]]] = {}
    for code, g in df.dropna(subset=["ratio"]).groupby(
        df["SECURITY_CODE"].astype(str).str.zfill(6)
    ):
        gg = g.sort_values("d").groupby("d")["ratio"].sum()
        out[code] = list(zip(gg.index, gg.to_numpy()))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--pit-cache", default="pit_cache")
    ap.add_argument("--start", default="2023-12-01")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--horizon", type=int, default=10)
    ap.add_argument("--step", type=int, default=10)
    args = ap.parse_args(argv)

    if args.step < args.horizon:
        print("❌ step < horizon：截面重叠会虚高 t，拒绝")
        return 2

    from aqsp.strategies.composite import CompositeStrategy
    from aqsp.strategies.thresholds import load_thresholds

    th = load_thresholds(str(REPO_ROOT / "config" / "thresholds.yaml"))
    base = CompositeStrategy(thresholds=th)
    thr = float(th.composite.min_total_score)

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
    all_dates = sorted(px["trade_date"].unique())
    rebal = [d for d in all_dates if args.start.replace("-", "") <= d <= args.end.replace("-", "")][:: args.step]
    cache = Path(args.pit_cache)

    srcs = {
        "lhb_net": load_lhb(cache),
        "forecast": load_forecast(cache),
        "lockup": load_lockup(cache),
    }
    for k, v in srcs.items():
        print(f"[src] {k}: {len(v)} 只票有数据")

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
        pool = [s for s in data if s in labels and b.get(s, 0) >= thr]

        def _emit(name: str, scores: dict[str, float]) -> None:
            act = [s for s in data if s in labels and abs(scores.get(s, 0.0)) > 1e-12]
            if len(act) >= 100:
                rows.append({"date": d, "factor": name, "scope": "all", "n": len(act),
                             "ic": _spearman(np.array([scores[s] for s in act]),
                                             np.array([labels[s] for s in act]))})
            pact = [s for s in pool if abs(scores.get(s, 0.0)) > 1e-12]
            if len(pact) >= 30:
                rows.append({"date": d, "factor": name, "scope": "pool", "n": len(pact),
                             "ic": _spearman(np.array([scores[s] for s in pact]),
                                             np.array([labels[s] for s in pact]))})

        # 1) 龙虎榜：近 20 日净买入占比之和，**滞后 1 个交易日**
        lhb = srcs["lhb_net"]
        if lhb:
            day_pos = {dd: i for i, dd in enumerate(all_dates)}
            i_now = day_pos.get(d)
            cutoff = all_dates[max(0, i_now - 1)] if i_now is not None else d
            w_start = all_dates[max(0, i_now - 21)] if i_now is not None else d
            sc = {}
            for s in data:
                code = s.split(".")[0]
                ser = lhb.get(code)
                if not ser:
                    continue
                tot = sum(v for dd, v in ser if w_start <= dd <= cutoff)
                if tot != 0:
                    sc[s] = tot
            _emit("lhb_net", sc)

        # 2) 业绩预告：按披露日生效，60 自然日内线性衰减
        fc = srcs["forecast"]
        if fc:
            import datetime as _dt
            d0 = _dt.date.fromisoformat(f"{d[:4]}-{d[4:6]}-{d[6:]}")
            sc = {}
            for s in data:
                code = s.split(".")[0]
                ser = fc.get(code)
                if not ser:
                    continue
                best = 0.0
                for dd, sig in ser:
                    if dd > f"{d[:4]}-{d[4:6]}-{d[6:]}":
                        break
                    try:
                        age = (d0 - _dt.date.fromisoformat(dd)).days
                    except ValueError:
                        continue
                    if 0 <= age <= 60:
                        best = sig * (1.0 - age / 60.0)
                if best != 0:
                    sc[s] = best
            _emit("forecast", sc)

        # 3) 解禁：未来 30 日解禁比例（取负号=压力）
        lk = srcs["lockup"]
        if lk:
            import datetime as _dt
            d0 = _dt.date.fromisoformat(f"{d[:4]}-{d[4:6]}-{d[6:]}")
            d1 = (d0 + _dt.timedelta(days=30)).isoformat()
            sc = {}
            for s in data:
                code = s.split(".")[0]
                ser = lk.get(code)
                if not ser:
                    continue
                tot = sum(v for dd, v in ser if f"{d[:4]}-{d[4:6]}-{d[6:]}" <= dd <= d1)
                if tot > 0:
                    sc[s] = -tot
            _emit("lockup", sc)

    if not rows:
        print("❌ 无有效截面")
        return 2

    df = pd.DataFrame(rows)
    print("\n[新源] 作业面 IC 筛选")
    print("| 因子 | 全市场 IC | t | 池内 IC | 池内 t | 池内正占比 | 池内覆盖 | 判读 |")
    print("|---|---|---|---|---|---|---|---|")
    for name, g in df.groupby("factor"):
        out = []
        for scope in ("all", "pool"):
            sub = g[g["scope"] == scope]
            vals = sub["ic"].dropna().to_numpy()
            if len(vals) >= 2:
                m, sd = float(vals.mean()), float(vals.std(ddof=1))
                out.append((m, m / (sd / np.sqrt(len(vals))) if sd > 0 else float("nan"),
                            float((vals > 0).mean()), float(sub["n"].mean())))
            else:
                out.append((float("nan"),) * 4)
        (ma, ta, _, _), (mp, tp, hitp, npool) = out
        verdict = ("✅ 池内有效" if abs(tp) >= 2 and abs(mp) >= 0.01
                   else "⚠️ 边缘" if abs(tp) >= 1 and abs(mp) >= 0.01 else "❌ 无效")
        print(f"| {name} | {ma:+.4f} | {ta:+.2f} | {mp:+.4f} | {tp:+.2f} | "
              f"{hitp:.0%} | {npool:.0f} | {verdict} |")
    print("\n⚠️ lockup 用「未来 30 日解禁」⇒ 依赖「解禁计划提前公告」这一假设，结论须标注")
    return 0


if __name__ == "__main__":
    sys.exit(main())
