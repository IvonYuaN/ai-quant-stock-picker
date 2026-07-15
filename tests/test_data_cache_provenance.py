from __future__ import annotations

from datetime import date, timezone
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from aqsp.core.time import now_shanghai
from aqsp.core.errors import DataError
from aqsp.data.cache import DataCache
from aqsp.data.eastmoney_source import EastmoneySource
from aqsp.data.akshare_source import AkshareSource
from aqsp.data.sqlite_db_source import SqliteDbSource


def _ohlcv_frame(day: date | str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [str(day)],
            "open": [10.0],
            "high": [10.5],
            "low": [9.5],
            "close": [10.0],
            "volume": [1000.0],
            "amount": [10000.0],
            "suspended": [0],
            "limit_up": [0.0],
            "limit_down": [0.0],
            "adj_factor": [1.0],
        }
    )


def _eastmoney_source(cache: DataCache) -> EastmoneySource:
    source = EastmoneySource.__new__(EastmoneySource)
    source.name = "eastmoney"
    source.cache = cache
    source._active_workload = None
    return source


def test_eastmoney_live_short_does_not_use_historical_sqlite_cache(
    tmp_path: Path,
) -> None:
    cache = DataCache(tmp_path / "cache.db")
    day = now_shanghai().date()
    cache.set_ohlcv(
        "600000",
        _ohlcv_frame(day),
        source="sqlite_db",
        workload="walkforward",
    )
    source = _eastmoney_source(cache)
    source.set_workload("live_short")

    def fetch_online(*_args, **_kwargs) -> pd.DataFrame:
        frame = _ohlcv_frame(day)
        frame["close"] = 10.2
        return frame

    source._fetch_eastmoney_daily = fetch_online

    result = source.fetch_daily(["600000"], day, day)

    assert result["600000"]["close"].iloc[0] == pytest.approx(10.2)
    assert result["600000"].attrs["source_name"] == "eastmoney"
    assert result["600000"].attrs["workload"] == "live_short"


def test_eastmoney_live_short_hits_matching_realtime_cache(tmp_path: Path) -> None:
    cache = DataCache(tmp_path / "cache.db")
    day = now_shanghai().date()
    cache.set_ohlcv(
        "600000",
        _ohlcv_frame(day),
        source="eastmoney",
        workload="live_short",
    )
    source = _eastmoney_source(cache)
    source.set_workload("live_short")
    source._fetch_eastmoney_daily = lambda *_args, **_kwargs: pytest.fail(
        "matching realtime cache should be used"
    )

    result = source.fetch_daily(["600000"], day, day)

    assert result["600000"]["close"].iloc[0] == pytest.approx(10.0)
    assert result["600000"].attrs["source_name"] == "eastmoney"


def test_cache_source_mismatch_is_an_explicit_miss(tmp_path: Path) -> None:
    cache = DataCache(tmp_path / "cache.db")
    day = now_shanghai().date()
    cache.set_ohlcv(
        "600000",
        _ohlcv_frame(day),
        source="sina",
        workload="live_short",
    )

    assert (
        cache.get_ohlcv(
            "600000",
            day,
            day,
            source="eastmoney",
            workload="live_short",
        )
        is None
    )


def test_cache_workload_partitions_same_symbol_and_date(tmp_path: Path) -> None:
    cache = DataCache(tmp_path / "cache.db")
    day = date(2024, 1, 2)
    cache.set_ohlcv(
        "600000",
        _ohlcv_frame(day).assign(close=10.0),
        source="eastmoney",
        workload="historical",
    )
    cache.set_ohlcv(
        "600000",
        _ohlcv_frame(day).assign(close=10.2),
        source="eastmoney",
        workload="live_short",
    )

    historical = cache.get_ohlcv(
        "600000", day, day, source="eastmoney", workload="historical"
    )
    live = cache.get_ohlcv(
        "600000", day, day, source="eastmoney", workload="live_short"
    )

    assert historical is not None
    assert live is not None
    assert historical["close"].iloc[0] == pytest.approx(10.0)
    assert live["close"].iloc[0] == pytest.approx(10.2)
    default = cache.get_ohlcv("600000", day, day, source="eastmoney")
    assert default is not None
    assert default["close"].iloc[0] == pytest.approx(10.0)


def test_cache_migrates_legacy_tables_to_workload_partitioned_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-cache.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE ohlcv (
                symbol TEXT NOT NULL, date TEXT NOT NULL,
                price_mode TEXT NOT NULL DEFAULT 'raw',
                open REAL, high REAL, low REAL, close REAL,
                volume REAL, amount REAL, suspended INTEGER,
                limit_up REAL, limit_down REAL, adj_factor REAL,
                source TEXT, fetched_at TEXT,
                PRIMARY KEY (symbol, date, price_mode)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE index_ohlcv (
                code TEXT NOT NULL, date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                volume REAL, amount REAL, source TEXT, fetched_at TEXT,
                PRIMARY KEY (code, date)
            )
            """
        )

    cache = DataCache(path)
    with sqlite3.connect(path) as conn:
        ohlcv_pk = [
            row[1]
            for row in sorted(conn.execute("PRAGMA table_info(ohlcv)"), key=lambda row: row[5])
            if row[5]
        ]
        index_pk = [
            row[1]
            for row in sorted(
                conn.execute("PRAGMA table_info(index_ohlcv)"), key=lambda row: row[5]
            )
            if row[5]
        ]

    assert ohlcv_pk == ["symbol", "date", "price_mode", "workload"]
    assert index_pk == ["code", "date", "workload"]
    cache.set_ohlcv(
        "600000", _ohlcv_frame(date(2024, 1, 2)), source="eastmoney", workload="historical"
    )
    cache.set_ohlcv(
        "600000", _ohlcv_frame(date(2024, 1, 2)), source="eastmoney", workload="live_short"
    )
    assert cache.get_ohlcv(
        "600000", date(2024, 1, 2), date(2024, 1, 2), source="eastmoney", workload="historical"
    ) is not None
    assert cache.get_ohlcv(
        "600000", date(2024, 1, 2), date(2024, 1, 2), source="eastmoney", workload="live_short"
    ) is not None


def test_cache_without_provenance_fails_closed_for_live_short_but_history_works(
    tmp_path: Path,
) -> None:
    cache = DataCache(tmp_path / "cache.db")
    day = date(2024, 1, 2)
    cache.set_ohlcv("600000", _ohlcv_frame(day), source="sqlite_db")
    with sqlite3.connect(cache.db_path) as conn:
        conn.execute(
            "UPDATE ohlcv SET timestamp_source = NULL WHERE symbol = ?",
            ("600000",),
        )
        conn.commit()

    assert (
        cache.get_ohlcv(
            "600000",
            day,
            day,
            source="sqlite_db",
            workload="live_short",
        )
        is None
    )
    assert cache.get_ohlcv("600000", day, day) is None

    cache.set_ohlcv("600000", _ohlcv_frame(day), source="sqlite_db")
    assert cache.get_ohlcv("600000", day, day) is not None


def test_cache_rejects_future_fetched_at_when_timestamp_is_not_yet_observed(
    tmp_path: Path,
) -> None:
    cache = DataCache(tmp_path / "cache.db")
    day = date(2024, 1, 2)
    cache.set_ohlcv("600000", _ohlcv_frame(day), source="sqlite_db")
    future = (now_shanghai() + pd.Timedelta(hours=1)).isoformat()
    with sqlite3.connect(cache.db_path) as conn:
        conn.execute(
            "UPDATE ohlcv SET fetched_at = ? WHERE symbol = ?",
            (future, "600000"),
        )
        conn.commit()

    assert cache.get_ohlcv("600000", day, day) is None


def test_cache_compares_fetched_at_across_timezones_when_snapshot_is_fresh(
    tmp_path: Path,
) -> None:
    cache = DataCache(tmp_path / "cache.db")
    day = now_shanghai().date()
    cache.set_ohlcv("600000", _ohlcv_frame(day), source="eastmoney")
    fetched_at_utc = (now_shanghai() - pd.Timedelta(minutes=30)).astimezone(
        timezone.utc
    )
    with sqlite3.connect(cache.db_path) as conn:
        conn.execute(
            "UPDATE ohlcv SET fetched_at = ? WHERE symbol = ?",
            (fetched_at_utc.isoformat(), "600000"),
        )
        conn.commit()

    result = cache.get_ohlcv(
        "600000",
        day,
        day,
        max_age_hours=1,
    )

    assert result is not None


def test_cache_keeps_live_and_historical_workloads_as_separate_rows(
    tmp_path: Path,
) -> None:
    cache = DataCache(tmp_path / "cache.db")
    day = date(2024, 1, 2)
    historical = _ohlcv_frame(day)
    historical["close"] = 10.0
    live = _ohlcv_frame(day)
    live["close"] = 10.5
    cache.set_ohlcv("600000", historical, source="eastmoney", workload="historical")
    cache.set_ohlcv("600000", live, source="eastmoney", workload="live_short")

    history_result = cache.get_ohlcv(
        "600000", day, day, source="eastmoney", workload="historical"
    )
    live_result = cache.get_ohlcv(
        "600000", day, day, source="eastmoney", workload="live_short"
    )

    assert history_result is not None
    assert live_result is not None
    assert history_result["close"].iloc[0] == pytest.approx(10.0)
    assert live_result["close"].iloc[0] == pytest.approx(10.5)


def test_sqlite_source_rejects_live_short_before_reading_historical_db(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "historical.db"
    sqlite3.connect(db_path).close()
    source = SqliteDbSource(db_path=db_path, cache=None)
    source.set_workload("live_short")

    with pytest.raises(DataError, match="sqlite_db 不适合 live_short"):
        source.fetch_daily(["600000"], date(2024, 1, 2), date(2024, 1, 2))


def test_akshare_live_short_does_not_reuse_historical_cache(
    tmp_path: Path,
) -> None:
    cache = DataCache(tmp_path / "cache.db")
    day = now_shanghai().date()
    cache.set_ohlcv(
        "600000",
        _ohlcv_frame(day),
        source="sqlite_db",
        workload="walkforward",
    )
    source = AkshareSource.__new__(AkshareSource)
    source.name = "akshare"
    source.cache = cache
    source._active_workload = None
    source._ak = SimpleNamespace(
        stock_zh_a_hist=lambda **_kwargs: _ohlcv_frame(day).assign(close=10.2)
    )
    source.set_workload("live_short")

    result = source.fetch_daily(["600000"], day, day)

    assert result["600000"]["close"].iloc[0] == pytest.approx(10.2)
    assert result["600000"].attrs["source_name"] == "akshare"
    assert result["600000"].attrs["workload"] == "live_short"
