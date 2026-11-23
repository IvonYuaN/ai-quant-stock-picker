#!/usr/bin/env python3
"""把收益拆成「日内」与「隔夜」两段当因子 —— 一个此前从未测过的维度。

## 为什么值得测

`turnover_cost_probe.py` 证明：主板 700 日，**日内（开→收）+0.111%/天**，
**隔夜（收→次日开）−0.079%/天**，全收益 +0.030%/天。也就是说同一只股票的钱
是**从一个特定时段流进来的**。此前所有因子（动量/波动/量价相关…）都建在
**收盘到收盘**上，等于把这两段加在一起、把信息揉掉了。

若某类股票的钱主要来自日内（真实买卖推动），另一类主要来自隔夜（跳空/情绪），
那它们的**未来延续性应该不同** —— 这是一个结构上独立的假设，不是同一批因子的变体。

## 构造（全部只用 ≤t 的价格，无前视）

    r_intra(t) = log(close_t / open_t)          日内
    r_over(t)  = log(open_t / close_{t-1})      隔夜
    I_t = Σ_{s≤t, 近 L 日} r_intra(s)            日内累计
    O_t = Σ_{s≤t, 近 L 日} r_over(s)             隔夜累计
    T_t = I_t + O_t                              = 收盘到收盘动量（旧口径，作对照）
    R_t = I_t − O_t                              「日内相对强度」——本脚本的主角

## 检验

  每月（每 20 个交易日）末按因子分 10 档，等权持 20 日，扣 0.15% 往返。
  报：各档净收益、多空价差、秩 IC、以及**扣成本后**的净年化。

判据（沿用打板 lane，不另立标准）：NW-t≥2 且扣成本后为正，且分 regime 不显著负。

用法：
    python research/intraday_overnight_factor.py --db <db> [--json out.json]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HOLD = 20          # 持有/调仓间隔（交易日）
LOOKBACKS = (20, 60)


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


def _rollsum(a: np.ndarray, w: int) -> np.ndarray:
    """沿时间轴滚动求和，最小样本 w//2。"""
    df = pd.DataFrame(a)
    return df.rolling(w, min_periods=max(5, w // 2)).sum().to_numpy()


def split_returns(op: np.ndarray, cq: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """把日收益拆成日内/隔夜两段（对数）。非法涨跌幅置 nan。

    r_intra(t) = log(close_t / open_t)
    r_over(t)  = log(open_t / close_{t-1})

    🔴 为什么置 nan 而不是保留：主板涨跌幅上限 ±10%，单日对数收益超过 log(1.11)
    只可能是除权、退市整理或脏数据；保留会把送转的 −50% 当成真实亏损。
    本库 `*_qfq` 列与原始列逐行相同（库里明写的 price_mode=raw 约定），
    所以除权**没有被消掉**，必须在这里挡住。
    """
    T, N = cq.shape
    with np.errstate(invalid="ignore", divide="ignore"):
        r_intra = np.log(cq / op)
        prev = np.vstack([np.full((1, N), np.nan), cq[:-1]])
        r_over = np.log(op / prev)
    bad = (np.abs(r_intra) > 0.105) | (np.abs(r_over) > 0.105)
    r_intra[bad] = np.nan
    r_over[bad] = np.nan
    return r_intra, r_over


def build_factors(op: np.ndarray, cq: np.ndarray, lookback: int) -> dict:
    """近 lookback 日累计的日内/隔夜收益。

    🔴 只用 ≤t 的数据：`_rollsum` 是向后看的 rolling sum，第 t 行只含 t-L+1..t。
    这条由 `tests/test_intraday_overnight_factor.py::test_factor_uses_only_past_data`
    用「扰动未来行、断言历史行不变」的变异方式守住。
    """
    r_intra, r_over = split_returns(op, cq)
    cum_intra = _rollsum(r_intra, lookback)
    cum_over = _rollsum(r_over, lookback)
    return {"cum_intra": cum_intra, "cum_over": cum_over,
            "momentum": cum_intra + cum_over,
            "rel_strength": cum_intra - cum_over}


def screen_mask(cum_intra: np.ndarray, cum_over: np.ndarray, cut: float,
                valid: np.ndarray) -> np.ndarray:
    """横截面筛选：剔除隔夜累计最差 cut 分位 + 日内累计最好 cut 分位。

    这是本组因子里唯一**可执行**的形态：
      - 隔夜累计高 = 近期靠跳空上涨，后续**继续**偏强（正 IC，+0.031）
      - 日内累计高 = 近期靠日内拉升，后续**回吐**（负 IC，−0.086）
      - 两者相加 = 经典动量 ⇒ 互相抵消（IC −0.076），这就是「动量无效」的真身
    A 股散户不能融券 ⇒ 空头腿不可实施，只能做多排除法。

    返回 bool 掩码（True = 保留）。cut=0 时全保留。
    """
    out = valid.copy()
    if cut <= 0:
        return out
    n = int(valid.sum())
    if n < 20:
        return np.zeros_like(valid)
    idx = np.where(valid)[0]
    rk_over = np.argsort(np.argsort(cum_over[idx])) / (n - 1.0)      # 0=最差
    rk_intra = np.argsort(np.argsort(cum_intra[idx])) / (n - 1.0)    # 1=最强
    keep = (rk_over > cut) & (rk_intra < 1.0 - cut)
    out[idx[~keep]] = False
    return out


def _nw_t(vals: np.ndarray, lag: int = 1) -> float:
    v = vals - vals.mean()
    n = len(v)
    if n < 3:
        return float("nan")
    var = float((v ** 2).sum()) / n
    if var <= 0:
        return float("nan")
    se = var / n
    for L in range(1, lag + 1):
        acf = float((v[L:] * v[:n - L]).sum()) / (n * var)
        se += 2.0 * acf * var / n
    return float(vals.mean() / np.sqrt(se)) if se else float("nan")


def probe(db: str, start: str = "20231101", n_dec: int = 10,
          cost: float = 0.0015) -> dict:
    sl = _load_probe()
    import sqlite3
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    df = pd.read_sql_query(
        """SELECT ts_code, trade_date, open, close FROM daily_qfq
           WHERE trade_date >= ? AND (ts_code LIKE '60%' OR ts_code LIKE '00%')
           ORDER BY ts_code, trade_date""", con, params=[start])
    con.close()
    df["ts_code"] = df["ts_code"].astype(str)
    df = df[df["ts_code"].map(sl.is_main_board)]

    def piv(c):
        p = df.pivot(index="trade_date", columns="ts_code", values=c)
        p.index = pd.to_datetime(p.index, format="%Y%m%d")
        return p.sort_index().to_numpy(dtype=float)

    op, cq = piv("open"), piv("close")
    T, N = cq.shape
    dates = pd.to_datetime(sorted(df["trade_date"].unique()))

    listed = np.isfinite(cq) & (cq > 0)
    cnt = pd.DataFrame(listed.astype(float)).rolling(60).sum().to_numpy()
    listed = listed & (cnt >= 50)

    # 前向 HOLD 日收益（收盘→收盘）
    fwd = np.full_like(cq, np.nan)
    fwd[:-HOLD] = cq[HOLD:] / cq[:-HOLD] - 1.0
    fwd[~listed] = np.nan

    def factor_panel(lb):
        f = build_factors(op, cq, lb)
        return {"日内累计": f["cum_intra"], "隔夜累计": f["cum_over"],
                "动量(=I+O)": f["momentum"], "日内相对强度(I-O)": f["rel_strength"]}

    out: dict = {"as_of": str(dates[-1].date()), "start": start, "hold_days": HOLD,
                 "n_stocks": int(df["ts_code"].nunique()), "n_days": int(T),
                 "cost_round_trip_pct": cost * 100, "lookbacks": list(LOOKBACKS),
                 "factors": {}}
    rebal = np.arange(250, T - HOLD - 1, HOLD)     # 留足 250 日预热

    for L in LOOKBACKS:
        for name, F in factor_panel(L).items():
            rows = []
            for t in rebal:
                x = F[t].copy()
                ok = listed[t] & np.isfinite(x) & np.isfinite(fwd[t])
                idx = np.where(ok)[0]
                if idx.size < 200:
                    continue
                xs = x[idx]
                rk = xs.argsort().argsort() / (idx.size - 1.0)     # 0..1 分位
                bins = np.clip((rk * n_dec).astype(int), 0, n_dec - 1)
                means = np.array([fwd[t, idx[bins == b]].mean() if (bins == b).any()
                                  else np.nan for b in range(n_dec)])
                ic = float(pd.Series(xs).corr(pd.Series(fwd[t, idx]), method="spearman"))
                rows.append({"t": t, "means": means, "ic": ic, "n": idx.size})
            if len(rows) < 12:
                out["factors"]["%s@%dd" % (name, L)] = {"note": "调仓次数<%d" % 12}
                continue
            M = np.vstack([r["means"] for r in rows])
            ls = M[:, -1] - M[:, 0]                     # 高减低，毛收益
            net = ls - cost * 2                         # 一买一卖
            per_year = 240.0 / HOLD
            cagr = float(np.prod(1.0 + net) ** (per_year / len(net)) - 1.0) * 100
            out["factors"]["%s@%dd" % (name, L)] = {
                "n_rebal": int(len(rows)),
                "decile_means_pct": [round(float(x) * 100, 3) for x in np.nanmean(M, axis=0)],
                "top_minus_bottom_gross_pct": round(float(ls.mean()) * 100, 3),
                "top_minus_bottom_net_pct": round(float(net.mean()) * 100, 3),
                "t_nw": round(_nw_t(net, 1), 2),
                "win_pct": round(float((net > 0).mean()) * 100, 1),
                "ic_mean": round(float(np.nanmean([r["ic"] for r in rows])), 4),
                "net_cagr_pct": round(cagr, 1),
            }
    return out


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
