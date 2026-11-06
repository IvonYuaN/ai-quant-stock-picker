from __future__ import annotations

from datetime import date, datetime, timedelta

import app as app_module
import abnormal_deviation
from aqsp.data.cache import AdjustmentFactorRecord
from fastapi.testclient import TestClient
import pandas as pd


def _fixture_inputs():
    sessions = [date(2026, 7, 1) + timedelta(days=index) for index in range(31)]
    current_date = sessions[-1]
    stock_closes = [100.0] * 28 + [115.0] * 3
    stock = pd.DataFrame(
        {
            "date": [day.isoformat() for day in sessions],
            "name": ["样例股份"] * len(sessions),
            "close": stock_closes,
            "volume": [1000.0] * len(sessions),
            "suspended": [False] * len(sessions),
        }
    )
    benchmark = pd.DataFrame(
        {
            "date": [day.isoformat() for day in sessions],
            "close": [100.0] * len(sessions),
        }
    )
    factors = {
        "600001": {
            day: AdjustmentFactorRecord(
                date=day,
                value=1.0,
                source="fixture",
                fetched_at="2026-11-02T16:00:00+08:00",
            )
            for day in sessions
        }
    }
    return sessions, current_date, {"600001": stock}, {"000002": benchmark}, factors


def test_abnormal_deviation_computes_board_threshold_and_fallback_when_pit_is_complete():
    sessions, current_date, stocks, indexes, factors = _fixture_inputs()

    result = abnormal_deviation.build_abnormal_deviation_snapshot(
        stocks,
        indexes,
        session_dates=sessions,
        pit_adjustment_factors=factors,
        generated_at=datetime.fromisoformat("2026-11-02T16:00:00+08:00"),
        expected_as_of=current_date,
        requested_symbols=1,
    )

    assert result["availability"] == "ready"
    assert result["benchmark_selection"]["SH"] == {
        "symbol": "000002",
        "fallback": False,
    }
    assert result["benchmark_selection"]["GEM"]["fallback"] is True
    assert result["window_coverage"]["3d"]["computed"] == 1
    assert result["window_coverage"]["3d"]["index_pairs"] == 1
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["windows"]["3d"]["deviation"] == 0.15
    assert row["windows"]["3d"]["threshold"] == 0.20
    assert row["status"] == "edge"


def test_abnormal_deviation_blocks_empty_results_when_pit_factors_are_missing():
    sessions, current_date, stocks, indexes, _factors = _fixture_inputs()

    result = abnormal_deviation.build_abnormal_deviation_snapshot(
        stocks,
        indexes,
        session_dates=sessions,
        pit_adjustment_factors={},
        generated_at=datetime.fromisoformat("2026-11-02T16:00:00+08:00"),
        expected_as_of=current_date,
        requested_symbols=1,
    )

    assert result["availability"] == "blocked"
    assert "missing_pit_factors" in result["blocking_reasons"]
    assert result["window_coverage"]["3d"]["raw_pairs"] == 1
    assert result["window_coverage"]["3d"]["pit_pairs"] == 0
    assert result["window_coverage"]["3d"]["index_pairs"] == 1
    assert result["rows"] == []


def test_abnormal_deviation_blocks_missing_benchmark_date_when_index_series_is_incomplete():
    sessions, current_date, stocks, indexes, factors = _fixture_inputs()
    indexes["000002"] = indexes["000002"].iloc[:-1].copy()

    result = abnormal_deviation.build_abnormal_deviation_snapshot(
        stocks,
        indexes,
        session_dates=sessions,
        pit_adjustment_factors=factors,
        generated_at=datetime.fromisoformat("2026-11-02T16:00:00+08:00"),
        expected_as_of=current_date,
        requested_symbols=1,
    )

    assert result["availability"] == "blocked"
    assert "missing_benchmark_dates" in result["blocking_reasons"]
    assert result["window_coverage"]["3d"]["pit_pairs"] == 1
    assert result["window_coverage"]["3d"]["index_pairs"] == 0
    assert result["rows"] == []


def test_abnormal_deviation_marks_calculable_sample_as_partial_when_universe_coverage_is_incomplete():
    sessions, current_date, stocks, indexes, factors = _fixture_inputs()

    result = abnormal_deviation.build_abnormal_deviation_snapshot(
        stocks,
        indexes,
        session_dates=sessions,
        pit_adjustment_factors=factors,
        generated_at=datetime.fromisoformat("2026-11-02T16:00:00+08:00"),
        expected_as_of=current_date,
        requested_symbols=2,
    )

    assert result["availability"] == "partial"
    assert result["blocking_reasons"] == ["partial_market_coverage"]
    assert result["current_bar_symbols"] == 1
    assert result["window_coverage"]["3d"]["computed"] == 1
    assert len(result["rows"]) == 1


def test_abnormal_deviation_api_returns_source_stamped_snapshot(monkeypatch):
    expected = {
        "availability": "blocked",
        "source": "sqlite_db",
        "price_mode": "raw",
        "as_of": "2026-09-30",
        "blocking_reasons": ["missing_pit_factors"],
        "rows": [],
    }
    calls = []
    monkeypatch.setattr(
        abnormal_deviation,
        "scan_local_abnormal_deviations",
        lambda **kwargs: calls.append(kwargs) or expected,
    )

    response = TestClient(app_module.app).get(
        "/api/aqsp/abnormal-deviation?min_closeness=0.7&limit=50"
    )

    assert response.status_code == 200
    assert response.json() == {"data": expected}
    assert calls == [{"min_closeness": 0.7, "limit": 50}]
