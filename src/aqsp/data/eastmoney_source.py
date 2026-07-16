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
from aqsp.data.quote_metadata import parse_vendor_timestamp, quote_timestamp_metadata

_REQUEST_DELAY = 0.3
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0
_SPOT_PAGE_SIZE = 200

_logger = logging.getLogger("aqsp.data.eastmoney")


class EastmoneySource(DataSource):
    name: str = "eastmoney"

    def __init__(self, cache: DataCache | None = None) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Referer": "https://quote.eastmoney.com",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
        )
        self.cache = cache or DataCache()
        self._last_request_ts: float = 0.0
        self._active_workload: str | None = None

    def set_workload(self, workload: str | None) -> None:
        """Set provenance context for the next cache-backed fetch."""
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
                out[symbol] = self._annotate_frame(
                    _normalize_stock_volume_to_shares(cached)
                )
                continue

            df = require_fetched_frame(
                self.name,
                "日线",
                symbol,
                self._fetch_eastmoney_daily(symbol, start, end),
            )
            df = self._normalize_eastmoney_df(
                _normalize_stock_volume_to_shares(df), symbol
            )
            validated = self._validate_ohlcv(df, symbol)
            self.cache.set_ohlcv(
                symbol,
                validated,
                source=self.name,
                price_mode=adjust or "raw",
                workload=self._cache_workload(),
            )
            out[symbol] = self._annotate_frame(validated)
        require_non_empty_fetch_result(self.name, "日线", symbols, out)
        return out

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for symbol in symbols:
            out[symbol] = _normalize_stock_volume_to_shares(
                require_fetched_frame(
                    self.name,
                    "分时",
                    symbol,
                    self._fetch_eastmoney_intraday(symbol, period),
                )
            )
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
                self._fetch_eastmoney_intraday(code, period, is_index=True),
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
                self._fetch_eastmoney_quote(symbol),
            )
        require_non_empty_fetch_result(self.name, "实时行情", symbols, quotes)
        return quotes

    def get_available_symbols(self) -> list[str]:
        snapshot = self._fetch_eastmoney_spot_snapshot()
        if snapshot.empty or "symbol" not in snapshot.columns:
            raise DataError("eastmoney 全市场快照未返回可用标的")
        return snapshot["symbol"].astype(str).tolist()

    def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
        snapshot = self._fetch_eastmoney_spot_snapshot()
        if snapshot.empty:
            raise DataError("eastmoney 全市场快照为空，无法筛选高流动性标的")
        min_amount_value = max(float(min_amount or 0.0), 0.0)
        row_limit = max(int(limit or 0), 0)
        liquid = snapshot[snapshot["amount"] >= min_amount_value].sort_values(
            "amount",
            ascending=False,
        )
        if row_limit > 0:
            liquid = liquid.head(row_limit)
        return liquid["symbol"].astype(str).tolist()

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
                self._fetch_eastmoney_index(code, start, end),
            )
            df = self._normalize_eastmoney_df(df, code)
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

    def _fetch_eastmoney_daily(
        self, symbol: str, start: date, end: date
    ) -> pd.DataFrame | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = "1" if symbol.startswith("6") else "0"
                url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                params = {
                    "secid": f"{market}.{symbol}",
                    "ut": "7eea3edcaed734bea9cbfc24409ed989",
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "klt": "101",
                    "fqt": "0",
                    "beg": start.strftime("%Y%m%d"),
                    "end": end.strftime("%Y%m%d"),
                }
                response = self._session.get(url, params=params, timeout=10)
                data = response.json()
                if not data.get("data"):
                    return None
                payload = data["data"]
                klines = payload.get("klines", [])
                if not klines:
                    return None
                rows = []
                for kline in klines:
                    parts = kline.split(",")
                    if len(parts) >= 11:
                        rows.append(
                            {
                                "date": parts[0],
                                "open": float(parts[1]),
                                "close": float(parts[2]),
                                "high": float(parts[3]),
                                "low": float(parts[4]),
                                "volume": float(parts[5]),
                                # Eastmoney 日线 kline:
                                # 0日期 1开 2收 3高 4低 5成交量 6成交额 7振幅 8涨跌幅 9涨跌额 10换手率
                                # 这里必须取 parts[6]，之前误取 parts[9]（涨跌额），
                                # 会把大票真实成交额误判成几十/几百，触发流动性错杀。
                                "amount": float(parts[6]),
                            }
                        )
                frame = pd.DataFrame(rows)
                frame["name"] = str(payload.get("name", "") or symbol)
                return frame
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "eastmoney 日线获取失败 %s（重试%d次后放弃）: %s",
                        symbol,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"eastmoney 日线获取失败: {symbol}") from exc
        return None

    def _fetch_eastmoney_intraday(
        self, symbol: str, period: str, *, is_index: bool = False
    ) -> pd.DataFrame | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = "1" if is_index or symbol.startswith("6") else "0"
                klt_map = {"1": "1", "5": "5", "15": "15", "30": "30", "60": "60"}
                url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                params = {
                    "secid": f"{market}.{symbol}",
                    "ut": "7eea3edcaed734bea9cbfc24409ed989",
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "klt": klt_map.get(period, "5"),
                    "fqt": "0",
                }
                response = self._session.get(url, params=params, timeout=10)
                data = response.json()
                if not data.get("data"):
                    return None
                payload = data["data"]
                klines = payload.get("klines", [])
                if not klines:
                    return None
                rows = []
                for kline in klines:
                    parts = kline.split(",")
                    if len(parts) >= 11:
                        rows.append(
                            {
                                "date": parts[0],
                                "open": float(parts[1]),
                                "close": float(parts[2]),
                                "high": float(parts[3]),
                                "low": float(parts[4]),
                                "volume": float(parts[5]),
                                "amount": float(parts[6]),
                            }
                        )
                df = pd.DataFrame(rows)
                df["symbol"] = symbol
                df["name"] = symbol
                return df
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "eastmoney 分时获取失败 %s（重试%d次后放弃）: %s",
                        symbol,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"eastmoney 分时获取失败: {symbol}") from exc
        return None

    def _fetch_eastmoney_quote(self, symbol: str) -> dict | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = "1" if symbol.startswith("6") else "0"
                url = "https://push2.eastmoney.com/api/qt/stock/get"
                params = {
                    "secid": f"{market}.{symbol}",
                    "fields": "f57,f58,f10,f60,f61,f152,f168,f177",
                }
                response = self._session.get(url, params=params, timeout=10)
                data = response.json()
                if not data.get("data"):
                    return None
                d = data["data"]
                received_at = now_shanghai().isoformat()
                vendor_ts = parse_vendor_timestamp(d.get("f86") or d.get("f124"))
                return {
                    "price": float(d.get("f60", 0)),
                    "bid1": float(d.get("f152", 0)),
                    "ask1": float(d.get("f168", 0)),
                    "volume": float(d.get("f61", 0)),
                    "amount": float(d.get("f177", 0)),
                    **quote_timestamp_metadata(vendor_ts, received_at),
                }
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "eastmoney 实时报价获取失败 %s（重试%d次后放弃）: %s",
                        symbol,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"eastmoney 实时报价获取失败: {symbol}") from exc
        return None

    def _fetch_eastmoney_spot_snapshot(self) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        page = 1
        while True:
            data = self._fetch_eastmoney_spot_page(page)
            diff = (data.get("data") or {}).get("diff") or []
            if not diff:
                break
            for item in diff:
                symbol = str(item.get("f12") or "").strip()
                name = str(item.get("f14") or "").strip()
                if len(symbol) != 6 or not symbol.isdigit():
                    continue
                if name.startswith(("ST", "*ST", "退市")):
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "name": name or symbol,
                        "price": _safe_float(item.get("f2")),
                        "volume": _safe_float(item.get("f5")),
                        "amount": _safe_float(item.get("f6")),
                    }
                )
            if len(diff) < _SPOT_PAGE_SIZE:
                break
            page += 1
        if not rows:
            raise DataError("eastmoney 全市场快照无有效 A 股标的")
        return pd.DataFrame(rows)

    def _fetch_eastmoney_spot_page(self, page: int) -> dict:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                url = "https://push2.eastmoney.com/api/qt/clist/get"
                params = {
                    "pn": page,
                    "pz": _SPOT_PAGE_SIZE,
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f6",
                    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                    "fields": "f2,f5,f6,f12,f14",
                }
                response = self._session.get(url, params=params, timeout=10)
                return response.json()
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "eastmoney 全市场快照获取失败 page=%s（重试%d次后放弃）: %s",
                        page,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError("eastmoney 全市场快照获取失败") from exc
        return {}

    def _fetch_eastmoney_index(
        self, code: str, start: date, end: date
    ) -> pd.DataFrame | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = "1" if code.startswith("000") else "0"
                url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                params = {
                    "secid": f"{market}.{code}",
                    "ut": "7eea3edcaed734bea9cbfc24409ed989",
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "klt": "101",
                    "fqt": "0",
                    "beg": start.strftime("%Y%m%d"),
                    "end": end.strftime("%Y%m%d"),
                }
                response = self._session.get(url, params=params, timeout=10)
                data = response.json()
                if not data.get("data"):
                    return None
                payload = data["data"]
                klines = payload.get("klines", [])
                if not klines:
                    return None
                rows = []
                for kline in klines:
                    parts = kline.split(",")
                    if len(parts) >= 11:
                        rows.append(
                            {
                                "date": parts[0],
                                "open": float(parts[1]),
                                "close": float(parts[2]),
                                "high": float(parts[3]),
                                "low": float(parts[4]),
                                "volume": float(parts[5]),
                                "amount": float(parts[6]),
                            }
                        )
                frame = pd.DataFrame(rows)
                frame["name"] = str(payload.get("name", "") or code)
                return frame
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "eastmoney 指数获取失败 %s（重试%d次后放弃）: %s",
                        code,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"eastmoney 指数获取失败: {code}") from exc
        return None

    def _normalize_eastmoney_df(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        df["symbol"] = symbol
        if "name" not in df.columns:
            df["name"] = symbol
        else:
            df["name"] = df["name"].astype(str).replace("", symbol)
        df = apply_limit_suspended_adj(df, symbol, cache=self.cache)
        return df


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_stock_volume_to_shares(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize Eastmoney stock volume from lots to the project-wide share unit."""
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
