#!/usr/bin/env python3
"""一字板（A1）的替代买法检验 —— 「买不到」到底是不是终局。

背景（research/stop_sensitivity_probe.py 实测，700 交易日）：
  二板里「一字封死」的票次日收益 **+5.313%/t32.66/胜89.0%**，是所有格子中最强的，
  但当天全天封死在涨停价上，挂单排不进 ⇒ 记为不可成交（A1，3195 格）。
  可成交的 B 格只有 +1.362%/t16.57。这正好印证了「溢价主要在排不进的 A 格」。

于是必须回答：**A1 的票，第二天买还有没有肉？**
这是唯一现实可行的替代时点 —— 当天买不到，次日情绪已释放一部分，开盘不再一字就能成交。
若次日开盘仍在涨停价上（连续一字），同样买不到，必须剔除，否则又是把不可成交记成收益。

口径（三条，缺一条就会把「买不到」算成「赚到了」）：
  1. 买入价 = 次日**开盘价**（不是收盘、不是均价）—— 开盘那一刻是唯一确定的成交价
  2. 剔除次日开盘即涨停的（`nxt_open >= 次日涨停价×0.998`）⇒ 那天同样排不进
  3. 卖出 = 次日**收盘**，与 P1/P6 同为 T+1 日内口径，不做跨日持有
  次日涨停价 = 今日收盘(非复权) × 1.10（主板；一字板当日收在涨停价上）

同时给出对照组：
  - A1 当日买（不可成交，只作理论上界参考）
  - B  格当日买（可成交基准线 +1.362%）
  - A1 次日买 + 按「次日跳空」分档（低开买 vs 高开买，看是不是越便宜越好）
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


def probe(db: str, start: str = "20231101") -> dict:
    sl = _load_probe()
    P = sl.load_main_board(db, start)
    close = P["close"]
    cv = close.to_numpy(dtype=float)
    cq = P["cqr"].to_numpy(dtype=float)
    hh = P["high"].to_numpy(dtype=float)
    ll = P["low"].to_numpy(dtype=float)
    op = P["opn"].to_numpy(dtype=float)
    T, N = cv.shape
    dates = close.index
    listing = sl._listing_mask(close, lookback=30)

    # ---- 与 stop_sensitivity_probe 完全一致的格子定义 ----
    zt_prev = cv / np.roll(cv, 1, axis=0) - 1.0
    zt_prev[:, 0] = np.nan
    with np.errstate(invalid="ignore", divide="ignore"):
        gap = op / np.roll(cq, 1, axis=0) - 1.0
    gap[:, 0] = np.nan
    limit_up = np.round(np.roll(cq, 1, axis=0) * 1.10, 2)
    limit_up[:, 0] = np.nan
    valid = (zt_prev >= 0.098) & np.isfinite(hh) & np.isfinite(limit_up) & np.isfinite(op) & np.isfinite(gap)
    near = hh >= limit_up * 0.998
    A = valid & (op >= limit_up * 0.998) & (cq >= limit_up * 0.998)
    B = valid & (op < limit_up * 0.998) & near & (cq >= limit_up * 0.998)
    A1 = A & (ll >= limit_up * 0.998)          # 真一字：全天没开过板
    A2 = A & (ll < limit_up * 0.998)           # 开板回封

    def _shift1(a):
        out = np.full_like(a, np.nan)
        out[:-1] = a[1:]
        return out

    nxt_op = _shift1(op)
    nxt_cq = _shift1(cq)
    nxt_limit = np.round(cq * 1.10, 2)         # 次日涨停价（主板固定 10%）
    nxt_limit[-1, :] = np.nan

    # 次日开盘还在涨停价上 ⇒ 连续一字，同样买不到，必须剔除
    tradable_next = (np.isfinite(nxt_op) & (nxt_op > 0) &
                     np.isfinite(nxt_limit) & (nxt_op < nxt_limit * 0.998))

    def daily_ret(mask, buy_px, sell_px, extra=None, top_n=None):
        out = []
        for t in range(1, T - 1):
            m = mask[t] & listing[t] & np.isfinite(buy_px[t]) & np.isfinite(sell_px[t]) & (buy_px[t] > 0)
            if extra is not None:
                m = m & extra[t]
            cols = np.where(m)[0]
            if len(cols) == 0:
                continue
            if top_n:
                g = gap[t, cols]
                cols = cols[np.argsort(-np.nan_to_num(g, nan=-9e9))[:top_n]]
            r = sell_px[t, cols] / buy_px[t, cols] - 1.0
            out.append(float(np.nanmean(r)))
        return out

    # 当日买入（对照组）
    d_a1_now = daily_ret(A1, cq, nxt_cq)      # 理论值，实际买不到
    d_b_now = daily_ret(B, cq, nxt_cq)        # 可成交基准
    d_a2_now = daily_ret(A2, cq, nxt_cq)
    # 次日开盘买入 → 次日收盘卖出
    d_a1_next = daily_ret(A1, nxt_op, nxt_cq, extra=tradable_next)
    d_b_next = daily_ret(B, nxt_op, nxt_cq, extra=tradable_next)

    # 🔴 关键对照：当日买入 → **次日开盘卖出**（t1_open）
    # 上面「次日开盘买→收盘卖」的负收益说明次日高开后普遍低走；
    # 若成立，那 P1/P6 现在用的 t1_close（次日收盘卖）就是把开盘的溢价又还回去了。
    # t1_open 同时更安全：不承担次日日内波动，一字跌停开盘也有 sealed 顺延处理。
    d_b_open = daily_ret(B, cq, nxt_op)
    d_a2_open = daily_ret(A2, cq, nxt_op)
    d_p6_open = daily_ret(B, cq, nxt_op, top_n=2)
    d_p6_close = daily_ret(B, cq, nxt_cq, top_n=2)

    # 次日买的分档：按次日跳空（nxt_op / 今收 - 1）看「越便宜越好」是否成立
    with np.errstate(invalid="ignore", divide="ignore"):
        nxt_gap = nxt_op / cq - 1.0
    buckets = {}
    for lo_, hi_, lab in ((-0.30, 0.0, "低开<0"), (0.0, 0.03, "0~3%"),
                          (0.03, 0.06, "3~6%"), (0.06, 0.30, "≥6%")):
        sub = np.isfinite(nxt_gap) & (nxt_gap >= lo_) & (nxt_gap < hi_)
        buckets[lab] = {
            "A1": _stat(daily_ret(A1, nxt_op, nxt_cq, extra=sub & tradable_next)),
            "B": _stat(daily_ret(B, nxt_op, nxt_cq, extra=sub & tradable_next)),
        }

    return {
        "as_of": dates[-1].strftime("%Y-%m-%d"),
        "start": start,
        "n_days_total": int(T),
        "A1_tradable_next_pct": round(float(tradable_next[A1].mean()) * 100, 1) if A1.sum() else None,
        "groups": {
            "A1_当日买_买不到": {"cost0": _stat(d_a1_now), "cost03": _stat(d_a1_now, 0.003)},
            "A2_开板回封_当日买": {"cost0": _stat(d_a2_now), "cost03": _stat(d_a2_now, 0.003)},
            "B_放量封住_当日买": {"cost0": _stat(d_b_now), "cost03": _stat(d_b_now, 0.003)},
            "A1_次日开盘买": {"cost0": _stat(d_a1_next), "cost03": _stat(d_a1_next, 0.003)},
            "B_次日开盘买": {"cost0": _stat(d_b_next), "cost03": _stat(d_b_next, 0.003)},
            "B_次日开盘卖_t1open": {"cost0": _stat(d_b_open), "cost03": _stat(d_b_open, 0.003)},
            "A2_次日开盘卖_t1open": {"cost0": _stat(d_a2_open), "cost03": _stat(d_a2_open, 0.003)},
            "P6top2_次日收盘卖_t1close": {"cost0": _stat(d_p6_close), "cost03": _stat(d_p6_close, 0.003)},
            "P6top2_次日开盘卖_t1open": {"cost0": _stat(d_p6_open), "cost03": _stat(d_p6_open, 0.003)},
        },
        "next_gap_buckets": buckets,
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
