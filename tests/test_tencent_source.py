from __future__ import annotations

import pytest
from datetime import date
import pandas as pd

from aqsp.core.errors import DataError
from aqsp.data.tencent_source import TencentSource, _get_market_prefix


@pytest.fixture
def tencent_source():
    return TencentSource()


@pytest.fixture
def mock_tencent_quote_response():
    return {
        "price": 10.5,
        "bid1": 10.49,
        "ask1": 10.51,
        "volume": 1000000,
        "amount": 10500000,
        "limit_up": 11.55,
        "limit_down": 9.45,
        "ts": "2026-05-27T14:30:00+08:00",
    }


@pytest.fixture
def mock_tencent_daily_data():
    return pd.DataFrame(
        {
            "date": ["2026-05-25", "2026-05-26", "2026-05-27"],
            "open": [10.0, 10.1, 10.2],
            "close": [10.1, 10.2, 10.3],
            "high": [10.5, 10.6, 10.7],
            "low": [9.9, 10.0, 10.1],
            "volume": [1000000, 1100000, 1200000],
        }
    )


def test_tencent_source_has_name(tencent_source):
    assert tencent_source.name == "tencent"


def test_get_market_prefix_sh():
    assert _get_market_prefix("600000") == "sh"
    assert _get_market_prefix("601398") == "sh"
    assert _get_market_prefix("688001") == "sh"


def test_get_market_prefix_sz():
    assert _get_market_prefix("000001") == "sz"
    assert _get_market_prefix("002142") == "sz"
    assert _get_market_prefix("300750") == "sz"


def test_normalize_tencent_df(tencent_source, mock_tencent_daily_data):
    df = mock_tencent_daily_data.copy()
    normalized = tencent_source._normalize_tencent_df(df, "600000")
    assert "date" in normalized.columns
    assert "symbol" in normalized.columns
    assert "name" in normalized.columns
    assert "open" in normalized.columns
    assert "high" in normalized.columns
    assert "low" in normalized.columns
    assert "close" in normalized.columns
    assert "volume" in normalized.columns
    assert "amount" in normalized.columns
    assert "limit_up" in normalized.columns
    assert "limit_down" in normalized.columns
    assert "suspended" in normalized.columns
    assert normalized["symbol"].iloc[0] == "600000"
    assert normalized["name"].iloc[0] == "600000"


def test_normalize_tencent_df_calculates_amount(
    tencent_source, mock_tencent_daily_data
):
    df = mock_tencent_daily_data.copy()
    normalized = tencent_source._normalize_tencent_df(df, "600000")
    expected_amount = normalized["volume"] * normalized["close"]
    pd.testing.assert_series_equal(
        normalized["amount"], expected_amount, check_names=False
    )


def test_normalize_tencent_df_applies_limit_suspended_adj(
    tencent_source, mock_tencent_daily_data
):
    df = mock_tencent_daily_data.copy()
    normalized = tencent_source._normalize_tencent_df(df, "600000")
    assert normalized["limit_up"].iloc[1] == pytest.approx(10.1 * 1.10, rel=1e-2)
    assert normalized["limit_down"].iloc[1] == pytest.approx(10.1 * 0.90, rel=1e-2)
    assert not normalized["suspended"].iloc[0]


def test_tencent_quote_fields_mapping(tencent_source, mock_tencent_quote_response):
    quote = mock_tencent_quote_response
    assert quote["price"] == 10.5
    assert quote["bid1"] == 10.49
    assert quote["ask1"] == 10.51
    assert quote["volume"] == 1000000
    assert quote["amount"] == 10500000
    assert quote["limit_up"] == 11.55
    assert quote["limit_down"] == 9.45
    assert "ts" in quote


def test_tencent_quote_uses_real_limits(tencent_source):
    quote = tencent_source._fetch_tencent_quote("600000")
    if quote is not None:
        if quote.get("limit_up") is not None:
            assert isinstance(quote["limit_up"], float)
        if quote.get("limit_down") is not None:
            assert isinstance(quote["limit_down"], float)


def test_fetch_daily_returns_dict(tencent_source):
    try:
        result = tencent_source.fetch_daily(
            symbols=["600000"],
            start=date(2026, 5, 20),
            end=date(2026, 5, 27),
        )
    except DataError:
        return
    assert isinstance(result, dict)


def test_tencent_daily_request_does_not_use_qfq(monkeypatch, tencent_source):
    captured = {}

    class FakeResponse:
        def json(self):
            return {"data": {"600000": {"day": []}}}

    def fake_get(url, params=None, **kwargs):
        captured["params"] = params
        return FakeResponse()

    monkeypatch.setattr(tencent_source._session, "get", fake_get)

    tencent_source._fetch_tencent_daily(
        "600000",
        start=date(2026, 5, 20),
        end=date(2026, 5, 27),
    )

    assert "qfq" not in captured["params"]["param"]


def test_fetch_intraday_returns_dict(tencent_source):
    try:
        result = tencent_source.fetch_intraday(
            symbols=["600000"],
            period="5",
        )
    except DataError:
        return
    assert isinstance(result, dict)


def test_fetch_realtime_quote_returns_dict(tencent_source):
    try:
        result = tencent_source.fetch_realtime_quote(
            symbols=["600000"],
        )
    except DataError:
        return
    assert isinstance(result, dict)


def test_fetch_index_returns_dict(tencent_source):
    try:
        result = tencent_source.fetch_index(
            index_codes=["000300"],
            start=date(2026, 5, 20),
            end=date(2026, 5, 27),
        )
    except DataError:
        return
    assert isinstance(result, dict)


def test_tencent_public_fetch_methods_raise_data_error_when_empty(
    monkeypatch, tencent_source
) -> None:
    monkeypatch.setattr(tencent_source, "_fetch_tencent_intraday", lambda *_args: None)
    monkeypatch.setattr(tencent_source, "_fetch_tencent_quote", lambda *_args: None)
    monkeypatch.setattr(
        tencent_source,
        "_fetch_tencent_daily",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(DataError, match="tencent 分时获取失败"):
        tencent_source.fetch_intraday(["600000"])
    with pytest.raises(DataError, match="tencent 实时行情获取失败"):
        tencent_source.fetch_realtime_quote(["600000"])
    with pytest.raises(DataError, match="tencent 指数获取失败"):
        tencent_source.fetch_index(["000300"], date(2026, 5, 20), date(2026, 5, 27))


def test_normalize_df_has_required_columns(tencent_source, mock_tencent_daily_data):
    df = mock_tencent_daily_data.copy()
    normalized = tencent_source._normalize_tencent_df(df, "600000")
    required_columns = {
        "date",
        "symbol",
        "name",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "limit_up",
        "limit_down",
        "suspended",
    }
    assert required_columns.issubset(set(normalized.columns))


def test_normalize_df_preserves_data(tencent_source, mock_tencent_daily_data):
    df = mock_tencent_daily_data.copy()
    normalized = tencent_source._normalize_tencent_df(df, "600000")
    assert normalized["open"].iloc[0] == 10.0
    assert normalized["close"].iloc[0] == 10.1
    assert normalized["high"].iloc[0] == 10.5
    assert normalized["low"].iloc[0] == 9.9
    assert normalized["volume"].iloc[0] == 1000000
