from __future__ import annotations

from datetime import date
from typing import Literal
import pandas as pd

from aqsp.data.source import DataSource, OhlcvFrame
from aqsp.core.errors import DataError, DataInconsistencyError


class MultiSource(DataSource):
    name: str = "multi"

    def __init__(self, primary: DataSource, fallbacks: list[DataSource]) -> None:
        self.primary = primary
        self.fallbacks = fallbacks
        self._last_used_source: str | None = None

    @property
    def last_used_source(self) -> str | None:
        return self._last_used_source

    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
    ) -> dict[str, OhlcvFrame]:
        return self._with_fallback(
            lambda src: src.fetch_daily(symbols, start, end, adjust),
            "fetch_daily",
        )

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        return self._with_fallback(
            lambda src: src.fetch_intraday(symbols, period),
            "fetch_intraday",
        )

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        return self._with_fallback(
            lambda src: src.fetch_realtime_quote(symbols),
            "fetch_realtime_quote",
        )

    def fetch_index(
        self,
        index_codes: list[str],
        start: date,
        end: date,
    ) -> dict[str, OhlcvFrame]:
        return self._with_fallback(
            lambda src: src.fetch_index(index_codes, start, end),
            "fetch_index",
        )

    def _with_fallback(self, func, method_name: str):
        sources = [self.primary] + self.fallbacks

        primary_result = None
        fallback_result = None
        exceptions = []

        for source in sources:
            try:
                result = func(source)
                if not result:
                    continue
                if primary_result is None:
                    primary_result = result
                    self._last_used_source = source.name
                elif fallback_result is None:
                    fallback_result = result
                    break
            except Exception as e:
                exceptions.append((source.name, e))

        if primary_result is not None:
            if fallback_result is not None:
                self._validate_consistency(primary_result, fallback_result)
            return primary_result

        raise DataError(
            f"所有数据源获取{method_name}失败: {', '.join(f'{name}: {str(e)[:50]}' for name, e in exceptions)}"
        )

    def _validate_consistency(
        self,
        primary_data: dict[str, pd.DataFrame],
        fallback_data: dict[str, pd.DataFrame],
    ) -> None:
        common_symbols = set(primary_data.keys()) & set(fallback_data.keys())

        for symbol in common_symbols:
            primary_df = primary_data[symbol]
            fallback_df = fallback_data[symbol]

            if primary_df.empty or fallback_df.empty:
                continue

            primary_latest = primary_df.iloc[-1]
            fallback_latest = fallback_df.iloc[-1]

            if "close" in primary_df.columns and "close" in fallback_df.columns:
                p_close = float(primary_latest["close"])
                f_close = float(fallback_latest["close"])

                if p_close > 0 and f_close > 0:
                    diff_pct = abs(p_close - f_close) / p_close * 100
                    if diff_pct > 0.5:
                        raise DataInconsistencyError(
                            symbol,
                            self.primary.name,
                            self.fallbacks[0].name if self.fallbacks else "unknown",
                            diff_pct,
                        )
