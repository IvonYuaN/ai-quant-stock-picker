from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.data.pit_financial import merge_pit_financials


def _ohlcv_frame() -> pd.DataFrame:
    """Synthetic two-day OHLCV frame for 600519 (no network)."""
    return pd.DataFrame(
        [
            {
                "date": "2026-04-28",
                "symbol": "600519",
                "name": "贵州茅台",
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
                "amount": 1.0,
                "suspended": False,
                "limit_up": 1.1,
                "limit_down": 0.9,
            },
            {
                "date": "2026-04-30",
                "symbol": "600519",
                "name": "贵州茅台",
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
                "amount": 1.0,
                "suspended": False,
                "limit_up": 1.1,
                "limit_down": 0.9,
            },
        ]
    )


def _financial_frame(pub_date: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "600519",
                "statDate": "2026-03-31",
                "pubDate": pub_date,
                "roeAvg": 0.2,
                "gpMargin": 0.3,
                "epsTTM": 10.0,
                "totalShare": 1000.0,
            }
        ]
    )


def test_merge_pit_financials_fails_when_pubdate_after_asof_cutoff() -> None:
    """A pubDate strictly after the backtest cutoff must be rejected."""
    ohlcv = {"600519": _ohlcv_frame()}
    financials = {"600519": _financial_frame("2026-04-20")}

    with pytest.raises(DataError, match="as-of 之后才可见"):
        merge_pit_financials(ohlcv, financials, as_of=date(2026, 4, 15))


def test_merge_pit_financials_fails_when_disclosure_date_after_asof_cutoff() -> None:
    """The as_of guard must apply after disclosure overlay overrides pubDate."""
    ohlcv = {"600519": _ohlcv_frame()}
    financials = {"600519": _financial_frame("2026-04-20")}
    disclosures = {
        "600519": pd.DataFrame(
            [
                {
                    "symbol": "600519",
                    "end_date": "2026-03-31",
                    "ann_date": "2026-05-01",
                    "actual_date": "2026-05-02",
                }
            ]
        )
    }

    with pytest.raises(DataError, match="as-of 之后才可见"):
        merge_pit_financials(
            ohlcv,
            financials,
            disclosure_data=disclosures,
            as_of=date(2026, 4, 30),
        )


def test_merge_pit_financials_allows_future_pubdate_when_asof_is_none() -> None:
    """Default as_of=None preserves the legacy format-only validation."""
    ohlcv = {"600519": _ohlcv_frame()}
    financials = {"600519": _financial_frame("2026-04-20")}

    merged = merge_pit_financials(ohlcv, financials)

    assert "600519" in merged
    assert merged["600519"]["roe"].iloc[1] == 0.2


def test_merge_pit_financials_passes_when_pubdate_on_asof_cutoff() -> None:
    """A pubDate equal to the cutoff is visible and must be accepted."""
    ohlcv = {"600519": _ohlcv_frame()}
    financials = {"600519": _financial_frame("2026-04-20")}

    merged = merge_pit_financials(ohlcv, financials, as_of=date(2026, 4, 20))

    assert merged["600519"]["roe"].iloc[1] == 0.2
