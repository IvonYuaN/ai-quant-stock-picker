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

        def get_available_symbols(self) -> list[str]:
            return ["600000", "000001", "300001"]

        def get_symbol_name(self, symbol: str) -> str:
            return symbol

        def get_symbols_with_daily_coverage(
            self, symbols: list[str], *_args, **_kwargs
        ) -> list[str]:
            return symbols

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
    assert payload["target_day_symbols"] == ["600000", "000001"]
    assert payload["last_batch"]["raw_max_trade_date"] == "2026-07-28"


def test_refresh_sqlite_batch_interleaves_supported_boards_and_excludes_st() -> None:
    class FakeSource:
        def get_available_symbols(self) -> list[str]:
            return ["600001", "688001", "000001", "300001", "600002", "002001"]

        def get_symbol_name(self, symbol: str) -> str:
            return "ST测试" if symbol == "002001" else symbol

    assert refresh_sqlite_batch._refresh_universe(FakeSource(), universe_limit=0) == [
        "600001",
        "000001",
        "300001",
        "600002",
    ]


def test_refresh_sqlite_batch_accumulates_same_day_covered_symbols(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeSource:
        def __init__(self, **_kwargs) -> None:
            pass

        def get_available_symbols(self) -> list[str]:
            return ["600000", "000001", "300001", "600001"]

        def get_symbol_name(self, symbol: str) -> str:
            return symbol

        def get_symbols_with_daily_coverage(
            self, symbols: list[str], *_args, **_kwargs
        ) -> list[str]:
            return symbols

    summary = UpdateSummary(
        updated_rows=2,
        skipped_symbols=0,
        failed_symbols=0,
        target_day=date(2026, 7, 29),
        price_mode="raw",
        target_day_symbol_count=2,
        total_symbols=2,
        raw_max_trade_date=date(2026, 7, 29),
        processed_symbols=2,
    )
    monkeypatch.setattr(refresh_sqlite_batch, "SqliteDbSource", FakeSource)
    monkeypatch.setattr(
        refresh_sqlite_batch, "update_sqlite_daily", lambda *_args, **_kwargs: summary
    )
    state = tmp_path / "cursor.json"
    kwargs = {
        "db_path": tmp_path / "market.db",
        "state_path": state,
        "target_day": date(2026, 7, 29),
        "batch_size": 2,
        "universe_limit": 0,
        "min_amount": 0.0,
        "query_timeout_seconds": 4.0,
        "max_runtime_seconds": 120.0,
    }

    refresh_sqlite_batch.refresh_batch(**kwargs)
    refresh_sqlite_batch.refresh_batch(**kwargs)

    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["target_day_symbols"] == ["600000", "000001", "300001", "600001"]


def test_refresh_sqlite_batch_runs_multiple_chunks_with_shared_summary(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeSource:
        def __init__(self, **_kwargs) -> None:
            pass

        def get_available_symbols(self) -> list[str]:
            return ["600000", "000001", "300001"]

        def get_symbol_name(self, symbol: str) -> str:
            return symbol

    summary = UpdateSummary(
        updated_rows=2,
        skipped_symbols=0,
        failed_symbols=0,
        target_day=date(2026, 7, 29),
        price_mode="raw",
        target_day_symbol_count=3,
        total_symbols=2,
        raw_max_trade_date=date(2026, 7, 29),
        processed_symbols=2,
    )
    calls: list[float] = []

    def fake_refresh_batch(**kwargs) -> UpdateSummary:
        calls.append(float(kwargs["max_runtime_seconds"]))
        return summary

    monkeypatch.setattr(refresh_sqlite_batch, "SqliteDbSource", FakeSource)
    monkeypatch.setattr(refresh_sqlite_batch, "refresh_batch", fake_refresh_batch)

    result = refresh_sqlite_batch.refresh_batches(
        db_path=tmp_path / "market.db",
        state_path=tmp_path / "cursor.json",
        target_day=date(2026, 7, 29),
        batch_size=2,
        universe_limit=0,
        min_amount=0.0,
        query_timeout_seconds=4.0,
        max_runtime_seconds=120.0,
        batches=2,
    )

    assert len(calls) == 2
    assert result.processed_symbols == 4
    assert result.total_symbols == 3
