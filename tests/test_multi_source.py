from __future__ import annotations

import time
from datetime import date

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.data.multi_source import MultiSource


class _Source:
    name = "eastmoney"

    def fetch_intraday(self, symbols: list[str], period: str = "5") -> dict:
        return {
            symbol: pd.DataFrame({"date": ["2026-07-16 09:30:00"], "close": [10.0]})
            for symbol in symbols
        }

    def fetch_daily(self, symbols: list[str], start, end, adjust: str = "") -> dict:
        return {symbols[0]: pd.DataFrame({"date": ["2026-07-16"], "close": [10.0]})}

    def set_workload(self, workload: str | None) -> None:
        self.workload = workload


def test_multi_source_live_intraday_keeps_realtime_provenance() -> None:
    source = MultiSource(_Source(), [], validate_consistency=False)

    result = source.fetch_intraday(["600000"])

    assert result["600000"].attrs["source_name"] == "eastmoney"
    assert source.last_used_sources == {"600000": "eastmoney"}


def test_multi_source_live_short_daily_keeps_partial_batch_for_coverage_gate() -> None:
    source = MultiSource(_Source(), [], validate_consistency=False)
    source.set_workload("live_short")

    result = source.fetch_daily(
        ["600000", "000001"],
        start=date(2026, 7, 1),
        end=date(2026, 7, 16),
    )

    assert set(result) == {"600000"}


def test_multi_source_live_intraday_uses_fallback_after_primary_failure() -> None:
    class SlowSource(_Source):
        def __init__(self, name: str, delay: float, *, fail: bool = False) -> None:
            self.name = name
            self.delay = delay
            self.fail = fail

        def fetch_intraday(self, symbols: list[str], period: str = "5") -> dict:
            time.sleep(self.delay)
            if self.fail:
                raise RuntimeError("source unavailable")
            return super().fetch_intraday(symbols, period)

    source = MultiSource(
        SlowSource("eastmoney", 0.2, fail=True),
        [SlowSource("sina", 0.02)],
        validate_consistency=False,
        live_fetch_deadline_seconds=0.5,
    )

    started = time.monotonic()
    result = source.fetch_intraday(["600000"])
    elapsed = time.monotonic() - started

    assert set(result) == {"600000"}
    assert result["600000"].attrs["source_name"] == "sina"
    assert elapsed < 0.35


def test_multi_source_live_intraday_does_not_call_fallback_when_primary_complete() -> (
    None
):
    class RecordingSource(_Source):
        def __init__(self, name: str) -> None:
            self.name = name
            self.calls: list[tuple[str, ...]] = []

        def fetch_intraday(self, symbols: list[str], period: str = "5") -> dict:
            self.calls.append(tuple(symbols))
            return super().fetch_intraday(symbols, period)

    primary = RecordingSource("eastmoney")
    fallback = RecordingSource("sina")

    result = MultiSource(primary, [fallback]).fetch_intraday(["600000"])

    assert set(result) == {"600000"}
    assert primary.calls == [("600000",)]
    assert fallback.calls == []


def test_multi_source_live_intraday_fallback_only_receives_missing_symbols() -> None:
    class RecordingSource(_Source):
        def __init__(self, name: str, data: dict[str, pd.DataFrame]) -> None:
            self.name = name
            self.data = data
            self.calls: list[tuple[str, ...]] = []

        def fetch_intraday(self, symbols: list[str], period: str = "5") -> dict:
            self.calls.append(tuple(symbols))
            return {
                symbol: self.data[symbol] for symbol in symbols if symbol in self.data
            }

    primary = RecordingSource(
        "eastmoney", {"600000": _Source().fetch_intraday(["600000"])["600000"]}
    )
    fallback = RecordingSource(
        "sina",
        {
            "000001": _Source().fetch_intraday(["000001"])["000001"],
            "300750": _Source().fetch_intraday(["300750"])["300750"],
        },
    )

    result = MultiSource(primary, [fallback]).fetch_intraday(
        ["600000", "000001", "300750"]
    )

    assert set(result) == {"600000", "000001", "300750"}
    assert primary.calls == [("600000", "000001", "300750")]
    assert fallback.calls == [("000001", "300750")]


def test_multi_source_live_intraday_rejects_historical_fallback() -> None:
    historical = _Source()
    historical.name = "sqlite_db"

    with pytest.raises(DataError, match="所有数据源获取fetch_intraday失败"):
        MultiSource(historical, []).fetch_intraday(["600000"])
