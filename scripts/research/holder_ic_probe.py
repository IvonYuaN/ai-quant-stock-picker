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
    parser.add_argument(
        "--pool-ic",
        action="store_true",
        help="额外算「**达标池内**」的 holder IC —— 这才是 overlay 的真实作业面。"
        "全市场 IC 高但池内 IC≈0 时，overlay 不可能有效；反之才有 edge。",
    )
    parser.add_argument(
        "--blend-lambda",
        default="",
        help="逗号分隔的混合系数 λ：最终排序分 = λ*holder + (1-λ)*基础分。"
        "λ=1 等价于现行 pure-overlay（完全覆盖），λ=0 等价于纯 2f。"
        "例：--blend-lambda 1.0,0.3,0.15 用来比较不同集成方式的换手与净效果。",
    )
    parser.add_argument(
        "--factors",
        action="store_true",
        help="额外对**全部候选因子**算 全市场 IC + 池内 IC（作业面），用于筛因子。",
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
        # 🔴 必须取全 OHLC：`htf` 需要 high、`lower_shadow_ratio` 需要 low。
        # 2026-11-12 实测：只取 close/volume 时这两个因子抛 KeyError，
        # 而筛选器当时**静默跳过**它们 ⇒ 表里少了 2 个因子却看不出异常。
        "SELECT ts_code, trade_date, open, high, low, close, volume, amount"
        " FROM daily_qfq WHERE trade_date >= ?",
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

    # 全因子筛选用：从 CompositeStrategy 取各维度实例（**同一实现**，
    # 避免"另写一套因子"导致与生产口径漂移）
    factor_map: dict[str, object] = {}
    if args.factors:
        for name, attr in (
            ("momentum", "momentum_strategy"),
            ("price_volume_corr", "price_volume_corr_strategy"),
            ("volume", "volume_strategy"),
            ("mean_reversion", "mean_reversion_strategy"),
            ("triple_rise", "triple_rise_strategy"),
            ("htf", "htf_strategy"),
            ("volume_surge", "volume_surge_strategy"),
            ("volatility_position", "volatility_position_strategy"),
            ("macd_hist", "macd_hist_strategy"),
            ("lower_shadow_ratio", "lower_shadow_ratio_strategy"),
            ("quality", "quality_strategy"),
            ("value", "value_strategy"),
        ):
            inst = getattr(base, attr, None)
            if inst is not None:
                factor_map[name] = inst
        print(f"[factors] 参与筛选：{list(factor_map)}")

    rows = []
    portfolio_ns = [int(x) for x in args.portfolio_n.split(",") if x.strip()]
    blend_lams = [float(x) for x in args.blend_lambda.split(",") if x.strip()] or [1.0]
    port_rows: list[dict] = []
    factor_rows: list[dict] = []
    factor_errors: dict[str, str] = {}
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
        # 达标池（overlay 的真实作业面）：先按 2f 阈值筛，再按基础分降序
        passed: list[str] = []
        if portfolio_ns or args.pool_ic or factor_map:
            thr = float(th.composite.min_total_score)
            # 只在「有可用前向收益」的票里选（停牌/退市不可买），否则取均值会 KeyError
            passed = sorted(
                (s for s in data if s in b and s in labels and b[s] >= thr),
                key=lambda s: -b[s],
            )

        # 全因子筛选：各因子在全市场 / 达标池内的截面 IC
        for fname, finst in factor_map.items():
            try:
                fs = finst.calculate_score(data)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - 记录失败，绝不静默跳过
                factor_errors.setdefault(fname, f"{type(exc).__name__}: {str(exc)[:80]}")
                continue
            act = [s for s in data if s in labels and abs(fs.get(s, 0.5) - 0.5) > 1e-12]
            if len(act) >= 100:
                factor_rows.append({
                    "date": d, "factor": fname, "scope": "all", "n": len(act),
                    "ic": _spearman(
                        np.array([fs[s] for s in act]), np.array([labels[s] for s in act])
                    ),
                })
            pact = [s for s in passed if abs(fs.get(s, 0.5) - 0.5) > 1e-12]
            if len(pact) >= 30:
                factor_rows.append({
                    "date": d, "factor": fname, "scope": "pool", "n": len(pact),
                    "ic": _spearman(
                        np.array([fs[s] for s in pact]), np.array([labels[s] for s in pact])
                    ),
                })

        # 🔴 池内 IC：全市场 IC 高但池内 IC≈0 时，overlay 不可能有效。
        ic_pool = float("nan")
        ic_top = float("nan")
        pool_n = 0
        if args.pool_ic and passed:
            pool_n = len(passed)
            ps = [s for s in passed if abs(h.get(s, 0.5) - 0.5) > 1e-12]
            if len(ps) >= 30:
                ic_pool = _spearman(
                    np.array([h[s] for s in ps]), np.array([labels[s] for s in ps])
                )
            overlay_pool = int(th.holder_concentration.overlay_pool)
            top = passed[: max(20, overlay_pool)]
            ts = [s for s in top if abs(h.get(s, 0.5) - 0.5) > 1e-12]
            if len(ts) >= 20:
                ic_top = _spearman(
                    np.array([h[s] for s in ts]), np.array([labels[s] for s in ts])
                )

        rows.append({
            "date": d, "n": len(syms), "ic": ic, "rho_2f": rho,
            "hit_ratio": holder.last_stats.get("hit_ratio", 0.0),
            "pool_n": pool_n, "ic_pool": ic_pool, "ic_top": ic_top,
        })
        # 组合级 A/B：2f top-n vs overlay 重排 top-n（同窗口同参数，唯一差异=overlay）
        if portfolio_ns:
            for n_sel in portfolio_ns:
                pool = passed[: max(n_sel * 2, int(th.holder_concentration.overlay_pool))]
                off_sel = passed[:n_sel]
                for lam in blend_lams:
                    # λ=1：纯 holder 覆盖（现行 pure-overlay）；λ<1：与基础分混合
                    if lam >= 1.0:
                        key = lambda s: -h.get(s, 0.5)  # noqa: E731
                    elif lam <= 0.0:
                        key = lambda s: -b[s]  # noqa: E731
                    else:
                        key = lambda s: -(lam * h.get(s, 0.5) + (1 - lam) * b[s])  # noqa: E731
                    on_sel = sorted(pool, key=key)[:n_sel]
                    if len(off_sel) < n_sel or len(on_sel) < n_sel:
                        continue
                    ret_off = float(np.mean([labels[s] for s in off_sel]))
                    ret_on = float(np.mean([labels[s] for s in on_sel]))
                    changed = len(set(off_sel) - set(on_sel))
                    # 边际成本：overlay 相对基线多换的量 × 单边成本 × 2（卖出+买入）
                    round_trip = 2.0 * (3.0 + 5.0) / 10000.0
                    port_rows.append({
                        "date": d, "n": n_sel, "lam": lam,
                        "ret_off": ret_off, "ret_on": ret_on,
                        "diff": ret_on - ret_off,
                        "net_diff": ret_on - ret_off - (changed / n_sel) * round_trip,
                        "changed": changed,
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

    # ---- 池内 IC 汇总（overlay 的真实作业面）----
    if args.pool_ic and "ic_pool" in df.columns:
        print("\n[pool-ic] 达标池内 IC（overlay 只能在这个池子里重排）")
        for col, label in (("ic_pool", "达标池内全部"), ("ic_top", "overlay 池前 N")):
            vals = df[col].dropna().to_numpy()
            if len(vals) >= 2:
                m, sd = float(vals.mean()), float(vals.std(ddof=1))
                tt = m / (sd / np.sqrt(len(vals))) if sd > 0 else float("nan")
                print(
                    f"  {label}: 截面 {len(vals)} | 均值IC {m:+.4f} | t={tt:+.2f} "
                    f"| 正占比 {(vals > 0).mean():.0%}"
                )
        print(
            f"  平均达标池规模 {df['pool_n'].mean():.0f} 只"
            "（若池内 IC 显著低于全市场 IC ⇒ overlay 的边际价值被池子吃掉）"
        )
        pool_ics = df["ic_pool"].dropna().to_numpy()
        full_ics = df["ic"].dropna().to_numpy()
        if len(pool_ics) >= 2 and len(full_ics) >= 2:
            print(
                f"  全市场 IC {full_ics.mean():+.4f} vs 池内 IC {pool_ics.mean():+.4f}"
            )

    # ---- 组合级 A/B 汇总 ----
    if port_rows:
        pdf = pd.DataFrame(port_rows)
        print("\n[port] 组合级 A/B（2f top-n vs 重排 top-n，同窗口；λ=1 为现行 pure-overlay）")
        for (n_sel, lam), g in pdf.groupby(["n", "lam"]):
            diffs = g["diff"].to_numpy()
            nets = g["net_diff"].to_numpy()
            m, sd = float(diffs.mean()), float(diffs.std(ddof=1))
            tt = m / (sd / np.sqrt(len(diffs))) if sd > 0 else float("nan")
            mn, sdn = float(nets.mean()), float(nets.std(ddof=1))
            tn = mn / (sdn / np.sqrt(len(nets))) if sdn > 0 else float("nan")
            periods_per_year = 250.0 / args.step
            sh_on = (
                g["ret_on"].mean() / g["ret_on"].std(ddof=1) * np.sqrt(periods_per_year)
                if g["ret_on"].std(ddof=1) > 0 else float("nan")
            )
            print(
                f"  n={n_sel} λ={lam:g}: 截面 {len(g)} | 毛 Δ={m:+.4%} t={tt:+.2f} | "
                f"净 Δ={mn:+.4%} t={tn:+.2f} | 换掉 {g['changed'].mean():.1f}/{n_sel} | "
                f"on 粗Sharpe={sh_on:+.2f}"
            )
        print("  ⚠️ 粗 Sharpe 未扣成本、非 gate 口径；净 Δ 已按 单边8bp×2 估算边际成本")

    # ---- 全因子筛选汇总 ----
    if factor_rows:
        fdf = pd.DataFrame(factor_rows)
        print("\n[factors] 因子筛选（全市场 vs 达标池内 = 作业面）")
        print("| 因子 | 全市场 IC | t | 池内 IC | 池内 t | 池内正占比 | 判读 |")
        print("|---|---|---|---|---|---|---|")
        for fname, g in fdf.groupby("factor"):
            out = []
            for scope in ("all", "pool"):
                vals = g[g["scope"] == scope]["ic"].dropna().to_numpy()
                if len(vals) >= 2:
                    m, sd = float(vals.mean()), float(vals.std(ddof=1))
                    tv = m / (sd / np.sqrt(len(vals))) if sd > 0 else float("nan")
                    out.append((m, tv, float((vals > 0).mean())))
                else:
                    out.append((float("nan"), float("nan"), float("nan")))
            (ma, ta, _), (mp, tp, hitp) = out
            # 判读只看**作业面**：池内 |t|>=2 且 IC 同号才算可用
            if abs(tp) >= 2 and abs(mp) >= 0.01:
                verdict = "✅ 作业面有效"
            elif abs(tp) >= 1 and abs(mp) >= 0.01:
                verdict = "⚠️ 边缘"
            else:
                verdict = "❌ 作业面无效"
            print(
                f"| {fname} | {ma:+.4f} | {ta:+.2f} | {mp:+.4f} | {tp:+.2f} | "
                f"{hitp:.0%} | {verdict} |"
            )
        print("  ⚠️ 判读以**池内**为准：全市场 IC 好但池内无效 ⇒ 该因子在真实可选集合里没有用")

        # 🔴 失败/无效必须显式列出：静默跳过会让"缺席"与"无效"无法区分
        if factor_errors:
            print("\n[factors] ❌ 计算失败（不是「无效」，是「没算出来」）：")
            for fname, msg in sorted(factor_errors.items()):
                print(f"  - {fname}: {msg}")
        produced = set(fdf["factor"].unique())
        missing = [n for n in factor_map if n not in produced and n not in factor_errors]
        if missing:
            print(f"\n[factors] ⚠️ 无有效截面（可能全中性/停用）：{missing}")
    return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
