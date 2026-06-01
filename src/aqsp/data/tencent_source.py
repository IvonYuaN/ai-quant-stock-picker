from __future__ import annotations

import time
from datetime import date
from typing import Literal
import pandas as pd
import requests

from aqsp.data.source import DataSource, OhlcvFrame, apply_limit_suspended_adj
from aqsp.data.cache import DataCache
from aqsp.core.time import now_shanghai

TENCENT_SIMPLE_QUOTE_URL = "http://qt.gtimg.cn/q=s_{symbol}"
TENCENT_FULL_QUOTE_URL = "http://qt.gtimg.cn/q={market}{symbol}"
TENCENT_KLINE_URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

TENCENT_QUOTE_FIELD_LIMIT_UP = 47
TENCENT_QUOTE_FIELD_LIMIT_DOWN = 48

_REQUEST_DELAY = 0.3
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0


def _get_market_prefix(symbol: str) -> str:
    if symbol.startswith("6"):
        return "sh"
    return "sz"


class TencentSource(DataSource):
    name: str = "tencent"

    def __init__(self, cache: DataCache | None = None) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
        )
        self.cache = cache or DataCache()
        self._last_request_ts: float = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < _REQUEST_DELAY:
            time.sleep(_REQUEST_DELAY - elapsed)
        self._last_request_ts = time.monotonic()

    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for symbol in symbols:
            cached = self.cache.get_ohlcv(symbol, start, end)
            if cached is not None and not cached.empty:
                out[symbol] = cached
                continue

            df = self._fetch_tencent_daily(symbol, start, end)
            if df is not None and not df.empty:
                df = self._normalize_tencent_df(df, symbol)
                validated = self._validate_ohlcv(df, symbol)
                self.cache.set_ohlcv(symbol, validated, source="tencent")
                out[symbol] = validated
        return out

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for symbol in symbols:
            df = self._fetch_tencent_intraday(symbol, period)
            if df is not None and not df.empty:
                out[symbol] = df
        return out

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        quotes = {}
        for symbol in symbols:
            data = self._fetch_tencent_quote(symbol)
            if data:
                quotes[symbol] = data
        return quotes

    def fetch_index(
        self,
        index_codes: list[str],
        start: date,
        end: date,
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for code in index_codes:
            cached = self.cache.get_index(code, start, end)
            if cached is not None and not cached.empty:
                out[code] = cached
                continue

            df = self._fetch_tencent_daily(code, start, end, is_index=True)
            if df is not None and not df.empty:
                df = self._normalize_tencent_df(df, code)
                validated = self._validate_ohlcv(df, code)
                self.cache.set_index(code, validated, source="tencent")
                out[code] = validated
        return out

    def _fetch_tencent_daily(
        self,
        symbol: str,
        start: date,
        end: date,
        is_index: bool = False,
    ) -> pd.DataFrame | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                params = {
                    "param": f"{symbol},day,{start.strftime('%Y-%m-%d')},{end.strftime('%Y-%m-%d')},640",
                }
                response = self._session.get(
                    TENCENT_KLINE_URL, params=params, timeout=10
                )
                data = response.json()
                if not data.get("data"):
                    return None
                stock_data = data["data"].get(symbol, {})
                if not stock_data:
                    return None
                klines = stock_data.get("day", [])
                if not klines:
                    return None
                rows = []
                for kline in klines:
                    if len(kline) >= 6:
                        rows.append(
                            {
                                "date": kline[0],
                                "open": float(kline[1]),
                                "close": float(kline[2]),
                                "high": float(kline[3]),
                                "low": float(kline[4]),
                                "volume": float(kline[5]),
                            }
                        )
                return pd.DataFrame(rows)
            except Exception:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
        return None

    def _fetch_tencent_intraday(self, symbol: str, period: str) -> pd.DataFrame | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = _get_market_prefix(symbol)
                url = f"http://web.ifzq.gtimg.cn/appstock/app/minute/query?code={market}{symbol}"
                response = self._session.get(url, timeout=10)
                data = response.json()
                if not data.get("data"):
                    return None
                stock_data = data["data"].get(symbol, {})
                if not stock_data:
                    return None
                minutes = stock_data.get("data", [])
                if not minutes:
                    return None
                rows = []
                for minute in minutes:
                    if len(minute) >= 6:
                        rows.append(
                            {
                                "date": minute[0],
                                "open": float(minute[1]),
                                "close": float(minute[2]),
                                "high": float(minute[3]),
                                "low": float(minute[4]),
                                "volume": float(minute[5]),
                            }
                        )
                df = pd.DataFrame(rows)
                df["symbol"] = symbol
                df["name"] = symbol
                return df
            except Exception:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
        return None

    def _fetch_tencent_quote(self, symbol: str) -> dict | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = _get_market_prefix(symbol)
                url = TENCENT_FULL_QUOTE_URL.format(market=market, symbol=symbol)
                response = self._session.get(url, timeout=10)
                content = response.text
                parts = content.split("~")
                if len(parts) < 50:
                    return None
                price = float(parts[3]) if parts[3] else 0.0
                bid1 = float(parts[9]) if parts[9] else 0.0
                ask1 = float(parts[19]) if parts[19] else 0.0
                volume = float(parts[6]) if parts[6] else 0.0
                amount = float(parts[37]) if parts[37] else 0.0
                limit_up = (
                    float(parts[TENCENT_QUOTE_FIELD_LIMIT_UP])
                    if parts[TENCENT_QUOTE_FIELD_LIMIT_UP]
                    else None
                )
                limit_down = (
                    float(parts[TENCENT_QUOTE_FIELD_LIMIT_DOWN])
                    if parts[TENCENT_QUOTE_FIELD_LIMIT_DOWN]
                    else None
                )
                return {
                    "price": price,
                    "bid1": bid1,
                    "ask1": ask1,
                    "volume": volume,
                    "amount": amount,
                    "limit_up": limit_up,
                    "limit_down": limit_down,
                    "ts": now_shanghai().isoformat(),
                }
            except Exception:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
        return None

    def _normalize_tencent_df(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        df["symbol"] = symbol
        df["name"] = symbol
        df["amount"] = df["volume"] * df["close"]
        df = apply_limit_suspended_adj(df, symbol, cache=self.cache)
        return df
