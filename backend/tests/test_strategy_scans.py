from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import strategy_definitions
import strategy_scans
import tenant
import app as app_module
from fastapi.testclient import TestClient


@pytest.fixture
def scan_store(monkeypatch, tmp_path):
    monkeypatch.setattr(strategy_scans.pf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(strategy_definitions.pf, "CACHE_DIR", str(tmp_path))
    token = tenant.current_tenant.set("local")
    yield tmp_path
    tenant.current_tenant.reset(token)


def _save(expression: str = "close > ma20 and volume_ratio >= 1") -> dict:
    return strategy_definitions.save_strategy(
        strategy_id="daily_trend",
        name="日线趋势策略",
        description="收盘位于均线之上且量比有效",
        expression=expression,
        enabled=True,
        expected_revision=None,
    )


class RawStrategySource:
    name = "sqlite_db"

    def __init__(self, *, stale: set[str] | None = None, missing_pit: set[str] | None = None):
        self.stale = stale or set()
        self.missing_pit = missing_pit or set()
        self.fetch_calls = 0

    def price_mode(self):
        return "raw"

    def get_latest_daily_date(self):
        return date(2026, 10, 30)

    def get_available_daily_dates(self, start, end):
        return [end] if end == date(2026, 10, 30) else []

    def get_latest_daily_dates(self, symbols):
        return {
            symbol: date(2026, 10, 29) if symbol in self.stale else date(2026, 10, 30)
            for symbol in symbols
        }

    def get_available_symbols(self):
        return ["600001", "600002", "830001", "510300", "600003"]

    def get_symbol_name(self, symbol):
        return {"600001": "甲公司", "600002": "乙公司", "600003": "ST丙"}.get(symbol, symbol)

    def get_symbols_with_daily_coverage(self, symbols, start, end, **_kwargs):
        return list(symbols)

    def fetch_daily(self, symbols, start, end, adjust=""):
        self.fetch_calls += 1
        days = pd.bdate_range(end=end, periods=80)
        frames = {}
        for symbol in symbols:
            dates = days[:-1] if symbol in self.stale else days
            close = [20.0 + index * 0.15 + (index % 4) * 0.07 for index in range(len(dates))]
            frames[symbol] = pd.DataFrame(
                {
                    "date": dates.strftime("%Y-%m-%d"),
                    "open": [value - 0.1 for value in close],
                    "high": [value + 0.5 for value in close],
                    "low": [value - 0.6 for value in close],
                    "close": close,
                    "raw_close": close,
                    "volume": [1000.0] * len(close),
                    "amount": [25000.0] * len(close),
                }
            )
        return frames

    def get_point_in_time_adjustment_factors(self, symbols, dates):
        return {
            symbol: {
                day: SimpleNamespace(value=1.0)
                for day in dates
            }
            for symbol in symbols
            if symbol not in self.missing_pit
        }


def _prepare_run(strategy: dict, symbols: list[str]) -> tuple[str, Path]:
    run_id = "a" * 32
    run_dir = strategy_scans._root() / run_id
    run_dir.mkdir(parents=True)
    config = {
        "run_id": run_id,
        "strategy": strategy,
        "strategy_hash": strategy["expression_sha256"],
        "as_of": "2026-10-30",
        "market_as_of": "2026-10-30",
        "universe_as_of": "2026-10-29",
        "symbols": symbols,
    }
    (run_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    state_path = run_dir / "state.json"
    strategy_scans._write(
        state_path,
        {"run_id": run_id, "pid": os.getpid(), "status": "running", "processed_symbols": 0},
    )
    return run_id, state_path


def test_strategy_scans_run_full_universe_with_raw_hits_and_exports(scan_store, monkeypatch):
    strategy = _save()
    source = RawStrategySource()
    monkeypatch.setattr(strategy_scans, "_source", lambda: source)
    run_id, state_path = _prepare_run(strategy, ["600001", "600002"])

    assert strategy_scans.execute_run(run_id, state_path, "local") == 0

    run = strategy_scans.get_run(run_id)
    assert run["status"] == "completed"
    assert run["coverage_status"] == "ready"
    assert run["result"]["universe_size"] == 2
    assert run["result"]["hit_count"] == 2
    assert run["result"]["hits"][0]["raw_close"] > 0
    assert "600001" in strategy_scans.export_csv(run_id, view="hits")
    assert "no_match" not in strategy_scans.export_csv(run_id, view="coverage")


def test_strategy_scans_fail_closed_on_stale_bars_and_missing_pit(scan_store, monkeypatch):
    strategy = _save("close > ma20")
    source = RawStrategySource(stale={"600001"}, missing_pit={"600002"})
    monkeypatch.setattr(strategy_scans, "_source", lambda: source)
    run_id, state_path = _prepare_run(strategy, ["600001", "600002"])

    strategy_scans.execute_run(run_id, state_path, "local")
    result = strategy_scans.get_run(run_id)["result"]

    assert result["status"] == "partial"
    assert result["hit_count"] == 0
    assert result["coverage_counts"] == {"missing_pit": 1, "stale": 1}


def test_strategy_scans_results_are_tenant_scoped_and_explicitly_deletable(scan_store, monkeypatch):
    strategy = _save()
    monkeypatch.setattr(strategy_scans, "_source", lambda: RawStrategySource())
    run_id, state_path = _prepare_run(strategy, ["600001"])
    strategy_scans.execute_run(run_id, state_path, "local")
    token = tenant.current_tenant.set("u_" + tenant.tenant_hash("other-user"))
    try:
        assert strategy_scans.get_run(run_id) is None
        assert strategy_scans.delete_run(run_id) is False
    finally:
        tenant.current_tenant.reset(token)
    assert strategy_scans.delete_run(run_id)
    assert strategy_scans.get_run(run_id) is None


def test_strategy_scans_api_starts_tenant_task_from_saved_definition(
    scan_store, monkeypatch
):
    client = TestClient(app_module.app)
    owner = {"X-User-Id": "strategy-scan-owner"}
    other = {"X-User-Id": "strategy-scan-other"}
    saved = client.post(
        "/api/aqsp/strategy-definitions",
        headers=owner,
        json={
            "id": "daily_trend",
            "name": "日线趋势策略",
            "description": "",
            "expression": "volume_ratio > 1",
            "enabled": True,
        },
    )
    assert saved.status_code == 200
    source = RawStrategySource()
    monkeypatch.setattr(strategy_scans, "_source", lambda: source)
    monkeypatch.setattr(
        "aqsp.data.daily_universe.latest_available_daily_day",
        lambda _source, *, not_after: date(2026, 10, 29),
    )
    monkeypatch.setattr(
        strategy_scans.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=987654),
    )

    response = client.post(
        "/api/aqsp/strategy-scans",
        headers=owner,
        json={"strategy_id": "daily_trend", "as_of": "2026-10-30"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "running"
    assert response.json()["data"]["universe_size"] == 3
    run_id = response.json()["data"]["run_id"]
    assert client.get(f"/api/aqsp/strategy-scans/{run_id}", headers=other).status_code == 404
