from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from aqsp.data import pit_financial
from aqsp.data.cache import DataCache


@dataclass(frozen=True)
class WalkforwardFetchRequest:
    source: str
    symbols: list[str]
    start: str
    end: str
    cache_path: str | None = None
    skip_pit_financials: bool = False


@dataclass(frozen=True)
class WalkforwardFetchResult:
    frames: dict[str, pd.DataFrame]
    symbols: list[str]


def fetch_walkforward_frames(
    request: WalkforwardFetchRequest,
    *,
    get_source_fn: Callable[[str], Any],
    fetch_frames_for_cli_fn: Callable[..., dict[str, pd.DataFrame]],
    load_csv_fn: Callable[[str], dict[str, pd.DataFrame]],
    fetch_days_fn: Callable[[str, str], int],
    print_fn: Callable[[str], None] = print,
) -> WalkforwardFetchResult:
    source = request.source
    symbols = list(request.symbols)
    start_d = date.fromisoformat(request.start)
    end_d = date.fromisoformat(request.end)

    if source in {"multi", "akshare", "eastmoney", "tencent"}:
        frames = fetch_frames_for_cli_fn(
            source,
            symbols,
            benchmark_symbol=None,
            cache_path=request.cache_path or None,
            days=fetch_days_fn(request.start, request.end),
        )
        return WalkforwardFetchResult(frames=frames, symbols=symbols)

    if source == "mootdx":
        src = get_source_fn("mootdx")
        frames = src.fetch_daily(symbols, start_d, end_d, adjust="", count=2000)
        return WalkforwardFetchResult(frames=frames, symbols=symbols)

    if source == "sina":
        src = get_source_fn("sina")
        frames = src.fetch_daily(symbols, start_d, end_d, adjust="")
        return WalkforwardFetchResult(frames=frames, symbols=symbols)

    if source in {"baostock", "sqlite_db"}:
        src = get_source_fn(source)
        if source == "sqlite_db":
            available = src.get_available_symbols()
            symbols = [symbol for symbol in symbols if symbol in available]
            if hasattr(src, "get_symbols_with_daily_coverage"):
                symbols = src.get_symbols_with_daily_coverage(
                    symbols,
                    start_d,
                    end_d,
                    min_rows=None,
                )
            print_fn(f"SQLite 数据库中可用且覆盖区间的标的: {len(symbols)} 只")
        frames = src.fetch_daily(symbols, start_d, end_d, adjust="")
        if request.skip_pit_financials:
            print_fn("已跳过 point-in-time 财务补充，仅使用价格数据跑 gate")
            return WalkforwardFetchResult(frames=frames, symbols=symbols)
        print_fn(
            f"正在获取 {len(symbols)} 只股票 {request.start} ~ {request.end} 的 point-in-time 财务数据..."
        )
        pit_result = pit_financial.enrich_ohlcv_with_pit_financials(
            frames,
            symbols,
            start_d,
            end_d,
            cache=DataCache(),
        )
        print_fn(f"财务数据合并完成: {pit_result.financial_symbol_count} 只有财务数据")
        if pit_result.disclosure_symbol_count:
            print_fn(f"Tushare 披露日覆盖完成: {pit_result.disclosure_symbol_count} 只")
        for status in getattr(pit_result, "source_statuses", ()):
            print_fn(f"PIT源 {status.source_id}: {status.status} - {status.message}")
        return WalkforwardFetchResult(frames=pit_result.frames, symbols=symbols)

    return WalkforwardFetchResult(frames=load_csv_fn(source), symbols=symbols)
