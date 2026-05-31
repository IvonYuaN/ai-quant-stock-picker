from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from aqsp.data.source import DataSource, OhlcvFrame
from aqsp.data.akshare_source import AkshareSource
from aqsp.data.sina_source import SinaSource
from aqsp.data.eastmoney_source import EastmoneySource
from aqsp.data.tencent_source import TencentSource
from aqsp.data.mootdx_source import MootdxSource
from aqsp.data.tdx_vipdoc_source import TdxVipdocSource
from aqsp.data.multi_source import MultiSource
from aqsp.data.cache import DataCache
from aqsp.data.adjust import AdjustmentService
from aqsp.data.intraday import IntradayService
from aqsp.data.realtime import RealtimeService
from aqsp.indicators import normalize_ohlcv


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
    end = date.today()
    start = end - timedelta(days=max(days * 2, 365))
    out = source.fetch_daily(symbols, start, end, adjust)
    for symbol, df in out.items():
        out[symbol] = df.tail(days).reset_index(drop=True)
    if benchmark_symbol and benchmark_symbol not in out:
        bench = source.fetch_index([benchmark_symbol], start, end)
        if benchmark_symbol in bench:
            out[benchmark_symbol] = (
                bench[benchmark_symbol].tail(days).reset_index(drop=True)
            )
    return out


def fetch_with_source(
    source: DataSource,
    symbols: list[str],
    days: int = 260,
    adjust: str = "",
    benchmark_symbol: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV using an arbitrary DataSource."""
    end = date.today()
    start = end - timedelta(days=max(days * 2, 365))
    out = source.fetch_daily(symbols, start, end, adjust)
    for symbol, df in out.items():
        out[symbol] = df.tail(days).reset_index(drop=True)
    if benchmark_symbol and benchmark_symbol not in out:
        bench = source.fetch_index([benchmark_symbol], start, end)
        if benchmark_symbol in bench:
            out[benchmark_symbol] = (
                bench[benchmark_symbol].tail(days).reset_index(drop=True)
            )
    return out


__all__ = [
    "DataSource",
    "OhlcvFrame",
    "AkshareSource",
    "SinaSource",
    "EastmoneySource",
    "TencentSource",
    "MootdxSource",
    "TdxVipdocSource",
    "MultiSource",
    "DataCache",
    "AdjustmentService",
    "IntradayService",
    "RealtimeService",
    "load_csv",
    "fetch_akshare",
    "fetch_with_source",
]
