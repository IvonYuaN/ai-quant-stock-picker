from __future__ import annotations

from datetime import date
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
import pandas as pd

from aqsp.data.source import DataSource, OhlcvFrame
from aqsp.core.errors import DataError, DataInconsistencyError


@dataclass(frozen=True)
class SourceFactory:
    name: str
    build: Callable[[], DataSource]


class MultiSource(DataSource):
    name: str = "multi"

    def __init__(
        self,
        primary: DataSource | SourceFactory,
        fallbacks: list[DataSource | SourceFactory],
        *,
        validate_consistency: bool = True,
    ) -> None:
        self.primary = primary
        self.fallbacks = fallbacks
        self.validate_consistency = validate_consistency
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
            expected_keys=symbols,
        )

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        return self._with_fallback(
            lambda src: src.fetch_intraday(symbols, period),
            "fetch_intraday",
            expected_keys=symbols,
        )

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        return self._with_fallback(
            lambda src: src.fetch_realtime_quote(symbols),
            "fetch_realtime_quote",
            expected_keys=symbols,
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
            expected_keys=index_codes,
        )

    def get_available_symbols(self) -> list[str]:
        exceptions = []
        for source_ref in [self.primary] + self.fallbacks:
            source_name = self._source_name(source_ref)
            try:
                source = self._materialize(source_ref)
                method = getattr(source, "get_available_symbols", None)
                if method is None:
                    exceptions.append((source_name, "not supported"))
                    continue
                symbols = method()
                if symbols:
                    self._last_used_source = source_name
                    return list(symbols)
                exceptions.append((source_name, "empty result"))
            except Exception as exc:
                exceptions.append((source_name, exc))
        raise DataError(
            "所有数据源获取可用标的失败: "
            + ", ".join(f"{name}: {str(exc)[:50]}" for name, exc in exceptions)
        )

    def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
        exceptions = []
        for source_ref in [self.primary] + self.fallbacks:
            source_name = self._source_name(source_ref)
            try:
                source = self._materialize(source_ref)
                method = getattr(source, "get_liquid_symbols", None)
                if method is None:
                    exceptions.append((source_name, "not supported"))
                    continue
                symbols = method(limit=limit, min_amount=min_amount)
                if symbols:
                    self._last_used_source = source_name
                    return list(symbols)
                exceptions.append((source_name, "empty result"))
            except Exception as exc:
                exceptions.append((source_name, exc))
        raise DataError(
            "所有数据源获取高流动性标的失败: "
            + ", ".join(f"{name}: {str(exc)[:50]}" for name, exc in exceptions)
        )

    def _source_name(self, source: DataSource | SourceFactory) -> str:
        return source.name

    def _materialize(self, source: DataSource | SourceFactory) -> DataSource:
        if isinstance(source, SourceFactory):
            return source.build()
        return source

    def _with_fallback(self, func, method_name: str, *, expected_keys: list[str]):
        sources = [self.primary] + self.fallbacks

        primary_result = None
        fallback_result = None
        primary_source_name = ""
        fallback_source_name = ""
        exceptions = []

        for source_ref in sources:
            source_name = self._source_name(source_ref)
            try:
                source = self._materialize(source_ref)
                result = func(source)
                if not result:
                    exceptions.append((source_name, "empty result"))
                    continue
                missing = _missing_requested_keys(result, expected_keys)
                if missing:
                    exceptions.append(
                        (
                            source_name,
                            f"partial result missing {len(missing)}/{len(expected_keys)}: "
                            + ",".join(missing[:5]),
                        )
                    )
                    continue
                if primary_result is None:
                    primary_result = result
                    primary_source_name = source_name
                    self._last_used_source = source_name
                    if not self.validate_consistency:
                        return primary_result
                elif fallback_result is None:
                    fallback_result = result
                    fallback_source_name = source_name
                    break
            except Exception as e:
                exceptions.append((source_name, e))

        if primary_result is not None:
            if self.validate_consistency and fallback_result is not None:
                self._validate_consistency(
                    primary_result,
                    fallback_result,
                    primary_source_name=primary_source_name,
                    fallback_source_name=fallback_source_name,
                )
            return primary_result

        raise DataError(
            f"所有数据源获取{method_name}失败: {', '.join(f'{name}: {str(e)[:50]}' for name, e in exceptions)}"
        )

    def _validate_consistency(
        self,
        primary_data: dict[str, pd.DataFrame],
        fallback_data: dict[str, pd.DataFrame],
        *,
        primary_source_name: str | None = None,
        fallback_source_name: str | None = None,
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
                            primary_source_name or self._source_name(self.primary),
                            fallback_source_name
                            or (
                                self._source_name(self.fallbacks[0])
                                if self.fallbacks
                                else "unknown"
                            ),
                            diff_pct,
                        )


def _missing_requested_keys(result: dict[str, object], expected_keys: list[str]) -> list[str]:
    requested = [str(key) for key in expected_keys if str(key)]
    if not requested:
        return []
    present = {str(key) for key in result}
    return [key for key in requested if key not in present]
