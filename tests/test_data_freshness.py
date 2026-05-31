from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from aqsp.core.errors import FreshnessError
from aqsp.freshness import assert_fresh_data


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


def test_data_freshness_rejects_stale_data_when_schema_valid() -> None:
    stale = (date.today() - timedelta(days=10)).isoformat()
    frames = {"600000": _fresh_frame(stale)}

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
