from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
import inspect
import os
from typing import Any

import pandas as pd

from aqsp.data import pit_financial
from aqsp.data.cache import DataCache

_SQLITE_PREFILTERED_SYMBOLS_ENV = "AQSP_SQLITE_PREFILTERED_SYMBOLS"


@dataclass(frozen=True)
class WalkforwardFetchRequest:
    source: str
    symbols: list[str]
    start: str
    end: str
    cache_path: str | None = None
    skip_pit_financials: bool = False
    benchmark_symbol: str | None = None


@dataclass(frozen=True)
class WalkforwardFetchResult:
    frames: dict[str, pd.DataFrame]
    symbols: list[str]
    # 2026-10-06：本次跑批「财务数据是否真的参与了打分」的说明（None = 未跳过 PIT，
    # 走的是 enrich 分支，状态由 source_statuses 表达）。skip 分支必须填入，
    # 否则「quality/value/mean_reversion 三维空转」这个事实不会被写进任何产物。
    pit_note: str | None = None


def _attach_benchmark_frame(
    frames: dict[str, pd.DataFrame],
    source: Any,
    benchmark_symbol: str | None,
    start: date,
    end: date,
) -> None:
    if not benchmark_symbol or not hasattr(source, "fetch_index"):
        return
    try:
        benchmark_frames = source.fetch_index([benchmark_symbol], start, end)
    except Exception:
        return
    benchmark_frame = benchmark_frames.get(benchmark_symbol)
    if isinstance(benchmark_frame, pd.DataFrame) and not benchmark_frame.empty:
        frames[benchmark_symbol] = benchmark_frame


def _get_source_with_optional_cache(
    get_source_fn: Callable[..., Any],
    source: str,
    cache: DataCache | None,
) -> Any:
    if cache is None:
        return get_source_fn(source)
    try:
        signature = inspect.signature(get_source_fn)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        parameters = signature.parameters.values()
        if not any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            or (
                parameter.name == "cache"
                and parameter.kind
                in {
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                }
            )
            for parameter in parameters
        ):
            return get_source_fn(source)
    try:
        return get_source_fn(source, cache=cache)
    except TypeError as exc:
        if "cache" not in str(exc):
            raise
        return get_source_fn(source)


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
    benchmark_symbol = str(request.benchmark_symbol or "").strip() or None
    start_d = date.fromisoformat(request.start)
    end_d = date.fromisoformat(request.end)

    if source in {"multi", "akshare", "eastmoney", "tencent"}:
        frames = fetch_frames_for_cli_fn(
            source,
            symbols,
            benchmark_symbol=benchmark_symbol,
            cache_path=request.cache_path or None,
            days=fetch_days_fn(request.start, request.end),
        )
        return WalkforwardFetchResult(frames=frames, symbols=symbols)

    if source == "mootdx":
        src = get_source_fn("mootdx")
        frames = src.fetch_daily(symbols, start_d, end_d, adjust="", count=2000)
        _attach_benchmark_frame(frames, src, benchmark_symbol, start_d, end_d)
        return WalkforwardFetchResult(frames=frames, symbols=symbols)

    if source == "sina":
        src = get_source_fn("sina")
        frames = src.fetch_daily(symbols, start_d, end_d, adjust="")
        _attach_benchmark_frame(frames, src, benchmark_symbol, start_d, end_d)
        return WalkforwardFetchResult(frames=frames, symbols=symbols)

    if source in {"baostock", "sqlite_db"}:
        cache = (
            DataCache(request.cache_path)
            if source == "sqlite_db" and request.cache_path
            else None
        )
        src = _get_source_with_optional_cache(get_source_fn, source, cache)
        if source == "sqlite_db":
            available = src.get_available_symbols()
            symbols = [symbol for symbol in symbols if symbol in available]
            prefiltered = (
                str(os.environ.get(_SQLITE_PREFILTERED_SYMBOLS_ENV, "")).strip().lower()
            )
            if hasattr(src, "get_symbols_with_daily_coverage") and prefiltered not in {
                "1",
                "true",
                "yes",
                "on",
            }:
                symbols = src.get_symbols_with_daily_coverage(
                    symbols,
                    start_d,
                    end_d,
                    min_rows=None,
                )
            print_fn(f"SQLite 数据库中可用且覆盖区间的标的: {len(symbols)} 只")
        frames = src.fetch_daily(symbols, start_d, end_d, adjust="")
        _attach_benchmark_frame(frames, src, benchmark_symbol, start_d, end_d)
        if request.skip_pit_financials:
            # 🔴 2026-10-06：这里必须**显式声明哪些维度因此不参与打分**。
            # 背景：`--streaming` 架构上强制本开关（cli.py:3691，避免 PIT 帧无界占内存），
            # 跳过 ⇒ frames 无 pe/roe ⇒ quality/value/mean_reversion 恒为常数（实测
            # 唯一值=1、std=0），即 7 维里有 3 维是**空转**的。若变体对这些维度配了
            # 非零权重，该权重对选股**毫无作用**，而报告的逐变体表仍会如实显示它。
            # 旧实现在此直接 return，把「财务数据源是否可用」这个关键事实
            # **完全不上报**（pit_result.source_statuses 的打印在其后，永不执行）
            # ⇒ 只能被人肉考古发现。现改为：既打印、也把状态挂到返回值上，
            # 供 cli 写进 gate report（见 WalkforwardFetchResult.pit_note）。
            skipped_note = (
                "⚠️ 已跳过 point-in-time 财务补充（--skip-pit-financials；"
                "--streaming 架构强制，见 cli.py:3691）⇒ quality / value / "
                "mean_reversion 三维**未参与打分**（无 pe/roe 输入时它们恒为常数）；"
                "若这些维度在变体表里有非零权重，该权重对选股**没有任何作用**。"
            )
            print_fn("已跳过 point-in-time 财务补充，仅使用价格数据跑 gate")
            print_fn(skipped_note)
            return WalkforwardFetchResult(
                frames=frames, symbols=symbols, pit_note=skipped_note
            )
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
