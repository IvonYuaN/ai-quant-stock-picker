from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from aqsp.freshness import assert_fresh_data, latest_trade_date


def test_latest_trade_date() -> None:
    frames = {
        "A": pd.DataFrame({"date": ["2026-05-20", "2026-05-21"]}),
        "B": pd.DataFrame({"date": ["2026-05-19"]}),
    }
    assert latest_trade_date(frames) == date(2026, 5, 21)


def test_assert_fresh_data_rejects_stale_data() -> None:
    stale = (date.today() - timedelta(days=10)).isoformat()
    frames = {"A": pd.DataFrame({"date": [stale]})}
    with pytest.raises(RuntimeError, match="stale"):
        assert_fresh_data(frames, max_lag_days=3)
