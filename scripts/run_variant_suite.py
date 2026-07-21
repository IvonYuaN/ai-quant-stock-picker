#!/usr/bin/env python3
"""Run isolated short-term variants against raw historical OHLCV data.

The script consumes only historical workload data and writes an experiment
artifact. It never changes formal candidates, ledgers, or broker state.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from aqsp.backtest.variant_account import (
    VariantOrder,
    VariantExecutionRules,
    simulate_variant,
    variant_result_to_dict,
)
from aqsp.core.time import now_shanghai
from aqsp.data.source_factory import load_sqlite_symbol_name_map
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
    max_positions: int = 5
    selection: str = "ranked_signal"


PROFILES = (
    VariantProfile("trend_follow", "趋势跟随", 20, 2.0, 12.0, max_positions=5),
    VariantProfile("pullback", "趋势回踩", 20, 0.0, 4.0, max_positions=5),
    VariantProfile("breakout_continuation", "突破延续", 10, 4.0, 15.0, "breakout", max_positions=3),
    VariantProfile("defensive_momentum", "防守动量", 10, 1.0, 8.0, max_positions=3),
    VariantProfile("mean_reversion", "均值回归", 20, 3.0, 0.0, "reversion", max_positions=6),
    VariantProfile("low_volatility", "低波动趋势", 30, 1.0, 6.0, "low_vol", max_positions=6),
    VariantProfile("relative_strength", "相对强势", 15, 3.0, 10.0, "relative_strength", "强势股相对强度延续", 3),
    VariantProfile("volume_breakout", "量价突破", 20, 2.0, 15.0, "volume_breakout", "成交量确认突破比单看价格更可靠", 3),
    VariantProfile("atr_trend", "ATR趋势", 20, 1.5, 12.0, "atr_trend", "用波动率调整趋势入场，避免追逐异常波动", 5),
    VariantProfile("defensive_range", "防守区间", 20, 0.0, 5.0, "defensive_range", "低波动区间承接优先于高波动追涨", 6),
)

FEATURE_WARMUP_CALENDAR_DAYS = 90
DEFAULT_VARIANT_UNIVERSE_SIZE = 0
_UNIVERSE_BUCKETS = ("main_sh", "main_sz", "chinext")
_PRICE_BANDS = ("under_5", "5_to_15", "15_to_30", "30_to_80", "over_80")


def _universe_bucket(symbol: str) -> str | None:
    """Map an A-share code to a board bucket used for stratified sampling."""
    code = str(symbol).strip()
    if code.startswith(("600", "601", "603", "605")):
        return "main_sh"
    if code.startswith(("000", "001", "002", "003")):
        return "main_sz"
    if code.startswith(("300", "301")):
        return "chinext"
    return None


def _price_band(close: float) -> str:
    if close < 5:
        return "under_5"
    if close < 15:
        return "5_to_15"
    if close < 30:
        return "15_to_30"
    if close < 80:
        return "30_to_80"
    return "over_80"


def _sample_turnover_quantiles(
    candidates: list[tuple[str, float, float]], take: int
) -> list[str]:
    """Sample one price band from low to high turnover without ranking only by it."""
    ordered = sorted(candidates, key=lambda item: (item[2], item[0]))
    if take >= len(ordered):
        return [symbol for symbol, _close, _amount in ordered]
    if take == 1:
        return [ordered[len(ordered) // 2][0]]
    indexes = {
        round(index * (len(ordered) - 1) / (take - 1))
        for index in range(take)
    }
    return [ordered[index][0] for index in sorted(indexes)]


def select_stratified_symbols(
    db_path: Path,
    before_date: str,
    *,
    max_symbols: int = DEFAULT_VARIANT_UNIVERSE_SIZE,
) -> tuple[str, ...]:
    """Select a broad, reproducible universe without using future bars.

    The reference date is chosen from the historical date with the strongest
    coverage before the reset date. Each board is sampled across turnover
    quantiles, so the result is not an alphabetical or large-cap-only slice.
    """
    if max_symbols < 0:
        raise ValueError("max_symbols must be non-negative")
    with sqlite3.connect(db_path) as conn:
        reference_row = conn.execute(
            """
            SELECT date
            FROM ohlcv
            WHERE price_mode = 'raw' AND workload = 'historical' AND date < ?
            GROUP BY date
            ORDER BY COUNT(*) DESC, date DESC
            LIMIT 1
            """,
            (before_date,),
        ).fetchone()
        if reference_row is None:
            raise ValueError("reset date 前没有可用 raw/historical 覆盖日")
        reference_date = str(reference_row[0])
        rows = conn.execute(
            """
            SELECT symbol, close, amount
            FROM ohlcv
            WHERE price_mode = 'raw' AND workload = 'historical'
              AND date = ? AND suspended = 0 AND close > 0 AND amount > 0
            ORDER BY symbol
            """,
            (reference_date,),
        ).fetchall()
    name_map = load_sqlite_symbol_name_map([str(symbol) for symbol, _close, _amount in rows])
    grouped: dict[str, list[tuple[str, float, float]]] = {
        key: [] for key in _UNIVERSE_BUCKETS
    }
    for symbol, close, amount in rows:
        bucket = _universe_bucket(str(symbol))
        name = name_map.get(str(symbol), "")
        if bucket is not None and not name.startswith(("ST", "*ST", "退市")):
            grouped[bucket].append((str(symbol), float(close), float(amount)))
    if not any(grouped.values()):
        raise ValueError(f"{reference_date} 没有可用分层股票")

    available_count = sum(len(items) for items in grouped.values())
    if max_symbols <= 0 or max_symbols >= available_count:
        return tuple(
            symbol
            for bucket in _UNIVERSE_BUCKETS
            for symbol, _close, _amount in sorted(grouped[bucket], key=lambda item: item[0])
        )

    desired = {"main_sh": 28, "main_sz": 52, "chinext": 40}
    quotas = {bucket: min(desired[bucket], len(grouped[bucket])) for bucket in _UNIVERSE_BUCKETS}
    remaining = max_symbols - sum(quotas.values())
    while remaining > 0:
        candidates = [
            bucket
            for bucket in _UNIVERSE_BUCKETS
            if len(grouped[bucket]) > quotas[bucket]
        ]
        if not candidates:
            break
        for bucket in candidates:
            if remaining <= 0:
                break
            quotas[bucket] += 1
            remaining -= 1

    selected: list[str] = []
    for bucket in _UNIVERSE_BUCKETS:
        candidates = grouped[bucket]
        take = quotas[bucket]
        if take >= len(candidates):
            selected.extend(
                symbol for symbol, _close, _amount in sorted(candidates, key=lambda item: item[0])
            )
            continue
        by_price_band: dict[str, list[tuple[str, float, float]]] = {
            band: [] for band in _PRICE_BANDS
        }
        for candidate in candidates:
            by_price_band[_price_band(candidate[1])].append(candidate)
        active_bands = [band for band in _PRICE_BANDS if by_price_band[band]]
        target_share = {
            "under_5": 0.30,
            "5_to_15": 0.40,
            "15_to_30": 0.20,
            "30_to_80": 0.08,
            "over_80": 0.02,
        }
        target_counts = {
            band: max(1, round(take * target_share[band]))
            for band in active_bands
        }
        band_quotas = {band: min(1, len(by_price_band[band])) for band in active_bands}
        remaining = take - sum(band_quotas.values())
        while remaining > 0:
            expandable = [
                band
                for band in active_bands
                if len(by_price_band[band]) > band_quotas[band]
                and band_quotas[band] < target_counts[band]
            ]
            if not expandable:
                expandable = [
                    band
                    for band in active_bands
                    if len(by_price_band[band]) > band_quotas[band]
                ]
            if not expandable:
                break
            band = max(
                expandable,
                key=lambda item: (
                    target_counts[item] - band_quotas[item],
                    -_PRICE_BANDS.index(item),
                ),
            )
            band_quotas[band] += 1
            remaining -= 1
        for band in active_bands:
            selected.extend(
                _sample_turnover_quantiles(by_price_band[band], band_quotas[band])
            )
    return tuple(selected)


def _training_volatility_pct(
    frames: dict[str, pd.DataFrame], *, before_date: str = ""
) -> float:
    """Estimate volatility from the first 60 bars only; never use evaluation data."""
    values: list[float] = []
    for frame in frames.values():
        training = frame
        if before_date:
            training = frame.loc[frame["date"].astype(str) < before_date]
        closes = pd.to_numeric(training["close"], errors="coerce").dropna().head(60)
        if len(closes) > 1:
            values.extend((closes.pct_change().dropna().abs() * 100.0).tolist())
    return float(pd.Series(values).median()) if values else 0.0


def generate_variant_profiles(
    frames: dict[str, pd.DataFrame],
    *,
    training_end: str = "",
) -> tuple[VariantProfile, ...]:
    """Add deterministic mutations based on a point-in-time training window."""
    volatility = _training_volatility_pct(frames, before_date=training_end)
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
    grid_specs = (
        ("trend", "趋势网格", (0.0, 2.0), (4.0, 10.0), "趋势延续需要均线方向和收益确认"),
        ("reversion", "回归网格", (1.0, 3.0), (0.0, 4.0), "回撤达到阈值后等待均值修复"),
        ("breakout", "突破网格", (2.0, 4.0), (8.0, 15.0), "突破前高并限制追高偏离"),
        ("low_vol", "低波网格", (0.5, 1.5), (3.0, 6.0), "优先低波动趋势，控制追涨偏离"),
        ("macd", "MACD网格", (0.0, 2.0), (6.0, 12.0), "MACD柱体转强并由趋势确认"),
        ("kdj", "KDJ网格", (0.0, 2.0), (0.0, 8.0), "KDJ金叉从超卖区修复"),
        ("volume", "量能网格", (1.0, 3.0), (8.0, 15.0), "突破必须由成交量放大确认"),
        ("bollinger", "布林网格", (-3.0, -1.0), (0.0, 4.0), "价格触及下轨后观察均值回归"),
        ("rsi", "RSI网格", (-2.0, 0.0), (4.0, 8.0), "RSI从弱势区修复且不过度追涨"),
        ("ema_cross", "EMA交叉网格", (0.0, 2.0), (6.0, 12.0), "短长周期EMA交叉确认趋势"),
        ("obv", "OBV网格", (0.0, 2.0), (6.0, 12.0), "价格趋势与能量潮同步"),
        ("donchian", "唐奇安网格", (2.0, 4.0), (8.0, 15.0), "突破历史通道并控制乖离"),
        ("vwap", "成交额加权网格", (0.0, 2.0), (6.0, 12.0), "收盘站上成交额加权成本线"),
    )
    grid: list[VariantProfile] = []
    for mode, label, entries, biases, hypothesis in grid_specs:
        for lookback in (10, 20, 30):
            for entry_index, entry in enumerate(entries):
                bias = biases[entry_index]
                for max_positions in (3, 6):
                    grid.append(
                        VariantProfile(
                            variant_id=f"grid_{mode}_lb{lookback}_t{entry_index}_n{max_positions}",
                            label=(
                                f"{label}·{lookback}日·收益{entry:+g}%·"
                                f"乖离≤{bias:g}%·{max_positions}持仓"
                            ),
                            lookback=lookback,
                            entry_return_pct=entry,
                            max_bias_pct=bias,
                            mode=mode,
                            hypothesis=hypothesis,
                            max_positions=max_positions,
                        )
                    )
    return (*PROFILES, *mutations, *grid)


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
    metadata_columns = [
        column
        for column in ("source", "fetched_at", "timestamp_source")
        if column in columns
    ]
    select_columns = (
        "symbol, date, open, high, low, close, volume, amount, "
        "suspended, limit_up, limit_down"
        + (", " + ", ".join(metadata_columns) if metadata_columns else "")
    )
    query = f"""
        SELECT {select_columns}
        FROM ohlcv
        WHERE price_mode = 'raw'{workload_filter}
          AND symbol IN ({placeholders}) AND date BETWEEN ? AND ?
        ORDER BY symbol, date
    """
    with sqlite3.connect(db_path) as conn:
        frame = pd.read_sql_query(query, conn, params=(*symbols, start, end))
    if frame.empty:
        raise ValueError("历史 raw/historical OHLCV 为空")
    result: dict[str, pd.DataFrame] = {}
    for symbol, group in frame.groupby("symbol", sort=True):
        prepared = group.drop(columns=["symbol"]).reset_index(drop=True)
        prepared.attrs["sources"] = tuple(
            sorted(
                {
                    str(value).strip()
                    for value in group.get("source", pd.Series(dtype=str)).tolist()
                    if str(value).strip()
                }
            )
        )
        prepared.attrs["latest_fetched_at"] = max(
            (
                str(value).strip()
                for value in group.get("fetched_at", pd.Series(dtype=str)).tolist()
                if str(value).strip()
            ),
            default="",
        )
        for column in metadata_columns:
            if column in prepared.columns:
                prepared = prepared.drop(columns=[column])
        result[str(symbol)] = prepared
    return result


def _prepare_signal_frame(raw: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Compute all technical features once for one symbol/lookback pair."""
    frame = raw.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    frame["sma"] = frame["close"].rolling(lookback).mean()
    frame["ret"] = frame["close"].pct_change(lookback) * 100.0
    frame["bias"] = (frame["close"] / frame["sma"] - 1.0) * 100.0
    frame["prior_high"] = frame["high"].rolling(lookback).max().shift(1)
    frame["prior_low"] = frame["low"].rolling(lookback).min().shift(1)
    frame["volume_mean"] = frame["volume"].rolling(lookback).mean().shift(1)
    frame["volume_ratio"] = frame["volume"] / frame["volume_mean"]
    frame["atr"] = (frame["high"] - frame["low"]).rolling(14).mean()
    frame["atr_pct"] = frame["atr"] / frame["close"] * 100.0
    frame["ema12"] = frame["close"].ewm(span=12, adjust=False).mean()
    frame["ema26"] = frame["close"].ewm(span=26, adjust=False).mean()
    frame["macd"] = frame["ema12"] - frame["ema26"]
    frame["macd_signal"] = frame["macd"].ewm(span=9, adjust=False).mean()
    frame["macd_hist"] = frame["macd"] - frame["macd_signal"]
    frame["macd_hist_prev"] = frame["macd_hist"].shift(1)
    low9 = frame["low"].rolling(9).min()
    high9 = frame["high"].rolling(9).max()
    rsv = (frame["close"] - low9) / (high9 - low9).replace(0, float("nan")) * 100.0
    frame["kdj_k"] = rsv.ewm(com=2, adjust=False).mean()
    frame["kdj_d"] = frame["kdj_k"].ewm(com=2, adjust=False).mean()
    frame["kdj_j"] = 3.0 * frame["kdj_k"] - 2.0 * frame["kdj_d"]
    frame["rsi"] = _rsi(frame["close"], 14)
    frame["bb_mid"] = frame["close"].rolling(20).mean()
    frame["bb_std"] = frame["close"].rolling(20).std(ddof=0)
    frame["bb_lower"] = frame["bb_mid"] - 2.0 * frame["bb_std"]
    direction = frame["close"].diff().fillna(0.0).map(
        lambda value: 1 if value > 0 else -1 if value < 0 else 0
    )
    frame["obv"] = (direction * frame["volume"]).cumsum()
    frame["obv_mean"] = frame["obv"].rolling(lookback).mean()
    frame["vwap"] = (
        frame["amount"].rolling(lookback).sum()
        / frame["volume"].rolling(lookback).sum()
    )
    return frame


def build_orders(
    frames: dict[str, pd.DataFrame],
    profile: VariantProfile,
    *,
    first_trade_date: str = "",
    prepared_cache: dict[tuple[str, int], pd.DataFrame] | None = None,
) -> tuple[VariantOrder, ...]:
    signals_by_date: dict[
        str, list[tuple[str, float, bool, bool, tuple[str, ...]]]
    ] = defaultdict(list)
    for symbol, raw in frames.items():
        cache_key = (symbol, profile.lookback)
        frame = prepared_cache.get(cache_key) if prepared_cache is not None else None
        if frame is None:
            frame = _prepare_signal_frame(raw, profile.lookback)
            if prepared_cache is not None:
                prepared_cache[cache_key] = frame
        dates = frame["date"].tolist()
        for index in range(profile.lookback, len(frame) - 1):
            row = frame.iloc[index]
            next_date = dates[index + 1]
            if first_trade_date and next_date < first_trade_date:
                continue
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
            elif profile.mode == "breakout":
                entry = bool(
                    row["close"] >= row["prior_high"]
                    and row["ret"] >= profile.entry_return_pct
                    and row["bias"] <= profile.max_bias_pct
                )
                exit_signal = bool(row["close"] < row["sma"])
            elif profile.mode == "volume_breakout":
                entry = bool(
                    row["close"] >= row["prior_high"]
                    and row["volume_ratio"] >= 1.35
                    and row["bias"] <= profile.max_bias_pct
                )
                exit_signal = bool(row["close"] < row["sma"])
            elif profile.mode == "volume":
                entry = bool(
                    row["close"] >= row["prior_high"]
                    and row["volume_ratio"] >= 1.2 + profile.entry_return_pct * 0.1
                    and row["bias"] <= profile.max_bias_pct
                )
                exit_signal = bool(
                    row["close"] < row["sma"] or row["volume_ratio"] < 0.8
                )
            elif profile.mode == "macd":
                entry = bool(
                    row["close"] > row["sma"]
                    and row["macd_hist"] > 0
                    and row["macd_hist"] >= row["macd_hist_prev"]
                    and row["ret"] >= profile.entry_return_pct
                    and row["bias"] <= profile.max_bias_pct
                )
                exit_signal = bool(row["macd_hist"] < 0 or row["close"] < row["sma"])
            elif profile.mode == "kdj":
                entry = bool(
                    row["kdj_k"] > row["kdj_d"]
                    and row["kdj_j"] < 100.0
                    and row["ret"] >= profile.entry_return_pct
                    and row["bias"] <= profile.max_bias_pct
                )
                exit_signal = bool(row["kdj_k"] < row["kdj_d"] or row["kdj_j"] > 110.0)
            elif profile.mode == "bollinger":
                entry = bool(
                    row["close"] <= row["bb_lower"]
                    and row["ret"] <= profile.entry_return_pct
                    and row["bias"] <= profile.max_bias_pct
                )
                exit_signal = bool(row["close"] > row["bb_mid"])
            elif profile.mode == "rsi":
                entry = bool(
                    row["rsi"] < 45.0
                    and row["ret"] >= profile.entry_return_pct
                    and row["bias"] <= profile.max_bias_pct
                )
                exit_signal = bool(row["rsi"] > 65.0 or row["close"] < row["sma"])
            elif profile.mode == "ema_cross":
                entry = bool(
                    row["ema12"] > row["ema26"]
                    and row["macd_hist"] >= row["macd_hist_prev"]
                    and row["ret"] >= profile.entry_return_pct
                    and row["bias"] <= profile.max_bias_pct
                )
                exit_signal = bool(row["ema12"] < row["ema26"])
            elif profile.mode == "obv":
                entry = bool(
                    row["obv"] > row["obv_mean"]
                    and row["close"] > row["sma"]
                    and row["ret"] >= profile.entry_return_pct
                    and row["bias"] <= profile.max_bias_pct
                )
                exit_signal = bool(row["obv"] < row["obv_mean"])
            elif profile.mode == "donchian":
                entry = bool(
                    row["close"] >= row["prior_high"]
                    and row["ret"] >= profile.entry_return_pct
                    and row["bias"] <= profile.max_bias_pct
                )
                exit_signal = bool(row["close"] < row["prior_low"])
            elif profile.mode == "vwap":
                entry = bool(
                    row["close"] > row["vwap"]
                    and row["ret"] >= profile.entry_return_pct
                    and row["bias"] <= profile.max_bias_pct
                )
                exit_signal = bool(row["close"] < row["vwap"])
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
            score = float(row["ret"])
            if profile.mode == "reversion":
                score = -score
            elif profile.mode == "low_vol":
                atr_pct = float(row["atr_pct"]) if pd.notna(row["atr_pct"]) else 999.0
                score = -atr_pct
            evidence = _signal_evidence(row, profile)
            signals_by_date[next_date].append(
                (symbol, score, entry, exit_signal, evidence)
            )

    orders: list[VariantOrder] = []
    active_symbols: set[str] = set()
    for trade_date in sorted(signals_by_date):
        signals = signals_by_date[trade_date]
        entries = sorted(
            (signal for signal in signals if signal[2]),
            key=lambda signal: (signal[1], signal[0]),
            reverse=True,
        )
        selected = {signal[0] for signal in entries[: max(profile.max_positions, 1)]}
        for symbol, _score, entry, exit_signal, evidence in signals:
            if exit_signal and symbol in active_symbols:
                orders.append(
                    VariantOrder(
                        trade_date,
                        symbol,
                        "sell",
                        weight=1.0,
                        evidence=evidence + ("退出条件：策略退出信号触发",),
                    )
                )
                active_symbols.discard(symbol)
            elif symbol in active_symbols and symbol not in selected:
                orders.append(
                    VariantOrder(
                        trade_date,
                        symbol,
                        "sell",
                        weight=1.0,
                        evidence=evidence + ("退出条件：未进入当日持仓名额",),
                    )
                )
                active_symbols.discard(symbol)
            elif entry and symbol in selected and symbol not in active_symbols:
                orders.append(
                    VariantOrder(
                        trade_date,
                        symbol,
                        "buy",
                        weight=min(0.5, 1.0 / max(profile.max_positions, 1)),
                        evidence=evidence,
                    )
                )
                active_symbols.add(symbol)
    return tuple(orders)


def _rsi(close: pd.Series, period: int) -> pd.Series:
    """Compute Wilder-style RSI using only current and prior closes."""
    delta = close.diff()
    gains = delta.clip(lower=0.0).ewm(alpha=1.0 / period, adjust=False).mean()
    losses = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / period, adjust=False).mean()
    relative = gains / losses.replace(0, float("nan"))
    return 100.0 - 100.0 / (1.0 + relative)


def _signal_evidence(row: pd.Series, profile: VariantProfile) -> tuple[str, ...]:
    """Serialize observed technical values attached to every generated order."""
    evidence = [
        f"信号日 {str(row['date'])[:10]}",
        f"收盘 {float(row['close']):.2f}",
        f"{profile.lookback}日收益 {float(row['ret']):+.2f}%",
    ]
    values = {
        "MACD": ("macd", "macd_signal", "macd_hist"),
        "KDJ": ("kdj_k", "kdj_d", "kdj_j"),
        "量比": ("volume_ratio",),
        "RSI": ("rsi",),
        "布林": ("bb_lower", "bb_mid"),
    }
    for label, columns in values.items():
        if all(pd.notna(row.get(column)) for column in columns):
            evidence.append(
                label
                + " "
                + "/".join(
                    f"{float(row[column]):.2f}" for column in columns
                )
            )
    return tuple(evidence)


def deduplicate_variant_results(
    results: list[dict[str, object]], *, symbol_count: int
) -> tuple[list[dict[str, object]], int]:
    """Remove exact duplicate end portfolios from a broad-universe result set."""
    if symbol_count < 8:
        return results, 0
    unique_results: list[dict[str, object]] = []
    seen_portfolios: set[tuple[tuple[str, int], ...]] = set()
    removed = 0
    for item in results:
        positions = item.get("positions", {})
        signature = tuple(
            sorted(
                (str(symbol), int(quantity))
                for symbol, quantity in dict(positions).items()
            )
        )
        if signature in seen_portfolios:
            removed += 1
            continue
        seen_portfolios.add(signature)
        unique_results.append(item)
    return unique_results, removed


def run_suite(
    db_path: Path,
    symbols: tuple[str, ...],
    start: str,
    end: str,
) -> dict[str, object]:
    start_day = date.fromisoformat(start)
    end_day = date.fromisoformat(end)
    if start_day > end_day:
        raise ValueError("start must not be later than end")
    warmup_start = start_day - timedelta(days=FEATURE_WARMUP_CALENDAR_DAYS)
    # Indicators may use only bars before the reset date as warm-up data. Orders
    # are filtered separately, so the paper account always starts from zero cash
    # and zero holdings on the requested reset date.
    frames = load_frames(
        db_path,
        symbols,
        warmup_start.isoformat(),
        end,
    )
    rules = VariantExecutionRules(initial_cash=100_000.0)
    profiles = generate_variant_profiles(frames, training_end=start)
    results = []
    prepared_cache: dict[tuple[str, int], pd.DataFrame] = {}
    for profile in profiles:
        result = simulate_variant(
            profile.variant_id,
            frames,
            build_orders(
                frames,
                profile,
                first_trade_date=start,
                prepared_cache=prepared_cache,
            ),
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
            "max_positions": profile.max_positions,
            "selection": profile.selection,
        }
        results.append(payload)
    generated_variant_count = len(results)
    results, duplicate_portfolios_removed = deduplicate_variant_results(
        results, symbol_count=len(symbols)
    )
    results.sort(key=lambda item: float(item["final_equity"]), reverse=True)
    for rank, item in enumerate(results, start=1):
        item["rank"] = rank
    training_volatility_pct = _training_volatility_pct(frames, before_date=start)
    latest_trade_date = max(
        str(frame["date"].max())[:10] for frame in frames.values() if not frame.empty
    )
    end_coverage = sum(
        str(frame["date"].max())[:10] == end for frame in frames.values() if not frame.empty
    )
    latest_sources = sorted(
        {
            source
            for frame in frames.values()
            for source in frame.attrs.get("sources", ())
        }
    )
    return {
        "schema_version": "variant-suite-v1",
        "generated_at": now_shanghai().isoformat(timespec="seconds"),
        "data_mode": "historical_raw_unadjusted",
        "start_date": start,
        "end_date": end,
        "data_start_date": warmup_start.isoformat(),
        "data_latest_trade_date": latest_trade_date,
        "data_sources": latest_sources,
        "data_coverage": {
            "symbols": len(frames),
            "end_date_symbols": end_coverage,
            "end_date_coverage_pct": round(
                end_coverage / max(len(frames), 1) * 100.0, 2
            ),
        },
        "symbols": list(symbols),
        "universe_scope": {
            "symbol_count": len(symbols),
            "board_scope": "沪深主板+创业板",
            "excluded": ["ST", "科创板", "其他板块"],
        },
        "universe_warning": (
            "变体样本池少于 8 只，结果不适合比较持仓差异"
            if len(symbols) < 8
            else ""
        ),
        "initial_cash": 100_000.0,
        "optimization": {
            "method": "training_only_evolution_and_orthogonal_strategy_grid_v3",
            "training_bars": 60,
            "training_volatility_pct": training_volatility_pct,
            "training_end_exclusive": start,
            "minimum_training_samples": 60,
            "cooldown_days": 20,
            "evaluation_only": True,
            "generated_variant_count": generated_variant_count,
            "variant_count": len(results),
            "duplicate_portfolios_removed": duplicate_portfolios_removed,
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
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument(
        "--universe-size",
        type=int,
        default=DEFAULT_VARIANT_UNIVERSE_SIZE,
        help="未显式传入 symbols 时，从 reset 日期前的历史覆盖中取样；0 表示全部可用股票",
    )
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    symbols = tuple(dict.fromkeys(args.symbols or ()))
    if not symbols:
        symbols = select_stratified_symbols(
            args.db,
            args.start,
            max_symbols=args.universe_size,
        )
    payload = run_suite(args.db, symbols, args.start, args.end)
    atomic_write_text(args.output, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(
        f"variant suite completed: variants={len(payload['variants'])} "
        f"symbols={len(payload['symbols'])} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
