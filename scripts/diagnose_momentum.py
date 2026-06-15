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
import argparse
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from aqsp.data.sina_source import SinaSource
from aqsp.core.time import today_shanghai
from aqsp.strategies.momentum import MomentumStrategy
from aqsp.strategies.base import StrategyConfig

# fmt: off
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
# fmt: on

FORWARD_DAYS = 5
STEP_DAYS = 20
SCORED_COLUMNS = ["symbol", "date", "score", "forward_ret", "close_idx"]


def fetch_data(
    symbols: list[str], start: date, end: date, source: str = "sina"
) -> dict[str, pd.DataFrame]:
    if source != "sina":
        raise ValueError(f"unsupported source: {source}")
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
            rows.append(
                {
                    "symbol": symbol,
                    "date": str(dates[i])[:10],
                    "score": score,
                    "forward_ret": fwd_ret,
                    "close_idx": i,
                }
            )

    return pd.DataFrame(rows, columns=SCORED_COLUMNS)


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
    if scored.empty or "date" not in scored.columns:
        return {}
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
    if (
        scored.empty
        or "score" not in scored.columns
        or "forward_ret" not in scored.columns
    ):
        return pd.DataFrame(columns=["q", "mean", "std", "count"])
    scored = scored.copy()
    scored = scored.replace([np.inf, -np.inf], np.nan).dropna(
        subset=["score", "forward_ret"]
    )
    if scored.empty:
        return pd.DataFrame(columns=["q", "mean", "std", "count"])
    quantiles = pd.qcut(scored["score"], 5, duplicates="drop")
    if quantiles.isna().all():
        return pd.DataFrame(columns=["q", "mean", "std", "count"])
    categories = list(quantiles.cat.categories)
    labels = {category: f"Q{idx + 1}" for idx, category in enumerate(categories)}
    scored["q"] = quantiles.map(labels).astype(str)
    return (
        scored.groupby("q", observed=True)["forward_ret"]
        .agg(["mean", "std", "count"])
        .reset_index()
    )


def run_analysis(scored: pd.DataFrame, label: str) -> dict:
    if "score" not in scored.columns or "forward_ret" not in scored.columns:
        return {"label": label, "n": len(scored), "error": "schema missing"}
    scored = scored.replace([np.inf, -np.inf], np.nan).dropna(
        subset=["score", "forward_ret"]
    )
    if len(scored) < 10:
        return {"label": label, "n": len(scored), "error": "样本不足"}
    rho, pval = spearmanr(scored["score"], scored["forward_ret"])
    qt = quantile_table(scored)
    return {"label": label, "n": len(scored), "rho": rho, "pval": pval, "quantiles": qt}


def classify_conclusion(overall: dict) -> str:
    rho = overall.get("rho")
    if overall.get("error") == "schema missing":
        return "INVALID_SCHEMA"
    if "error" in overall or rho is None:
        return "INSUFFICIENT"
    if not np.isfinite(rho):
        return "B"
    if rho < -0.05:
        return "A"
    if abs(rho) <= 0.05:
        return "B"
    return "C"


def conclusion_lines(conclusion: str, rho: float | None) -> list[str]:
    if conclusion == "INVALID_SCHEMA":
        return [
            "**分类：输入结构错误**\n",
            "诊断数据缺少 `score` 或 `forward_ret` 列，无法判断 momentum 方向。\n",
            "→ 先检查滚动打分输出 schema，再重跑方向诊断。\n",
        ]
    if conclusion == "INSUFFICIENT" or rho is None:
        return [
            "**分类：样本不足**\n",
            "有效样本不足 10 个，本次诊断只作为数据覆盖检查，不判断 momentum 方向。\n",
            "→ 先补齐数据窗口或扩大标的池，再重跑方向诊断。\n",
        ]
    if conclusion == "A":
        return [
            "**分类：A — 方向回归风险**\n",
            f"Spearman ρ = {rho:.4f} < -0.05，高分股系统性跑输低分股。\n",
            "→ momentum.py 的 RSI 正向和 return_score 下限已有防回退测试；若再次出现 A 类结果，先查数据窗口、regime 与阈值版本。\n",
        ]
    if conclusion == "B":
        if not np.isfinite(rho):
            return [
                "**分类：B — 因子无信息**\n",
                "Spearman ρ 无法计算，通常说明 score 或 forward return 为常量。\n",
                "→ 单 momentum 不可直接上线；需先复核数据覆盖、分数分布和组合因子增量。\n",
            ]
        return [
            "**分类：B — 因子无信息**\n",
            f"|ρ| = {abs(rho):.4f} <= 0.05，momentum score 对未来收益无预测力。\n",
            "→ 单 momentum 不可直接上线；需用 walk-forward 复核 quality/value 或组合因子增量。\n",
        ]
    return [
        "**分类：C — 窗口期不利 / regime 分化**\n",
        f"ρ = {rho:.4f}，需结合 regime 分层判断。\n",
        "→ 继续使用 regime gate，仅在有效 regime 下解释 momentum 结果。\n",
    ]


def spearman_status_line(rho: float) -> str:
    if not np.isfinite(rho):
        return "⚠️ **Spearman ρ 无法计算 → signal 无信息量**：score 或 forward return 可能为常量。\n"
    if rho < -0.05:
        return "❌ **ρ < -0.05 → signal 反向**：高分股反而跑输低分股，bug 实锤。\n"
    if abs(rho) <= 0.05:
        return "⚠️ **|ρ| <= 0.05 → signal 无信息量**：不是反向，但 momentum score 对未来收益无预测力。\n"
    return "✅ **ρ > 0.05 → signal 正向**：momentum 方向正确。\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="")
    parser.add_argument("--source", default="sina", choices=["sina"])
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2026-05-28")
    parser.add_argument("--output", default="docs/momentum-direction-2026-05-28.md")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or HS300_SAMPLE
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    print("=== PR22.5 Momentum 方向诊断 ===")
    print(f"数据源: {args.source}")
    print(f"标的数: {len(symbols)}")
    print(f"区间: {args.start} ~ {args.end}")
    print(f"forward: {FORWARD_DAYS}日, step: {STEP_DAYS}日")
    print()

    print("拉取数据...")
    data = fetch_data(symbols, start, end, args.source)
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

    conclusion = classify_conclusion(overall)

    lines = []
    lines.append("# PR22.5 Momentum 方向诊断报告\n")
    lines.append(f"**运行日期**: {today_shanghai().isoformat()}\n")
    lines.append(
        f"**运行命令**: `python3 scripts/diagnose_momentum.py --source {args.source} --start {args.start} --end {args.end}`\n"
    )
    lines.append("**exit code**: 0\n")
    lines.append(f"**标的数**: {len(valid)} 有效 / {len(symbols)} 总数\n")
    lines.append(
        f"**样本点**: {len(scored)}（{FORWARD_DAYS}日前瞻收益, 每 {STEP_DAYS} 日采样）\n"
    )
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
        lines.append(spearman_status_line(float(overall["rho"])))

    lines.append("## 2. 分位数对照（Q1-Q5）\n")
    if "error" not in overall:
        qt = overall["quantiles"]
        lines.append("| 分位 | mean_forward_5d | std | 样本数 |\n")
        lines.append("|------|-----------------|-----|--------|\n")
        for _, row in qt.iterrows():
            lines.append(
                f"| {row['q']} | {row['mean']:.4f} | {row['std']:.4f} | {int(row['count'])} |\n"
            )
        lines.append("")
        if len(qt) >= 2:
            q5_mean = qt.iloc[-1]["mean"]
            q1_mean = qt.iloc[0]["mean"]
            if q5_mean < q1_mean:
                lines.append(
                    f"❌ Q5 ({q5_mean:.4f}) < Q1 ({q1_mean:.4f}) → **反向确认**\n"
                )
            elif abs(q5_mean - q1_mean) < 0.005:
                lines.append(
                    f"⚠️ Q5 ({q5_mean:.4f}) ≈ Q1 ({q1_mean:.4f}) → **无效因子**\n"
                )
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
                lines.append(
                    f"| {row['q']} | {row['mean']:.4f} | {int(row['count'])} |\n"
                )
        lines.append("")

    lines.append("## 4. 结论\n")
    rho = float(overall["rho"]) if "rho" in overall else None
    lines.extend(conclusion_lines(conclusion, rho))

    lines.append("")
    lines.append("---\n")
    lines.append("*仅供研究，不构成交易指令或投资建议。*\n")

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
