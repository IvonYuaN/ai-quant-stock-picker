from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import pandas as pd

from aqsp.data.cache import DataCache
from aqsp.data.source import DataSource, apply_limit_suspended_adj, get_limit_pct
from aqsp.data.akshare_source import AkshareSource
from aqsp.data.eastmoney_source import EastmoneySource
from aqsp.data.mootdx_source import MootdxSource
from aqsp.data.sina_source import SinaSource
from aqsp.data.sqlite_db_source import SqliteDbSource
from aqsp.data.tencent_source import TencentSource
from aqsp.data import fetch_frames_for_cli_with_metadata, fetch_with_source
from aqsp.core.errors import DataError

try:
    from aqsp.data.baostock_source import BaostockSource
except ModuleNotFoundError:  # pragma: no cover - depends on optional local package
    BaostockSource = None


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


def test_eastmoney_get_liquid_symbols_uses_spot_snapshot(monkeypatch) -> None:
    source = EastmoneySource.__new__(EastmoneySource)
    source.name = "eastmoney"
    monkeypatch.setattr(source, "_throttle", lambda: None)

    pages = {
        1: {
            "data": {
                "diff": [
                    {"f12": "600000", "f14": "浦发银行", "f2": 10.0, "f5": 1, "f6": 80_000_000},
                    {"f12": "000001", "f14": "平安银行", "f2": 10.0, "f5": 1, "f6": 120_000_000},
                    {"f12": "000002", "f14": "ST测试", "f2": 3.0, "f5": 1, "f6": 200_000_000},
                    {"f12": "300001", "f14": "特锐德", "f2": 20.0, "f5": 1, "f6": "-"},
                ]
            }
        }
    }

    monkeypatch.setattr(source, "_fetch_eastmoney_spot_page", lambda page: pages[page])

    assert source.get_liquid_symbols(limit=2, min_amount=50_000_000) == [
        "000001",
        "600000",
    ]
    assert source.get_available_symbols() == ["600000", "000001", "300001"]


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


def test_http_style_sources_fail_when_one_daily_symbol_returns_empty(
    monkeypatch,
) -> None:
    def frame_for(symbol: str) -> pd.DataFrame | None:
        if symbol == "000001":
            return None
        return pd.DataFrame(
            [
                {
                    "date": "2026-06-05",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 1000,
                    "amount": 10200,
                }
            ]
        )

    class Cache:
        def get_ohlcv(self, *_args, **_kwargs):
            return None

        def set_ohlcv(self, *_args, **_kwargs):
            return None

        def get_adj_factor(self, *_args, **_kwargs):
            return 1.0

    cases = [
        (EastmoneySource, "_fetch_eastmoney_daily", "eastmoney"),
        (SinaSource, "_fetch_sina_daily", "sina"),
        (TencentSource, "_fetch_tencent_daily", "tencent"),
        (MootdxSource, "_fetch_mootdx_daily", "mootdx"),
    ]
    if BaostockSource is not None:
        cases.append((BaostockSource, "_fetch_daily_single", "baostock"))
    for source_cls, helper_name, source_name in cases:
        source = source_cls.__new__(source_cls)
        source.cache = Cache()
        source.name = source_name
        if source_name == "baostock":
            source._logged_in = True
        monkeypatch.setattr(
            source, helper_name, lambda symbol, *_args, **_kwargs: frame_for(symbol)
        )

        with pytest.raises(DataError, match=f"{source_name} 日线获取失败"):
            source.fetch_daily(
                ["600000", "000001"],
                date(2026, 6, 1),
                date(2026, 6, 5),
            )


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


def test_get_limit_pct_uses_precise_beijing_board_prefixes() -> None:
    assert get_limit_pct("430001") == pytest.approx(0.30)
    assert get_limit_pct("830001") == pytest.approx(0.30)
    assert get_limit_pct("400001") == pytest.approx(0.10)
    assert get_limit_pct("800001") == pytest.approx(0.10)


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


def test_apply_limit_suspended_adj_prefers_bulk_adj_factor_lookup() -> None:
    class Cache:
        def __init__(self) -> None:
            self.bulk_calls = 0

        def get_adj_factors(self, symbol: str, dates: list[date]) -> dict[date, float]:
            self.bulk_calls += 1
            assert symbol == "600000"
            assert dates == [date(2026, 5, 27), date(2026, 5, 28)]
            return {
                date(2026, 5, 27): 1.0,
                date(2026, 5, 28): 1.1,
            }

        def get_adj_factor(self, *_args, **_kwargs):
            raise AssertionError("single-row adj lookup should not be used")

    df = pd.DataFrame(
        {
            "date": ["2026-05-27", "2026-05-28"],
            "name": ["测试", "测试"],
            "close": [10.0, 10.5],
            "high": [10.2, 10.8],
            "low": [9.8, 10.1],
            "volume": [1000, 1200],
            "amount": [10000, 12600],
        }
    )

    cache = Cache()
    result = apply_limit_suspended_adj(df, "600000", cache=cache)

    assert cache.bulk_calls == 1
    assert list(result["adj_factor"]) == [1.0, 1.1]


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


def test_validate_ohlcv_rejects_partial_nan_price_values() -> None:
    df = pd.DataFrame(
        {
            "date": ["2026-05-27", "2026-05-28"],
            "symbol": ["600000", "600000"],
            "name": ["测试", "测试"],
            "open": [10.0, None],
            "high": [10.5, 10.6],
            "low": [9.5, 9.6],
            "close": [10.2, 10.3],
            "volume": [1000, 1200],
            "amount": [10_200, 12_360],
            "suspended": [False, False],
            "limit_up": [11.22, 11.33],
            "limit_down": [9.18, 9.27],
        }
    )
    source = AkshareSource.__new__(AkshareSource)
    source.name = "test"

    with pytest.raises(DataError, match="存在无效数值"):
        source._validate_ohlcv(df, "600000")


def test_validate_ohlcv_rejects_impossible_price_range() -> None:
    df = pd.DataFrame(
        {
            "date": ["2026-05-27"],
            "symbol": ["600000"],
            "name": ["测试"],
            "open": [10.8],
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

    with pytest.raises(DataError, match="超出 high-low"):
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


def test_akshare_get_liquid_symbols_uses_realtime_snapshot(monkeypatch):
    calls = {"count": 0}

    def fake_spot():
        calls["count"] += 1
        return pd.DataFrame(
            [
                {"代码": "600000", "名称": "浦发银行", "成交额": 80_000_000},
                {"代码": "000001", "名称": "平安银行", "成交额": 120_000_000},
                {"代码": "000002", "名称": "ST测试", "成交额": 200_000_000},
                {"代码": "300001", "名称": "特锐德", "成交额": "-"},
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
    monkeypatch.setattr("aqsp.data.akshare_source.time.monotonic", lambda: 100.0)

    assert source.get_liquid_symbols(limit=2, min_amount=50_000_000) == [
        "000001",
        "600000",
    ]
    assert source.get_available_symbols() == ["600000", "000001", "300001"]
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


def test_fetch_with_source_uses_explicit_end_date_when_provided(monkeypatch):
    import aqsp.data as data_mod

    seen: dict[str, date] = {}

    class DummySource:
        name = "dummy"

        def fetch_daily(self, symbols, start, end, adjust=""):
            seen["start"] = start
            seen["end"] = end
            return {"600000": pd.DataFrame([{"date": "2026-07-06", "close": 10.1}])}

    monkeypatch.setattr(data_mod, "today_shanghai", lambda: date(2026, 7, 7))

    fetch_with_source(DummySource(), ["600000"], days=30, end_date=date(2026, 7, 6))

    assert seen["end"] == date(2026, 7, 6)
    assert seen["start"] == date(2025, 7, 6)


def test_fetch_frames_for_cli_with_metadata_passes_explicit_end_date() -> None:
    seen: dict[str, object] = {}

    class DummySource:
        name = "dummy"

    def fake_fetch(source, symbols, **kwargs):
        seen["source"] = source.name
        seen["symbols"] = symbols
        seen["end_date"] = kwargs["end_date"]
        return {"600000": pd.DataFrame([{"date": "2026-07-06", "close": 10.1}])}

    frames, actual_source = fetch_frames_for_cli_with_metadata(
        "dummy",
        ["600000"],
        benchmark_symbol=None,
        end_date=date(2026, 7, 6),
        get_source_fn=lambda _source_name, *, cache=None: DummySource(),
        fetch_with_source_fn=fake_fetch,
        record_source_success_fn=lambda requested, actual: seen.update(
            {"success": (requested, actual)}
        ),
        record_source_failure_fn=lambda *_args: None,
    )

    assert actual_source == "dummy"
    assert list(frames) == ["600000"]
    assert seen == {
        "source": "dummy",
        "symbols": ["600000"],
        "end_date": date(2026, 7, 6),
        "success": ("dummy", "dummy"),
    }


def test_fetch_frames_for_cli_with_metadata_blocks_history_source_for_live_short() -> (
    None
):
    seen: dict[str, object] = {}

    def fail_get_source(*_args, **_kwargs):
        raise AssertionError("blocked live_short source must not be opened")

    with pytest.raises(DataError, match="sqlite_db 不适合 live_short"):
        fetch_frames_for_cli_with_metadata(
            "sqlite_db",
            ["600000"],
            benchmark_symbol=None,
            workload="live_short",
            get_source_fn=fail_get_source,
            fetch_with_source_fn=lambda *_args, **_kwargs: {},
            record_source_success_fn=lambda *_args: None,
            record_source_failure_fn=lambda requested, reason: seen.update(
                {"failure": (requested, reason)}
            ),
        )

    assert seen["failure"][0] == "sqlite_db"
    assert "live_short" in seen["failure"][1]


def test_fetch_frames_for_cli_with_metadata_blocks_actual_history_fallback_for_live_short() -> (
    None
):
    seen: dict[str, object] = {}

    class DummySource:
        name = "online_first"
        last_used_source = "sqlite_db"

    def fake_fetch(_source, _symbols, **_kwargs):
        return {"600000": pd.DataFrame([{"date": "2026-07-06", "close": 10.1}])}

    with pytest.raises(DataError, match="实际落到 sqlite_db"):
        fetch_frames_for_cli_with_metadata(
            "online_first",
            ["600000"],
            benchmark_symbol=None,
            workload="live_short",
            get_source_fn=lambda _source_name, *, cache=None: DummySource(),
            fetch_with_source_fn=fake_fetch,
            record_source_success_fn=lambda *_args: seen.update({"success": True}),
            record_source_failure_fn=lambda requested, reason: seen.update(
                {"failure": (requested, reason)}
            ),
        )

    assert "success" not in seen
    assert seen["failure"][0] == "online_first"
    assert "sqlite_db" in seen["failure"][1]


def test_fetch_with_source_raises_when_source_returns_no_valid_frames() -> None:
    class DummySource:
        name = "dummy"

        def fetch_daily(self, symbols, start, end, adjust=""):
            return {}

        def fetch_index(self, index_codes, start, end):
            return {}

    with pytest.raises(DataError, match="未返回任何有效日线"):
        fetch_with_source(DummySource(), ["600000"], days=30)


def test_fetch_with_source_keeps_partial_daily_frames_when_some_symbols_missing(
    caplog,
) -> None:
    class DummySource:
        name = "dummy"

        def fetch_daily(self, symbols, start, end, adjust=""):
            return {"600000": pd.DataFrame([{"date": "2026-06-13", "close": 10.1}])}

        def fetch_index(self, index_codes, start, end):
            return {}

    frames = fetch_with_source(DummySource(), ["600000", "000001"], days=30)

    assert list(frames) == ["600000"]
    assert "日线获取不完整" in caplog.text


def test_live_short_fetch_rejects_partial_daily_frames_explicitly() -> None:
    class DummySource:
        name = "eastmoney"

    failures: list[tuple[str, str]] = []

    with pytest.raises(DataError, match="live_short 日线取数不完整"):
        fetch_frames_for_cli_with_metadata(
            "eastmoney",
            ["600000", "000001"],
            benchmark_symbol=None,
            workload="live_short",
            get_source_fn=lambda _name, *, cache=None: DummySource(),
            fetch_with_source_fn=lambda *_args, **_kwargs: {
                "600000": pd.DataFrame([{"date": "2026-07-15", "close": 10.0}])
            },
            record_source_success_fn=lambda *_args: pytest.fail(
                "partial live_short result must not be recorded as success"
            ),
            record_source_failure_fn=lambda requested, reason: failures.append(
                (requested, reason)
            ),
        )

    assert failures
    assert failures[0][0] == "eastmoney"
    assert "000001" in failures[0][1]


def test_fetch_with_source_skips_bad_symbol_when_other_symbols_succeed() -> None:
    class DummySource:
        name = "dummy"

        def fetch_daily(self, symbols, start, end, adjust=""):
            if len(symbols) > 1:
                raise DataError("batch failed on bad symbol")
            symbol = symbols[0]
            if symbol == "000001":
                raise DataError("bad symbol")
            return {
                symbol: pd.DataFrame(
                    [{"date": "2026-06-13", "symbol": symbol, "close": 10.1}]
                )
            }

        def fetch_index(self, index_codes, start, end):
            return {}

    frames = fetch_with_source(DummySource(), ["600000", "000001"], days=30)

    assert list(frames) == ["600000"]
    assert frames["600000"]["close"].iloc[-1] == 10.1


def _make_sqlite_daily_db(path: Path, symbols: int = 3, days: int = 3) -> None:
    with sqlite3.connect(path) as conn:
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
        for idx in range(symbols):
            market = "SH" if idx % 2 == 0 else "SZ"
            code = f"600{idx:03d}.{market}"
            conn.execute(
                "INSERT INTO stocks (ts_code, name) VALUES (?, ?)", (code, code)
            )
            for day in range(1, days + 1):
                conn.execute(
                    """
                    INSERT INTO daily_qfq (
                        ts_code, trade_date, open, high, low, close,
                        open_qfq, high_qfq, low_qfq, close_qfq, volume, amount
                    ) VALUES (?, ?, 10, 11, 9, ?, 8, 9, 7, ?, 1000, ?)
                    """,
                    (
                        code,
                        f"202401{day:02d}",
                        10 + idx,
                        8 + idx,
                        0 if day == 1 else 10000,
                    ),
                )


def test_sqlite_db_source_fetch_daily_batches_symbols(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "astocks_raw.db"
    _make_sqlite_daily_db(db_path, symbols=5, days=3)
    monkeypatch.setattr("aqsp.data.sqlite_db_source._SQLITE_BATCH_SIZE", 2)
    source = SqliteDbSource(db_path=db_path, cache=None)

    result = source.fetch_daily(
        ["600000", "600001", "600002", "600003", "600004"],
        date(2024, 1, 1),
        date(2024, 1, 3),
    )

    assert list(result) == ["600000", "600001", "600002", "600003", "600004"]
    assert result["600000"]["date"].tolist() == [
        "2024-01-01",
        "2024-01-02",
        "2024-01-03",
    ]
    assert result["600000"]["amount"].iloc[0] > 0
    assert result["600004"]["name"].iloc[0] == "600004.SH"


def test_sqlite_db_source_get_liquid_symbols_uses_latest_available_day(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("aqsp.data.sqlite_db_source._LIQUID_SYMBOL_MIN_HISTORY_ROWS", 3)
    monkeypatch.setattr(
        "aqsp.data.sqlite_db_source._LIQUID_SYMBOL_LOOKBACK_CALENDAR_DAYS", 2
    )
    db_path = tmp_path / "astocks_raw.db"
    _make_sqlite_daily_db(db_path, symbols=5, days=3)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE daily_qfq SET amount = 50000 WHERE ts_code = '600004.SH' AND trade_date = '20240103'"
        )
        conn.execute(
            "UPDATE daily_qfq SET amount = 40000 WHERE ts_code = '600003.SZ' AND trade_date = '20240103'"
        )
    source = SqliteDbSource(db_path=db_path, cache=None)

    assert source.get_liquid_symbols(limit=2, min_amount=1) == ["600004", "600003"]


def test_sqlite_db_source_get_liquid_symbols_falls_back_when_amount_filter_empty(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("aqsp.data.sqlite_db_source._LIQUID_SYMBOL_MIN_HISTORY_ROWS", 3)
    monkeypatch.setattr(
        "aqsp.data.sqlite_db_source._LIQUID_SYMBOL_LOOKBACK_CALENDAR_DAYS", 2
    )
    db_path = tmp_path / "astocks_raw.db"
    _make_sqlite_daily_db(db_path, symbols=3, days=3)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE daily_qfq SET amount = 50000 WHERE ts_code = '600002.SH' AND trade_date = '20240103'"
        )
        conn.execute(
            "UPDATE daily_qfq SET amount = 40000 WHERE ts_code = '600001.SZ' AND trade_date = '20240103'"
        )
    source = SqliteDbSource(db_path=db_path, cache=None)

    assert source.get_liquid_symbols(limit=2, min_amount=999_999_999) == [
        "600002",
        "600001",
    ]


def test_sqlite_db_source_get_liquid_symbols_excludes_short_history(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("aqsp.data.sqlite_db_source._LIQUID_SYMBOL_MIN_HISTORY_ROWS", 3)
    monkeypatch.setattr(
        "aqsp.data.sqlite_db_source._LIQUID_SYMBOL_LOOKBACK_CALENDAR_DAYS", 2
    )
    db_path = tmp_path / "astocks_raw.db"
    _make_sqlite_daily_db(db_path, symbols=3, days=3)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM daily_qfq WHERE ts_code = '600002.SH'")
        conn.execute(
            """
            INSERT INTO daily_qfq (
                ts_code, trade_date, open, high, low, close,
                open_qfq, high_qfq, low_qfq, close_qfq, volume, amount
            ) VALUES ('600002.SH', '20240103', 10, 11, 9, 10, 8, 9, 7, 8, 1000, 99999)
            """
        )
    source = SqliteDbSource(db_path=db_path, cache=None)

    assert source.get_liquid_symbols(limit=3, min_amount=1) == ["600000", "600001"]


def test_sqlite_db_source_get_liquid_symbols_excludes_late_start(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("aqsp.data.sqlite_db_source._LIQUID_SYMBOL_MIN_HISTORY_ROWS", 2)
    monkeypatch.setattr(
        "aqsp.data.sqlite_db_source._LIQUID_SYMBOL_LOOKBACK_CALENDAR_DAYS", 2
    )
    db_path = tmp_path / "astocks_raw.db"
    _make_sqlite_daily_db(db_path, symbols=3, days=3)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM daily_qfq WHERE ts_code = '600002.SH' AND trade_date = '20240101'"
        )
        conn.execute(
            "UPDATE daily_qfq SET amount = 99999 WHERE ts_code = '600002.SH' AND trade_date = '20240103'"
        )
    source = SqliteDbSource(db_path=db_path, cache=None)

    assert "600002" not in source.get_liquid_symbols(limit=3, min_amount=1)


def test_sqlite_daily_coverage_uses_batched_group_query(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "astocks_raw.db"
    _make_sqlite_daily_db(db_path, symbols=5, days=30)
    monkeypatch.setattr("aqsp.data.sqlite_db_source._SQLITE_BATCH_SIZE", 2)
    source = SqliteDbSource(db_path=db_path, cache=None)

    assert source.get_symbols_with_daily_coverage(
        ["600000", "600001", "600002", "600003", "600004"],
        date(2024, 1, 1),
        date(2024, 1, 30),
        min_rows=None,
    ) == ["600000", "600001", "600002", "600003", "600004"]


def test_sqlite_db_source_fetch_daily_filters_to_covered_symbols(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "astocks_raw.db"
    _make_sqlite_daily_db(db_path, symbols=2, days=30)
    source = SqliteDbSource(db_path=db_path, cache=None)

    result = source.fetch_daily(
        ["600000", "600001", "600999"],
        date(2024, 1, 1),
        date(2024, 1, 30),
    )

    assert list(result) == ["600000", "600001"]


def test_sqlite_db_source_fetch_daily_reuses_recent_coverage_snapshot(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "astocks_raw.db"
    _make_sqlite_daily_db(db_path, symbols=2, days=30)
    source = SqliteDbSource(db_path=db_path, cache=None)

    covered = source.get_symbols_with_daily_coverage(
        ["600000", "600001"],
        date(2024, 1, 1),
        date(2024, 1, 30),
        min_rows=None,
    )
    assert covered == ["600000", "600001"]

    def fail_coverage(*_args, **_kwargs):
        raise AssertionError("duplicate coverage check should be skipped")

    source.get_symbols_with_daily_coverage = fail_coverage  # type: ignore[method-assign]

    result = source.fetch_daily(
        ["600000", "600001"],
        date(2024, 1, 1),
        date(2024, 1, 30),
    )

    assert list(result) == ["600000", "600001"]


def test_sqlite_db_source_fetch_daily_uses_bulk_cache_paths(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "astocks_raw.db"
    cache_path = tmp_path / "cache.db"
    _make_sqlite_daily_db(db_path, symbols=3, days=5)
    cache = DataCache(db_path=cache_path)
    source = SqliteDbSource(db_path=db_path, cache=cache)

    get_calls: list[str] = []
    set_calls: list[str] = []

    def fail_get_ohlcv(symbol, *_args, **_kwargs):
        get_calls.append(symbol)
        raise AssertionError(
            "fetch_daily should not fall back to per-symbol cache reads"
        )

    def fail_set_ohlcv(symbol, *_args, **_kwargs):
        set_calls.append(symbol)
        raise AssertionError(
            "fetch_daily should not fall back to per-symbol cache writes"
        )

    monkeypatch.setattr(cache, "get_ohlcv", fail_get_ohlcv)
    monkeypatch.setattr(cache, "set_ohlcv", fail_set_ohlcv)

    first = source.fetch_daily(
        ["600000", "600001", "600002"],
        date(2024, 1, 1),
        date(2024, 1, 5),
    )
    assert list(first) == ["600000", "600001", "600002"]
    assert get_calls == []
    assert set_calls == []

    second = source.fetch_daily(
        ["600000", "600001", "600002"],
        date(2024, 1, 1),
        date(2024, 1, 5),
    )
    assert list(second) == ["600000", "600001", "600002"]
    assert get_calls == []
    assert set_calls == []


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


def test_validate_ohlcv_allows_unknown_limit_prices_as_nan() -> None:
    df = pd.DataFrame(
        {
            "date": ["2026-05-27"],
            "symbol": ["000300"],
            "name": ["沪深300"],
            "open": [3500.0],
            "high": [3510.0],
            "low": [3490.0],
            "close": [3505.0],
            "volume": [1000.0],
            "amount": [350500000.0],
            "suspended": [False],
            "limit_up": [float("nan")],
            "limit_down": [float("nan")],
        }
    )
    source = AkshareSource.__new__(AkshareSource)
    source.name = "test"

    assert source._validate_ohlcv(df, "000300") is df


def test_sqlite_daily_coverage_requires_requested_start_and_end(
    tmp_path: Path,
) -> None:
    db = tmp_path / "sqlite.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table stocks (ts_code text, name text)")
        conn.execute("insert into stocks values ('600519.SH', '贵州茅台')")
        conn.execute(
            """
            create table daily_qfq (
                ts_code text,
                trade_date text,
                open real,
                high real,
                low real,
                close real,
                volume real,
                amount real
            )
            """
        )
        for day in pd.date_range("2023-01-02", "2023-12-29", freq="B"):
            conn.execute(
                "insert into daily_qfq values (?, ?, 1, 1, 1, 1, 100, 100)",
                ("600519.SH", day.strftime("%Y%m%d")),
            )
        conn.execute(
            "insert into stocks values ('000001.SZ', '平安银行')",
        )
        for day in pd.date_range("2023-01-03", "2023-12-29", freq="B"):
            conn.execute(
                "insert into daily_qfq values (?, ?, 1, 1, 1, 1, 100, 100)",
                ("000001.SZ", day.strftime("%Y%m%d")),
            )

    source = SqliteDbSource(db_path=db)

    assert (
        source.get_symbols_with_daily_coverage(
            ["600519"], date(2018, 1, 1), date(2024, 12, 31)
        )
        == []
    )
    assert source.get_symbols_with_daily_coverage(
        ["600519"], date(2023, 1, 1), date(2023, 12, 31)
    ) == ["600519"]
    assert (
        source.get_symbols_with_daily_coverage(
            ["000001"], date(2023, 1, 1), date(2023, 12, 31)
        )
        == []
    )


def test_sqlite_daily_coverage_excludes_symbol_missing_target_day(
    tmp_path: Path,
) -> None:
    db = tmp_path / "sqlite.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table stocks (ts_code text, name text)")
        conn.execute("insert into stocks values ('600519.SH', '贵州茅台')")
        conn.execute("insert into stocks values ('000001.SZ', '平安银行')")
        conn.execute(
            """
            create table daily_qfq (
                ts_code text,
                trade_date text,
                open real,
                high real,
                low real,
                close real,
                volume real,
                amount real
            )
            """
        )
        for day in pd.date_range("2024-01-02", "2024-01-10", freq="B"):
            conn.execute(
                "insert into daily_qfq values (?, ?, 1, 1, 1, 1, 100, 100)",
                ("600519.SH", day.strftime("%Y%m%d")),
            )
        for day in pd.date_range("2024-01-02", "2024-01-09", freq="B"):
            conn.execute(
                "insert into daily_qfq values (?, ?, 1, 1, 1, 1, 100, 100)",
                ("000001.SZ", day.strftime("%Y%m%d")),
            )

    source = SqliteDbSource(db_path=db)

    assert source.get_available_symbols() == ["600519", "000001"]
    assert source.get_symbols_with_daily_coverage(
        ["600519", "000001"],
        date(2024, 1, 2),
        date(2024, 1, 10),
    ) == ["600519"]


def test_sqlite_db_source_marks_qfq_database_price_mode(tmp_path: Path) -> None:
    db = tmp_path / "astocks_qfq.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table stocks (ts_code text, name text)")
        conn.execute(
            """
            create table daily_qfq (
                ts_code text,
                trade_date text,
                open real,
                high real,
                low real,
                close real,
                volume real,
                amount real
            )
            """
        )

    source = SqliteDbSource(db_path=db)

    assert source.price_mode() == "qfq"


def test_sqlite_db_source_treats_filename_qfq_as_raw_when_raw_columns_present(
    tmp_path: Path,
) -> None:
    db = tmp_path / "astocks_qfq.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table stocks (ts_code text, name text)")
        conn.execute(
            """
            create table daily_qfq (
                ts_code text,
                trade_date text,
                open real,
                high real,
                low real,
                close real,
                open_qfq real,
                high_qfq real,
                low_qfq real,
                close_qfq real,
                volume real,
                amount real
            )
            """
        )
        conn.execute(
            """
            insert into daily_qfq values
            ('600519.SH', '20260102', 1400, 1410, 1390, 1405, null, null, null, null, 1000, 1405000)
            """
        )

    source = SqliteDbSource(db_path=db)

    assert source.price_mode() == "raw"


def test_sqlite_db_source_rejects_qfq_database_for_raw_fetch(
    tmp_path: Path, monkeypatch
) -> None:
    db = tmp_path / "astocks_qfq.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table stocks (ts_code text, name text)")
        conn.execute(
            """
            create table daily_qfq (
                ts_code text,
                trade_date text,
                open real,
                high real,
                low real,
                close real,
                volume real,
                amount real
            )
            """
        )
    monkeypatch.delenv("AQSP_ALLOW_QFQ_SQLITE_SOURCE", raising=False)
    source = SqliteDbSource(db_path=db)

    with pytest.raises(DataError, match="qfq 数据库"):
        source.fetch_daily(["600519"], date(2026, 1, 1), date(2026, 1, 2), adjust="")


def test_sqlite_db_source_marks_raw_database_price_mode(tmp_path: Path) -> None:
    db = tmp_path / "astocks_raw.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table stocks (ts_code text, name text)")
        conn.execute(
            """
            create table daily_qfq (
                ts_code text,
                trade_date text,
                open real,
                high real,
                low real,
                close real,
                volume real,
                amount real
            )
            """
        )

    source = SqliteDbSource(db_path=db)

    assert source.price_mode() == "raw"


def test_sqlite_db_source_default_path_prefers_raw_database(
    tmp_path: Path, monkeypatch
) -> None:
    raw_dir = tmp_path / "A股量化分析数据"
    raw_dir.mkdir()
    db = raw_dir / "astocks_raw.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table stocks (ts_code text, name text)")
        conn.execute(
            """
            create table daily_qfq (
                ts_code text,
                trade_date text,
                open real,
                high real,
                low real,
                close real,
                volume real,
                amount real
            )
            """
        )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AQSP_SQLITE_DB_PATH", raising=False)

    source = SqliteDbSource()

    assert source.db_path.name == "astocks_raw.db"
    assert source.price_mode() == "raw"
