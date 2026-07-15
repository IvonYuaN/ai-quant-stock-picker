from __future__ import annotations

from datetime import datetime, timedelta

from aqsp.core.errors import DataError
from aqsp.data.source import DataSource
from aqsp.data.intraday import FrameProvenance, _normalize_fetched_at
from aqsp.data.source_readiness import source_role_for_workload, workload_guard_message
from aqsp.core.time import now_shanghai, is_market_open

_COMPOSITE_SOURCES = frozenset({"auto", "local_first", "online_first", "multi"})


class RealtimeService:
    def __init__(self, source: DataSource):
        source_name = str(getattr(source, "name", "") or "").strip()
        guard_message = workload_guard_message(source_name, "live_short")
        if guard_message:
            raise DataError(guard_message)
        self.source = source
        self._last_fetch: dict[str, datetime] = {}
        self._last_data: dict[str, dict] = {}

    def get_quotes(
        self, symbols: list[str], force_refresh: bool = False
    ) -> dict[str, dict]:
        from aqsp.freshness import validate_realtime_quotes
        from aqsp.data.quote_metadata import LIVE_SHORT_MAX_FUTURE_SECONDS

        now = now_shanghai()
        result = {}
        errors: list[Exception] = []

        for symbol in symbols:
            try:
                last_fetch = self._last_fetch.get(symbol)

                if not force_refresh and last_fetch is not None:
                    if (now - last_fetch) < timedelta(seconds=3):
                        if symbol in self._last_data:
                            result[symbol] = self._last_data[symbol]
                            continue

                quotes = self.source.fetch_realtime_quote([symbol])
                validate_realtime_quotes(
                    quotes,
                    require_vendor_timestamp=True,
                    max_future_seconds=LIVE_SHORT_MAX_FUTURE_SECONDS,
                )
                if symbol in quotes:
                    quotes[symbol] = _annotate_quote_provenance(
                        symbol,
                        quotes[symbol],
                        source=self.source,
                        fetched_at=now,
                    )
                    self._last_fetch[symbol] = now
                    self._last_data[symbol] = quotes[symbol]
                    result[symbol] = quotes[symbol]
            except Exception as exc:
                errors.append(exc)

        if not result and errors:
            raise errors[0]

        return result

    def get_price(self, symbols: list[str]) -> dict[str, float]:
        quotes = self.get_quotes(symbols)
        return {symbol: data.get("price", 0.0) for symbol, data in quotes.items()}

    def get_bid_ask(self, symbols: list[str]) -> dict[str, tuple[float, float]]:
        quotes = self.get_quotes(symbols)
        result = {}
        for symbol, data in quotes.items():
            bid = data.get("bid1", 0.0)
            ask = data.get("ask1", 0.0)
            result[symbol] = (bid, ask)
        return result

    def get_volume_amount(self, symbols: list[str]) -> dict[str, tuple[float, float]]:
        quotes = self.get_quotes(symbols)
        result = {}
        for symbol, data in quotes.items():
            volume = data.get("volume", 0.0)
            amount = data.get("amount", 0.0)
            result[symbol] = (volume, amount)
        return result

    def is_realtime_available(self) -> bool:
        return is_market_open()

    def get_latest_timestamp(self) -> datetime:
        return now_shanghai()

    def calculate_intraday_return(self, symbol: str, prev_close: float) -> float | None:
        quotes = self.get_quotes([symbol])
        if symbol not in quotes:
            return None
        price = quotes[symbol].get("price")
        if price is None or prev_close <= 0:
            return None
        return (price - prev_close) / prev_close * 100

    def get_market_status(self) -> str:
        if not is_market_open():
            return "closed"

        now = now_shanghai()
        hour = now.hour
        minute = now.minute

        if hour < 9:
            return "pre-market"
        elif hour == 9 and minute < 30:
            return "pre-market"
        elif hour == 9 and minute < 35:
            return "auction"
        elif hour < 11 or (hour == 11 and minute <= 30):
            return "morning"
        elif hour < 13:
            return "lunch"
        elif hour < 15:
            return "afternoon"
        else:
            return "closed"


def _annotate_quote_provenance(
    symbol: str,
    quote: dict,
    *,
    source: DataSource,
    fetched_at: datetime,
) -> dict:
    annotated = dict(quote)
    source_name = str(annotated.get("source_name") or annotated.get("source") or "").strip()
    source_provenance = getattr(source, "last_used_sources", {})
    if not source_name and isinstance(source_provenance, dict):
        source_name = str(source_provenance.get(symbol, "") or "").strip()
    if not source_name and str(getattr(source, "name", "")).strip() != "multi":
        source_name = str(getattr(source, "name", "") or "").strip()
    if not source_name or source_name in _COMPOSITE_SOURCES:
        raise DataError(f"实时行情标的 {symbol} 缺少可验证 provenance，拒绝继续")
    if source_role_for_workload(source_name, "live_short") != "realtime":
        raise DataError(f"实时行情标的 {symbol} 来源 {source_name} 角色不可接受")

    workload = str(annotated.get("workload") or "live_short").strip()
    if workload != "live_short":
        raise DataError(f"实时行情标的 {symbol} workload 不匹配: {workload or 'unknown'}")
    timestamp_source = str(annotated.get("timestamp_source") or "").strip()
    if not timestamp_source:
        raise DataError(f"实时行情标的 {symbol} 缺少 timestamp_source，拒绝继续")
    received_at = _normalize_fetched_at(
        annotated.get("fetched_at")
        or annotated.get("received_at")
        or fetched_at.isoformat(),
        field=f"实时行情 {symbol} fetched_at",
    )
    provenance = FrameProvenance(
        source=source_name,
        workload=workload,
        fetched_at=received_at,
        timestamp_source=timestamp_source,
        freshness="fresh",
    )
    annotated.update(
        {
            "source_name": source_name,
            "source": source_name,
            "workload": workload,
            "fetched_at": received_at,
            "freshness": "fresh",
            "provenance": provenance,
        }
    )
    return annotated
