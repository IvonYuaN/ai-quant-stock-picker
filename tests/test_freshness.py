from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from aqsp.freshness import assert_fresh_data, latest_trade_date


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


def test_assert_fresh_data_rejects_stale_data(monkeypatch) -> None:
    frames = {"A": _frame_for_day("2026-05-20")}
    monkeypatch.setattr("aqsp.freshness.today_shanghai", lambda: date(2026, 6, 1))
    with pytest.raises(RuntimeError, match="stale"):
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
