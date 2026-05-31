from __future__ import annotations

from datetime import date

import pandas as pd

from aqsp.core.errors import FreshnessError
from aqsp.core.time import today_shanghai
from aqsp.data.source import REQUIRED_OHLCV_COLUMNS


class MarketDataFreshnessError(FreshnessError, RuntimeError):
    def __init__(
        self,
        symbol: str,
        days_lag: int,
        max_allowed: int,
        reason: str,
    ) -> None:
        super().__init__(symbol, days_lag, max_allowed)
        self.reason = reason
        self.args = (reason,)


def _raise_freshness(
    symbol: str,
    reason: str,
    *,
    days_lag: int = 0,
    max_allowed: int = 0,
) -> None:
    raise MarketDataFreshnessError(
        symbol=symbol,
        days_lag=days_lag,
        max_allowed=max_allowed,
        reason=reason,
    )


def latest_trade_date(frames: dict[str, pd.DataFrame]) -> date | None:
    dates: list[date] = []
    for frame in frames.values():
        if frame.empty or "date" not in frame.columns:
            continue
        value = pd.to_datetime(frame["date"], errors="coerce").dropna().max()
        if pd.notna(value):
            dates.append(value.date())
    return max(dates) if dates else None


def assert_fresh_data(frames: dict[str, pd.DataFrame], max_lag_days: int) -> date:
    if not frames:
        _raise_freshness("ALL", "no valid market data loaded")

    latest = latest_trade_date(frames)
    if latest is None:
        _raise_freshness("ALL", "no valid market data loaded")

    lag = (today_shanghai() - latest).days
    if lag > max_lag_days:
        stale_symbols = []
        for symbol, frame in frames.items():
            if frame.empty or "date" not in frame.columns:
                continue
            symbol_latest = (
                pd.to_datetime(frame["date"], errors="coerce").dropna().max()
            )
            if pd.isna(symbol_latest):
                continue
            symbol_lag = (today_shanghai() - symbol_latest.date()).days
            if symbol_lag > max_lag_days:
                stale_symbols.append(f"{symbol}:{symbol_latest.date().isoformat()}")
        _raise_freshness(
            ",".join(stale_symbols) or "ALL",
            "market data is stale: "
            f"latest={latest.isoformat()}, lag={lag} days, max={max_lag_days}",
            days_lag=lag,
            max_allowed=max_lag_days,
        )

    for symbol, frame in frames.items():
        if frame.empty:
            _raise_freshness(
                symbol,
                f"no valid market data loaded: empty market data frame: {symbol}",
            )
        missing = REQUIRED_OHLCV_COLUMNS - set(frame.columns)
        if missing:
            missing_text = ", ".join(sorted(missing))
            _raise_freshness(
                symbol,
                f"market data schema missing columns for {symbol}: {missing_text}",
            )
        value = pd.to_datetime(frame["date"], errors="coerce").dropna().max()
        if pd.isna(value):
            _raise_freshness(symbol, f"no valid trade date for {symbol}")
    return latest
