from __future__ import annotations

from datetime import date

import pandas as pd

from aqsp.services.walkforward_data import (
    WalkforwardFetchRequest,
    fetch_walkforward_frames,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2024-01-02"],
            "open": [1.0],
            "high": [1.1],
            "low": [0.9],
            "close": [1.0],
            "volume": [1000],
            "amount": [1000.0],
            "suspended": [False],
            "limit_up": [1.1],
            "limit_down": [0.9],
        }
    )


def test_fetch_walkforward_frames_delegates_online_source_when_multi() -> None:
    seen: dict[str, object] = {}

    def fetch_frames_for_cli(source, symbols, **kwargs):
        seen["source"] = source
        seen["symbols"] = list(symbols)
        seen["kwargs"] = kwargs
        return {"600519": _frame()}

    result = fetch_walkforward_frames(
        WalkforwardFetchRequest(
            source="multi",
            symbols=["600519"],
            start="2024-01-01",
            end="2024-06-30",
            cache_path="/tmp/cache.db",
        ),
        get_source_fn=lambda _name: None,
        fetch_frames_for_cli_fn=fetch_frames_for_cli,
        load_csv_fn=lambda _path: {},
        fetch_days_fn=lambda start, end: 181,
    )

    assert result.frames.keys() == {"600519"}
    assert result.symbols == ["600519"]
    assert seen == {
        "source": "multi",
        "symbols": ["600519"],
        "kwargs": {
            "benchmark_symbol": None,
            "cache_path": "/tmp/cache.db",
            "days": 181,
        },
    }


def test_fetch_walkforward_frames_filters_sqlite_coverage_when_available() -> None:
    seen: dict[str, object] = {}

    class Source:
        def get_available_symbols(self) -> list[str]:
            return ["600519", "000001"]

        def get_symbols_with_daily_coverage(
            self,
            symbols: list[str],
            start: date,
            end: date,
            min_rows: int | None = None,
        ) -> list[str]:
            seen["coverage"] = (list(symbols), start, end, min_rows)
            return ["600519"]

        def fetch_daily(
            self, symbols: list[str], start: date, end: date, adjust: str = ""
        ) -> dict[str, pd.DataFrame]:
            seen["fetch"] = (list(symbols), start, end, adjust)
            return {"600519": _frame()}

    result = fetch_walkforward_frames(
        WalkforwardFetchRequest(
            source="sqlite_db",
            symbols=["600519", "300750"],
            start="2024-01-01",
            end="2024-06-30",
            skip_pit_financials=True,
        ),
        get_source_fn=lambda name: Source() if name == "sqlite_db" else None,
        fetch_frames_for_cli_fn=lambda *args, **kwargs: {},
        load_csv_fn=lambda _path: {},
        fetch_days_fn=lambda _start, _end: 0,
        print_fn=lambda _message: None,
    )

    assert result.symbols == ["600519"]
    assert result.frames.keys() == {"600519"}
    assert seen["coverage"] == (
        ["600519"],
        date(2024, 1, 1),
        date(2024, 6, 30),
        None,
    )
    assert seen["fetch"] == (
        ["600519"],
        date(2024, 1, 1),
        date(2024, 6, 30),
        "",
    )


def test_fetch_walkforward_frames_skips_pit_enrichment_when_requested(
    monkeypatch,
) -> None:
    called = False

    class Source:
        def fetch_daily(self, symbols, start, end, adjust=""):
            return {"600519": _frame()}

    def fail_enrich(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("PIT enrichment should be skipped")

    monkeypatch.setattr(
        "aqsp.data.pit_financial.enrich_ohlcv_with_pit_financials",
        fail_enrich,
    )

    result = fetch_walkforward_frames(
        WalkforwardFetchRequest(
            source="baostock",
            symbols=["600519"],
            start="2024-01-01",
            end="2024-06-30",
            skip_pit_financials=True,
        ),
        get_source_fn=lambda name: Source() if name == "baostock" else None,
        fetch_frames_for_cli_fn=lambda *args, **kwargs: {},
        load_csv_fn=lambda _path: {},
        fetch_days_fn=lambda _start, _end: 0,
        print_fn=lambda _message: None,
    )

    assert result.frames.keys() == {"600519"}
    assert called is False


def test_fetch_walkforward_frames_loads_csv_fallback_source() -> None:
    result = fetch_walkforward_frames(
        WalkforwardFetchRequest(
            source="fixtures/sample.csv",
            symbols=["600519"],
            start="2024-01-01",
            end="2024-06-30",
        ),
        get_source_fn=lambda _name: None,
        fetch_frames_for_cli_fn=lambda *args, **kwargs: {},
        load_csv_fn=lambda path: {path: _frame()},
        fetch_days_fn=lambda _start, _end: 0,
    )

    assert result.frames.keys() == {"fixtures/sample.csv"}
    assert result.symbols == ["600519"]
