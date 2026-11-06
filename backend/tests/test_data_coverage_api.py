"""The /data page must never present an incomplete cross-section as a market day."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as app_module
import aqsp.data.daily_coverage as daily_coverage
from aqsp.core.errors import DataError
from aqsp.data.daily_coverage import DailyCoverageSummary


@pytest.fixture(autouse=True)
def fixed_threshold(monkeypatch):
    monkeypatch.delenv("AQSP_MIN_MARKET_DAILY_COVERAGE", raising=False)


def _summary(covered: int, total: int, max_date: str | None) -> DailyCoverageSummary:
    return DailyCoverageSummary(
        db_path="fake.db",
        total_symbols=total,
        market_max_date=max_date,
        symbols_on_max_date=covered,
        sentinel_symbols=("000001.SZ",) if covered == 1 else (),
    )


def _patch(monkeypatch, summary: DailyCoverageSummary) -> None:
    monkeypatch.setattr(
        "aqsp.data.source_factory.resolve_sqlite_db_path", lambda: "fake.db"
    )
    monkeypatch.setattr(
        daily_coverage, "summarize_daily_coverage", lambda *_args, **_kwargs: summary
    )


def test_incomplete_cross_section_is_not_published_as_market_date(monkeypatch):
    _patch(monkeypatch, _summary(1, 5728, "20261102"))

    response = TestClient(app_module.app).get("/api/aqsp/data-coverage")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is True
    assert data["market_max_date"] == "20261102"
    assert data["symbols_on_max_date"] == 1
    assert data["total_symbols"] == 5728
    assert data["market_date_usable"] is False
    assert data["verified_market_date"] is None
    assert data["sentinel_symbols"] == ["000001.SZ"]
    assert data["min_coverage_ratio"] == 0.8


def test_complete_cross_section_publishes_verified_date(monkeypatch):
    _patch(monkeypatch, _summary(5728, 5728, "20261102"))

    data = TestClient(app_module.app).get("/api/aqsp/data-coverage").json()["data"]

    assert data["market_date_usable"] is True
    assert data["verified_market_date"] == "20261102"
    assert data["coverage_pct"] == 1.0


def test_missing_database_reports_unavailable(monkeypatch):
    monkeypatch.setattr("aqsp.data.source_factory.resolve_sqlite_db_path", lambda: None)

    data = TestClient(app_module.app).get("/api/aqsp/data-coverage").json()["data"]

    assert data["available"] is False
    assert "未找到" in data["reason"]


def test_unreadable_coverage_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "aqsp.data.source_factory.resolve_sqlite_db_path", lambda: "fake.db"
    )

    def broken(*_args, **_kwargs):
        raise DataError("日线数据库缺少 daily_qfq 表")

    monkeypatch.setattr(daily_coverage, "summarize_daily_coverage", broken)

    response = TestClient(app_module.app).get("/api/aqsp/data-coverage")

    assert response.status_code == 503
    assert "覆盖不可核验" in response.json()["detail"]
