from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from typing import Any

import pandas as pd

from aqsp.core.errors import FreshnessError
from aqsp.core.time import SHANGHAI_TZ, now_shanghai, today_shanghai
from aqsp.data.trading_calendar import load_optional_trade_calendar, trading_day_lag
from aqsp.data.source import REQUIRED_OHLCV_COLUMNS
from aqsp.data.source_readiness import WorkloadId

REQUIRED_REALTIME_QUOTE_FIELDS = frozenset(
    {"price", "bid1", "ask1", "volume", "amount", "ts"}
)


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
        value = _latest_frame_trade_day(frame)
        if value is not None:
            dates.append(value)
    return max(dates) if dates else None


def _trade_day_from_value(value: object) -> date | None:
    """Return a date in Shanghai time from date-like source values."""

    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    if parsed.tzinfo is None:
        return parsed.date()
    return parsed.tz_convert(SHANGHAI_TZ).date()


def _latest_frame_trade_day(frame: pd.DataFrame) -> date | None:
    values = (_trade_day_from_value(value) for value in frame["date"])
    valid = [value for value in values if value is not None]
    return max(valid) if valid else None


def assert_fresh_data(
    frames: dict[str, pd.DataFrame],
    max_lag_days: int,
    *,
    workload: WorkloadId | None = None,
) -> date:
    max_lag_days = _effective_max_lag_days(max_lag_days, workload=workload)
    if not frames:
        _raise_freshness("ALL", "no valid market data loaded")

    latest = latest_trade_date(frames)
    if latest is None:
        _raise_freshness("ALL", "no valid market data loaded")

    today = today_shanghai()
    symbol_latest_dates = [
        latest_symbol_date
        for frame in frames.values()
        if not frame.empty
        and "date" in frame.columns
        and (latest_symbol_date := _latest_frame_trade_day(frame)) is not None
    ]
    calendar_df = (
        load_optional_trade_calendar(
            min(symbol_latest_dates) - timedelta(days=31),
            today,
        )
        if symbol_latest_dates
        else None
    )

    if latest > today:
        _raise_freshness(
            "ALL",
            "market data timestamp is in the future: "
            f"latest={latest.isoformat()}, reference={today.isoformat()}",
        )

    lag = trading_day_lag(latest, today, calendar_df=calendar_df)
    if lag > max_lag_days:
        stale_symbols = []
        for symbol, frame in frames.items():
            if frame.empty or "date" not in frame.columns:
                continue
            symbol_latest = _latest_frame_trade_day(frame)
            if symbol_latest is None:
                continue
            symbol_lag = trading_day_lag(
                symbol_latest,
                today,
                calendar_df=calendar_df,
            )
            if symbol_lag > max_lag_days:
                stale_symbols.append(f"{symbol}:{symbol_latest.isoformat()}")
        _raise_freshness(
            ",".join(stale_symbols) or "ALL",
            "market data is stale: "
            f"latest={latest.isoformat()}, lag={lag} days, max={max_lag_days}",
            days_lag=lag,
            max_allowed=max_lag_days,
        )

    stale_symbols = []
    max_symbol_lag = 0
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
        symbol_latest = _latest_frame_trade_day(frame)
        if symbol_latest is None:
            _raise_freshness(symbol, f"no valid trade date for {symbol}")
        symbol_lag = trading_day_lag(symbol_latest, today, calendar_df=calendar_df)
        max_symbol_lag = max(max_symbol_lag, symbol_lag)
        if symbol_lag > max_lag_days:
            stale_symbols.append(f"{symbol}:{symbol_latest.isoformat()}")
    if stale_symbols:
        _raise_freshness(
            ",".join(stale_symbols),
            "market data contains stale symbols: "
            f"latest={latest.isoformat()}, stale={','.join(stale_symbols)}, "
            f"max={max_lag_days}",
            days_lag=max_symbol_lag,
            max_allowed=max_lag_days,
        )
    return latest


def assert_live_short_fresh_data(
    frames: dict[str, pd.DataFrame],
    max_lag_days: int = 1,
) -> date:
    return assert_fresh_data(
        frames,
        max_lag_days,
        workload="live_short",
    )


def _effective_max_lag_days(
    max_lag_days: int,
    *,
    workload: WorkloadId | None,
) -> int:
    normalized = max(0, int(max_lag_days))
    if workload == "live_short":
        return min(normalized, 1)
    return normalized


def validate_realtime_quotes(
    quotes: dict[str, dict[str, Any]],
    *,
    max_age_seconds: int = 120,
    require_vendor_timestamp: bool = False,
    max_future_seconds: int = 5,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    if not quotes:
        _raise_freshness("ALL", "no realtime quotes loaded")
    current = now or now_shanghai()
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)

    for symbol, quote in quotes.items():
        missing = REQUIRED_REALTIME_QUOTE_FIELDS - set(quote)
        if missing:
            _raise_freshness(
                symbol,
                "realtime quote schema missing fields for "
                f"{symbol}: {', '.join(sorted(missing))}",
            )
        for field in ("price", "bid1", "ask1"):
            try:
                value = float(quote[field])
            except (TypeError, ValueError):
                _raise_freshness(
                    symbol,
                    f"realtime quote field {field} is not numeric for {symbol}",
                )
            if not math.isfinite(value) or value <= 0:
                _raise_freshness(
                    symbol,
                    f"realtime quote field {field} must be positive for {symbol}",
                )
        for field in ("volume", "amount"):
            try:
                value = float(quote[field])
            except (TypeError, ValueError):
                _raise_freshness(
                    symbol,
                    f"realtime quote field {field} is not numeric for {symbol}",
                )
            if not math.isfinite(value) or value < 0:
                _raise_freshness(
                    symbol,
                    f"realtime quote field {field} must be finite and non-negative for {symbol}",
                )
        vendor_ts = str(quote.get("vendor_ts") or "").strip()
        if require_vendor_timestamp and (
            not vendor_ts or str(quote.get("timestamp_source") or "") != "vendor"
        ):
            _raise_freshness(
                symbol,
                "realtime quote vendor timestamp missing or unverifiable",
            )
        ts = _parse_quote_ts(vendor_ts or quote.get("ts"))
        age_seconds = (current - ts).total_seconds()
        if age_seconds < -max(0, int(max_future_seconds)):
            _raise_freshness(
                symbol,
                "realtime quote timestamp is in the future: "
                f"symbol={symbol}, age={age_seconds:.0f}s, "
                f"max_future={max_future_seconds}s",
            )
        if age_seconds < 0:
            age_seconds = 0
        if age_seconds > max_age_seconds:
            _raise_freshness(
                symbol,
                "realtime quote is stale: "
                f"symbol={symbol}, age={age_seconds:.0f}s, "
                f"max={max_age_seconds}s",
                days_lag=max(1, int(age_seconds // 86_400)),
                max_allowed=0,
            )
    return quotes


def _parse_quote_ts(value: Any) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        _raise_freshness("ALL", "realtime quote ts missing")
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        _raise_freshness("ALL", f"realtime quote ts invalid: {raw}")
    if parsed.tzinfo is None:
        _raise_freshness("ALL", f"realtime quote ts missing timezone: {raw}")
    return parsed.astimezone(SHANGHAI_TZ)
