from __future__ import annotations

import pytest
from datetime import date, timedelta
import pandas as pd

from aqsp.core.time import now_shanghai
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
        quote_data: dict[str, dict] | None = None,
    ):
        self._data = data
        self._should_fail = should_fail
        self._symbols = symbols
        self._quote_data = quote_data or {}

    def fetch_daily(self, symbols, start, end, adjust=""):
        if self._should_fail:
            raise RuntimeError("mock failure")
        return {k: v for k, v in self._data.items() if k in symbols}

    def fetch_intraday(self, symbols, period="5"):
        if self._should_fail:
            raise RuntimeError("mock failure")
        return {k: v for k, v in self._data.items() if k in symbols}

    def fetch_realtime_quote(self, symbols):
        if self._should_fail:
            raise RuntimeError("mock failure")
        return {k: v for k, v in self._quote_data.items() if k in symbols}

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


def test_multi_source_rejects_partial_primary_and_uses_complete_fallback():
    primary_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}
    fallback_data = {
        "600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]}),
        "000001": pd.DataFrame({"date": ["2026-05-27"], "close": [12.0]}),
    }

    multi = MultiSource(MockSource(primary_data), [MockSource(fallback_data)])

    result = multi.fetch_daily(
        ["600000", "000001"],
        date(2026, 5, 27),
        date(2026, 5, 27),
    )

    assert set(result) == {"600000", "000001"}


def test_multi_source_merges_partial_daily_results_across_sources():
    primary_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}
    fallback_data = {"000001": pd.DataFrame({"date": ["2026-05-27"], "close": [12.0]})}

    multi = MultiSource(MockSource(primary_data), [MockSource(fallback_data)])

    result = multi.fetch_daily(
        ["600000", "000001"],
        date(2026, 5, 27),
        date(2026, 5, 27),
    )

    assert multi.last_used_source == "multi"
    assert set(result) == {"600000", "000001"}
    assert result["600000"]["close"].iloc[0] == 10.0
    assert result["000001"]["close"].iloc[0] == 12.0


def test_multi_source_live_workload_rejects_historical_partial_merge():
    primary = MockSource(
        {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}
    )
    primary.name = "eastmoney"
    historical = MockSource(
        {"000001": pd.DataFrame({"date": ["2026-05-27"], "close": [12.0]})}
    )
    historical.name = "sqlite_db"
    multi = MultiSource(primary, [historical])
    multi.set_workload("live_short")

    with pytest.raises(DataError, match="sqlite_db 不适合 live_short"):
        multi.fetch_daily(
            ["600000", "000001"],
            date(2026, 5, 27),
            date(2026, 5, 27),
        )


def test_multi_source_live_partial_merge_records_symbol_provenance():
    primary = MockSource(
        {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}
    )
    primary.name = "eastmoney"
    fallback = MockSource(
        {"000001": pd.DataFrame({"date": ["2026-05-27"], "close": [12.0]})}
    )
    fallback.name = "sina"
    multi = MultiSource(primary, [fallback])
    multi.set_workload("live_short")

    result = multi.fetch_daily(
        ["600000", "000001"],
        date(2026, 5, 27),
        date(2026, 5, 27),
    )

    assert multi.last_used_source == "multi"
    assert multi.last_used_sources == {"600000": "eastmoney", "000001": "sina"}
    assert result["600000"].attrs["source_name"] == "eastmoney"
    assert result["000001"].attrs["source_name"] == "sina"


def test_multi_source_raises_when_partial_merge_still_missing_symbol():
    primary_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}
    fallback_data = {"000001": pd.DataFrame({"date": ["2026-05-27"], "close": [12.0]})}

    multi = MultiSource(MockSource(primary_data), [MockSource(fallback_data)])

    with pytest.raises(DataError, match="partial result missing"):
        multi.fetch_daily(
            ["600000", "000001", "300750"],
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


def test_multi_source_clears_previous_provenance_when_all_sources_fail():
    primary = MockSource(
        {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}
    )
    fallback = MockSource(
        {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}
    )
    multi = MultiSource(primary, [fallback])
    multi.fetch_daily(["600000"], date(2026, 5, 27), date(2026, 5, 27))
    primary._should_fail = True
    fallback._should_fail = True

    with pytest.raises(DataError):
        multi.fetch_daily(["600000"], date(2026, 5, 27), date(2026, 5, 27))

    assert multi.last_used_source is None
    assert multi.last_used_sources == {}


def test_multi_source_validates_consistency():
    primary_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}
    fallback_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [11.0]})}

    primary = MockSource(primary_data)
    fallback = MockSource(fallback_data)
    multi = MultiSource(primary, [fallback])

    with pytest.raises(DataInconsistencyError):
        multi.fetch_daily(["600000"], date(2026, 5, 27), date(2026, 5, 27))


def test_multi_source_consistency_reports_actual_fallback_name():
    primary_data = {"600000": pd.DataFrame({"date": ["2026-05-27"], "close": [10.0]})}
    incomplete_fallback = {}
    complete_bad_fallback = {
        "600000": pd.DataFrame({"date": ["2026-05-27"], "close": [11.0]})
    }

    first = MockSource(incomplete_fallback)
    first.name = "empty_fallback"
    second = MockSource(complete_bad_fallback)
    second.name = "complete_fallback"
    multi = MultiSource(MockSource(primary_data), [first, second])

    with pytest.raises(DataInconsistencyError, match="complete_fallback"):
        multi.fetch_daily(["600000"], date(2026, 5, 27), date(2026, 5, 27))


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


def test_multi_source_realtime_quote_uses_fallback_when_primary_schema_invalid():
    primary = MockSource(
        {},
        quote_data={"600000": {"price": 10.0, "ts": now_shanghai().isoformat()}},
    )
    fallback = MockSource(
        {},
        quote_data={"600000": _fresh_quote(price=10.1)},
    )
    primary.name = "eastmoney"
    fallback.name = "sina"
    multi = MultiSource(primary, [fallback])

    result = multi.fetch_realtime_quote(["600000"])

    assert multi.last_used_source == "sina"
    assert result["600000"]["price"] == 10.1


def test_multi_source_realtime_quote_falls_back_per_symbol_when_one_quote_is_invalid():
    class CountingSource(MockSource):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.calls: list[tuple[str, ...]] = []

        def fetch_realtime_quote(self, symbols):
            self.calls.append(tuple(symbols))
            return super().fetch_realtime_quote(symbols)

    invalid = _fresh_quote()
    invalid["vendor_ts"] = ""
    invalid["timestamp_source"] = "received_at"
    primary = CountingSource(
        {},
        quote_data={
            "600000": _fresh_quote(price=10.0),
            "000001": invalid,
        },
    )
    fallback = CountingSource(
        {},
        quote_data={"000001": _fresh_quote(price=11.0)},
    )
    primary.name = "eastmoney"
    fallback.name = "sina"
    multi = MultiSource(primary, [fallback])

    result = multi.fetch_realtime_quote(["600000", "000001"])

    assert result["600000"]["price"] == 10.0
    assert result["000001"]["price"] == 11.0
    assert primary.calls[0] == ("600000", "000001")
    assert ("000001",) in primary.calls
    assert fallback.calls == [("000001",)]
    assert multi.last_used_source == "multi"
    assert multi.last_used_sources == {"600000": "eastmoney", "000001": "sina"}


def test_multi_source_realtime_quote_keeps_valid_symbols_when_other_symbol_fails_closed():
    invalid = _fresh_quote()
    invalid["vendor_ts"] = ""
    invalid["timestamp_source"] = "received_at"
    primary = MockSource(
        {},
        quote_data={
            "600000": _fresh_quote(price=10.0),
            "000001": invalid,
        },
    )
    fallback = MockSource({}, quote_data={"000001": invalid})
    primary.name = "eastmoney"
    fallback.name = "sina"
    multi = MultiSource(primary, [fallback])

    result = multi.fetch_realtime_quote(["600000", "000001"])

    assert set(result) == {"600000"}
    assert "000001" not in result


def test_multi_source_realtime_quote_rejects_all_future_vendor_timestamps():
    future = _fresh_quote(ts=(now_shanghai() + timedelta(seconds=1)).isoformat())
    primary = MockSource({}, quote_data={"600000": future})
    fallback = MockSource({}, quote_data={"600000": future})
    multi = MultiSource(primary, [fallback])

    primary.name = "eastmoney"
    fallback.name = "sina"

    with pytest.raises(DataError, match="timestamp is in the future"):
        multi.fetch_realtime_quote(["600000"])


def test_multi_source_realtime_quote_rejects_stale_quotes_from_all_sources():
    stale_quote = _fresh_quote(ts="2026-01-01T09:30:00+08:00")
    primary = MockSource({}, quote_data={"600000": stale_quote})
    fallback = MockSource({}, quote_data={"600000": stale_quote})

    primary.name = "eastmoney"
    fallback.name = "sina"
    multi = MultiSource(primary, [fallback])

    with pytest.raises(DataError, match="realtime quote is stale"):
        multi.fetch_realtime_quote(["600000"])


def test_multi_source_realtime_quote_skips_dataframe_consistency_for_dict_payloads():
    primary = MockSource({}, quote_data={"600000": _fresh_quote(price=10.0)})
    fallback = MockSource({}, quote_data={"600000": _fresh_quote(price=11.0)})
    primary.name = "eastmoney"
    fallback.name = "sina"
    multi = MultiSource(primary, [fallback], validate_consistency=True)

    result = multi.fetch_realtime_quote(["600000"])

    assert result["600000"]["price"] == 10.0


def test_multi_source_intraday_skips_historical_fallback_source():
    primary = MockSource({}, should_fail=True)
    primary.name = "eastmoney"
    historical = MockSource(
        {"600000": pd.DataFrame({"time": ["09:35"], "close": [99.0]})}
    )
    historical.name = "sqlite_db"
    live = MockSource({"600000": pd.DataFrame({"time": ["09:35"], "close": [10.2]})})
    live.name = "sina"
    multi = MultiSource(primary, [historical, live])

    result = multi.fetch_intraday(["600000"])

    assert multi.last_used_source == "sina"
    assert result["600000"]["close"].iloc[0] == 10.2


def test_multi_source_intraday_raises_when_only_historical_fallback_source():
    primary = MockSource({}, should_fail=True)
    primary.name = "eastmoney"
    historical = MockSource(
        {"600000": pd.DataFrame({"time": ["09:35"], "close": [99.0]})}
    )
    historical.name = "sqlite_db"
    multi = MultiSource(primary, [historical])

    with pytest.raises(DataError, match="sqlite_db 不适合 live_short"):
        multi.fetch_intraday(["600000"])


def test_multi_source_structure():
    primary = MockSource({})
    fallback = MockSource({})
    multi = MultiSource(primary, [fallback])

    assert multi.name == "multi"
    assert multi.primary is primary
    assert multi.fallbacks == [fallback]
    assert multi.validate_consistency is True


def _fresh_quote(*, price: float = 10.0, ts: str | None = None) -> dict:
    return {
        "price": price,
        "bid1": price - 0.01,
        "ask1": price + 0.01,
        "volume": 1_000_000,
        "amount": price * 1_000_000,
        "ts": ts or now_shanghai().isoformat(),
        "vendor_ts": ts or now_shanghai().isoformat(),
        "timestamp_source": "vendor",
    }


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


def test_multi_source_live_symbol_discovery_skips_historical_fallbacks():
    primary = MockSource({}, symbols=[])
    primary.name = "eastmoney"
    historical = MockSource({}, symbols=["600519"])
    historical.name = "tdx_vipdoc"
    multi = MultiSource(primary, [historical])
    multi.set_workload("live_short")

    with pytest.raises(DataError, match="tdx_vipdoc"):
        multi.get_liquid_symbols(limit=0, min_amount=50_000_000)


def test_multi_source_live_available_discovery_skips_historical_fallbacks():
    primary = MockSource({}, symbols=[])
    primary.name = "eastmoney"
    historical = MockSource({}, symbols=["600519"])
    historical.name = "tdx_vipdoc"
    multi = MultiSource(primary, [historical])
    multi.set_workload("live_short")

    with pytest.raises(DataError, match="tdx_vipdoc"):
        multi.get_available_symbols()
