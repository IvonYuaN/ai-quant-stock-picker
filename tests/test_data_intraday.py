from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from aqsp.core.errors import FreshnessError
from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai
from aqsp.data.intraday import (
    FrameProvenance,
    IntradayService,
    OverlayProvenance,
    _a_share_elapsed_trading_minutes,
)
from aqsp.data.realtime import RealtimeService
from aqsp.data.source import DataSource


class MockSource(DataSource):
    name: str = "eastmoney"

    def __init__(self, intraday_data=None, quote_data=None, index_intraday_data=None):
        self._intraday = intraday_data or {}
        self._quotes = quote_data or {}
        self._index_intraday = index_intraday_data or {}

    def fetch_daily(self, symbols, start, end, adjust=""):
        return {}

    def fetch_intraday(self, symbols, period="5"):
        return {k: v for k, v in self._intraday.items() if k in symbols}

    def fetch_index_intraday(self, index_codes, period="5"):
        return {k: v for k, v in self._index_intraday.items() if k in index_codes}

    def fetch_realtime_quote(self, symbols):
        return {k: v for k, v in self._quotes.items() if k in symbols}

    def fetch_index(self, index_codes, start, end):
        return {}


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    (
        (datetime(2026, 7, 16, 11, 30), 120),
        (datetime(2026, 7, 16, 12, 15), 120),
        (datetime(2026, 7, 16, 13, 5), 125),
        (datetime(2026, 7, 16, 15, 0), 240),
    ),
)
def test_a_share_elapsed_trading_minutes_excludes_lunch_break(
    timestamp: datetime, expected: int
) -> None:
    assert _a_share_elapsed_trading_minutes(timestamp) == expected


def _historical_frame(data: dict) -> pd.DataFrame:
    frame = pd.DataFrame(data)
    frame.attrs.update(
        {
            "source_name": "sqlite_db",
            "source": "sqlite_db",
            "workload": "walkforward",
            "fetched_at": "2026-06-26T08:00:00+08:00",
            "timestamp_source": "vendor",
            "freshness": "historical",
        }
    )
    return frame


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
    service = IntradayService(source, allow_historical_replay=True)

    bars = service.get_intraday_bars(["600000"], target_date=date(2026, 5, 27))
    assert "600000" in bars
    assert len(bars["600000"]) == 2
    assert bars["600000"].attrs["source"] == "eastmoney"
    assert bars["600000"].attrs["workload"] == "walkforward"
    assert bars["600000"].attrs["freshness"] == "historical"
    assert bars["600000"].attrs["timestamp_source"] == "bar_time"
    assert isinstance(bars["600000"].attrs["provenance"], FrameProvenance)


def test_intraday_service_rejects_empty_symbol_request() -> None:
    with pytest.raises(DataError, match="未请求分时标的"):
        IntradayService(MockSource()).get_intraday_bars([])


def test_intraday_service_routes_benchmark_to_index_endpoint() -> None:
    stock = pd.DataFrame(
        {
            "date": ["2026-05-27 09:30:00"],
            "open": [10.0],
            "high": [10.2],
            "low": [9.9],
            "close": [10.1],
            "volume": [1000],
            "symbol": ["600000"],
            "name": ["test"],
        }
    )
    benchmark = stock.assign(symbol="000300", name="沪深300")
    service = IntradayService(
        MockSource(
            intraday_data={"600000": stock},
            index_intraday_data={"000300": benchmark},
        ),
        allow_historical_replay=True,
    )

    bars = service.get_intraday_bars(
        ["600000", "000300"],
        index_symbols=("000300",),
        target_date=date(2026, 5, 27),
    )

    assert set(bars) == {"600000", "000300"}


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
    service = IntradayService(source, allow_historical_replay=True)

    daily = service.synthesize_daily_from_intraday(
        ["600000"], target_date=date(2026, 5, 27)
    )
    assert "600000" in daily
    df = daily["600000"]
    assert df["open"].iloc[0] == 10.0
    assert df["high"].iloc[0] == 10.3
    assert df["low"].iloc[0] == 9.9
    assert df["close"].iloc[0] == 10.2
    assert df["volume"].iloc[0] == 3000


def test_merge_intraday_bar_into_daily_replaces_same_trade_day() -> None:
    intraday_data = {
        "600000": pd.DataFrame(
            {
                "date": ["2026-06-26 09:30:00", "2026-06-26 09:35:00"],
                "open": [10.0, 10.1],
                "high": [10.2, 10.4],
                "low": [9.9, 10.0],
                "close": [10.1, 10.3],
                "volume": [1000, 2000],
                "amount": [10100.0, 20600.0],
                "symbol": ["600000", "600000"],
                "name": ["test", "test"],
            }
        )
    }
    daily_data = {
        "600000": _historical_frame(
            {
                "date": ["2026-06-25", "2026-06-26", "2026-06-27"],
                "symbol": ["600000", "600000", "600000"],
                "name": ["test", "test", "test"],
                "open": [9.8, 9.9, 10.3],
                "high": [10.0, 10.0, 10.5],
                "low": [9.7, 9.8, 10.1],
                "close": [9.9, 10.0, 10.4],
                "volume": [500, 600, 700],
                "amount": [4950.0, 6000.0, 7280.0],
                "suspended": [False, False, False],
                "limit_up": [10.89, 11.0, 11.44],
                "limit_down": [8.91, 9.0, 9.36],
                "adj_factor": [1.0, 1.0, 1.0],
            }
        )
    }

    source = MockSource(intraday_data=intraday_data)
    service = IntradayService(source, allow_historical_replay=True)

    merged = service.merge_intraday_bar_into_daily(
        daily_data,
        ["600000"],
        target_date=date(2026, 6, 26),
    )

    frame = merged["600000"]
    assert list(frame["date"]) == ["2026-06-25", "2026-06-26"]
    assert "2026-06-27" not in set(frame["date"])
    assert frame["close"].iloc[-1] == 10.3
    assert frame["volume"].iloc[-1] == 3000
    assert frame["amount"].iloc[-1] == pytest.approx(30700.0)
    assert frame.attrs["source_name"] == "eastmoney"
    assert frame.attrs["historical_source"] == "sqlite_db"
    assert frame.attrs["historical_freshness"] == "historical"
    assert isinstance(frame.attrs["provenance"], OverlayProvenance)


def test_merge_intraday_bar_into_daily_skips_bad_symbol_when_other_symbol_succeeds() -> (
    None
):
    class PartiallyBadSource(MockSource):
        def fetch_intraday(self, symbols, period="5"):
            if len(symbols) > 1:
                raise DataError("batch failed on bad symbol")
            symbol = symbols[0]
            if symbol == "000001":
                raise DataError("bad symbol")
            return super().fetch_intraday(symbols, period)

    intraday_data = {
        "600000": pd.DataFrame(
            {
                "date": ["2026-06-26 09:30:00", "2026-06-26 09:35:00"],
                "open": [10.0, 10.1],
                "high": [10.2, 10.4],
                "low": [9.9, 10.0],
                "close": [10.1, 10.3],
                "volume": [1000, 2000],
                "amount": [10100.0, 20600.0],
                "symbol": ["600000", "600000"],
                "name": ["test", "test"],
            }
        )
    }
    daily_data = {
        "600000": _historical_frame(
            {
                "date": ["2026-06-25"],
                "symbol": ["600000"],
                "name": ["test"],
                "open": [9.8],
                "high": [10.0],
                "low": [9.7],
                "close": [9.9],
                "volume": [500],
                "amount": [4950.0],
                "suspended": [False],
                "limit_up": [10.89],
                "limit_down": [8.91],
                "adj_factor": [1.0],
            }
        )
    }
    service = IntradayService(
        PartiallyBadSource(intraday_data=intraday_data),
        allow_historical_replay=True,
    )

    merged = service.merge_intraday_bar_into_daily(
        daily_data,
        ["600000", "000001"],
        target_date=date(2026, 6, 26),
    )

    assert list(merged) == ["600000"]
    assert merged["600000"]["close"].iloc[-1] == 10.3


def test_intraday_overlay_coverage_keeps_missing_symbols_explicit() -> None:
    intraday = {
        "600000": pd.DataFrame(
            {
                "date": ["2026-06-26 09:30:00"],
                "open": [10.0],
                "high": [10.1],
                "low": [9.9],
                "close": [10.0],
                "volume": [1000],
                "symbol": ["600000"],
            }
        )
    }
    result = IntradayService(
        MockSource(intraday_data=intraday),
        allow_historical_replay=True,
    ).merge_intraday_bar_into_daily_with_coverage(
        {"600000": _historical_frame({"date": ["2026-06-25"], "symbol": ["600000"]})},
        ["600000", "000300"],
        target_date=date(2026, 6, 26),
    )

    assert result.covered_symbols == ("600000",)
    assert result.missing_symbols == ("000300",)
    assert not result.complete


def test_merge_intraday_bar_into_daily_requires_target_day_bars() -> None:
    intraday_data = {
        "600000": pd.DataFrame(
            {
                "date": ["2026-06-25 14:55:00"],
                "open": [10.0],
                "high": [10.2],
                "low": [9.9],
                "close": [10.1],
                "volume": [1000],
                "symbol": ["600000"],
                "name": ["test"],
            }
        )
    }
    source = MockSource(intraday_data=intraday_data)
    service = IntradayService(source, allow_historical_replay=True)

    with pytest.raises(Exception, match="当日 bar"):
        service.merge_intraday_bar_into_daily(
            {},
            ["600000"],
            target_date=date(2026, 6, 26),
        )


def test_intraday_overlay_rejects_historical_frame_without_provenance() -> None:
    intraday_data = {
        "600000": pd.DataFrame(
            {
                "date": ["2026-06-26 09:30:00"],
                "open": [10.0],
                "high": [10.1],
                "low": [9.9],
                "close": [10.0],
                "volume": [1000],
            }
        )
    }
    daily_data = {
        "600000": pd.DataFrame(
            {
                "date": ["2026-06-25"],
                "symbol": ["600000"],
                "close": [9.9],
            }
        )
    }

    with pytest.raises(DataError, match="历史日线 600000 provenance 不完整"):
        IntradayService(
            MockSource(intraday_data=intraday_data),
            allow_historical_replay=True,
        ).merge_intraday_bar_into_daily(
            daily_data,
            ["600000"],
            target_date=date(2026, 6, 26),
        )


def test_intraday_service_rejects_composite_source_without_symbol_provenance() -> None:
    source = MockSource(
        intraday_data={
            "600000": pd.DataFrame(
                {
                    "date": ["2026-05-27 09:30:00"],
                    "close": [10.0],
                }
            )
        }
    )
    source.name = "multi"

    with pytest.raises(Exception, match="全部缺失或已过期"):
        IntradayService(source, allow_historical_replay=True).get_intraday_bars(
            ["600000"], target_date=date(2026, 5, 27)
        )


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
    service = IntradayService(source, allow_historical_replay=True)

    bars = service.get_current_bar(["600000"], target_date=date(2026, 5, 27))
    assert "600000" in bars
    assert bars["600000"]["close"] == 10.2


def test_intraday_service_rejects_explicit_historical_target_without_replay(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "aqsp.data.intraday.now_shanghai",
        lambda: datetime(2026, 7, 14, 10, 0, tzinfo=now_shanghai().tzinfo),
    )
    frame = pd.DataFrame(
        {
            "date": ["2026-07-13 14:55:00"],
            "close": [10.0],
        }
    )

    with pytest.raises(DataError, match="必须显式启用 replay 模式"):
        IntradayService(MockSource(intraday_data={"600000": frame})).get_intraday_bars(
            ["600000"], target_date=date(2026, 7, 13)
        )


def test_legacy_intraday_merge_does_not_return_daily_history_when_market_is_closed(
    monkeypatch,
) -> None:
    monkeypatch.setattr("aqsp.data.intraday.is_market_open", lambda: False)
    daily = {
        "600000": _historical_frame(
            {
                "date": ["2026-07-13"],
                "symbol": ["600000"],
                "close": [10.0],
            }
        )
    }

    with pytest.raises(DataError, match="分时数据全部缺失"):
        IntradayService(MockSource()).merge_intraday_with_daily(
            daily,
            ["600000"],
        )


def test_intraday_service_rejects_stale_current_day_bars(monkeypatch) -> None:
    monkeypatch.setattr(
        "aqsp.data.intraday.now_shanghai",
        lambda: datetime(2026, 7, 13, 10, 0, tzinfo=now_shanghai().tzinfo),
    )
    source = MockSource(
        intraday_data={
            "600000": pd.DataFrame(
                {
                    "date": ["2026-07-13 09:30:00"],
                    "open": [10.0],
                    "high": [10.1],
                    "low": [9.9],
                    "close": [10.0],
                    "volume": [1000],
                }
            )
        }
    )

    with pytest.raises(Exception, match="全部缺失或已过期"):
        IntradayService(source).get_intraday_bars(["600000"], period="5")


def test_intraday_service_rejects_previous_day_bars_during_live_market(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "aqsp.data.intraday.now_shanghai",
        lambda: datetime(2026, 7, 14, 10, 0, tzinfo=now_shanghai().tzinfo),
    )
    source = MockSource(
        intraday_data={
            "600000": pd.DataFrame(
                {
                    "date": ["2026-07-13 14:55:00"],
                    "open": [10.0],
                    "high": [10.1],
                    "low": [9.9],
                    "close": [10.0],
                    "volume": [1000],
                }
            )
        }
    )

    with pytest.raises(Exception, match="全部缺失或已过期"):
        IntradayService(source).get_intraday_bars(["600000"], period="5")


def test_get_current_bar_uses_the_same_freshness_gate(monkeypatch) -> None:
    monkeypatch.setattr(
        "aqsp.data.intraday.now_shanghai",
        lambda: datetime(2026, 7, 13, 10, 0, tzinfo=now_shanghai().tzinfo),
    )
    source = MockSource(
        intraday_data={
            "600000": pd.DataFrame(
                {
                    "date": ["2026-07-13 09:30:00"],
                    "close": [10.0],
                }
            )
        }
    )

    with pytest.raises(Exception, match="全部缺失或已过期"):
        IntradayService(source).get_current_bar(["600000"], period="5")


def test_realtime_service_get_price():
    quote_data = {
        "600000": _quote(price=10.5, bid1=10.4, ask1=10.6),
    }
    source = MockSource(quote_data=quote_data)
    service = RealtimeService(source)

    prices = service.get_price(["600000"])
    assert prices["600000"] == 10.5
    assert prices
    quote = service.get_quotes(["600000"])["600000"]
    assert quote["source_name"] == "eastmoney"
    assert quote["workload"] == "live_short"
    assert isinstance(quote["provenance"], FrameProvenance)


def test_realtime_service_get_bid_ask():
    quote_data = {
        "600000": _quote(price=10.5, bid1=10.4, ask1=10.6),
    }
    source = MockSource(quote_data=quote_data)
    service = RealtimeService(source)

    bid_ask = service.get_bid_ask(["600000"])
    assert bid_ask["600000"] == (10.4, 10.6)


def test_realtime_service_get_volume_amount_returns_values():
    quote_data = {
        "600000": _quote(price=10.5, bid1=10.4, ask1=10.6),
    }
    source = MockSource(quote_data=quote_data)
    service = RealtimeService(source)

    volume_amount = service.get_volume_amount(["600000"])
    assert volume_amount["600000"] == (1000, 10500)


def test_realtime_service_caching():
    quote_data = {
        "600000": _quote(price=10.5, bid1=10.4, ask1=10.6),
    }
    source = MockSource(quote_data=quote_data)
    service = RealtimeService(source)

    prices1 = service.get_price(["600000"])
    prices2 = service.get_price(["600000"])
    assert prices1["600000"] == prices2["600000"]


def test_realtime_service_intraday_return():
    quote_data = {"600000": _quote(price=10.5)}
    source = MockSource(quote_data=quote_data)
    service = RealtimeService(source)

    ret = service.calculate_intraday_return("600000", 10.0)
    assert ret == 5.0


def test_realtime_service_rejects_quote_missing_contract_fields():
    quote_data = {"600000": {"price": 10.5, "ts": now_shanghai().isoformat()}}
    source = MockSource(quote_data=quote_data)
    service = RealtimeService(source)

    with pytest.raises(FreshnessError, match="schema missing"):
        service.get_price(["600000"])


def test_realtime_service_rejects_stale_quote():
    quote_data = {"600000": _quote(ts="2026-01-01T09:30:00+08:00")}
    source = MockSource(quote_data=quote_data)
    service = RealtimeService(source)

    with pytest.raises(FreshnessError, match="stale"):
        service.get_price(["600000"])


def test_realtime_service_rejects_quote_without_vendor_timestamp():
    quote = _quote()
    quote["vendor_ts"] = ""
    quote["timestamp_source"] = "received_at"
    service = RealtimeService(MockSource(quote_data={"600000": quote}))

    with pytest.raises(FreshnessError, match="vendor timestamp missing"):
        service.get_price(["600000"])


def test_realtime_service_keeps_valid_symbols_when_one_quote_fails_closed():
    invalid = _quote()
    invalid["vendor_ts"] = ""
    invalid["timestamp_source"] = "received_at"
    service = RealtimeService(
        MockSource(
            quote_data={
                "600000": _quote(price=10.5),
                "000001": invalid,
            }
        )
    )

    quotes = service.get_quotes(["600000", "000001"])

    assert set(quotes) == {"600000"}
    assert quotes["600000"]["price"] == 10.5


def test_realtime_service_rejects_any_future_vendor_timestamp():
    quote = _quote(ts="2099-01-01T09:30:00+08:00")
    service = RealtimeService(MockSource(quote_data={"600000": quote}))

    with pytest.raises(FreshnessError, match="timestamp is in the future"):
        service.get_price(["600000"])


def test_realtime_service_rejects_historical_only_source():
    source = MockSource(quote_data={"600000": _quote()})
    source.name = "sqlite_db"

    with pytest.raises(DataError, match="sqlite_db 不适合 live_short"):
        RealtimeService(source)


def test_realtime_services_reject_observation_source_before_fetching():
    source = MockSource()
    source.name = "akshare"

    with pytest.raises(DataError, match="akshare.*observation"):
        RealtimeService(source)
    with pytest.raises(DataError, match="akshare.*observation"):
        IntradayService(source)


def test_intraday_service_rejects_historical_only_source():
    source = MockSource()
    source.name = "sqlite_db"

    with pytest.raises(DataError, match="sqlite_db 不适合 live_short"):
        IntradayService(source)


def _quote(
    *,
    price: float = 10.5,
    bid1: float = 10.49,
    ask1: float = 10.51,
    volume: float = 1000,
    amount: float = 10500,
    ts: str | None = None,
) -> dict:
    return {
        "price": price,
        "bid1": bid1,
        "ask1": ask1,
        "volume": volume,
        "amount": amount,
        "ts": ts or now_shanghai().isoformat(),
        "vendor_ts": ts or now_shanghai().isoformat(),
        "timestamp_source": "vendor",
    }
