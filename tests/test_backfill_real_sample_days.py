from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from aqsp.core.types import PickResult
from scripts import backfill_real_sample_days as backfill


def test_build_backfill_plan_skips_existing_days(monkeypatch) -> None:
    monkeypatch.setattr(
        backfill,
        "is_trading_day",
        lambda day: day.weekday() < 5,
    )

    plan = backfill.build_backfill_plan(
        start_date=date(2026, 6, 2),
        end_date=date(2026, 6, 8),
        existing_signal_days={"2026-06-03"},
        existing_paper_days={"2026-06-04"},
        max_days=10,
    )

    assert [day.isoformat() for day in plan.trading_days] == [
        "2026-06-02",
        "2026-06-05",
        "2026-06-08",
    ]


def test_truncate_frames_to_date_filters_future_rows_and_keeps_tail() -> None:
    frame = pd.DataFrame(
        [
            {"date": "2026-06-01", "close": 1.0},
            {"date": "2026-06-02", "close": 2.0},
            {"date": "2026-06-03", "close": 3.0},
            {"date": "2026-06-04", "close": 4.0},
        ]
    )

    trimmed = backfill.truncate_frames_to_date(
        {"600000": frame},
        end_date=date(2026, 6, 3),
        lookback_days=2,
    )

    assert trimmed["600000"]["date"].tolist() == ["2026-06-02", "2026-06-03"]


def test_collect_paper_sync_symbols_dedupes_and_skips_run_marker(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    ledger_path.write_text(
        "\n".join(
            [
                json.dumps({"symbol": "__RUN__", "signal_date": "2026-06-02"}),
                json.dumps({"symbol": "600000", "signal_date": "2026-06-02"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text(
        json.dumps({"symbol": "600519", "signal_date": "2026-06-03"}) + "\n",
        encoding="utf-8",
    )

    picks = [
        PickResult(
            symbol="600000",
            name="浦发银行",
            date="2026-06-04",
            close=10.0,
            score=1.0,
            rating="watch",
            entry_type="next_open",
            ideal_buy=10.0,
            stop_loss=9.5,
            take_profit=11.0,
            position="10%",
        ),
        PickResult(
            symbol="601318",
            name="中国平安",
            date="2026-06-04",
            close=20.0,
            score=2.0,
            rating="watch",
            entry_type="next_open",
            ideal_buy=20.0,
            stop_loss=19.0,
            take_profit=22.0,
            position="10%",
        ),
    ]

    symbols = backfill.collect_paper_sync_symbols(
        ledger_path=ledger_path,
        paper_ledger_path=paper_path,
        new_picks=picks,
    )

    assert symbols == ["600000", "600519", "601318"]


def test_resolve_backfill_symbols_uses_sqlite_source_directly() -> None:
    class DummySqliteSource:
        def get_available_symbols(self):
            return ["600000", "600519", "601318"]

        def get_symbols_with_daily_coverage(self, symbols, start, end, min_rows=None):
            assert min_rows is None
            return symbols[:2]

    symbols = backfill.resolve_backfill_symbols(
        source_name="sqlite_db",
        source=DummySqliteSource(),
        explicit_symbols="",
        pool_name="",
        signal_day=date(2026, 6, 23),
        max_universe=0,
        min_avg_amount=50_000_000,
    )

    assert symbols == ["600000", "600519"]
