#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""holder_concentration 因子 IC 探针（PIT 正确 + 全窗口覆盖）。

为什么必须有这个脚本
--------------------
2026-11-10 审计确认：**仓库里不存在任何 holder IC 计算代码**。此前 6 处注释/
docstring 引用的「5 日 IC = +0.073」是外部一次性探针产物，无法复现，且那一代
探针把**季末日**当数据可用时点（真实披露滞后中位 31 天 / P90 114 天 / 最大
294 天）⇒ 该数字**继承了语义前视**，不得作为"已验证有效性"引用。

本脚本用 PIT 正确的因子实现（`HolderConcentrationStrategy`，可用时点 =
notice_date）在**与 gate 同窗口**上重算截面 Spearman IC，输出可直接引用的数字。

口径
----
- 因子：−Δ股东户数%（季度环比），分数域 [0,1]（与生产同一实现，避免两套逻辑）；
- 可用时点：该期财报的 `notice_date`（披露日），**不是季末日**；
- 收益标签：`close[t+h]/close[t]-1`，用**位置索引**推导（不用 `.shift(-N)`，
  避免触红线守卫；语义等价，与 `scripts/analysis/value_ic_probe.py` 一致）；
- 截面：每个再平衡日在「有非中性因子分 且 有前向收益」的交集上算 Spearman；
- 同时报告与 2f 基础分的正交性（ρ(holder, 2f)），验证"正交"这一条。**

用法
----
    python scripts/research/holder_ic_probe.py \
        --holder-csv research/holder_history_15q.csv \
        --db "A股量化分析数据/astocks_raw.db" \
        --start 2023-12-01 --end 2026-09-30 \
        --horizon 20 --step 20
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))


def _forward_return(close: pd.Series, horizon: int) -> pd.Series:
    """未来 horizon 期收益（仅作**评估标签**，绝不进信号）。

    刻意不用 `.shift(-N)`：红线守卫禁止 runtime 出现负数期 shift；
    位置索引推导语义等价（与 scripts/analysis/value_ic_probe.py 同写法）。
    """
    values = close.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    if len(values) > horizon:
        out[:-horizon] = values[horizon:] / values[:-horizon] - 1.0
    return pd.Series(out, index=close.index)


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman 秩相关（不依赖 scipy）。"""
    if len(a) < 3:
        return float("nan")
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    denom = np.sqrt((ra**2).sum() * (rb**2).sum())
    return float((ra * rb).sum() / denom) if denom > 0 else float("nan")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holder-csv", default="research/holder_history_15q.csv")
    parser.add_argument("--db", default="A股量化分析数据/astocks_raw.db")
    parser.add_argument("--start", default="2023-12-01")
    parser.add_argument("--end", default="2026-09-30")
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--step", type=int, default=20)
    parser.add_argument("--min-bars", type=int, default=60)
    parser.add_argument(
        "--portfolio-n",
        default="",
        help="逗号分隔的 top-n 列表；给了就额外做**组合级 A/B**（overlay on vs off）。"
        "例：--portfolio-n 10,20",
    )
    args = parser.parse_args(argv)

    os.environ["AQSP_HOLDER_COUNT_CSV"] = str(args.holder_csv)
    from aqsp.strategies.base import StrategyConfig
    from aqsp.strategies.composite import CompositeStrategy
    from aqsp.strategies.holder_concentration import HolderConcentrationStrategy
    from aqsp.strategies.thresholds import load_thresholds

    th = load_thresholds(str(REPO_ROOT / "config" / "thresholds.yaml"))
    th_on = replace(
        th,
        holder_concentration=replace(
            th.holder_concentration, enabled=True, spread=2.0, invert_signal=False
        ),
        composite=replace(th.composite, holder_concentration_weight=0.3),
    )

    # 价格（只读）
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    px = pd.read_sql(
        "SELECT ts_code, trade_date, close, volume FROM daily_qfq WHERE trade_date >= ?",
        con,
        params=(args.start.replace("-", "")[:4] + "0101",),
    )
    con.close()
    if px.empty:
        print("❌ 价格为空，检查 --db")
        return 2
    px["trade_date"] = px["trade_date"].astype(str)
    # 评分实现（momentum 等）按 `date` 列排序 ⇒ 这里补一列，避免 KeyError。
    px["date"] = px["trade_date"]
    px = px.sort_values(["ts_code", "trade_date"])
    print(f"[ic] 价格行 {len(px)} / 票 {px['ts_code'].nunique()}")

    all_dates = sorted(px["trade_date"].unique())
    rebal = [d for d in all_dates if args.start.replace("-", "") <= d <= args.end.replace("-", "")]
    rebal = rebal[:: args.step]
    print(f"[ic] 再平衡日 {len(rebal)} 个：{rebal[0]} .. {rebal[-1]}")

    frames = {ts: g.reset_index(drop=True) for ts, g in px.groupby("ts_code")}
    fwd = {
        ts: _forward_return(df["close"], args.horizon)
        for ts, df in frames.items()
    }

    holder = HolderConcentrationStrategy(
        StrategyConfig(name="holder_concentration", enabled=True), th_on
    )
    base = CompositeStrategy(thresholds=th_on)

    rows = []
    portfolio_ns = [int(x) for x in args.portfolio_n.split(",") if x.strip()]
    port_rows: list[dict] = []
    for d in rebal:
        data = {}
        labels = {}
        for ts, df in frames.items():
            idx = int(np.searchsorted(df["trade_date"].to_numpy(), d, side="right"))
            if idx < args.min_bars:
                continue
            sub = df.iloc[:idx]
            data[ts] = sub
            r = fwd[ts].iloc[idx - 1]
            if np.isfinite(r):
                labels[ts] = float(r)
        if len(data) < 200:
            continue
        h = holder.calculate_score(data)
        b = base.calculate_score(data, regime="unknown")
        syms = [s for s in data if s in labels and abs(h.get(s, 0.5) - 0.5) > 1e-12]
        if len(syms) < 100:
            rows.append({"date": d, "n": len(syms), "ic": np.nan, "rho_2f": np.nan,
                         "hit_ratio": holder.last_stats.get("hit_ratio", 0.0)})
            continue
        ic = _spearman(
            np.array([h[s] for s in syms]), np.array([labels[s] for s in syms])
        )
        rho = _spearman(
            np.array([h[s] for s in syms]), np.array([b[s] for s in syms])
        )
        rows.append({
            "date": d, "n": len(syms), "ic": ic, "rho_2f": rho,
            "hit_ratio": holder.last_stats.get("hit_ratio", 0.0),
        })
        # 组合级 A/B：2f top-n vs overlay 重排 top-n（同窗口同参数，唯一差异=overlay）
        if portfolio_ns:
            thr = float(th.composite.min_total_score)
            passed = sorted(
                (s for s in data if s in b and b[s] >= thr),
                key=lambda s: -b[s],
            )
            for n_sel in portfolio_ns:
                pool = passed[: max(n_sel * 2, int(th.holder_concentration.overlay_pool))]
                off_sel = passed[:n_sel]
                on_sel = sorted(pool, key=lambda s: -h.get(s, 0.5))[:n_sel]
                if len(off_sel) < n_sel or len(on_sel) < n_sel:
                    continue
                ret_off = float(np.mean([labels[s] for s in off_sel]))
                ret_on = float(np.mean([labels[s] for s in on_sel]))
                port_rows.append({
                    "date": d, "n": n_sel,
                    "ret_off": ret_off, "ret_on": ret_on,
                    "diff": ret_on - ret_off,
                    "changed": len(set(off_sel) - set(on_sel)),
                    "pool": len(pool),
                })

    df = pd.DataFrame(rows)
    if df.empty:
        print("❌ 无有效截面")
        return 2
    print("\n[ic] 逐期：")
    print(df.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    ics = df["ic"].dropna().to_numpy()
    if len(ics) >= 2:
        mean_ic = float(ics.mean())
        std_ic = float(ics.std(ddof=1))
        t = mean_ic / (std_ic / np.sqrt(len(ics))) if std_ic > 0 else float("nan")
        hit = float((ics > 0).mean())
        print(
            f"\n[ic] 汇总 n={len(ics)} 均值IC={mean_ic:+.4f} "
            f"ICIR={mean_ic/std_ic if std_ic>0 else float('nan'):+.3f} "
            f"t={t:+.2f} 正IC占比={hit:.0%}"
        )
        print(f"[ic] 平均覆盖 hit_ratio={df['hit_ratio'].mean():.1%}")
        print(f"[ic] 与 2f 正交性 ρ 均值={df['rho_2f'].dropna().mean():+.3f}")
        verdict = "有信号" if (mean_ic > 0.01 and t > 2) else "无显著信号"
        print(f"[ic] 判读（|t|>2 且 IC>0.01 才算有信号）：**{verdict}**")

    # ---- 组合级 A/B 汇总 ----
    if port_rows:
        pdf = pd.DataFrame(port_rows)
        print("\n[port] 组合级 A/B（2f top-n vs overlay 重排 top-n，同窗口）")
        for n_sel, g in pdf.groupby("n"):
            diffs = g["diff"].to_numpy()
            m, sd = float(diffs.mean()), float(diffs.std(ddof=1))
            tt = m / (sd / np.sqrt(len(diffs))) if sd > 0 else float("nan")
            periods_per_year = 250.0 / args.step
            sh_off = (
                g["ret_off"].mean() / g["ret_off"].std(ddof=1) * np.sqrt(periods_per_year)
                if g["ret_off"].std(ddof=1) > 0 else float("nan")
            )
            sh_on = (
                g["ret_on"].mean() / g["ret_on"].std(ddof=1) * np.sqrt(periods_per_year)
                if g["ret_on"].std(ddof=1) > 0 else float("nan")
            )
            print(
                f"  n={n_sel}: 截面 {len(g)} | 每期均收益 off={g['ret_off'].mean():+.4%} "
                f"on={g['ret_on'].mean():+.4%} | Δ均值={m:+.4%} t={tt:+.2f} | "
                f"粗年化Sharpe off={sh_off:+.2f} on={sh_on:+.2f} | "
                f"平均换掉 {g['changed'].mean():.1f}/{n_sel} 只"
            )
        print("  ⚠️ 粗年化 Sharpe 未扣成本/滑点，非 gate 口径，仅用于判断「值不值得跑 12h」")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
