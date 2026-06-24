from __future__ import annotations

import pandas as pd

from aqsp.core.errors import DataError


def fetch_frames_for_cli_with_metadata(
    source_name: str,
    symbols: list[str],
    *,
    benchmark_symbol: str | None,
    cache_path: str | None = None,
    days: int = 260,
    get_source_fn,
    fetch_with_source_fn,
    record_source_success_fn,
    record_source_failure_fn,
) -> tuple[dict[str, pd.DataFrame], str]:
    try:
        source = get_source_fn(source_name)
        _ = cache_path
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
