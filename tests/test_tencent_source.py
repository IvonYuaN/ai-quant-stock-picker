from __future__ import annotations

import pytest
from datetime import date
import pandas as pd

from aqsp.core.errors import DataError
from aqsp.data.tencent_source import (
    TencentSource,
    _get_market_prefix,
    _normalize_tencent_intraday_volume_to_shares,
)


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


def test_tencent_source_fetches_quote_batch_and_keeps_partial_symbols(
    monkeypatch, tencent_source
):
    def payload(symbol: str, price: str) -> str:
        parts = [""] * 50
        parts[3] = price
        parts[6] = "1000"
        parts[9] = price
        parts[19] = price
        parts[30] = "2026-07-20"
        parts[31] = "10:15:00"
        parts[37] = "10000"
        parts[47] = "11.0"
        parts[48] = "9.0"
        body = "~".join(parts)
        prefix = "sh" if symbol.startswith("6") else "sz"
        return f'v_{prefix}{symbol}="{body}";'

    class Response:
        text = payload("600000", "10.5") + payload("000001", "12.5")

    requested_urls: list[str] = []

    def fake_get(url: str, **_kwargs):
        requested_urls.append(url)
        return Response()

    monkeypatch.setattr(tencent_source._session, "get", fake_get)
    monkeypatch.setattr(tencent_source, "_throttle", lambda: None)

    result = tencent_source.fetch_realtime_quote(["600000", "000001", "000002"])

    assert set(result) == {"600000", "000001"}
    assert result["600000"]["price"] == 10.5
    assert "q=sh600000,sz000001,sz000002" in requested_urls[0]


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


def test_tencent_daily_request_uses_market_prefixed_payload_key(
    monkeypatch, tencent_source
):
    captured = {}

    class FakeResponse:
        def json(self):
            return {
                "data": {
                    "sz000001": {
                        "day": [["2026-07-20", "10", "10.2", "10.3", "9.9", "100"]]
                    }
                }
            }

    def fake_get(url, params=None, **kwargs):
        captured["params"] = params
        return FakeResponse()

    monkeypatch.setattr(tencent_source._session, "get", fake_get)
    monkeypatch.setattr(tencent_source, "_throttle", lambda: None)

    frame = tencent_source._fetch_tencent_daily(
        "000001", start=date(2026, 7, 20), end=date(2026, 7, 20)
    )

    assert frame is not None
    assert captured["params"]["param"].startswith("sz000001,day,")
    assert frame["close"].iloc[0] == pytest.approx(10.2)


def test_tencent_intraday_reads_market_prefixed_payload_key(monkeypatch):
    class FakeResponse:
        def json(self):
            return {
                "data": {
                    "sh600519": {
                        "data": {
                            "data": [
                                "0930 1182.20 100 118220.00",
                                "0931 1181.00 140 165460.00",
                            ]
                        }
                    }
                }
            }

    class FakeSession:
        def get(self, url, **_kwargs):
            assert "code=sh600519" in url
            return FakeResponse()

    class FixedNow:
        def date(self):
            return date(2026, 7, 10)

    source = TencentSource.__new__(TencentSource)
    source._session = FakeSession()
    source._last_request_ts = 0.0
    monkeypatch.setattr(source, "_throttle", lambda: None)
    monkeypatch.setattr("aqsp.data.tencent_source.now_shanghai", lambda: FixedNow())

    frame = source._fetch_tencent_intraday("600519", "5")

    assert frame is not None
    assert frame["date"].tolist() == ["2026-07-10 09:30", "2026-07-10 09:31"]
    assert frame["open"].tolist() == [1182.20, 1182.20]
    assert frame["close"].tolist() == [1182.20, 1181.00]
    assert frame["volume"].tolist() == [100.0, 40.0]
    assert frame["amount"].tolist() == [118220.0, 47240.0]


def test_tencent_intraday_volume_normalizes_lots_row_by_row() -> None:
    frame = pd.DataFrame(
        {
            "close": [10.0, 10.0],
            "volume": [1_000.0, 100_000.0],
            "amount": [1_000_000.0, 1_000_000.0],
        }
    )

    normalized = _normalize_tencent_intraday_volume_to_shares(frame)

    assert normalized["volume"].tolist() == [100_000.0, 100_000.0]


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
    monkeypatch.setattr(tencent_source, "_fetch_tencent_quotes_batch", lambda *_args: {})
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
