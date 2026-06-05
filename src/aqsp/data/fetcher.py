"""
MultiSourceFetcher: Integrated data fetcher with Tushare (primary) and Akshare (fallback).

Implements automatic fallback from Tushare to Akshare when primary source fails.
Ensures unified data format and comprehensive logging of source switching.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Literal
import pandas as pd

from aqsp.data.source import DataSource, OhlcvFrame, apply_limit_suspended_adj
from aqsp.data.cache import DataCache
from aqsp.data.validation import DataValidator
from aqsp.core.errors import DataError

_logger = logging.getLogger("aqsp.data.fetcher")


class MultiSourceFetcher:
    """Fetches daily OHLCV data with automatic fallback from Tushare to Akshare.
    
    Attributes:
        primary_source: Primary data source (usually Tushare)
        fallback_source: Fallback data source (usually Akshare)
        cache: Optional data cache for persistence
    """

    def __init__(
        self,
        primary_source: DataSource,
        fallback_source: DataSource,
        cache: DataCache | None = None,
    ) -> None:
        """Initialize MultiSourceFetcher with primary and fallback sources.
        
        Args:
            primary_source: Primary data source
            fallback_source: Fallback data source
            cache: Optional DataCache instance
        """
        self.primary_source = primary_source
        self.fallback_source = fallback_source
        self.cache = cache or DataCache()
        self._last_source_used: dict[str, str] = {}

    def fetch_daily_data(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
    ) -> dict[str, OhlcvFrame]:
        """Fetch daily OHLCV data with automatic fallback.
        
        Priority:
        1. Try primary source (Tushare)
        2. If primary fails, fallback to secondary source (Akshare)
        3. Log switching events
        
        Args:
            symbols: List of stock symbols to fetch
            start: Start date
            end: End date
            adjust: Price adjustment type ("", "qfq", "hfq")
            
        Returns:
            Dictionary mapping symbol to OHLCV DataFrame
            
        Raises:
            DataError: If both primary and fallback sources fail
        """
        result: dict[str, OhlcvFrame] = {}
        primary_error: Exception | None = None

        try:
            primary_data = self._fetch_from_tushare(symbols, start, end, adjust)
            result.update(primary_data)
            for symbol in primary_data:
                self._last_source_used[symbol] = self.primary_source.name
                _logger.info(
                    "primary source success: source=%s symbol=%s rows=%s range=%s..%s",
                    self.primary_source.name,
                    symbol,
                    len(primary_data[symbol]),
                    start.isoformat(),
                    end.isoformat(),
                )
        except Exception as exc:
            primary_error = exc
            _logger.warning(
                "primary source failed: source=%s symbols=%s error=%s",
                self.primary_source.name,
                symbols,
                exc,
            )

        missing_symbols = [s for s in symbols if s not in result]
        if missing_symbols:
            try:
                fallback_data = self._fetch_from_akshare(missing_symbols, start, end, adjust)
                result.update(fallback_data)
                for symbol in fallback_data:
                    self._last_source_used[symbol] = self.fallback_source.name
                    _logger.warning(
                        "fallback success: symbol=%s primary=%s fallback=%s rows=%s range=%s..%s",
                        symbol,
                        self.primary_source.name,
                        self.fallback_source.name,
                        len(fallback_data[symbol]),
                        start.isoformat(),
                        end.isoformat(),
                    )
            except Exception as exc:
                _logger.error(
                    "all sources failed: primary=%s primary_error=%s fallback=%s fallback_error=%s symbols=%s",
                    self.primary_source.name,
                    primary_error,
                    self.fallback_source.name,
                    exc,
                    missing_symbols,
                )
                raise DataError(
                    f"Failed to fetch data from both sources for {missing_symbols}: "
                    f"primary={str(primary_error)[:100] if primary_error else 'partial_or_empty'}, "
                    f"fallback={str(exc)[:100]}"
                ) from exc

        if not result:
            raise DataError(f"No data fetched for symbols: {symbols}")

        # 集成4: 验证获取的数据质量（DataValidator）
        validator = DataValidator()
        validated_result: dict[str, OhlcvFrame] = {}
        for symbol, df in result.items():
            try:
                validation = validator.validate_ohlc(df, symbol=symbol)
                if validation.is_valid:
                    validated_result[symbol] = df
                    if validation.warnings:
                        _logger.warning(f"{symbol} 数据验证警告: {validation.warnings}")
                else:
                    _logger.error(f"{symbol} 数据验证失败: {validation.errors}")
            except Exception as e:
                _logger.error(f"{symbol} 数据验证异常: {e}")
        
        if not validated_result:
            raise DataError(f"所有 {len(result)} 个符号的数据验证失败，拒绝返回脏数据")
        
        _logger.info(f"数据验证完成: {len(result)} -> {len(validated_result)} (通过验证 {len(validated_result)} 条)")
        return validated_result

    def _fetch_from_tushare(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
    ) -> dict[str, OhlcvFrame]:
        """Fetch data from Tushare (primary source).
        
        Args:
            symbols: List of stock symbols
            start: Start date
            end: End date
            adjust: Price adjustment type
            
        Returns:
            Dictionary mapping symbol to normalized OHLCV DataFrame
        """
        raw_data = self.primary_source.fetch_daily(symbols, start, end, adjust)
        result: dict[str, OhlcvFrame] = {}
        for symbol, df in raw_data.items():
            if df.empty:
                continue
            df = self._normalize_columns(df, symbol)
            self.cache.set_ohlcv(symbol, df, source=self.primary_source.name)
            result[symbol] = df
        return result

    def _fetch_from_akshare(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
    ) -> dict[str, OhlcvFrame]:
        """Fetch data from Akshare (fallback source).
        
        Args:
            symbols: List of stock symbols
            start: Start date
            end: End date
            adjust: Price adjustment type
            
        Returns:
            Dictionary mapping symbol to normalized OHLCV DataFrame
        """
        raw_data = self.fallback_source.fetch_daily(symbols, start, end, adjust)
        result: dict[str, OhlcvFrame] = {}
        for symbol, df in raw_data.items():
            if df.empty:
                continue
            df = self._normalize_columns(df, symbol)
            self.cache.set_ohlcv(symbol, df, source=self.fallback_source.name)
            result[symbol] = df
        return result

    def _normalize_columns(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Normalize OHLCV column names to standard format.
        
        Standardizes to: date, symbol, name, open, high, low, close, volume, amount,
        suspended, limit_up, limit_down
        
        Args:
            df: Raw OHLCV DataFrame
            symbol: Stock symbol for validation
            
        Returns:
            Normalized DataFrame with standard column names
        """
        df = df.copy()

        rename_map = {
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
            "名称": "name",
        }
        df = df.rename(columns=rename_map)

        if "date" not in df.columns:
            if "日期" in df.columns:
                df["date"] = df["日期"]
            elif "trade_date" in df.columns:
                df["date"] = df["trade_date"]

        parsed_dates = pd.to_datetime(df["date"], errors="coerce")
        df["date"] = parsed_dates.dt.strftime("%Y-%m-%d").astype(object)

        if "symbol" not in df.columns:
            if "代码" in df.columns:
                df["symbol"] = df["代码"].astype(str)
            else:
                df["symbol"] = symbol
        else:
            df["symbol"] = df["symbol"].astype(str)
        if "name" not in df.columns:
            df["name"] = symbol

        numeric_cols = ["open", "high", "low", "close", "volume", "amount"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "amount" not in df.columns or df["amount"].isna().all():
            if "volume" in df.columns and "close" in df.columns:
                df["amount"] = df["volume"] * df["close"]

        df = apply_limit_suspended_adj(df, symbol, cache=self.cache)

        standard_cols = [
            "date",
            "symbol",
            "name",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "suspended",
            "limit_up",
            "limit_down",
        ]
        existing_cols = [col for col in standard_cols if col in df.columns]
        df = df[existing_cols]
        return df

    def get_last_source_used(self, symbol: str) -> str | None:
        """Get the last source used for fetching a symbol's data.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Source name or None if symbol not yet fetched
        """
        return self._last_source_used.get(symbol)

    def get_all_last_sources(self) -> dict[str, str]:
        """Get all symbols and their last used sources.
        
        Returns:
            Dictionary mapping symbol to source name
        """
        return self._last_source_used.copy()


def create_default_fetcher(cache: DataCache | None = None) -> MultiSourceFetcher:
    """Factory function to create a MultiSourceFetcher with default sources.
    
    Creates a fetcher with:
    - Primary: Tushare (requires token setup)
    - Fallback: Akshare (free, no setup needed)
    
    Args:
        cache: Optional DataCache instance
        
    Returns:
        Configured MultiSourceFetcher instance
    """
    from aqsp.data.akshare_source import AkshareSource
    
    try:
        # Try to import and create Tushare source
        # This is a placeholder - actual Tushare integration would go here
        # For now, using Akshare as primary with itself as fallback for demo
        primary = AkshareSource(cache=cache)
        fallback = AkshareSource(cache=cache)
    except ImportError as exc:
        raise RuntimeError(
            "Failed to create default fetcher: Tushare/Akshare not properly configured"
        ) from exc
    
    return MultiSourceFetcher(primary, fallback, cache=cache)
