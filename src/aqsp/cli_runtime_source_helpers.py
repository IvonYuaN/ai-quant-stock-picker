"""Runtime data-source helpers extracted from ``cli.py``.

Contains source construction (sqlite / generic), CLI-level frame fetching
with provenance metadata, benchmark frame removal, and run-symbol resolution.

All symbols are re-exported by ``cli.py`` for backward compatibility.
Tests monkeypatch these names on ``aqsp.cli`` (the re-export site); internal
calls between these functions resolve via *this* module's globals, which is
safe because no test creates a scenario where one is patched on ``cli`` while
another is reached through the internal call chain.
"""

from __future__ import annotations

import inspect
from datetime import date

import pandas as pd

from aqsp.data import fetch_frames_for_cli_with_metadata, fetch_with_source
from aqsp.data.cache import DataCache
from aqsp.data.source_factory import build_data_source, build_sqlite_db_source
from aqsp.data.source_health import record_source_failure, record_source_success
from aqsp.data.source_readiness import WorkloadId
from aqsp.universe import DEFAULT_SYMBOLS
from aqsp.universe.runtime import resolve_run_symbols as resolve_runtime_run_symbols


def _build_sqlite_db_source(*, cache: DataCache | None):
    return build_sqlite_db_source(cache=cache)


def _get_source(source_name: str, *, cache: DataCache | None = None):
    return build_data_source(source_name, cache=cache or DataCache())


def _get_source_optional_cache(source_name: str, *, cache: DataCache | None = None):
    if cache is None:
        return _get_source(source_name)
    try:
        signature = inspect.signature(_get_source)
    except (TypeError, ValueError):
        signature = None
    if signature is not None and not any(
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
        return _get_source(source_name)
    try:
        return _get_source(source_name, cache=cache)
    except TypeError as exc:
        if "cache" not in str(exc):
            raise
        return _get_source(source_name)


def _fetch_frames_for_cli(
    source_name: str,
    symbols: list[str],
    *,
    benchmark_symbol: str | None,
    cache_path: str | None = None,
    days: int = 260,
    end_date: date | None = None,
    workload: WorkloadId | None = None,
) -> dict[str, pd.DataFrame]:
    frames, _actual_source = _fetch_frames_for_cli_with_metadata(
        source_name,
        symbols,
        benchmark_symbol=benchmark_symbol,
        cache_path=cache_path,
        days=days,
        end_date=end_date,
        workload=workload,
    )
    return frames


def _fetch_frames_for_cli_with_metadata(
    source_name: str,
    symbols: list[str],
    *,
    benchmark_symbol: str | None,
    cache_path: str | None = None,
    days: int = 260,
    end_date: date | None = None,
    workload: WorkloadId | None = None,
) -> tuple[dict[str, pd.DataFrame], str]:
    return fetch_frames_for_cli_with_metadata(
        source_name,
        symbols,
        benchmark_symbol=benchmark_symbol,
        cache_path=cache_path,
        days=days,
        end_date=end_date,
        workload=workload,
        get_source_fn=_get_source,
        fetch_with_source_fn=fetch_with_source,
        record_source_success_fn=record_source_success,
        record_source_failure_fn=record_source_failure,
    )


def _drop_benchmark_frame(
    frames: dict[str, pd.DataFrame],
    benchmark_symbol: str | None,
) -> dict[str, pd.DataFrame]:
    if not benchmark_symbol:
        return frames
    return {symbol: df for symbol, df in frames.items() if symbol != benchmark_symbol}


def _resolve_run_symbols(
    source_name: str,
    explicit_symbols: str,
    *,
    pool_name: str = "",
    as_of: date | None = None,
    max_universe: int,
    min_avg_amount: float,
) -> list[str]:
    return resolve_runtime_run_symbols(
        source_name,
        explicit_symbols,
        get_source_fn=_get_source,
        default_symbols=DEFAULT_SYMBOLS,
        pool_name=pool_name,
        as_of=as_of,
        max_universe=max_universe,
        min_avg_amount=min_avg_amount,
    )
