from __future__ import annotations

from datetime import date
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


def test_cache_without_provenance_fails_closed_for_live_short_but_history_works(
    tmp_path: Path,
) -> None:
    cache = DataCache(tmp_path / "cache.db")
    day = date(2024, 1, 2)
    cache.set_ohlcv("600000", _ohlcv_frame(day), source="sqlite_db")
    with sqlite3.connect(cache.db_path) as conn:
        conn.execute(
            "UPDATE ohlcv SET workload = NULL, timestamp_source = NULL WHERE symbol = ?",
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
