from __future__ import annotations

from typing import Literal
import pandas as pd

from aqsp.data.source import DataSource, OhlcvFrame
from aqsp.core.errors import DataError
from aqsp.core.time import today_shanghai, is_market_open
from aqsp.core.errors import MissingDataError


class IntradayService:
    def __init__(self, source: DataSource):
        self.source = source

    def get_intraday_bars(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        result = self.source.fetch_intraday(symbols, period)
        missing = [s for s in symbols if s not in result or result[s].empty]
        if missing and len(missing) == len(symbols):
            raise MissingDataError(symbols[0], reason=f"分时数据全部缺失: {missing}")
        return result

    def synthesize_daily_from_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
        *,
        target_date=None,
    ) -> dict[str, OhlcvFrame]:
        intraday_data = self.source.fetch_intraday(symbols, period)
        result = {}

        for symbol, df in intraday_data.items():
            if df.empty:
                continue
            result[symbol] = self._synthesize_single_symbol_daily(
                symbol,
                df,
                target_date=target_date,
            )

        if not result:
            raise MissingDataError(
                symbols[0], reason="所有标的分时数据缺失,无法合成日K"
            )
        return result

    def merge_intraday_bar_into_daily(
        self,
        daily_data: dict[str, pd.DataFrame],
        symbols: list[str],
        *,
        period: Literal["1", "5", "15", "30", "60"] = "5",
        target_date=None,
    ) -> dict[str, pd.DataFrame]:
        trade_day = target_date or today_shanghai()
        intraday_data = self.get_intraday_bars(symbols, period)
        merged: dict[str, pd.DataFrame] = {}

        for symbol in symbols:
            daily = daily_data.get(symbol)
            intraday_frame = intraday_data.get(symbol)
            if intraday_frame is None or intraday_frame.empty:
                raise MissingDataError(symbol, reason="缺少当日分时数据")
            synthesized = self._synthesize_single_symbol_daily(
                symbol,
                intraday_frame,
                target_date=trade_day,
            )
            merged[symbol] = self._merge_single_symbol_daily(
                daily,
                synthesized,
                trade_day=trade_day,
            )
        return merged

    def _merge_single_symbol_daily(
        self,
        daily: pd.DataFrame | None,
        intraday_daily: pd.DataFrame,
        *,
        trade_day,
    ) -> pd.DataFrame:
        intraday_daily = intraday_daily.copy()
        intraday_day_text = trade_day.isoformat()
        if daily is None or daily.empty:
            return intraday_daily.reset_index(drop=True)

        base = daily.copy()
        base["date"] = pd.to_datetime(base["date"], errors="coerce").dt.strftime(
            "%Y-%m-%d"
        )
        base = base.dropna(subset=["date"])
        base = base[base["date"] != intraday_day_text]
        merged = pd.concat([base, intraday_daily], ignore_index=True)
        merged = merged.sort_values("date").reset_index(drop=True)
        return merged

    def _synthesize_single_symbol_daily(
        self,
        symbol: str,
        df: pd.DataFrame,
        *,
        target_date=None,
    ) -> pd.DataFrame:
        normalized = df.copy()
        normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
        normalized = normalized.dropna(subset=["date"]).sort_values("date")
        resolved_date = target_date
        if resolved_date is None:
            if normalized.empty:
                raise MissingDataError(symbol, reason="分时数据为空")
            resolved_date = normalized["date"].dt.date.iloc[-1]
        normalized = normalized[
            normalized["date"].dt.date == resolved_date
        ].reset_index(drop=True)
        if normalized.empty:
            raise MissingDataError(
                symbol,
                reason=f"分时数据不含 {resolved_date.isoformat()} 当日 bar",
            )

        for column in ("open", "high", "low", "close", "volume"):
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        if normalized[["open", "high", "low", "close", "volume"]].isna().any().any():
            raise DataError(f"分时数据存在无效数值: {symbol}")

        first_bar = normalized.iloc[0]
        last_bar = normalized.iloc[-1]
        amount_series = (
            pd.to_numeric(normalized["amount"], errors="coerce")
            if "amount" in normalized.columns
            else pd.Series([], dtype=float)
        )
        amount = float(amount_series.fillna(0.0).sum()) if not amount_series.empty else 0.0
        name_value = symbol
        if "name" in normalized.columns and not normalized["name"].dropna().empty:
            name_value = str(normalized["name"].dropna().iloc[-1])

        return pd.DataFrame(
            {
                "date": [resolved_date.isoformat()],
                "symbol": [symbol],
                "name": [name_value],
                "open": [float(first_bar["open"])],
                "high": [float(normalized["high"].max())],
                "low": [float(normalized["low"].min())],
                "close": [float(last_bar["close"])],
                "volume": [float(normalized["volume"].sum())],
                "amount": [amount],
                "suspended": [False],
                "limit_up": [0.0],
                "limit_down": [0.0],
                "adj_factor": [1.0],
            }
        )

    def merge_intraday_with_daily(
        self,
        daily_data: dict[str, pd.DataFrame],
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, pd.DataFrame]:
        result = {}

        if not is_market_open():
            return daily_data

        intraday = self.synthesize_daily_from_intraday(symbols, period)

        for symbol in symbols:
            daily = daily_data.get(symbol)
            if daily is None or daily.empty:
                if symbol in intraday:
                    result[symbol] = intraday[symbol]
                continue

            daily = daily.copy().sort_values("date").reset_index(drop=True)

            if symbol in intraday:
                today_intraday = intraday[symbol].iloc[0]
                today_date = today_intraday["date"]

                mask = daily["date"] != today_date
                daily = pd.concat(
                    [daily[mask], pd.DataFrame([today_intraday])],
                    ignore_index=True,
                )

            result[symbol] = daily

        return result

    def get_current_bar(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, pd.Series]:
        intraday = self.source.fetch_intraday(symbols, period)
        result = {}

        for symbol, df in intraday.items():
            if not df.empty:
                result[symbol] = df.iloc[-1]

        if not result:
            raise MissingDataError(symbols[0], reason="所有标的当前Bar数据缺失")
        return result
