from __future__ import annotations

from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


_A_SHARE_HOLIDAYS: dict[int, set[date]] = {
    2026: {
        date(2026, 1, 1),
        date(2026, 2, 16),
        date(2026, 2, 17),
        date(2026, 2, 18),
        date(2026, 2, 19),
        date(2026, 2, 20),
        date(2026, 4, 6),
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 6, 19),
        date(2026, 9, 25),
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 5),
        date(2026, 10, 6),
        date(2026, 10, 7),
    },
}


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
    explicit_holidays = _A_SHARE_HOLIDAYS.get(d.year, set())
    if explicit_holidays:
        return d not in explicit_holidays
    holidays = {
        date(d.year, 1, 1),
        date(d.year, 5, 1),
        date(d.year, 10, 1),
    }
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
