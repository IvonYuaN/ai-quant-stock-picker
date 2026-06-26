from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from aqsp.strategies.thresholds import load_thresholds


@dataclass(frozen=True)
class DynamicStopResult:
    symbol: str
    atr_stop: float
    trailing_stop: float
    support_level: float
    recommended_stop: float
    method: str


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        if len(df) < 2:
            return 0.0
        period = max(len(df) - 1, 1)

    high = df["high"].values
    low = df["low"].values
    prev_close = df["close"].shift(1).values

    tr1 = high - low
    tr2 = np.abs(high - prev_close)
    tr3 = np.abs(low - prev_close)
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    tr[0] = tr1[0]

    tr_series = pd.Series(tr)
    atr = float(tr_series.rolling(window=period, min_periods=period).mean().iloc[-1])

    if np.isnan(atr):
        atr = float(tr_series.mean())

    return atr


def _find_support_level(df: pd.DataFrame, lookback: int = 20) -> float:
    lows = df["low"].tail(lookback).values
    if len(lows) < 3:
        return float(lows.min()) if len(lows) > 0 else 0.0

    local_mins: list[float] = []
    for i in range(1, len(lows) - 1):
        if lows[i] <= lows[i - 1] and lows[i] <= lows[i + 1]:
            local_mins.append(float(lows[i]))

    if local_mins:
        return float(np.median(local_mins))
    return float(lows.min())


def compute_dynamic_stop(
    df: pd.DataFrame,
    entry_price: float,
    symbol: str = "",
    atr_period: int | None = None,
    atr_multiplier: float | None = None,
    fallback_pct: float | None = None,
    recent_low_days: int | None = None,
    trailing_pct: float | None = None,
    support_lookback: int | None = None,
) -> DynamicStopResult:
    if any(
        value is None
        for value in (
            atr_period,
            atr_multiplier,
            fallback_pct,
            recent_low_days,
            trailing_pct,
            support_lookback,
        )
    ):
        risk = load_thresholds().risk
        atr_period = risk.dynamic_stop_atr_period if atr_period is None else atr_period
        atr_multiplier = (
            risk.dynamic_stop_atr_multiplier
            if atr_multiplier is None
            else atr_multiplier
        )
        fallback_pct = (
            risk.dynamic_stop_fallback_pct if fallback_pct is None else fallback_pct
        )
        recent_low_days = (
            risk.dynamic_stop_recent_low_days
            if recent_low_days is None
            else recent_low_days
        )
        trailing_pct = (
            risk.dynamic_stop_trailing_pct if trailing_pct is None else trailing_pct
        )
        support_lookback = (
            risk.dynamic_stop_support_lookback
            if support_lookback is None
            else support_lookback
        )
    atr_period = int(atr_period)
    atr_multiplier = float(atr_multiplier)
    fallback_pct = float(fallback_pct)
    recent_low_days = int(recent_low_days)
    trailing_pct = float(trailing_pct)
    support_lookback = int(support_lookback)

    if df.empty or "close" not in df.columns:
        fallback_stop = entry_price * (1 - fallback_pct)
        return DynamicStopResult(
            symbol=symbol,
            atr_stop=fallback_stop,
            trailing_stop=fallback_stop,
            support_level=fallback_stop,
            recommended_stop=fallback_stop,
            method=f"fallback_{fallback_pct:.0%}",
        )

    atr = compute_atr(df, period=max(1, int(atr_period)))
    atr_stop = round(entry_price - atr_multiplier * atr, 2)

    recent_lows = df["low"].tail(max(1, int(recent_low_days)))
    trailing_stop = round(float(recent_lows.max()) * (1 - trailing_pct), 2)

    support_level = round(_find_support_level(df, lookback=support_lookback), 2)

    recommended = max(atr_stop, trailing_stop, support_level)
    recommended = round(recommended, 2)
    latest_close = float(pd.to_numeric(df["close"], errors="coerce").dropna().iloc[-1])
    stop_ceiling = round(min(float(entry_price), latest_close) * 0.999, 2)
    if recommended >= min(float(entry_price), latest_close):
        fallback_stop = round(min(float(entry_price), latest_close) * (1 - fallback_pct), 2)
        valid_stops = [
            value
            for value in (atr_stop, trailing_stop, support_level, fallback_stop)
            if value < min(float(entry_price), latest_close)
        ]
        recommended = round(max(valid_stops), 2) if valid_stops else stop_ceiling

    if recommended == atr_stop:
        method = "atr"
    elif recommended == trailing_stop:
        method = "trailing"
    elif recommended == support_level:
        method = "support"
    else:
        method = "fallback_guard"

    return DynamicStopResult(
        symbol=symbol,
        atr_stop=atr_stop,
        trailing_stop=trailing_stop,
        support_level=support_level,
        recommended_stop=recommended,
        method=method,
    )
