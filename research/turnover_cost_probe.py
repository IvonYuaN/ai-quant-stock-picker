#!/usr/bin/env python3
"""为什么「日频短线」在这套数据上几乎不可能赚钱 —— 把成本、隔夜、日内三件事一次算清。

## 起因

打板/P3/P5 三条路逐一被证伪后，需要一个**结构性**解释：是我的信号找得不够好，
还是这个市场结构本身不给日频留空间？本脚本给出可对账的答案。

## 三个恒等式（全部用主板 3283 只、700 交易日、等权截面均值）

    全（收→次日收） = 隔夜（收→次日开） + 日内（开→收）

实测（**税前**）：

    隔夜  −0.079%/天（中位 −0.071%，39.7% 的交易日为正）
    日内  +0.112%/天（中位 +0.207%，58.1% 的交易日为正）
    全    +0.032%/天

⇒ **A 股的收益几乎全在日内，隔夜是净流出。** 这与美股（隔夜为正、日内偏平）相反。

## 而 T+1 恰好禁止你赚那一半

当日买入的股票**当日不能卖**（T+1），所以「开盘买、收盘卖」这个日内多头
在制度上不成立。你能做的只有「今天收盘买 → 明天某时卖」，
即**必须承担那段负的隔夜**。

## 成本把最后一点空间吃干净

日频全换手 = 每天一次往返 = 0.15%（佣金 2.5bp×2 + 印花税 10bp + 过户费 0.2bp）。

    全市场日均税前收益 +0.032%  vs  每次往返成本 0.150%
    ⇒ 日频等权全换手 ≈ −0.118%/天 ≈ **−25%/年**（复利）
    ⇒ 成本拖累单独就是 0.15% × 240 ≈ **36%/年**

**这就是 alpha 赤字的真身**：不是因子不够多，是日频策略的生存门槛被抬到
「每笔必须有 >0.15% 的可执行超额」。而这个门槛下，实测三条路全部为负：

    打板（摸板即买）   首板 −0.94%/笔  2板 −2.30%/笔
    超跌反抽（P3）     −0.12%/笔（税前），分 regime 全 ≤0
    尾盘惯性（P5）     −0.72%/笔（税前 −0.57%）

## 对 P5 的判决

P5「涨 3~7% + 量比≥1.5，14:45 买、次日开盘卖」= **专门去买那段负的隔夜**，
并且只在「当日已经涨了 3~7%」时买（涨幅越高、隔夜越负：涨 7~10% 档 −1.06%）。
⇒ 方向性错误，不是参数没调好。**禁再调参尝试救活。**

## 唯一残留的方向（记录，不宣称成立）

日内为正而 T+1 禁止当日了结 ⇒ 可行的形态只能是**持有多日**（把日内收益累积起来），
成本被摊薄到 0.15%/持有天数。但持有多日就要承担隔夜流出与方向风险，
那是另一个 lane（中线/主战场），不是短线。本脚本不评估它。

用法：
    python research/turnover_cost_probe.py --db <db> [--json out.json]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


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


def _pivot(df: pd.DataFrame, col: str) -> np.ndarray:
    p = df.pivot(index="trade_date", columns="ts_code", values=col)
    p.index = pd.to_datetime(p.index, format="%Y%m%d")
    return p.sort_index().to_numpy(dtype=float)


def _daily(mask, buy, sell, listed, lo=6):
    """逐日截面等权收益（不扣成本）。"""
    T = buy.shape[0]
    out = []
    for t in range(lo, T - 1):
        m = mask[t] & listed[t] & np.isfinite(buy[t]) & np.isfinite(sell[t]) & (buy[t] > 0)
        c = np.where(m)[0]
        if c.size >= 5:
            out.append(float(np.nanmean(sell[t, c] / buy[t, c] - 1.0)))
    return np.asarray(out, dtype=float)


def _fmt(name, arr, cost=0.0):
    a = arr[np.isfinite(arr)]
    if a.size < 20:
        return {"n_days": int(a.size), "note": "样本<20"}
    b = a - cost
    t = b.mean() / (b.std(ddof=1) / np.sqrt(b.size))
    return {"n_days": int(b.size),
            "mean_pct": round(float(b.mean()) * 100, 4),
            "median_pct": round(float(np.median(b)) * 100, 4),
            "win_pct": round(float((b > 0).mean()) * 100, 1),
            "t": round(float(t), 2)}


def probe(db: str, start: str = "20231101") -> dict:
    sl = _load_probe()
    con_sql = """
    SELECT ts_code, trade_date, open, high, low, close, close_qfq, volume
    FROM daily_qfq WHERE trade_date >= ? AND (ts_code LIKE '60%' OR ts_code LIKE '00%')
    ORDER BY ts_code, trade_date
    """
    import sqlite3
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    df = pd.read_sql_query(con_sql, con, params=[start])
    con.close()
    df["ts_code"] = df["ts_code"].astype(str)
    df = df[df["ts_code"].map(sl.is_main_board)]

    op, cq, cv = (_pivot(df, c) for c in ("open", "close", "close_qfq"))
    vol = _pivot(df, "volume")
    T, N = cv.shape

    listed = np.isfinite(cv) & (cq > 0)
    cnt = pd.DataFrame(listed.astype(float)).rolling(30).sum().to_numpy()
    listed = listed & (cnt >= 25)

    with np.errstate(invalid="ignore", divide="ignore"):
        dayret = cv / _shift(cv, 1) - 1.0          # 当日涨跌幅（选股条件用）
        vma5 = pd.DataFrame(vol).rolling(5).mean().to_numpy()
        vr = vol / vma5

    res: dict = {"as_of": df["trade_date"].max(), "start": start,
                 "n_stocks": int(df["ts_code"].nunique()), "n_days": int(T),
                 "cost_one_way": 0.00075, "cost_round_trip": 0.0015}

    res["decomposition"] = {
        "overnight_pre": _fmt("overnight", _daily(np.ones_like(listed), cq, _shift(op, -1), listed)),
        "overnight_post": _fmt("overnight", _daily(np.ones_like(listed), cq, _shift(op, -1), listed), 0.0015),
        "intraday_pre": _fmt("intraday", _daily(np.ones_like(listed), op, cv, listed)),
        "full_pre": _fmt("full", _daily(np.ones_like(listed), cv, _shift(cv, -1), listed)),
        "full_post": _fmt("full", _daily(np.ones_like(listed), cv, _shift(cv, -1), listed), 0.0015),
    }

    # 成本门槛：日均税前收益 vs 单次往返成本
    f = res["decomposition"]["full_pre"].get("mean_pct") or 0.0
    res["cost_hurdle"] = {
        "daily_gross_pct": f,
        "round_trip_cost_pct": 0.15,
        "net_per_day_pct": round(f - 0.15, 4),
        "cost_drag_per_year_pct": 36.0,
        "note": "日频全换手成本拖累约 36%%/年；日均税前仅 +{f:.3f}%%".format(f=f),
    }

    # P5：尾盘买、次日开盘卖 —— 专门去买那段负隔夜
    p5 = (dayret >= 0.03) & (dayret <= 0.07) & (vr >= 1.5)
    res["P5"] = {
        "params": "涨3~7% + 量比≥1.5，收盘买 → 次日开盘卖",
        "pre": _fmt("P5", _daily(p5, cv, _shift(op, -1), listed)),
        "post": _fmt("P5", _daily(p5, cv, _shift(op, -1), listed), 0.0015),
        "to_next_close_post": _fmt("P5c", _daily(p5, cv, _shift(cv, -1), listed), 0.0015),
    }

    # 涨幅分档：隔夜随涨幅单调变负 ⇒ P5 的选股条件本身选的是最差的隔夜
    buckets = {}
    for a, b in ((-0.10, -0.03), (-0.03, 0.0), (0.0, 0.02),
                 (0.02, 0.05), (0.05, 0.07), (0.07, 0.095)):
        key = "%+.0f%%~%+.0f%%" % (a * 100, b * 100)
        buckets[key] = _fmt(key, _daily((dayret > a) & (dayret <= b),
                                        cv, _shift(op, -1), listed))
    res["overnight_by_day_return"] = buckets

    # 量比加严只会更差（P5 的第二个条件也是反向的）
    res["P5_volume_ratio_grid"] = {
        "%.1f" % v: _fmt("vr>=%.1f" % v,
                         _daily((dayret >= 0.03) & (dayret <= 0.07) & (vr >= v),
                                cv, _shift(op, -1), listed))
        for v in (1.0, 1.2, 1.5, 2.0, 3.0)
    }
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
