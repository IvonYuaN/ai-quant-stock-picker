from __future__ import annotations

import json

import pandas as pd

from aqsp.ledger.runtime import (
    collect_independent_signal_dates,
    collect_paper_tracking_dates,
    compute_paper_mark_to_market_pnl,
    compute_real_pnl,
    count_independent_signal_days,
    count_paper_tracking_days,
    strategy_executability_weight_adjustments,
)


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

    assert collect_independent_signal_dates(str(ledger)) == {
        "2026-06-01",
        "2026-06-02",
    }
    assert count_independent_signal_days(str(ledger)) == 2


def test_count_independent_signal_days_ignores_backfill_no_pick_markers(
    tmp_path,
) -> None:
    ledger = tmp_path / "predictions.jsonl"
    rows = [
        {
            "signal_date": "2026-06-01",
            "symbol": "__RUN__",
            "status": "backfill_no_picks",
            "event_type": "backfill_no_picks",
        },
        {
            "signal_date": "2026-06-02",
            "symbol": "600036",
            "thresholds_version": "1.1.1",
            "status": "watch_only",
        },
    ]
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    assert count_independent_signal_days(str(ledger)) == 1


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


def test_count_independent_signal_days_rejects_unknown_status(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    rows = [
        {
            "signal_date": "2026-06-03",
            "symbol": "600036",
            "status": "paper_only",
        },
        {
            "signal_date": "2026-06-04",
            "symbol": "000001",
            "rating": "watch",
        },
    ]
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    assert count_independent_signal_days(str(ledger)) == 1


def test_count_independent_signal_days_rejects_paper_only_statuses(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    rows = [
        {
            "signal_date": "2026-06-03",
            "symbol": "600036",
            "status": "open",
            "score": 60,
        },
        {
            "signal_date": "2026-06-04",
            "symbol": "000001",
            "status": "closed",
            "score": 61,
        },
        {
            "signal_date": "2026-06-05",
            "symbol": "601318",
            "status": "pending_entry",
            "score": 62,
        },
        {
            "signal_date": "2026-06-06",
            "symbol": "300750",
            "status": "pending",
            "score": 63,
        },
    ]
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    assert count_independent_signal_days(str(ledger)) == 1


def test_count_paper_tracking_days_counts_real_paper_events(tmp_path) -> None:
    ledger = tmp_path / "paper_trades.jsonl"
    rows = [
        {
            "signal_date": "2026-06-01",
            "symbol": "600036",
            "status": "open",
        },
        {
            "signal_date": "2026-06-02",
            "symbol": "000001",
            "status": "closed",
        },
        {
            "signal_date": "2026-06-03",
            "symbol": "601318",
            "status": "not_executable",
        },
        {
            "signal_date": "2026-06-04",
            "symbol": "300750",
            "status": "watch_only",
        },
        {
            "signal_date": "2026-06-05",
            "symbol": "688981",
            "status": "closed",
            "is_simulated": True,
        },
        {
            "signal_date": "2026-06-06",
            "symbol": "600519",
            "rating": "buy_candidate",
        },
    ]
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    assert collect_paper_tracking_dates(str(ledger)) == {
        "2026-06-01",
        "2026-06-02",
        "2026-06-03",
    }
    assert count_paper_tracking_days(str(ledger)) == 3


def test_compute_real_pnl_aggregates_latest_day_week_and_month(
    monkeypatch, tmp_path
) -> None:
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


def test_compute_paper_mark_to_market_pnl_uses_open_positions(
    monkeypatch, tmp_path
) -> None:
    from datetime import datetime

    monkeypatch.setattr(
        "aqsp.ledger.runtime.now_shanghai",
        lambda: datetime.fromisoformat("2026-06-17T18:00:00+08:00"),
    )
    ledger = tmp_path / "paper_trades.jsonl"
    rows = [
        {
            "status": "open",
            "signal_date": "2026-06-16",
            "symbol": "600519",
            "entry_price": 100.0,
        },
        {
            "status": "closed",
            "signal_date": "2026-06-17",
            "symbol": "300750",
            "entry_price": 100.0,
        },
    ]
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    pnl = compute_paper_mark_to_market_pnl(
        str(ledger),
        {
            "600519": pd.DataFrame(
                [
                    {"date": "2026-06-16", "close": 98.0},
                    {"date": "2026-06-17", "close": 91.5},
                ]
            )
        },
    )

    assert pnl is not None
    daily_pnl, weekly_pnl, monthly_pnl = pnl
    assert round(daily_pnl, 2) == -6.63
    assert round(weekly_pnl, 2) == -8.5
    assert round(monthly_pnl, 2) == -8.5


def test_compute_paper_mark_to_market_pnl_uses_period_start_prices(
    monkeypatch, tmp_path
) -> None:
    from datetime import datetime

    monkeypatch.setattr(
        "aqsp.ledger.runtime.now_shanghai",
        lambda: datetime.fromisoformat("2026-06-17T18:00:00+08:00"),
    )
    ledger = tmp_path / "paper_trades.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "status": "open",
                "entry_date": "2026-05-01",
                "symbol": "600519",
                "entry_price": 100.0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    pnl = compute_paper_mark_to_market_pnl(
        str(ledger),
        {
            "600519": pd.DataFrame(
                [
                    {"date": "2026-05-15", "close": 90.0},
                    {"date": "2026-06-10", "close": 105.0},
                    {"date": "2026-06-16", "close": 100.0},
                    {"date": "2026-06-17", "close": 95.0},
                ]
            )
        },
    )

    assert pnl is not None
    daily_pnl, weekly_pnl, monthly_pnl = pnl
    assert round(daily_pnl, 2) == -5.0
    assert round(weekly_pnl, 2) == -9.52
    assert round(monthly_pnl, 2) == 5.56


def test_strategy_executability_weight_adjustments_penalizes_blocked_strategy(
    tmp_path,
) -> None:
    ledger = tmp_path / "predictions.jsonl"
    rows = [
        {
            "status": "not_executable" if idx < 3 else "validated",
            "symbol": f"600{idx:03d}",
            "signal_date": f"2026-06-{idx + 1:02d}",
            "strategies": ["limit_up_ladder"],
        }
        for idx in range(5)
    ]
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    adjustments, reasons = strategy_executability_weight_adjustments(str(ledger))

    assert adjustments == {"limit_up_ladder": 0.5}
    assert "60%" in reasons["limit_up_ladder"]


def test_strategy_executability_weight_adjustments_ignores_unresolved_rows(
    tmp_path,
) -> None:
    ledger = tmp_path / "predictions.jsonl"
    rows = [
        {
            "status": "not_executable" if idx < 3 else "validated",
            "symbol": f"600{idx:03d}",
            "signal_date": f"2026-06-{idx + 1:02d}",
            "strategies": ["limit_up_ladder"],
        }
        for idx in range(5)
    ]
    rows.extend(
        {
            "status": "pending" if idx % 2 == 0 else "watch_only",
            "symbol": f"000{idx:03d}",
            "signal_date": f"2026-06-{idx + 6:02d}",
            "strategies": ["limit_up_ladder"],
        }
        for idx in range(20)
    )
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    adjustments, reasons = strategy_executability_weight_adjustments(str(ledger))

    assert adjustments == {"limit_up_ladder": 0.5}
    assert "60% (3/5)" in reasons["limit_up_ladder"]
