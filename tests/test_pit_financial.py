from __future__ import annotations

from datetime import date

import pandas as pd

from aqsp.data.pit_financial import (
    fetch_pit_financials,
    enrich_ohlcv_with_pit_financials,
    load_optional_disclosure_data,
    merge_pit_financials,
)
from aqsp.data.tushare_pit import TusharePitClient


def test_merge_pit_financials_when_disclosure_overrides_pubdate() -> None:
    ohlcv = {
        "600519": pd.DataFrame(
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
    }
    financials = {
        "600519": pd.DataFrame(
            [
                {
                    "symbol": "600519",
                    "statDate": "2026-03-31",
                    "pubDate": "2026-04-20",
                    "roeAvg": 0.2,
                    "gpMargin": 0.3,
                    "epsTTM": 10.0,
                    "totalShare": 1000.0,
                }
            ]
        )
    }
    disclosures = {
        "600519": pd.DataFrame(
            [
                {
                    "symbol": "600519",
                    "end_date": "2026-03-31",
                    "ann_date": "2026-04-29",
                    "actual_date": "2026-04-30",
                }
            ]
        )
    }

    merged = merge_pit_financials(ohlcv, financials, disclosure_data=disclosures)

    assert pd.isna(merged["600519"]["roe"].iloc[0])
    assert merged["600519"]["roe"].iloc[1] == 0.2


def test_load_optional_disclosure_data_when_client_returns_rows() -> None:
    class DummyClient(TusharePitClient):
        def __init__(self) -> None:
            pass

        def fetch_disclosure_dates(self, symbols, start, end):
            return pd.DataFrame(
                [
                    {
                        "symbol": "600519",
                        "end_date": "2026-03-31",
                        "ann_date": "2026-04-29",
                        "actual_date": "2026-04-30",
                    }
                ]
            )

    result = load_optional_disclosure_data(
        ["600519"],
        date(2026, 4, 1),
        date(2026, 6, 30),
        client=DummyClient(),
    )

    assert list(result) == ["600519"]
    assert result["600519"]["actual_date"].iloc[0] == "2026-04-30"


def test_enrich_ohlcv_with_pit_financials_when_disclosure_available(
    monkeypatch,
) -> None:
    ohlcv = {
        "600519": pd.DataFrame(
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
    }
    financials = {
        "600519": pd.DataFrame(
            [
                {
                    "symbol": "600519",
                    "statDate": "2026-03-31",
                    "pubDate": "2026-04-20",
                    "roeAvg": 0.2,
                    "gpMargin": 0.3,
                    "epsTTM": 10.0,
                    "totalShare": 1000.0,
                }
            ]
        )
    }
    disclosures = {
        "600519": pd.DataFrame(
            [
                {
                    "symbol": "600519",
                    "end_date": "2026-03-31",
                    "ann_date": "2026-04-29",
                    "actual_date": "2026-04-30",
                }
            ]
        )
    }

    monkeypatch.setattr(
        "aqsp.data.pit_financial._fetch_pit_financials_with_status",
        lambda symbols, start_year, end_year, cache=None: (
            financials,
            type(
                "Status",
                (),
                {
                    "source_id": "baostock",
                    "status": "ok",
                    "message": "baostock 财务数据可用，覆盖 1 只标的。",
                },
            )(),
        ),
    )
    monkeypatch.setattr(
        "aqsp.data.pit_financial._load_optional_disclosure_data_with_status",
        lambda symbols, start, end, client=None: (
            disclosures,
            type(
                "Status",
                (),
                {
                    "source_id": "tushare",
                    "status": "ok",
                    "message": "Tushare 披露日可用，覆盖 1 只标的。",
                },
            )(),
        ),
    )

    result = enrich_ohlcv_with_pit_financials(
        ohlcv,
        ["600519"],
        date(2026, 4, 1),
        date(2026, 6, 30),
    )

    assert result.financial_symbol_count == 1
    assert result.disclosure_symbol_count == 1
    assert [item.source_id for item in result.source_statuses] == [
        "baostock",
        "tushare",
    ]
    assert pd.isna(result.frames["600519"]["roe"].iloc[0])
    assert result.frames["600519"]["roe"].iloc[1] == 0.2


def test_fetch_pit_financials_returns_empty_when_baostock_login_fails(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "aqsp.data.pit_financial._ensure_baostock_login",
        lambda: False,
    )

    result = fetch_pit_financials(["600519"], 2024, 2024)

    assert result == {}


def test_enrich_ohlcv_with_pit_financials_records_login_failure_status(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "aqsp.data.pit_financial._fetch_pit_financials_with_status",
        lambda symbols, start_year, end_year, cache=None: (
            {},
            type(
                "Status",
                (),
                {
                    "source_id": "baostock",
                    "status": "login_failed",
                    "message": "baostock 未登录成功，财务补充已跳过。",
                },
            )(),
        ),
    )
    monkeypatch.setattr(
        "aqsp.data.pit_financial._load_optional_disclosure_data_with_status",
        lambda symbols, start, end, client=None: (
            {},
            type(
                "Status",
                (),
                {
                    "source_id": "tushare",
                    "status": "missing_env",
                    "message": "TUSHARE_TOKEN is required for tushare PIT data",
                },
            )(),
        ),
    )

    result = enrich_ohlcv_with_pit_financials(
        {},
        ["600519"],
        date(2026, 4, 1),
        date(2026, 6, 30),
    )

    assert [item.status for item in result.source_statuses] == [
        "login_failed",
        "missing_env",
    ]
