from __future__ import annotations

from collections.abc import Callable
from datetime import date

from aqsp.core.errors import DataError
from aqsp.core.time import today_shanghai


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
        return symbols
    if pool_name and pool_name != "all":
        from aqsp.universe.pool import UniversePool

        pool = UniversePool.from_default(pool_name)
        return pool.get_symbols(as_of=target_day)
    try:
        source = get_source_fn(source_name)
    except DataError:
        return list(
            default_symbols[:max_universe] if max_universe > 0 else default_symbols
        )
    if hasattr(source, "get_liquid_symbols"):
        try:
            liquid_symbols = source.get_liquid_symbols(
                limit=max_universe,
                min_amount=min_avg_amount,
            )
        except DataError:
            liquid_symbols = []
        if liquid_symbols:
            return list(liquid_symbols)
    if hasattr(source, "get_available_symbols"):
        try:
            available = source.get_available_symbols()
        except DataError:
            available = []
        if available:
            return list(available[:max_universe] if max_universe > 0 else available)
    return list(default_symbols[:max_universe] if max_universe > 0 else default_symbols)
