from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from aqsp.core.errors import FreshnessError
from aqsp.freshness import assert_fresh_data
from aqsp.data.freshness import check_freshness


def _fresh_frame(latest: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [latest],
            "symbol": ["600000"],
            "name": ["测试"],
            "open": [10.0],
            "high": [10.5],
            "low": [9.5],
            "close": [10.2],
            "volume": [1000],
            "amount": [10_200],
            "suspended": [False],
            "limit_up": [11.22],
            "limit_down": [9.18],
        }
    )


def test_data_freshness_rejects_stale_data_when_schema_valid(monkeypatch) -> None:
    """行情落后超过 max_lag_days 个交易日时必须抛 FreshnessError。

    这里固定「今天」而不是用 `date.today()`：断言依赖的是「数据日期 → 今天」跨过的
    **交易日**数，而长假会把交易日 lag 压到阈值内 —— 2026-10-08（国庆后首个交易日）
    实测自然日差 10 天、交易日 lag 只有 2，`max_lag_days=3` 于是判为新鲜，这条用例
    就这么红过一次。同文件其余用例一律 monkeypatch 固定日期，这里对齐。
    """
    monkeypatch.setattr("aqsp.freshness.today_shanghai", lambda: date(2026, 10, 8))
    monkeypatch.setattr(
        "aqsp.freshness.load_optional_trade_calendar", lambda *_args, **_kwargs: None
    )

    frames = {"600000": _fresh_frame("2026-09-01")}

    with pytest.raises(FreshnessError, match="stale"):
        assert_fresh_data(frames, max_lag_days=3)


def test_data_freshness_rejects_missing_schema_when_source_drifted() -> None:
    fresh = date.today().isoformat()
    frames = {"600000": pd.DataFrame({"date": [fresh], "close": [10.0]})}

    with pytest.raises(FreshnessError, match="schema missing"):
        assert_fresh_data(frames, max_lag_days=3)


def test_data_freshness_rejects_empty_frames_explicitly() -> None:
    with pytest.raises(FreshnessError, match="no valid market data"):
        assert_fresh_data({"600000": pd.DataFrame()}, max_lag_days=3)


def test_data_freshness_uses_trade_day_lag_on_weekend(monkeypatch) -> None:
    monkeypatch.setattr("aqsp.data.freshness.today_shanghai", lambda: date(2026, 5, 31))
    monkeypatch.setattr(
        "aqsp.data.freshness.load_optional_trade_calendar", lambda *_args: None
    )

    reports = check_freshness({"600000": pd.DataFrame({"date": ["2026-05-29"]})})

    assert reports[0].delay_days == 0
    assert reports[0].status == "fresh"


def test_data_freshness_uses_supplied_holiday_calendar(monkeypatch) -> None:
    monkeypatch.setattr("aqsp.data.freshness.today_shanghai", lambda: date(2026, 10, 2))
    monkeypatch.setattr(
        "aqsp.data.freshness.load_optional_trade_calendar",
        lambda *_args: pd.DataFrame(
            [
                {"cal_date": "2026-09-30", "is_open": 1},
                {"cal_date": "2026-10-01", "is_open": 0},
                {"cal_date": "2026-10-02", "is_open": 0},
            ]
        ),
    )

    reports = check_freshness({"600000": pd.DataFrame({"date": ["2026-09-30"]})})

    assert reports[0].delay_days == 0
    assert reports[0].status == "fresh"
