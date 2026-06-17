from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import pandas as pd

from aqsp.data.source import DataSource
from aqsp.data.akshare_source import AkshareSource
from aqsp.data.eastmoney_source import EastmoneySource
from aqsp.data.sina_source import SinaSource
from aqsp.data.sqlite_db_source import SqliteDbSource
from aqsp.data import fetch_with_source
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


def test_eastmoney_fetch_daily_uses_turnover_amount_not_price_change(monkeypatch):
    class DummyResponse:
        def json(self):
            return {
                "data": {
                    "name": "长江电力",
                    "klines": [
                        "2026-06-05,27.50,27.77,27.88,27.40,123456,987654321,1.75,0.98,0.27,0.56"
                    ],
                }
            }

    class DummySession:
        def get(self, *_args, **_kwargs):
            return DummyResponse()

    source = EastmoneySource.__new__(EastmoneySource)
    source._session = DummySession()
    source.cache = None
    source._last_request_ts = 0.0
    monkeypatch.setattr(source, "_throttle", lambda: None)

    df = source._fetch_eastmoney_daily(
        "600900",
        pd.Timestamp("2026-06-05").date(),
        pd.Timestamp("2026-06-05").date(),
    )

    assert df is not None
    assert df["amount"].iloc[0] == pytest.approx(987654321.0)


def test_public_fetch_methods_raise_data_error_when_eastmoney_returns_empty(
    monkeypatch,
) -> None:
    source = EastmoneySource.__new__(EastmoneySource)
    source.cache = SimpleNamespace(
        get_ohlcv=lambda *_args, **_kwargs: None,
        get_index=lambda *_args, **_kwargs: None,
    )
    source.name = "eastmoney"
    monkeypatch.setattr(source, "_fetch_eastmoney_intraday", lambda *_args: None)
    monkeypatch.setattr(source, "_fetch_eastmoney_quote", lambda *_args: None)
    monkeypatch.setattr(source, "_fetch_eastmoney_index", lambda *_args: None)

    with pytest.raises(DataError, match="eastmoney 分时获取失败"):
        source.fetch_intraday(["600000"])
    with pytest.raises(DataError, match="eastmoney 实时行情获取失败"):
        source.fetch_realtime_quote(["600000"])
    with pytest.raises(DataError, match="eastmoney 指数获取失败"):
        source.fetch_index(["000300"], date(2026, 5, 20), date(2026, 5, 27))


def test_public_fetch_methods_raise_data_error_when_sina_returns_empty(
    monkeypatch,
) -> None:
    source = SinaSource.__new__(SinaSource)
    source.cache = SimpleNamespace(get_index=lambda *_args, **_kwargs: None)
    source.name = "sina"
    monkeypatch.setattr(source, "_fetch_sina_intraday", lambda *_args: None)
    monkeypatch.setattr(source, "_fetch_sina_quote", lambda *_args: None)
    monkeypatch.setattr(source, "_fetch_sina_daily", lambda *_args, **_kwargs: None)

    with pytest.raises(DataError, match="sina 分时获取失败"):
        source.fetch_intraday(["600000"])
    with pytest.raises(DataError, match="sina 实时行情获取失败"):
        source.fetch_realtime_quote(["600000"])
    with pytest.raises(DataError, match="sina 指数获取失败"):
        source.fetch_index(["000300"], date(2026, 5, 20), date(2026, 5, 27))


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


def test_fetch_with_source_keeps_daily_frames_when_optional_benchmark_fails():
    class DummySource:
        name = "dummy"

        def fetch_daily(self, symbols, start, end, adjust=""):
            return {
                "600000": pd.DataFrame(
                    [
                        {
                            "date": "2026-06-03",
                            "symbol": "600000",
                            "name": "浦发银行",
                            "open": 10.0,
                            "high": 10.2,
                            "low": 9.9,
                            "close": 10.1,
                            "volume": 1000,
                            "amount": 10100.0,
                            "suspended": False,
                            "limit_up": 11.11,
                            "limit_down": 9.09,
                        }
                    ]
                )
            }

        def fetch_index(self, index_codes, start, end):
            raise DataError("benchmark unavailable")

    frames = fetch_with_source(
        DummySource(),
        ["600000"],
        days=30,
        benchmark_symbol="000300",
    )

    assert list(frames) == ["600000"]
    assert frames["600000"]["close"].iloc[-1] == 10.1


def test_fetch_with_source_uses_shanghai_today(monkeypatch):
    import aqsp.data as data_mod

    seen: dict[str, date] = {}

    class DummySource:
        name = "dummy"

        def fetch_daily(self, symbols, start, end, adjust=""):
            seen["start"] = start
            seen["end"] = end
            return {"600000": pd.DataFrame([{"date": "2026-06-13", "close": 10.1}])}

    monkeypatch.setattr(data_mod, "today_shanghai", lambda: date(2026, 6, 13))

    fetch_with_source(DummySource(), ["600000"], days=30)

    assert seen["end"] == date(2026, 6, 13)
    assert seen["start"] == date(2025, 6, 13)


def test_fetch_with_source_raises_when_source_returns_no_valid_frames() -> None:
    class DummySource:
        name = "dummy"

        def fetch_daily(self, symbols, start, end, adjust=""):
            return {}

        def fetch_index(self, index_codes, start, end):
            return {}

    with pytest.raises(DataError, match="未返回任何有效日线"):
        fetch_with_source(DummySource(), ["600000"], days=30)


def test_sqlite_db_source_fetch_index_uses_raw_close_when_qfq_differs(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "astocks_qfq.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            """
            CREATE TABLE daily_qfq (
                ts_code TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                open_qfq REAL,
                high_qfq REAL,
                low_qfq REAL,
                close_qfq REAL,
                volume REAL,
                amount REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO stocks (ts_code, name) VALUES (?, ?)",
            ("000300.SH", "沪深300"),
        )
        conn.execute(
            """
            INSERT INTO daily_qfq (
                ts_code, trade_date, open, high, low, close,
                open_qfq, high_qfq, low_qfq, close_qfq, volume, amount
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "000300.SH",
                "20260612",
                3500.0,
                3510.0,
                3490.0,
                3505.0,
                3000.0,
                3010.0,
                2990.0,
                3005.0,
                1000.0,
                350500000.0,
            ),
        )

    source = SqliteDbSource(db_path=db_path, cache=None)

    result = source.fetch_index(
        ["000300"],
        date(2026, 6, 12),
        date(2026, 6, 12),
    )

    df = result["000300"]
    assert df["close"].iloc[0] == 3505.0
    assert df["close"].iloc[0] != 3005.0


def test_sqlite_db_source_intraday_raises_unsupported(tmp_path: Path) -> None:
    db_path = tmp_path / "astocks_qfq.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT)")
    source = SqliteDbSource(db_path=db_path, cache=None)

    with pytest.raises(DataError, match="不支持分时数据"):
        source.fetch_intraday(["600000"])


def test_sqlite_db_source_realtime_raises_unsupported(tmp_path: Path) -> None:
    db_path = tmp_path / "astocks_qfq.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT)")
    source = SqliteDbSource(db_path=db_path, cache=None)

    with pytest.raises(DataError, match="不支持实时行情"):
        source.fetch_realtime_quote(["600000"])
