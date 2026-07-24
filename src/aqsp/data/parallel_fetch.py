from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar

_ResultT = TypeVar("_ResultT")


def configured_fetch_workers(
    requested_count: int,
    *,
    env_name: str = "AQSP_INTRADAY_FETCH_WORKERS",
    default: int = 12,
) -> int:
    """Return a bounded worker count for independent market requests."""
    raw = os.getenv(env_name, "").strip()
    try:
        configured = int(raw) if raw else default
    except ValueError:
        configured = default
    return max(1, min(configured, max(1, requested_count)))


def fetch_in_parallel(
    symbols: list[str],
    fetch_one: Callable[[str], _ResultT],
) -> tuple[dict[str, _ResultT], dict[str, Exception]]:
    """Fetch independent symbols concurrently and retain per-symbol failures."""
    requested = list(dict.fromkeys(str(symbol) for symbol in symbols if str(symbol)))
    results: dict[str, _ResultT] = {}
    errors: dict[str, Exception] = {}
    with ThreadPoolExecutor(
        max_workers=configured_fetch_workers(len(requested)),
        thread_name_prefix="aqsp-intraday-fetch",
    ) as executor:
        futures = {executor.submit(fetch_one, symbol): symbol for symbol in requested}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                errors[symbol] = exc
            else:
                results[symbol] = result
    return results, errors
