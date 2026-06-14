from __future__ import annotations

import pytest
from datetime import date
import pandas as pd

from aqsp.data.source import DataSource
from aqsp.data.multi_source import MultiSource, SourceFactory
from aqsp.core.errors import DataError, DataInconsistencyError


class MockSource(DataSource):
    name: str = "mock"

    def __init__(
        self,
        data: dict[str, pd.DataFrame],
        should_fail: bool = False,
        symbols: list[str] | None = None,
    ):
        self._data = data
        self._should_fail = should_fail
        self._symbols = symbols

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

    def get_available_symbols(self):
        if self._should_fail:
            raise RuntimeError("mock failure")
        return self._symbols or []

    def get_liquid_symbols(self, *, limit: int, min_amount: float):
        if self._should_fail:
            raise RuntimeError("mock failure")
        symbols = self._symbols or []
        return symbols[:limit] if limit > 0 else symbols


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


def test_multi_source_fills_missing_symbols_from_fallback():
    primary_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}
    fallback_data = {"300750": pd.DataFrame({"date": ["2026-05-27"], "close": [210.0]})}

    primary = MockSource(primary_data)
    fallback = MockSource(fallback_data)
    primary.name = "primary"
    fallback.name = "fallback"
    multi = MultiSource(primary, [fallback])

    result = multi.fetch_daily(
        ["600000", "300750"],
        date(2026, 5, 27),
        date(2026, 5, 27),
    )

    assert set(result) == {"600000", "300750"}
    assert result["600000"]["close"].iloc[0] == 10.0
    assert result["300750"]["close"].iloc[0] == 210.0
    assert multi.last_used_source == "fallback"
    assert multi.last_used_sources == {
        "600000": "primary",
        "300750": "fallback",
    }


def test_multi_source_raises_when_partial_fallback_still_missing_symbols():
    primary_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}
    fallback_data = {"300750": pd.DataFrame({"date": ["2026-05-27"], "close": [210.0]})}

    multi = MultiSource(MockSource(primary_data), [MockSource(fallback_data)])

    with pytest.raises(DataError, match="部分标的获取fetch_daily失败"):
        multi.fetch_daily(
            ["600000", "300750", "000001"],
            date(2026, 5, 27),
            date(2026, 5, 27),
        )


def test_multi_source_falls_back_when_primary_factory_init_fails():
    fallback_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}

    def fail_build():
        raise DataError("local vipdoc missing")

    fallback = MockSource(fallback_data)
    multi = MultiSource(SourceFactory("tdx_vipdoc", fail_build), [fallback])

    result = multi.fetch_daily(["600000"], date(2026, 5, 27), date(2026, 5, 27))

    assert multi.last_used_source == "mock"
    assert "600000" in result


def test_multi_source_raises_when_all_fail():
    primary = MockSource({}, should_fail=True)
    fallback = MockSource({}, should_fail=True)
    multi = MultiSource(primary, [fallback])

    with pytest.raises(DataError):
        multi.fetch_daily(["600000"], date(2026, 5, 27), date(2026, 5, 27))


def test_multi_source_reports_empty_fallbacks_when_all_empty():
    primary = MockSource({})
    fallback = MockSource({})
    multi = MultiSource(primary, [fallback])

    with pytest.raises(DataError, match="empty result"):
        multi.fetch_daily(["600000"], date(2026, 5, 27), date(2026, 5, 27))


def test_multi_source_validates_consistency():
    primary_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}
    fallback_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [11.0]})}

    primary = MockSource(primary_data)
    fallback = MockSource(fallback_data)
    multi = MultiSource(primary, [fallback])

    with pytest.raises(DataInconsistencyError):
        multi.fetch_daily(["600000"], date(2026, 5, 27), date(2026, 5, 27))


def test_multi_source_validates_open_consistency():
    primary_data = {
        "600000": pd.DataFrame(
            {"date": ["2026-05-27"], "open": [10.0], "close": [10.0]}
        )
    }
    fallback_data = {
        "600000": pd.DataFrame(
            {"date": ["2026-05-27"], "open": [11.0], "close": [10.0]}
        )
    }

    multi = MultiSource(MockSource(primary_data), [MockSource(fallback_data)])

    with pytest.raises(DataInconsistencyError):
        multi.fetch_daily(["600000"], date(2026, 5, 27), date(2026, 5, 27))


def test_multi_source_keeps_probing_consistency_after_disjoint_fallback():
    primary_data = {
        "600000": pd.DataFrame(
            {"date": ["2026-05-27"], "open": [10.0], "close": [10.0]}
        )
    }
    disjoint_fallback_data = {
        "300750": pd.DataFrame(
            {"date": ["2026-05-27"], "open": [210.0], "close": [210.0]}
        )
    }
    conflicting_fallback_data = {
        "600000": pd.DataFrame(
            {"date": ["2026-05-27"], "open": [10.0], "close": [11.0]}
        )
    }

    multi = MultiSource(
        MockSource(primary_data),
        [MockSource(disjoint_fallback_data), MockSource(conflicting_fallback_data)],
    )

    with pytest.raises(DataInconsistencyError):
        multi.fetch_daily(
            ["600000", "300750"],
            date(2026, 5, 27),
            date(2026, 5, 27),
        )


def test_multi_source_can_skip_consistency_for_cross_tier_fallbacks():
    primary_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}
    fallback_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [11.0]})}

    multi = MultiSource(
        MockSource(primary_data),
        [MockSource(fallback_data)],
        validate_consistency=False,
    )

    result = multi.fetch_daily(["600000"], date(2026, 5, 27), date(2026, 5, 27))

    assert result["600000"]["close"].iloc[0] == 10.0


def test_multi_source_structure():
    primary = MockSource({})
    fallback = MockSource({})
    multi = MultiSource(primary, [fallback])

    assert multi.name == "multi"
    assert multi.primary is primary
    assert multi.fallbacks == [fallback]
    assert multi.validate_consistency is True


def test_multi_source_exposes_available_symbols_from_primary():
    multi = MultiSource(MockSource({}, symbols=["600000", "000001"]), [])

    assert multi.get_available_symbols() == ["600000", "000001"]
    assert multi.last_used_source == "mock"


def test_multi_source_available_symbols_falls_back():
    multi = MultiSource(
        MockSource({}, should_fail=True),
        [MockSource({}, symbols=["300750"])],
    )

    assert multi.get_available_symbols() == ["300750"]


def test_multi_source_exposes_liquid_symbols_from_primary():
    multi = MultiSource(MockSource({}, symbols=["600000", "000001"]), [])

    assert multi.get_liquid_symbols(limit=1, min_amount=50_000_000) == ["600000"]
