from __future__ import annotations

from datetime import datetime, date

import pandas as pd

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


def test_is_trading_day_2026_national_day_window_matches_exchange_notice():
    """2026 中秋/国庆窗口必须与交易所公告逐日一致。

    依据：深交所《关于2026年中秋节、国庆节休市安排的通知》（2026-09-17）原文：
      「9月25日（星期五）至9月27日（星期日）休市，9月28日（星期一）起照常开市。
        10月1日（星期四）至10月7日（星期三）休市，**10月8日（星期四）起照常开市**。
        另外，9月20日（星期日）、10月10日（星期六）为周末休市。」

    ⚠️ 回归守卫（issue #310）：`config/trading_holidays.json` 曾把 `2026-10-08`
    误列入 `holidays`，导致 prod 的 `data-refresh` 判其为「非交易日」而跳过 ⇒
    `daily_qfq` **永久缺失该交易日约 4400 只标的日线**（实测 range 停在 20260930）。
    该文件还曾把 `2026-09-27` / `2026-10-10` 放进 `makeup_workdays`，而
    `_is_basic_trading_day()` 对 `makeup_workdays` 无条件返回 True ⇒ 公告明写
    「周末休市」的两天被判成交易日。
    """
    # 中秋：9/25(五)–9/27(日) 休市，9/28(一) 照常开市
    assert not is_trading_day(date(2026, 9, 25))
    assert not is_trading_day(date(2026, 9, 27))  # 周日 + 公告「周末休市」
    assert is_trading_day(date(2026, 9, 28))

    # 国庆：10/1(四)–10/7(三) 休市
    for day in (1, 2, 5, 6, 7):
        assert not is_trading_day(date(2026, 10, day)), f"2026-10-0{day} 应休市"

    # 🔴 公告明写「10月8日（星期四）起照常开市」—— 本用例的核心回归点
    assert is_trading_day(date(2026, 10, 8)), (
        "2026-10-08 是交易日（交易所公告「照常开市」）；"
        "若判为休市，data-refresh 会静默跳过该日，永久缺失一根 K 线（issue #310）"
    )
    assert is_trading_day(date(2026, 10, 9))

    # 10/10(六) 公告「周末休市」⇒ 非交易日（调休上班日不是交易日）
    assert not is_trading_day(date(2026, 10, 10))

    # 链路：10-08 必须真的在交易日链上，不能被跳过
    assert get_previous_trading_day(date(2026, 10, 9)) == date(2026, 10, 8)
    assert get_next_trading_day(date(2026, 9, 30)) == date(2026, 10, 8)


def test_static_holiday_overrides_runtime_calendar_open_flag():
    from aqsp.data.trading_calendar import resolve_is_trading_day

    calendar = pd.DataFrame([{"cal_date": "20260619", "is_open": 1}])

    assert not resolve_is_trading_day(date(2026, 6, 19), calendar_df=calendar)


def test_static_holiday_overrides_runtime_calendar_previous_and_next_trade_day():
    from aqsp.data.trading_calendar import (
        resolve_next_trading_day,
        resolve_previous_trading_day,
    )

    calendar = pd.DataFrame(
        [
            {"cal_date": "20260618", "is_open": 1},
            {"cal_date": "20260619", "is_open": 1},
            {"cal_date": "20260622", "is_open": 1},
        ]
    )

    assert resolve_previous_trading_day(
        date(2026, 6, 22), calendar_df=calendar
    ) == date(2026, 6, 18)
    assert resolve_next_trading_day(date(2026, 6, 18), calendar_df=calendar) == date(
        2026, 6, 22
    )


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
