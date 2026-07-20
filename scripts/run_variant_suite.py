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


PROFILES = (
    VariantProfile("trend_follow", "趋势跟随", 20, 2.0, 12.0),
    VariantProfile("pullback", "趋势回踩", 20, 0.0, 4.0),
    VariantProfile("defensive_momentum", "防守动量", 10, 1.0, 8.0),
)


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
        dates = frame["date"].tolist()
        for index in range(profile.lookback, len(frame) - 1):
            row = frame.iloc[index]
            next_date = dates[index + 1]
            valid = pd.notna(row["sma"]) and pd.notna(row["ret"])
            if not valid:
                continue
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
    results = []
    for profile in PROFILES:
        result = simulate_variant(
            profile.variant_id,
            frames,
            build_orders(frames, profile),
            rules=rules,
        )
        payload = variant_result_to_dict(result)
        payload["label"] = profile.label
        results.append(payload)
    return {
        "schema_version": "variant-suite-v1",
        "generated_at": now_shanghai().isoformat(timespec="seconds"),
        "data_mode": "historical_raw_unadjusted",
        "start_date": start,
        "end_date": end,
        "symbols": list(symbols),
        "initial_cash": 100_000.0,
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
