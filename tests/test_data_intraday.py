from __future__ import annotations

import pandas as pd

from aqsp.data.intraday import IntradayService
from aqsp.data.realtime import RealtimeService
from aqsp.data.source import DataSource


class MockSource(DataSource):
    name: str = "mock"

    def __init__(self, intraday_data=None, quote_data=None):
        self._intraday = intraday_data or {}
        self._quotes = quote_data or {}

    def fetch_daily(self, symbols, start, end, adjust=""):
        return {}

    def fetch_intraday(self, symbols, period="5"):
        return {k: v for k, v in self._intraday.items() if k in symbols}

    def fetch_realtime_quote(self, symbols):
        return {k: v for k, v in self._quotes.items() if k in symbols}

    def fetch_index(self, index_codes, start, end):
        return {}


def test_intraday_service():
    intraday_data = {
        "600000": pd.DataFrame(
            {
                "date": ["2026-05-27 09:30:00", "2026-05-27 09:35:00"],
                "open": [10.0, 10.1],
                "high": [10.2, 10.3],
                "low": [9.9, 10.0],
                "close": [10.1, 10.2],
                "volume": [1000, 2000],
                "symbol": ["600000", "600000"],
                "name": ["test", "test"],
            }
        )
    }
    source = MockSource(intraday_data=intraday_data)
    service = IntradayService(source)

    bars = service.get_intraday_bars(["600000"])
    assert "600000" in bars
    assert len(bars["600000"]) == 2


def test_synthesize_daily_from_intraday():
    intraday_data = {
        "600000": pd.DataFrame(
            {
                "date": ["2026-05-27 09:30:00", "2026-05-27 09:35:00"],
                "open": [10.0, 10.1],
                "high": [10.2, 10.3],
                "low": [9.9, 10.0],
                "close": [10.1, 10.2],
                "volume": [1000, 2000],
                "symbol": ["600000", "600000"],
                "name": ["test", "test"],
            }
        )
    }
    source = MockSource(intraday_data=intraday_data)
    service = IntradayService(source)

    daily = service.synthesize_daily_from_intraday(["600000"])
    assert "600000" in daily
    df = daily["600000"]
    assert df["open"].iloc[0] == 10.0
    assert df["high"].iloc[0] == 10.3
    assert df["low"].iloc[0] == 9.9
    assert df["close"].iloc[0] == 10.2
    assert df["volume"].iloc[0] == 3000


def test_get_current_bar():
    intraday_data = {
        "600000": pd.DataFrame(
            {
                "date": ["2026-05-27 09:30:00", "2026-05-27 09:35:00"],
                "close": [10.1, 10.2],
            }
        )
    }
    source = MockSource(intraday_data=intraday_data)
    service = IntradayService(source)

    bars = service.get_current_bar(["600000"])
    assert "600000" in bars
    assert bars["600000"]["close"] == 10.2


def test_realtime_service_get_price():
    quote_data = {
        "600000": {
            "price": 10.5,
            "bid1": 10.4,
            "ask1": 10.6,
            "volume": 1000,
            "amount": 10500,
        }
    }
    source = MockSource(quote_data=quote_data)
    service = RealtimeService(source)

    prices = service.get_price(["600000"])
    assert prices["600000"] == 10.5


def test_realtime_service_get_bid_ask():
    quote_data = {
        "600000": {
            "price": 10.5,
            "bid1": 10.4,
            "ask1": 10.6,
            "volume": 1000,
            "amount": 10500,
        }
    }
    source = MockSource(quote_data=quote_data)
    service = RealtimeService(source)

    bid_ask = service.get_bid_ask(["600000"])
    assert bid_ask["600000"] == (10.4, 10.6)


def test_realtime_service_caching():
    quote_data = {
        "600000": {
            "price": 10.5,
            "bid1": 10.4,
            "ask1": 10.6,
            "volume": 1000,
            "amount": 10500,
        }
    }
    source = MockSource(quote_data=quote_data)
    service = RealtimeService(source)

    prices1 = service.get_price(["600000"])
    prices2 = service.get_price(["600000"])
    assert prices1["600000"] == prices2["600000"]


def test_realtime_service_intraday_return():
    quote_data = {"600000": {"price": 10.5}}
    source = MockSource(quote_data=quote_data)
    service = RealtimeService(source)

    ret = service.calculate_intraday_return("600000", 10.0)
    assert ret == 5.0
