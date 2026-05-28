from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import TYPE_CHECKING, Literal
import pandas as pd

from aqsp.core.errors import DataError

if TYPE_CHECKING:
    from aqsp.data.cache import DataCache

OhlcvFrame = pd.DataFrame


def get_limit_pct(symbol: str, name: str = "") -> float:
    if "*ST" in name or name.endswith("ST"):
        return 0.05
    if symbol.startswith("688") or symbol.startswith("689"):
        return 0.20
    if symbol.startswith("300") or symbol.startswith("301"):
        return 0.20
    if symbol.startswith("8") or symbol.startswith("4"):
        return 0.30
    return 0.10


def apply_limit_suspended_adj(
    df: pd.DataFrame,
    symbol: str,
    cache: DataCache | None = None,
) -> pd.DataFrame:
    df = df.copy()
    name = df["name"].iloc[0] if "name" in df.columns and len(df) > 0 else ""
    limit_pct = get_limit_pct(symbol, name)

    prev_close = df["close"].shift(1)
    df["limit_up"] = prev_close * (1 + limit_pct)
    df["limit_down"] = prev_close * (1 - limit_pct)

    vol_zero = df["volume"] == 0 if "volume" in df.columns else False
    amt_zero = df["amount"] == 0 if "amount" in df.columns else False
    df["suspended"] = vol_zero | amt_zero

    if cache is not None:
        df["adj_factor"] = df["date"].apply(
            lambda d: cache.get_adj_factor(symbol, pd.to_datetime(d).date()) or 1.0
        )
    else:
        df["adj_factor"] = 1.0

    return df


class DataSource(ABC):
    name: str

    @abstractmethod
    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
    ) -> dict[str, OhlcvFrame]: ...

    @abstractmethod
    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]: ...

    @abstractmethod
    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]: ...

    @abstractmethod
    def fetch_index(
        self,
        index_codes: list[str],
        start: date,
        end: date,
    ) -> dict[str, OhlcvFrame]: ...

    def _validate_ohlcv(self, df: pd.DataFrame, symbol: str) -> OhlcvFrame:
        required_columns = {
            "date",
            "symbol",
            "name",
            "open",
            "high",
            "low",
            "close",
            "volume",
        }
        missing = required_columns - set(df.columns)
        if missing:
            raise DataError(f"OHLCV 数据缺少必要列: {symbol} 缺少 {missing}")
        return df

    def _normalize_date(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if "date" not in df.columns:
            if "日期" in df.columns:
                df["date"] = df["日期"]
            elif "trade_date" in df.columns:
                df["date"] = df["trade_date"]
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df

    def _normalize_symbol(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        if "symbol" not in df.columns:
            if "代码" in df.columns:
                df["symbol"] = df["代码"].astype(str)
            else:
                df["symbol"] = symbol
        df["symbol"] = df["symbol"].astype(str)
        return df
