from __future__ import annotations

import json
import math
import os
from pathlib import Path

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
            if workload == "live_short":
                frames = _drop_stale_live_frames(frames, end_date=end_date)
                missing = [
                    str(symbol)
                    for symbol in symbols
                    if str(symbol) not in frames
                    or not isinstance(frames.get(str(symbol)), pd.DataFrame)
                    or frames[str(symbol)].empty
                ]
                if missing and not _allow_partial_intraday_batch():
                    raise DataError(
                        "live_short 日线取数不完整，拒绝生成候选；缺少: "
                        + ", ".join(missing[:20])
                    )
                if missing:
                    minimum = _minimum_live_short_frames(len(symbols))
                    fetched = len(symbols) - len(missing)
                    if fetched < minimum:
                        raise DataError(
                            "盘中批次有效日线低于最低覆盖，拒绝生成候选；"
                            f"有效 {fetched}/{len(symbols)}，最低 {minimum}；缺少: "
                            + ", ".join(missing[:20])
                        )
                    for frame in frames.values():
                        if isinstance(frame, pd.DataFrame):
                            frame.attrs["live_short_missing_symbols"] = tuple(missing)
                            frame.attrs["live_short_resolved_symbol_count"] = len(symbols)
                            frame.attrs["live_short_fetched_frame_count"] = fetched
                else:
                    fetched = len(symbols)
                _write_intraday_batch_detail(
                    symbols,
                    fetched_symbols=[
                        str(symbol)
                        for symbol in symbols
                        if str(symbol) in frames
                        and isinstance(frames[str(symbol)], pd.DataFrame)
                        and not frames[str(symbol)].empty
                    ],
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


def _allow_partial_intraday_batch() -> bool:
    """Allow bad symbols to be isolated only for the production batch task."""
    task_id = os.getenv("AQSP_RUN_TASK_ID", "").strip().lower()
    value = os.getenv("AQSP_INTRADAY_ALLOW_PARTIAL_BATCH", "true").strip().lower()
    return task_id in {"intraday", "midday"} and value in {"1", "true", "yes", "on"}


def _minimum_live_short_frames(symbol_count: int) -> int:
    raw = os.getenv("AQSP_INTRADAY_MIN_VALID_RATIO", "0.8").strip()
    try:
        ratio = float(raw)
    except ValueError:
        ratio = 0.8
    ratio = min(max(ratio, 0.0), 1.0)
    return max(1, math.ceil(symbol_count * ratio))


def _drop_stale_live_frames(
    frames: dict[str, pd.DataFrame], *, end_date: date | None
) -> dict[str, pd.DataFrame]:
    """Remove old daily bars from a live batch instead of treating them as live."""
    if not frames or end_date is None:
        return frames
    result: dict[str, pd.DataFrame] = {}
    for symbol, frame in frames.items():
        if frame.empty or "date" not in frame.columns:
            continue
        latest = pd.to_datetime(frame["date"], errors="coerce").max()
        # A live batch must contain the requested trade date exactly. Older
        # rows are stale; future rows are look-ahead data and equally invalid.
        if pd.isna(latest) or latest.date() != end_date:
            continue
        result[symbol] = frame
    return result


def _write_intraday_batch_detail(
    resolved_symbols: list[str], *, fetched_symbols: list[str]
) -> None:
    """Persist batch coverage for the shell runner without changing source policy."""
    raw_path = os.getenv("AQSP_INTRADAY_SKIP_DETAIL_PATH", "").strip()
    if not raw_path:
        return
    resolved = list(
        dict.fromkeys(str(symbol) for symbol in resolved_symbols if str(symbol))
    )
    fetched = list(
        dict.fromkeys(str(symbol) for symbol in fetched_symbols if str(symbol))
    )
    fetched_set = set(fetched)
    skipped = [symbol for symbol in resolved if symbol not in fetched_set]
    payload = {
        "resolved_count": len(resolved),
        "fetched_count": len(fetched),
        "skipped_count": len(skipped),
        "resolved_symbols": resolved,
        "fetched_symbols": fetched,
        "skipped_symbols": skipped,
    }
    path = Path(raw_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


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
