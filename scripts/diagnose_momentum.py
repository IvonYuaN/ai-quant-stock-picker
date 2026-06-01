#!/usr/bin/env python3
"""PR22.5 momentum 方向诊断。

把 MomentumStrategy.calculate_score 当黑盒，对沪深300 标的池逐日计算
(score_t, forward_return_5d_{t+1})，回答三个问题：
  1. Spearman ρ 方向自检
  2. 分位数对照（Q1-Q5）
  3. Regime 分层

输出 docs/momentum-direction-2026-05-28.md
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from aqsp.data.sina_source import SinaSource
from aqsp.strategies.momentum import MomentumStrategy
from aqsp.strategies.base import StrategyConfig

HS300_SAMPLE = [
    "600519", "300750", "000001", "000858", "601318",
    "002714", "600036", "000333", "601012", "600900",
    "600276", "000568", "002475", "601888", "000651",
    "600030", "601166", "000725", "002304", "601088",
    "600887", "000002", "600000", "601398", "600050",
    "000776", "002142", "601668", "600585", "002352",
    "601899", "600104", "601601", "000063", "002415",
    "600031", "601138", "600048", "000538", "601628",
    "002049", "600196", "601225", "000661", "601818",
    "002230", "600309", "601669", "000876", "601211",
    "600089", "002027", "601766", "600837", "000166",
    "002812", "601111", "600115", "601800", "000983",
    "601390", "600406", "002460", "601186", "600346",
    "000338", "601857", "600028", "601088", "000651",
    "601328", "002493", "600019", "601919", "600188",
    "002241", "600547", "601699", "600918", "000069",
]

FORWARD_DAYS = 5
STEP_DAYS = 20


def fetch_data(symbols: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    src = SinaSource()
    return src.fetch_daily(symbols, start, end)


def compute_rolling_scores(
    data: dict[str, pd.DataFrame], strategy: MomentumStrategy
) -> pd.DataFrame:
    lookback = strategy.thresholds.momentum.lookback_days
    rows = []

    for symbol, df in data.items():
        if df is None or df.empty or len(df) < lookback + FORWARD_DAYS + 10:
            continue
        df = df.sort_values("date").reset_index(drop=True)
        closes = df["close"].values
        volumes = df["volume"].values if "volume" in df.columns else np.ones(len(df))
        dates = df["date"].values

        for i in range(lookback, len(df) - FORWARD_DAYS, STEP_DAYS):
            if volumes[i] == 0:
                continue
            window = df.iloc[i - lookback : i + 1]
            score_map = strategy.calculate_score({symbol: window})
            score = score_map.get(symbol, 0.0)
            fwd_ret = (closes[i + FORWARD_DAYS] - closes[i]) / closes[i]
            rows.append({
                "symbol": symbol,
                "date": str(dates[i])[:10],
                "score": score,
                "forward_ret": fwd_ret,
                "close_idx": i,
            })

    return pd.DataFrame(rows)


def classify_regime_simple(df_slice: pd.DataFrame) -> str:
    if len(df_slice) < 5:
        return "unknown"
    rets = df_slice.groupby("symbol")["forward_ret"].mean()
    avg_ret = rets.mean()
    vol = rets.std()
    if vol > 0.08:
        if avg_ret > 0.02:
            return "volatile_bull"
        elif avg_ret < -0.02:
            return "volatile_bear"
        return "volatile_sideways"
    else:
        if avg_ret > 0.01:
            return "stable_bull"
        elif avg_ret < -0.01:
            return "stable_bear"
        return "stable_sideways"


def split_by_regime(scored: pd.DataFrame) -> dict[str, pd.DataFrame]:
    scored = scored.copy()
    scored["year_month"] = scored["date"].str[:7]
    regimes: dict[str, list[str]] = {}
    for ym, group in scored.groupby("year_month"):
        regime = classify_regime_simple(group)
        regimes.setdefault(regime, []).append(ym)
    result: dict[str, pd.DataFrame] = {}
    for regime, months in regimes.items():
        result[regime] = scored[scored["year_month"].isin(months)]
    return result


def quantile_table(scored: pd.DataFrame) -> pd.DataFrame:
    scored = scored.copy()
    scored["q"] = pd.qcut(scored["score"], 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"], duplicates="drop")
    return scored.groupby("q", observed=True)["forward_ret"].agg(["mean", "std", "count"]).reset_index()


def run_analysis(scored: pd.DataFrame, label: str) -> dict:
    if len(scored) < 10:
        return {"label": label, "n": len(scored), "error": "样本不足"}
    rho, pval = spearmanr(scored["score"], scored["forward_ret"])
    qt = quantile_table(scored)
    return {"label": label, "n": len(scored), "rho": rho, "pval": pval, "quantiles": qt}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2026-05-28")
    parser.add_argument("--output", default="docs/momentum-direction-2026-05-28.md")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or HS300_SAMPLE
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    print("=== PR22.5 Momentum 方向诊断 ===")
    print(f"标的数: {len(symbols)}")
    print(f"区间: {args.start} ~ {args.end}")
    print(f"forward: {FORWARD_DAYS}日, step: {STEP_DAYS}日")
    print()

    print("拉取数据...")
    data = fetch_data(symbols, start, end)
    valid = {s: df for s, df in data.items() if df is not None and len(df) > 80}
    print(f"有效标的: {len(valid)}/{len(symbols)}")

    strategy = MomentumStrategy(StrategyConfig(name="momentum"))
    print("计算滚动 scores...")
    scored = compute_rolling_scores(valid, strategy)
    print(f"有效样本点: {len(scored)}")
    print()

    overall = run_analysis(scored, "全量")
    regime_groups = split_by_regime(scored)
    regime_results = {}
    for regime_name in sorted(regime_groups.keys()):
        regime_results[regime_name] = run_analysis(
            regime_groups[regime_name], regime_name
        )

    conclusion = "C"
    if overall.get("rho") is not None and overall["rho"] < -0.05:
        conclusion = "A"
    elif overall.get("rho") is not None and abs(overall["rho"]) < 0.05:
        conclusion = "B"

    lines = []
    lines.append("# PR22.5 Momentum 方向诊断报告\n")
    lines.append(f"**运行日期**: {date.today().isoformat()}\n")
    lines.append(f"**运行命令**: `python3 scripts/diagnose_momentum.py --start {args.start} --end {args.end}`\n")
    lines.append("**exit code**: 0\n")
    lines.append(f"**标的数**: {len(valid)} 有效 / {len(symbols)} 总数\n")
    lines.append(f"**样本点**: {len(scored)}（{FORWARD_DAYS}日前瞻收益, 每 {STEP_DAYS} 日采样）\n")
    lines.append("")

    lines.append("## 1. Spearman ρ 方向自检\n")
    if "error" in overall:
        lines.append(f"⚠️ {overall['error']}\n")
    else:
        lines.append("| 指标 | 值 |\n|------|----|\n")
        lines.append(f"| Spearman ρ | **{overall['rho']:.4f}** |\n")
        lines.append(f"| p-value | {overall['pval']:.4g} |\n")
        lines.append(f"| 样本量 | {overall['n']} |\n")
        lines.append("")
        if overall["rho"] < -0.05:
            lines.append("❌ **ρ < -0.05 → signal 反向**：高分股反而跑输低分股，bug 实锤。\n")
        elif abs(overall["rho"]) < 0.05:
            lines.append("⚠️ **|ρ| < 0.05 → signal 无信息量**：不是反向，但 momentum score 对未来收益无预测力。\n")
        else:
            lines.append("✅ **ρ > 0.05 → signal 正向**：momentum 方向正确。\n")

    lines.append("## 2. 分位数对照（Q1-Q5）\n")
    if "error" not in overall:
        qt = overall["quantiles"]
        lines.append("| 分位 | mean_forward_5d | std | 样本数 |\n")
        lines.append("|------|-----------------|-----|--------|\n")
        for _, row in qt.iterrows():
            lines.append(f"| {row['q']} | {row['mean']:.4f} | {row['std']:.4f} | {int(row['count'])} |\n")
        lines.append("")
        if len(qt) >= 2:
            q5_mean = qt.iloc[-1]["mean"]
            q1_mean = qt.iloc[0]["mean"]
            if q5_mean < q1_mean:
                lines.append(f"❌ Q5 ({q5_mean:.4f}) < Q1 ({q1_mean:.4f}) → **反向确认**\n")
            elif abs(q5_mean - q1_mean) < 0.005:
                lines.append(f"⚠️ Q5 ({q5_mean:.4f}) ≈ Q1 ({q1_mean:.4f}) → **无效因子**\n")
            else:
                lines.append(f"✅ Q5 ({q5_mean:.4f}) > Q1 ({q1_mean:.4f}) → **正向**\n")

    lines.append("## 3. Regime 分层\n")
    for regime_name in sorted(regime_results.keys()):
        r = regime_results[regime_name]
        lines.append(f"### {regime_name}（{r['n']} 样本）\n")
        if "error" in r:
            lines.append(f"⚠️ {r['error']}\n")
        else:
            lines.append(f"- Spearman ρ = **{r['rho']:.4f}** (p={r['pval']:.4g})\n")
            qt = r["quantiles"]
            lines.append("| 分位 | mean_forward_5d | 样本数 |\n")
            lines.append("|------|-----------------|--------|\n")
            for _, row in qt.iterrows():
                lines.append(f"| {row['q']} | {row['mean']:.4f} | {int(row['count'])} |\n")
        lines.append("")

    lines.append("## 4. 结论\n")
    if conclusion == "A":
        lines.append("**分类：A — 方向反向 bug**\n")
        lines.append(f"Spearman ρ = {overall['rho']:.4f} < -0.05，高分股系统性跑输低分股。\n")
        lines.append("→ PR23 应检查 momentum.py 中 score 组件方向（RSI / return_score / trend_score）。\n")
    elif conclusion == "B":
        lines.append("**分类：B — 因子无信息**\n")
        lines.append(f"|ρ| = {abs(overall['rho']):.4f} < 0.05，momentum score 对未来收益无预测力。\n")
        lines.append("→ PR23 必须启用 quality/value 因子，单 momentum 不可用。\n")
    else:
        lines.append("**分类：C — 窗口期不利 / regime 分化**\n")
        lines.append(f"ρ = {overall['rho']:.4f}，需结合 regime 分层判断。\n")
        lines.append("→ PR23 加 regime gate，仅在有效 regime 下使用 momentum。\n")

    lines.append("")
    lines.append("---\n")
    lines.append("*仅供研究，不构成投资建议。*\n")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(lines), encoding="utf-8")
    print(f"报告已保存: {output_path}")

    print()
    print("=== 诊断摘要 ===")
    if "error" not in overall:
        print(f"Spearman ρ = {overall['rho']:.4f} (p={overall['pval']:.4g})")
    print(f"结论分类: {conclusion}")
    print(f"样本点: {len(scored)}")


if __name__ == "__main__":
    main()
