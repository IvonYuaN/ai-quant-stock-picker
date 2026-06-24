from __future__ import annotations

import logging
import time
from datetime import date
from typing import Literal
import pandas as pd
import baostock as bs

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

_REQUEST_DELAY = 0.05
_MAX_RETRIES = 3

_logger = logging.getLogger("aqsp.data.baostock")


class BaostockSource(DataSource):
    name: str = "baostock"

    def __init__(self, cache: DataCache | None = None) -> None:
        self.cache = cache or DataCache()
        self._logged_in = False
        self._last_request_ts: float = 0.0

    def _ensure_login(self) -> None:
        if not self._logged_in:
            bs.login()
            self._logged_in = True

    def _logout(self) -> None:
        if self._logged_in:
            bs.logout()
            self._logged_in = False

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < _REQUEST_DELAY:
            time.sleep(_REQUEST_DELAY - elapsed)
        self._last_request_ts = time.monotonic()

    def _to_bs_code(self, symbol: str) -> str:
        if symbol.startswith("6"):
            return f"sh.{symbol}"
        return f"sz.{symbol}"

    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
    ) -> dict[str, OhlcvFrame]:
        self._ensure_login()
        out: dict[str, OhlcvFrame] = {}
        adj_map = {"": "1", "qfq": "2", "hfq": "3"}
        adjustflag = adj_map.get(adjust, "1")

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
                self._fetch_daily_single(symbol, start, end, adjustflag),
            )
            df = self._normalize_df(df, symbol)
            validated = self._validate_ohlcv(df, symbol)
            self.cache.set_ohlcv(
                symbol, validated, source="baostock", price_mode=adjust or "raw"
            )
            out[symbol] = validated
        require_non_empty_fetch_result(self.name, "日线", symbols, out)

        return out

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        self._ensure_login()
        out: dict[str, OhlcvFrame] = {}
        freq_map = {"1": "5", "5": "5", "15": "15", "30": "30", "60": "60"}
        freq = freq_map.get(period, "5")

        for symbol in symbols:
            out[symbol] = require_fetched_frame(
                self.name,
                "分时",
                symbol,
                self._fetch_intraday_single(symbol, freq),
            )

        require_non_empty_fetch_result(self.name, "分时", symbols, out)
        return out

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        self._ensure_login()
        quotes = {}
        for symbol in symbols:
            quotes[symbol] = require_fetched_mapping(
                self.name,
                "实时行情",
                symbol,
                self._fetch_quote_single(symbol),
            )
        require_non_empty_fetch_result(self.name, "实时行情", symbols, quotes)
        return quotes

    def fetch_index(
        self,
        index_codes: list[str],
        start: date,
        end: date,
    ) -> dict[str, OhlcvFrame]:
        self._ensure_login()
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
                self._fetch_index_single(code, start, end),
            )
            df = self._normalize_df(df, code)
            validated = self._validate_ohlcv(df, code)
            self.cache.set_index(code, validated, source="baostock")
            out[code] = validated

        require_non_empty_fetch_result(self.name, "指数", index_codes, out)
        return out

    def fetch_financial(
        self,
        symbols: list[str],
        start_year: int,
        end_year: int,
    ) -> dict[str, pd.DataFrame]:
        self._ensure_login()
        out: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            rows = []
            bs_code = self._to_bs_code(symbol)
            for year in range(start_year, end_year + 1):
                for quarter in range(1, 5):
                    self._throttle()
                    try:
                        rs = bs.query_profit_data(
                            code=bs_code, year=year, quarter=quarter
                        )
                        while (rs.error_code == "0") and rs.next():
                            row = rs.get_row_data()
                            if row:
                                rows.append(dict(zip(rs.fields, row)))
                    except Exception as exc:
                        # 单季度财务缺失属常见情况，用 debug 级避免刷屏
                        _logger.debug(
                            "baostock 财务 %s %dQ%d 获取失败: %s",
                            bs_code,
                            year,
                            quarter,
                            exc,
                        )
                        continue
            if rows:
                df = pd.DataFrame(rows)
                df["symbol"] = symbol
                out[symbol] = df
        return out

    def fetch_financial_pit(
        self,
        symbols: list[str],
        start_year: int,
        end_year: int,
    ) -> dict[str, pd.DataFrame]:
        raw = self.fetch_financial(symbols, start_year, end_year)
        pit: dict[str, pd.DataFrame] = {}
        for symbol, df in raw.items():
            if df.empty:
                continue
            df = df.copy()
            df["pubDate"] = pd.to_datetime(df["pubDate"], errors="coerce")
            df["statDate"] = pd.to_datetime(df["statDate"], errors="coerce")
            df = df.dropna(subset=["pubDate"])
            df = df.sort_values("pubDate")
            for col in ["roeAvg", "npMargin", "gpMargin", "epsTTM"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            pit[symbol] = df
        return pit

    def _fetch_daily_single(
        self, symbol: str, start: date, end: date, adjustflag: str
    ) -> pd.DataFrame | None:
        bs_code = self._to_bs_code(symbol)
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,open,high,low,close,volume,amount",
                    start_date=start.strftime("%Y-%m-%d"),
                    end_date=end.strftime("%Y-%m-%d"),
                    frequency="d",
                    adjustflag=adjustflag,
                )
                rows = []
                while (rs.error_code == "0") and rs.next():
                    rows.append(rs.get_row_data())
                if not rows:
                    return None
                df = pd.DataFrame(rows, columns=rs.fields)
                for col in ["open", "high", "low", "close", "volume", "amount"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["close"])
                return df
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(1)
                else:
                    _logger.warning(
                        "baostock 日线获取失败（重试%d次后放弃）: %s", _MAX_RETRIES, exc
                    )
                    raise DataError(f"baostock 日线获取失败: {symbol}") from exc
        return None

    def _fetch_intraday_single(self, symbol: str, freq: str) -> pd.DataFrame | None:
        bs_code = self._to_bs_code(symbol)
        try:
            self._throttle()
            today = now_shanghai().date()
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,time,open,high,low,close,volume,amount",
                start_date=today.strftime("%Y-%m-%d"),
                end_date=today.strftime("%Y-%m-%d"),
                frequency=freq,
                adjustflag="1",
            )
            rows = []
            while (rs.error_code == "0") and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                return None
            df = pd.DataFrame(rows, columns=rs.fields)
            for col in ["open", "high", "low", "close", "volume", "amount"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["symbol"] = symbol
            df["name"] = symbol
            return df
        except Exception as exc:
            _logger.warning("baostock 分时获取失败 %s: %s", symbol, exc)
            raise DataError(f"baostock 分时获取失败: {symbol}") from exc

    def _fetch_quote_single(self, symbol: str) -> dict | None:
        bs_code = self._to_bs_code(symbol)
        try:
            self._throttle()
            today = now_shanghai().date()
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume,amount",
                start_date=today.strftime("%Y-%m-%d"),
                end_date=today.strftime("%Y-%m-%d"),
                frequency="d",
                adjustflag="1",
            )
            rows = []
            while (rs.error_code == "0") and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                return None
            row = rows[-1]
            return {
                "price": float(row[4]) if row[4] else 0.0,
                "bid1": 0.0,
                "ask1": 0.0,
                "volume": float(row[5]) if row[5] else 0.0,
                "amount": float(row[6]) if row[6] else 0.0,
                "ts": now_shanghai().isoformat(),
            }
        except Exception as exc:
            _logger.warning("baostock 实时报价获取失败 %s: %s", symbol, exc)
            raise DataError(f"baostock 实时报价获取失败: {symbol}") from exc

    def _fetch_index_single(
        self, code: str, start: date, end: date
    ) -> pd.DataFrame | None:
        bs_code = f"sh.{code}" if code.startswith("000") else f"sz.{code}"
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,open,high,low,close,volume,amount",
                    start_date=start.strftime("%Y-%m-%d"),
                    end_date=end.strftime("%Y-%m-%d"),
                    frequency="d",
                    adjustflag="1",
                )
                rows = []
                while (rs.error_code == "0") and rs.next():
                    rows.append(rs.get_row_data())
                if not rows:
                    return None
                df = pd.DataFrame(rows, columns=rs.fields)
                for col in ["open", "high", "low", "close", "volume", "amount"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["close"])
                return df
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(1)
                else:
                    _logger.warning(
                        "baostock 指数日线获取失败（重试%d次后放弃）: %s",
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"baostock 指数获取失败: {code}") from exc
        return None

    def _normalize_df(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        df["symbol"] = symbol
        df["name"] = symbol
        if "amount" not in df.columns:
            df["amount"] = df["volume"] * df["close"]
        df = apply_limit_suspended_adj(df, symbol, cache=self.cache)
        return df
