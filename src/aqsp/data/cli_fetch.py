from __future__ import annotations

from typing import Callable

import pandas as pd

from aqsp.core.errors import DataError


def fetch_frames_for_cli_with_metadata(
    source_name: str,
    symbols: list[str],
    *,
    benchmark_symbol: str | None,
    cache_path: str | None = None,
    days: int = 260,
    fetch_akshare_fn: Callable[..., dict[str, pd.DataFrame]],
    get_source_fn: Callable[[str], object],
    fetch_with_source_fn: Callable[..., dict[str, pd.DataFrame]],
    record_source_success_fn: Callable[[str, str], None],
    record_source_failure_fn: Callable[[str, str], None],
) -> tuple[dict[str, pd.DataFrame], str]:
    try:
        if source_name == "akshare":
            frames = fetch_akshare_fn(
                symbols,
                days=days,
                benchmark_symbol=benchmark_symbol,
                cache_path=cache_path,
            )
            record_source_success_fn(source_name, "akshare")
            return frames, "akshare"
        source = get_source_fn(source_name)
        frames = fetch_with_source_fn(
            source,
            symbols,
            days=days,
            benchmark_symbol=benchmark_symbol,
        )
        actual_source = str(getattr(source, "last_used_source", None) or source.name)
        record_source_success_fn(source_name, actual_source)
        return frames, actual_source
    except DataError as exc:
        record_source_failure_fn(source_name, str(exc))
        raise
    except Exception as exc:
        record_source_failure_fn(source_name, str(exc))
        raise DataError(f"数据源 {source_name} 获取失败: {exc}") from exc
