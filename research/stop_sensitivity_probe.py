#!/usr/bin/env python3
"""止损位敏感性 + P6 参数稳健性检验 —— 直击口径不一致：

过门的 P1（h1 +3.06%/t8.61）与 B 格（+1.37%/t19.2）都是**无止损**口径：
信号日收盘（=涨停价）买入，次日收盘卖出，中途不看回撤。
但影子账本（paper_arena）给所有 sleeve 硬加了 stop_pct=0.05，
前向实测 stop_impact：P1/P6 −2.60pp、P2 −6.89pp ⇒ 止损在吃已验证的 alpha。

这不是「亏了调参」，而是**执行规则与验证口径对齐**：要么把账本止损改到回测口径，
要么证明某个止损位最优。两种都得先有数据。所以本脚本对同一批历史信号回放：

  止损档位 ∈ {关闭, 3%, 5%, 8%, 10%, 15%}
  触发判定 : 次日 low <= 买入价 × (1 - stop)
  成交价   : min(买入价 × (1 - stop), 次日 open)   ← 跳空低开按开盘价，与账本 _close 同口径

同时回答两个「合理的参数模式」问题：
  1) P6 的 top_n 平台在哪（top1/2/3/5/8 一次性给出，避免单点最优的假象）
  2) 跳空幅度分档（<1% / 1~2% / 2~4% / 4~7% / ≥7%）是否单调 —— 若 monoton，
     说明 P6 的本质是「跳空强度」而不是「取两只」这个离散操作

口径纪律（与既有 lane 一致，不新造）：
  - 池 = load_main_board（纯主板 60/00，20cm 已剔）
  - 买入价 = 信号日非复权 close（B 格定义含 close≥涨停，即涨停价）
  - 收益 = 绝对毛收益（不打成本，成本另列一档），**同时**报相对池等权超额
  - t 值按交易日聚类（每天一个组合观测），NW 修正，lag 由自相关定
  - 不写未来数据：t+1 的 low/open/close 只在卖出日使用，选股只用 t 及以前
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


def _stat(daily, label):
    """daily = 每交易日的组合毛收益（比例）。返回均值/胜率/t。"""
    ex = np.asarray([d for d in daily if np.isfinite(d)], dtype=float)
    if len(ex) < 20:
        return {"label": label, "n_days": int(len(ex)), "note": "样本<20 跳过"}
    lag = _acf_lag(ex)
    return {
        "label": label,
        "n_days": int(len(ex)),
        "mean_pct": round(float(ex.mean()) * 100, 3),
        "med_pct": round(float(np.median(ex)) * 100, 3),
        "std_pct": round(float(ex.std(ddof=0)) * 100, 3),
        "win_pct": round(float((ex > 0).mean()) * 100, 1),
        "t_nw": round(_nw_t(ex, lag), 2),
        "nw_lag": int(lag),
    }


def probe(db: str, start: str = "20231101",
          costs=((0.0, ""), (0.003, "_cost03"), (0.01, "_cost1"), (0.02, "_cost2"))) -> dict:
    """costs 是**往返**成本档位。0.003=真实费率档（佣金2.5bp×2+印花税10bp+过户费≈15bp，
    再留一半余量给收盘集合竞价滑点）。0.02 是账本沿用的旧假设（单边1%），
    它没有任何费率依据 —— A 股不存在 1% 的固定交易费率 —— 但保留下来做「结论对成本多敏感」的对照。"""
    sl = _load_probe()
    P = sl.load_main_board(db, start)
    close = P["close"]
    cv = close.to_numpy(dtype=float)     # qfq close（跨日基准/超额）
    cq = P["cqr"].to_numpy(dtype=float)  # 非复权 close（真实成交价）
    hh = P["high"].to_numpy(dtype=float)
    ll = P["low"].to_numpy(dtype=float)
    op = P["opn"].to_numpy(dtype=float)
    T, N = cv.shape
    dates = close.index
    listing = sl._listing_mask(close, lookback=30)

    # ---- setup：前一日 sealed 涨停 + 今日高开 ----
    # 🔴 11-23 修 off-by-one（原写法错了两处，导致 setup 完全失效）：
    #   旧：zt_prev = cv / np.roll(cv,1) - 1.0 ; zt_prev[:, 0] = np.nan
    #   ① 这个式子的 t 行算的是「**当日**涨幅」（prev 只指「用前一日收盘算」）；
    #   ② `[:, 0]` 清的是第 0 **列**（第一只股票），不是第 0 **行**；
    #   ⇒ `setup_prev_zt` 实际要求「今日涨停」，与 B 格「今日触板收板」同义反复，
    #     setup 形同虚设 ⇒ 所谓「B 格」是**全部连板高度混合**，不是二板。
    #   正确语义：昨日涨停 = 截至前一日的连续涨停 ≥1（用 streak 计数，与
    #   research/board_height_probe.py / 实盘 trailing_zt_run() 同一口径）。
    with np.errstate(invalid="ignore", divide="ignore"):
        zt_today = cv / np.roll(cv, 1, axis=0) - 1.0 >= 0.098
    zt_today[0, :] = False                       # 第 0 行无前一日，无效
    streak_prev = np.zeros_like(zt_today, dtype=np.int8)
    _run = np.zeros(zt_today.shape[1], dtype=np.int8)
    for _t in range(zt_today.shape[0]):
        streak_prev[_t] = _run                   # 先记「截至前一日」
        _run = np.where(zt_today[_t], _run + 1, 0)
    setup_prev_zt = streak_prev >= 1             # 昨日涨停（连续≥1天）
    with np.errstate(invalid="ignore", divide="ignore"):
        gap = op / np.roll(cq, 1, axis=0) - 1.0     # 今日跳空（非复权，P6 排序键）
    gap[0, :] = np.nan
    limit_up = np.round(np.roll(cq, 1, axis=0) * 1.10, 2)
    limit_up[0, :] = np.nan
    valid = setup_prev_zt & np.isfinite(hh) & np.isfinite(limit_up) & np.isfinite(op) & np.isfinite(gap)
    near = hh >= limit_up * 0.998
    B = valid & (op < limit_up * 0.998) & near & (cq >= limit_up * 0.998)   # 放量封住（可成交）
    C = valid & (op < limit_up * 0.998) & near & (cq < limit_up * 0.998)    # 炸板（可成交）
    A = valid & (op >= limit_up * 0.998) & (cq >= limit_up * 0.998)         # 一字（排不进）
    BC = B | C
    ANY = A | B | C                                                          # 全部触板（含排不进）
    # A 格必须再拆：老大的原话「溢价全在排不进的 A」是对的（P6_ANY 是 P6_B 的两倍多），
    # 但「A」不等于「买不到」——只有全天封死才叫买不到。盘中开过板再回封的，
    # 开板那一刻挂单是能成交的。这两者混在一起会把「可执行」误判成「不可执行」。
    A1 = A & (ll >= limit_up * 0.998)                                        # 真一字：全天没开过板
    A2 = A & (ll < limit_up * 0.998)                                         # 开板回封：盘中打开过，可买
    D = valid & (op < limit_up * 0.998) & ~near                              # 高开未封

    # ---- 前向：t+1 的 open / low / close（非复权，止损判定用）----
    def _shift1(a):
        out = np.full_like(a, np.nan)
        out[:-1] = a[1:]
        return out

    nxt_op, nxt_lo, nxt_cq = _shift1(op), _shift1(ll), _shift1(cq)
    buy = cq                                                              # 信号日收盘=涨停价

    # 池等权基准（t+1 qfq 收益），用于报超额
    fwd1 = np.roll(cv, -1, axis=0) / cv - 1.0
    fwd1[-1, :] = np.nan
    pool = np.full(T, np.nan)
    okf = np.isfinite(fwd1) & listing
    for t in range(T):
        r = fwd1[t][okf[t]]
        if len(r) >= 60:
            pool[t] = r.mean()

    def branch_ret(t, cols, stop, cost):
        """返回 (绝对毛收益数组, 是否触发止损数组)。stop=None 表示不止损。"""
        b = buy[t, cols]
        c = nxt_cq[t, cols]
        absr = c / b - 1.0
        hit = np.zeros(len(cols), dtype=bool)
        if stop is None:
            return absr - cost, hit
        thr = b * (1.0 - stop)
        lo_ = nxt_lo[t, cols]
        op_ = nxt_op[t, cols]
        hit = np.isfinite(lo_) & (lo_ <= thr)
        # 跳空低开：开盘就已在止损价下方 ⇒ 按开盘成交（诚实，不假装在止损价成交）
        fill = np.where(np.isfinite(op_) & (op_ < thr), op_, thr)
        absr = np.where(hit, fill / b - 1.0, absr)
        return absr - cost, hit

    def eval_mask(mask, stop, cost, top_n=None, subset=None):
        """逐日聚合：当天命中集合的等权收益（top_n 时按跳空取最大的几只）。"""
        daily, hits, n_cells = [], [], 0
        for t in range(1, T - 1):
            m = mask[t] & listing[t] & np.isfinite(nxt_cq[t]) & np.isfinite(buy[t]) & (buy[t] > 0)
            if subset is not None:
                m = m & subset[t]
            cols = np.where(m)[0]
            if len(cols) == 0:
                continue
            if top_n:
                g = gap[t, cols]
                order = np.argsort(-np.nan_to_num(g, nan=-9e9))
                cols = cols[order[:top_n]]
            r, h = branch_ret(t, cols, stop, cost)
            if not np.isfinite(r).any():
                continue
            daily.append(float(np.nanmean(r)))
            hits.append(float(h.mean()) if len(h) else 0.0)
            n_cells += len(cols)
        rec = _stat(daily, "")
        rec.pop("label", None)
        rec["n_cells"] = n_cells
        rec["stop_hit_pct"] = round(float(np.mean(hits)) * 100, 1) if hits else 0.0
        return rec

    STOPS = [(None, "no_stop"), (0.03, "stop3"), (0.05, "stop5"),
             (0.08, "stop8"), (0.10, "stop10"), (0.15, "stop15")]

    out_cells: dict[str, dict] = {}
    for name, mask, top_n in (("B_放量封住", B, None),
                              ("C_炸板", C, None),
                              ("BC_可成交合并", BC, None),
                              ("A1_真一字_买不到", A1, None),
                              ("A2_开板回封_可买", A2, None),
                              ("D_高开未封", D, None),
                              ("ANY_全部触板", ANY, None),
                              ("P6_二板跳空top2_B", B, 2),
                              ("P6_二板跳空top2_A2", A2, 2),
                              ("P6_二板跳空top2_ANY", ANY, 2)):
        block = {}
        for stop, tag in STOPS:
            for c_lvl, c_tag in costs:
                block[tag + c_tag] = eval_mask(mask, stop, c_lvl, top_n=top_n)
        out_cells[name] = block

    # ---- A2 开板深度分档：开板越浅（回封越快）是否越强 ----
    # 深度 = (涨停价 - 当日最低) / 前收，即开板时相对涨停价砸下去多少
    prev_c = limit_up / 1.10
    with np.errstate(invalid="ignore", divide="ignore"):
        open_depth = (limit_up - ll) / prev_c
    depth_buckets = {}
    for lo_, hi_ in ((0.0, 0.005), (0.005, 0.01), (0.01, 0.02), (0.02, 0.04), (0.04, 0.30)):
        sub = np.isfinite(open_depth) & (open_depth >= lo_) & (open_depth < hi_)
        depth_buckets["depth_%.1f_%.1fpct" % (lo_ * 100, hi_ * 100)] = {
            "nostop": eval_mask(A2, None, 0.0, subset=sub),
            "stop5": eval_mask(A2, 0.05, 0.0, subset=sub),
        }

    # ---- P6 top_n 平台 ----
    topn_curve = {}
    for k in (1, 2, 3, 5, 8, 15):
        topn_curve[str(k)] = eval_mask(B, 0.05, 0.0, top_n=k)
        topn_curve[str(k) + "_nostop"] = eval_mask(B, None, 0.0, top_n=k)

    # ---- 跳空分档（B 格内，不取 top_n，看单调性）----
    gap_buckets = {}
    edges = [(-9.9, 0.01), (0.01, 0.02), (0.02, 0.04), (0.04, 0.07), (0.07, 0.22)]
    for lo_, hi_ in edges:
        sub = np.isfinite(gap) & (gap >= lo_) & (gap < hi_)
        key = "gap_%.0f_%.0f" % (lo_ * 100, hi_ * 100)
        gap_buckets[key] = {
            "nostop": eval_mask(B, None, 0.0, subset=sub),
            "stop5": eval_mask(B, 0.05, 0.0, subset=sub),
        }

    return {
        "as_of": dates[-1].strftime("%Y-%m-%d"),
        "start": start,
        "n_days_total": int(T),
        "cells": out_cells,
        "topn_curve": topn_curve,
        "gap_buckets": gap_buckets,
        "open_depth_buckets": depth_buckets,
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
