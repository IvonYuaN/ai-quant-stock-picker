#!/usr/bin/env python3
"""转债 ETF 隔夜探针 —— 这是「转债隔夜」这条路的**终审**，不是又一个探针。

## 为什么必须测 ETF

`cb_overnight_probe.py` 把转债隔夜做到了 +0.1391%/天、t5.01（¥10000、高价优先、单只≤1/3）。
但把收益拆成 α + β×市场隔夜之后，结果是：

    α +0.0268%/天 t1.34      β 2.32

也就是 **81% 的收益是「2.3 倍杠杆买转债市场的隔夜漂移」，不是选票能力** ——
而杠杆可以免费加，不需要这个策略。地板（全池 244 只等权）是 +0.0515%/天、t6.83。

问题于是变成：**那个 β 能不能用便宜工具拿到？**

- 全池 244 只：1 手 × 224 元 = 2240 元/只 ⇒ 54 万，买不到
- 转债本身：佣金 0.04% 往返，直接吃掉地板的 78%
- **转债 ETF：佣金万分之一起步，比转债便宜 4 倍** ⇒ 唯一可能拿到 β 的工具

## 结论（2026-11-24）

    511380 可转债ETF博时   隔夜 +0.0356%/天 t2.45   日内 −0.0277%   全段 +0.0072% t0.28
    511180 上证可转债ETF   隔夜 +0.0314%/天 t2.32   日内 −0.0252%   全段 +0.0055% t0.28

「隔夜正、日内负、全段≈0」= 和个券**同一个微观结构**，所以不是「市场涨了」，
是真实的收盘压低/开盘回归。但幅度只有个券地板的 70%，且**分年剧烈衰减**：

    511380   2023 +0.0435 → 2024 +0.0552 → 2025 +0.0294 → 2026 +0.0170(t0.72)

往返佣金 0.02% ⇒ **2026 已经转负**。ETF 是能真的下单的东西，它归零就等于路到头了。

## 已知偏差

1. **折溢价测不出来**：日线只有成交价。收盘买 ETF 常常买在溢价上，实际比这里更差。
2. 511380 跟踪的是中证转债指数（规模加权），不等于全池等权 ⇒ 幅度天然更低。
3. 800 根上限（单次拉取），2023-08 起，覆盖不到 2021-2022。

用法：
    python research/cb_etf_probe.py
    python research/cb_etf_probe.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

ETFS = (("sh511380", "可转债ETF博时"), ("sh511180", "上证可转债ETF"))
BAD_MOVE = 0.12                            # ETF 日内 >12% 视为脏数据（转债指数不会）
COST_LEVELS = [(0.0002, "往返0.02%(万1)"), (0.0005, "往返0.05%(万2.5)")]


def fetch_etf(sym: str, n: int = 800) -> list:
    """返回 [(date, open, close)]，按时间升序。单次上限 800 根。"""
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq"
           % (sym, n))
    with urllib.request.urlopen(url, timeout=20) as r:
        d = json.load(r)["data"][sym]
    k = d.get("qfqday") or d.get("day") or []
    return [(row[0], float(row[1]), float(row[2])) for row in k]


def split_legs(rows: list):
    """拆成 (dates, 隔夜, 日内, 全段)，异常行三个一起置 nan。

    🔴 隔夜 = 今开/昨收−1，**不是**收→收。搞错这个，整份结论就是「市场涨了」。
    """
    dates = [r[0] for r in rows[1:]]
    op = np.asarray([r[1] for r in rows], dtype=float)
    cl = np.asarray([r[2] for r in rows], dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        over = op[1:] / cl[:-1] - 1.0
        intra = cl[1:] / op[1:] - 1.0
        full = cl[1:] / cl[:-1] - 1.0
    bad = (np.abs(over) > BAD_MOVE) | (np.abs(intra) > BAD_MOVE)
    for a in (over, intra, full):
        a[bad] = np.nan
    return dates, over, intra, full


def summarise(x: np.ndarray) -> dict:
    x = x[np.isfinite(x)]
    if x.size < 30:
        return {"n_days": int(x.size), "note": "样本<30"}
    sd = float(x.std(ddof=1))
    t = x.mean() / (sd / np.sqrt(x.size)) if sd > 0 else 0.0   # 常数序列 ⇒ 没有证据
    return {"n_days": int(x.size), "mean_pct": round(float(x.mean()) * 100, 4),
            "median_pct": round(float(np.median(x)) * 100, 4),
            "win_pct": round(float((x > 0).mean()) * 100, 1), "t": round(float(t), 2)}


def by_year(dates: list, x: np.ndarray, min_days: int = 40) -> dict:
    out = {}
    yrs = np.asarray([d[:4] for d in dates])
    for y in sorted(set(yrs)):
        a = x[(yrs == y) & np.isfinite(x)]
        if a.size < min_days:
            continue
        out[y] = summarise(a)
    return out


def net_after_cost(x: np.ndarray) -> dict:
    """扣往返佣金后的年化与 SR。**这才是判据**，不是扣成本前的 t 值。"""
    x = x[np.isfinite(x)]
    out = {}
    if x.size < 30:
        return out
    sd = float(x.std(ddof=1))
    for c, lab in COST_LEVELS:
        r = x - c
        sr = (r.mean() / r.std(ddof=1)) * np.sqrt(240) if r.std(ddof=1) > 0 else float("nan")
        out[lab] = {"cost_pct": round(c * 100, 3),
                    "mean_pct": round(float(r.mean()) * 100, 4),
                    "annual_pct": round(((1 + r.mean()) ** 240 - 1) * 100, 1),
                    "sharpe": round(float(sr), 2),
                    "t": round(float(r.mean() / (sd / np.sqrt(r.size))), 2)}
    return out


def probe_etf(rows: list) -> dict:
    dates, over, intra, full = split_legs(rows)
    res = {"span": "%s ~ %s" % (dates[0], dates[-1]) if dates else "",
           "n_days": len(dates)}
    for lab, a in (("over", over), ("intra", intra), ("full", full)):
        res[lab] = summarise(a)
    res["by_year"] = by_year(dates, over)
    res["net"] = net_after_cost(over)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--n", type=int, default=800)
    a = ap.parse_args(argv)

    out = {}
    print("=" * 78)
    print("转债 ETF 隔夜：收盘买 → 次日开盘卖（唯一能真的下单的口径）")
    print("=" * 78)
    for sym, name in ETFS:
        try:
            rows = fetch_etf(sym, a.n)
        except Exception as e:                      # noqa: BLE001 — 取数失败要说出来
            print("\n%s %s 取数失败：%s" % (sym, name, e))
            continue
        if len(rows) < 60:
            print("\n%s %s 只有 %d 根，跳过" % (sym, name, len(rows)))
            continue
        res = probe_etf(rows)
        out[sym] = dict(res, name=name)
        print("\n%s %s ｜ %s ｜ %d 个交易日" % (sym, name, res["span"], res["n_days"]))
        for lab, nm in (("over", "隔夜(可赚)"), ("intra", "日内(赚不到)"), ("full", "全段")):
            v = res[lab]
            if "mean_pct" not in v:
                print("  %-10s 样本不足" % nm)
                continue
            print("  %-10s %+.4f%%/天 t%+.2f 胜%4.1f%% (n=%d)"
                  % (nm, v["mean_pct"], v["t"], v["win_pct"], v["n_days"]))
        print("  分年（🔴 只报全样本 = 把正在变弱的漂移说成稳定的）：")
        for y, v in res["by_year"].items():
            print("    %s %+.4f%%/天 t%+.2f (n=%d)" % (y, v["mean_pct"], v["t"], v["n_days"]))
        print("  扣往返佣金后（🔴 这才是判据）：")
        for lab, v in res["net"].items():
            print("    %-16s 年化 %+6.1f%%  SR %5.2f  t%+.2f"
                  % (lab, v["annual_pct"], v["sharpe"], v["t"]))
    print()
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        print("写出 %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
