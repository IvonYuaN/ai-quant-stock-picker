from __future__ import annotations

from datetime import datetime, timedelta

from aqsp.data.source import DataSource
from aqsp.core.time import now_shanghai, is_market_open


class RealtimeService:
    def __init__(self, source: DataSource):
        self.source = source
        self._last_fetch: dict[str, datetime] = {}
        self._last_data: dict[str, dict] = {}

    def get_quotes(
        self, symbols: list[str], force_refresh: bool = False
    ) -> dict[str, dict]:
        now = now_shanghai()
        result = {}

        for symbol in symbols:
            last_fetch = self._last_fetch.get(symbol)

            if not force_refresh and last_fetch is not None:
                if (now - last_fetch) < timedelta(seconds=3):
                    if symbol in self._last_data:
                        result[symbol] = self._last_data[symbol]
                        continue

            quotes = self.source.fetch_realtime_quote([symbol])
            if symbol in quotes:
                self._last_fetch[symbol] = now
                self._last_data[symbol] = quotes[symbol]
                result[symbol] = quotes[symbol]

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
