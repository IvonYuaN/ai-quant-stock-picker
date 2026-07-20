from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.data.source import DataSource, OhlcvFrame
from aqsp.data.akshare_source import AkshareSource
from aqsp.data.efinance_source import EfinanceSource
from aqsp.data.sina_source import SinaSource
from aqsp.data.eastmoney_source import EastmoneySource
from aqsp.data.tushare_pit import TusharePitClient
from aqsp.data.pit_financial import (
    PitEnrichmentResult,
    PitSourceStatus,
    enrich_ohlcv_with_pit_financials,
    load_optional_disclosure_data,
)
from aqsp.data.index_constituents import load_optional_index_constituents
from aqsp.data.tencent_source import TencentSource
from aqsp.data.mootdx_source import MootdxSource
from aqsp.data.tdx_vipdoc_source import TdxVipdocSource
from aqsp.data.multi_source import MultiSource
from aqsp.data.cache import DataCache
from aqsp.data.adjust import AdjustmentService
from aqsp.data.fetcher import MultiSourceFetcher, create_default_fetcher
from aqsp.data.cli_fetch import fetch_frames_for_cli_with_metadata
from aqsp.data.intraday import IntradayService
from aqsp.data.realtime import RealtimeService
from aqsp.data.trading_calendar import (
    TradingCalendarWindow,
    load_optional_trade_calendar,
    resolve_is_trading_day,
    resolve_next_trading_day,
    resolve_previous_trading_day,
    trading_day_lag,
)
from aqsp.indicators import normalize_ohlcv
from aqsp.core.time import today_shanghai

_logger = logging.getLogger(__name__)


def _attach_optional_benchmark(
    out: dict[str, pd.DataFrame],
    source: DataSource,
    *,
    benchmark_symbol: str | None,
    start: date,
    end: date,
    days: int,
) -> None:
    if not benchmark_symbol or benchmark_symbol in out:
        return
    try:
        bench = source.fetch_index([benchmark_symbol], start, end)
    except Exception as exc:
        _logger.warning(
            "optional benchmark fetch failed %s via %s: %s",
            benchmark_symbol,
            source.name,
            exc,
        )
        return
    if benchmark_symbol in bench:
        out[benchmark_symbol] = (
            bench[benchmark_symbol].tail(days).reset_index(drop=True)
        )


def load_csv(path: str | Path) -> dict[str, pd.DataFrame]:
    df = normalize_ohlcv(pd.read_csv(path, dtype={"symbol": str, "代码": str}))
    if df["symbol"].eq("").all():
        return {"CSV": df}
    return {
        str(symbol): part.reset_index(drop=True)
        for symbol, part in df.groupby("symbol")
    }


def fetch_akshare(
    symbols: list[str],
    days: int = 260,
    adjust: str = "",
    benchmark_symbol: str | None = "000300",
    cache_path: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Fetch A-share daily OHLCV via akshare with optional benchmark index.

    Args:
        cache_path: optional independent SQLite cache path. Pass when running
            walk-forward to avoid contaminating the production cache.
    """
    cache = DataCache(db_path=cache_path) if cache_path else None
    source = AkshareSource(cache=cache) if cache else AkshareSource()
    end = today_shanghai()
    start = end - timedelta(days=max(days * 2, 365))
    out = source.fetch_daily(symbols, start, end, adjust)
    for symbol, df in out.items():
        out[symbol] = df.tail(days).reset_index(drop=True)
    _attach_optional_benchmark(
        out,
        source,
        benchmark_symbol=benchmark_symbol,
        start=start,
        end=end,
        days=days,
    )
    return out


def fetch_with_source(
    source: DataSource,
    symbols: list[str],
    days: int = 260,
    adjust: str = "",
    benchmark_symbol: str | None = None,
    end_date: date | None = None,
) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV using an arbitrary DataSource."""
    end = end_date or today_shanghai()
    start = end - timedelta(days=max(days * 2, 365))
    out = _fetch_daily_with_symbol_isolation(source, symbols, start, end, adjust)
    requested = {str(symbol) for symbol in symbols}
    returned = {
        str(symbol)
        for symbol, frame in out.items()
        if symbol and isinstance(frame, pd.DataFrame) and not frame.empty
    }
    if requested and not returned:
        raise DataError(f"数据源 {source.name} 未返回任何有效日线: {sorted(requested)}")
    for symbol, df in out.items():
        out[symbol] = df.tail(days).reset_index(drop=True)
    _attach_optional_benchmark(
        out,
        source,
        benchmark_symbol=benchmark_symbol,
        start=start,
        end=end,
        days=days,
    )
    missing = sorted(requested - returned)
    if missing:
        _logger.warning(
            "数据源 %s 日线获取不完整，跳过 %d/%d 个标的: %s",
            source.name,
            len(missing),
            len(requested),
            missing[:20],
        )
    return out


def _fetch_daily_with_symbol_isolation(
    source: DataSource,
    symbols: list[str],
    start: date,
    end: date,
    adjust: str,
) -> dict[str, pd.DataFrame]:
    try:
        out = source.fetch_daily(symbols, start, end, adjust)
    except Exception as exc:
        _logger.warning(
            "数据源 %s 批量日线获取失败，改为逐标的隔离: %s",
            source.name,
            exc,
        )
        out = {}
    missing = _missing_valid_daily_symbols(out, symbols)
    if not missing:
        return out

    # live_short has an explicit coverage gate downstream. Do not spend the
    # remaining deadline retrying every missing symbol serially after the
    # multi-source layer already returned a fresh partial batch.
    if getattr(source, "_active_workload", None) == "live_short":
        return out

    for symbol in missing:
        try:
            single = source.fetch_daily([symbol], start, end, adjust)
        except Exception as exc:
            _logger.warning(
                "数据源 %s 日线跳过坏标的 %s: %s",
                source.name,
                symbol,
                exc,
            )
            continue
        frame = single.get(symbol)
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            out[symbol] = frame
    return out


def _missing_valid_daily_symbols(
    out: dict[str, pd.DataFrame],
    symbols: list[str],
) -> list[str]:
    returned = {
        str(symbol)
        for symbol, frame in out.items()
        if symbol and isinstance(frame, pd.DataFrame) and not frame.empty
    }
    return [str(symbol) for symbol in symbols if str(symbol) not in returned]


__all__ = [
    "DataSource",
    "OhlcvFrame",
    "AkshareSource",
    "EfinanceSource",
    "SinaSource",
    "EastmoneySource",
    "TusharePitClient",
    "PitEnrichmentResult",
    "PitSourceStatus",
    "enrich_ohlcv_with_pit_financials",
    "load_optional_disclosure_data",
    "load_optional_index_constituents",
    "TencentSource",
    "MootdxSource",
    "TdxVipdocSource",
    "MultiSource",
    "DataCache",
    "AdjustmentService",
    "IntradayService",
    "RealtimeService",
    "TradingCalendarWindow",
    "load_optional_trade_calendar",
    "resolve_is_trading_day",
    "resolve_next_trading_day",
    "resolve_previous_trading_day",
    "trading_day_lag",
    "load_csv",
    "fetch_akshare",
    "fetch_with_source",
    "fetch_frames_for_cli_with_metadata",
    "MultiSourceFetcher",
    "create_default_fetcher",
]
