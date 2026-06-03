from __future__ import annotations

from types import SimpleNamespace

import pytest
import pandas as pd

from aqsp.data.source import DataSource
from aqsp.data.akshare_source import AkshareSource
from aqsp.data.eastmoney_source import EastmoneySource
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


def test_eastmoney_normalize_df_preserves_meaningful_name():
    source = EastmoneySource.__new__(EastmoneySource)
    source.cache = None
    df = pd.DataFrame(
        {
            "date": ["2026-05-27", "2026-05-28"],
            "open": [10.0, 10.1],
            "close": [10.2, 10.3],
            "high": [10.5, 10.6],
            "low": [9.9, 10.0],
            "volume": [1000, 2000],
            "amount": [10000, 20000],
            "name": ["宁德时代", "宁德时代"],
        }
    )

    normalized = source._normalize_eastmoney_df(df, "300750")

    assert normalized["symbol"].iloc[0] == "300750"
    assert normalized["name"].iloc[0] == "宁德时代"


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


def test_akshare_realtime_snapshot_reuses_cache_within_interval(monkeypatch):
    calls = {"count": 0}

    def fake_spot():
        calls["count"] += 1
        return pd.DataFrame(
            [
                {
                    "代码": "600000",
                    "最新价": 10.0,
                    "买一价": 9.9,
                    "卖一价": 10.1,
                    "成交量": 1000,
                    "成交额": 10000,
                }
            ]
        )

    source = AkshareSource.__new__(AkshareSource)
    source._ak = SimpleNamespace(stock_zh_a_spot_em=fake_spot)
    source.cache = None
    source._realtime_min_interval_sec = 30.0
    source._realtime_failure_cooldown_sec = 180.0
    source._last_realtime_fetch_ts = 0.0
    source._realtime_cooldown_until = 0.0
    source._cached_realtime_snapshot = None
    source._cached_realtime_snapshot_ts = 0.0
    source.name = "akshare"
    clock = {"value": 100.0}
    monkeypatch.setattr(
        "aqsp.data.akshare_source.time.monotonic", lambda: clock["value"]
    )

    first = source.fetch_realtime_quote(["600000"])
    second = source.fetch_realtime_quote(["600000"])

    assert first["600000"]["price"] == 10.0
    assert second["600000"]["price"] == 10.0
    assert calls["count"] == 1


def test_akshare_realtime_snapshot_enters_cooldown_after_failure(monkeypatch):
    calls = {"count": 0}

    def boom():
        calls["count"] += 1
        raise RuntimeError("429")

    source = AkshareSource.__new__(AkshareSource)
    source._ak = SimpleNamespace(stock_zh_a_spot_em=boom)
    source.cache = None
    source._realtime_min_interval_sec = 30.0
    source._realtime_failure_cooldown_sec = 180.0
    source._last_realtime_fetch_ts = 0.0
    source._realtime_cooldown_until = 0.0
    source._cached_realtime_snapshot = None
    source._cached_realtime_snapshot_ts = 0.0
    source.name = "akshare"
    clock = {"value": 200.0}
    monkeypatch.setattr(
        "aqsp.data.akshare_source.time.monotonic", lambda: clock["value"]
    )

    with pytest.raises(DataError, match="进入冷却 180s"):
        source.fetch_realtime_quote(["600000"])
    with pytest.raises(DataError, match="冷却中"):
        source.fetch_realtime_quote(["600000"])

    assert calls["count"] == 1
