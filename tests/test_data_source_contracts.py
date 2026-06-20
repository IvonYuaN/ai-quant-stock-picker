from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from aqsp.core.errors import DataError
from aqsp.data.akshare_source import AkshareSource
from aqsp.data.baostock_source import BaostockSource
from aqsp.data.eastmoney_source import EastmoneySource
from aqsp.data.efinance_source import EfinanceSource
from aqsp.data.sina_source import SinaSource
from aqsp.data.tencent_source import TencentSource
from aqsp.data.mootdx_source import MootdxSource
from aqsp.data.source import require_non_empty_fetch_result


def test_mootdx_public_fetch_methods_raise_data_error_when_empty(monkeypatch) -> None:
    source = MootdxSource.__new__(MootdxSource)
    source.name = "mootdx"
    monkeypatch.setattr(source, "_fetch_mootdx_daily", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(source, "_fetch_mootdx_intraday", lambda *_args: None)
    monkeypatch.setattr(source, "_fetch_mootdx_quote", lambda *_args: None)

    with pytest.raises(DataError, match="mootdx 日线获取失败"):
        source.fetch_daily(["600000"], date(2026, 5, 20), date(2026, 5, 27))
    with pytest.raises(DataError, match="mootdx 分时获取失败"):
        source.fetch_intraday(["600000"])
    with pytest.raises(DataError, match="mootdx 实时行情获取失败"):
        source.fetch_realtime_quote(["600000"])
    with pytest.raises(DataError, match="mootdx 指数获取失败"):
        source.fetch_index(["000300"], date(2026, 5, 20), date(2026, 5, 27))


def test_baostock_public_fetch_methods_raise_data_error_when_empty(monkeypatch) -> None:
    source = BaostockSource.__new__(BaostockSource)
    source.name = "baostock"
    source.cache = SimpleNamespace(
        get_ohlcv=lambda *_args, **_kwargs: None,
        get_index=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(source, "_ensure_login", lambda: None)
    monkeypatch.setattr(source, "_fetch_daily_single", lambda *_args: None)
    monkeypatch.setattr(source, "_fetch_intraday_single", lambda *_args: None)
    monkeypatch.setattr(source, "_fetch_quote_single", lambda *_args: None)
    monkeypatch.setattr(source, "_fetch_index_single", lambda *_args: None)

    with pytest.raises(DataError, match="baostock 日线获取失败"):
        source.fetch_daily(["600000"], date(2026, 5, 20), date(2026, 5, 27))
    with pytest.raises(DataError, match="baostock 分时获取失败"):
        source.fetch_intraday(["600000"])
    with pytest.raises(DataError, match="baostock 实时行情获取失败"):
        source.fetch_realtime_quote(["600000"])
    with pytest.raises(DataError, match="baostock 指数获取失败"):
        source.fetch_index(["000300"], date(2026, 5, 20), date(2026, 5, 27))


def test_require_non_empty_fetch_result_rejects_partial_results() -> None:
    with pytest.raises(DataError, match="test 日线获取不完整"):
        require_non_empty_fetch_result(
            "test",
            "日线",
            ["600000", "000001"],
            {"600000": object()},
        )


def test_online_public_daily_methods_raise_data_error_when_empty(monkeypatch) -> None:
    cases = [
        (AkshareSource, "akshare", "_ak", "stock_zh_a_hist"),
        (EfinanceSource, "efinance", "_ef", "stock.get_quote_history"),
        (EastmoneySource, "eastmoney", "_fetch_eastmoney_daily", None),
        (SinaSource, "sina", "_fetch_sina_daily", None),
        (TencentSource, "tencent", "_fetch_tencent_daily", None),
    ]
    for cls, name, attr, nested in cases:
        source = cls.__new__(cls)
        source.name = name
        source.cache = SimpleNamespace(
            get_ohlcv=lambda *_args, **_kwargs: None,
            set_ohlcv=lambda *_args, **_kwargs: None,
        )
        if nested is None:
            monkeypatch.setattr(source, attr, lambda *_args, **_kwargs: None)
        elif nested == "stock.get_quote_history":
            source._ef = SimpleNamespace(
                stock=SimpleNamespace(get_quote_history=lambda *_args, **_kwargs: None)
            )
        else:
            source._ak = SimpleNamespace(
                stock_zh_a_hist=lambda *_args, **_kwargs: SimpleNamespace(empty=True)
            )

        with pytest.raises(DataError, match=f"{name} 日线获取失败"):
            source.fetch_daily(["600000"], date(2026, 5, 20), date(2026, 5, 27))
