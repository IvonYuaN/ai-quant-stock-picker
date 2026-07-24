from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
import time
import pandas as pd

from aqsp.data.source import DataSource, OhlcvFrame
from aqsp.core.errors import DataError, DataInconsistencyError
from aqsp.data.source_readiness import (
    WorkloadId,
    source_role_for_workload,
    workload_guard_message,
)


@dataclass(frozen=True)
class SourceFactory:
    name: str
    build: Callable[[], DataSource]


@dataclass(frozen=True)
class _PartialResult:
    source_name: str
    result: dict[str, object]


class MultiSource(DataSource):
    name: str = "multi"

    def __init__(
        self,
        primary: DataSource | SourceFactory,
        fallbacks: list[DataSource | SourceFactory],
        *,
        validate_consistency: bool = True,
        live_fetch_deadline_seconds: float = 30.0,
    ) -> None:
        self.primary = primary
        self.fallbacks = fallbacks
        self.validate_consistency = validate_consistency
        if live_fetch_deadline_seconds <= 0:
            raise ValueError("live_fetch_deadline_seconds 必须大于 0")
        self.live_fetch_deadline_seconds = float(live_fetch_deadline_seconds)
        self._last_used_source: str | None = None
        self._last_used_sources: dict[str, str] = {}
        self._active_workload: WorkloadId | None = None

    @property
    def last_used_source(self) -> str | None:
        return self._last_used_source

    @property
    def last_used_sources(self) -> dict[str, str]:
        """Return source provenance keyed by returned symbol."""
        return dict(self._last_used_sources)

    def set_workload(self, workload: WorkloadId | None) -> None:
        """Set the workload for the next fetch, including daily fetches."""
        self._active_workload = workload

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
            workload=self._active_workload,
        )

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        return self._with_live_short_fallback(
            lambda src, requested: src.fetch_intraday(requested, period),
            "fetch_intraday",
            expected_keys=symbols,
        )

    def _with_live_short_fallback(
        self,
        func,
        method_name: str,
        *,
        expected_keys: list[str],
    ) -> dict[str, object]:
        """Use sources by priority and ask fallbacks only for missing symbols."""
        self._clear_last_used()
        eligible: list[tuple[int, DataSource | SourceFactory, str]] = []
        exceptions: list[tuple[str, object]] = []
        for index, source_ref in enumerate([self.primary] + self.fallbacks):
            source_name = self._source_name(source_ref)
            guard_message = workload_guard_message(source_name, "live_short")
            if guard_message:
                exceptions.append((source_name, guard_message))
                continue
            if source_role_for_workload(source_name, "live_short") == "observation":
                exceptions.append((source_name, "candidate 来源仅可观察"))
                continue
            eligible.append((index, source_ref, source_name))

        if not eligible:
            raise DataError(
                f"所有数据源获取{method_name}失败: "
                + ", ".join(f"{name}: {error}" for name, error in exceptions)
            )

        requested = list(dict.fromkeys(str(key) for key in expected_keys if str(key)))
        accepted: dict[str, object] = {}
        pending = requested[:]
        deadline = time.monotonic() + self.live_fetch_deadline_seconds
        executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="aqsp-live-source"
        )
        try:
            for _, source_ref, source_name in eligible:
                if not pending:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                future = executor.submit(
                    self._fetch_live_source, source_ref, func, pending[:]
                )
                try:
                    result = future.result(timeout=remaining)
                except Exception as exc:
                    exceptions.append((source_name, exc))
                    continue
                if not result:
                    exceptions.append((source_name, "empty result"))
                    continue
                result = _annotate_result(result, source_name, "live_short")
                if not _has_realtime_provenance(result):
                    exceptions.append(
                        (source_name, "实时 workload 缺少逐标的 provenance")
                    )
                    continue
                for symbol in requested:
                    if symbol in result and symbol not in accepted:
                        accepted[symbol] = result[symbol]
                missing = [symbol for symbol in pending if symbol not in result]
                if missing:
                    exceptions.append(
                        (
                            source_name,
                            f"partial result missing {len(missing)}/{len(pending)}",
                        )
                    )
                pending = missing
            if not pending and accepted:
                merged_result = {symbol: accepted[symbol] for symbol in requested}
                self._set_last_used_provenance(merged_result, "multi")
                return merged_result
            raise DataError(
                f"所有数据源获取{method_name}失败: "
                + ", ".join(f"{name}: {str(error)[:80]}" for name, error in exceptions)
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _fetch_live_source(
        self,
        source_ref: DataSource | SourceFactory,
        func,
        symbols: list[str],
    ) -> dict[str, object]:
        source = self._materialize(source_ref)
        return self._call_source(
            source,
            lambda current: func(current, symbols),
            workload="live_short",
        )

    def fetch_index_intraday(
        self,
        index_codes: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        """Fetch index bars without routing index codes through stock APIs."""
        return self._with_live_short_fallback(
            lambda src, requested: _fetch_index_intraday_from_source(
                src, requested, period
            ),
            "fetch_index_intraday",
            expected_keys=index_codes,
        )

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        return self._with_quote_fallback(symbols)

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
            workload=self._active_workload,
        )

    def get_available_symbols(self) -> list[str]:
        exceptions = []
        for source_ref in [self.primary] + self.fallbacks:
            source_name = self._source_name(source_ref)
            if self._active_workload is not None:
                guard_message = workload_guard_message(
                    source_name, self._active_workload
                )
                if guard_message or (
                    self._active_workload == "live_short"
                    and source_role_for_workload(source_name, "live_short")
                    != "realtime"
                ):
                    exceptions.append(
                        (source_name, guard_message or "非实时源不能解析盘中股票池")
                    )
                    continue
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
            if self._active_workload is not None:
                guard_message = workload_guard_message(
                    source_name, self._active_workload
                )
                if guard_message or (
                    self._active_workload == "live_short"
                    and source_role_for_workload(source_name, "live_short")
                    != "realtime"
                ):
                    exceptions.append(
                        (source_name, guard_message or "非实时源不能解析盘中流动性池")
                    )
                    continue
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

    def _with_fallback(
        self,
        func,
        method_name: str,
        *,
        expected_keys: list[str],
        workload: WorkloadId | None = None,
    ):
        self._clear_last_used()
        effective_workload = workload or self._active_workload
        sources = [self.primary] + self.fallbacks

        primary_result = None
        fallback_result = None
        primary_source_name = ""
        fallback_source_name = ""
        partial_results: list[_PartialResult] = []
        exceptions = []

        for source_ref in sources:
            source_name = self._source_name(source_ref)
            if effective_workload is not None:
                guard_message = workload_guard_message(source_name, effective_workload)
                if guard_message:
                    exceptions.append((source_name, guard_message))
                    continue
                if (
                    effective_workload == "live_short"
                    and source_role_for_workload(source_name, effective_workload)
                    == "observation"
                ):
                    exceptions.append(
                        (source_name, "candidate 来源仅可观察，不能组成正式盘中信号")
                    )
                    continue
            try:
                source = self._materialize(source_ref)
                result = self._call_source(
                    source,
                    func,
                    workload=effective_workload,
                )
                if not result:
                    exceptions.append((source_name, "empty result"))
                    continue
                result = _annotate_result(result, source_name, effective_workload)
                if (
                    effective_workload == "live_short"
                    and not _has_verifiable_provenance(result)
                ):
                    exceptions.append(
                        (source_name, "实时 workload 缺少逐标的 provenance")
                    )
                    continue
                missing = _missing_requested_keys(result, expected_keys)
                if missing:
                    partial_results.append(
                        _PartialResult(source_name=source_name, result=result)
                    )
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
                    self._set_last_used_provenance(result, source_name)
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
                try:
                    self._validate_consistency(
                        primary_result,
                        fallback_result,
                        primary_source_name=primary_source_name,
                        fallback_source_name=fallback_source_name,
                    )
                except Exception:
                    self._clear_last_used()
                    raise
            return primary_result

        merged_result = _merge_partial_results(
            partial_results,
            expected_keys,
            allow_incomplete=effective_workload == "live_short",
        )
        if effective_workload == "live_short" and merged_result is not None:
            missing = _missing_requested_keys(merged_result, expected_keys)
            guarded_sources = [
                name for name, error in exceptions if "不适合 live_short" in str(error)
            ]
            if missing and guarded_sources and len(partial_results) <= 1:
                self._clear_last_used()
                raise DataError(
                    f"{guarded_sources[0]} 不适合 live_short；实时数据存在未覆盖标的，"
                    "且历史源不得补齐: " + ", ".join(missing[:20])
                )
        if merged_result is not None:
            self._set_last_used_provenance(merged_result, "multi")
            self._last_used_source = "multi"
            return merged_result

        self._clear_last_used()
        raise DataError(
            f"所有数据源获取{method_name}失败: {', '.join(f'{name}: {str(e)[:50]}' for name, e in exceptions)}"
        )

    def _clear_last_used(self) -> None:
        self._last_used_source = None
        self._last_used_sources = {}

    def _call_source(self, source: DataSource, func, *, workload: WorkloadId | None):
        setter = getattr(source, "set_workload", None)
        if workload is not None and callable(setter):
            setter(workload)
        try:
            return func(source)
        finally:
            if workload is not None and callable(setter):
                setter(None)

    def _set_last_used_provenance(
        self, result: dict[str, object], fallback_source_name: str
    ) -> None:
        provenance = _result_provenance(result, fallback_source_name)
        self._last_used_sources = provenance
        if provenance:
            sources = set(provenance.values())
            self._last_used_source = (
                next(iter(sources)) if len(sources) == 1 else "multi"
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

    def _with_quote_fallback(self, symbols: list[str]) -> dict[str, dict]:
        from aqsp.freshness import validate_realtime_quotes
        from aqsp.data.quote_metadata import LIVE_SHORT_MAX_FUTURE_SECONDS

        requested = tuple(
            dict.fromkeys(str(symbol) for symbol in symbols if str(symbol))
        )
        if not requested:
            raise DataError("未请求实时行情标的")

        self._clear_last_used()
        unresolved = set(requested)
        accepted: dict[str, dict] = {}
        used_sources: set[str] = set()
        exceptions: dict[str, list[str]] = {symbol: [] for symbol in requested}

        for source_ref in [self.primary] + self.fallbacks:
            if not unresolved:
                break
            source_name = self._source_name(source_ref)
            guard_message = workload_guard_message(source_name, "live_short")
            if guard_message:
                for symbol in unresolved:
                    _record_quote_error(
                        exceptions, symbol, source_name, DataError(guard_message)
                    )
                continue
            pending = [symbol for symbol in requested if symbol in unresolved]
            try:
                source = self._materialize(source_ref)
            except Exception as exc:
                for symbol in pending:
                    _record_quote_error(exceptions, symbol, source_name, exc)
                continue

            setter = getattr(source, "set_workload", None)
            if callable(setter):
                setter("live_short")
            batch_result: dict[str, dict] = {}
            try:
                raw_result = source.fetch_realtime_quote(pending)
                batch_result = {
                    str(symbol): quote
                    for symbol, quote in raw_result.items()
                    if str(symbol) in unresolved and isinstance(quote, dict)
                }
            except Exception as exc:
                for symbol in pending:
                    _record_quote_error(exceptions, symbol, source_name, exc)

            try:
                for symbol, quote in batch_result.items():
                    try:
                        quote = _annotate_quote(quote, source_name)
                        validate_realtime_quotes(
                            {symbol: quote},
                            require_vendor_timestamp=True,
                            max_future_seconds=LIVE_SHORT_MAX_FUTURE_SECONDS,
                        )
                    except Exception as exc:
                        _record_quote_error(exceptions, symbol, source_name, exc)
                        continue
                    accepted[symbol] = quote
                    unresolved.discard(symbol)
                    used_sources.add(str(quote["source_name"]))

                # A bad symbol may make a source reject its whole batch. Retry only
                # unresolved symbols so one malformed quote cannot discard good ones.
                for symbol in tuple(unresolved):
                    try:
                        single_result = source.fetch_realtime_quote([symbol])
                        quote = single_result.get(symbol)
                        if not isinstance(quote, dict):
                            raise DataError(f"{symbol} 返回空实时行情")
                        quote = _annotate_quote(quote, source_name)
                        validate_realtime_quotes(
                            {symbol: quote},
                            require_vendor_timestamp=True,
                            max_future_seconds=LIVE_SHORT_MAX_FUTURE_SECONDS,
                        )
                    except Exception as exc:
                        _record_quote_error(exceptions, symbol, source_name, exc)
                        continue
                    accepted[symbol] = quote
                    unresolved.discard(symbol)
                    used_sources.add(str(quote["source_name"]))
            finally:
                if callable(setter):
                    setter(None)

        if accepted:
            self._last_used_sources = {
                symbol: str(quote["source_name"]) for symbol, quote in accepted.items()
            }
            self._last_used_source = (
                next(iter(used_sources)) if len(used_sources) == 1 else "multi"
            )
            return {
                symbol: accepted[symbol] for symbol in requested if symbol in accepted
            }

        details = "; ".join(
            f"{symbol}: {', '.join(errors[:3]) or '无有效结果'}"
            for symbol, errors in exceptions.items()
        )
        self._clear_last_used()
        raise DataError("所有数据源获取fetch_realtime_quote失败: " + details)


def _missing_requested_keys(
    result: dict[str, object], expected_keys: list[str]
) -> list[str]:
    requested = [str(key) for key in expected_keys if str(key)]
    if not requested:
        return []
    present = {str(key) for key in result}
    return [key for key in requested if key not in present]


def _record_quote_error(
    errors: dict[str, list[str]],
    symbol: str,
    source_name: str,
    error: Exception,
) -> None:
    errors.setdefault(symbol, []).append(f"{source_name}: {str(error)[:120]}")


def _merge_partial_results(
    partial_results: list[_PartialResult],
    expected_keys: list[str],
    *,
    allow_incomplete: bool = False,
) -> dict[str, object] | None:
    requested = [str(key) for key in expected_keys if str(key)]
    if not requested:
        return None
    merged: dict[str, object] = {}
    for partial in partial_results:
        for key in requested:
            if key in merged:
                continue
            if key in partial.result:
                merged[key] = partial.result[key]
    if not allow_incomplete and _missing_requested_keys(merged, requested):
        return None
    return merged or None


def _annotate_result(
    result: dict[str, object],
    source_name: str,
    workload: WorkloadId | None,
) -> dict[str, object]:
    annotated: dict[str, object] = {}
    for key, value in result.items():
        if not isinstance(value, pd.DataFrame):
            annotated[str(key)] = value
            continue
        frame = value.copy()
        existing = str(frame.attrs.get("source_name", "")).strip()
        if not existing and (
            workload is None
            or source_role_for_workload(source_name, workload) is not None
        ):
            frame.attrs["source_name"] = source_name
        annotated[str(key)] = frame
    return annotated


def _has_verifiable_provenance(result: dict[str, object]) -> bool:
    return all(
        not isinstance(value, pd.DataFrame)
        or bool(str(value.attrs.get("source_name", "")).strip())
        for value in result.values()
    )


def _has_realtime_provenance(result: dict[str, object]) -> bool:
    """Require both traceability and an accepted live_short source role."""
    for value in result.values():
        if not isinstance(value, pd.DataFrame):
            continue
        source_name = str(value.attrs.get("source_name", "")).strip()
        if (
            not source_name
            or source_role_for_workload(source_name, "live_short") != "realtime"
        ):
            return False
    return True


def _result_provenance(
    result: dict[str, object], fallback_source_name: str
) -> dict[str, str]:
    provenance: dict[str, str] = {}
    for key, value in result.items():
        source_name = ""
        if isinstance(value, pd.DataFrame):
            source_name = str(value.attrs.get("source_name", "")).strip()
        if not source_name and fallback_source_name != "multi":
            source_name = fallback_source_name
        if source_name:
            provenance[str(key)] = source_name
    return provenance


def _fetch_index_intraday_from_source(
    source: DataSource,
    index_codes: list[str],
    period: Literal["1", "5", "15", "30", "60"],
) -> dict[str, OhlcvFrame]:
    method = getattr(source, "fetch_index_intraday", None)
    if not callable(method):
        raise DataError(f"数据源 {source.name} 不支持指数分时接口")
    return method(index_codes, period)


def _annotate_quote(quote: dict, source_name: str) -> dict:
    annotated = dict(quote)
    existing = str(annotated.get("source_name", "")).strip()
    actual_source = existing or source_name
    if source_role_for_workload(actual_source, "live_short") != "realtime":
        raise DataError(f"实时行情来源角色无法验证: {actual_source or 'unknown'}")
    annotated["source_name"] = actual_source
    return annotated
