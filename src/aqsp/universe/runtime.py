from __future__ import annotations

import csv
import json
import os
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from aqsp.core.errors import DataError
from aqsp.core.time import today_shanghai

_DEFAULT_FETCH_HISTORY_DAYS = 260
_DEFAULT_FETCH_LOOKBACK_DAYS = max(_DEFAULT_FETCH_HISTORY_DAYS * 2, 365)
_LIVE_LIQUIDITY_SOURCES = frozenset(
    {"online_first", "eastmoney", "akshare", "sina", "tencent", "multi"}
)
_HIGH_FREQUENCY_TASKS = frozenset({"intraday", "midday", "live_short"})


def _requires_liquid_universe(source_name: str, min_avg_amount: float) -> bool:
    return source_name in _LIVE_LIQUIDITY_SOURCES and float(min_avg_amount or 0.0) > 0


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_high_frequency_task() -> bool:
    return os.getenv("AQSP_RUN_TASK_ID", "").strip().lower() in _HIGH_FREQUENCY_TASKS


def _fast_symbol_cache_disabled() -> bool:
    return _is_truthy(os.getenv("AQSP_INTRADAY_DISABLE_FAST_SYMBOL_CACHE", ""))


def _intraday_fast_fill_cache_enabled() -> bool:
    return _is_truthy(os.getenv("AQSP_INTRADAY_FAST_FILL_CACHE", ""))


def _filter_symbols_with_daily_coverage(
    source: object,
    symbols: list[str],
    *,
    target_day: date,
    strict: bool = False,
) -> list[str]:
    if not symbols or not hasattr(source, "get_symbols_with_daily_coverage"):
        return symbols
    start = target_day - timedelta(days=_DEFAULT_FETCH_LOOKBACK_DAYS)
    try:
        covered = source.get_symbols_with_daily_coverage(
            symbols,
            start,
            target_day,
            min_rows=None,
        )
    except DataError:
        if strict:
            raise
        return symbols
    if covered:
        return list(covered)
    if strict:
        raise DataError("sqlite_db 日线覆盖过滤后无可用标的")
    return symbols


def _resolve_symbols_from_source(
    source_name: str,
    source: object,
    *,
    target_day: date,
    max_universe: int,
    min_avg_amount: float,
) -> list[str]:
    needs_live_liquidity = _requires_liquid_universe(source_name, min_avg_amount)
    if hasattr(source, "get_liquid_symbols"):
        try:
            # 收盘主链需要完整的流动性梯队再做确定性抽样；盘中任务沿用
            # bounded limit，避免把一次实时刷新变成全市场扫描。
            source_limit = max_universe if _is_high_frequency_task() else 0
            liquid_symbols = source.get_liquid_symbols(
                limit=source_limit,
                min_amount=min_avg_amount,
            )
        except DataError:
            liquid_symbols = []
        if liquid_symbols:
            covered = _filter_symbols_with_daily_coverage(
                source,
                list(liquid_symbols),
                target_day=target_day,
                strict=source_name == "sqlite_db",
            )
            return _limit_resolved_symbols(
                covered,
                max_universe=max_universe,
                stratified=needs_live_liquidity,
            )
    if needs_live_liquidity:
        return []
    if hasattr(source, "get_available_symbols"):
        try:
            available = source.get_available_symbols()
        except DataError:
            available = []
        if available:
            covered = _filter_symbols_with_daily_coverage(
                source,
                list(available),
                target_day=target_day,
                strict=source_name == "sqlite_db",
            )
            return _limit_resolved_symbols(
                covered,
                max_universe=max_universe,
                stratified=False,
            )
    return []


def _limit_resolved_symbols(
    symbols: list[str],
    *,
    max_universe: int,
    stratified: bool,
) -> list[str]:
    if max_universe <= 0 or len(symbols) <= max_universe:
        return list(symbols)
    if not stratified:
        return list(symbols[:max_universe])
    return _stratified_symbol_sample(symbols, max_universe=max_universe)


def _stratified_symbol_sample(symbols: list[str], *, max_universe: int) -> list[str]:
    """Pick a deterministic spread from a liquidity-ranked list.

    Live quote sources return liquid symbols sorted by turnover. Taking the head turns
    the runtime universe into a large-cap proxy, so sample across the full eligible
    liquidity ladder while preserving source order inside the selected points.
    """
    if max_universe <= 0 or len(symbols) <= max_universe:
        return list(symbols)
    if max_universe == 1:
        return [symbols[0]]
    last_index = len(symbols) - 1
    selected: list[str] = []
    seen: set[str] = set()
    for slot in range(max_universe):
        index = round(slot * last_index / (max_universe - 1))
        symbol = symbols[index]
        if symbol in seen:
            continue
        seen.add(symbol)
        selected.append(symbol)
    return selected


def _load_cached_symbol_pool_from_path(
    raw_path: str,
    *,
    max_universe: int,
) -> list[str]:
    raw_path = raw_path.strip()
    if not raw_path:
        return []
    path = Path(raw_path).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    raw_symbols = payload.get("covered_symbols") or payload.get("symbols") or []
    if not isinstance(raw_symbols, list):
        return []
    symbols: list[str] = []
    seen: set[str] = set()
    for item in raw_symbols:
        symbol = str(item).strip()
        if not symbol or symbol in seen or not symbol.isdigit() or len(symbol) != 6:
            continue
        seen.add(symbol)
        symbols.append(symbol)
        if max_universe > 0 and len(symbols) >= max_universe:
            break
    return symbols


def _load_cached_symbol_pool(*, max_universe: int) -> list[str]:
    raw_path = os.getenv(
        "AQSP_RUNTIME_SYMBOL_CACHE",
        "data/walkforward_production_symbols.json",
    )
    return _load_cached_symbol_pool_from_path(raw_path, max_universe=max_universe)


def _append_unique_symbols(
    target: list[str],
    symbols: list[str],
    *,
    max_universe: int,
) -> list[str]:
    seen = set(target)
    for symbol in symbols:
        normalized = str(symbol).strip()
        if (
            not normalized
            or normalized in seen
            or not normalized.isdigit()
            or len(normalized) != 6
        ):
            continue
        seen.add(normalized)
        target.append(normalized)
        if max_universe > 0 and len(target) >= max_universe:
            break
    return target


def _load_symbol_pool_from_candidate_csv(
    raw_path: str,
) -> list[dict[str, str]]:
    path = Path(raw_path.strip()).expanduser()
    if not raw_path.strip():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def _candidate_score(row: dict[str, str]) -> float:
    try:
        return float(row.get("score", "") or 0.0)
    except ValueError:
        return 0.0


def _load_symbol_pool_from_candidate_csvs(
    raw_csvs: str,
    *,
    max_universe: int,
) -> list[str]:
    ranked_rows: list[tuple[int, int, float, int, str]] = []
    for path_index, raw_path in enumerate(item.strip() for item in raw_csvs.split(",")):
        if not raw_path:
            continue
        rows = _load_symbol_pool_from_candidate_csv(raw_path)
        for row_index, row in enumerate(rows):
            symbol = str(row.get("symbol", "") or "").strip()
            rating = str(row.get("rating", "") or "").strip().lower()
            avoid_rank = 1 if rating == "avoid" else 0
            ranked_rows.append(
                (path_index, avoid_rank, -_candidate_score(row), row_index, symbol)
            )
    ranked_rows.sort()
    symbols = [item[-1] for item in ranked_rows]
    return _append_unique_symbols([], symbols, max_universe=max_universe)


def _load_intraday_fast_symbol_pool(*, max_universe: int) -> list[str]:
    symbols: list[str] = []
    raw_csvs = os.getenv(
        "AQSP_INTRADAY_FAST_SYMBOL_CSVS",
        "reports/latest.csv",
    )
    csv_symbols = _load_symbol_pool_from_candidate_csvs(
        raw_csvs,
        max_universe=max_universe,
    )
    _append_unique_symbols(symbols, csv_symbols, max_universe=max_universe)
    if symbols and not _intraday_fast_fill_cache_enabled():
        return symbols
    if max_universe > 0 and len(symbols) >= max_universe:
        return symbols

    raw_path = os.getenv("AQSP_INTRADAY_FAST_SYMBOL_CACHE", "").strip()
    if not raw_path:
        raw_path = os.getenv(
            "AQSP_RUNTIME_SYMBOL_CACHE",
            "data/walkforward_production_symbols.json",
        )
    cache_symbols = _load_cached_symbol_pool_from_path(
        raw_path,
        max_universe=max_universe,
    )
    return _append_unique_symbols(symbols, cache_symbols, max_universe=max_universe)


def resolve_run_symbols(
    source_name: str,
    explicit_symbols: str,
    *,
    get_source_fn: Callable[[str], object],
    default_symbols: tuple[str, ...],
    pool_name: str = "",
    as_of: date | None = None,
    max_universe: int,
    min_avg_amount: float,
) -> list[str]:
    target_day = as_of or today_shanghai()
    symbols = [item.strip() for item in explicit_symbols.split(",") if item.strip()]
    if symbols:
        if source_name != "sqlite_db":
            return symbols
        source = get_source_fn(source_name)
        return _filter_symbols_with_daily_coverage(
            source,
            symbols,
            target_day=target_day,
            strict=True,
        )
    if pool_name and pool_name != "all":
        from aqsp.universe.pool import UniversePool

        pool = UniversePool.from_default(pool_name)
        return pool.get_symbols(as_of=target_day)
    source = None
    try:
        source = get_source_fn(source_name)
    except DataError:
        source = None
    if source is not None:
        resolved = _resolve_symbols_from_source(
            source_name,
            source,
            target_day=target_day,
            max_universe=max_universe,
            min_avg_amount=min_avg_amount,
        )
        if resolved:
            return resolved
    if (
        source_name != "sqlite_db"
        and _is_high_frequency_task()
        and _requires_liquid_universe(source_name, min_avg_amount)
        and not _fast_symbol_cache_disabled()
    ):
        cached_symbols = _load_intraday_fast_symbol_pool(max_universe=max_universe)
        if cached_symbols:
            return cached_symbols

    if source_name != "sqlite_db":
        if not _requires_liquid_universe(source_name, min_avg_amount):
            cached_symbols = _load_cached_symbol_pool(max_universe=max_universe)
            if cached_symbols:
                return cached_symbols
        try:
            sqlite_source = get_source_fn("sqlite_db")
        except DataError:
            sqlite_source = None
        if sqlite_source is not None:
            resolved = _resolve_symbols_from_source(
                "sqlite_db",
                sqlite_source,
                target_day=target_day,
                max_universe=max_universe,
                min_avg_amount=min_avg_amount,
            )
            if resolved:
                return resolved

    if _requires_liquid_universe(source_name, min_avg_amount):
        raise DataError(f"{source_name} 未能解析实时流动性标的池，拒绝退回默认大盘池")

    return list(default_symbols[:max_universe] if max_universe > 0 else default_symbols)
