from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.core.time import (
    get_next_trading_day,
    get_previous_trading_day,
    is_trading_day,
)
from aqsp.data.tushare_pit import (
    TusharePitClient,
    is_open_day_in_calendar,
    next_trade_date_from_calendar,
    previous_trade_date_from_calendar,
)


@dataclass(frozen=True)
class TradingCalendarWindow:
    # 覆盖春节/国庆等长休市窗口，避免只查到周末附近。
    before_days: int = 31
    after_days: int = 31


def load_optional_trade_calendar(
    start: date,
    end: date,
    *,
    exchange: str = "SSE",
    client: TusharePitClient | None = None,
) -> pd.DataFrame | None:
    """Load Tushare trade calendar when available, otherwise return None."""
    try:
        pit_client = client or TusharePitClient()
    except (RuntimeError, ValueError):
        return None
    try:
        return pit_client.fetch_trade_calendar(start, end, exchange=exchange)
    except DataError:
        return None


def resolve_is_trading_day(
    target: date,
    *,
    calendar_df: pd.DataFrame | None = None,
    exchange: str = "SSE",
    window: TradingCalendarWindow | None = None,
) -> bool:
    runtime_calendar = (
        calendar_df
        if calendar_df is not None
        else _load_runtime_calendar(
            target,
            exchange=exchange,
            window=window,
        )
    )
    if runtime_calendar is not None:
        return is_open_day_in_calendar(runtime_calendar, target)
    return is_trading_day(target)


def resolve_previous_trading_day(
    target: date,
    *,
    calendar_df: pd.DataFrame | None = None,
    exchange: str = "SSE",
    window: TradingCalendarWindow | None = None,
) -> date:
    runtime_calendar = (
        calendar_df
        if calendar_df is not None
        else _load_runtime_calendar(
            target,
            exchange=exchange,
            window=window,
        )
    )
    if runtime_calendar is not None:
        try:
            return previous_trade_date_from_calendar(runtime_calendar, target)
        except DataError:
            pass
    return get_previous_trading_day(target)


def resolve_next_trading_day(
    target: date,
    *,
    calendar_df: pd.DataFrame | None = None,
    exchange: str = "SSE",
    window: TradingCalendarWindow | None = None,
) -> date:
    runtime_calendar = (
        calendar_df
        if calendar_df is not None
        else _load_runtime_calendar(
            target,
            exchange=exchange,
            window=window,
        )
    )
    if runtime_calendar is not None:
        try:
            return next_trade_date_from_calendar(runtime_calendar, target)
        except DataError:
            pass
    return get_next_trading_day(target)


def trading_day_lag(
    latest: date,
    reference_day: date,
    *,
    calendar_df: pd.DataFrame | None = None,
    exchange: str = "SSE",
) -> int:
    runtime_calendar = (
        calendar_df
        if calendar_df is not None
        else load_optional_trade_calendar(
            latest - timedelta(days=31),
            reference_day + timedelta(days=31),
            exchange=exchange,
        )
    )
    anchor = (
        reference_day
        if resolve_is_trading_day(
            reference_day,
            calendar_df=runtime_calendar,
            exchange=exchange,
        )
        else resolve_previous_trading_day(
            reference_day,
            calendar_df=runtime_calendar,
            exchange=exchange,
        )
    )
    if latest >= anchor:
        return 0
    if runtime_calendar is not None:
        normalized = runtime_calendar.copy()
        normalized["cal_date"] = pd.to_datetime(
            normalized["cal_date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        normalized["is_open"] = (
            pd.to_numeric(normalized["is_open"], errors="coerce").fillna(0).astype(int)
        )
        open_days = normalized[
            (normalized["is_open"] == 1)
            & (normalized["cal_date"] > latest.isoformat())
            & (normalized["cal_date"] <= anchor.isoformat())
        ]
        return int(open_days.shape[0])

    lag = 0
    cursor = latest
    while cursor < anchor:
        cursor = get_next_trading_day(cursor)
        lag += 1
    return lag


def _load_runtime_calendar(
    target: date,
    *,
    exchange: str,
    window: TradingCalendarWindow | None,
) -> pd.DataFrame | None:
    runtime_window = window or TradingCalendarWindow()
    return load_optional_trade_calendar(
        target - timedelta(days=runtime_window.before_days),
        target + timedelta(days=runtime_window.after_days),
        exchange=exchange,
    )
