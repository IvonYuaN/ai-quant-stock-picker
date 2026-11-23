#!/usr/bin/env python3
"""P3「超跌反抽」的卖点检验：修复是在次日开盘完成，还是要等到收盘？

背景：打板家族（P1/P2/P6）实测卖出时点从 t1_close 改 t1_open 后
每笔收益 +49%（严格二板 +1.431% → +2.127%），成因是「隔夜情绪溢价在集合竞价
一次性兑现，日内平均为负」。P3 的经济假设是「单日暴跌 = 散户过度反应，次日修复」，
同样属于**隔夜**现象 —— 那它的修复会不会也在开盘就完成、等收盘反而回吐？

当前 P3 用 t1_close，且它是唯一一个「每天上百信号」的模式（实测 170 个/天，
账本受资金约束只买 2 个），所以卖点差一点，影响面很大。

口径（**已声明为近似**，不假装精确）：
- 真实 P3 = 10:00 买（盘中价）。库里没有分时，用**当日开盘价**近似
  ⇒ 这会高估可买性（开盘价是首个成交价，未必能成交），除非开盘跳空在区间内。
- setup：昨日跌幅 ≤ -7%（主板 0.7×跌停）+ 今日开盘跳空在 -1%~+4%
- 卖出两档：次日开盘 / 次日收盘
- 判据沿用打板 lane：NW-t≥2 & 胜率≥60% & 分 regime 不显著负

⚠️ 本脚本只回答「卖点方向」，不作为 P3 过门的依据 ——
近似买价本身就带乐观偏差，真要过门必须用分时数据重做。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np


def _load_probe():
    for c in (Path(__file__).resolve().parent / "shortline_probe.py",
              Path(__file__).resolve().parents[1] / "research" / "shortline_probe.py"):
        if c.exists():
            p = c
            break
    else:
        raise SystemExit("[fatal] 找不到 shortline_probe.py")
    spec = importlib.util.spec_from_file_location("shortline_probe", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["shortline_probe"] = m
    spec.loader.exec_module(m)
    return m


def _nw_t(vals, lag):
    v = vals - vals.mean()
    n = len(v)
    if n < 2:
        return float("nan")
    var = float((v ** 2).sum()) / n
    if var <= 0:
        return float("nan")
    se = var / n
    for L in range(1, lag + 1):
        acf = float((v[L:] * v[:n - L]).sum()) / (n * var)
        se += 2.0 * acf * var / n
    return float(vals.mean() / np.sqrt(se)) if se else float("nan")


def _acf_lag(vals, cap=20):
    v = vals - vals.mean()
    n = len(v)
    if n < 4:
        return 1
    denom = float((v ** 2).sum())
    if denom <= 0:
        return 1
    lag = 1
    for L in range(1, min(cap, n // 4) + 1):
        if abs(float((v[L:] * v[:n - L]).sum()) / denom) > 0.5:
            lag = max(lag, L)
    return lag


def _stat(daily, cost=0.0):
    ex = np.asarray([d for d in daily if np.isfinite(d)], dtype=float)
    if len(ex) < 20:
        return {"n_days": int(len(ex)), "note": "样本<20 跳过"}
    ex = ex - cost
    lag = _acf_lag(ex)
    return {"n_days": int(len(ex)), "mean_pct": round(float(ex.mean()) * 100, 3),
            "med_pct": round(float(np.median(ex)) * 100, 3),
            "win_pct": round(float((ex > 0).mean()) * 100, 1),
            "t_nw": round(_nw_t(ex, lag), 2)}


def probe(db: str, start: str = "20231101") -> dict:
    sl = _load_probe()
    P = sl.load_main_board(db, start)
    close = P["close"]
    cv = close.to_numpy(dtype=float)
    cq = P["cqr"].to_numpy(dtype=float)
    op = P["opn"].to_numpy(dtype=float)
    T, N = cv.shape
    dates = close.index
    listing = sl._listing_mask(close, lookback=30)

    def _shift(a, k):
        out = np.full_like(a, np.nan)
        if k > 0:
            out[k:] = a[:-k]
        else:
            out[:k] = a[-k:]
        return out

    # ret[i] = 第 i 日涨跌幅（close[i]/close[i-1]）。买入日是 t，要看的是**昨日** t-1
    # 的涨跌幅 ⇒ 必须 _shift(ret, +1) 把 t-1 的值搬到第 t 行。
    # 🔴 这里曾经写成 _shift(ret, -1)，那取到的是**当日**涨跌幅 ⇒ 变成「买完再跌 7%」
    # 的事后筛选，是同族 off-by-one（与 board_height_probe 的 zt_prev 同型）。
    day_ret = cv / _shift(cv, 1) - 1.0
    prev_ret = _shift(day_ret, 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        gap = op / _shift(cq, 1) - 1.0           # 今日开盘跳空
    nxt_op = _shift(op, -1)
    nxt_cq = _shift(cq, -1)

    setup = (np.isfinite(prev_ret) & (prev_ret <= -0.07) &
             np.isfinite(gap) & (gap >= -0.01) & (gap <= 0.04) &
             np.isfinite(nxt_op) & np.isfinite(nxt_cq) & (op > 0))
    buy = op

    def daily(sell):
        out = []
        for t in range(1, T - 1):
            m = setup[t] & listing[t] & np.isfinite(sell[t]) & (sell[t] > 0) & (buy[t] > 0)
            cols = np.where(m)[0]
            if len(cols) == 0:
                continue
            out.append(float(np.nanmean(sell[t, cols] / buy[t, cols] - 1.0)))
        return out

    d_open, d_close = daily(nxt_op), daily(nxt_cq)

    # 池等权基准（同口径），用于报超额
    pool = []
    for t in range(1, T - 1):
        m = listing[t] & np.isfinite(nxt_cq[t]) & np.isfinite(cq[t]) & (cq[t] > 0)
        cols = np.where(m)[0]
        pool.append(float(np.nanmean(nxt_cq[t, cols] / cq[t, cols] - 1.0)) if len(cols) >= 60 else np.nan)

    # 分 regime
    state = sl.market_state(close, win=60)
    labels = sl.regime_buckets(state, list(range(T)), k=3)
    regimes = {}
    for lab in sorted(set(labels.values())):
        days = np.array([labels.get(t) == lab for t in range(T)], dtype=bool)
        sub = setup & days[:, None]
        def dr(sell):
            o = []
            for t in range(1, T - 1):
                m = sub[t] & listing[t] & np.isfinite(sell[t]) & (sell[t] > 0) & (buy[t] > 0)
                cols = np.where(m)[0]
                if len(cols):
                    o.append(float(np.nanmean(sell[t, cols] / buy[t, cols] - 1.0)))
            return o
        regimes[lab] = {"t1_open": _stat(dr(nxt_op)), "t1_close": _stat(dr(nxt_cq))}

    return {
        "as_of": dates[-1].strftime("%Y-%m-%d"), "start": start, "n_days_total": int(T),
        "n_setup_cells": int(setup.sum()),
        "t1_open": {"cost0": _stat(d_open), "cost03": _stat(d_open, 0.003)},
        "t1_close": {"cost0": _stat(d_close), "cost03": _stat(d_close, 0.003)},
        "regimes": regimes,
        "note": "买价用当日开盘价近似 10:00 价，带乐观偏差，只判卖点方向",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--start", default="20231101")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    out = probe(args.db, args.start)
    txt = json.dumps(out, ensure_ascii=False, indent=2)
    print(txt)
    if args.json:
        Path(args.json).write_text(txt, encoding="utf-8")
        print("[written] %s" % args.json, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
