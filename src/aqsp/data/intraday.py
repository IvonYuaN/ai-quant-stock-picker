from __future__ import annotations

from typing import Literal
import pandas as pd

from aqsp.data.source import DataSource, OhlcvFrame
from aqsp.core.time import now_shanghai, is_market_open
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
    ) -> dict[str, OhlcvFrame]:
        intraday_data = self.source.fetch_intraday(symbols, period)
        result = {}

        for symbol, df in intraday_data.items():
            if df.empty:
                continue

            df = df.copy().sort_values("date").reset_index(drop=True)
            first_bar = df.iloc[0]
            last_bar = df.iloc[-1]

            daily = pd.DataFrame(
                {
                    "date": [now_shanghai().strftime("%Y-%m-%d")],
                    "symbol": [symbol],
                    "name": [df.get("name", symbol)],
                    "open": [float(first_bar["open"])],
                    "high": [float(df["high"].max())],
                    "low": [float(df["low"].min())],
                    "close": [float(last_bar["close"])],
                    "volume": [float(df["volume"].sum())],
                    "amount": [0.0],
                    "suspended": [False],
                    "limit_up": [0.0],
                    "limit_down": [0.0],
                    "adj_factor": [1.0],
                }
            )
            result[symbol] = daily

        if not result:
            raise MissingDataError(
                symbols[0], reason="所有标的分时数据缺失,无法合成日K"
            )
        return result

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
