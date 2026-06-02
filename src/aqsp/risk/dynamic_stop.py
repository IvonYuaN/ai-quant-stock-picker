from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


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
    atr_multiplier: float = 2.0,
) -> DynamicStopResult:
    if df.empty or "close" not in df.columns:
        return DynamicStopResult(
            symbol=symbol,
            atr_stop=entry_price * 0.95,
            trailing_stop=entry_price * 0.95,
            support_level=entry_price * 0.95,
            recommended_stop=entry_price * 0.95,
            method="fallback_5pct",
        )

    atr = compute_atr(df)
    atr_stop = round(entry_price - atr_multiplier * atr, 2)

    recent_lows = df["low"].tail(5)
    trailing_stop = round(float(recent_lows.max()) * 0.97, 2)

    support_level = round(_find_support_level(df), 2)

    recommended = max(atr_stop, trailing_stop, support_level)
    recommended = round(recommended, 2)

    if recommended == atr_stop:
        method = "atr"
    elif recommended == trailing_stop:
        method = "trailing"
    else:
        method = "support"

    return DynamicStopResult(
        symbol=symbol,
        atr_stop=atr_stop,
        trailing_stop=trailing_stop,
        support_level=support_level,
        recommended_stop=recommended,
        method=method,
    )
