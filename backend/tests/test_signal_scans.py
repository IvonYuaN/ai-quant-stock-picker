from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as app_module
import signal_definitions
import signal_scans
import tenant


@pytest.fixture
def scan_store(monkeypatch, tmp_path):
    monkeypatch.setattr(signal_scans.pf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(signal_definitions.pf, "CACHE_DIR", str(tmp_path))
    token = tenant.current_tenant.set("local")
    yield tmp_path
    tenant.current_tenant.reset(token)


def _save_signal(*, right: str = "field:open", kind: str = "entry") -> dict:
    return signal_definitions.save_signal(
        signal_id="close_above_open",
        name="收盘高于开盘",
        kind=kind,
        enabled=True,
        conditions=[{"left": "close", "op": ">", "right": right}],
        expected_revision=None,
    )


class RawSource:
    name = "sqlite_db"

    def __init__(
        self, *, stale: bool = False, factors: dict | None = None, second_close: float = 9.0
    ):
        self.stale = stale
        self.factors = factors or {}
        self.second_close = second_close
        self.fetch_calls = 0

    def price_mode(self):
        return "raw"

    def get_latest_daily_date(self):
        return date(2026, 10, 30)

    def get_latest_daily_dates(self, symbols):
        day = date(2026, 10, 29) if self.stale else date(2026, 10, 30)
        return {symbol: day for symbol in symbols}

    def get_point_in_time_adjustment_factors(self, symbols, dates):
        return {symbol: self.factors.get(symbol, {}) for symbol in symbols}

    def fetch_daily(self, symbols, start, end, adjust=""):
        self.fetch_calls += 1
        frame = pd.DataFrame(
            {
                "date": ["2026-10-29", "2026-10-30"],
                "open": [10.0, 10.0],
                "close": [11.0, self.second_close],
                "raw_close": [11.0, self.second_close],
                "high": [11.0, max(10.0, self.second_close)],
                "low": [10.0, min(9.0, self.second_close)],
                "volume": [100.0, 120.0],
                "amount": [1000.0, 1100.0],
            }
        )
        return {symbol: frame.copy() for symbol in symbols}


def test_signal_scans_persist_raw_hits_and_definition_hash(scan_store, monkeypatch):
    _save_signal()
    source = RawSource()
    monkeypatch.setattr(signal_scans, "build_data_source", lambda *_args, **_kwargs: source)

    run = signal_scans.run_signal_scan(
        signal_ids=["close_above_open"],
        symbols=["600519"],
        start="2026-10-29",
        end="2026-10-30",
    )

    assert run["status"] == "ready"
    assert run["price_mode"] == "raw"
    assert run["hits"][0]["date"] == "2026-10-29"
    assert run["hits"][0]["raw_close"] == 11.0
    assert len(run["signal_hashes"]["close_above_open"]) == 64
    assert signal_scans.list_runs() == [run]


def test_signal_scans_report_missing_pit_factors_and_exclude_hits(scan_store, monkeypatch):
    _save_signal(right="field:prev_close")
    monkeypatch.setattr(signal_scans, "build_data_source", lambda *_args, **_kwargs: RawSource())

    run = signal_scans.run_signal_scan(
        signal_ids=["close_above_open"],
        symbols=["600519"],
        start="2026-10-30",
        end="2026-10-30",
    )

    assert run["status"] == "partial"
    assert run["hits"] == []
    assert "缺 PIT 复权因子" in run["blockers"][0]["reason"]


def test_signal_scans_use_point_in_time_factors_for_prior_close(scan_store, monkeypatch):
    _save_signal(right="field:prev_close")
    pit_factors = {
        date(2026, 10, 29): SimpleNamespace(value=1.0),
        date(2026, 10, 30): SimpleNamespace(value=1.0),
    }
    source = RawSource(factors={"600519": pit_factors}, second_close=12.0)
    monkeypatch.setattr(signal_scans, "build_data_source", lambda *_args, **_kwargs: source)

    run = signal_scans.run_signal_scan(
        signal_ids=["close_above_open"],
        symbols=["600519"],
        start="2026-10-30",
        end="2026-10-30",
    )

    assert run["status"] == "ready"
    assert run["hits"][0]["date"] == "2026-10-30"
    assert run["hits"][0]["raw_close"] == 12.0


def test_signal_scans_block_stale_symbol_before_fetch(scan_store, monkeypatch):
    _save_signal()
    source = RawSource(stale=True)
    monkeypatch.setattr(signal_scans, "build_data_source", lambda *_args, **_kwargs: source)

    run = signal_scans.run_signal_scan(
        signal_ids=["close_above_open"],
        symbols=["600519"],
        start="2026-10-29",
        end="2026-10-30",
    )

    assert run["status"] == "blocked"
    assert run["scanned_symbols"] == 0
    assert source.fetch_calls == 0
    assert "早于请求结束日" in run["blockers"][0]["reason"]


def test_signal_scans_are_tenant_scoped_and_deletable(scan_store, monkeypatch):
    _save_signal()
    monkeypatch.setattr(signal_scans, "build_data_source", lambda *_args, **_kwargs: RawSource())
    run = signal_scans.run_signal_scan(
        signal_ids=["close_above_open"],
        symbols=["600519"],
        start="2026-10-29",
        end="2026-10-30",
    )
    token = tenant.current_tenant.set("other-user")
    try:
        assert signal_scans.list_runs() == []
        assert signal_scans.delete_run(run["run_id"]) is False
    finally:
        tenant.current_tenant.reset(token)
    assert signal_scans.delete_run(run["run_id"]) is True
    assert signal_scans.list_runs() == []


def test_signal_scans_require_explicit_retention_cleanup(scan_store):
    for index in range(signal_scans.LIMITS.max_retained_runs):
        signal_scans._persist({"run_id": f"{index:032x}", "generated_at": str(index)})

    with pytest.raises(signal_scans.SignalScanConflict, match="先显式删除"):
        signal_scans._persist({"run_id": "f" * 32, "generated_at": "next"})
    assert signal_scans.delete_run("0" * 32)
    signal_scans._persist({"run_id": "f" * 32, "generated_at": "next"})
    assert len(signal_scans.list_runs()) == signal_scans.LIMITS.max_retained_runs


def test_signal_scans_reject_exit_only_disabled_or_oversized_requests(scan_store):
    _save_signal(kind="exit")
    with pytest.raises(ValueError, match="退出信号"):
        signal_scans._definitions(["close_above_open"])
    with pytest.raises(ValueError, match="1 到 20 个"):
        signal_scans._parse_request(
            signal_ids=["close_above_open"],
            symbols=[f"{index:06d}" for index in range(21)],
            start="2026-10-29",
            end="2026-10-30",
        )


def test_signal_scans_api_runs_lists_and_deletes_with_tenant_isolation(scan_store, monkeypatch):
    client = TestClient(app_module.app)
    owner = {"X-User-Id": "signal-scan-owner"}
    other = {"X-User-Id": "signal-scan-other"}
    definition = client.post(
        "/api/aqsp/signal-definitions",
        headers=owner,
        json={
            "id": "close_above_open",
            "name": "收盘高于开盘",
            "kind": "entry",
            "enabled": True,
            "conditions": [{"left": "close", "op": ">", "right": "field:open"}],
        },
    )
    assert definition.status_code == 200
    monkeypatch.setattr(signal_scans, "build_data_source", lambda *_args, **_kwargs: RawSource())

    scanned = client.post(
        "/api/aqsp/signal-scans",
        headers=owner,
        json={
            "signal_ids": ["close_above_open"],
            "symbols": ["600519"],
            "start": "2026-10-29",
            "end": "2026-10-30",
        },
    )
    assert scanned.status_code == 200
    run_id = scanned.json()["data"]["run_id"]
    assert len(client.get("/api/aqsp/signal-scans", headers=owner).json()["data"]["items"]) == 1
    assert client.get("/api/aqsp/signal-scans", headers=other).json()["data"]["items"] == []
    assert client.delete(f"/api/aqsp/signal-scans/{run_id}", headers=other).status_code == 404
    assert client.delete(f"/api/aqsp/signal-scans/{run_id}", headers=owner).status_code == 200
