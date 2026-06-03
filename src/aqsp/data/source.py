from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import TYPE_CHECKING, Literal
import pandas as pd

from aqsp.core.errors import DataError

if TYPE_CHECKING:
    from aqsp.data.cache import DataCache

OhlcvFrame = pd.DataFrame

# Standard OHLCV schema required by docs/architecture.md §3.1.
REQUIRED_OHLCV_COLUMNS = {
    "date",
    "symbol",
    "name",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "suspended",
    "limit_up",
    "limit_down",
}
NUMERIC_OHLCV_COLUMNS = {
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "limit_up",
    "limit_down",
}


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
        missing = REQUIRED_OHLCV_COLUMNS - set(df.columns)
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise DataError(f"OHLCV 数据缺少必要列: {symbol} 缺少 {missing_text}")
        if df.empty:
            raise DataError(f"OHLCV 数据为空: {symbol}")
        invalid_dates = pd.to_datetime(df["date"], errors="coerce").isna()
        if invalid_dates.any():
            raise DataError(f"OHLCV 数据日期无效: {symbol}")
        for col in NUMERIC_OHLCV_COLUMNS:
            if pd.to_numeric(df[col], errors="coerce").isna().all():
                raise DataError(f"OHLCV 数据列无有效数值: {symbol}.{col}")
        if df["suspended"].isna().any():
            raise DataError(f"OHLCV 数据 suspended 缺失: {symbol}")
        return df

    def _normalize_date(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if "date" not in df.columns:
            if "日期" in df.columns:
                df["date"] = df["日期"]
            elif "trade_date" in df.columns:
                df["date"] = df["trade_date"]
        # 用 coerce 解析：脏日期变 NaT 而非裸 ValueError 崩溃。
        # 数据源原始数据若含非法日期，保留 NaT 交给 _validate_ohlcv 的
        # invalid_dates 检查 fail loud 报规范的 DataError（宪法：数据失效硬报错）。
        parsed = pd.to_datetime(df["date"], errors="coerce")
        df["date"] = parsed.dt.strftime("%Y-%m-%d").where(parsed.notna(), df["date"])
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
