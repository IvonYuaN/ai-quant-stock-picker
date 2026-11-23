#!/usr/bin/env python3
"""连板高度 × 卖出时点 全扫描 —— 找第二个可交易信号，或证明只有二板这一处有肉。

为什么做：现在唯一过门的两个模式（P1 二板全取、P6 二板跳空top2）其实是**同一个信号**
的两个变体，等于只有一个 alpha 源。而「连板高度」是一个完整维度，此前只在
t1_close 口径下零散测过（首板当对照组、三板四板没测）。
换上已经验证更优的 t1_open 之后结论可能变，必须在同一份数据上一次性扫完。

口径（与 P1/P6 完全共用，唯一变量是高度）：
  - 池 = load_main_board（纯主板 60/00，20cm 已剔）
  - 前一日涨停 = qfq 涨幅 ≥9.8%；连续涨停天数 streak 用**截至前一日**的计数
    （当日触板 = streak_prev + 1 板；streak_prev=0 即首板）
  - 可成交 B 格 = 今日 open 未顶死 & high 摸到涨停 & close 收在涨停（排得进）
  - 买入 = 今日收盘（=涨停价）；卖出两个口径：次日开盘 / 次日收盘
  - t 值按交易日聚类 + NW 修正

判据（沿用打板 lane，不另立标准）：NW-t≥2 & 胜率≥60% & 扣 0.3% 成本后仍为正。
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
    return {
        "n_days": int(len(ex)),
        "mean_pct": round(float(ex.mean()) * 100, 3),
        "med_pct": round(float(np.median(ex)) * 100, 3),
        "win_pct": round(float((ex > 0).mean()) * 100, 1),
        "t_nw": round(_nw_t(ex, lag), 2),
        "nw_lag": int(lag),
    }


def _streak_prev(zt: np.ndarray) -> np.ndarray:
    """截至**前一日**的连续涨停天数。zt[t] = 第 t 日是否涨停。

    streak_prev[t] = 从 t-1 往回数的连续 True 个数（0 表示前一日没涨停 ⇒ 今日若涨停是首板）。
    实盘可用：前一日收盘后就已知，不构成前视。
    """
    T, N = zt.shape
    out = np.zeros((T, N), dtype=np.int8)
    run = np.zeros(N, dtype=np.int8)
    for t in range(T):
        out[t] = run                       # 先写「前一日为止」的计数
        run = np.where(zt[t], run + 1, 0)  # 再用今日是否涨停更新，供下一日读
    return out


def probe(db: str, start: str = "20231101") -> dict:
    sl = _load_probe()
    P = sl.load_main_board(db, start)
    close = P["close"]
    cv = close.to_numpy(dtype=float)
    cq = P["cqr"].to_numpy(dtype=float)
    hh = P["high"].to_numpy(dtype=float)
    op = P["opn"].to_numpy(dtype=float)
    T, N = cv.shape
    dates = close.index
    listing = sl._listing_mask(close, lookback=30)

    with np.errstate(invalid="ignore", divide="ignore"):
        zt = cv / np.roll(cv, 1, axis=0) - 1.0 >= 0.098
    zt[:, 0] = False
    streak_prev = _streak_prev(zt)

    limit_up = np.round(np.roll(cq, 1, axis=0) * 1.10, 2)
    limit_up[:, 0] = np.nan
    def _shift1(a):
        out = np.full_like(a, np.nan)
        out[:-1] = a[1:]
        return out

    nxt_op, nxt_cq = _shift1(op), _shift1(cq)
    valid = np.isfinite(hh) & np.isfinite(limit_up) & np.isfinite(op)
    near = hh >= limit_up * 0.998
    B = valid & (op < limit_up * 0.998) & near & (cq >= limit_up * 0.998)   # 可成交（排得进）

    def daily_ret(buy_px, sell_px, extra=None, top_n=None, gap=None):
        out = []
        for t in range(1, T - 1):
            m = B[t] & listing[t] & np.isfinite(buy_px[t]) & np.isfinite(sell_px[t]) & (buy_px[t] > 0)
            if extra is not None:
                m = m & extra[t]
            cols = np.where(m)[0]
            if len(cols) == 0:
                continue
            if top_n:
                g = np.nan_to_num(gap[t, cols], nan=-9e9)
                cols = cols[np.argsort(-g)[:top_n]]
            out.append(float(np.nanmean(sell_px[t, cols] / buy_px[t, cols] - 1.0)))
        return out

    with np.errstate(invalid="ignore", divide="ignore"):
        gap = op / np.roll(cq, 1, axis=0) - 1.0
    gap[:, 0] = np.nan

    heights = {}
    for h in (0, 1, 2, 3):
        # streak_prev = h ⇒ 今日这是第 h+1 板
        sub = (streak_prev == h)
        heights["%d板" % (h + 1)] = {
            "n_cells": int((B & sub).sum()),
            "t1_open": {"cost0": _stat(daily_ret(cq, nxt_op, extra=sub)),
                        "cost03": _stat(daily_ret(cq, nxt_op, extra=sub), 0.003)},
            "t1_close": {"cost0": _stat(daily_ret(cq, nxt_cq, extra=sub)),
                         "cost03": _stat(daily_ret(cq, nxt_cq, extra=sub), 0.003)},
        }
    # 4 板及以上合并（样本稀）
    sub4 = (streak_prev >= 3)
    heights["4板+"] = {
        "n_cells": int((B & sub4).sum()),
        "t1_open": {"cost0": _stat(daily_ret(cq, nxt_op, extra=sub4)),
                    "cost03": _stat(daily_ret(cq, nxt_op, extra=sub4), 0.003)},
        "t1_close": {"cost0": _stat(daily_ret(cq, nxt_cq, extra=sub4)),
                     "cost03": _stat(daily_ret(cq, nxt_cq, extra=sub4), 0.003)},
    }

    # 各高度下的跳空 top_n 平台（给容量选择用）
    topn = {}
    for h in (0, 1, 2):
        sub = (streak_prev == h)
        row = {}
        for k in (1, 2, 3, 5):
            row[str(k)] = _stat(daily_ret(cq, nxt_op, extra=sub, top_n=k, gap=gap))
        topn["%d板" % (h + 1)] = row

    # ---- 分 regime（打板判据的一条：分牛/熊/震荡不得显著负）----
    # 市场状态 = 60 日全市场中位收益（只用 ≤t），按全期三分位切 牛/震荡/熊。
    state = sl.market_state(close, win=60)
    labels = sl.regime_buckets(state, list(range(T)), k=3)
    regimes = {}
    for h in (0, 1):
        sub_h = (streak_prev == h)
        per = {}
        for lab in sorted(set(labels.values())):
            days = np.array([labels.get(t) == lab for t in range(T)], dtype=bool)
            per[lab] = _stat(daily_ret(cq, nxt_op, extra=sub_h & days[:, None]))
        regimes["%d板" % (h + 1)] = per

    return {"as_of": dates[-1].strftime("%Y-%m-%d"), "start": start,
            "n_days_total": int(T), "heights": heights, "topn_t1open": topn,
            "regimes_t1open": regimes}


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
