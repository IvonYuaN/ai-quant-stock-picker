from __future__ import annotations

import logging
import time
from datetime import date
from typing import Literal
import pandas as pd
import requests

from aqsp.data.source import (
    DataSource,
    OhlcvFrame,
    apply_limit_suspended_adj,
    require_fetched_frame,
    require_fetched_mapping,
    require_non_empty_fetch_result,
)
from aqsp.data.cache import DataCache
from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai

_REQUEST_DELAY = 0.3
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0

_logger = logging.getLogger("aqsp.data.sina")


class SinaSource(DataSource):
    name: str = "sina"

    def __init__(self, cache: DataCache | None = None) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Referer": "http://finance.sina.com.cn",
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
            cached = self.cache.get_ohlcv(
                symbol, start, end, price_mode=adjust or "raw"
            )
            if cached is not None and not cached.empty:
                out[symbol] = cached
                continue

            df = require_fetched_frame(
                self.name,
                "日线",
                symbol,
                self._fetch_sina_daily(symbol, start, end),
            )
            df = self._normalize_sina_df(df, symbol)
            validated = self._validate_ohlcv(df, symbol)
            self.cache.set_ohlcv(
                symbol, validated, source="sina", price_mode=adjust or "raw"
            )
            out[symbol] = validated
        require_non_empty_fetch_result(self.name, "日线", symbols, out)
        return out

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for symbol in symbols:
            out[symbol] = require_fetched_frame(
                self.name,
                "分时",
                symbol,
                self._fetch_sina_intraday(symbol, period),
            )
        require_non_empty_fetch_result(self.name, "分时", symbols, out)
        return out

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        quotes = {}
        for symbol in symbols:
            quotes[symbol] = require_fetched_mapping(
                self.name,
                "实时行情",
                symbol,
                self._fetch_sina_quote(symbol),
            )
        require_non_empty_fetch_result(self.name, "实时行情", symbols, quotes)
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

            df = require_fetched_frame(
                self.name,
                "指数",
                code,
                self._fetch_sina_daily(code, start, end, is_index=True),
            )
            df = self._normalize_sina_df(df, code)
            validated = self._validate_ohlcv(df, code)
            self.cache.set_index(code, validated, source="sina")
            out[code] = validated
        require_non_empty_fetch_result(self.name, "指数", index_codes, out)
        return out

    def _fetch_sina_daily(
        self, symbol: str, start: date, end: date, is_index: bool = False
    ) -> pd.DataFrame | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = "sh" if symbol.startswith("6") else "sz"
                url = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
                params = {
                    "symbol": f"{market}{symbol}",
                    "scale": "240",
                    "ma": "no",
                    "datalen": "1023",
                }
                response = self._session.get(url, params=params, timeout=10)
                data = response.json()
                if not data:
                    return None
                df = pd.DataFrame(data)
                df["date"] = df["day"]
                df["open"] = pd.to_numeric(df["open"], errors="coerce")
                df["high"] = pd.to_numeric(df["high"], errors="coerce")
                df["low"] = pd.to_numeric(df["low"], errors="coerce")
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
                df = df[
                    (df["date"] >= start.strftime("%Y-%m-%d"))
                    & (df["date"] <= end.strftime("%Y-%m-%d"))
                ]
                return df
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "sina 日线获取失败 %s（重试%d次后放弃）: %s",
                        symbol,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"sina 日线获取失败: {symbol}") from exc
        return None

    def _fetch_sina_intraday(self, symbol: str, period: str) -> pd.DataFrame | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = "sh" if symbol.startswith("6") else "sz"
                url = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
                scale_map = {
                    "1": "60",
                    "5": "300",
                    "15": "900",
                    "30": "1800",
                    "60": "240",
                }
                params = {
                    "symbol": f"{market}{symbol}",
                    "scale": scale_map.get(period, "300"),
                    "ma": "no",
                    "datalen": "100",
                }
                response = self._session.get(url, params=params, timeout=10)
                data = response.json()
                if not data:
                    return None
                df = pd.DataFrame(data)
                df["date"] = df["day"] + " " + df["time"]
                df["open"] = pd.to_numeric(df["open"], errors="coerce")
                df["high"] = pd.to_numeric(df["high"], errors="coerce")
                df["low"] = pd.to_numeric(df["low"], errors="coerce")
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
                df["symbol"] = symbol
                df["name"] = symbol
                return df
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "sina 分时获取失败 %s（重试%d次后放弃）: %s",
                        symbol,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"sina 分时获取失败: {symbol}") from exc
        return None

    def _fetch_sina_quote(self, symbol: str) -> dict | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = "sh" if symbol.startswith("6") else "sz"
                url = f"http://hq.sinajs.cn/list={market}{symbol}"
                response = self._session.get(url, timeout=10)
                content = response.text
                parts = content.split(",")
                if len(parts) < 11:
                    return None
                return {
                    "price": float(parts[3]),
                    "bid1": float(parts[10]),
                    "ask1": float(parts[20]),
                    "volume": float(parts[8]),
                    "amount": float(parts[9]),
                    "ts": now_shanghai().isoformat(),
                }
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "sina 实时报价获取失败 %s（重试%d次后放弃）: %s",
                        symbol,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"sina 实时报价获取失败: {symbol}") from exc
        return None

    def _normalize_sina_df(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        df["symbol"] = symbol
        df["name"] = symbol
        df["amount"] = df["volume"] * df["close"]
        df = apply_limit_suspended_adj(df, symbol, cache=self.cache)
        return df
