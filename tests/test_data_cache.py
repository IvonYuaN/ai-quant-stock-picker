from __future__ import annotations

from datetime import date
import pandas as pd

from aqsp.data.cache import DataCache
from aqsp.data.adjust import AdjustmentService


def test_cache_init(tmp_path):
    cache_path = tmp_path / "test_cache.db"
    cache = DataCache(db_path=cache_path)
    assert cache.db_path.exists()


def test_cache_set_and_get_ohlcv(tmp_path):
    cache = DataCache(db_path=tmp_path / "test_cache.db")
    df = pd.DataFrame(
        {
            "date": ["2026-05-27", "2026-05-28"],
            "open": [10.0, 10.1],
            "high": [10.5, 10.6],
            "low": [9.9, 10.0],
            "close": [10.2, 10.3],
            "volume": [1000, 2000],
            "amount": [10000, 20000],
            "suspended": [0, 0],
            "limit_up": [0.0, 0.0],
            "limit_down": [0.0, 0.0],
            "adj_factor": [1.0, 1.0],
        }
    )
    cache.set_ohlcv("600000", df, source="test")

    start = date(2026, 5, 27)
    end = date(2026, 5, 28)
    result = cache.get_ohlcv("600000", start, end, max_age_hours=1)
    assert result is not None
    assert len(result) == 2
    assert result["close"].iloc[0] == 10.2


def test_cache_get_empty_when_not_cached(tmp_path):
    cache = DataCache(db_path=tmp_path / "test_cache.db")
    start = date(2026, 5, 27)
    end = date(2026, 5, 28)
    result = cache.get_ohlcv("600000", start, end)
    assert result is None


def test_cache_adj_factor(tmp_path):
    cache = DataCache(db_path=tmp_path / "test_cache.db")
    df = pd.DataFrame(
        {
            "date": ["2026-05-27", "2026-05-28"],
            "adj_factor": [1.0, 1.1],
        }
    )
    cache.set_adj_factors("600000", df, source="test")

    result = cache.get_adj_factor("600000", date(2026, 5, 27))
    assert result == 1.0
    result = cache.get_adj_factor("600000", date(2026, 5, 28))
    assert result == 1.1


def test_cache_clear_expired(tmp_path):
    cache = DataCache(db_path=tmp_path / "test_cache.db")
    df = pd.DataFrame(
        {
            "date": ["2026-05-27"],
            "open": [10.0],
            "high": [10.5],
            "low": [9.9],
            "close": [10.2],
            "volume": [1000],
            "amount": [10000],
        }
    )
    cache.set_ohlcv("600000", df, source="test")

    deleted = cache.clear_expired(max_age_hours=0)
    assert deleted >= 1


def test_adjustment_apply_qfq():
    df = pd.DataFrame(
        {
            "date": ["2026-05-27", "2026-05-28"],
            "symbol": ["600000", "600000"],
            "open": [10.0, 10.1],
            "high": [10.5, 10.6],
            "low": [9.9, 10.0],
            "close": [10.2, 10.3],
            "adj_factor": [1.0, 1.1],
        }
    )
    service = AdjustmentService()
    result = service.apply_qfq(df)

    assert "open_qfq" in result.columns
    assert "close_qfq" in result.columns
    assert result["close_qfq"].iloc[0] == 10.2 / 1.1


def test_adjustment_apply_hfq():
    df = pd.DataFrame(
        {
            "date": ["2026-05-27", "2026-05-28"],
            "symbol": ["600000", "600000"],
            "open": [10.0, 10.1],
            "high": [10.5, 10.6],
            "low": [9.9, 10.0],
            "close": [10.2, 10.3],
            "adj_factor": [1.0, 1.1],
        }
    )
    service = AdjustmentService()
    result = service.apply_hfq(df)

    assert "close_hfq" in result.columns
    assert result["close_hfq"].iloc[0] == 10.2
    assert result["close_hfq"].iloc[1] == 10.3 * 1.1


def test_adjustment_get_point_in_time_factors(tmp_path):
    cache = DataCache(db_path=tmp_path / "test_cache.db")
    df = pd.DataFrame(
        {
            "date": ["2026-05-27", "2026-05-28"],
            "adj_factor": [1.0, 1.1],
        }
    )
    cache.set_adj_factors("600000", df, source="test")

    service = AdjustmentService(cache=cache)
    factors = service.get_point_in_time_factors("600000", ["2026-05-27", "2026-05-28"])

    assert len(factors) == 2
    assert factors["adj_factor"].iloc[0] == 1.0
    assert factors["adj_factor"].iloc[1] == 1.1
