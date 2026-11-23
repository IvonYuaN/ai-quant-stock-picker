#!/usr/bin/env python3
"""打板 lane 的可执行性审计 —— 证明「B 格过门」是按结果筛选（前视），并给出可执行口径的真值。

## 起因

同一份数据上有两组互相矛盾的数：
  - 「严格二板 B 格」买收盘、次日开盘卖：**+2.13%/t19.4/胜80.7%**
  - 「可成交口径」次日开盘买：            **+0.09%（没肉）**

两者不可能同时成立。本脚本把差异拆开，逐个口径验「能不能真的成交」。

## 结论（详见输出与 out_executable_entry_probe.json）

1. `B 格` 的定义是 `收盘 ≥ 涨停 × 0.998` = **收盘封死在涨停**。
   封死意味着 14:57 后面排着巨量买单 ⇒ **收盘价买不到**。
2. 换成任何能成交的价，收益全部为负，且 t 值 −5 ~ −20：

   | 口径 | 首板 | 2板 | 可执行 |
   |---|---|---|---|
   | 买收盘涨停价（B格，要求回封） | +1.63% | +2.80% | ❌ 前视 |
   | 摸板即买（挂单在涨停，开板才成交） | −0.94% | −2.32% | ✅ |
   | 次日开盘买 → 隔日开盘卖 | −0.81% | −1.75% | ✅ |
   | 次日开盘买 → 当日收盘卖 | −0.53% | −1.16% | ✅ |

3. 差异的来源可精确对账（2板）：
   摸板 64220 格中，**回封 40031（62.3%）均 +1.5%~+2.2%**、
   **炸板 24189（37.7%）均 −5.1%~−12.5%**。
   `0.623 × (+1.5%) + 0.377 × (−5.1%) ≈ −0.96%` —— 与实测 −0.936% 闭合。
   「回封」要等收盘才知道 ⇒ B 格的 +2.8% 是**在已知结果的样本上统计的**。

## 两个曾被误用的口径（务必别再用）

  - **盲挂涨停价**：以涨停价挂买单，若市价在 +2% 会**立即按 +2% 成交**，
    不会挂在涨停价上。按「成交价=涨停价」建模会得到 −7.9% 的假数字（作废）。
  - **限价下移 k%**：先去掉「当日需摸板」还是负；保留 `high≥涨停` 则 k 越大越好
    （+3% → +2.06%/t25.8），那同样是拿「后来冲板」这个未来信息筛选，作废。

用法：
    python research/executable_entry_probe.py --db <db> --json out.json
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
            "win_pct": round(float((a > 0).mean()) * 100, 1), "t": round(float(t), 2)}


def _streak_prev(zt: np.ndarray) -> np.ndarray:
    T, N = zt.shape
    out = np.zeros((T, N), dtype=np.int8)
    run = np.zeros(N, dtype=np.int8)
    for t in range(T):
        out[t] = run
        run = np.where(zt[t], run + 1, 0)
    return out


def probe(db: str, start: str = "20231101") -> dict:
    sl = _load_probe()
    P = sl.load_main_board(db, start)
    close = P["close"]
    cv = close.to_numpy(dtype=float)          # 前复权：一律用它算收益
    cq = P["cqr"].to_numpy(dtype=float)       # 非复权：只用来判涨停价
    op, hh, lo = (P[k].to_numpy(dtype=float) for k in ("opn", "high", "low"))
    T, N = cv.shape
    listing = sl._listing_mask(close, lookback=30)

    with np.errstate(invalid="ignore", divide="ignore"):
        zt = np.zeros_like(cv, dtype=bool)
        zt[1:] = cv[1:] / cv[:-1] - 1.0 >= 0.098
        ratio = cv / cq                        # 把非复权价折到复权基准
        prev_cq = np.vstack([np.full((1, N), np.nan), cq[:-1]])
        lu = np.round(prev_cq * 1.10, 2)       # 今日涨停价（非复权）
    lu_q = lu * ratio
    op_q = op * ratio
    streak = _streak_prev(zt)
    nxt_op_q = _shift(op_q, -1)
    nxt2_op_q = _shift(nxt_op_q, -1)

    touch = np.isfinite(hh) & np.isfinite(lu) & (hh >= lu * 0.998)     # 盘中摸到涨停
    opened = np.isfinite(lo) & (lo < lu * 0.998)                        # 盘中开过板
    reseal = np.isfinite(cq) & (cq >= lu * 0.998)                       # 收盘回封（事后）

    def daily(mask, buy, sell, sig_row=None):
        out = []
        for t in range(1, T - 1):
            m = mask[t] & listing[t] & np.isfinite(buy[t]) & np.isfinite(sell[t]) & (buy[t] > 0)
            c = np.where(m)[0]
            if c.size:
                out.append(float(np.nanmean(sell[t, c] / buy[t, c] - 1.0)))
        return out

    res: dict = {"as_of": close.index[-1].strftime("%Y-%m-%d"), "cost_round_trip": 0.003,
                 "heights": {}}
    res["fills"] = {"touch_cells": int(touch.sum()),
                    "opened_cells": int((touch & opened).sum()),
                    "reseal_cells": int((touch & opened & reseal).sum()),
                    "broken_cells": int((touch & opened & ~reseal).sum())}

    for h, lab in ((0, "首板"), (1, "2板"), (2, "3板"), (3, "4板+")):
        hm = streak == h
        row = {}
        # —— 可执行 1：摸板即买（挂单在涨停价，开板才成交；成交价保守取涨停价）
        row["exec_board_touch"] = _stat(daily(touch & opened & hm, lu_q, nxt_op_q))
        # —— 可执行 2：次日开盘买 → 隔日开盘卖
        prev_hm = np.zeros_like(zt)
        prev_hm[1:] = hm[:-1]
        buy_open = np.where(np.isfinite(lu_q) & (op_q < lu_q * 0.998), op_q, np.nan)
        row["exec_next_open_1night"] = _stat(daily(prev_hm, buy_open, nxt2_op_q))
        # —— 可执行 3：次日开盘买 → 当日收盘卖
        nxt_cv = _shift(cv, -1)
        row["exec_next_open_intraday"] = _stat(daily(prev_hm, buy_open, nxt_cv))
        # —— 前视对照：要求收盘回封
        row["lookahead_reseal"] = _stat(daily(touch & opened & reseal & hm, lu_q, nxt_op_q))
        row["lookahead_broken"] = _stat(daily(touch & opened & ~reseal & hm, lu_q, nxt_op_q))
        res["heights"][lab] = row

    # 对账：回封/炸板 加权是否等于「摸板即买」
    r = res["heights"]["2板"]
    w_re = res["fills"]["reseal_cells"] / max(1, res["fills"]["opened_cells"])
    try:
        recon = (w_re * r["lookahead_reseal"]["mean_pct"]
                 + (1 - w_re) * r["lookahead_broken"]["mean_pct"])
    except KeyError:
        recon = None
    res["reconciliation_2板"] = {
        "weight_reseal": round(w_re, 4),
        "weighted_pct": round(recon, 3) if recon is not None else None,
        "measured_exec_pct": r["exec_board_touch"].get("mean_pct"),
        "note": "加权值应≈摸板即买（差异来自按日等权 vs 按格加权）"}
    return res


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
