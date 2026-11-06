from __future__ import annotations

from types import SimpleNamespace
from datetime import date

import app as app_module
import auction_benchmark
import pandas as pd
import pytest
from aqsp.data import source_factory
from fastapi.testclient import TestClient


def test_auction_benchmark_returns_unavailable_when_provider_key_is_missing(monkeypatch):
    monkeypatch.delenv("FUYAO_API_KEY", raising=False)

    result = auction_benchmark.get_auction_benchmark()

    assert result["state"] == "source_unavailable"
    assert result["source"] == "fuyao"
    assert result["items"] == []
    assert "FUYAO_API_KEY" in result["message"]


def test_auction_benchmark_normalizes_rows_when_provider_returns_valid_data(monkeypatch):
    monkeypatch.setenv("FUYAO_API_KEY", "test-secret")
    calls = []
    envelope = {
        "code": 0,
        "data": {
            "date": "2026-11-02",
            "item": [
                {
                    "thscode": "600000.SH",
                    "ticker": "600000",
                    "name": "浦发银行",
                    "auction_pct": "1.25",
                    "tags": ["金融"],
                },
                {"ticker": "invalid"},
            ],
        },
    }

    def fetch(url, *, params, headers, timeout):
        calls.append((url, params, headers, timeout))
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: envelope,
        )

    monkeypatch.setattr(auction_benchmark.requests, "get", fetch)

    result = auction_benchmark.get_auction_benchmark()

    assert calls == [
        (
            "https://fuyao.aicubes.cn/api/a-share/auction/short-term-benchmark",
            {},
            {"X-api-key": "test-secret"},
            10,
        )
    ]
    assert result["state"] == "ok"
    assert result["trade_date"] == "2026-11-02"
    assert result["count"] == 1
    assert result["items"] == [
        {
            "symbol": "600000",
            "thscode": "600000.SH",
            "name": "浦发银行",
            "auction_pct": 1.25,
            "tags": ["金融"],
            "day0_oc": None,
            "d1_pct": None,
        }
    ]
    assert "test-secret" not in str(result)


def test_auction_benchmark_enriches_returns_when_raw_and_pit_data_are_complete(monkeypatch):
    trade_date = date(2026, 11, 2)
    next_date = date(2026, 11, 3)

    class RawSource:
        name = "sqlite_db"

        def get_latest_daily_date(self):
            return next_date

        def fetch_daily(self, symbols, start, end, *, adjust):
            assert symbols == ["600000"]
            assert start == trade_date
            assert end == next_date
            assert adjust == ""
            return {
                "600000": pd.DataFrame(
                    {
                        "date": [trade_date.isoformat(), next_date.isoformat()],
                        "open": [10.0, 11.0],
                        "close": [10.5, 11.55],
                    }
                )
            }

        def get_available_daily_dates(self, start, end):
            assert start == trade_date
            assert end == next_date
            return [trade_date, next_date]

        def get_point_in_time_adjustment_factors(self, symbols, dates):
            assert symbols == ["600000"]
            assert dates == [trade_date, next_date]
            return {
                "600000": {
                    trade_date: SimpleNamespace(value=1.0),
                    next_date: SimpleNamespace(value=1.05),
                }
            }

    monkeypatch.setattr(source_factory, "build_data_source", lambda *_args, **_kwargs: RawSource())

    items, as_of = auction_benchmark._enrich_raw_returns(
        [{"symbol": "600000", "day0_oc": None, "d1_pct": None}], trade_date
    )

    assert items[0]["day0_oc"] == pytest.approx(0.05)
    assert items[0]["d1_pct"] == pytest.approx(0.155)
    assert as_of == next_date.isoformat()


def test_auction_benchmark_omits_next_day_return_when_pit_factor_is_missing(monkeypatch):
    trade_date = date(2026, 11, 2)
    next_date = date(2026, 11, 3)

    class RawSource:
        def get_latest_daily_date(self):
            return next_date

        def fetch_daily(self, *_args, **_kwargs):
            return {
                "600000": pd.DataFrame(
                    {
                        "date": [trade_date.isoformat(), next_date.isoformat()],
                        "open": [10.0, 11.0],
                        "close": [10.5, 11.55],
                    }
                )
            }

        def get_available_daily_dates(self, *_args, **_kwargs):
            return [trade_date, next_date]

        def get_point_in_time_adjustment_factors(self, *_args, **_kwargs):
            return {"600000": {trade_date: SimpleNamespace(value=1.0)}}

    monkeypatch.setattr(source_factory, "build_data_source", lambda *_args, **_kwargs: RawSource())

    items, _ = auction_benchmark._enrich_raw_returns(
        [{"symbol": "600000", "day0_oc": None, "d1_pct": None}], trade_date
    )

    assert items[0]["day0_oc"] == pytest.approx(0.05)
    assert items[0]["d1_pct"] is None


def test_auction_benchmark_endpoint_preserves_date_when_requested(monkeypatch):
    expected = {
        "state": "ok",
        "source": "fuyao",
        "requested_date": "2026-11-02",
        "trade_date": "2026-11-02",
        "count": 0,
        "items": [],
    }
    calls = []
    monkeypatch.setattr(
        auction_benchmark,
        "get_auction_benchmark",
        lambda requested_date: calls.append(requested_date) or expected,
    )

    response = TestClient(app_module.app).get(
        "/api/aqsp/auction-benchmark?date=2026-11-02"
    )

    assert response.status_code == 200
    assert response.json() == {"data": expected}
    assert calls[0].isoformat() == "2026-11-02"
