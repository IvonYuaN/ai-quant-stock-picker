from __future__ import annotations

import pandas as pd
from datetime import date

from aqsp.core.errors import DataError
from aqsp.data.cache import DataCache
from aqsp.data.source_readiness import (
    WorkloadId,
    source_role_for_workload,
    workload_guard_message,
)


def fetch_frames_for_cli_with_metadata(
    source_name: str,
    symbols: list[str],
    *,
    benchmark_symbol: str | None,
    cache_path: str | None = None,
    days: int = 260,
    end_date: date | None = None,
    workload: WorkloadId | None = None,
    get_source_fn,
    fetch_with_source_fn,
    record_source_success_fn,
    record_source_failure_fn,
) -> tuple[dict[str, pd.DataFrame], str]:
    try:
        requested_guard = _workload_guard(source_name, workload)
        if requested_guard:
            raise DataError(requested_guard)
        cache = DataCache(db_path=cache_path) if cache_path else None
        source = _get_source_with_optional_cache(
            get_source_fn,
            source_name,
            cache=cache,
        )
        workload_setter = getattr(source, "set_workload", None)
        if workload is not None and callable(workload_setter):
            workload_setter(workload)
        try:
            frames = fetch_with_source_fn(
                source,
                symbols,
                days=days,
                benchmark_symbol=benchmark_symbol,
                end_date=end_date,
            )
            actual_source = str(
                getattr(source, "last_used_source", None) or source.name
            )
            actual_guard = _workload_guard(actual_source, workload)
            if actual_guard:
                if actual_source != source_name:
                    raise DataError(
                        f"请求源 {source_name} 实际落到 {actual_source}；{actual_guard}"
                    )
                raise DataError(actual_guard)
            _validate_workload_provenance(
                source,
                frames,
                actual_source=actual_source,
                workload=workload,
            )
            record_source_success_fn(source_name, actual_source)
            return frames, actual_source
        finally:
            if workload is not None and callable(workload_setter):
                workload_setter(None)
    except DataError as exc:
        record_source_failure_fn(source_name, str(exc))
        raise
    except Exception as exc:
        record_source_failure_fn(source_name, str(exc))
        raise DataError(f"数据源 {source_name} 获取失败: {exc}") from exc


def _get_source_with_optional_cache(get_source_fn, source_name: str, *, cache):
    import inspect

    try:
        signature = inspect.signature(get_source_fn)
    except (TypeError, ValueError):
        return get_source_fn(source_name, cache=cache)
    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        or (
            parameter.name == "cache"
            and parameter.kind
            in {
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
        )
        for parameter in signature.parameters.values()
    ):
        return get_source_fn(source_name, cache=cache)
    return get_source_fn(source_name)


def _workload_guard(source_name: str, workload: WorkloadId | None) -> str:
    if workload is None:
        return ""
    return workload_guard_message(source_name, workload)


def _validate_workload_provenance(
    source,
    frames: dict[str, pd.DataFrame],
    *,
    actual_source: str,
    workload: WorkloadId | None,
) -> None:
    if workload != "live_short":
        return

    source_provenance = getattr(source, "last_used_sources", {})
    for symbol, frame in frames.items():
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        provenance = str(frame.attrs.get("source_name", "")).strip()
        if not provenance and isinstance(source_provenance, dict):
            provenance = str(source_provenance.get(str(symbol), "")).strip()
        if not provenance and actual_source != "multi":
            provenance = actual_source
        if not provenance:
            raise DataError(
                f"实时 workload 标的 {symbol} 缺少可验证 provenance，拒绝继续"
            )
        role = source_role_for_workload(provenance, workload)
        if role != "realtime":
            raise DataError(
                f"实时 workload 标的 {symbol} 来源 {provenance} 角色不可接受"
            )
