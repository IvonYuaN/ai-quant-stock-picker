from __future__ import annotations

from datetime import date
import sqlite3
import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai
from aqsp.data.adjust import AdjustmentService
from aqsp.data.cache import DataCache


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
    assert result["symbol"].iloc[0] == "600000"
    assert result["name"].iloc[0] == "600000"


def test_cache_get_index_restores_required_columns(tmp_path):
    cache = DataCache(db_path=tmp_path / "test_cache.db")
    df = pd.DataFrame(
        {
            "date": ["2026-05-27", "2026-05-28"],
            "open": [4000.0, 4010.0],
            "high": [4010.0, 4020.0],
            "low": [3990.0, 4000.0],
            "close": [4005.0, 4015.0],
            "volume": [1000000, 1200000],
            "amount": [10000000, 12000000],
            "symbol": ["000300", "000300"],
            "name": ["000300", "000300"],
            "suspended": [False, False],
            "limit_up": [0.0, 0.0],
            "limit_down": [0.0, 0.0],
        }
    )
    cache.set_index("000300", df, source="test")

    result = cache.get_index("000300", date(2026, 5, 27), date(2026, 5, 28))

    assert result is not None
    assert result["symbol"].iloc[0] == "000300"
    assert result["name"].iloc[0] == "000300"
    assert bool(result["suspended"].iloc[0]) is False
    assert result["limit_up"].iloc[0] == 0.0
    assert result["limit_down"].iloc[0] == 0.0


def test_cache_get_empty_when_not_cached(tmp_path):
    cache = DataCache(db_path=tmp_path / "test_cache.db")
    start = date(2026, 5, 27)
    end = date(2026, 5, 28)
    result = cache.get_ohlcv("600000", start, end)
    assert result is None


def test_cache_allows_stale_historical_ohlcv_for_old_ranges(tmp_path) -> None:
    cache = DataCache(db_path=tmp_path / "test_cache.db")
    df = pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-03"],
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

    stale_cutoff = (now_shanghai() - pd.Timedelta(hours=72)).isoformat()
    with sqlite3.connect(cache.db_path) as conn:
        conn.execute("UPDATE ohlcv SET fetched_at = ?", (stale_cutoff,))
        conn.commit()

    result = cache.get_ohlcv(
        "600000",
        date(2024, 1, 2),
        date(2024, 1, 3),
        max_age_hours=1,
    )

    assert result is not None
    assert len(result) == 2


def test_cache_rejects_stale_recent_ohlcv(tmp_path) -> None:
    cache = DataCache(db_path=tmp_path / "test_cache.db")
    recent_day = now_shanghai().date() - pd.Timedelta(days=1)
    df = pd.DataFrame(
        {
            "date": [recent_day.isoformat()],
            "open": [10.0],
            "high": [10.5],
            "low": [9.9],
            "close": [10.2],
            "volume": [1000],
            "amount": [10000],
            "suspended": [0],
            "limit_up": [0.0],
            "limit_down": [0.0],
            "adj_factor": [1.0],
        }
    )
    cache.set_ohlcv("600000", df, source="test")

    stale_cutoff = (now_shanghai() - pd.Timedelta(hours=72)).isoformat()
    with sqlite3.connect(cache.db_path) as conn:
        conn.execute("UPDATE ohlcv SET fetched_at = ?", (stale_cutoff,))
        conn.commit()

    result = cache.get_ohlcv(
        "600000",
        recent_day,
        recent_day,
        max_age_hours=1,
    )

    assert result is None


def test_cache_rejects_implausible_amount_scale(tmp_path):
    cache = DataCache(db_path=tmp_path / "test_cache.db")
    df = pd.DataFrame(
        {
            "date": ["2026-06-04", "2026-06-05"],
            "open": [27.5, 27.6],
            "high": [27.8, 27.9],
            "low": [27.4, 27.5],
            "close": [27.7, 27.8],
            "volume": [123456, 123000],
            "amount": [0.27, 0.21],
            "suspended": [0, 0],
            "limit_up": [0.0, 0.0],
            "limit_down": [0.0, 0.0],
            "adj_factor": [1.0, 1.0],
        }
    )
    cache.set_ohlcv("600900", df, source="eastmoney")

    result = cache.get_ohlcv("600900", date(2026, 6, 4), date(2026, 6, 5))

    assert result is None


def test_cache_separates_raw_and_qfq_price_modes(tmp_path):
    cache = DataCache(db_path=tmp_path / "test_cache.db")
    base = {
        "date": ["2026-05-27"],
        "open": [10.0],
        "high": [10.5],
        "low": [9.9],
        "volume": [1000],
        "amount": [10_000],
        "suspended": [0],
        "limit_up": [0.0],
        "limit_down": [0.0],
        "adj_factor": [1.0],
    }
    raw = pd.DataFrame({**base, "close": [10.2]})
    qfq = pd.DataFrame({**base, "close": [8.8]})

    cache.set_ohlcv("600000", raw, source="test", price_mode="raw")
    cache.set_ohlcv("600000", qfq, source="test", price_mode="qfq")

    start = date(2026, 5, 27)
    end = date(2026, 5, 27)
    raw_result = cache.get_ohlcv("600000", start, end, price_mode="raw")
    qfq_result = cache.get_ohlcv("600000", start, end, price_mode="qfq")

    assert raw_result is not None
    assert qfq_result is not None
    assert raw_result["close"].iloc[0] == 10.2
    assert qfq_result["close"].iloc[0] == 8.8


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


def test_cache_set_financial_persists_dates(tmp_path):
    cache = DataCache(db_path=tmp_path / "test_cache.db")
    df = pd.DataFrame(
        {
            "pubDate": [pd.Timestamp("2026-05-27")],
            "statDate": [pd.Timestamp("2026-03-31")],
            "roeAvg": [12.3],
            "npMargin": [8.1],
            "gpMargin": [15.2],
            "epsTTM": [1.23],
            "totalShare": [1000000],
        }
    )

    cache.set_financial("600000", df, source="test")
    result = cache.get_financial("600000")

    assert result is not None
    assert result["pubDate"].iloc[0].startswith("2026-05-27")
    assert result["statDate"].iloc[0].startswith("2026-03-31")


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


def test_adjustment_get_point_in_time_factors_raises_when_missing(tmp_path):
    cache = DataCache(db_path=tmp_path / "test_cache.db")
    service = AdjustmentService(cache=cache)

    with pytest.raises(DataError, match="缺少复权因子"):
        service.get_point_in_time_factors("600000", ["2026-05-27"])


def test_adjustment_rejects_multi_symbol_without_explicit_factors():
    df = pd.DataFrame(
        {
            "date": ["2026-05-27", "2026-05-27"],
            "symbol": ["600000", "000001"],
            "open": [10.0, 12.0],
            "high": [10.5, 12.5],
            "low": [9.9, 11.9],
            "close": [10.2, 12.2],
        }
    )
    service = AdjustmentService()

    with pytest.raises(DataError, match="多标的复权必须先显式提供 adj_factor"):
        service.apply_qfq(df)


def test_adjustment_rejects_incomplete_explicit_factors():
    df = pd.DataFrame(
        {
            "date": ["2026-05-27", "2026-05-28"],
            "symbol": ["600000", "600000"],
            "open": [10.0, 12.0],
            "high": [10.5, 12.5],
            "low": [9.9, 11.9],
            "close": [10.2, 12.2],
            "adj_factor": [None, 1.1],
        }
    )
    service = AdjustmentService()

    with pytest.raises(DataError, match="复权因子不完整"):
        service.apply_qfq(df)
