from __future__ import annotations

import pytest
import pandas as pd

from aqsp.data.source import DataSource
from aqsp.data.akshare_source import AkshareSource
from aqsp.core.errors import DataError


def test_datasource_is_abstract():
    with pytest.raises(TypeError):
        DataSource()


def test_akshare_source_has_name():
    try:
        source = AkshareSource()
        assert source.name == "akshare"
    except RuntimeError:
        pytest.skip("akshare not installed")


def test_akshare_normalize_df():
    try:
        source = AkshareSource()
    except RuntimeError:
        pytest.skip("akshare not installed")
    df = pd.DataFrame(
        {
            "日期": ["2026-05-27", "2026-05-28"],
            "开盘": [10.0, 10.1],
            "最高": [10.5, 10.6],
            "最低": [9.9, 10.0],
            "收盘": [10.2, 10.3],
            "成交量": [1000, 2000],
            "成交额": [10000, 20000],
            "名称": ["测试股票", "测试股票"],
        }
    )
    normalized = source._normalize_akshare_df(df, "600000")
    assert "date" in normalized.columns
    assert "symbol" in normalized.columns
    assert "name" in normalized.columns
    assert "open" in normalized.columns
    assert "high" in normalized.columns
    assert "low" in normalized.columns
    assert "close" in normalized.columns
    assert "volume" in normalized.columns
    assert normalized["date"].iloc[0] == "2026-05-27"
    assert normalized["symbol"].iloc[0] == "600000"


def test_validate_ohlcv_missing_columns():
    df = pd.DataFrame(
        {
            "date": ["2026-05-27"],
            "symbol": ["600000"],
            "open": [10.0],
        }
    )
    source = AkshareSource.__new__(AkshareSource)
    source.name = "test"
    with pytest.raises(DataError):
        source._validate_ohlcv(df, "600000")


def test_validate_ohlcv_valid():
    df = pd.DataFrame(
        {
            "date": ["2026-05-27"],
            "symbol": ["600000"],
            "name": ["测试"],
            "open": [10.0],
            "high": [10.5],
            "low": [9.5],
            "close": [10.2],
            "volume": [1000],
            "amount": [10_200],
            "suspended": [False],
            "limit_up": [11.22],
            "limit_down": [9.18],
        }
    )
    source = AkshareSource.__new__(AkshareSource)
    source.name = "test"
    result = source._validate_ohlcv(df, "600000")
    assert result is not None


def test_validate_ohlcv_requires_architecture_schema():
    df = pd.DataFrame(
        {
            "date": ["2026-05-27"],
            "symbol": ["600000"],
            "name": ["测试"],
            "open": [10.0],
            "high": [10.5],
            "low": [9.5],
            "close": [10.2],
            "volume": [1000],
        }
    )
    source = AkshareSource.__new__(AkshareSource)
    source.name = "test"

    with pytest.raises(DataError, match="amount"):
        source._validate_ohlcv(df, "600000")


def test_normalize_date():
    df = pd.DataFrame({"日期": ["2026-05-27", "2026-05-28"]})
    source = AkshareSource.__new__(AkshareSource)
    source.name = "test"
    result = source._normalize_date(df)
    assert "date" in result.columns
    assert result["date"].iloc[0] == "2026-05-27"


def test_normalize_symbol():
    df = pd.DataFrame({"代码": ["600000", "600001"]})
    source = AkshareSource.__new__(AkshareSource)
    source.name = "test"
    result = source._normalize_symbol(df, "600000")
    assert "symbol" in result.columns
    assert result["symbol"].dtype.name in ("object", "string", "str")
