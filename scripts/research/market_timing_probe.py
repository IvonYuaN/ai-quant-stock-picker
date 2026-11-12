#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小择时检验：市场层面动量能否预测下一段收益？

为什么做这个
------------
2026-11-12 结论：现有 11 个因子 + 三类新数据源，在**作业面（2f 达标池内）
无一 |t|≥2** ⇒ 继续在"截面选股"上加因子，投入产出比极低。
于是换问题：**能否在时间维度（择时/仓位）上找到信号？**

数据说明（重要）
----------------
库里的指数序列太短（`000300.SH` 仅 126 行、自 2026-05 起）⇒ 不可用。
故用**全市场等权日收益**自建市场序列：
``mkt_ret[t] = 当日所有票 close/prev_close-1 的中位数``（中位数抗极值）。

检验内容
--------
1. **自相关**：滚动窗口收益 ``R[-L:]`` 与未来 ``R[0:L]`` 的截面（时间维）相关；
2. **简单择时规则**：若过去 L 日市场收益 > 0 则持有、否则空仓，
   与买入持有比较年化收益 / Sharpe / 最大回撤；
3. 多个 L（10/20/60）都看，避免只挑一个好看的窗口。

⚠️ 这是**研究性检验**：中位数收益不含成本，且空仓期假设 0 收益。
结论只能说明"值不值得深入研究择时"，不能直接当交易规则。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import numpy as np
import pandas as pd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--start", default="2023-11-01")
    ap.add_argument("--end", default="2026-11-12")
    ap.add_argument("--sample", type=int, default=800, help="用于构造市场序列的票数（0=全部）")
    args = ap.parse_args(argv)

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    # 先挑票（避免全表 1400 万行的宽表 pivot 爆内存）
    if args.sample:
        codes = [
            r[0]
            for r in con.execute(
                "SELECT ts_code FROM daily_qfq WHERE trade_date >= ? "
                "GROUP BY ts_code HAVING COUNT(*) > 200 ORDER BY ts_code LIMIT ?",
                (args.start.replace("-", ""), args.sample),
            )
        ]
    else:
        codes = [
            r[0]
            for r in con.execute(
                "SELECT ts_code FROM daily_qfq WHERE trade_date >= ? "
                "GROUP BY ts_code HAVING COUNT(*) > 200",
                (args.start.replace("-", ""),),
            )
        ]
    marks = ",".join("?" * len(codes))
    px = pd.read_sql(
        f"SELECT ts_code, trade_date, close FROM daily_qfq "
        f"WHERE ts_code IN ({marks}) AND trade_date >= ?",
        con,
        params=(*codes, args.start.replace("-", "")),
    )
    con.close()

    wide = px.pivot_table(index="trade_date", columns="ts_code", values="close")
    wide = wide.sort_index()
    rets = wide.pct_change()
    mkt = rets.median(axis=1).dropna()
    mkt = mkt[mkt.index <= args.end.replace("-", "")]
    print(f"[mkt] 票 {len(codes)}｜交易日 {len(mkt)}｜{mkt.index.min()} ~ {mkt.index.max()}")
    print(f"[mkt] 日均收益 {mkt.mean():+.4%}｜年化波动 {mkt.std()*np.sqrt(250):.2%}")

    print("\n| 窗口L | 动量→未来相关 | t | 持有年化 | 动量规则年化 | 反向规则年化 | 持有Sharpe | 动量Sharpe | 反向Sharpe | 持有MDD | 动量MDD | 反向MDD |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    arr = mkt.to_numpy()
    n = len(arr)
    for L in (10, 20, 60):
        if n < 4 * L:
            continue
        past = np.array([arr[i - L:i].sum() for i in range(L, n - L + 1)])
        fut = np.array([arr[i:i + L].sum() for i in range(L, n - L + 1)])
        if len(past) < 30:
            continue
        rho = float(np.corrcoef(past, fut)[0, 1])
        t = rho * np.sqrt(len(past) - 2) / np.sqrt(max(1e-12, 1 - rho**2))

        # 两种规则都算：动量（past>0 持有）与反向（past<0 持有）
        pos_ret, bh_ret, rev_ret = [], [], []
        for i in range(L, n - L + 1, L):
            seg = arr[i:i + L].sum()
            past_sum = arr[i - L:i].sum()
            bh_ret.append(seg)
            pos_ret.append(seg if past_sum > 0 else 0.0)
            rev_ret.append(seg if past_sum < 0 else 0.0)
        pos_ret, bh_ret, rev_ret = np.array(pos_ret), np.array(bh_ret), np.array(rev_ret)
        ppy = 250.0 / L

        def _ann(x: np.ndarray) -> float:
            return float((1 + x).prod() ** (ppy / len(x)) - 1) if len(x) else float("nan")

        def _sh(x: np.ndarray) -> float:
            return float(x.mean() / x.std(ddof=1) * np.sqrt(ppy)) if x.std(ddof=1) > 0 else float("nan")

        def _mdd(x: np.ndarray) -> float:
            eq = np.cumprod(1 + x)
            peak = np.maximum.accumulate(eq)
            return float((eq / peak - 1).min())

        print(
            f"| {L} | {rho:+.3f} | {t:+.2f} | {_ann(bh_ret):+.2%} | {_ann(pos_ret):+.2%} | "
            f"{_ann(rev_ret):+.2%} | {_sh(bh_ret):+.2f} | {_sh(pos_ret):+.2f} | {_sh(rev_ret):+.2f} | "
            f"{_mdd(bh_ret):+.1%} | {_mdd(pos_ret):+.1%} | {_mdd(rev_ret):+.1%} |"
        )

    print("\n⚠️ 中位数收益、不含成本、空仓按 0 收益；仅用于判断「是否值得研究择时」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
