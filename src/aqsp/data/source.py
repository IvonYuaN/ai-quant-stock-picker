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
    clean_symbol = str(symbol or "").strip().split(".", 1)[0]
    if "*ST" in name or name.endswith("ST"):
        return 0.05
    if clean_symbol.startswith(("688", "689")):
        return 0.20
    if clean_symbol.startswith(("300", "301")):
        return 0.20
    if clean_symbol.startswith(("43", "83", "87", "88")):
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
    # A股涨跌停价 = 昨收 × (1±幅度)，四舍五入到 0.01 元（交易所实际规则）。
    # 不舍入会导致用 11.033 这类非真实价位判定一字板/涨停，与实际可成交价错位。
    df["limit_up"] = (prev_close * (1 + limit_pct)).round(2)
    df["limit_down"] = (prev_close * (1 - limit_pct)).round(2)

    vol_zero = df["volume"] == 0 if "volume" in df.columns else False
    amt_zero = df["amount"] == 0 if "amount" in df.columns else False
    df["suspended"] = vol_zero | amt_zero

    if cache is not None:
        parsed_dates = pd.to_datetime(df["date"], errors="coerce")
        valid_dates = [item.date() for item in parsed_dates if not pd.isna(item)]
        if valid_dates and hasattr(cache, "get_adj_factors"):
            factors = cache.get_adj_factors(symbol, valid_dates)
            df["adj_factor"] = [
                factors.get(item.date(), 1.0) if not pd.isna(item) else 1.0
                for item in parsed_dates
            ]
        else:
            df["adj_factor"] = df["date"].apply(
                lambda d: cache.get_adj_factor(symbol, pd.to_datetime(d).date()) or 1.0
            )
    else:
        df["adj_factor"] = 1.0

    return df


def require_non_empty_fetch_result(
    source_name: str,
    method: str,
    requested: list[str],
    result: dict[str, object],
) -> None:
    requested_keys = [str(key) for key in requested if str(key)]
    if requested_keys and not result:
        raise DataError(f"{source_name} {method}获取失败: {requested}")
    present = {str(item) for item in result}
    missing = [key for key in requested_keys if key not in present]
    if missing:
        raise DataError(f"{source_name} {method}获取不完整: 缺少 {missing}")


def require_fetched_frame(
    source_name: str,
    method: str,
    key: str,
    frame: pd.DataFrame | None,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise DataError(f"{source_name} {method}获取失败: {key} 返回空结果")
    return frame


def require_fetched_mapping(
    source_name: str,
    method: str,
    key: str,
    data: dict | None,
) -> dict:
    if not data:
        raise DataError(f"{source_name} {method}获取失败: {key} 返回空结果")
    return data


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
        optional_numeric_columns = {"limit_up", "limit_down"}
        for col in NUMERIC_OHLCV_COLUMNS:
            if col in optional_numeric_columns:
                continue
            numeric = pd.to_numeric(df[col], errors="coerce")
            if numeric.isna().any():
                raise DataError(f"OHLCV 数据列存在无效数值: {symbol}.{col}")
            if col in {"open", "high", "low", "close"} and (numeric <= 0).any():
                raise DataError(f"OHLCV 价格必须为正: {symbol}.{col}")
            if col in {"volume", "amount"} and (numeric < 0).any():
                raise DataError(f"OHLCV 成交数据不能为负: {symbol}.{col}")
        high = pd.to_numeric(df["high"], errors="coerce")
        low = pd.to_numeric(df["low"], errors="coerce")
        open_ = pd.to_numeric(df["open"], errors="coerce")
        close = pd.to_numeric(df["close"], errors="coerce")
        if (high < low).any():
            raise DataError(f"OHLCV high 小于 low: {symbol}")
        if ((open_ < low) | (open_ > high) | (close < low) | (close > high)).any():
            raise DataError(f"OHLCV open/close 超出 high-low 区间: {symbol}")
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
