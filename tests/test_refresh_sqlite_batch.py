from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from scripts import refresh_sqlite_batch
from scripts.update_sqlite_daily import UpdateSummary


def test_refresh_sqlite_batch_uses_temporary_exit_for_unpublished_target_day(
    monkeypatch, tmp_path: Path
) -> None:
    summary = UpdateSummary(
        updated_rows=0,
        skipped_symbols=1,
        failed_symbols=0,
        target_day=date(2026, 7, 30),
        price_mode="raw",
        target_day_symbol_count=0,
        total_symbols=1,
        raw_max_trade_date=date(2026, 7, 29),
        processed_symbols=1,
        empty_response_symbols=1,
    )
    monkeypatch.setattr(
        refresh_sqlite_batch, "refresh_batches", lambda **_kwargs: summary
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "refresh_sqlite_batch.py",
            "--db",
            str(tmp_path / "db"),
            "--state",
            str(tmp_path / "state"),
        ],
    )

    assert refresh_sqlite_batch.main() == 75


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


def test_refresh_sqlite_batch_excludes_historical_symbols_from_active_baseline() -> (
    None
):
    class FakeSource:
        def get_available_symbols(self) -> list[str]:
            return ["600000", "000001", "300001", "600001"]

        def get_symbol_name(self, symbol: str) -> str:
            return symbol

        def get_symbols_with_daily_coverage(
            self, symbols: list[str], *_args, **_kwargs
        ) -> list[str]:
            assert symbols == ["600000", "000001", "300001", "600001"]
            return ["600000", "000001", "300001"]

    assert refresh_sqlite_batch._refresh_universe(
        FakeSource(),
        universe_limit=0,
        reference_day=date(2026, 7, 29),
    ) == ["600000", "000001", "300001"]


def test_refresh_sqlite_batch_uses_previous_trade_day_for_active_baseline(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeSource:
        def __init__(self, **_kwargs) -> None:
            pass

        def get_symbols_with_daily_coverage(
            self, symbols: list[str], *_args, **_kwargs
        ) -> list[str]:
            return symbols

    summary = UpdateSummary(
        updated_rows=1,
        skipped_symbols=0,
        failed_symbols=0,
        target_day=date(2026, 7, 30),
        price_mode="raw",
        target_day_symbol_count=1,
        total_symbols=1,
        raw_max_trade_date=date(2026, 7, 30),
        processed_symbols=1,
    )
    observed: list[date | None] = []

    def fake_universe(
        _source, *, universe_limit: int, reference_day: date | None = None
    ) -> list[str]:
        assert universe_limit == 0
        observed.append(reference_day)
        return ["600000"]

    monkeypatch.setattr(refresh_sqlite_batch, "SqliteDbSource", FakeSource)
    monkeypatch.setattr(
        refresh_sqlite_batch, "get_previous_trading_day", lambda _: date(2026, 7, 29)
    )
    monkeypatch.setattr(refresh_sqlite_batch, "_refresh_universe", fake_universe)
    monkeypatch.setattr(
        refresh_sqlite_batch, "update_sqlite_daily", lambda *_args, **_kwargs: summary
    )

    refresh_sqlite_batch.refresh_batch(
        db_path=tmp_path / "market.db",
        state_path=tmp_path / "cursor.json",
        target_day=date(2026, 7, 30),
        batch_size=1,
        universe_limit=0,
        min_amount=0.0,
        query_timeout_seconds=4.0,
        max_runtime_seconds=120.0,
    )

    assert observed == [date(2026, 7, 29)]


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


def test_refresh_sqlite_batch_keeps_cursor_when_target_day_is_unavailable(
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
            self, symbols: list[str], start: date, *_args, **_kwargs
        ) -> list[str]:
            return [] if start == date(2026, 7, 29) else symbols

    summary = UpdateSummary(
        updated_rows=0,
        skipped_symbols=2,
        failed_symbols=0,
        target_day=date(2026, 7, 29),
        price_mode="raw",
        target_day_symbol_count=0,
        total_symbols=2,
        raw_max_trade_date=date(2026, 7, 28),
        processed_symbols=2,
        empty_response_symbols=2,
    )
    monkeypatch.setattr(refresh_sqlite_batch, "SqliteDbSource", FakeSource)
    monkeypatch.setattr(
        refresh_sqlite_batch, "update_sqlite_daily", lambda *_args, **_kwargs: summary
    )
    state = tmp_path / "cursor.json"
    state.write_text(
        json.dumps({"target_day": "2026-07-29", "offset": 2}), encoding="utf-8"
    )

    refresh_sqlite_batch.refresh_batch(
        db_path=tmp_path / "market.db",
        state_path=state,
        target_day=date(2026, 7, 29),
        batch_size=2,
        universe_limit=0,
        min_amount=0.0,
        query_timeout_seconds=4.0,
        max_runtime_seconds=120.0,
    )

    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["offset"] == 2


def test_refresh_sqlite_batch_stops_multi_batch_run_when_target_day_is_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeSource:
        def __init__(self, **_kwargs) -> None:
            pass

    summary = UpdateSummary(
        updated_rows=0,
        skipped_symbols=2,
        failed_symbols=0,
        target_day=date(2026, 7, 29),
        price_mode="raw",
        target_day_symbol_count=0,
        total_symbols=2,
        raw_max_trade_date=date(2026, 7, 28),
        processed_symbols=2,
        empty_response_symbols=2,
    )
    calls = 0

    def fake_refresh_batch(**_kwargs) -> UpdateSummary:
        nonlocal calls
        calls += 1
        return summary

    monkeypatch.setattr(refresh_sqlite_batch, "SqliteDbSource", FakeSource)
    monkeypatch.setattr(
        refresh_sqlite_batch,
        "_refresh_universe",
        lambda *_args, **_kwargs: ["600000", "000001"],
    )
    monkeypatch.setattr(refresh_sqlite_batch, "refresh_batch", fake_refresh_batch)

    refresh_sqlite_batch.refresh_batches(
        db_path=tmp_path / "market.db",
        state_path=tmp_path / "cursor.json",
        target_day=date(2026, 7, 29),
        batch_size=2,
        universe_limit=0,
        min_amount=0.0,
        query_timeout_seconds=4.0,
        max_runtime_seconds=120.0,
        batches=0,
    )

    assert calls == 1


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


def test_refresh_sqlite_batch_reuses_one_universe_for_multiple_chunks(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeSource:
        def __init__(self, **_kwargs) -> None:
            pass

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
    calls = 0

    def fake_universe(*_args, **_kwargs) -> list[str]:
        nonlocal calls
        calls += 1
        return ["600000", "000001", "300001", "600001"]

    monkeypatch.setattr(refresh_sqlite_batch, "SqliteDbSource", FakeSource)
    monkeypatch.setattr(refresh_sqlite_batch, "_refresh_universe", fake_universe)
    monkeypatch.setattr(
        refresh_sqlite_batch, "update_sqlite_daily", lambda *_args, **_kwargs: summary
    )

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

    assert calls == 1
    assert result.processed_symbols == 4
