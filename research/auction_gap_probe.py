#!/usr/bin/env python3
"""P4 的可测子集：只测「高开 2~7%」，不测竞价额。

## 为什么要拆开测

P4（竞价抢筹）的入场条件是两半：

    高开 2~7%  且  竞价额 ≥ 昨日成交额 2%   → 09:27 集合竞价买，次日收盘卖

第二半需要**集合竞价成交额**的历史数据，库里没有（`daily_qfq.amount` 全 NULL，
连当日成交额都没有；分钟线也只能拿到 9:30 之后）。所以 P4 一直是 `unverified`
——「没测过」不等于「测出为负」，这条我不肯含糊过去。

但第一半**有数据**：`open` 和昨收都在。于是有一个干净的判决：

- 若「高开 2~7%」这一半本身没有超额 ⇒ P4 判死。因为竞价放量是在一个没有 alpha
  的底子上再加条件，不可能凭空造出 alpha（加条件只能筛样本，不能改符号）。
- 若有超额 ⇒ P4 有了**不依赖竞价额的简化版**，可以真跑。

## 口径

- 池：主板（60/00），10% 涨停。
- 剔除开盘就涨停（`open ≥ 涨停价×0.998`）—— 那个价买不到，与打板 B 格同一个坑。
- 流动性：昨日 `volume × 100 × 昨收 ≥ 5000 万`（P4 的 `MIN_YEST_AMOUNT`）。
  🔴 用 volume×价 而不是 amount 列：`amount` 全 NULL，只能近似。这是流动性粗筛，
  不参与收益计算，粗糙不影响结论方向。
- 买价：**开盘价**（集合竞价成交价就是开盘价，无滑点）。
- 卖价：次日收盘（P4 原定 `t1_close`）；同时报次日开盘供对照。
- 🔴 **必须报超额**：同日全市场等权（同样开盘买、次日收盘卖）作为基准。
  只报绝对收益会把牛市当成 alpha —— 这是 gate 只报绝对收益踩过的坑。
- 成本 0.15% 双边；按日等权，**不累乘**。

用法：
    python research/auction_gap_probe.py --db <db> --json out.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

# P4 原定阈值
GAP_LO, GAP_HI = 0.02, 0.07
MIN_YEST_AMT = 5e7            # 5000 万元（P4 的 MIN_YEST_AMOUNT=5000 万）
BUCKETS = [(0.02, 0.03), (0.03, 0.04), (0.04, 0.05), (0.05, 0.06), (0.06, 0.07)]


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


def _shift(a: np.ndarray, k: int) -> np.ndarray:
    out = np.full_like(a, np.nan)
    if k > 0:
        out[k:] = a[:-k]
    else:
        out[:k] = a[-k:]
    return out


def _stat(daily, cost=0.0015):
    a = np.asarray([d for d in daily if np.isfinite(d)], dtype=float) - cost
    if a.size < 20:
        return {"n_days": int(a.size), "note": "样本<20"}
    t = a.mean() / (a.std(ddof=1) / np.sqrt(a.size))
    return {"n_days": int(a.size), "mean_pct": round(float(a.mean()) * 100, 3),
            "median_pct": round(float(np.median(a)) * 100, 3),
            "win_pct": round(float((a > 0).mean()) * 100, 1),
            "t": round(float(t), 2)}


def _excess(sig_daily, mkt_daily):
    """逐日超额序列的统计。两条 daily 列表**按同一批交易日对齐**生成。"""
    a = np.asarray(sig_daily, dtype=float)
    b = np.asarray(mkt_daily, dtype=float)
    n = min(a.size, b.size)
    d = a[:n] - b[:n]
    d = d[np.isfinite(d)]
    if d.size < 20:
        return {"n_days": int(d.size), "note": "样本<20"}
    t = d.mean() / (d.std(ddof=1) / np.sqrt(d.size))
    return {"n_days": int(d.size), "mean_pct": round(float(d.mean()) * 100, 3),
            "win_pct": round(float((d > 0).mean()) * 100, 1), "t": round(float(t), 2)}


def probe(db: str, start: str = "20231101") -> dict:
    sl = _load_probe()
    P = sl.load_main_board(db, start)
    close = P["close"]
    cv = close.to_numpy(dtype=float)
    cq = P["cqr"].to_numpy(dtype=float)
    op, vol = P["opn"].to_numpy(dtype=float), P["vol"].to_numpy(dtype=float)
    T, N = cv.shape
    listing = sl._listing_mask(close, lookback=30)

    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = cv / cq
        prev_cq = _shift(cq, 1)
        lu = np.round(prev_cq * 1.10, 2)
        gap = op / prev_cq - 1.0
        # 昨日成交额（元）：主板 volume 单位=手 ⇒ ×100 转股
        yest_amt = _shift(vol, 1) * 100.0 * prev_cq

    price_q = op * ratio                 # 开盘价的复权基准
    sell_close = _shift(cv, -1)          # 次日收盘
    sell_open = _shift(price_q, -1)      # 次日开盘

    base = (
        listing
        & np.isfinite(gap) & np.isfinite(price_q) & np.isfinite(sell_close)
        & (price_q > 0) & (yest_amt >= MIN_YEST_AMT)
        & (op < lu * 0.998)              # 开盘就涨停 ⇒ 买不到，剔除
        & (gap >= GAP_LO) & (gap <= GAP_HI)
    )

    def daily(mask, sell):
        """返回 (信号日均值, 全市场日均值)，两条列表逐日对齐。"""
        sig, mkt = [], []
        for t in range(1, T - 1):
            m = mask[t] & np.isfinite(price_q[t]) & np.isfinite(sell[t]) & (price_q[t] > 0)
            c = np.where(m)[0]
            if c.size < 5:               # 当天信号太少，均值没意义，整天跳过
                continue
            allm = (listing[t] & np.isfinite(price_q[t]) & np.isfinite(sell[t])
                    & (price_q[t] > 0))
            allc = np.where(allm)[0]
            if allc.size < 200:
                continue
            sig.append(float(np.nanmean(sell[t, c] / price_q[t, c] - 1.0)))
            mkt.append(float(np.nanmean(sell[t, allc] / price_q[t, allc] - 1.0)))
        return sig, mkt

    res: dict = {"as_of": close.index[-1].strftime("%Y-%m-%d"),
                 "cost_round_trip": 0.003, "gap_range": [GAP_LO, GAP_HI],
                 "min_yest_amt_yuan": MIN_YEST_AMT,
                 "note": "买=开盘价；amount 列全 NULL ⇒ 流动性用 volume×昨收 近似",
                 "cells": int(base.sum()), "exits": {}, "by_gap": {}}

    for lab, sell in (("t1_close", sell_close), ("t1_open", sell_open)):
        sig, mkt = daily(base, sell)
        res["exits"][lab] = {"abs": _stat(sig), "market": _stat(mkt),
                             "excess": _excess(sig, mkt)}

    # 高开幅度是否单调：若单调，说明信息在「高开多少」本身
    for lo, hi in BUCKETS:
        m = base & (gap > lo) & (gap <= hi)
        sig, mkt = daily(m, sell_close)
        res["by_gap"]["%.0f~%.0f%%" % (lo * 100, hi * 100)] = {
            "abs": _stat(sig), "excess": _excess(sig, mkt)}
    return res


def _fmt(r: dict) -> str:
    if "mean_pct" not in r:
        return "样本不足(%s)" % r.get("note", "")
    return "%+.3f%% t%-6.2f 胜%4.1f%% (n=%d)" % (
        r["mean_pct"], r["t"], r["win_pct"], r["n_days"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--start", default="20231101")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    res = probe(a.db, a.start)

    print("=" * 78)
    print("P4 的可测子集：只测「高开 2~7%」，不测竞价额")
    print("截至 %s ｜ 买=开盘价 ｜ 流动性≥5000万 ｜ 扣 %.1f%% 双边 ｜ %d 格"
          % (res["as_of"], res["cost_round_trip"] * 100, res["cells"]))
    print("=" * 78)
    for lab, r in res["exits"].items():
        print("\n【卖点 %s】" % lab)
        print("  信号(绝对) : %s" % _fmt(r["abs"]))
        print("  全市场基准 : %s" % _fmt(r["market"]))
        print("  超额       : %s   ← 这才是判据" % _fmt(r["excess"]))
    print("\n【按高开幅度分档（卖=次日收盘，报超额）】")
    for k, v in res["by_gap"].items():
        print("  高开 %-9s 绝对 %s ｜ 超额 %s" % (k, _fmt(v["abs"]), _fmt(v["excess"])))
    print()
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print("写出 %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
