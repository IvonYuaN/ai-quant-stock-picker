from __future__ import annotations

import logging
from datetime import date
from typing import Literal
import pandas as pd

from aqsp.data.source import (
    DataSource,
    OhlcvFrame,
    apply_limit_suspended_adj,
    require_fetched_frame,
    require_fetched_mapping,
    require_non_empty_fetch_result,
)
from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai

_logger = logging.getLogger("aqsp.data.mootdx")

try:
    from mootdx.quotes import Quotes

    MOOTDX_AVAILABLE = True
except ImportError:
    MOOTDX_AVAILABLE = False


def _get_market_code(symbol: str) -> int:
    if symbol.startswith("6"):
        return 1
    return 0


class MootdxSource(DataSource):
    name: str = "mootdx"

    def __init__(self) -> None:
        if not MOOTDX_AVAILABLE:
            raise ImportError(
                "mootdx is not installed. Install it with: pip install mootdx"
            )
        self._client = Quotes.factory(market="std")

    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
        count: int = 800,
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for symbol in symbols:
            df = require_fetched_frame(
                self.name,
                "日线",
                symbol,
                self._fetch_mootdx_daily(symbol, start, end, count=count),
            )
            df = self._normalize_mootdx_df(df, symbol)
            out[symbol] = self._validate_ohlcv(df, symbol)
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
                self._fetch_mootdx_intraday(symbol, period),
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
                self._fetch_mootdx_quote(symbol),
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
            df = require_fetched_frame(
                self.name,
                "指数",
                code,
                self._fetch_mootdx_daily(code, start, end, is_index=True),
            )
            df = self._normalize_mootdx_df(df, code)
            out[code] = self._validate_ohlcv(df, code)
        require_non_empty_fetch_result(self.name, "指数", index_codes, out)
        return out

    def _fetch_mootdx_daily(
        self,
        symbol: str,
        start: date,
        end: date,
        is_index: bool = False,
        count: int = 800,
    ) -> pd.DataFrame | None:
        try:
            df = self._client.bars(
                symbol=symbol,
                frequency=9,
                offset=0,
                count=count,
            )
            if df is None or df.empty:
                return None
            df = df.reset_index()
            if "datetime" in df.columns:
                df["date"] = pd.to_datetime(df["datetime"]).dt.strftime("%Y-%m-%d")
            elif "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            else:
                return None
            start_str = start.strftime("%Y-%m-%d")
            end_str = end.strftime("%Y-%m-%d")
            df = df[(df["date"] >= start_str) & (df["date"] <= end_str)]
            return df
        except Exception as exc:
            _logger.warning("mootdx 日线获取失败 %s: %s", symbol, exc)
            raise DataError(f"mootdx 日线获取失败: {symbol}") from exc

    def _fetch_mootdx_intraday(self, symbol: str, period: str) -> pd.DataFrame | None:
        try:
            frequency_map = {"1": 8, "5": 0, "15": 1, "30": 2, "60": 3}
            frequency = frequency_map.get(period, 0)
            df = self._client.bars(
                symbol=symbol,
                frequency=frequency,
                offset=0,
                count=100,
            )
            if df is None or df.empty:
                return None
            df = df.reset_index()
            if "datetime" in df.columns:
                df["date"] = df["datetime"].astype(str)
            elif "date" in df.columns:
                df["date"] = df["date"].astype(str)
            df["symbol"] = symbol
            df["name"] = symbol
            return df
        except Exception as exc:
            _logger.warning("mootdx 分时获取失败 %s: %s", symbol, exc)
            raise DataError(f"mootdx 分时获取失败: {symbol}") from exc

    def _fetch_mootdx_quote(self, symbol: str) -> dict | None:
        try:
            df = self._client.quotes(symbol=[symbol])
            if df is None or df.empty:
                return None
            row = df.iloc[0]
            return {
                "price": float(row.get("price", 0)),
                "bid1": float(row.get("bid1", 0)),
                "ask1": float(row.get("ask1", 0)),
                "volume": float(row.get("vol", 0)),
                "amount": float(row.get("amount", 0)),
                "ts": now_shanghai().isoformat(),
            }
        except Exception as exc:
            _logger.warning("mootdx 实时报价获取失败 %s: %s", symbol, exc)
            raise DataError(f"mootdx 实时报价获取失败: {symbol}") from exc

    def _normalize_mootdx_df(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        if "open" not in df.columns and "open_x" in df.columns:
            df = df.rename(
                columns={
                    "open_x": "open",
                    "high_x": "high",
                    "low_x": "low",
                    "close_x": "close",
                }
            )
        df["symbol"] = symbol
        df["name"] = symbol
        df = apply_limit_suspended_adj(df, symbol)
        return df
