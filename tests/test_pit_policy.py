from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.data.pit_policy import PitTimestampPolicy, validate_point_in_time_frame


def test_validate_point_in_time_frame_normalizes_timestamp_when_present() -> None:
    frame = pd.DataFrame({"pubDate": ["2026-04-30"], "roe": [0.2]})

    validated = validate_point_in_time_frame(
        frame,
        PitTimestampPolicy("financials", ("pubDate",)),
    )

    assert str(validated["pubDate"].iloc[0].date()) == "2026-04-30"


def test_validate_point_in_time_frame_fails_when_timestamp_missing() -> None:
    frame = pd.DataFrame({"roe": [0.2]})

    with pytest.raises(DataError, match="缺少 point-in-time 时间列"):
        validate_point_in_time_frame(
            frame,
            PitTimestampPolicy("financials", ("pubDate",)),
        )


def test_validate_point_in_time_frame_fails_when_timestamp_after_asof() -> None:
    frame = pd.DataFrame({"pubDate": ["2026-05-01"], "roe": [0.2]})

    with pytest.raises(DataError, match="as-of 之后才可见"):
        validate_point_in_time_frame(
            frame,
            PitTimestampPolicy("financials", ("pubDate",), as_of=date(2026, 4, 30)),
        )
