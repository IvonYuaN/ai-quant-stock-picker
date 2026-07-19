from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from aqsp.freshness import (
    assert_fresh_data,
    assert_live_short_fresh_data,
    latest_trade_date,
    validate_realtime_quotes,
)


def _frame_for_day(day: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": day,
                "symbol": "600519",
                "name": "Kweichow Moutai",
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
                "amount": 1.0,
                "suspended": False,
                "limit_up": 2.0,
                "limit_down": 0.5,
            }
        ]
    )


def test_latest_trade_date() -> None:
    frames = {
        "A": pd.DataFrame({"date": ["2026-05-20", "2026-05-21"]}),
        "B": pd.DataFrame({"date": ["2026-05-19"]}),
    }
    assert latest_trade_date(frames) == date(2026, 5, 21)


def test_latest_trade_date_normalizes_utc_timestamp_to_shanghai_day() -> None:
    frames = {
        "A": pd.DataFrame({"date": ["2026-07-19T16:30:00Z"]}),
    }

    assert latest_trade_date(frames) == date(2026, 7, 20)


def test_assert_fresh_data_rejects_future_trade_day(monkeypatch) -> None:
    frames = {"A": _frame_for_day("2026-07-21")}
    monkeypatch.setattr("aqsp.freshness.today_shanghai", lambda: date(2026, 7, 20))

    with pytest.raises(RuntimeError, match="in the future"):
        assert_fresh_data(frames, max_lag_days=0)


def test_assert_fresh_data_rejects_stale_data(monkeypatch) -> None:
    frames = {"A": _frame_for_day("2026-05-20")}
    monkeypatch.setattr("aqsp.freshness.today_shanghai", lambda: date(2026, 6, 1))
    with pytest.raises(RuntimeError, match="stale"):
        assert_fresh_data(frames, max_lag_days=3)


def test_assert_fresh_data_rejects_partially_stale_symbols(monkeypatch) -> None:
    frames = {
        "fresh": _frame_for_day("2026-06-01"),
        "stale": _frame_for_day("2026-05-20"),
    }
    monkeypatch.setattr("aqsp.freshness.today_shanghai", lambda: date(2026, 6, 1))

    with pytest.raises(RuntimeError, match="stale symbols.*stale:2026-05-20"):
        assert_fresh_data(frames, max_lag_days=3)


def test_assert_fresh_data_accepts_long_holiday_when_calendar_available(
    monkeypatch,
) -> None:
    frames = {"A": _frame_for_day("2026-09-30")}

    monkeypatch.setattr("aqsp.freshness.today_shanghai", lambda: date(2026, 10, 2))
    monkeypatch.setattr(
        "aqsp.freshness.load_optional_trade_calendar",
        lambda start, end: pd.DataFrame(
            [
                {"cal_date": "2026-09-30", "is_open": 1},
                {"cal_date": "2026-10-01", "is_open": 0},
                {"cal_date": "2026-10-02", "is_open": 0},
            ]
        ),
    )

    assert assert_fresh_data(frames, max_lag_days=0) == date(2026, 9, 30)


def test_assert_fresh_data_caps_live_short_lag_even_when_config_allows_more(
    monkeypatch,
) -> None:
    frames = {"A": _frame_for_day("2026-06-01")}
    monkeypatch.setattr("aqsp.freshness.today_shanghai", lambda: date(2026, 6, 3))
    monkeypatch.setattr(
        "aqsp.freshness.load_optional_trade_calendar",
        lambda start, end: pd.DataFrame(
            [
                {"cal_date": "2026-06-01", "is_open": 1},
                {"cal_date": "2026-06-02", "is_open": 1},
                {"cal_date": "2026-06-03", "is_open": 1},
            ]
        ),
    )

    assert assert_fresh_data(frames, max_lag_days=3) == date(2026, 6, 1)
    with pytest.raises(RuntimeError, match="max=1"):
        assert_fresh_data(frames, max_lag_days=3, workload="live_short")


def test_assert_live_short_fresh_data_uses_live_short_freshness_policy(
    monkeypatch,
) -> None:
    frames = {"A": _frame_for_day("2026-06-01")}
    monkeypatch.setattr("aqsp.freshness.today_shanghai", lambda: date(2026, 6, 3))
    monkeypatch.setattr(
        "aqsp.freshness.load_optional_trade_calendar",
        lambda start, end: pd.DataFrame(
            [
                {"cal_date": "2026-06-01", "is_open": 1},
                {"cal_date": "2026-06-02", "is_open": 1},
                {"cal_date": "2026-06-03", "is_open": 1},
            ]
        ),
    )

    with pytest.raises(RuntimeError, match="max=1"):
        assert_live_short_fresh_data(frames, max_lag_days=3)


def _quote(*, vendor_ts: str = "2026-07-13T10:00:00+08:00") -> dict[str, object]:
    return {
        "price": 10.0,
        "bid1": 9.99,
        "ask1": 10.01,
        "volume": 1000,
        "amount": 10000,
        "ts": vendor_ts,
        "vendor_ts": vendor_ts,
        "timestamp_source": "vendor",
    }


def test_validate_realtime_quotes_rejects_future_vendor_timestamp() -> None:
    with pytest.raises(RuntimeError, match="timestamp is in the future"):
        validate_realtime_quotes(
            {"600000": _quote(vendor_ts="2026-07-13T10:01:00+08:00")},
            require_vendor_timestamp=True,
            now=datetime(2026, 7, 13, 10, 0, tzinfo=datetime.fromisoformat("2026-07-13T10:00:00+08:00").tzinfo),
        )


def test_validate_realtime_quotes_rejects_received_at_for_live_short() -> None:
    quote = _quote()
    quote["vendor_ts"] = ""
    quote["timestamp_source"] = "received_at"

    with pytest.raises(RuntimeError, match="vendor timestamp missing"):
        validate_realtime_quotes({"600000": quote}, require_vendor_timestamp=True)


def test_validate_realtime_quotes_rejects_non_finite_trade_metrics() -> None:
    quote = _quote()
    quote["amount"] = float("nan")

    with pytest.raises(RuntimeError, match="amount must be finite"):
        validate_realtime_quotes({"600000": quote})
