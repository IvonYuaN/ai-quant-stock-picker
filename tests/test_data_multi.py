from __future__ import annotations

import pytest
from datetime import date
import pandas as pd

from aqsp.data.source import DataSource
from aqsp.data.multi_source import MultiSource
from aqsp.core.errors import DataError, DataInconsistencyError


class MockSource(DataSource):
    name: str = "mock"

    def __init__(self, data: dict[str, pd.DataFrame], should_fail: bool = False):
        self._data = data
        self._should_fail = should_fail

    def fetch_daily(self, symbols, start, end, adjust=""):
        if self._should_fail:
            raise RuntimeError("mock failure")
        return {k: v for k, v in self._data.items() if k in symbols}

    def fetch_intraday(self, symbols, period="5"):
        return {}

    def fetch_realtime_quote(self, symbols):
        return {}

    def fetch_index(self, index_codes, start, end):
        return {}


def test_multi_source_uses_primary():
    primary_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}
    fallback_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}

    primary = MockSource(primary_data)
    fallback = MockSource(fallback_data)
    multi = MultiSource(primary, [fallback])

    result = multi.fetch_daily(["600000"], date(2026, 5, 27), date(2026, 5, 27))

    assert multi.last_used_source == "mock"
    assert "600000" in result
    assert result["600000"]["close"].iloc[0] == 10.0


def test_multi_source_falls_back():
    primary_data = {}
    fallback_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}

    primary = MockSource(primary_data, should_fail=True)
    fallback = MockSource(fallback_data)
    multi = MultiSource(primary, [fallback])

    result = multi.fetch_daily(["600000"], date(2026, 5, 27), date(2026, 5, 27))

    assert multi.last_used_source == "mock"
    assert "600000" in result


def test_multi_source_raises_when_all_fail():
    primary = MockSource({}, should_fail=True)
    fallback = MockSource({}, should_fail=True)
    multi = MultiSource(primary, [fallback])

    with pytest.raises(DataError):
        multi.fetch_daily(["600000"], date(2026, 5, 27), date(2026, 5, 27))


def test_multi_source_validates_consistency():
    primary_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}
    fallback_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [11.0]})}

    primary = MockSource(primary_data)
    fallback = MockSource(fallback_data)
    multi = MultiSource(primary, [fallback])

    with pytest.raises(DataInconsistencyError):
        multi.fetch_daily(["600000"], date(2026, 5, 27), date(2026, 5, 27))


def test_multi_source_structure():
    primary = MockSource({})
    fallback = MockSource({})
    multi = MultiSource(primary, [fallback])

    assert multi.name == "multi"
    assert multi.primary is primary
    assert multi.fallbacks == [fallback]
