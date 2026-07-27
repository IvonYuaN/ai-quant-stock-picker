#!/usr/bin/env python3
"""Run isolated short-term variants against raw historical OHLCV data.

The script consumes only historical workload data and writes an experiment
artifact. It never changes formal candidates, ledgers, or broker state.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from aqsp.backtest.variant_account import (
    VariantExecutionRules,
    VariantOrder,
    VariantResult,
    prepare_variant_data,
    simulate_variant,
    variant_result_to_dict,
)
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import atomic_write_text


BASE_CASH = 100_000.0
TRAINING_BARS = 60
RECENT_ACTION_LIMIT = 8
MAX_HOLDING_DUPLICATES_IN_TOP = 3


@dataclass(frozen=True)
class VariantProfile:
    variant_id: str
    label: str
    lookback: int
    entry_return_pct: float
    max_bias_pct: float
    mode: str
    max_positions: int
    position_weight: float
    hypothesis: str


@dataclass(frozen=True)
class _Signal:
    date: str
    symbol: str
    side: str
    score: float
    reason: str


@dataclass(frozen=True)
class _RiskTier:
    key: str
    label: str
    entry_delta: float
    bias_delta: float
    max_positions: int


MODE_HYPOTHESES: dict[str, str] = {
    "trend": "均线之上的温和趋势延续优先，避免只追单日涨幅。",
    "pullback": "趋势中低乖离回踩承接，等待强势股降温后再纸面入场。",
    "breakout": "突破前高后若乖离可控，说明价格发现仍在延续。",
    "reversion": "短期跌离均线但未破流动性，博弈均值回归修复。",
    "low_vol": "低波动趋势比高波动冲高更稳定，先过滤异常震荡。",
    "relative_strength": "同池横向强度靠前的股票更容易延续资金关注。",
    "volume_breakout": "放量突破比单纯价格突破更能说明新增资金确认。",
    "atr_trend": "用 ATR 约束趋势，避免把异常波动误当成趋势。",
    "defensive_range": "低 ATR 区间向上更适合作为防守型纸面验证。",
    "macd_cross": "MACD 柱线由弱转强时，趋势拐点比静态均线更早暴露。",
    "kdj_rebound": "KDJ 低位回升用于捕捉超跌后的短线修复。",
    "volume_dry_pullback": "缩量回踩说明抛压衰减，趋势未坏时保留观察价值。",
}

MODE_LOOKBACKS: dict[str, tuple[int, ...]] = {
    "trend": (10, 15, 20),
    "pullback": (15, 20, 30),
    "breakout": (8, 10, 15),
    "reversion": (10, 15, 20),
    "low_vol": (20, 30, 40),
    "relative_strength": (10, 15, 20),
    "volume_breakout": (10, 15, 20),
    "atr_trend": (15, 20, 30),
    "defensive_range": (20, 30, 40),
    "macd_cross": (12, 20, 30),
    "kdj_rebound": (9, 15, 21),
    "volume_dry_pullback": (15, 20, 30),
}

BASE_ENTRY_RETURN: dict[str, float] = {
    "trend": 1.5,
    "pullback": 0.0,
    "breakout": 3.0,
    "reversion": 2.5,
    "low_vol": 0.8,
    "relative_strength": 2.0,
    "volume_breakout": 2.0,
    "atr_trend": 1.2,
    "defensive_range": 0.0,
    "macd_cross": 0.5,
    "kdj_rebound": 1.0,
    "volume_dry_pullback": 0.0,
}

BASE_MAX_BIAS: dict[str, float] = {
    "trend": 10.0,
    "pullback": 4.0,
    "breakout": 15.0,
    "reversion": 8.0,
    "low_vol": 6.0,
    "relative_strength": 12.0,
    "volume_breakout": 15.0,
    "atr_trend": 10.0,
    "defensive_range": 5.0,
    "macd_cross": 8.0,
    "kdj_rebound": 6.0,
    "volume_dry_pullback": 5.0,
}

RISK_TIERS = (
    _RiskTier("tight", "严选", 1.0, -2.0, 2),
    _RiskTier("balanced", "均衡", 0.0, 0.0, 3),
    _RiskTier("wide", "扩散", -0.5, 3.0, 4),
    _RiskTier("basket", "篮子", -1.0, 5.0, 5),
)


def _training_volatility_pct(frames: dict[str, pd.DataFrame]) -> float:
    """Estimate volatility from the first 60 bars only; never use evaluation data."""
    values: list[float] = []
    for frame in frames.values():
        closes = (
            pd.to_numeric(frame["close"], errors="coerce").dropna().head(TRAINING_BARS)
        )
        if len(closes) > 1:
            values.extend((closes.pct_change().dropna().abs() * 100.0).tolist())
    return float(pd.Series(values).median()) if values else 0.0


def generate_variant_profiles(
    frames: dict[str, pd.DataFrame],
) -> tuple[VariantProfile, ...]:
    """Create a deterministic grid plus point-in-time volatility mutations."""
    profiles: list[VariantProfile] = []
    for mode, lookbacks in MODE_LOOKBACKS.items():
        for lookback in lookbacks:
            for tier in RISK_TIERS:
                max_positions = tier.max_positions
                entry = max(0.0, BASE_ENTRY_RETURN[mode] + tier.entry_delta)
                bias = max(0.0, BASE_MAX_BIAS[mode] + tier.bias_delta)
                profiles.append(
                    VariantProfile(
                        variant_id=f"{mode}_lb{lookback}_{tier.key}",
                        label=f"{_mode_label(mode)}·{lookback}日·{tier.label}",
                        lookback=lookback,
                        entry_return_pct=entry,
                        max_bias_pct=bias,
                        mode=mode,
                        max_positions=max_positions,
                        position_weight=min(0.5, 1.0 / max_positions),
                        hypothesis=MODE_HYPOTHESES[mode],
                    )
                )
    volatility = _training_volatility_pct(frames)
    if volatility >= 2.5:
        profiles.extend(
            (
                VariantProfile(
                    "auto_high_vol_defensive",
                    "自动·高波防守",
                    15,
                    2.0,
                    5.0,
                    "low_vol",
                    2,
                    0.5,
                    MODE_HYPOTHESES["low_vol"],
                ),
                VariantProfile(
                    "auto_high_vol_reversal",
                    "自动·高波反转",
                    15,
                    4.0,
                    8.0,
                    "reversion",
                    2,
                    0.5,
                    MODE_HYPOTHESES["reversion"],
                ),
                VariantProfile(
                    "auto_high_vol_trend",
                    "自动·高波趋势",
                    25,
                    3.0,
                    7.0,
                    "atr_trend",
                    3,
                    1 / 3,
                    MODE_HYPOTHESES["atr_trend"],
                ),
                VariantProfile(
                    "auto_high_vol_breakout",
                    "自动·高波突破",
                    8,
                    5.0,
                    18.0,
                    "breakout",
                    2,
                    0.5,
                    MODE_HYPOTHESES["breakout"],
                ),
            )
        )
    else:
        profiles.extend(
            (
                VariantProfile(
                    "auto_low_vol_breakout",
                    "自动·低波突破",
                    15,
                    3.0,
                    10.0,
                    "breakout",
                    3,
                    1 / 3,
                    MODE_HYPOTHESES["breakout"],
                ),
                VariantProfile(
                    "auto_low_vol_pullback",
                    "自动·低波回踩",
                    25,
                    0.0,
                    3.0,
                    "pullback",
                    4,
                    0.25,
                    MODE_HYPOTHESES["pullback"],
                ),
                VariantProfile(
                    "auto_low_vol_defensive",
                    "自动·低波防守",
                    35,
                    0.5,
                    4.0,
                    "defensive_range",
                    4,
                    0.25,
                    MODE_HYPOTHESES["defensive_range"],
                ),
                VariantProfile(
                    "auto_low_vol_macd",
                    "自动·低波MACD",
                    20,
                    0.5,
                    6.0,
                    "macd_cross",
                    3,
                    1 / 3,
                    MODE_HYPOTHESES["macd_cross"],
                ),
            )
        )
    if len({profile.variant_id for profile in profiles}) != len(profiles):
        raise ValueError("variant_id 不得重复")
    return tuple(profiles)


def _mode_label(mode: str) -> str:
    return {
        "trend": "趋势",
        "pullback": "回踩",
        "breakout": "突破",
        "reversion": "反转",
        "low_vol": "低波",
        "relative_strength": "强势",
        "volume_breakout": "量突",
        "atr_trend": "ATR",
        "defensive_range": "防守",
        "macd_cross": "MACD",
        "kdj_rebound": "KDJ",
        "volume_dry_pullback": "缩量回踩",
    }.get(mode, mode)


def load_frames(
    db_path: Path,
    symbols: tuple[str, ...],
    start: str,
    end: str,
) -> dict[str, pd.DataFrame]:
    placeholders = ",".join("?" for _ in symbols)
    with sqlite3.connect(db_path) as conn:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(ohlcv)")}
    workload_filter = " AND workload = 'historical'" if "workload" in columns else ""
    name_column = ", name" if "name" in columns else ""
    query = f"""
        SELECT symbol, date{name_column}, open, high, low, close, volume, amount,
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
    indicator_cache: dict[int, dict[str, pd.DataFrame]] | None = None,
) -> tuple[VariantOrder, ...]:
    buy_candidates: dict[str, list[_Signal]] = defaultdict(list)
    sell_orders: list[VariantOrder] = []
    prepared_frames = (
        indicator_cache.get(profile.lookback, {}) if indicator_cache is not None else {}
    )
    for symbol, raw in frames.items():
        frame = prepared_frames.get(symbol)
        if frame is None:
            frame = _with_indicators(raw, profile.lookback)
        if len(frame) <= profile.lookback + 1:
            continue
        entry_mask, exit_mask, scores = _evaluate_frame(frame, profile)
        eligible_index = frame.index[profile.lookback : -1]
        next_dates = frame["date"].shift(-1)
        entry_indices = eligible_index[entry_mask.loc[eligible_index]]
        entry_rows = frame.loc[
            entry_indices,
            ("ret", "bias", "macd_hist", "kdj_j", "volume_ratio", "atr_pct"),
        ].assign(
            next_date=next_dates.loc[entry_indices], score=scores.loc[entry_indices]
        )
        for row in entry_rows.itertuples(index=False):
            next_date = str(row.next_date)
            score = float(row.score)
            buy_candidates[next_date].append(
                _Signal(
                    next_date,
                    symbol,
                    "buy",
                    score,
                    _entry_reason_values(
                        profile=profile,
                        ret=float(row.ret),
                        bias=float(row.bias),
                        macd_hist=float(row.macd_hist or 0.0),
                        kdj_j=float(row.kdj_j or 0.0),
                        volume_ratio=float(row.volume_ratio),
                        atr_pct=float(row.atr_pct),
                        score=score,
                    ),
                )
            )
        exit_indices = eligible_index[exit_mask.loc[eligible_index]]
        exit_rows = frame.loc[exit_indices, ("ret", "bias", "macd_hist")].assign(
            next_date=next_dates.loc[exit_indices]
        )
        for row in exit_rows.itertuples(index=False):
            sell_orders.append(
                VariantOrder(
                    str(row.next_date),
                    symbol,
                    "sell",
                    weight=1.0,
                    reason=_exit_reason_values(
                        profile=profile,
                        ret=float(row.ret),
                        bias=float(row.bias),
                        macd_hist=float(row.macd_hist or 0.0),
                    ),
                )
            )
    buy_orders = [
        VariantOrder(
            date,
            signal.symbol,
            "buy",
            weight=profile.position_weight,
            reason=signal.reason,
        )
        for date, signals in buy_candidates.items()
        for signal in sorted(signals, key=lambda item: (-item.score, item.symbol))[
            : profile.max_positions
        ]
    ]
    return tuple(
        sorted(
            (*sell_orders, *buy_orders),
            key=lambda order: (order.date, order.side, order.symbol),
        )
    )


def _with_indicators(raw: pd.DataFrame, lookback: int) -> pd.DataFrame:
    frame = raw.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    close = pd.to_numeric(frame["close"], errors="coerce")
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    frame["sma"] = close.rolling(lookback).mean()
    frame["ret"] = close.pct_change(lookback) * 100.0
    frame["bias"] = (close / frame["sma"] - 1.0) * 100.0
    frame["prior_high"] = high.rolling(lookback).max().shift(1)
    frame["volume_mean"] = volume.rolling(lookback).mean().shift(1)
    frame["volume_ratio"] = volume / frame["volume_mean"]
    frame["atr_pct"] = (high - low).rolling(14).mean() / close * 100.0
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    frame["macd_hist"] = (dif - dea) * 2.0
    low_min = low.rolling(9).min()
    high_max = high.rolling(9).max()
    kdj_range = (high_max - low_min).where((high_max - low_min) != 0)
    rsv = ((close - low_min) / kdj_range * 100.0).astype("float64")
    frame["kdj_k"] = rsv.ewm(com=2, adjust=False).mean()
    frame["kdj_d"] = frame["kdj_k"].ewm(com=2, adjust=False).mean()
    frame["kdj_j"] = 3.0 * frame["kdj_k"] - 2.0 * frame["kdj_d"]
    return frame


def build_indicator_cache(
    frames: dict[str, pd.DataFrame],
    profiles: tuple[VariantProfile, ...],
) -> dict[int, dict[str, pd.DataFrame]]:
    """Cache indicator frames by lookback; profiles share the same inputs."""
    lookbacks = sorted({profile.lookback for profile in profiles})
    return {
        lookback: {
            symbol: _with_indicators(raw, lookback) for symbol, raw in frames.items()
        }
        for lookback in lookbacks
    }


def _row_is_valid(row: pd.Series) -> bool:
    return all(
        pd.notna(row.get(key))
        for key in ("sma", "ret", "bias", "volume_ratio", "atr_pct")
    )


def _evaluate_frame(
    frame: pd.DataFrame, profile: VariantProfile
) -> tuple[pd.Series, pd.Series, pd.Series]:
    close = pd.to_numeric(frame["close"], errors="coerce")
    sma = pd.to_numeric(frame["sma"], errors="coerce")
    ret = pd.to_numeric(frame["ret"], errors="coerce")
    bias = pd.to_numeric(frame["bias"], errors="coerce")
    volume_ratio = pd.to_numeric(frame["volume_ratio"], errors="coerce")
    atr_pct = pd.to_numeric(frame["atr_pct"], errors="coerce")
    macd_hist = pd.to_numeric(frame.get("macd_hist"), errors="coerce").fillna(0.0)
    kdj_j = pd.to_numeric(frame.get("kdj_j"), errors="coerce").fillna(0.0)
    prior_high = pd.to_numeric(frame.get("prior_high"), errors="coerce").fillna(close)
    valid = (
        sma.notna()
        & ret.notna()
        & bias.notna()
        & volume_ratio.notna()
        & atr_pct.notna()
    )
    mode = profile.mode
    if mode == "reversion":
        entry = (
            (close < sma)
            & (ret <= -profile.entry_return_pct)
            & (bias >= -profile.max_bias_pct)
        )
        exit_signal = (close > sma) | (ret > 2.0)
        score = -ret + (60.0 - kdj_j).clip(lower=0.0) / 10.0
    elif mode == "volume_breakout":
        entry = (
            (close >= prior_high)
            & (volume_ratio >= 1.35)
            & (bias <= profile.max_bias_pct)
        )
        exit_signal = (close < sma) | (volume_ratio < 0.65)
        score = ret + volume_ratio * 2.0 + macd_hist
    elif mode == "breakout":
        entry = (
            (close >= prior_high)
            & (ret >= profile.entry_return_pct)
            & (bias <= profile.max_bias_pct)
        )
        exit_signal = (close < sma) | (ret < -2.0)
        score = ret + bias / 5.0
    elif mode == "atr_trend":
        entry = (close > sma) & (ret >= profile.entry_return_pct) & (atr_pct <= 6.0)
        exit_signal = (close < sma) | (ret < -2.0) | (atr_pct > 8.0)
        score = ret - atr_pct + macd_hist
    elif mode == "defensive_range":
        entry = (
            (close > sma)
            & (ret >= profile.entry_return_pct)
            & (bias <= profile.max_bias_pct)
            & (atr_pct <= 3.5)
        )
        exit_signal = (close < sma) | (atr_pct > 6.0)
        score = ret - atr_pct * 1.5
    elif mode == "low_vol":
        entry = (
            (close > sma)
            & (ret >= profile.entry_return_pct)
            & (bias <= profile.max_bias_pct)
            & (atr_pct <= 4.5)
        )
        exit_signal = (close < sma) | (atr_pct > 7.0)
        score = ret - atr_pct
    elif mode == "relative_strength":
        entry = (
            (close > sma)
            & (ret >= profile.entry_return_pct)
            & (bias <= profile.max_bias_pct)
            & (volume_ratio >= 0.8)
        )
        exit_signal = (close < sma) | (ret < -2.0)
        score = ret + volume_ratio
    elif mode == "macd_cross":
        entry = (
            (close > sma)
            & (macd_hist > 0)
            & (ret >= profile.entry_return_pct)
            & (bias <= profile.max_bias_pct)
        )
        exit_signal = (close < sma) | (macd_hist < 0)
        score = ret + macd_hist * 2.0
    elif mode == "kdj_rebound":
        entry = (
            (close > sma * 0.98)
            & (kdj_j >= 35.0)
            & (ret >= -profile.entry_return_pct)
            & (bias <= profile.max_bias_pct)
        )
        exit_signal = (close < sma * 0.97) | (kdj_j > 105.0)
        score = ret + kdj_j.clip(upper=100.0) / 20.0
    elif mode == "volume_dry_pullback":
        entry = (
            (close > sma * 0.98)
            & (bias.abs() <= profile.max_bias_pct)
            & (volume_ratio <= 0.9)
        )
        exit_signal = (close < sma * 0.96) | (volume_ratio > 1.8)
        score = -bias.abs() + ret / 2.0
    else:
        entry = (
            (close > sma)
            & (ret >= profile.entry_return_pct)
            & (bias <= profile.max_bias_pct)
        )
        exit_signal = (close < sma) | (ret < -2.0)
        score = ret - (bias - profile.max_bias_pct).clip(lower=0.0)
    return entry & valid, exit_signal & valid, score.fillna(0.0)


def _evaluate_row(
    row: pd.Series, profile: VariantProfile
) -> tuple[bool, bool, float, str]:
    close = float(row["close"])
    sma = float(row["sma"])
    ret = float(row["ret"])
    bias = float(row["bias"])
    volume_ratio = float(row["volume_ratio"])
    atr_pct = float(row["atr_pct"])
    macd_hist = float(row.get("macd_hist") or 0.0)
    kdj_j = float(row.get("kdj_j") or 0.0)
    prior_high = float(row.get("prior_high") or close)
    mode = profile.mode
    if mode == "reversion":
        entry = (
            close < sma
            and ret <= -profile.entry_return_pct
            and bias >= -profile.max_bias_pct
        )
        exit_signal = close > sma or ret > 2.0
        score = -ret + max(0.0, 60.0 - kdj_j) / 10.0
    elif mode == "volume_breakout":
        entry = (
            close >= prior_high
            and volume_ratio >= 1.35
            and bias <= profile.max_bias_pct
        )
        exit_signal = close < sma or volume_ratio < 0.65
        score = ret + volume_ratio * 2.0 + macd_hist
    elif mode == "breakout":
        entry = (
            close >= prior_high
            and ret >= profile.entry_return_pct
            and bias <= profile.max_bias_pct
        )
        exit_signal = close < sma or ret < -2.0
        score = ret + bias / 5.0
    elif mode == "atr_trend":
        entry = close > sma and ret >= profile.entry_return_pct and atr_pct <= 6.0
        exit_signal = close < sma or ret < -2.0 or atr_pct > 8.0
        score = ret - atr_pct + macd_hist
    elif mode == "defensive_range":
        entry = (
            close > sma
            and ret >= profile.entry_return_pct
            and bias <= profile.max_bias_pct
            and atr_pct <= 3.5
        )
        exit_signal = close < sma or atr_pct > 6.0
        score = ret - atr_pct * 1.5
    elif mode == "low_vol":
        entry = (
            close > sma
            and ret >= profile.entry_return_pct
            and bias <= profile.max_bias_pct
            and atr_pct <= 4.5
        )
        exit_signal = close < sma or atr_pct > 7.0
        score = ret - atr_pct
    elif mode == "relative_strength":
        entry = (
            close > sma
            and ret >= profile.entry_return_pct
            and bias <= profile.max_bias_pct
            and volume_ratio >= 0.8
        )
        exit_signal = close < sma or ret < -2.0
        score = ret + volume_ratio
    elif mode == "macd_cross":
        entry = (
            close > sma
            and macd_hist > 0
            and ret >= profile.entry_return_pct
            and bias <= profile.max_bias_pct
        )
        exit_signal = close < sma or macd_hist < 0
        score = ret + macd_hist * 2.0
    elif mode == "kdj_rebound":
        entry = (
            close > sma * 0.98
            and kdj_j >= 35.0
            and ret >= -profile.entry_return_pct
            and bias <= profile.max_bias_pct
        )
        exit_signal = close < sma * 0.97 or kdj_j > 105.0
        score = ret + min(kdj_j, 100.0) / 20.0
    elif mode == "volume_dry_pullback":
        entry = (
            close > sma * 0.98
            and abs(bias) <= profile.max_bias_pct
            and volume_ratio <= 0.9
        )
        exit_signal = close < sma * 0.96 or volume_ratio > 1.8
        score = -abs(bias) + ret / 2.0
    else:
        entry = (
            close > sma
            and ret >= profile.entry_return_pct
            and bias <= profile.max_bias_pct
        )
        exit_signal = close < sma or ret < -2.0
        score = ret - max(0.0, bias - profile.max_bias_pct)
    reason = _entry_reason(row, profile, score)
    return bool(entry), bool(exit_signal), float(score), reason


def _entry_reason(row: pd.Series, profile: VariantProfile, score: float) -> str:
    return _entry_reason_values(
        profile=profile,
        ret=float(row["ret"]),
        bias=float(row["bias"]),
        macd_hist=float(row.get("macd_hist") or 0.0),
        kdj_j=float(row.get("kdj_j") or 0.0),
        volume_ratio=float(row["volume_ratio"]),
        atr_pct=float(row["atr_pct"]),
        score=score,
    )


def _entry_reason_values(
    *,
    profile: VariantProfile,
    ret: float,
    bias: float,
    macd_hist: float,
    kdj_j: float,
    volume_ratio: float,
    atr_pct: float,
    score: float,
) -> str:
    return (
        f"{_mode_label(profile.mode)}触发：{profile.lookback}日收益{ret:+.1f}%，"
        f"乖离{bias:+.1f}%，MACD柱{macd_hist:+.2f}，"
        f"KDJ-J{kdj_j:.0f}，量比{volume_ratio:.2f}，"
        f"ATR{atr_pct:.1f}%，同日排序分{score:.2f}。"
    )


def _exit_reason(row: pd.Series, profile: VariantProfile) -> str:
    return _exit_reason_values(
        profile=profile,
        ret=float(row["ret"]),
        bias=float(row["bias"]),
        macd_hist=float(row.get("macd_hist") or 0.0),
    )


def _exit_reason_values(
    *,
    profile: VariantProfile,
    ret: float,
    bias: float,
    macd_hist: float,
) -> str:
    return (
        f"{_mode_label(profile.mode)}退出：收盘相对均线乖离{bias:+.1f}%，"
        f"{profile.lookback}日收益{ret:+.1f}%，MACD柱{macd_hist:+.2f}。"
    )


def run_suite(
    db_path: Path,
    symbols: tuple[str, ...],
    start: str,
    end: str,
) -> dict[str, object]:
    frames = load_frames(db_path, symbols, start, end)
    rules = VariantExecutionRules(initial_cash=BASE_CASH)
    profiles = generate_variant_profiles(frames)
    indicator_cache = build_indicator_cache(frames, profiles)
    symbol_names = _symbol_names(frames)
    previous_date = _previous_trade_date(frames, end)
    prepared_data = prepare_variant_data(frames)
    snapshot_dates = (previous_date,) if previous_date else ()
    results = []
    for profile in profiles:
        orders = build_orders(frames, profile, indicator_cache)
        result = simulate_variant(
            profile.variant_id,
            prepared_data,
            orders,
            rules=rules,
            snapshot_dates=snapshot_dates,
        )
        payload = variant_result_to_dict(result)
        _attach_holding_names(payload["holdings"], symbol_names)
        previous_holdings = _holding_dicts(result.snapshots.get(previous_date, ()))
        _attach_holding_names(previous_holdings, symbol_names)
        payload["label"] = profile.label
        payload["strategy_label"] = profile.label
        payload["strategy"] = _strategy_payload(profile)
        payload["strategy_signature"] = _strategy_signature(profile)
        payload["holdings_date"] = end
        payload["previous_holdings_date"] = previous_date or ""
        payload["previous_holdings"] = previous_holdings
        payload["recent_actions"] = _recent_actions(result, symbol_names)
        payload["adjustments"] = _adjustments(
            payload["holdings"], previous_holdings, result, symbol_names
        )
        payload["holdings_signature"] = _holdings_signature(payload["holdings"])
        results.append(payload)
    results = _diversity_ranked(results)
    training_volatility_pct = _training_volatility_pct(frames)
    return {
        "schema_version": "variant-suite-v2",
        "generated_at": now_shanghai().isoformat(timespec="seconds"),
        "data_mode": "historical_raw_unadjusted",
        "start_date": start,
        "end_date": end,
        "symbols": list(symbols),
        "initial_cash": BASE_CASH,
        "optimization": {
            "method": "deterministic_grid_training_window_volatility_v2",
            "training_bars": TRAINING_BARS,
            "training_volatility_pct": training_volatility_pct,
            "evaluation_only": True,
            "variant_count": len(results),
            "unique_strategy_signatures": len(
                {item["strategy_signature"] for item in results}
            ),
            "unique_holding_signatures": len(
                {item["holdings_signature"] for item in results}
            ),
            "selected_variant_id": results[0]["variant_id"] if results else "",
        },
        "execution_rules": {
            "t_plus_one": True,
            "lot_size": 100,
            "suspended_block": True,
            "limit_up_buy_block": True,
            "limit_down_sell_block": True,
            "fees_and_slippage": True,
            "raw_unadjusted_prices": True,
        },
        "variants": results,
    }


def _strategy_payload(profile: VariantProfile) -> dict[str, object]:
    return {
        "id": profile.variant_id,
        "lookback_days": profile.lookback,
        "entry_return_pct": profile.entry_return_pct,
        "max_bias_pct": profile.max_bias_pct,
        "mode": profile.mode,
        "max_positions": profile.max_positions,
        "position_weight": profile.position_weight,
        "hypothesis": profile.hypothesis,
    }


def _strategy_signature(profile: VariantProfile) -> str:
    return (
        f"{profile.mode}|lb={profile.lookback}|entry={profile.entry_return_pct:.2f}|"
        f"bias={profile.max_bias_pct:.2f}|slots={profile.max_positions}"
    )


def _symbol_names(frames: dict[str, pd.DataFrame]) -> dict[str, str]:
    names: dict[str, str] = {}
    for symbol, frame in frames.items():
        if "name" in frame.columns:
            series = frame["name"].dropna().astype(str).str.strip()
            if not series.empty and series.iloc[0]:
                names[symbol] = series.iloc[0]
    return names


def _attach_holding_names(
    holdings: list[dict[str, Any]], names: dict[str, str]
) -> None:
    for holding in holdings:
        symbol = str(holding.get("symbol", ""))
        holding["name"] = names.get(symbol, symbol)


def _previous_trade_date(frames: dict[str, pd.DataFrame], end: str) -> str:
    dates = sorted(
        {str(date)[:10] for frame in frames.values() for date in frame["date"]}
    )
    eligible = [date for date in dates if date < end]
    return eligible[-1] if eligible else ""


def _recent_actions(
    result: VariantResult, names: dict[str, str]
) -> list[dict[str, object]]:
    filled = [fill for fill in result.fills if fill.status == "filled"][
        -RECENT_ACTION_LIMIT:
    ]
    return [
        {
            "date": fill.date,
            "symbol": fill.symbol,
            "name": names.get(fill.symbol, fill.symbol),
            "side": fill.side,
            "quantity": fill.quantity,
            "price": fill.price,
            "reason": fill.reason or "规则触发但未记录细分原因",
        }
        for fill in filled
    ]


def _holding_dicts(holdings: object) -> list[dict[str, Any]]:
    return [
        {
            "symbol": holding.symbol,
            "quantity": holding.quantity,
            "average_price": holding.average_price,
            "last_price": holding.last_price,
            "market_value": holding.market_value,
            "unrealized_pnl": holding.unrealized_pnl,
        }
        for holding in holdings
    ]


def _adjustments(
    holdings: list[dict[str, Any]],
    previous_holdings: list[dict[str, Any]],
    result: VariantResult,
    names: dict[str, str],
) -> list[str]:
    current = {str(item["symbol"]): int(item.get("quantity") or 0) for item in holdings}
    previous = {
        str(item["symbol"]): int(item.get("quantity") or 0)
        for item in previous_holdings
    }
    reasons = _last_reasons(result)
    lines: list[str] = []
    for symbol in sorted(set(current) | set(previous)):
        name = names.get(symbol, symbol)
        before = previous.get(symbol, 0)
        after = current.get(symbol, 0)
        if before == after and after > 0:
            lines.append(
                f"保留 {symbol} {name}：昨日 {before} 股，今日 {after} 股；无换票，继续满足持仓。"
            )
        elif before == 0 and after > 0:
            lines.append(
                f"买入 {symbol} {name}：昨日无，今日 {after} 股；{reasons.get((symbol, 'buy'), '入场规则触发')}"
            )
        elif before > 0 and after == 0:
            lines.append(
                f"移出 {symbol} {name}：昨日 {before} 股，今日无；{reasons.get((symbol, 'sell'), '退出规则触发')}"
            )
        elif after > before:
            lines.append(
                f"加仓 {symbol} {name}：昨日 {before} 股，今日 {after} 股；{reasons.get((symbol, 'buy'), '入场规则再次触发')}"
            )
        elif after < before:
            lines.append(
                f"减仓 {symbol} {name}：昨日 {before} 股，今日 {after} 股；{reasons.get((symbol, 'sell'), '退出规则部分触发')}"
            )
    return lines[:RECENT_ACTION_LIMIT] or ["今日/昨日持仓无变化，未发生换票。"]


def _last_reasons(result: VariantResult) -> dict[tuple[str, str], str]:
    reasons: dict[tuple[str, str], str] = {}
    for fill in result.fills:
        if fill.status == "filled" and fill.reason:
            reasons[(fill.symbol, fill.side)] = fill.reason
    return reasons


def _holdings_signature(holdings: list[dict[str, Any]]) -> str:
    if not holdings:
        return "empty"
    return "|".join(
        f"{item.get('symbol')}:{int(item.get('quantity') or 0)}"
        for item in sorted(holdings, key=lambda value: str(value.get("symbol", "")))
    )


def _diversity_ranked(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_results = sorted(
        results, key=lambda item: float(item["final_equity"]), reverse=True
    )
    duplicate_counts: Counter[str] = Counter()
    primary: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    for item in sorted_results:
        signature = str(item.get("holdings_signature", "empty"))
        if duplicate_counts[signature] < MAX_HOLDING_DUPLICATES_IN_TOP:
            primary.append(item)
            duplicate_counts[signature] += 1
        else:
            overflow.append(item)
    ranked = [*primary, *overflow]
    for rank, item in enumerate(ranked, start=1):
        item["rank"] = rank
    return ranked


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = run_suite(
        args.db, tuple(dict.fromkeys(args.symbols)), args.start, args.end
    )
    atomic_write_text(
        args.output, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        f"variant suite completed: variants={len(payload['variants'])} "
        f"symbols={len(payload['symbols'])} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
