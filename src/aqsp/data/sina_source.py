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
from aqsp.data.quote_metadata import (
    parse_legacy_quote_timestamp,
    quote_timestamp_metadata,
)
from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai
from aqsp.data.parallel_fetch import fetch_in_parallel

_REQUEST_DELAY = 0.3
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0
_SPOT_PAGE_SIZE = 100
_SPOT_MAX_PAGES = 100
_SINA_SPOT_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)

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
        self._active_workload: str | None = None

    def set_workload(self, workload: str | None) -> None:
        """Set provenance context for cache-backed runtime fetches."""
        self._active_workload = workload

    def _cache_workload(self) -> str | None:
        return getattr(self, "_active_workload", None)

    def _annotate_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        frame.attrs["source_name"] = self.name
        frame.attrs["source"] = self.name
        workload = self._cache_workload()
        if workload:
            frame.attrs["workload"] = workload
            frame.attrs["fetched_at"] = str(
                frame.attrs.get("fetched_at") or now_shanghai().isoformat()
            )
            frame.attrs["timestamp_source"] = str(
                frame.attrs.get("timestamp_source") or "received_at"
            )
        return frame

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
        if self._cache_workload() == "live_short":
            return self._fetch_live_daily_parallel(symbols, start, end, adjust)
        out: dict[str, OhlcvFrame] = {}
        for symbol in symbols:
            cached = self.cache.get_ohlcv(
                symbol,
                start,
                end,
                price_mode=adjust or "raw",
                source=self.name,
                workload=self._cache_workload(),
            )
            if cached is not None and not cached.empty:
                out[symbol] = self._annotate_frame(cached)
                continue

            try:
                df = require_fetched_frame(
                    self.name,
                    "日线",
                    symbol,
                    self._fetch_sina_daily(symbol, start, end),
                )
            except DataError:
                if self._cache_workload() != "live_short":
                    raise
                _logger.warning("sina 盘中日线跳过无返回标的: %s", symbol)
                continue
            df = self._normalize_sina_df(df, symbol)
            validated = self._validate_ohlcv(df, symbol)
            self.cache.set_ohlcv(
                symbol,
                validated,
                source=self.name,
                price_mode=adjust or "raw",
                workload=self._cache_workload(),
            )
            out[symbol] = self._annotate_frame(validated)
        if self._cache_workload() == "live_short":
            if not out:
                raise DataError(f"{self.name} 日线获取失败: {symbols}")
        else:
            require_non_empty_fetch_result(self.name, "日线", symbols, out)
        return out

    def _fetch_live_daily_parallel(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"],
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        pending: list[str] = []
        for symbol in symbols:
            cached = self.cache.get_ohlcv(
                symbol,
                start,
                end,
                price_mode=adjust or "raw",
                source=self.name,
                workload=self._cache_workload(),
            )
            if cached is not None and not cached.empty:
                out[symbol] = self._annotate_frame(cached)
            else:
                pending.append(symbol)

        fetched, errors = fetch_in_parallel(
            pending,
            lambda symbol: require_fetched_frame(
                self.name,
                "日线",
                symbol,
                self._fetch_sina_daily(symbol, start, end),
            ),
        )
        for symbol, raw_frame in fetched.items():
            df = self._normalize_sina_df(raw_frame, symbol)
            validated = self._validate_ohlcv(df, symbol)
            self.cache.set_ohlcv(
                symbol,
                validated,
                source=self.name,
                price_mode=adjust or "raw",
                workload=self._cache_workload(),
            )
            out[symbol] = self._annotate_frame(validated)
        for symbol, error in errors.items():
            _logger.warning("sina 盘中日线跳过无返回标的 %s: %s", symbol, error)
        if not out:
            raise DataError(f"{self.name} 日线获取失败: {symbols}")
        return out

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        def fetch_one(symbol: str) -> OhlcvFrame:
            return require_fetched_frame(
                self.name,
                "分时",
                symbol,
                self._fetch_sina_intraday(symbol, period),
            )

        out, errors = fetch_in_parallel(list(symbols), fetch_one)
        if errors and self._cache_workload() != "live_short":
            raise next(iter(errors.values()))
        require_non_empty_fetch_result(self.name, "分时", symbols, out)
        return out

    def fetch_index_intraday(
        self,
        index_codes: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        out = {
            code: require_fetched_frame(
                self.name,
                "指数分时",
                code,
                self._fetch_sina_intraday(code, period, is_index=True),
            )
            for code in index_codes
        }
        require_non_empty_fetch_result(self.name, "指数分时", index_codes, out)
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

    def get_available_symbols(self) -> list[str]:
        """Return the current A-share pool from Sina's paginated live snapshot."""
        snapshot = self._fetch_sina_spot_snapshot()
        if snapshot.empty or "symbol" not in snapshot.columns:
            raise DataError("sina 全市场实时快照未返回可用标的")
        return snapshot["symbol"].astype(str).tolist()

    def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
        """Return live symbols ranked by the current turnover amount."""
        snapshot = self._fetch_sina_spot_snapshot(sort="amount")
        if snapshot.empty:
            raise DataError("sina 全市场实时快照为空，无法筛选高流动性标的")
        liquid = snapshot[snapshot["amount"] >= max(float(min_amount or 0.0), 0.0)]
        row_limit = max(int(limit or 0), 0)
        if row_limit > 0:
            liquid = liquid.head(row_limit)
        return liquid["symbol"].astype(str).tolist()

    def _fetch_sina_spot_snapshot(self, *, sort: str = "symbol") -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        for page in range(1, _SPOT_MAX_PAGES + 1):
            data = self._fetch_sina_spot_page(page, sort=sort)
            if not data:
                break
            for item in data:
                symbol = str(item.get("code") or "").strip()
                name = str(item.get("name") or "").strip()
                if (
                    len(symbol) != 6
                    or not symbol.isdigit()
                    or name.startswith(("ST", "*ST", "退市"))
                ):
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "name": name or symbol,
                        "price": _safe_float(item.get("trade")),
                        "volume": _safe_float(item.get("volume")),
                        "amount": _safe_float(item.get("amount")),
                    }
                )
            if len(data) < _SPOT_PAGE_SIZE:
                break
        else:
            raise DataError("sina 全市场实时快照分页超过最大页数")
        if not rows:
            raise DataError("sina 全市场实时快照无有效 A 股标的")
        return pd.DataFrame(rows)

    def _fetch_sina_spot_page(self, page: int, *, sort: str) -> list[dict[str, object]]:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                response = self._session.get(
                    _SINA_SPOT_URL,
                    params={
                        "page": page,
                        "num": _SPOT_PAGE_SIZE,
                        "sort": sort,
                        "asc": 1 if sort == "symbol" else 0,
                        "node": "hs_a",
                        "symbol": "",
                        "_s_r_a": "init",
                    },
                    timeout=10,
                )
                data = response.json()
                if not isinstance(data, list):
                    raise DataError("sina 全市场实时快照返回格式错误")
                return data
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    raise DataError(f"sina 全市场实时快照获取失败 page={page}") from exc
        return []

    def fetch_index(
        self,
        index_codes: list[str],
        start: date,
        end: date,
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for code in index_codes:
            cached = self.cache.get_index(
                code,
                start,
                end,
                source=self.name,
                workload=self._cache_workload(),
            )
            if cached is not None and not cached.empty:
                out[code] = self._annotate_frame(cached)
                continue

            df = require_fetched_frame(
                self.name,
                "指数",
                code,
                self._fetch_sina_daily(code, start, end, is_index=True),
            )
            df = self._normalize_sina_df(df, code)
            validated = self._validate_ohlcv(df, code)
            self.cache.set_index(
                code,
                validated,
                source=self.name,
                workload=self._cache_workload(),
            )
            out[code] = self._annotate_frame(validated)
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

    def _fetch_sina_intraday(
        self, symbol: str, period: str, *, is_index: bool = False
    ) -> pd.DataFrame | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = "sh" if is_index or symbol.startswith("6") else "sz"
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
                received_at = now_shanghai().isoformat()
                return {
                    "price": float(parts[3]),
                    "bid1": float(parts[10]),
                    "ask1": float(parts[20]),
                    "volume": float(parts[8]),
                    "amount": float(parts[9]),
                    **quote_timestamp_metadata(
                        parse_legacy_quote_timestamp(parts), received_at
                    ),
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


def _safe_float(value: object) -> float:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return 0.0 if pd.isna(parsed) else float(parsed)
