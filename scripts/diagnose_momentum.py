#!/usr/bin/env python3
"""诊断 momentum 策略的 score 分布，定位 trades=0 根因。

用法:
    python3 scripts/diagnose_momentum.py [--source sina|akshare] [--symbols 600519,300750,000001] [--date 2025-11-28]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import pandas as pd

from aqsp.strategies.momentum import MomentumStrategy
from aqsp.strategies.base import StrategyConfig


def fetch_data_sina(symbols: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    from datetime import date as dt_date
    from aqsp.data.sina_source import SinaSource
    src = SinaSource()
    return src.fetch_daily(symbols, dt_date.fromisoformat(start), dt_date.fromisoformat(end))


def fetch_data_akshare(symbols: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    from datetime import date as dt_date
    from aqsp.data.akshare_source import AkshareSource
    src = AkshareSource()
    return src.fetch_daily(symbols, dt_date.fromisoformat(start), dt_date.fromisoformat(end))


def diagnose_single_stock(df: pd.DataFrame, strategy: MomentumStrategy, symbol: str) -> dict:
    if df is None or df.empty or len(df) < 10:
        return {"symbol": symbol, "error": "数据不足"}

    thresholds = strategy.thresholds
    df = df.sort_values("date").tail(thresholds.momentum.lookback_days)
    prices = df["close"].values
    returns = np.diff(prices) / prices[:-1]

    total_return = (prices[-1] - prices[0]) / prices[0]
    volatility = np.std(returns) * np.sqrt(252)

    min_returns = thresholds.momentum.min_returns
    max_volatility = thresholds.momentum.max_volatility
    return_score = min(total_return / min_returns, 1.0) if min_returns > 0 else 0.5
    vol_score = max(1 - volatility / max_volatility, 0.0) if max_volatility > 0 else 0.5
    momentum_score = (return_score + vol_score) / 2

    ma_period = thresholds.momentum.ma_period
    trend_threshold = thresholds.momentum.trend_strength_threshold
    if len(df) >= ma_period:
        df_copy = df.copy()
        df_copy["ma"] = df_copy["close"].rolling(ma_period).mean()
        df_copy["trend"] = (df_copy["close"] - df_copy["ma"]) / df_copy["ma"]
        recent_trend = df_copy["trend"].tail(5).mean()
        trend_score = min(recent_trend / trend_threshold, 1.0) if trend_threshold > 0 else 0.5
    else:
        trend_score = 0.5

    rsi = strategy._calculate_rsi(df["close"])
    rsi_score = strategy._calculate_rsi_score(df)

    w = thresholds.momentum.weights
    raw_score = momentum_score * w.momentum + trend_score * w.trend + rsi_score * w.rsi
    final_score = max(0.0, min(1.0, raw_score))

    return {
        "symbol": symbol,
        "last_close": float(prices[-1]),
        "total_return": f"{total_return:.4f}",
        "volatility": f"{volatility:.4f}",
        "return_score": f"{return_score:.4f}",
        "vol_score": f"{vol_score:.4f}",
        "momentum_score": f"{momentum_score:.4f}",
        "trend_score": f"{trend_score:.4f}",
        "rsi": f"{rsi:.2f}" if rsi is not None else "N/A",
        "rsi_score": f"{rsi_score:.4f}",
        "raw_score": f"{raw_score:.4f}",
        "final_score": f"{final_score:.4f}",
        "weights": f"m={w.momentum} t={w.trend} r={w.rsi}",
    }


def main():
    parser = argparse.ArgumentParser(description="诊断 momentum 策略 score 分布")
    parser.add_argument("--source", default="sina", choices=["sina", "akshare"])
    parser.add_argument("--symbols", default="600519,300750,000001,000858,601318,002714,600036,000333,601012,600900")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2025-11-28")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    print(f"数据源: {args.source}")
    print(f"标的: {symbols}")
    print(f"区间: {args.start} ~ {args.end}")
    print()

    if args.source == "sina":
        data = fetch_data_sina(symbols, args.start, args.end)
    else:
        data = fetch_data_akshare(symbols, args.start, args.end)

    strategy = MomentumStrategy(StrategyConfig(name="momentum"))
    thresholds = strategy.thresholds

    print(f"参数: lookback={thresholds.momentum.lookback_days}, "
          f"min_returns={thresholds.momentum.min_returns}, "
          f"max_volatility={thresholds.momentum.max_volatility}")
    print(f"RSI: overbought={thresholds.momentum.rsi_overbought}, "
          f"oversold={thresholds.momentum.rsi_oversold}")
    print(f"权重: momentum={thresholds.momentum.weights.momentum}, "
          f"trend={thresholds.momentum.weights.trend}, "
          f"rsi={thresholds.momentum.weights.rsi}")
    print("=" * 120)

    results = []
    for symbol in symbols:
        df = data.get(symbol)
        result = diagnose_single_stock(df, strategy, symbol)
        results.append(result)

    print(f"{'symbol':<10} {'close':>10} {'return':>8} {'vol':>8} "
          f"{'ret_sc':>8} {'vol_sc':>8} {'mom_sc':>8} {'trend_sc':>8} "
          f"{'RSI':>8} {'rsi_sc':>8} {'raw':>8} {'final':>8}")
    print("-" * 120)

    for r in results:
        if "error" in r:
            print(f"{r['symbol']:<10} {r['error']}")
            continue
        print(f"{r['symbol']:<10} {r['last_close']:>10.2f} {r['total_return']:>8} {r['volatility']:>8} "
              f"{r['return_score']:>8} {r['vol_score']:>8} {r['momentum_score']:>8} {r['trend_score']:>8} "
              f"{r['rsi']:>8} {r['rsi_score']:>8} {r['raw_score']:>8} {r['final_score']:>8}")

    print()
    print("=== 诊断结论 ===")
    print()

    scores = [float(r["final_score"]) for r in results if "final_score" in r]
    above_threshold = [s for s in scores if s >= thresholds.composite.min_total_score]

    print(f"score 分布: min={min(scores):.4f} max={max(scores):.4f} "
          f"mean={np.mean(scores):.4f} median={np.median(scores):.4f}")
    print(f"min_total_score 阈值: {thresholds.composite.min_total_score}")
    print(f"过阈值标的数: {len(above_threshold)}/{len(scores)}")
    print()

    rsi_scores = [float(r["rsi_score"]) for r in results if "rsi_score" in r]
    mom_scores = [float(r["momentum_score"]) for r in results if "momentum_score" in r]
    print(f"RSI score 分布: min={min(rsi_scores):.4f} max={max(rsi_scores):.4f} mean={np.mean(rsi_scores):.4f}")
    print(f"momentum score 分布: min={min(mom_scores):.4f} max={max(mom_scores):.4f} mean={np.mean(mom_scores):.4f}")

    print()
    print("RSI 与 momentum 的相关性分析:")
    for r in results:
        if "rsi_score" not in r:
            continue
        rsi_s = float(r["rsi_score"])
        mom_s = float(r["momentum_score"])
        final_s = float(r["final_score"])
        direction = "同向" if (rsi_s > 0.5 and mom_s > 0.5) or (rsi_s < 0.5 and mom_s < 0.5) else "反向"
        print(f"  {r['symbol']}: RSI={rsi_s:.4f} momentum={mom_s:.4f} → {direction} "
              f"(RSI {'拉高' if rsi_s > 0.5 else '拉低'} final {final_s:.4f})")


if __name__ == "__main__":
    main()
