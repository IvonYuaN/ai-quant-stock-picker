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
def _load_basic_trading_calendar() -> frozenset[date]:
    """静态休市日历：``config/trading_holidays.json`` 的 ``holidays``。

    ⚠️ 该文件只表达「休市日」，**不含**国务院的「调休上班日」。调休上班日
    一律落在周末，而沪深交易所周末一律休市（交易所公告原文：「2月14日
    （星期六）、2月28日（星期六）为周末休市」），因此调休上班日**不是**
    交易日。2026-10 之前本文件曾带 ``makeup_workdays`` 并被无条件判成
    交易日，属事实错误（issue #312），已整体移除。
    """
    try:
        payload = json.loads(_BASIC_CALENDAR_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return frozenset()

    return frozenset(
        date.fromisoformat(str(item))
        for item in payload.get("holidays", [])
        if str(item).strip()
    )


def _is_basic_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in _load_basic_trading_calendar()


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


def latest_completed_trading_day(dt: datetime | None = None) -> date:
    """Return the latest trading day whose regular session has closed."""
    current = to_shanghai(dt or now_shanghai())
    if not is_trading_day(current.date()):
        return get_previous_trading_day(current.date())
    _, close_time = market_hours(current)
    if current >= close_time:
        return current.date()
    return get_previous_trading_day(current.date())


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
