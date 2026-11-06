from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from fastapi.testclient import TestClient

import app as app_module


def test_stock_levels_requests_raw_bars_and_returns_provenance(monkeypatch, tmp_path: Path):
    from aqsp.data import cache as cache_module
    from aqsp.data import source_factory

    class Source:
        request = None

        def set_workload(self, value: str) -> None:
            assert value == "historical"

        def fetch_daily(self, symbols, start, end, adjust=""):
            self.request = (symbols, start, end, adjust)
            rows = []
            for offset in range(25):
                day = end - timedelta(days=24 - offset)
                close = 10 + offset / 10
                rows.append({
                    "date": day.isoformat(),
                    "open": close - 0.1,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                })
            rows.append({
                "date": "2026-10-31",
                "open": 99.9,
                "high": 100.5,
                "low": 99.5,
                "close": 100,
            })
            frame = pd.DataFrame(rows)
            frame.attrs.update(
                source_name="synthetic-raw-source",
                fetched_at="2026-10-31T10:00:00+08:00",
            )
            return {"600519": frame}

    source = Source()
    monkeypatch.setattr(source_factory, "build_data_source", lambda *_args, **_kwargs: source)
    factor_requests = []

    class Cache:
        def __init__(self, _path=None):
            pass

        def get_adj_factor_record(self, symbol, requested_date):
            assert symbol == "600519"
            factor_requests.append(requested_date)
            return SimpleNamespace(
                date=min(requested_date, date(2026, 10, 29)),
                value=1.0,
                source="pit-fixture",
                fetched_at="2026-10-31T09:00:00+08:00",
            )

    monkeypatch.setattr(cache_module, "DataCache", Cache)
    monkeypatch.setattr(
        "aqsp.core.time.latest_completed_trading_day",
        lambda instant: date(2026, 10, 30),
    )

    response = TestClient(app_module.app).get(
        "/api/stock-levels?code=600519&as_of=2026-10-30"
    )

    assert response.status_code == 200
    assert source.request[0] == ["600519"]
    assert source.request[3] == ""
    payload = response.json()["data"]
    assert payload["price_basis"] == "pit_as_of"
    assert payload["source"] == "synthetic-raw-source"
    assert payload["adjustment_factor_source"] == "pit-fixture"
    assert payload["adjustment_factor_as_of"] == "2026-10-29"
    assert payload["available_rows"] == 25
    assert max(factor_requests) == date(2026, 10, 30)
    assert payload["as_of"] == "2026-10-30"
    assert len(payload["levels"]) == 11


def test_stock_levels_rejects_future_as_of(monkeypatch):
    from aqsp.core.time import now_shanghai

    tomorrow = now_shanghai().date() + timedelta(days=1)
    response = TestClient(app_module.app).get(
        "/api/stock-levels?code=600519&as_of=" + tomorrow.isoformat()
    )

    assert response.status_code == 400
