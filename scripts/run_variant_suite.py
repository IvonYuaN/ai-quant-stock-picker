#!/usr/bin/env python3
"""Run isolated short-term variants against raw historical OHLCV data.

The script consumes only historical workload data and writes an experiment
artifact. It never changes formal candidates, ledgers, or broker state.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from aqsp.backtest.variant_account import (
    VariantOrder,
    VariantExecutionRules,
    simulate_variant,
    variant_result_to_dict,
)
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import atomic_write_text


@dataclass(frozen=True)
class VariantProfile:
    variant_id: str
    label: str
    lookback: int
    entry_return_pct: float
    max_bias_pct: float
    mode: str = "trend"
    hypothesis: str = "价格趋势延续"


PROFILES = (
    VariantProfile("trend_follow", "趋势跟随", 20, 2.0, 12.0),
    VariantProfile("pullback", "趋势回踩", 20, 0.0, 4.0),
    VariantProfile("breakout_continuation", "突破延续", 10, 4.0, 15.0, "breakout"),
    VariantProfile("defensive_momentum", "防守动量", 10, 1.0, 8.0),
    VariantProfile("mean_reversion", "均值回归", 20, 3.0, 0.0, "reversion"),
    VariantProfile("low_volatility", "低波动趋势", 30, 1.0, 6.0, "low_vol"),
    VariantProfile("relative_strength", "相对强势", 15, 3.0, 10.0, "relative_strength", "强势股相对强度延续"),
    VariantProfile("volume_breakout", "量价突破", 20, 2.0, 15.0, "volume_breakout", "成交量确认突破比单看价格更可靠"),
    VariantProfile("atr_trend", "ATR趋势", 20, 1.5, 12.0, "atr_trend", "用波动率调整趋势入场，避免追逐异常波动"),
    VariantProfile("defensive_range", "防守区间", 20, 0.0, 5.0, "defensive_range", "低波动区间承接优先于高波动追涨"),
)


def _training_volatility_pct(frames: dict[str, pd.DataFrame]) -> float:
    """Estimate volatility from the first 60 bars only; never use evaluation data."""
    values: list[float] = []
    for frame in frames.values():
        closes = pd.to_numeric(frame["close"], errors="coerce").dropna().head(60)
        if len(closes) > 1:
            values.extend((closes.pct_change().dropna().abs() * 100.0).tolist())
    return float(pd.Series(values).median()) if values else 0.0


def generate_variant_profiles(
    frames: dict[str, pd.DataFrame],
) -> tuple[VariantProfile, ...]:
    """Add deterministic mutations based on a point-in-time training window."""
    volatility = _training_volatility_pct(frames)
    if volatility >= 2.5:
        mutations = (
            VariantProfile("auto_high_vol_defensive", "自动变体·高波防守", 15, 2.0, 5.0, "low_vol"),
            VariantProfile("auto_high_vol_reversal", "自动变体·高波反转", 15, 4.0, 0.0, "reversion"),
            VariantProfile("auto_high_vol_trend", "自动变体·高波趋势", 25, 3.0, 7.0),
            VariantProfile("auto_high_vol_breakout", "自动变体·高波突破", 8, 5.0, 18.0, "breakout"),
        )
    else:
        mutations = (
            VariantProfile("auto_low_vol_breakout", "自动变体·低波突破", 15, 3.0, 10.0, "breakout"),
            VariantProfile("auto_low_vol_pullback", "自动变体·低波回踩", 25, 0.0, 3.0),
            VariantProfile("auto_low_vol_defensive", "自动变体·低波防守", 35, 0.5, 4.0, "low_vol"),
            VariantProfile("auto_low_vol_reversal", "自动变体·低波反转", 30, 2.0, 0.0, "reversion"),
        )
    return (*PROFILES, *mutations)


def load_frames(
    db_path: Path,
    symbols: tuple[str, ...],
    start: str,
    end: str,
) -> dict[str, pd.DataFrame]:
    placeholders = ",".join("?" for _ in symbols)
    with sqlite3.connect(db_path) as conn:
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(ohlcv)")
        }
    workload_filter = " AND workload = 'historical'" if "workload" in columns else ""
    query = f"""
        SELECT symbol, date, open, high, low, close, volume, amount,
               suspended, limit_up, limit_down
        FROM ohlcv
        WHERE price_mode = 'raw'{workload_filter}
          AND symbol IN ({placeholders}) AND date BETWEEN ? AND ?
        ORDER BY symbol, date
    """
    with sqlite3.connect(db_path) as conn:
        frame = pd.read_sql_query(query, conn, params=(*symbols, start, end))
    if frame.empty:
        raise ValueError("历史 raw/historical OHLCV 为空")
    return {
        str(symbol): group.drop(columns=["symbol"]).reset_index(drop=True)
        for symbol, group in frame.groupby("symbol", sort=True)
    }


def build_orders(
    frames: dict[str, pd.DataFrame],
    profile: VariantProfile,
) -> tuple[VariantOrder, ...]:
    orders: list[VariantOrder] = []
    for symbol, raw in frames.items():
        frame = raw.copy()
        frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
        frame["sma"] = frame["close"].rolling(profile.lookback).mean()
        frame["ret"] = frame["close"].pct_change(profile.lookback) * 100.0
        frame["bias"] = (frame["close"] / frame["sma"] - 1.0) * 100.0
        frame["prior_high"] = frame["high"].rolling(profile.lookback).max().shift(1)
        frame["volume_mean"] = frame["volume"].rolling(profile.lookback).mean().shift(1)
        frame["atr"] = (frame["high"] - frame["low"]).rolling(14).mean()
        frame["atr_pct"] = frame["atr"] / frame["close"] * 100.0
        dates = frame["date"].tolist()
        for index in range(profile.lookback, len(frame) - 1):
            row = frame.iloc[index]
            next_date = dates[index + 1]
            valid = pd.notna(row["sma"]) and pd.notna(row["ret"])
            if not valid:
                continue
            if profile.mode == "reversion":
                entry = bool(
                    row["close"] < row["sma"]
                    and row["ret"] <= -profile.entry_return_pct
                    and row["bias"] >= -profile.max_bias_pct - 8.0
                )
                exit_signal = bool(row["close"] > row["sma"] or row["ret"] > 2.0)
            elif profile.mode == "volume_breakout":
                entry = bool(
                    row["close"] >= row["prior_high"]
                    and row["volume"] >= row["volume_mean"] * 1.35
                    and row["bias"] <= profile.max_bias_pct
                )
                exit_signal = bool(row["close"] < row["sma"])
            elif profile.mode == "atr_trend":
                entry = bool(
                    row["close"] > row["sma"]
                    and row["ret"] >= profile.entry_return_pct
                    and row["atr_pct"] <= 6.0
                )
                exit_signal = bool(row["close"] < row["sma"] or row["ret"] < -2.0)
            elif profile.mode == "defensive_range":
                entry = bool(
                    row["close"] > row["sma"]
                    and row["ret"] >= profile.entry_return_pct
                    and row["bias"] <= profile.max_bias_pct
                    and row["atr_pct"] <= 3.5
                )
                exit_signal = bool(row["close"] < row["sma"] or row["atr_pct"] > 6.0)
            else:
                entry = bool(
                    row["close"] > row["sma"]
                    and row["ret"] >= profile.entry_return_pct
                    and row["bias"] <= profile.max_bias_pct
                )
                exit_signal = bool(row["close"] < row["sma"] or row["ret"] < -2.0)
            if entry:
                orders.append(VariantOrder(next_date, symbol, "buy", weight=0.33))
            if exit_signal:
                orders.append(VariantOrder(next_date, symbol, "sell", weight=1.0))
    return tuple(orders)


def run_suite(
    db_path: Path,
    symbols: tuple[str, ...],
    start: str,
    end: str,
) -> dict[str, object]:
    frames = load_frames(db_path, symbols, start, end)
    rules = VariantExecutionRules(initial_cash=100_000.0)
    profiles = generate_variant_profiles(frames)
    results = []
    for profile in profiles:
        result = simulate_variant(
            profile.variant_id,
            frames,
            build_orders(frames, profile),
            rules=rules,
        )
        payload = variant_result_to_dict(result)
        payload["label"] = profile.label
        payload["strategy_label"] = profile.label
        payload["strategy"] = {
            "id": profile.variant_id,
            "lookback_days": profile.lookback,
            "entry_return_pct": profile.entry_return_pct,
            "max_bias_pct": profile.max_bias_pct,
            "mode": profile.mode,
            "hypothesis": profile.hypothesis,
        }
        results.append(payload)
    results.sort(key=lambda item: float(item["final_equity"]), reverse=True)
    for rank, item in enumerate(results, start=1):
        item["rank"] = rank
    training_volatility_pct = _training_volatility_pct(frames)
    return {
        "schema_version": "variant-suite-v1",
        "generated_at": now_shanghai().isoformat(timespec="seconds"),
        "data_mode": "historical_raw_unadjusted",
        "start_date": start,
        "end_date": end,
        "symbols": list(symbols),
        "initial_cash": 100_000.0,
        "optimization": {
            "method": "training_window_volatility_mutation_v1",
            "training_bars": 60,
            "training_volatility_pct": training_volatility_pct,
            "evaluation_only": True,
            "selected_variant_id": results[0]["variant_id"] if results else "",
        },
        "execution_rules": {
            "t_plus_one": True,
            "lot_size": 100,
            "suspended_block": True,
            "limit_up_buy_block": True,
            "limit_down_sell_block": True,
            "fees_and_slippage": True,
        },
        "variants": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = run_suite(args.db, tuple(dict.fromkeys(args.symbols)), args.start, args.end)
    atomic_write_text(args.output, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(
        f"variant suite completed: variants={len(payload['variants'])} "
        f"symbols={len(payload['symbols'])} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
