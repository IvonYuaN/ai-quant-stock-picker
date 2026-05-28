from __future__ import annotations

from datetime import date
from typing import Literal
import pandas as pd

from aqsp.data.source import DataSource, OhlcvFrame, apply_limit_suspended_adj
from aqsp.data.cache import DataCache
from aqsp.core.errors import DataError


class AkshareSource(DataSource):
    name: str = "akshare"

    def __init__(self, cache: DataCache | None = None) -> None:
        try:
            import akshare as ak

            self._ak = ak
        except ImportError as exc:
            raise RuntimeError(
                "akshare is not installed; run: pip install -e '.[data]'"
            ) from exc
        self.cache = cache or DataCache()

    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
        use_cache: bool = True,
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for symbol in symbols:
            cached = None
            if use_cache:
                cached = self.cache.get_ohlcv(symbol, start, end)

            if (
                cached is not None
                and not cached.empty
                and self._cache_covers_range(cached, start, end)
            ):
                out[symbol] = cached
                continue

            df = self._ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust=adjust,
            )
            if df.empty:
                continue
            df = self._normalize_akshare_df(df, symbol)
            validated = self._validate_ohlcv(df, symbol)

            if use_cache:
                self.cache.set_ohlcv(symbol, validated, source="akshare")

            out[symbol] = validated
        return out

    @staticmethod
    def _cache_covers_range(cached: pd.DataFrame, start: date, end: date) -> bool:
        dates = pd.to_datetime(cached["date"]).sort_values()
        if dates.iloc[0].date() > start or dates.iloc[-1].date() < end:
            return False
        gaps = dates.diff().dt.days.dropna()
        if (gaps > 5).any():
            return False
        return True

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for symbol in symbols:
            df = self._ak.stock_zh_a_minute(
                symbol=symbol,
                period=period,
                adjust="",
            )
            if df.empty:
                continue
            df = self._normalize_intraday_df(df, symbol)
            out[symbol] = df
        return out

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        quotes = {}
        try:
            df = self._ak.stock_zh_a_spot_em()
            df["代码"] = df["代码"].astype(str)
            for symbol in symbols:
                row = df[df["代码"] == symbol]
                if not row.empty:
                    quotes[symbol] = {
                        "price": float(row.iloc[0]["最新价"]),
                        "bid1": float(row.iloc[0]["买一价"]),
                        "ask1": float(row.iloc[0]["卖一价"]),
                        "volume": float(row.iloc[0]["成交量"]),
                        "amount": float(row.iloc[0]["成交额"]),
                        "ts": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
                    }
        except Exception as e:
            raise DataError(f"获取实时行情失败: {e}") from e
        return quotes

    def fetch_index(
        self,
        index_codes: list[str],
        start: date,
        end: date,
        use_cache: bool = True,
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for code in index_codes:
            cached = None
            if use_cache:
                cached = self.cache.get_index(code, start, end)

            if cached is not None and not cached.empty:
                out[code] = cached
                continue

            df = self._fetch_index_single(code, start, end)
            if df is not None and not df.empty:
                df = self._normalize_akshare_df(df, code)
                validated = self._validate_ohlcv(df, code)

                if use_cache:
                    self.cache.set_index(code, validated, source="akshare")

                out[code] = validated
        return out

    def _fetch_index_single(
        self, code: str, start: date, end: date
    ) -> pd.DataFrame | None:
        candidates = (
            lambda: self._ak.stock_zh_index_daily_em(symbol=f"sh{code}"),
            lambda: self._ak.stock_zh_index_daily_em(symbol=f"sz{code}"),
            lambda: self._ak.index_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            ),
        )
        for fetch in candidates:
            try:
                df = fetch()
            except Exception:
                continue
            if df is None or df.empty:
                continue
            return df
        return None

    def _normalize_akshare_df(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        df = self._normalize_date(df)
        df = self._normalize_symbol(df, symbol)
        if "名称" in df.columns:
            df["name"] = df["名称"]
        else:
            df["name"] = symbol
        rename_map = {
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
        }
        df = df.rename(columns=rename_map)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "amount" not in df.columns:
            df["amount"] = df["volume"] * df["close"]
        df = apply_limit_suspended_adj(df, symbol, cache=self.cache)
        return df

    def _normalize_intraday_df(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        df["date"] = pd.to_datetime(df["时间"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        df["symbol"] = symbol
        df["name"] = symbol
        rename_map = {
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
        }
        df = df.rename(columns=rename_map)
        return df
