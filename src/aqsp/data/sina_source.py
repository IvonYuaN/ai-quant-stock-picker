from __future__ import annotations

from datetime import date
from typing import Literal
import pandas as pd
import requests

from aqsp.data.source import DataSource, OhlcvFrame, apply_limit_suspended_adj


class SinaSource(DataSource):
    name: str = "sina"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Referer": "http://finance.sina.com.cn",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
        )

    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for symbol in symbols:
            df = self._fetch_sina_daily(symbol, start, end)
            if df is not None and not df.empty:
                df = self._normalize_sina_df(df, symbol)
                out[symbol] = self._validate_ohlcv(df, symbol)
        return out

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for symbol in symbols:
            df = self._fetch_sina_intraday(symbol, period)
            if df is not None and not df.empty:
                out[symbol] = df
        return out

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        quotes = {}
        for symbol in symbols:
            data = self._fetch_sina_quote(symbol)
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
            df = self._fetch_sina_daily(code, start, end, is_index=True)
            if df is not None and not df.empty:
                df = self._normalize_sina_df(df, code)
                out[code] = self._validate_ohlcv(df, code)
        return out

    def _fetch_sina_daily(
        self, symbol: str, start: date, end: date, is_index: bool = False
    ) -> pd.DataFrame | None:
        try:
            market = "sh" if symbol.startswith("6") else "sz"
            url = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
            params = {
                "symbol": f"{market}{symbol}",
                "scale": "240",
                "ma": "no",
                "datalen": "1023",
            }
            response = self._session.get(url, params=params)
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
        except Exception:
            return None

    def _fetch_sina_intraday(self, symbol: str, period: str) -> pd.DataFrame | None:
        try:
            market = "sh" if symbol.startswith("6") else "sz"
            url = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
            scale_map = {"1": "60", "5": "300", "15": "900", "30": "1800", "60": "240"}
            params = {
                "symbol": f"{market}{symbol}",
                "scale": scale_map.get(period, "300"),
                "ma": "no",
                "datalen": "100",
            }
            response = self._session.get(url, params=params)
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
        except Exception:
            return None

    def _fetch_sina_quote(self, symbol: str) -> dict | None:
        try:
            market = "sh" if symbol.startswith("6") else "sz"
            url = f"http://hq.sinajs.cn/list={market}{symbol}"
            response = self._session.get(url)
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
                "ts": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            }
        except Exception:
            return None

    def _normalize_sina_df(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        df["symbol"] = symbol
        df["name"] = symbol
        df["amount"] = df["volume"] * df["close"]
        df = apply_limit_suspended_adj(df, symbol)
        return df
