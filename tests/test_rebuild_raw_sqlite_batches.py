from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from scripts import rebuild_raw_sqlite_batches
from scripts.update_sqlite_daily import UpdateSummary, ensure_schema


def _summary() -> UpdateSummary:
    return UpdateSummary(
        updated_rows=2,
        skipped_symbols=0,
        failed_symbols=0,
        target_day=date(2026, 8, 3),
        price_mode="raw",
        target_day_symbol_count=2,
        total_symbols=2,
        raw_max_trade_date=date(2026, 8, 3),
        processed_symbols=2,
    )


def test_rebuild_raw_sqlite_batches_seeds_only_supported_non_st_symbols(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.db"
    candidate = tmp_path / "candidate.db"
    with sqlite3.connect(source) as conn:
        ensure_schema(conn)
        conn.executemany(
            "INSERT INTO stocks(ts_code, name) VALUES(?, ?)",
            [
                ("600000.SH", "浦发银行"),
                ("000001.SZ", "平安银行"),
                ("300001.SZ", "特锐德"),
                ("688001.SH", "华兴源创"),
                ("002001.SZ", "ST新和"),
            ],
        )
    assert rebuild_raw_sqlite_batches._seed_candidate_database(source, candidate) == [
        "000001.SZ",
        "300001.SZ",
        "600000.SH",
    ]
    with sqlite3.connect(candidate) as conn:
        assert conn.execute(
            "SELECT ts_code FROM stocks ORDER BY ts_code"
        ).fetchall() == [
            ("000001.SZ",),
            ("300001.SZ",),
            ("600000.SH",),
        ]
        assert conn.execute("SELECT COUNT(*) FROM daily_qfq").fetchone() == (0,)


def test_rebuild_raw_sqlite_batches_persists_resumable_cursor(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "legacy.db"
    candidate = tmp_path / "candidate.db"
    state = tmp_path / "rebuild.json"
    with sqlite3.connect(source) as conn:
        ensure_schema(conn)
        conn.executemany(
            "INSERT INTO stocks(ts_code, name) VALUES(?, ?)",
            [("600000.SH", "A"), ("000001.SZ", "B"), ("300001.SZ", "C")],
        )

    class FakeSource:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def price_mode(self) -> str:
            return "raw"

        def get_symbols_with_daily_coverage(self, symbols, *_args, **_kwargs):
            return list(symbols)

    monkeypatch.setattr(rebuild_raw_sqlite_batches, "SqliteDbSource", FakeSource)
    monkeypatch.setattr(
        rebuild_raw_sqlite_batches,
        "update_sqlite_daily",
        lambda *_args, **_kwargs: _summary(),
    )

    result = rebuild_raw_sqlite_batches.rebuild_batch(
        source_db=source,
        candidate_db=candidate,
        state_path=state,
        target_day=date(2026, 8, 3),
        start_day=date(2025, 1, 1),
        batch_size=2,
        query_timeout_seconds=4.0,
        max_runtime_seconds=60.0,
        min_coverage_ratio=0.98,
    )

    assert result.next_offset == 2
    assert not result.complete
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["target_day"] == "2026-08-03"
    assert payload["next_offset"] == 2
    assert payload["universe_size"] == 3
    assert payload["coverage_ratio"] == 2 / 3
    assert not payload["publish_ready"]


def test_rebuild_raw_sqlite_batches_atomically_activates_valid_candidate(
    monkeypatch, tmp_path: Path
) -> None:
    active = tmp_path / "astocks_raw.db"
    candidate = tmp_path / "astocks_raw.db.rebuild"
    active.write_text("legacy", encoding="utf-8")
    with sqlite3.connect(candidate):
        pass

    class FakeSource:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def price_mode(self) -> str:
            return "raw"

    monkeypatch.setattr(rebuild_raw_sqlite_batches, "SqliteDbSource", FakeSource)

    rebuild_raw_sqlite_batches._activate_candidate_database(
        active_db=active, candidate_db=candidate
    )

    assert active.is_symlink()
    assert active.resolve() == candidate.resolve()
    backups = list(tmp_path.glob("astocks_raw.db.invalid-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "legacy"
