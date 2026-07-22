#!/usr/bin/env python3
"""Run isolated short-term variants against raw historical OHLCV data.

The script consumes only historical workload data and writes an experiment
artifact. It never changes formal candidates, ledgers, or broker state.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
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


_FAMILY_SPECS = (
    ("trend", "趋势", (0.0, 8.0), "趋势延续需要均线方向和收益确认"),
    ("reversion", "均值回归", (2.0, 0.0), "偏离均线后等待价格回归"),
    ("breakout", "突破", (3.0, 12.0), "突破前高并限制追高乖离"),
    ("volume_breakout", "量价突破", (2.0, 12.0), "突破必须由成交量放大确认"),
    ("macd", "MACD", (1.0, 10.0), "MACD柱体转强并由价格趋势确认"),
    ("kdj", "KDJ", (1.0, 8.0), "KDJ金叉从弱势区修复"),
    ("low_vol", "低波趋势", (1.0, 6.0), "低波动趋势优先，减少异常追涨"),
)
_VARIANT_LOOKBACKS = (10, 20)
_VARIANT_POSITION_CAPS = (3, 6)

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

    The reference date is the latest available historical date before the
    reset date. Each board is sampled across turnover quantiles, so the result
    is not an alphabetical or large-cap-only slice.
    """
    if max_symbols < 0:
        raise ValueError("max_symbols must be non-negative")
    with sqlite3.connect(db_path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "ohlcv" in tables:
            reference_row = conn.execute(
                """
                SELECT MAX(date)
                FROM ohlcv
                WHERE price_mode = 'raw' AND workload = 'historical' AND date < ?
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
            name_map = load_sqlite_symbol_name_map(
                [str(symbol) for symbol, _close, _amount in rows]
            )
        elif {"daily_qfq", "stocks"} <= tables:
            compact_before = before_date.replace("-", "")
            current_rows = conn.execute(
                """
                SELECT ts_code
                FROM daily_qfq
                WHERE trade_date = ? AND close > 0 AND amount > 0
                """,
                (compact_before,),
            ).fetchall()
            if not current_rows:
                raise ValueError("reset date 前没有可用 raw daily_qfq 覆盖日")

            reference_date = ""
            for (current_code,) in current_rows:
                reference_row = conn.execute(
                    """
                    SELECT MAX(trade_date)
                    FROM daily_qfq
                    WHERE ts_code = ? AND trade_date != 'SKIP'
                      AND trade_date < ? AND close > 0 AND amount > 0
                    """,
                    (current_code, compact_before),
                ).fetchone()
                if reference_row and reference_row[0]:
                    reference_date = str(reference_row[0])
                    break
            if not reference_date:
                raise ValueError("reset date 前没有可用 raw daily_qfq 历史日")

            current_codes = [str(row[0]) for row in current_rows]
            raw_rows: list[tuple[object, ...]] = []
            for offset in range(0, len(current_codes), 400):
                chunk = current_codes[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                raw_rows.extend(
                    conn.execute(
                        f"""
                        SELECT substr(d.ts_code, 1, 6), d.close, d.amount,
                               COALESCE(s.name, '')
                        FROM daily_qfq AS d
                        LEFT JOIN stocks AS s ON s.ts_code = d.ts_code
                        WHERE d.trade_date = ? AND d.ts_code IN ({placeholders})
                          AND d.close > 0 AND d.amount > 0
                        ORDER BY d.ts_code
                        """,
                        (reference_date, *chunk),
                    ).fetchall()
                )
            rows = [(str(symbol), close, amount) for symbol, close, amount, _name in raw_rows]
            name_map = {str(symbol): str(name or "") for symbol, _close, _amount, name in raw_rows}
            reference_date = (
                f"{reference_date[:4]}-{reference_date[4:6]}-{reference_date[6:8]}"
                if len(reference_date) == 8
                else reference_date
            )
        else:
            raise ValueError("数据库缺少 ohlcv 或 daily_qfq/stocks 表")
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
    """Build a small orthogonal grid; training only selects the market note."""
    volatility = _training_volatility_pct(frames, before_date=training_end)
    grid: list[VariantProfile] = []
    for mode, label, (entry, bias), hypothesis in _FAMILY_SPECS:
        for lookback in _VARIANT_LOOKBACKS:
            for max_positions in _VARIANT_POSITION_CAPS:
                grid.append(
                    VariantProfile(
                        variant_id=f"{mode}_lb{lookback}_n{max_positions}",
                        label=(
                            f"{label}·{lookback}日·收益{entry:+g}%·"
                            f"乖离≤{bias:g}%·{max_positions}持仓"
                        ),
                        lookback=lookback,
                        entry_return_pct=entry,
                        max_bias_pct=bias,
                        mode=mode,
                        hypothesis=hypothesis
                        + (f"；训练波动中位数{volatility:.2f}%" if volatility else ""),
                        max_positions=max_positions,
                    )
                )
    return tuple(grid)


def load_frames(
    db_path: Path,
    symbols: tuple[str, ...],
    start: str,
    end: str,
) -> dict[str, pd.DataFrame]:
    with sqlite3.connect(db_path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(ohlcv)")
        }
        if "ohlcv" not in tables:
            if not {"daily_qfq", "stocks"} <= tables:
                raise ValueError("数据库缺少 ohlcv 或 daily_qfq/stocks 表")
            symbol_map = dict(
                conn.execute(
                    "SELECT substr(ts_code, 1, 6), ts_code FROM stocks"
                ).fetchall()
            )
            selected_ts_codes = [symbol_map[symbol] for symbol in symbols if symbol in symbol_map]
            frames: list[pd.DataFrame] = []
            for offset in range(0, len(selected_ts_codes), 400):
                chunk = selected_ts_codes[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                frames.append(
                    pd.read_sql_query(
                        f"""
                        SELECT substr(ts_code, 1, 6) AS symbol, trade_date AS date,
                               open, high, low, close, volume, amount,
                               0 AS suspended, NULL AS limit_up, NULL AS limit_down
                        FROM daily_qfq
                        WHERE ts_code IN ({placeholders})
                          AND trade_date != 'SKIP'
                          AND trade_date BETWEEN ? AND ?
                        ORDER BY ts_code, trade_date
                        """,
                        conn,
                        params=(*chunk, start.replace("-", ""), end.replace("-", "")),
                    )
                )
            frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            if not frame.empty:
                frame["date"] = frame["date"].map(
                    lambda value: (
                        f"{str(value)[:4]}-{str(value)[4:6]}-{str(value)[6:8]}"
                        if len(str(value)) == 8
                        else str(value)
                    )
                )
            metadata_columns: list[str] = []
        else:
            workload_filter = " AND workload = 'historical'" if "workload" in columns else ""
            metadata_columns = [
                column
                for column in ("source", "fetched_at", "timestamp_source")
                if column in columns
            ]
            placeholders = ",".join("?" for _ in symbols)
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
            frame = pd.read_sql_query(query, conn, params=(*symbols, start, end))
    if frame.empty:
        raise ValueError("历史 raw/historical OHLCV 为空")
    result: dict[str, pd.DataFrame] = {}
    external_source = "ohlcv" not in tables
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
        if external_source and not prepared.attrs["sources"]:
            prepared.attrs["sources"] = ("sqlite_db",)
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


def _rolling_sum_np(values: np.ndarray, window: int) -> np.ndarray:
    result = np.full(values.shape, np.nan, dtype=float)
    if window <= 0 or len(values) < window:
        return result
    cumulative = np.concatenate(([0.0], np.cumsum(values, dtype=float)))
    result[window - 1 :] = cumulative[window:] - cumulative[:-window]
    return result


def _rolling_mean_np(values: np.ndarray, window: int) -> np.ndarray:
    return _rolling_sum_np(values, window) / float(window)


def _rolling_std_np(values: np.ndarray, window: int) -> np.ndarray:
    result = np.full(values.shape, np.nan, dtype=float)
    if window <= 0 or len(values) < window:
        return result
    windows = np.lib.stride_tricks.sliding_window_view(values, window)
    result[window - 1 :] = windows.std(axis=1, ddof=0)
    return result


def _rolling_min_np(values: np.ndarray, window: int) -> np.ndarray:
    result = np.full(values.shape, np.nan, dtype=float)
    if window <= 0 or len(values) < window:
        return result
    result[window - 1 :] = np.lib.stride_tricks.sliding_window_view(
        values, window
    ).min(axis=1)
    return result


def _rolling_max_np(values: np.ndarray, window: int) -> np.ndarray:
    result = np.full(values.shape, np.nan, dtype=float)
    if window <= 0 or len(values) < window:
        return result
    result[window - 1 :] = np.lib.stride_tricks.sliding_window_view(
        values, window
    ).max(axis=1)
    return result


def _ewm_np(values: np.ndarray, alpha: float) -> np.ndarray:
    """Compute causal adjust=False EWM without constructing pandas objects."""
    result = np.full(values.shape, np.nan, dtype=float)
    if len(values) == 0:
        return result
    finite = np.flatnonzero(np.isfinite(values))
    if len(finite) == 0:
        return result
    first = int(finite[0])
    result[first] = values[first]
    for index in range(first + 1, len(values)):
        value = values[index]
        previous = result[index - 1]
        result[index] = (
            previous
            if not np.isfinite(value)
            else value
            if not np.isfinite(previous)
            else alpha * value + (1.0 - alpha) * previous
        )
    return result


def _prepare_base_signal_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Compute lookback-independent technical features once per symbol."""
    frame = raw.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    high = frame["high"].to_numpy(dtype=float, copy=False)
    low = frame["low"].to_numpy(dtype=float, copy=False)
    close = frame["close"].to_numpy(dtype=float, copy=False)
    volume = frame["volume"].to_numpy(dtype=float, copy=False)
    frame["atr"] = _rolling_mean_np(high - low, 14)
    frame["atr_pct"] = frame["atr"] / frame["close"] * 100.0
    frame["ema12"] = _ewm_np(close, 2.0 / 13.0)
    frame["ema26"] = _ewm_np(close, 2.0 / 27.0)
    frame["macd"] = frame["ema12"] - frame["ema26"]
    frame["macd_signal"] = _ewm_np(frame["macd"].to_numpy(dtype=float), 0.2)
    frame["macd_hist"] = frame["macd"] - frame["macd_signal"]
    frame["macd_hist_prev"] = frame["macd_hist"].shift(1)
    low9 = _rolling_min_np(low, 9)
    high9 = _rolling_max_np(high, 9)
    denominator = high9 - low9
    denominator[denominator == 0.0] = np.nan
    rsv = (close - low9) / denominator * 100.0
    frame["kdj_k"] = _ewm_np(rsv, 1.0 / 3.0)
    frame["kdj_d"] = _ewm_np(frame["kdj_k"].to_numpy(dtype=float), 1.0 / 3.0)
    frame["kdj_j"] = 3.0 * frame["kdj_k"] - 2.0 * frame["kdj_d"]
    delta = np.diff(close, prepend=np.nan)
    gains = np.maximum(delta, 0.0)
    losses = np.maximum(-delta, 0.0)
    average_gain = _ewm_np(gains, 1.0 / 14.0)
    average_loss = _ewm_np(losses, 1.0 / 14.0)
    relative = average_gain / np.where(average_loss == 0.0, np.nan, average_loss)
    frame["rsi"] = 100.0 - 100.0 / (1.0 + relative)
    frame["bb_mid"] = _rolling_mean_np(close, 20)
    frame["bb_std"] = _rolling_std_np(close, 20)
    frame["bb_lower"] = frame["bb_mid"] - 2.0 * frame["bb_std"]
    direction = np.sign(np.nan_to_num(delta, nan=0.0))
    frame["obv"] = np.cumsum(direction * volume)
    return frame


def _prepare_signal_frame(
    raw: pd.DataFrame,
    lookback: int,
    *,
    base: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Add the small set of lookback-dependent features to a shared base."""
    frame = base.copy() if base is not None else _prepare_base_signal_frame(raw)
    close = frame["close"].to_numpy(dtype=float, copy=False)
    high = frame["high"].to_numpy(dtype=float, copy=False)
    low = frame["low"].to_numpy(dtype=float, copy=False)
    volume = frame["volume"].to_numpy(dtype=float, copy=False)
    amount = frame["amount"].to_numpy(dtype=float, copy=False)
    obv = frame["obv"].to_numpy(dtype=float, copy=False)
    frame["sma"] = _rolling_mean_np(close, lookback)
    returns = np.full(close.shape, np.nan, dtype=float)
    returns[lookback:] = (close[lookback:] / close[:-lookback] - 1.0) * 100.0
    frame["ret"] = returns
    frame["bias"] = (frame["close"] / frame["sma"] - 1.0) * 100.0
    prior_high = _rolling_max_np(high, lookback)
    prior_low = _rolling_min_np(low, lookback)
    volume_mean = _rolling_mean_np(volume, lookback)
    obv_mean = _rolling_mean_np(obv, lookback)
    prior_high = np.roll(prior_high, 1)
    prior_low = np.roll(prior_low, 1)
    volume_mean = np.roll(volume_mean, 1)
    prior_high[0] = np.nan
    prior_low[0] = np.nan
    volume_mean[0] = np.nan
    frame["prior_high"] = prior_high
    frame["prior_low"] = prior_low
    frame["volume_mean"] = volume_mean
    frame["volume_ratio"] = frame["volume"] / frame["volume_mean"]
    frame["obv_mean"] = obv_mean
    volume_sum = _rolling_sum_np(volume, lookback)
    frame["vwap"] = _rolling_sum_np(amount, lookback) / volume_sum
    return frame


def build_orders(
    frames: dict[str, pd.DataFrame],
    profile: VariantProfile,
    *,
    first_trade_date: str = "",
    prepared_cache: dict[tuple[str, int], pd.DataFrame] | None = None,
    base_cache: dict[str, pd.DataFrame] | None = None,
) -> tuple[VariantOrder, ...]:
    signals_by_date: dict[
        str, list[tuple[str, float, bool, bool, tuple[str, ...]]]
    ] = defaultdict(list)
    for symbol, raw in frames.items():
        cache_key = (symbol, profile.lookback)
        frame = prepared_cache.get(cache_key) if prepared_cache is not None else None
        if frame is None:
            base = base_cache.get(symbol) if base_cache is not None else None
            if base is None:
                base = _prepare_base_signal_frame(raw)
                if base_cache is not None:
                    base_cache[symbol] = base
            frame = _prepare_signal_frame(raw, profile.lookback, base=base)
            if prepared_cache is not None:
                prepared_cache[cache_key] = frame
        dates = frame["date"].tolist()
        first_index = profile.lookback
        if first_trade_date:
            first_index = max(
                first_index,
                bisect_left(dates, first_trade_date) - 1,
            )
        for index in range(first_index, len(frame) - 1):
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
            evidence = _signal_evidence(row, profile) if entry or exit_signal else ()
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


def validate_variant_artifact(
    payload: Mapping[str, object],
    *,
    expected_end_date: str,
    expected_start_date: str | None = None,
) -> None:
    """Fail closed before a generated artifact replaces production data."""
    if payload.get("schema_version") != "variant-suite-v1":
        raise ValueError("variant artifact schema_version 不支持")
    if payload.get("data_mode") != "historical_raw_unadjusted":
        raise ValueError("variant artifact 必须使用不复权历史数据")
    if str(payload.get("end_date", "")) != expected_end_date:
        raise ValueError("variant artifact end_date 与重置日不一致")
    if expected_start_date is not None and str(payload.get("start_date", "")) != expected_start_date:
        raise ValueError("variant artifact start_date 与重置日不一致")
    symbols = payload.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise ValueError("variant artifact symbols 不能为空")
    if len(set(symbols)) != len(symbols):
        raise ValueError("variant artifact symbols 不得重复")
    scope = payload.get("universe_scope")
    if not isinstance(scope, dict):
        raise ValueError("variant artifact 缺少 universe_scope")
    if scope.get("symbol_count") != len(symbols):
        raise ValueError("universe_scope.symbol_count 与 symbols 不一致")
    if scope.get("board_scope") != "沪深主板+创业板":
        raise ValueError("variant artifact board_scope 不符合生产范围")
    if scope.get("excluded") != ["ST", "科创板", "其他板块"]:
        raise ValueError("variant artifact excluded 不符合生产范围")
    coverage = payload.get("data_coverage")
    if not isinstance(coverage, dict) or coverage.get("end_date_coverage_pct") != 100.0:
        raise ValueError("variant artifact 末日数据覆盖率不是 100%")
    variants = payload.get("variants")
    if not isinstance(variants, list) or not variants:
        raise ValueError("variant artifact variants 不能为空")
    ids: set[str] = set()
    labels: set[str] = set()
    for item in variants:
        if not isinstance(item, dict):
            raise ValueError("variant artifact variant 必须是 object")
        variant_id = str(item.get("variant_id", ""))
        label = str(item.get("label", ""))
        if not variant_id or variant_id in ids:
            raise ValueError("variant artifact variant_id 重复或为空")
        if not label or label in labels:
            raise ValueError("variant artifact label 重复或为空")
        ids.add(variant_id)
        labels.add(label)
        if item.get("initial_cash") != 100_000.0:
            raise ValueError("variant artifact initial_cash 必须为 100000")
        fills = item.get("fills", [])
        if not isinstance(fills, list):
            raise ValueError("variant artifact fills 必须是 array")
        for fill in fills:
            if not isinstance(fill, dict):
                raise ValueError("variant artifact fill 必须是 object")
            if fill.get("status") == "filled" and not fill.get("evidence"):
                raise ValueError("variant artifact 成交缺少技术证据")


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
    base_cache: dict[str, pd.DataFrame] = {}
    for lookback in sorted({profile.lookback for profile in profiles}):
        # Keep only one lookback's derived columns alive; the shared base
        # indicators remain cached for the other orthogonal variants.
        prepared_cache: dict[tuple[str, int], pd.DataFrame] = {}
        for profile in (item for item in profiles if item.lookback == lookback):
            result = simulate_variant(
                profile.variant_id,
                frames,
                build_orders(
                    frames,
                    profile,
                    first_trade_date=start,
                    prepared_cache=prepared_cache,
                    base_cache=base_cache,
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
    artifact = {
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
    validate_variant_artifact(artifact, expected_end_date=end)
    return artifact


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
