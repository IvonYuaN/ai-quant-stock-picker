#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基本面因子的「作业面 IC」筛选：ROE / 净利同比 / EP / BP。

背景：`quality`/`value` 两个因子在系统里**恒为常量**（缺基本面列），
是唯一还没被证伪的方向。本脚本用 `fetch_fundamentals_history.py` 采到的数据，
按与 holder 完全相同的口径给出「全市场 IC vs 池内 IC」。

四个因子（PIT 是硬约束）
----------------------
1. ``roe``    = `WEIGHTAVG_ROE`（加权 ROE），**按 `NOTICE_DATE` 披露日生效**；
               用报告期 = 前视 1~4 个月（同 holder 教训）。
2. ``profit_yoy`` = `PARENT_NETPROFIT` 同比（同一报告期 vs 上年同期），同上 PIT。
3. ``ep``     = 1 / `PE_TTM`（盈利收益率，越高越便宜）；按交易日取值，天然 PIT。
4. ``bp``     = 1 / `PB_MRQ`（账面市值比，越高越便宜），同上。

方向：ROE / 净利同比 / EP / BP **都是越大越好**（预期 IC 为正）。
若实测为负 ⇒ 说明该因子在当前市场反向，需 `invert`。

用法
----
    python scripts/research/fundamentals_pool_ic.py \
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


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3:
        return float("nan")
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    ra = ra - ra.mean()  # 重新赋值，避免只读视图就地运算
    rb = rb - rb.mean()
    denom = np.sqrt((ra**2).sum() * (rb**2).sum())
    return float((ra * rb).sum() / denom) if denom > 0 else float("nan")


def _forward_return(close: pd.Series, horizon: int) -> pd.Series:
    v = close.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    if len(v) > horizon:
        out[:-horizon] = v[horizon:] / v[:-horizon] - 1.0
    return pd.Series(out, index=close.index)


def load_report(cache: Path) -> tuple[dict[str, list[tuple[str, float]]], dict[str, list[tuple[str, float]]]]:
    """返回 (roe_map, yoy_map)：symbol → [(notice_date, value)] 升序。"""
    p = cache / "fundamentals_report_history.csv"
    if not p.exists():
        return {}, {}
    cols = {"SECURITY_CODE", "SECURITY_TYPE", "REPORTDATE", "NOTICE_DATE",
            "WEIGHTAVG_ROE", "PARENT_NETPROFIT"}
    df = pd.read_csv(p, usecols=lambda c: c in cols, dtype=str)
    if "SECURITY_TYPE" in df.columns:
        df = df[df["SECURITY_TYPE"] == "A股"]
    df["code"] = df["SECURITY_CODE"].astype(str).str.zfill(6)
    df["rd"] = df["REPORTDATE"].astype(str).str.slice(0, 10)
    df["nd"] = df["NOTICE_DATE"].astype(str).str.slice(0, 10)
    df["roe"] = pd.to_numeric(df["WEIGHTAVG_ROE"], errors="coerce")
    df["profit"] = pd.to_numeric(df["PARENT_NETPROFIT"], errors="coerce")
    df = df.dropna(subset=["nd"]).drop_duplicates(subset=["code", "rd"], keep="last")

    roe_out: dict[str, list[tuple[str, float]]] = {}
    yoy_out: dict[str, list[tuple[str, float]]] = {}
    for code, g in df.groupby("code"):
        g = g.sort_values("rd")
        roes = [(r.nd, r.roe) for r in g.itertuples() if pd.notna(r.roe)]
        if roes:
            roe_out[code] = sorted(roes)
        # 同比：按报告期年月日对齐上一年同一天
        pmap = {r.rd: r.profit for r in g.itertuples() if pd.notna(r.profit)}
        nmap = {r.rd: r.nd for r in g.itertuples()}
        ys: list[tuple[str, float]] = []
        for rd, cur in pmap.items():
            prev_rd = f"{int(rd[:4]) - 1}{rd[4:]}"
            prev = pmap.get(prev_rd)
            if prev is not None and abs(prev) > 1e-9:
                ys.append((nmap[rd], (cur - prev) / abs(prev)))
        if ys:
            yoy_out[code] = sorted(ys)
    return roe_out, yoy_out


def load_valuation(cache: Path) -> tuple[dict[str, list[tuple[str, float]]], dict[str, list[tuple[str, float]]]]:
    p = cache / "valuation_history.csv"
    if not p.exists():
        return {}, {}
    df = pd.read_csv(p, usecols=lambda c: c in {"SECURITY_CODE", "TRADE_DATE", "PE_TTM", "PB_MRQ"}, dtype=str)
    df["code"] = df["SECURITY_CODE"].astype(str).str.zfill(6)
    df["d"] = df["TRADE_DATE"].astype(str).str.slice(0, 10)
    pe = pd.to_numeric(df["PE_TTM"], errors="coerce")
    pb = pd.to_numeric(df["PB_MRQ"], errors="coerce")
    df = df.assign(ep=np.where(pe > 0, 1.0 / pe, np.nan), bp=np.where(pb > 0, 1.0 / pb, np.nan))
    ep_out, bp_out = {}, {}
    for code, g in df.groupby("code"):
        gg = g.sort_values("d")
        e = [(r.d, r.ep) for r in gg.itertuples() if pd.notna(r.ep)]
        b = [(r.d, r.bp) for r in gg.itertuples() if pd.notna(r.bp)]
        if e:
            ep_out[code] = e
        if b:
            bp_out[code] = b
    return ep_out, bp_out


def _latest(series: list[tuple[str, float]] | None, as_of_iso: str, max_age_days: int) -> float | None:
    """取 <= as_of 的最近值（带最大陈旧度限制）。series 已按日期升序。"""
    if not series:
        return None
    best = None
    for d, v in series:
        if d > as_of_iso:
            break
        best = (d, v)
    if best is None:
        return None
    if max_age_days > 0:
        from datetime import date

        try:
            age = (date.fromisoformat(as_of_iso) - date.fromisoformat(best[0])).days
        except ValueError:
            return best[1]
        if age > max_age_days:
            return None
    return best[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--pit-cache", default="pit_cache")
    ap.add_argument("--start", default="2023-12-01")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--horizon", type=int, default=10)
    ap.add_argument("--step", type=int, default=10)
    ap.add_argument("--max-age", type=int, default=200, help="基本面值最大陈旧天数")
    args = ap.parse_args(argv)
    if args.step < args.horizon:
        print("❌ step < horizon 会虚高 t，拒绝")
        return 2

    from aqsp.strategies.composite import CompositeStrategy
    from aqsp.strategies.thresholds import load_thresholds

    th = load_thresholds(str(REPO_ROOT / "config" / "thresholds.yaml"))
    base = CompositeStrategy(thresholds=th)
    thr = float(th.composite.min_total_score)

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    px = pd.read_sql(
        "SELECT ts_code, trade_date, close FROM daily_qfq WHERE trade_date >= ?",
        con, params=(args.start.replace("-", "")[:4] + "0101",),
    )
    con.close()
    px = px.sort_values(["ts_code", "trade_date"])
    frames = {ts: g.reset_index(drop=True) for ts, g in px.groupby("ts_code")}
    fwd = {ts: _forward_return(df["close"], args.horizon) for ts, df in frames.items()}
    all_dates = sorted(px["trade_date"].unique())
    rebal = [d for d in all_dates if args.start.replace("-", "") <= d <= args.end.replace("-", "")][:: args.step]

    cache = Path(args.pit_cache)
    roe_map, yoy_map = load_report(cache)
    ep_map, bp_map = load_valuation(cache)
    srcs = {"roe": roe_map, "profit_yoy": yoy_map, "ep": ep_map, "bp": bp_map}
    for k, v in srcs.items():
        print(f"[src] {k}: {len(v)} 只票")

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
        pool = sorted(
            (s for s in data if s in labels and b.get(s, 0) >= thr), key=lambda s: -b[s]
        )
        iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"

        for name, mp in srcs.items():
            sc: dict[str, float] = {}
            for s in data:
                code = s.split(".")[0]
                v = _latest(mp.get(code), iso, args.max_age)
                if v is not None and np.isfinite(v):
                    sc[s] = float(v)
            act = [s for s in data if s in labels and s in sc]
            if len(act) >= 100:
                rows.append({"date": d, "factor": name, "scope": "all", "n": len(act),
                             "ic": _spearman(np.array([sc[s] for s in act]),
                                             np.array([labels[s] for s in act]))})
            pact = [s for s in pool if s in sc]
            if len(pact) >= 30:
                rows.append({"date": d, "factor": name, "scope": "pool", "n": len(pact),
                             "ic": _spearman(np.array([sc[s] for s in pact]),
                                             np.array([labels[s] for s in pact]))})

    if not rows:
        print("❌ 无有效截面")
        return 2
    df = pd.DataFrame(rows)
    print("\n[基本面] 作业面 IC")
    print("| 因子 | 全市场 IC | t | 池内 IC | 池内 t | 池内正占比 | 池内覆盖 | 判读 |")
    print("|---|---|---|---|---|---|---|---|")
    for name, g in df.groupby("factor"):
        o = []
        for scope in ("all", "pool"):
            sub = g[g["scope"] == scope]
            v = sub["ic"].dropna().to_numpy()
            if len(v) >= 2:
                m, sd = float(v.mean()), float(v.std(ddof=1))
                o.append((m, m / (sd / np.sqrt(len(v))) if sd > 0 else float("nan"),
                          float((v > 0).mean()), float(sub["n"].mean())))
            else:
                o.append((float("nan"),) * 4)
        (ma, ta, _, _), (mp, tp, hit, npool) = o
        verdict = ("✅ 有效" if abs(tp) >= 2 and abs(mp) >= 0.01
                   else "⚠️ 边缘" if abs(tp) >= 1 and abs(mp) >= 0.01 else "❌ 无效")
        print(f"| {name} | {ma:+.4f} | {ta:+.2f} | {mp:+.4f} | {tp:+.2f} | {hit:.0%} | {npool:.0f} | {verdict} |")
    print("\n⚠️ 方向：四因子均为「越大越好」；若池内 IC 显著为负 ⇒ 需 invert")
    return 0


if __name__ == "__main__":
    sys.exit(main())
