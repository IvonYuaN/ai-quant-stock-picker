from __future__ import annotations

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.research.price_path import summarize_price_path


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=20).strftime("%Y-%m-%d"),
            "close": [10 + index for index in range(20)],
            "high": [10.5 + index for index in range(20)],
            "low": [9.5 + index for index in range(20)],
            "volume": [1000 + index * 10 for index in range(20)],
        }
    )


def test_summarize_price_path_outputs_multi_window_context() -> None:
    summaries = summarize_price_path(_frame(), windows=(5, 10, 30))

    assert tuple(item.window for item in summaries) == (5, 10)
    assert summaries[0].start_date == "2026-01-16"
    assert summaries[0].end_date == "2026-01-20"
    assert summaries[0].return_pct > 0
    assert summaries[0].max_drawdown_pct == 0
    assert 0 <= summaries[0].close_position <= 1
    assert summaries[0].volume_ratio > 1


def test_summarize_price_path_uses_trailing_data_only() -> None:
    frame = _frame()

    before = summarize_price_path(frame.iloc[:10], windows=(5,))[0]
    after = summarize_price_path(frame.iloc[:15], windows=(5,))[0]

    assert before.end_date == "2026-01-10"
    assert after.end_date == "2026-01-15"
    assert before.start_date != after.start_date


def test_summarize_price_path_fails_when_required_columns_missing() -> None:
    frame = _frame().drop(columns=["volume"])

    with pytest.raises(DataError, match="missing columns"):
        summarize_price_path(frame)
