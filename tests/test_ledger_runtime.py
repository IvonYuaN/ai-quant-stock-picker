from __future__ import annotations

import json

from aqsp.ledger.runtime import compute_real_pnl, count_independent_signal_days


def test_count_independent_signal_days_counts_observation_only_signal_days(
    tmp_path,
) -> None:
    ledger = tmp_path / "predictions.jsonl"
    rows = [
        {
            "signal_date": "2026-06-01",
            "symbol": "600036",
            "thresholds_version": "1.1.1",
            "status": "watch_only",
        },
        {
            "signal_date": "2026-06-02",
            "symbol": "000001",
            "thresholds_version": "1.1.1",
            "status": "not_executable",
        },
        {
            "signal_date": "2026-06-02",
            "symbol": "601318",
            "thresholds_version": "1.1.1",
            "status": "pending",
        },
        {"signal_date": "", "symbol": "bad", "thresholds_version": "1.1.1"},
        {"signal_date": "2026-06-03", "symbol": "legacy_without_thresholds"},
        {
            "signal_date": "2026-06-04",
            "symbol": "000002",
            "status": "pending",
            "is_simulated": True,
        },
    ]
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    assert count_independent_signal_days(str(ledger)) == 2


def test_count_independent_signal_days_counts_runtime_date_aliases(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    rows = [
        {
            "signal_day_group": "2026-06-03_ma_pullback",
            "symbol": "600036",
            "status": "watch_only",
        },
        {
            "created_at": "2026-06-04T18:00:00+08:00",
            "symbol": "000001",
            "rating": "watch",
        },
        {
            "date": "2026-06-05",
            "symbol": "601318",
            "score": 51.0,
        },
        {
            "created_at": "2026-06-06T18:00:00+08:00",
            "symbol": "300750",
            "status": "not_executable",
        },
    ]
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    assert count_independent_signal_days(str(ledger)) == 3


def test_compute_real_pnl_aggregates_latest_day_week_and_month(monkeypatch, tmp_path) -> None:
    from datetime import datetime

    monkeypatch.setattr(
        "aqsp.ledger.runtime.now_shanghai",
        lambda: datetime.fromisoformat("2026-06-17T18:00:00+08:00"),
    )
    ledger = tmp_path / "predictions.jsonl"
    rows = [
        {"status": "validated", "signal_date": "2026-06-10", "return_pct": 10.0},
        {"status": "validated", "signal_date": "2026-06-17", "return_pct": 5.0},
        {"status": "validated", "signal_date": "2026-06-17", "return_pct": -2.0},
        {"status": "pending", "signal_date": "2026-06-17", "return_pct": 99.0},
    ]
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    daily_pnl, weekly_pnl, monthly_pnl = compute_real_pnl(str(ledger))

    assert round(daily_pnl, 2) == 2.9
    assert round(weekly_pnl, 2) == 13.19
    assert round(monthly_pnl, 2) == 13.19
