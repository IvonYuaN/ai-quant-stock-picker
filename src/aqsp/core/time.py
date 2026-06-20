from __future__ import annotations

import json
from datetime import datetime, date
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_BASIC_CALENDAR_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "trading_holidays.json"
)


@lru_cache(maxsize=1)
def _load_basic_trading_calendar() -> tuple[frozenset[date], frozenset[date]]:
    try:
        payload = json.loads(_BASIC_CALENDAR_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return frozenset(), frozenset()

    holidays = {
        date.fromisoformat(str(item))
        for item in payload.get("holidays", [])
        if str(item).strip()
    }
    makeup_workdays = {
        date.fromisoformat(str(item))
        for item in payload.get("makeup_workdays", [])
        if str(item).strip()
    }
    return frozenset(holidays), frozenset(makeup_workdays)


def _is_basic_trading_day(d: date) -> bool:
    holidays, makeup_workdays = _load_basic_trading_calendar()
    if d in makeup_workdays:
        return True
    if d.weekday() >= 5:
        return False
    return d not in holidays


def _get_basic_previous_trading_day(d: date) -> date:
    cursor = d - date.resolution
    while not _is_basic_trading_day(cursor):
        cursor -= date.resolution
    return cursor


def _get_basic_next_trading_day(d: date) -> date:
    cursor = d + date.resolution
    while not _is_basic_trading_day(cursor):
        cursor += date.resolution
    return cursor


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
    from aqsp.data.trading_calendar import resolve_is_trading_day

    return resolve_is_trading_day(d)


def get_previous_trading_day(d: date | None = None) -> date:
    if d is None:
        d = today_shanghai()
    from aqsp.data.trading_calendar import resolve_previous_trading_day

    return resolve_previous_trading_day(d)


def get_next_trading_day(d: date | None = None) -> date:
    if d is None:
        d = today_shanghai()
    from aqsp.data.trading_calendar import resolve_next_trading_day

    return resolve_next_trading_day(d)


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
