"""
Test suite for MultiSourceFetcher with Tushare/Akshare fallback.

Tests:
1. Successful fetch from primary source
2. Fallback to secondary when primary fails
3. Data format consistency
4. Logging of source switching
"""

from __future__ import annotations

import logging
from datetime import date
from unittest.mock import Mock

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.data.cache import DataCache
from aqsp.data.fetcher import MultiSourceFetcher


# Configure logging for tests
logging.basicConfig(level=logging.DEBUG)
_logger = logging.getLogger(__name__)


@pytest.fixture
def sample_ohlcv_data() -> pd.DataFrame:
    """Create sample OHLCV data for testing."""
    dates = pd.date_range(start="2024-01-01", periods=10, freq="D")
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "symbol": "000001",
            "name": "平安银行",
            "open": [10.0 + i * 0.1 for i in range(10)],
            "high": [10.5 + i * 0.1 for i in range(10)],
            "low": [9.9 + i * 0.1 for i in range(10)],
            "close": [10.2 + i * 0.1 for i in range(10)],
            "volume": [1000000 + i * 10000 for i in range(10)],
            "amount": [10200000 + i * 102000 for i in range(10)],
            "suspended": [False] * 10,
            "limit_up": [11.0 + i * 0.1 for i in range(10)],
            "limit_down": [9.0 + i * 0.1 for i in range(10)],
        }
    )


class TestMultiSourceFetcherSuccessPath:
    """Test successful data fetching from primary source."""

    def test_fetch_from_primary_source(self, sample_ohlcv_data):
        """Test successful fetch from primary Tushare source."""
        # Setup mock sources
        primary_source = Mock()
        primary_source.name = "tushare"
        primary_source.fetch_daily = Mock(return_value={"000001": sample_ohlcv_data})

        fallback_source = Mock()
        fallback_source.name = "akshare"
        fallback_source.fetch_daily = Mock()

        cache = Mock(spec=DataCache)

        # Create fetcher
        fetcher = MultiSourceFetcher(primary_source, fallback_source, cache=cache)

        # Fetch data
        start = date(2024, 1, 1)
        end = date(2024, 1, 10)
        result = fetcher.fetch_daily_data(["000001"], start, end)

        # Verify
        assert "000001" in result
        assert not result["000001"].empty
        assert fetcher.get_last_source_used("000001") == "tushare"
        primary_source.fetch_daily.assert_called_once()
        fallback_source.fetch_daily.assert_not_called()

    def test_data_format_normalization(self, sample_ohlcv_data):
        """Test that data is normalized to standard column format."""
        primary_source = Mock()
        primary_source.name = "tushare"
        primary_source.fetch_daily = Mock(return_value={"000001": sample_ohlcv_data})

        fallback_source = Mock()
        fallback_source.name = "akshare"

        cache = Mock(spec=DataCache)

        fetcher = MultiSourceFetcher(primary_source, fallback_source, cache=cache)

        start = date(2024, 1, 1)
        end = date(2024, 1, 10)
        result = fetcher.fetch_daily_data(["000001"], start, end)

        df = result["000001"]

        # Verify standard columns are present
        required_cols = {
            "date",
            "symbol",
            "name",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "suspended",
            "limit_up",
            "limit_down",
        }
        assert required_cols.issubset(set(df.columns))

        # Verify data types
        assert df["date"].dtype == "object"  # String format
        assert pd.api.types.is_numeric_dtype(df["close"])
        assert pd.api.types.is_numeric_dtype(df["volume"])


class TestMultiSourceFetcherFallback:
    """Test fallback behavior when primary source fails."""

    def test_fallback_to_akshare_on_primary_failure(self, sample_ohlcv_data):
        """Test automatic fallback to Akshare when Tushare fails."""
        # Setup mock sources
        primary_source = Mock()
        primary_source.name = "tushare"
        primary_source.fetch_daily = Mock(side_effect=Exception("Tushare API error"))

        fallback_source = Mock()
        fallback_source.name = "akshare"
        fallback_source.fetch_daily = Mock(return_value={"000001": sample_ohlcv_data})

        cache = Mock(spec=DataCache)

        fetcher = MultiSourceFetcher(primary_source, fallback_source, cache=cache)

        start = date(2024, 1, 1)
        end = date(2024, 1, 10)
        result = fetcher.fetch_daily_data(["000001"], start, end)

        # Verify fallback was used
        assert "000001" in result
        assert fetcher.get_last_source_used("000001") == "akshare"
        primary_source.fetch_daily.assert_called_once()
        fallback_source.fetch_daily.assert_called_once()

    def test_both_sources_fail_raises_error(self):
        """Test that DataError is raised when both sources fail."""
        # Setup mock sources that both fail
        primary_source = Mock()
        primary_source.name = "tushare"
        primary_source.fetch_daily = Mock(side_effect=Exception("Tushare error"))

        fallback_source = Mock()
        fallback_source.name = "akshare"
        fallback_source.fetch_daily = Mock(side_effect=Exception("Akshare error"))

        cache = Mock(spec=DataCache)

        fetcher = MultiSourceFetcher(primary_source, fallback_source, cache=cache)

        start = date(2024, 1, 1)
        end = date(2024, 1, 10)

        # Verify error is raised
        with pytest.raises(DataError):
            fetcher.fetch_daily_data(["000001"], start, end)

    def test_partial_fallback_mixed_sources(self, sample_ohlcv_data):
        """Test that some symbols come from primary and others from fallback."""
        modified_data = sample_ohlcv_data.copy()
        modified_data["symbol"] = "000002"
        modified_data["name"] = "万科A"

        # Primary succeeds for 000001 but fails for 000002
        primary_source = Mock()
        primary_source.name = "tushare"
        primary_source.fetch_daily = Mock(return_value={"000001": sample_ohlcv_data})

        # Fallback provides 000002
        fallback_source = Mock()
        fallback_source.name = "akshare"
        fallback_source.fetch_daily = Mock(return_value={"000002": modified_data})

        cache = Mock(spec=DataCache)

        fetcher = MultiSourceFetcher(primary_source, fallback_source, cache=cache)

        start = date(2024, 1, 1)
        end = date(2024, 1, 10)
        result = fetcher.fetch_daily_data(["000001", "000002"], start, end)

        # Verify both symbols present
        assert "000001" in result
        assert "000002" in result
        assert fetcher.get_last_source_used("000001") == "tushare"
        assert fetcher.get_last_source_used("000002") == "akshare"

    def test_partial_result_after_validation_raises_data_error(self, sample_ohlcv_data):
        """Test that validated subsets do not silently pass as complete data."""
        invalid_data = sample_ohlcv_data.copy()
        invalid_data["close"] = None

        primary_source = Mock()
        primary_source.name = "tushare"
        primary_source.fetch_daily = Mock(
            return_value={"000001": sample_ohlcv_data, "000002": invalid_data}
        )

        fallback_source = Mock()
        fallback_source.name = "akshare"
        fallback_source.fetch_daily = Mock()

        cache = Mock(spec=DataCache)
        fetcher = MultiSourceFetcher(primary_source, fallback_source, cache=cache)

        with pytest.raises(DataError, match="数据验证后缺少请求标的"):
            fetcher.fetch_daily_data(
                ["000001", "000002"],
                date(2024, 1, 1),
                date(2024, 1, 10),
            )


class TestLoggingBehavior:
    """Test logging of data source switching."""

    def test_log_primary_success(self, sample_ohlcv_data, caplog):
        """Test that successful primary fetch is logged."""
        primary_source = Mock()
        primary_source.name = "tushare"
        primary_source.fetch_daily = Mock(return_value={"000001": sample_ohlcv_data})

        fallback_source = Mock()
        fallback_source.name = "akshare"

        cache = Mock(spec=DataCache)

        fetcher = MultiSourceFetcher(primary_source, fallback_source, cache=cache)

        with caplog.at_level(logging.INFO):
            fetcher.fetch_daily_data(["000001"], date(2024, 1, 1), date(2024, 1, 10))

        # Verify log contains success indicator
        assert any("tushare" in record.message.lower() for record in caplog.records)

    def test_log_fallback_switch(self, sample_ohlcv_data, caplog):
        """Test that fallback switching is logged."""
        primary_source = Mock()
        primary_source.name = "tushare"
        primary_source.fetch_daily = Mock(side_effect=Exception("Tushare error"))

        fallback_source = Mock()
        fallback_source.name = "akshare"
        fallback_source.fetch_daily = Mock(return_value={"000001": sample_ohlcv_data})

        cache = Mock(spec=DataCache)

        fetcher = MultiSourceFetcher(primary_source, fallback_source, cache=cache)

        with caplog.at_level(logging.WARNING):
            fetcher.fetch_daily_data(["000001"], date(2024, 1, 1), date(2024, 1, 10))

        # Verify log contains fallback indicator
        assert any(
            "fallback" in record.message.lower() or "akshare" in record.message.lower()
            for record in caplog.records
        )


class TestColumnNormalization:
    """Test data column standardization."""

    def test_normalize_chinese_column_names(self):
        """Test conversion of Chinese column names to English."""
        df = pd.DataFrame(
            {
                "日期": ["2024-01-01"],
                "代码": ["000001"],
                "名称": ["平安银行"],
                "开盘": [10.0],
                "最高": [10.5],
                "最低": [9.9],
                "收盘": [10.2],
                "成交量": [1000000],
                "成交额": [10200000],
            }
        )

        primary_source = Mock()
        primary_source.name = "source"
        primary_source.fetch_daily = Mock(return_value={"000001": df})

        fallback_source = Mock()
        cache = Mock(spec=DataCache)

        fetcher = MultiSourceFetcher(primary_source, fallback_source, cache=cache)

        result = fetcher.fetch_daily_data(
            ["000001"], date(2024, 1, 1), date(2024, 1, 1)
        )

        normalized_df = result["000001"]

        # Verify Chinese names converted to English
        assert "date" in normalized_df.columns
        assert "symbol" in normalized_df.columns
        assert "open" in normalized_df.columns
        assert "close" in normalized_df.columns


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
