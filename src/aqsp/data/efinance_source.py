from __future__ import annotations

from datetime import date
from typing import Any, Literal

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai
from aqsp.data.cache import DataCache
from aqsp.data.source import DataSource, OhlcvFrame, apply_limit_suspended_adj


class EfinanceSource(DataSource):
    name: str = "efinance"

    def __init__(self, cache: DataCache | None = None) -> None:
        try:
            import efinance as ef

            self._ef = ef
        except ImportError as exc:
            raise RuntimeError(
                "efinance is not installed; run: pip install -e '.[data]'"
            ) from exc
        self.cache = cache or DataCache()

    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        adjust_flag = {"": 0, "qfq": 1, "hfq": 2}.get(adjust, 0)
        for symbol in symbols:
            cached = self.cache.get_ohlcv(symbol, start, end)
            if cached is not None and not cached.empty:
                out[symbol] = cached
                continue
            try:
                df = self._ef.stock.get_quote_history(
                    symbol,
                    beg=start.strftime("%Y%m%d"),
                    end=end.strftime("%Y%m%d"),
                    klt=101,
                    fqt=adjust_flag,
                )
            except Exception as exc:
                raise DataError(f"efinance 日线获取失败: {symbol} - {exc}") from exc
            if df is None or df.empty:
                continue
            normalized = self._normalize_efinance_daily_df(df, symbol)
            validated = self._validate_ohlcv(normalized, symbol)
            self.cache.set_ohlcv(symbol, validated, source="efinance")
            out[symbol] = validated
        return out

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        return {}

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        try:
            df = self._ef.stock.get_realtime_quotes()
        except Exception as exc:
            raise DataError(f"efinance 实时行情获取失败: {exc}") from exc
        if df is None or df.empty:
            return {}
        quotes: dict[str, dict] = {}
        for symbol in symbols:
            quote = self._normalize_efinance_quote_row(df, symbol)
            if quote is not None:
                quotes[symbol] = quote
        return quotes

    def fetch_index(
        self,
        index_codes: list[str],
        start: date,
        end: date,
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for code in index_codes:
            cached = self.cache.get_index(code, start, end)
            if cached is not None and not cached.empty:
                out[code] = cached
                continue
            try:
                df = self._ef.stock.get_quote_history(
                    code,
                    beg=start.strftime("%Y%m%d"),
                    end=end.strftime("%Y%m%d"),
                    klt=101,
                    fqt=0,
                )
            except Exception as exc:
                raise DataError(f"efinance 指数获取失败: {code} - {exc}") from exc
            if df is None or df.empty:
                continue
            normalized = self._normalize_efinance_daily_df(df, code)
            validated = self._validate_ohlcv(normalized, code)
            self.cache.set_index(code, validated, source="efinance")
            out[code] = validated
        return out

    def fetch_history_bill(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        try:
            df = self._ef.stock.get_history_bill(
                symbol,
                beg=start.strftime("%Y%m%d"),
                end=end.strftime("%Y%m%d"),
            )
        except Exception as exc:
            raise DataError(f"efinance 资金流获取失败: {symbol} - {exc}") from exc
        if df is None or df.empty:
            raise DataError(f"efinance 资金流为空: {symbol}")
        return self._normalize_efinance_fund_flow_df(df, symbol)

    def _normalize_efinance_daily_df(
        self,
        df: pd.DataFrame,
        symbol: str,
    ) -> pd.DataFrame:
        normalized = df.copy()
        normalized = self._normalize_date(normalized)
        normalized = self._normalize_symbol(normalized, symbol)
        normalized["name"] = self._pick_name_column(normalized, symbol)
        normalized = normalized.rename(
            columns={
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "成交量": "volume",
                "成交额": "amount",
            }
        )
        for column in ["open", "high", "low", "close", "volume", "amount"]:
            if column in normalized.columns:
                normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        if "amount" not in normalized.columns:
            normalized["amount"] = normalized["volume"] * normalized["close"]
        normalized = normalized.dropna(subset=["close"])
        return apply_limit_suspended_adj(normalized, symbol, cache=self.cache)

    def _normalize_efinance_quote_row(
        self,
        df: pd.DataFrame,
        symbol: str,
    ) -> dict[str, Any] | None:
        code_col = (
            "股票代码"
            if "股票代码" in df.columns
            else "代码"
            if "代码" in df.columns
            else ""
        )
        if not code_col:
            return None
        scoped = df[df[code_col].astype(str) == symbol]
        if scoped.empty:
            return None
        row = scoped.iloc[0]
        price = _coerce_float(row.get("最新价", row.get("最新", 0.0)))
        return {
            "price": price,
            "bid1": _coerce_float(row.get("买一", row.get("买入", price))),
            "ask1": _coerce_float(row.get("卖一", row.get("卖出", price))),
            "volume": _coerce_float(row.get("成交量", 0.0)),
            "amount": _coerce_float(row.get("成交额", 0.0)),
            "ts": now_shanghai().isoformat(),
        }

    def _normalize_efinance_fund_flow_df(
        self,
        df: pd.DataFrame,
        symbol: str,
    ) -> pd.DataFrame:
        normalized = df.copy()
        normalized = self._normalize_date(normalized)
        normalized["symbol"] = symbol
        rename_map = {
            "主力净流入": "main_net_inflow",
            "超大单净流入": "super_large_net_inflow",
            "大单净流入": "large_net_inflow",
            "中单净流入": "medium_net_inflow",
            "小单净流入": "small_net_inflow",
        }
        normalized = normalized.rename(columns=rename_map)
        for column in rename_map.values():
            if column in normalized.columns:
                normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        return normalized

    @staticmethod
    def _pick_name_column(df: pd.DataFrame, symbol: str) -> pd.Series:
        for column in ("股票名称", "名称", "name"):
            if column in df.columns:
                return df[column].astype(str)
        return pd.Series([symbol] * len(df), index=df.index, dtype="object")


def _coerce_float(value: Any) -> float:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(parsed):
        return 0.0
    return float(parsed)
