from __future__ import annotations

from datetime import date
from typing import Literal
import pandas as pd
import requests

from aqsp.data.source import DataSource, OhlcvFrame, apply_limit_suspended_adj


class EastmoneySource(DataSource):
    name: str = "eastmoney"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Referer": "https://quote.eastmoney.com",
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
            df = self._fetch_eastmoney_daily(symbol, start, end)
            if df is not None and not df.empty:
                df = self._normalize_eastmoney_df(df, symbol)
                out[symbol] = self._validate_ohlcv(df, symbol)
        return out

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for symbol in symbols:
            df = self._fetch_eastmoney_intraday(symbol, period)
            if df is not None and not df.empty:
                out[symbol] = df
        return out

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        quotes = {}
        for symbol in symbols:
            data = self._fetch_eastmoney_quote(symbol)
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
            df = self._fetch_eastmoney_index(code, start, end)
            if df is not None and not df.empty:
                df = self._normalize_eastmoney_df(df, code)
                out[code] = self._validate_ohlcv(df, code)
        return out

    def _fetch_eastmoney_daily(
        self, symbol: str, start: date, end: date
    ) -> pd.DataFrame | None:
        try:
            market = "1" if symbol.startswith("6") else "0"
            url = "https://push2.eastmoney.com/api/qt/stock/kline/get"
            params = {
                "secid": f"{market}.{symbol}",
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",
                "fqt": "0",
                "beg": start.strftime("%Y%m%d"),
                "end": end.strftime("%Y%m%d"),
            }
            response = self._session.get(url, params=params)
            data = response.json()
            if not data.get("data"):
                return None
            klines = data["data"].get("klines", [])
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
                            "amount": float(parts[9]),
                        }
                    )
            return pd.DataFrame(rows)
        except Exception:
            return None

    def _fetch_eastmoney_intraday(
        self, symbol: str, period: str
    ) -> pd.DataFrame | None:
        try:
            market = "1" if symbol.startswith("6") else "0"
            klt_map = {"1": "1", "5": "5", "15": "15", "30": "30", "60": "60"}
            url = "https://push2.eastmoney.com/api/qt/stock/kline/get"
            params = {
                "secid": f"{market}.{symbol}",
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": klt_map.get(period, "5"),
                "fqt": "0",
            }
            response = self._session.get(url, params=params)
            data = response.json()
            if not data.get("data"):
                return None
            klines = data["data"].get("klines", [])
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
                        }
                    )
            df = pd.DataFrame(rows)
            df["symbol"] = symbol
            df["name"] = symbol
            return df
        except Exception:
            return None

    def _fetch_eastmoney_quote(self, symbol: str) -> dict | None:
        try:
            market = "1" if symbol.startswith("6") else "0"
            url = "https://push2.eastmoney.com/api/qt/stock/get"
            params = {
                "secid": f"{market}.{symbol}",
                "fields": "f57,f58,f10,f60,f61,f152,f168,f177",
            }
            response = self._session.get(url, params=params)
            data = response.json()
            if not data.get("data"):
                return None
            d = data["data"]
            return {
                "price": float(d.get("f60", 0)),
                "bid1": float(d.get("f152", 0)),
                "ask1": float(d.get("f168", 0)),
                "volume": float(d.get("f61", 0)),
                "amount": float(d.get("f177", 0)),
                "ts": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            }
        except Exception:
            return None

    def _fetch_eastmoney_index(
        self, code: str, start: date, end: date
    ) -> pd.DataFrame | None:
        try:
            market = "1" if code.startswith("000") else "0"
            url = "https://push2.eastmoney.com/api/qt/stock/kline/get"
            params = {
                "secid": f"{market}.{code}",
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",
                "fqt": "0",
                "beg": start.strftime("%Y%m%d"),
                "end": end.strftime("%Y%m%d"),
            }
            response = self._session.get(url, params=params)
            data = response.json()
            if not data.get("data"):
                return None
            klines = data["data"].get("klines", [])
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
                            "amount": float(parts[9]),
                        }
                    )
            return pd.DataFrame(rows)
        except Exception:
            return None

    def _normalize_eastmoney_df(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        df["symbol"] = symbol
        df["name"] = symbol
        df = apply_limit_suspended_adj(df, symbol)
        return df
