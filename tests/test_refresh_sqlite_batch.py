from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from scripts import refresh_sqlite_batch
from scripts.update_sqlite_daily import UpdateSummary


def test_refresh_sqlite_batch_persists_date_summary_and_advances_cursor(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeSource:
        def __init__(self, **_kwargs) -> None:
            pass

        def get_liquid_symbols(self, **_kwargs) -> list[str]:
            return ["600000", "000001", "300001"]

    summary = UpdateSummary(
        updated_rows=3,
        skipped_symbols=0,
        failed_symbols=0,
        target_day=date(2026, 7, 28),
        price_mode="raw",
        target_day_symbol_count=3,
        total_symbols=2,
        raw_max_trade_date=date(2026, 7, 28),
        processed_symbols=2,
    )
    monkeypatch.setattr(refresh_sqlite_batch, "SqliteDbSource", FakeSource)
    monkeypatch.setattr(
        refresh_sqlite_batch, "update_sqlite_daily", lambda *_args, **_kwargs: summary
    )
    state = tmp_path / "cursor.json"

    result = refresh_sqlite_batch.refresh_batch(
        db_path=tmp_path / "market.db",
        state_path=state,
        target_day=date(2026, 7, 28),
        batch_size=2,
        universe_limit=0,
        min_amount=0.0,
        query_timeout_seconds=4.0,
        max_runtime_seconds=120.0,
    )

    assert result == summary
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["target_day"] == "2026-07-28"
    assert payload["offset"] == 2
    assert payload["last_batch"]["raw_max_trade_date"] == "2026-07-28"
