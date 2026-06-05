"""National team (国家队) holdings tracking module.

This module identifies and tracks A-share holdings by Chinese government-related
institutions (central SAFE, securities finance corporations, national social security fund, etc.)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.utils.env import read_project_env_value

_logger = logging.getLogger(__name__)


# Keywords identifying national team institutions
NATIONAL_TEAM_KEYWORDS = [
    "中央汇金",
    "汇金资产",
    "汇金公司",
    "证金公司",
    "中国证券金融",
    "全国社保基金",
    "社保基金",
    "基本养老保险基金",
]


@dataclass(frozen=True)
class NationalTeamHolding:
    """Represents a national team holding in a stock."""

    symbol: str
    holder_name: str
    holding_shares: float
    holding_ratio: float
    announcement_date: str
    rank: int  # Top 10 shareholder rank


@dataclass(frozen=True)
class TushareConfig:
    """Tushare API configuration."""

    token: str


class NationalTeamTracker:
    """Tracks national team holdings using tushare API."""

    def __init__(self, config: TushareConfig | None = None) -> None:
        cfg = config or load_tushare_config()
        try:
            import tushare as ts

            self._pro = ts.pro_api(cfg.token)
        except ImportError as exc:
            raise RuntimeError(
                "tushare is not installed; run: pip install -e '.[data]'"
            ) from exc
        self._token = cfg.token

    def has_national_team_holding(self, symbol: str, as_of_date: date | None = None) -> bool:
        """Check if a stock has national team holdings.

        Args:
            symbol: Stock symbol (6-digit code without suffix)
            as_of_date: Reference date for holdings; defaults to today

        Returns:
            True if national team institution is in top 10 shareholders
        """
        try:
            holdings = self.fetch_top_holders(symbol, as_of_date)
            return not holdings.empty
        except Exception as exc:
            _logger.warning(
                "Failed to check national team holdings for %s: %s",
                symbol,
                exc,
            )
            return False

    def fetch_top_holders(
        self,
        symbol: str,
        as_of_date: date | None = None,
    ) -> pd.DataFrame:
        """Fetch top 10 shareholders and filter for national team.

        Args:
            symbol: Stock symbol (6-digit code without suffix)
            as_of_date: Reference date for holdings; defaults to today

        Returns:
            DataFrame with columns:
                - symbol: Stock symbol
                - holder_name: Institution name
                - holding_shares: Number of shares held
                - holding_ratio: Percentage of shares held
                - announcement_date: Date of announcement
                - rank: Top 10 rank
        """
        if as_of_date is None:
            as_of_date = date.today()

        ts_code = symbol_to_ts_code(symbol)

        try:
            # Fetch top 10 shareholders using tushare
            df = self._safe_pro_call(
                "top10_holders",
                ts_code=ts_code,
                start_date=None,  # Get latest data
                end_date=None,
            )
        except Exception as exc:
            raise DataError(
                f"Failed to fetch top 10 holders for {symbol} ({ts_code}): {exc}"
            ) from exc

        if df is None or df.empty:
            return pd.DataFrame(
                columns=[
                    "symbol",
                    "holder_name",
                    "holding_shares",
                    "holding_ratio",
                    "announcement_date",
                    "rank",
                ]
            )

        # Normalize column names and filter for national team
        normalized = self._normalize_top_holders(df, symbol)
        filtered = self._filter_national_team(normalized)

        return filtered

    def _safe_pro_call(self, method_name: str, **kwargs: Any) -> pd.DataFrame:
        """Call tushare pro API safely with error handling."""
        import contextlib
        import io

        method = getattr(self._pro, method_name)
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        try:
            with (
                contextlib.redirect_stdout(stdout_buffer),
                contextlib.redirect_stderr(stderr_buffer),
            ):
                return method(**kwargs)
        except Exception as exc:
            captured = " ".join(
                part.strip()
                for part in (stdout_buffer.getvalue(), stderr_buffer.getvalue())
                if part.strip()
            )
            if captured:
                raise DataError(f"tushare API error: {captured}") from exc
            raise

    def _normalize_top_holders(
        self,
        df: pd.DataFrame,
        symbol: str,
    ) -> pd.DataFrame:
        """Normalize tushare top holders response."""
        normalized = df.copy()

        # Expected columns from tushare top10_holders
        available = set(normalized.columns)

        # Try alternate column names
        col_mapping = {
            "holder_name": ["holder_name", "holder"],
            "hold_ratio": ["hold_ratio", "ratio"],
            "hold_amount": ["hold_amount", "amount", "share_num"],
            "ann_date": ["ann_date", "date", "announcement_date"],
            "rank": ["rank"],
        }

        for target, aliases in col_mapping.items():
            for alias in aliases:
                if alias in available:
                    if target not in normalized.columns:
                        normalized[target] = normalized[alias]
                    break

        # Ensure symbol column
        normalized["symbol"] = symbol

        # Ensure rank column (position in top 10)
        if "rank" not in normalized.columns:
            normalized["rank"] = range(1, len(normalized) + 1)

        # Normalize numeric columns
        if "hold_ratio" in normalized.columns:
            normalized["holding_ratio"] = (
                pd.to_numeric(normalized["hold_ratio"], errors="coerce").fillna(0)
            )
        else:
            normalized["holding_ratio"] = 0.0

        if "hold_amount" in normalized.columns:
            normalized["holding_shares"] = (
                pd.to_numeric(normalized["hold_amount"], errors="coerce").fillna(0)
            )
        else:
            normalized["holding_shares"] = 0.0

        # Normalize date column
        if "ann_date" in normalized.columns:
            normalized["announcement_date"] = pd.to_datetime(
                normalized["ann_date"], errors="coerce"
            ).dt.strftime("%Y-%m-%d")
        else:
            normalized["announcement_date"] = ""

        # Ensure holder_name is string
        if "holder_name" in normalized.columns:
            normalized["holder_name"] = normalized["holder_name"].astype(str)

        # Select and order output columns
        output_cols = [
            "symbol",
            "holder_name",
            "holding_shares",
            "holding_ratio",
            "announcement_date",
            "rank",
        ]
        return normalized[output_cols].reset_index(drop=True)

    @staticmethod
    def _filter_national_team(df: pd.DataFrame) -> pd.DataFrame:
        """Filter dataframe to only national team institutions."""
        if df.empty:
            return df

        df = df.copy()

        # Create a mask for national team keywords
        mask = df["holder_name"].str.contains(
            "|".join(NATIONAL_TEAM_KEYWORDS),
            case=False,
            na=False,
            regex=True,
        )

        return df[mask].reset_index(drop=True)

    def get_holding_cost_estimate(
        self,
        symbol: str,
        holding_announcement_date: str,
    ) -> float:
        """Estimate the building cost of national team position.

        This is a simplified version: uses VWAP from 90 days before announcement
        multiplied by 0.95 (assuming opportunistic entry).

        Args:
            symbol: Stock symbol
            holding_announcement_date: Date string in YYYY-MM-DD format

        Returns:
            Estimated cost per share, or 0 if unable to calculate
        """
        try:
            announcement_date = pd.to_datetime(holding_announcement_date).date()
            # Estimate building period: 90 days before announcement
            build_start = announcement_date - timedelta(days=90)

            # Note: This is a placeholder - actual implementation would fetch
            # historical price data and calculate VWAP
            _logger.debug(
                "Cost estimation for %s from %s to %s would require historical price data",
                symbol,
                build_start,
                announcement_date,
            )
            return 0.0
        except Exception as exc:
            _logger.warning("Failed to estimate holding cost for %s: %s", symbol, exc)
            return 0.0


def load_tushare_config() -> TushareConfig:
    """Load tushare configuration from environment."""
    token = os.getenv("TUSHARE_TOKEN", "").strip() or read_project_env_value(
        "TUSHARE_TOKEN"
    )
    if not token:
        raise ValueError("TUSHARE_TOKEN is required for national team tracking")
    return TushareConfig(token=token)


def symbol_to_ts_code(symbol: str) -> str:
    """Convert 6-digit symbol to tushare ts_code format.

    Args:
        symbol: 6-digit stock code (e.g., '600000')

    Returns:
        ts_code with suffix (e.g., '600000.SH')
    """
    if symbol.startswith(("6", "9")):
        suffix = "SH"
    elif symbol.startswith(("8", "4")):
        suffix = "BJ"
    else:
        suffix = "SZ"
    return f"{symbol}.{suffix}"


def ts_code_to_symbol(ts_code: Any) -> str:
    """Convert tushare ts_code to 6-digit symbol.

    Args:
        ts_code: ts_code format (e.g., '600000.SH')

    Returns:
        6-digit symbol (e.g., '600000')
    """
    text = str(ts_code).strip()
    if "." not in text:
        return text
    return text.split(".", maxsplit=1)[0]
