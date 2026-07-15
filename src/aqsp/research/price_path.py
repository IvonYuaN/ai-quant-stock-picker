from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

import pandas as pd

from aqsp.core.errors import DataError


PRICE_PATH_COLUMNS = ("date", "close", "high", "low", "volume")


@dataclass(frozen=True)
class PricePathWindowSummary:
    window: int
    start_date: str
    end_date: str
    return_pct: float
    max_drawdown_pct: float
    volatility_pct: float
    close_position: float
    volume_ratio: float


def summarize_price_path(
    frame: pd.DataFrame,
    windows: tuple[int, ...] = (5, 10, 20),
) -> tuple[PricePathWindowSummary, ...]:
    _validate_price_path_frame(frame)
    normalized = frame.copy().sort_values("date").reset_index(drop=True)
    normalized["close"] = pd.to_numeric(normalized["close"], errors="coerce")
    normalized["high"] = pd.to_numeric(normalized["high"], errors="coerce")
    normalized["low"] = pd.to_numeric(normalized["low"], errors="coerce")
    normalized["volume"] = pd.to_numeric(normalized["volume"], errors="coerce")
    if normalized[list(PRICE_PATH_COLUMNS[1:])].isna().any().any():
        raise DataError("price path contains invalid numeric values")
    if (normalized["close"] <= 0).any() or (normalized["volume"] < 0).any():
        raise DataError("price path contains non-positive close or negative volume")
    summaries: list[PricePathWindowSummary] = []
    for window in windows:
        if window <= 1 or len(normalized) < window:
            continue
        summaries.append(_summarize_window(normalized.tail(window), int(window)))
    return tuple(summaries)


def _validate_price_path_frame(frame: pd.DataFrame) -> None:
    missing = set(PRICE_PATH_COLUMNS) - set(frame.columns)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise DataError(f"price path missing columns: {missing_text}")
    if frame.empty:
        raise DataError("price path frame is empty")


def _summarize_window(
    window_frame: pd.DataFrame,
    window: int,
) -> PricePathWindowSummary:
    close = window_frame["close"].astype(float)
    high = window_frame["high"].astype(float)
    low = window_frame["low"].astype(float)
    volume = window_frame["volume"].astype(float)
    start_close = float(close.iloc[0])
    end_close = float(close.iloc[-1])
    cumulative_high = close.cummax()
    drawdowns = close / cumulative_high - 1.0
    prior_volume = volume.iloc[:-1]
    avg_prior_volume = float(prior_volume.mean()) if not prior_volume.empty else 0.0
    high_low_range = float(high.max() - low.min())
    close_position = (
        0.5 if high_low_range == 0 else float((end_close - low.min()) / high_low_range)
    )
    return PricePathWindowSummary(
        window=window,
        start_date=str(window_frame["date"].iloc[0]),
        end_date=str(window_frame["date"].iloc[-1]),
        return_pct=(end_close / start_close - 1.0) * 100.0,
        max_drawdown_pct=float(drawdowns.min()) * 100.0,
        volatility_pct=float(close.pct_change().dropna().std(ddof=0))
        * sqrt(window)
        * 100.0,
        close_position=max(0.0, min(1.0, close_position)),
        volume_ratio=0.0
        if avg_prior_volume <= 0
        else float(volume.iloc[-1]) / avg_prior_volume,
    )
