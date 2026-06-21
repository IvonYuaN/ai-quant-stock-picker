from __future__ import annotations

from datetime import datetime, date

from aqsp.core.time import (
    now_shanghai,
    today_shanghai,
    to_shanghai,
    to_iso8601,
    parse_iso8601,
    is_trading_day,
    get_previous_trading_day,
    get_next_trading_day,
    market_hours,
    is_market_open,
    SHANGHAI_TZ,
)


def test_now_shanghai_has_timezone():
    dt = now_shanghai()
    assert dt.tzinfo is not None
    assert str(dt.tzinfo) == "Asia/Shanghai"


def test_today_shanghai_returns_date():
    d = today_shanghai()
    assert isinstance(d, date)


def test_to_shanghai_converts_naive():
    naive = datetime(2026, 5, 27, 10, 30)
    aware = to_shanghai(naive)
    assert aware.tzinfo == SHANGHAI_TZ
    assert aware.hour == 10


def test_to_shanghai_converts_other_tz():
    utc_dt = datetime(2026, 5, 27, 2, 30, tzinfo=SHANGHAI_TZ)
    result = to_shanghai(utc_dt)
    assert result.tzinfo == SHANGHAI_TZ


def test_to_iso8601_format():
    dt = datetime(2026, 5, 27, 10, 30, 45, tzinfo=SHANGHAI_TZ)
    iso_str = to_iso8601(dt)
    assert "2026-05-27T10:30:45+08:00" in iso_str


def test_parse_iso8601():
    iso_str = "2026-05-27T10:30:45+08:00"
    dt = parse_iso8601(iso_str)
    assert dt.year == 2026
    assert dt.month == 5
    assert dt.day == 27
    assert dt.hour == 10
    assert dt.tzinfo == SHANGHAI_TZ


def test_is_trading_day_weekdays():
    assert is_trading_day(date(2026, 5, 27))
    assert is_trading_day(date(2026, 5, 28))


def test_is_trading_day_weekends():
    assert not is_trading_day(date(2026, 5, 31))
    assert not is_trading_day(date(2026, 6, 7))


def test_is_trading_day_a_share_2026_holidays():
    assert not is_trading_day(date(2026, 2, 17))
    assert not is_trading_day(date(2026, 4, 6))
    assert not is_trading_day(date(2026, 6, 19))
    assert not is_trading_day(date(2026, 9, 25))
    assert is_trading_day(date(2026, 6, 18))
    assert is_trading_day(date(2026, 6, 22))
    assert get_previous_trading_day(date(2026, 6, 22)) == date(2026, 6, 18)
    assert get_next_trading_day(date(2026, 6, 18)) == date(2026, 6, 22)


def test_get_previous_trading_day():
    friday = date(2026, 5, 30)
    assert get_previous_trading_day(friday) == date(2026, 5, 29)
    tuesday = date(2026, 6, 3)
    assert get_previous_trading_day(tuesday) == date(2026, 6, 2)


def test_market_hours():
    dt = datetime(2026, 5, 27, 12, 0, tzinfo=SHANGHAI_TZ)
    open_time, close_time = market_hours(dt)
    assert open_time.hour == 9
    assert open_time.minute == 30
    assert close_time.hour == 15
    assert close_time.minute == 0


def test_is_market_open():
    trading_hours = datetime(2026, 5, 27, 10, 0, tzinfo=SHANGHAI_TZ)
    assert is_market_open(trading_hours)
    after_close = datetime(2026, 5, 27, 16, 0, tzinfo=SHANGHAI_TZ)
    assert not is_market_open(after_close)
    weekend = datetime(2026, 5, 31, 10, 0, tzinfo=SHANGHAI_TZ)
    assert not is_market_open(weekend)
