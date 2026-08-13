from __future__ import annotations

import pytest

from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai
from aqsp.data.realtime import RealtimeService
from aqsp.data.source import DataSource


def _quote(
    *,
    price: float = 10.5,
    bid1: float = 10.49,
    ask1: float = 10.51,
    volume: float = 1000,
    amount: float = 10500,
    ts: str | None = None,
) -> dict:
    """合成一份可通过 freshness 校验的实时行情(结构同 test_data_intraday)。"""
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


class _FailingQuoteSource(DataSource):
    """合成实时行情源:对 ``fail_on`` 中的标的抛 DataError,其余返回 ``quote_data``。"""

    name: str = "eastmoney"

    def __init__(
        self,
        quote_data: dict[str, dict] | None = None,
        fail_on: set[str] | None = None,
    ) -> None:
        self._quotes = dict(quote_data or {})
        self._fail_on = set(fail_on or ())

    def fetch_realtime_quote(self, symbols: list[str]) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for symbol in symbols:
            if symbol in self._fail_on:
                raise DataError(f"模拟获取 {symbol} 实时行情失败")
            if symbol in self._quotes:
                result[symbol] = self._quotes[symbol]
        return result

    def fetch_daily(self, symbols, start, end, adjust=""):
        return {}

    def fetch_intraday(self, symbols, period="5"):
        return {}

    def fetch_index(self, index_codes, start, end):
        return {}


def test_realtime_get_quotes_exposes_failures_when_partial_fetch_fails() -> None:
    source = _FailingQuoteSource(
        quote_data={"600000": _quote(price=10.5)},
        fail_on={"000001"},
    )
    service = RealtimeService(source)

    quotes, failures = service.get_quotes(["600000", "000001"], expose_failures=True)

    assert set(quotes) == {"600000"}
    assert quotes["600000"]["price"] == 10.5
    assert set(failures) == {"000001"}
    assert "000001" in failures["000001"]


def test_realtime_get_quotes_hides_failures_by_default_when_partial_fetch_fails() -> (
    None
):
    source = _FailingQuoteSource(
        quote_data={"600000": _quote(price=10.5)},
        fail_on={"000001"},
    )
    service = RealtimeService(source)

    quotes = service.get_quotes(["600000", "000001"])

    # 默认行为完全不变:返回 dict 不含失败 symbol
    assert set(quotes) == {"600000"}
    assert "000001" not in quotes
    # 但 last_fetch_failures 属性记录了失败,调用方事后可查
    failures = service.last_fetch_failures
    assert set(failures) == {"000001"}
    assert "000001" in failures["000001"]


def test_realtime_get_quotes_returns_empty_failures_when_all_succeed() -> None:
    source = _FailingQuoteSource(
        quote_data={
            "600000": _quote(price=10.5),
            "000001": _quote(price=20.0),
        }
    )
    service = RealtimeService(source)

    quotes, failures = service.get_quotes(["600000", "000001"], expose_failures=True)

    assert set(quotes) == {"600000", "000001"}
    assert failures == {}
    assert service.last_fetch_failures == {}


def test_realtime_get_quotes_raises_when_all_symbols_fail() -> None:
    source = _FailingQuoteSource(fail_on={"600000", "000001"})
    service = RealtimeService(source)

    with pytest.raises(DataError, match="模拟获取"):
        service.get_quotes(["600000", "000001"])


def test_realtime_last_fetch_failures_returns_copy_not_internal_reference() -> None:
    source = _FailingQuoteSource(
        quote_data={"600000": _quote(price=10.5)},
        fail_on={"000001"},
    )
    service = RealtimeService(source)
    service.get_quotes(["600000", "000001"])

    snapshot = service.last_fetch_failures
    snapshot["000001"] = "tampered"
    snapshot["999999"] = "injected"

    # 只读属性返回副本,外部修改不污染内部状态
    assert service.last_fetch_failures == {"000001": "模拟获取 000001 实时行情失败"}


def test_realtime_last_fetch_failures_clears_after_successful_refresh() -> None:
    source = _FailingQuoteSource(
        quote_data={"600000": _quote(price=10.5)},
        fail_on={"000001"},
    )
    service = RealtimeService(source)
    service.get_quotes(["600000", "000001"])
    assert service.last_fetch_failures  # 先记录到失败

    # 再次调用只取成功标的(force_refresh 绕过缓存重新 fetch),无失败 -> 快照清空
    service.get_quotes(["600000"], force_refresh=True)
    assert service.last_fetch_failures == {}
