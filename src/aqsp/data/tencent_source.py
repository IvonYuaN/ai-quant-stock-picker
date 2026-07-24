from __future__ import annotations

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Literal
import pandas as pd
import requests

from aqsp.data.source import (
    DataSource,
    OhlcvFrame,
    apply_limit_suspended_adj,
    require_fetched_frame,
    require_non_empty_fetch_result,
)
from aqsp.data.cache import DataCache
from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai
from aqsp.data.quote_metadata import (
    parse_legacy_quote_timestamp,
    quote_timestamp_metadata,
)
from aqsp.data.parallel_fetch import fetch_in_parallel

_logger = logging.getLogger("aqsp.data.tencent")

TENCENT_SIMPLE_QUOTE_URL = "http://qt.gtimg.cn/q=s_{symbol}"
TENCENT_FULL_QUOTE_URL = "http://qt.gtimg.cn/q={market}{symbol}"
TENCENT_BATCH_QUOTE_URL = "http://qt.gtimg.cn/q={symbols}"
TENCENT_KLINE_URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

TENCENT_QUOTE_FIELD_LIMIT_UP = 47
TENCENT_QUOTE_FIELD_LIMIT_DOWN = 48

_REQUEST_DELAY = 0.3
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0
_DEFAULT_DAILY_FETCH_WORKERS = 8


def _get_market_prefix(symbol: str, *, is_index: bool = False) -> str:
    if is_index or symbol.startswith("6"):
        return "sh"
    return "sz"


def _configured_daily_fetch_workers(requested_count: int) -> int:
    raw = os.getenv("AQSP_TENCENT_DAILY_FETCH_WORKERS", "").strip()
    try:
        configured = int(raw) if raw else _DEFAULT_DAILY_FETCH_WORKERS
    except ValueError:
        configured = _DEFAULT_DAILY_FETCH_WORKERS
    return max(1, min(configured, max(1, requested_count)))


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
                continue
            pending.append(symbol)

        # Fetch network data concurrently, then normalize/cache sequentially.
        # This keeps SQLite/cache writes deterministic while avoiding a full
        # live batch deadline spent on the 0.3s per-symbol throttle.
        workers = _configured_daily_fetch_workers(len(pending))
        fetched: dict[str, pd.DataFrame] = {}
        if pending:
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="aqsp-tencent-daily"
            ) as executor:
                futures = {
                    executor.submit(
                        self._fetch_tencent_daily, symbol, start, end
                    ): symbol
                    for symbol in pending
                }
                for future in as_completed(futures):
                    symbol = futures[future]
                    try:
                        frame = future.result()
                    except Exception as exc:
                        _logger.warning("tencent 日线跳过标的 %s: %s", symbol, exc)
                        continue
                    if isinstance(frame, pd.DataFrame) and not frame.empty:
                        fetched[symbol] = frame

        for symbol, raw_frame in fetched.items():
            df = self._normalize_tencent_df(raw_frame, symbol)
            validated = self._validate_ohlcv(df, symbol)
            self.cache.set_ohlcv(
                symbol,
                validated,
                source=self.name,
                price_mode=adjust or "raw",
                workload=self._cache_workload(),
            )
            out[symbol] = self._annotate_frame(validated)
        if self._cache_workload() == "live_short" and out:
            return out
        if out and len(out) < len(tuple(dict.fromkeys(symbols))):
            missing = [symbol for symbol in symbols if symbol not in out]
            raise DataError(f"{self.name} 日线获取失败（不完整）: 缺少 {missing}")
        require_non_empty_fetch_result(self.name, "日线", symbols, out)
        return out

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        def fetch_one(symbol: str) -> OhlcvFrame:
            return _normalize_tencent_intraday_volume_to_shares(
                require_fetched_frame(
                    self.name,
                    "分时",
                    symbol,
                    self._fetch_tencent_intraday(symbol, period),
                )
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
                self._fetch_tencent_intraday(code, period, is_index=True),
            )
            for code in index_codes
        }
        require_non_empty_fetch_result(self.name, "指数分时", index_codes, out)
        return out

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        requested = tuple(
            dict.fromkeys(
                str(symbol).strip() for symbol in symbols if str(symbol).strip()
            )
        )
        if not requested:
            raise DataError("tencent 实时行情未请求标的")
        quotes = self._fetch_tencent_quotes_batch(requested)
        if not quotes:
            raise DataError(f"{self.name} 实时行情获取失败: {symbols}")
        return quotes

    def _fetch_tencent_quotes_batch(self, symbols: tuple[str, ...]) -> dict[str, dict]:
        """Fetch a quote batch in one request and retain partial successes.

        Tencent's quote endpoint accepts comma-separated market-prefixed
        symbols.  The previous one-request-per-symbol loop made a 64-symbol
        live batch spend most of its deadline on throttling and returned only
        the first successful quote under server-side rate limiting.
        """
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                query = ",".join(
                    f"{_get_market_prefix(symbol)}{symbol}" for symbol in symbols
                )
                response = self._session.get(
                    TENCENT_BATCH_QUOTE_URL.format(symbols=query), timeout=10
                )
                quotes = self._parse_tencent_quote_response(response.text)
                return {
                    symbol: quotes[symbol] for symbol in symbols if symbol in quotes
                }
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "tencent 批量实时报价获取失败（重试%d次后放弃）: %s",
                        _MAX_RETRIES,
                        exc,
                    )
        return {}

    @staticmethod
    def _parse_tencent_quote_response(content: str) -> dict[str, dict]:
        quotes: dict[str, dict] = {}
        for match in re.finditer(r'v_(?:sh|sz)(\d{6})="([^"]*)"', content or ""):
            symbol = match.group(1)
            parts = match.group(2).split("~")
            if len(parts) < 50:
                continue
            try:
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
            except (TypeError, ValueError):
                continue
            received_at = now_shanghai().isoformat()
            quotes[symbol] = {
                "price": price,
                "bid1": bid1,
                "ask1": ask1,
                "volume": volume,
                "amount": amount,
                "limit_up": limit_up,
                "limit_down": limit_down,
                **quote_timestamp_metadata(
                    parse_legacy_quote_timestamp(parts), received_at
                ),
            }
        return quotes

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
                self._fetch_tencent_daily(code, start, end, is_index=True),
            )
            df = self._normalize_tencent_df(df, code)
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
                market_symbol = (
                    symbol if is_index else f"{_get_market_prefix(symbol)}{symbol}"
                )
                params = {
                    "param": f"{market_symbol},day,{start.strftime('%Y-%m-%d')},{end.strftime('%Y-%m-%d')},640",
                }
                response = self._session.get(
                    TENCENT_KLINE_URL, params=params, timeout=10
                )
                data = response.json()
                if not data.get("data"):
                    return None
                stock_data = data["data"].get(market_symbol, {})
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
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "tencent 日线获取失败 %s（重试%d次后放弃）: %s",
                        symbol,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"tencent 日线获取失败: {symbol}") from exc
        return None

    def _fetch_tencent_intraday(
        self, symbol: str, period: str, *, is_index: bool = False
    ) -> pd.DataFrame | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = _get_market_prefix(symbol, is_index=is_index)
                market_symbol = f"{market}{symbol}"
                url = f"http://web.ifzq.gtimg.cn/appstock/app/minute/query?code={market_symbol}"
                response = self._session.get(url, timeout=10)
                data = response.json()
                if not data.get("data"):
                    return None
                stock_data = data["data"].get(market_symbol) or data["data"].get(
                    symbol, {}
                )
                if not stock_data:
                    return None
                minute_payload = stock_data.get("data", {})
                minutes = (
                    minute_payload.get("data", [])
                    if isinstance(minute_payload, dict)
                    else minute_payload
                )
                if not minutes:
                    return None
                trade_date = now_shanghai().date().isoformat()
                rows = []
                previous_price: float | None = None
                previous_volume = 0.0
                previous_amount = 0.0
                for minute in minutes:
                    parts = str(minute).split()
                    if len(parts) < 4:
                        continue
                    minute_time = parts[0]
                    price = float(parts[1])
                    cumulative_volume = float(parts[2])
                    cumulative_amount = float(parts[3])
                    bar_open = price if previous_price is None else previous_price
                    volume = max(cumulative_volume - previous_volume, 0.0)
                    amount = max(cumulative_amount - previous_amount, 0.0)
                    rows.append(
                        {
                            "date": f"{trade_date} {minute_time[:2]}:{minute_time[2:]}",
                            "open": bar_open,
                            "close": price,
                            "high": max(bar_open, price),
                            "low": min(bar_open, price),
                            "volume": volume,
                            "amount": amount,
                        }
                    )
                    previous_price = price
                    previous_volume = cumulative_volume
                    previous_amount = cumulative_amount
                if not rows:
                    return None
                df = pd.DataFrame(rows)
                df["symbol"] = symbol
                df["name"] = symbol
                return df
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "tencent 分时获取失败 %s（重试%d次后放弃）: %s",
                        symbol,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"tencent 分时获取失败: {symbol}") from exc
        return None

    def _fetch_tencent_quote(self, symbol: str) -> dict | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = _get_market_prefix(symbol)
                url = TENCENT_FULL_QUOTE_URL.format(market=market, symbol=symbol)
                response = self._session.get(url, timeout=10)
                parsed = self._parse_tencent_quote_response(response.text)
                return parsed.get(symbol)
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "tencent 实时报价获取失败 %s（重试%d次后放弃）: %s",
                        symbol,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"tencent 实时报价获取失败: {symbol}") from exc
        return None

    def _normalize_tencent_df(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        df["symbol"] = symbol
        df["name"] = symbol
        df["amount"] = df["volume"] * df["close"]
        df = apply_limit_suspended_adj(df, symbol, cache=self.cache)
        return df


def _normalize_tencent_intraday_volume_to_shares(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize Tencent minute volume row by row when it is reported in lots."""
    normalized = df.copy()
    if normalized.empty or "volume" not in normalized.columns:
        return normalized
    volume = pd.to_numeric(normalized["volume"], errors="coerce")
    empty = pd.Series(float("nan"), index=normalized.index)
    close = pd.to_numeric(normalized.get("close", empty), errors="coerce")
    amount = pd.to_numeric(normalized.get("amount", empty), errors="coerce")
    valid = (volume > 0) & (close > 0) & (amount > 0)
    implied_unit = amount / (close * volume)
    lots_mask = valid & implied_unit.between(20.0, 200.0)
    normalized["volume"] = volume.where(~lots_mask, volume * 100.0)
    normalized.attrs.update(df.attrs)
    normalized.attrs["volume_unit"] = "shares"
    return normalized
