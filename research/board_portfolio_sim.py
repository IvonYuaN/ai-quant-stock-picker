#!/usr/bin/env python3
"""把打板 lane 的「每笔统计」换成「资金约束下的组合净值」——唯一能回答「一年赚多少」的东西。

## 这个脚本的首要用途是验伪，不是报喜

此前所有结论都是**格级等权均值 + t 值**：4220 个「严格二板 B 格」每笔 +2.127%、t 19.4。
把它放进组合复利 240 个交易日后得到 **1839%/年**。这个数量级不可能存在，
所以它首先证明的是**口径有问题**，而不是发现了圣杯。根因已定位：

    B 格的定义是 `收盘价 ≥ 涨停价 × 0.998` = **收盘封死在涨停**。
    收盘封死 ⇒ 14:57 后面排着几万手买单，你买不到。
    格级统计却按「收盘价（=涨停价）买入」计收益 ⇒ 记下的是一个买不到的价格。

这就是 `h=1 信号日收盘 +3.06%` 系列数字的真实性质：**理论价，不可执行**。
（全样本 37723 个「收盘封死」 vs 5852 个「一字顶死」，前者被当成可成交，其实同样不可成交。）

## 两种入场口径必须并排看

  `entry=close`   —— 信号日收盘买入。**不可执行**，只作理论上限/历史口径对照。
  `entry=open_t1` —— 信号日收盘确认，**次日开盘买入**。完全可执行：开盘价一出来你就知道买不买，
                     没有前视。一字涨停买不到（open≥涨停×0.998）则跳过，与实盘一致。

两者用同一份数据、同一套成本、同一个卖出规则（次日开盘卖，一字跌停顺延），
差额就是「口径幻觉」的大小。

## 其余真实约束（全部计入）

  1. **资金约束**：同日信号 > 仓位上限时必须选，选了就不是「全取均值」。
  2. **整手约束**：A 股 100 股一手，买不起一手就跳过。
  3. **卖不掉**：次日一字跌停（open≤跌停×1.002 且 high≤open×1.002）卖不出，顺延最多 5 日。
  4. **成本**：单边 0.075%（佣金2.5bp+过户0.2bp；卖出另加印花税10bp）⇒ 往返≈0.15%。
  5. 仓位 = equity/M；无信号空仓（不凑满，资金利用率如实报）。

## 选股（信号 > M 时）
`rank=amount` 按成交额(vol×close)降序——容量优先，收盘即知，无前视；`rank=random` 作无偏对照。

## 输出
CAGR / 最大回撤 / 夏普 / 卡玛 / 日均持仓 / 资金利用率 / 笔数 / 胜率 / 单笔均值 /
顺延笔数，以及**全市场等权（前复权）**基准作对照。
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


def _streak_prev(zt: np.ndarray) -> np.ndarray:
    """截至**前一日**的连续涨停天数（收盘即知，无前视）。"""
    T, N = zt.shape
    out = np.zeros((T, N), dtype=np.int8)
    run = np.zeros(N, dtype=np.int8)
    for t in range(T):
        out[t] = run
        run = np.where(zt[t], run + 1, 0)
    return out


def _metrics(equity, dates, trades, util, npos, defer, bench) -> dict:
    eq = np.asarray(equity, dtype=float)
    if eq.size < 3 or eq[0] <= 0:
        return {"note": "样本不足"}
    years = (dates[-1] - dates[0]).days / 365.25
    total = eq[-1] / eq[0]
    cagr = (total ** (1.0 / years) - 1.0) * 100 if years > 0.2 and total > 0 else float("nan")
    peak = np.maximum.accumulate(eq)
    mdd = float(((eq / peak - 1.0) * 100).min())
    r = eq[1:] / eq[:-1] - 1.0
    sd = float(r.std(ddof=1)) if r.size > 2 else float("nan")
    sharpe = float(r.mean() / sd * np.sqrt(240)) if sd and sd > 0 else float("nan")
    out = {
        "total_multiple": round(float(total), 3),
        "cagr_pct": round(float(cagr), 2) if np.isfinite(cagr) else None,
        "max_drawdown_pct": round(mdd, 2),
        "sharpe": round(sharpe, 2) if np.isfinite(sharpe) else None,
        "calmar": round(float(cagr / abs(mdd)), 2) if np.isfinite(cagr) and mdd < 0 else None,
        "n_trades": int(len(trades)),
        "avg_positions": round(float(np.mean(npos)), 2) if npos else 0.0,
        "avg_utilization_pct": round(float(np.mean(util)) * 100, 1) if util else 0.0,
        "deferred_sells": int(defer),
    }
    tr = np.asarray([t for t in trades if np.isfinite(t)], dtype=float)
    if tr.size:
        out["trade_mean_pct"] = round(float(tr.mean()) * 100, 3)
        out["trade_median_pct"] = round(float(np.median(tr)) * 100, 3)
        out["trade_win_pct"] = round(float((tr > 0).mean()) * 100, 1)
    b = np.asarray([x for x in bench if np.isfinite(x)], dtype=float)
    if b.size > 20:
        bt = float(np.prod(1.0 + b))
        out["bench_total_multiple"] = round(bt, 3)
        out["bench_cagr_pct"] = round(float((bt ** (240.0 / b.size) - 1.0) * 100), 2)
    return out


def sim(db: str, start: str = "20231101", height: int = 1, max_pos: int = 5,
        capital: float = 100000.0, entry: str = "open_t1", rank: str = "amount",
        cost_one_way: float = 0.00075, seed: int = 0) -> dict:
    sl = _load_probe()
    P = sl.load_main_board(db, start)
    close = P["close"]
    cv = close.to_numpy(dtype=float)          # 前复权：算涨跌幅、算基准
    cq = P["cqr"].to_numpy(dtype=float)       # 非复权：算涨停价、成交价
    op = P["opn"].to_numpy(dtype=float)
    hh = P["high"].to_numpy(dtype=float)
    vol = P["vol"].to_numpy(dtype=float)
    T, N = cv.shape
    dates = close.index
    listing = sl._listing_mask(close, lookback=30)

    with np.errstate(invalid="ignore", divide="ignore"):
        zt = cv / np.roll(cv, 1, axis=0) - 1.0 >= 0.098
    zt[0, :] = False
    streak = _streak_prev(zt)

    prev_cq = np.roll(cq, 1, axis=0)
    prev_cq[0, :] = np.nan
    limit_up = np.round(prev_cq * 1.10, 2)
    limit_dn = np.round(prev_cq * 0.90, 2)
    amount = vol * cq

    ok = np.isfinite(hh) & np.isfinite(limit_up) & np.isfinite(op) & np.isfinite(cq) & (cq > 0)

    def _shift1(a):
        out = np.full_like(a, np.nan)
        out[:-1] = a[1:]
        return out

    nxt_cq = _shift1(cq)
    rng = np.random.default_rng(seed)
    cash = float(capital)
    pos: list[dict] = []
    equity, eq_dates, util, npos = [], [], [], []
    trades, defer = [], 0
    bench, n_taken, n_sig = [], 0, 0

    for t in range(1, T - 1):
        # ---- 1) 卖出到期仓（全部在今日开盘卖）
        still = []
        for p in pos:
            i = p["i"]
            px = op[t, i]
            if not np.isfinite(px) or px <= 0:
                still.append(p)                        # 停牌锁着
                continue
            ld = limit_dn[t, i]
            sealed_dn = (np.isfinite(ld) and px <= ld * 1.002
                         and np.isfinite(hh[t, i]) and hh[t, i] <= px * 1.002)
            if sealed_dn and (t - p["buy_t"]) < 5:
                defer += 1
                still.append(p)
                continue
            cash += p["shares"] * px * (1 - cost_one_way)
            trades.append(px / p["buy_px"] - 1.0 - cost_one_way * 2)
        pos = still

        # ---- 2) 今日是否有买入信号
        #   entry=close   ：今日收盘封死（不可执行，仅对照）
        #   entry=open_t1 ：**昨日**收盘封死 → 今日开盘买（完全可执行）
        if entry == "close":
            sig = t
            fillable = ok[t] & zt[t] & (streak[t] == height)
            buy_px_arr = np.where(ok[t], cq[t], np.nan)
        elif entry == "open_t1":
            sig = t - 1
            sig_ok = sig >= 1
            if not sig_ok:
                continue
            # 昨日涨停收盘 + 连板高度对；今日开盘不是一字涨停（否则买不到）
            fillable = (ok[t] & zt[sig] & (streak[sig] == height)
                        & np.isfinite(limit_up[t]) & (op[t] < limit_up[t] * 0.998))
            buy_px_arr = op[t]
        else:
            raise SystemExit("[fatal] entry 只能是 close / open_t1")

        cols = np.where(fillable & listing[t])[0]
        held = {p["i"] for p in pos}
        cols = np.array([c for c in cols if c not in held], dtype=int)
        n_sig += int(cols.size)
        if cols.size:
            if rank == "amount":
                key = np.nan_to_num(amount[t, cols], nan=0.0)
            else:
                key = rng.random(cols.size)
            cols = cols[np.argsort(-key)]
            budget = (cash + sum(p["shares"] * (cq[t, p["i"]] if np.isfinite(cq[t, p["i"]])
                                                else p["buy_px"]) for p in pos)) / float(max_pos)
            for c in cols:
                if len(pos) >= max_pos:
                    break
                px = buy_px_arr[c] if np.ndim(buy_px_arr) else buy_px_arr
                if not np.isfinite(px) or px <= 0:
                    continue
                lots = int(budget // (px * 100.0 * (1 + cost_one_way)))
                need = lots * 100 * px * (1 + cost_one_way)
                if lots < 1 or need > cash:
                    lots = int(cash // (px * 100.0 * (1 + cost_one_way)))
                    need = lots * 100 * px * (1 + cost_one_way)
                if lots < 1:
                    continue                            # 一手都买不起 ⇒ 跳过
                cash -= need
                pos.append({"i": c, "buy_px": px, "shares": lots * 100, "buy_t": t})
                n_taken += 1

        # ---- 3) 盘后标记净值（**买入之后**，用今日收盘价；这样利用率才是真的）
        mv = 0.0
        for p in pos:
            px = cq[t, p["i"]]
            mv += p["shares"] * (px if np.isfinite(px) and px > 0 else p["buy_px"])
        eqv = cash + mv
        equity.append(eqv)
        eq_dates.append(dates[t])
        util.append(mv / eqv if eqv > 0 else 0.0)
        npos.append(len(pos))

        # ---- 基准：全市场等权、每日再平衡、**前复权**（非复权会把除权当亏损）
        bm = listing[t] & np.isfinite(nxt_cq[t]) & np.isfinite(cq[t]) & (cq[t] > 0)
        bc = np.where(bm)[0]
        if bc.size >= 60:
            with np.errstate(invalid="ignore", divide="ignore"):
                rb = cv[t + 1, bc] / cv[t, bc] - 1.0
            rb = rb[np.isfinite(rb)]
            if rb.size >= 60:
                bench.append(float(rb.mean()) - cost_one_way * 2)

    # 末尾强平
    for p in pos:
        px = cq[T - 1, p["i"]] if np.isfinite(cq[T - 1, p["i"]]) else p["buy_px"]
        cash += p["shares"] * px * (1 - cost_one_way)
        trades.append(px / p["buy_px"] - 1.0 - cost_one_way * 2)

    out = _metrics(np.asarray(equity), eq_dates, trades, util, npos, defer, bench)
    out.update({"height": "第%d板" % (height + 1), "max_pos": max_pos, "entry": entry,
                "capital": capital, "rank": rank,
                "n_taken": int(n_taken), "n_signal": int(n_sig),
                "window": "%s ~ %s" % (eq_dates[0].strftime("%Y-%m-%d"),
                                       eq_dates[-1].strftime("%Y-%m-%d"))})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--start", default="20231101")
    ap.add_argument("--heights", default="0,1")
    ap.add_argument("--max-pos", default="3,5,10")
    ap.add_argument("--entries", default="close,open_t1")
    ap.add_argument("--capital", type=float, default=100000.0)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    rows = []
    for entry in [x.strip() for x in args.entries.split(",") if x.strip()]:
        for h in [int(x) for x in args.heights.split(",")]:
            for mp in [int(x) for x in args.max_pos.split(",")]:
                r = sim(args.db, args.start, height=h, max_pos=mp,
                        capital=args.capital, entry=entry)
                rows.append(r)
                print("[%s %s M=%d] %.2fx CAGR %s%% MDD %s%% Sharpe %s 笔%d "
                      "均笔%s%% 胜%s%% 仓位%s%% 顺延%d" % (
                          r["height"], entry, mp, r.get("total_multiple", 0),
                          r.get("cagr_pct"), r.get("max_drawdown_pct"), r.get("sharpe"),
                          r.get("n_trades", 0), r.get("trade_mean_pct"),
                          r.get("trade_win_pct"), r.get("avg_utilization_pct", 0),
                          r.get("deferred_sells", 0)), file=sys.stderr)
    txt = json.dumps(rows, ensure_ascii=False, indent=2)
    print(txt)
    if args.json:
        Path(args.json).write_text(txt, encoding="utf-8")
        print("[written] %s" % args.json, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
