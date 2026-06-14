from __future__ import annotations

from datetime import date
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
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
        self._last_used_sources: dict[str, str] = {}

    @property
    def last_used_source(self) -> str | None:
        return self._last_used_source

    @property
    def last_used_sources(self) -> dict[str, str]:
        return dict(self._last_used_sources)

    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
    ) -> dict[str, OhlcvFrame]:
        if symbols:
            return self._with_symbol_fallback(
                symbols,
                lambda src, requested: src.fetch_daily(requested, start, end, adjust),
                "fetch_daily",
            )
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

    def _with_symbol_fallback(
        self,
        symbols: list[str],
        func: Callable[[DataSource, list[str]], dict[str, Any]],
        method_name: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        self._last_used_sources = {}
        exceptions = []
        consistency_checked = False

        for source_ref in [self.primary] + self.fallbacks:
            missing_symbols = [symbol for symbol in symbols if symbol not in result]
            needs_consistency_probe = (
                self.validate_consistency and bool(result) and not consistency_checked
            )
            if not missing_symbols and not needs_consistency_probe:
                break

            source_name = self._source_name(source_ref)
            try:
                source = self._materialize(source_ref)
                requested_symbols = (
                    symbols if needs_consistency_probe else missing_symbols
                )
                source_result = func(source, requested_symbols)
                if not source_result:
                    exceptions.append((source_name, "empty result"))
                    continue

                usable_result = {
                    symbol: frame
                    for symbol, frame in source_result.items()
                    if symbol in requested_symbols and not self._is_empty_frame(frame)
                }
                if not usable_result:
                    exceptions.append((source_name, "empty result"))
                    continue

                if needs_consistency_probe:
                    consistency_checked = self._validate_consistency(
                        result,
                        usable_result,
                        fallback_source_name=source_name,
                    )

                missing_data = {
                    symbol: frame
                    for symbol, frame in usable_result.items()
                    if symbol in missing_symbols
                }
                if missing_data:
                    result.update(missing_data)
                    for symbol in missing_data:
                        self._last_used_sources[symbol] = source_name
                    self._last_used_source = source_name
            except DataInconsistencyError:
                raise
            except Exception as exc:
                exceptions.append((source_name, exc))

        if result:
            missing_symbols = [symbol for symbol in symbols if symbol not in result]
            if missing_symbols:
                raise DataError(
                    f"部分标的获取{method_name}失败: missing={missing_symbols}; "
                    + ", ".join(f"{name}: {str(exc)[:50]}" for name, exc in exceptions)
                )
            return result

        raise DataError(
            f"所有数据源获取{method_name}失败: "
            + ", ".join(f"{name}: {str(exc)[:50]}" for name, exc in exceptions)
        )

    def _with_fallback(self, func, method_name: str):
        self._last_used_sources = {}
        sources = [self.primary] + self.fallbacks

        primary_result = None
        fallback_result = None
        exceptions = []

        for source_ref in sources:
            source_name = self._source_name(source_ref)
            try:
                source = self._materialize(source_ref)
                result = func(source)
                if not result:
                    exceptions.append((source_name, "empty result"))
                    continue
                if primary_result is None:
                    primary_result = result
                    self._last_used_source = source_name
                    self._last_used_sources = self._source_result_map(
                        source_name, result
                    )
                    if not self.validate_consistency:
                        return primary_result
                elif fallback_result is None:
                    fallback_result = result
                    break
            except Exception as e:
                exceptions.append((source_name, e))

        if primary_result is not None:
            if self.validate_consistency and fallback_result is not None:
                self._validate_consistency(primary_result, fallback_result)
            return primary_result

        raise DataError(
            f"所有数据源获取{method_name}失败: {', '.join(f'{name}: {str(e)[:50]}' for name, e in exceptions)}"
        )

    def _validate_consistency(
        self,
        primary_data: dict[str, pd.DataFrame],
        fallback_data: dict[str, pd.DataFrame],
        *,
        fallback_source_name: str | None = None,
    ) -> bool:
        common_symbols = set(primary_data.keys()) & set(fallback_data.keys())
        if not common_symbols:
            return False

        for symbol in common_symbols:
            primary_df = primary_data[symbol]
            fallback_df = fallback_data[symbol]

            if primary_df.empty or fallback_df.empty:
                continue

            primary_latest = primary_df.iloc[-1]
            fallback_latest = fallback_df.iloc[-1]

            for column in ("open", "close"):
                if (
                    column not in primary_df.columns
                    or column not in fallback_df.columns
                ):
                    continue
                primary_value = float(primary_latest[column])
                fallback_value = float(fallback_latest[column])

                if primary_value <= 0 or fallback_value <= 0:
                    continue

                diff_pct = abs(primary_value - fallback_value) / primary_value * 100
                if diff_pct > 0.5:
                    raise DataInconsistencyError(
                        symbol,
                        self._source_name(self.primary),
                        fallback_source_name or self._first_fallback_name(),
                        diff_pct,
                    )
        return True

    def _first_fallback_name(self) -> str:
        return self._source_name(self.fallbacks[0]) if self.fallbacks else "unknown"

    def _source_result_map(
        self,
        source_name: str,
        result: dict[str, Any],
    ) -> dict[str, str]:
        return {symbol: source_name for symbol in result}

    def _is_empty_frame(self, value: Any) -> bool:
        return isinstance(value, pd.DataFrame) and value.empty
