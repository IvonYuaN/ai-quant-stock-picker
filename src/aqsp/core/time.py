from __future__ import annotations

from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def now_shanghai() -> datetime:
    return datetime.now(tz=SHANGHAI_TZ)


def today_shanghai() -> date:
    return now_shanghai().date()


def to_shanghai(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SHANGHAI_TZ)
    return dt.astimezone(SHANGHAI_TZ)


def to_iso8601(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def parse_iso8601(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(SHANGHAI_TZ)


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    holidays = [
        date(d.year, 1, 1),
        date(d.year, 5, 1),
        date(d.year, 10, 1),
    ]
    return d not in holidays


def get_previous_trading_day(d: date | None = None) -> date:
    if d is None:
        d = today_shanghai()
    d = d - timedelta(days=1)
    while not is_trading_day(d):
        d = d - timedelta(days=1)
    return d


def get_next_trading_day(d: date | None = None) -> date:
    if d is None:
        d = today_shanghai()
    d = d + timedelta(days=1)
    while not is_trading_day(d):
        d = d + timedelta(days=1)
    return d


def market_hours(dt: datetime) -> tuple[datetime, datetime]:
    dt = to_shanghai(dt)
    open_time = dt.replace(hour=9, minute=30, second=0, microsecond=0)
    close_time = dt.replace(hour=15, minute=0, second=0, microsecond=0)
    return open_time, close_time


def is_market_open(dt: datetime | None = None) -> bool:
    if dt is None:
        dt = now_shanghai()
    dt = to_shanghai(dt)
    if not is_trading_day(dt.date()):
        return False
    open_time, close_time = market_hours(dt)
    return open_time <= dt <= close_time
