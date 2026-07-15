from __future__ import annotations

import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

import pytest

from scripts import update_sqlite_daily


def test_update_sqlite_daily_cli_exposes_historical_backfill_flags() -> None:
    text = Path("scripts/update_sqlite_daily.py").read_text(encoding="utf-8")

    assert "--start-date" in text
    assert "--fill-history-gaps" in text
    assert "--force-from-start" in text
    assert "--price-mode" in text
    assert "--query-timeout-seconds" in text
    assert "fill_history_gaps=args.fill_history_gaps" in text
    assert "force_from_start=args.force_from_start" in text
    assert "price_mode=args.price_mode" in text
    assert 'default="raw"' in text


def test_update_sqlite_daily_requires_start_date_when_force_enabled(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "x.db"
    db.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        update_sqlite_daily, "_target_trade_day", lambda _raw: date(2026, 6, 18)
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["update_sqlite_daily.py", str(db), "--force-from-start"],
    )

    with pytest.raises(SystemExit, match="--force-from-start requires --start-date"):
        update_sqlite_daily.main()


def test_update_sqlite_daily_requires_start_date_when_fill_gaps_enabled(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "x.db"
    db.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        update_sqlite_daily, "_target_trade_day", lambda _raw: date(2026, 6, 18)
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["update_sqlite_daily.py", str(db), "--fill-history-gaps"],
    )

    with pytest.raises(SystemExit, match="--fill-history-gaps requires --start-date"):
        update_sqlite_daily.main()


def test_resolve_fetch_start_day_fills_historical_prefix_gap() -> None:
    assert update_sqlite_daily._resolve_fetch_start_day(
        first=date(2024, 1, 2),
        latest=date(2024, 12, 31),
        start_day=date(2018, 1, 1),
        target_day=date(2024, 12, 31),
        force_from_start=False,
        fill_history_gaps=True,
    ) == date(2018, 1, 1)


def test_resolve_fetch_start_day_keeps_incremental_when_prefix_is_covered() -> None:
    assert update_sqlite_daily._resolve_fetch_start_day(
        first=date(2018, 1, 2),
        latest=date(2024, 12, 31),
        start_day=date(2018, 1, 1),
        target_day=date(2024, 12, 31),
        force_from_start=False,
        fill_history_gaps=True,
    ) == date(2025, 1, 1)


def test_update_sqlite_daily_creates_raw_database_schema_when_missing(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeBaostock:
        def login(self):
            return type("Login", (), {"error_code": "0", "error_msg": ""})()

        def logout(self) -> None:
            return None

    db = tmp_path / "astocks_raw.db"
    monkeypatch.setattr(update_sqlite_daily, "_load_baostock", FakeBaostock)
    monkeypatch.setattr(update_sqlite_daily, "sync_stock_list", lambda conn, bs: [])

    summary = update_sqlite_daily.update_sqlite_daily(
        db,
        target_day=date(2026, 6, 18),
        sleep_seconds=0.0,
        limit=0,
        price_mode="raw",
    )

    assert summary.price_mode == "raw"
    assert summary.target_day_symbol_count == 0
    assert summary.total_symbols == 0
    with sqlite3.connect(db) as conn:
        tables = {row[0] for row in conn.execute("select name from sqlite_master")}
    assert {"stocks", "daily_qfq"}.issubset(tables)


def test_update_sqlite_daily_counts_target_day_coverage(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeBaostock:
        def login(self):
            return type("Login", (), {"error_code": "0", "error_msg": ""})()

        def logout(self) -> None:
            return None

    db = tmp_path / "astocks_raw.db"
    monkeypatch.setattr(update_sqlite_daily, "_load_baostock", FakeBaostock)

    def fake_sync_stock_list(conn, _bs, preserve_existing=True):
        del preserve_existing
        update_sqlite_daily.ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO stocks(ts_code, name) VALUES(?, ?)",
            ("600000.SH", "PF Bank"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO stocks(ts_code, name) VALUES(?, ?)",
            ("000001.SZ", "SZ Bank"),
        )
        conn.commit()
        return ["000001.SZ", "600000.SH"]

    monkeypatch.setattr(update_sqlite_daily, "sync_stock_list", fake_sync_stock_list)
    monkeypatch.setattr(
        update_sqlite_daily,
        "_query_history_rows",
        lambda **kwargs: (
            "0",
            [["2026-06-18", "1", "2", "0.5", "1.5", "100", "200"]]
            if kwargs["ts_code"] == "600000.SH"
            else [],
        ),
    )

    summary = update_sqlite_daily.update_sqlite_daily(
        db,
        target_day=date(2026, 6, 18),
        sleep_seconds=0.0,
        limit=0,
        price_mode="raw",
    )

    assert summary.updated_rows == 1
    assert summary.target_day_symbol_count == 1
    assert summary.total_symbols == 2
    assert summary.raw_max_trade_date == date(2026, 6, 18)
    assert summary.coverage_error is None


def test_update_sqlite_daily_fails_closed_when_target_is_beyond_raw_cutoff(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class FakeBaostock:
        def login(self):
            return type("Login", (), {"error_code": "0", "error_msg": ""})()

        def logout(self) -> None:
            return None

    db = tmp_path / "astocks_raw.db"
    monkeypatch.setattr(update_sqlite_daily, "_load_baostock", FakeBaostock)
    monkeypatch.setattr(
        update_sqlite_daily,
        "_sync_stock_list_compat",
        lambda conn, _bs, preserve_existing=True: ["600000.SH"],
    )
    monkeypatch.setattr(
        update_sqlite_daily,
        "_query_history_rows",
        lambda **_kwargs: ("0", []),
    )

    summary = update_sqlite_daily.update_sqlite_daily(
        db,
        target_day=date(2026, 6, 18),
        sleep_seconds=0.0,
        limit=0,
        price_mode="raw",
    )

    assert summary.target_day_symbol_count == 0
    assert summary.raw_max_trade_date is None
    assert summary.coverage_error == (
        "raw sqlite has no valid trade_date after update; "
        "target=2026-06-18 coverage=0/1"
    )


def test_target_coverage_error_reports_stale_raw_cutoff() -> None:
    detail = update_sqlite_daily._target_coverage_error(
        target_day=date(2026, 6, 18),
        target_day_symbol_count=0,
        total_symbols=10,
        raw_max_trade_date=date(2026, 6, 17),
    )

    assert detail == (
        "target=2026-06-18 exceeds raw MAX(trade_date)=2026-06-17; coverage=0/10"
    )


def test_update_sqlite_daily_cli_returns_nonzero_for_coverage_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db = tmp_path / "astocks_raw.db"
    db.touch()
    monkeypatch.setattr(
        update_sqlite_daily,
        "_target_trade_day",
        lambda _raw: date(2026, 6, 18),
    )
    monkeypatch.setattr(
        update_sqlite_daily,
        "update_sqlite_daily",
        lambda *args, **kwargs: update_sqlite_daily.UpdateSummary(
            updated_rows=0,
            skipped_symbols=1,
            failed_symbols=0,
            target_day=date(2026, 6, 18),
            price_mode="raw",
            target_day_symbol_count=0,
            total_symbols=1,
            coverage_error="target coverage unavailable",
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["update_sqlite_daily.py", str(db), "--price-mode", "raw"],
    )

    assert update_sqlite_daily.main() == 1


def test_update_sqlite_daily_retries_broken_pipe_and_relogs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class FakeBaostock:
        def __init__(self) -> None:
            self.login_calls = 0
            self.logout_calls = 0

        def login(self):
            self.login_calls += 1
            return type("Login", (), {"error_code": "0", "error_msg": ""})()

        def logout(self) -> None:
            self.logout_calls += 1

    fake_bs = FakeBaostock()
    db = tmp_path / "astocks_raw.db"
    monkeypatch.setattr(update_sqlite_daily, "_load_baostock", lambda: fake_bs)
    monkeypatch.setattr(
        update_sqlite_daily,
        "_sync_stock_list_compat",
        lambda conn, _bs, preserve_existing=True: ["600000.SH"],
    )
    monkeypatch.setattr(update_sqlite_daily.time, "sleep", lambda _seconds: None)
    calls = {"count": 0}

    def fake_query(**_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError(32, "Broken pipe")
        return "0", [["2026-06-18", "1", "2", "0.5", "1.5", "100", "200"]]

    monkeypatch.setattr(update_sqlite_daily, "_query_history_rows", fake_query)

    summary = update_sqlite_daily.update_sqlite_daily(
        db,
        target_day=date(2026, 6, 18),
        sleep_seconds=0.0,
        limit=0,
        price_mode="raw",
    )

    assert summary.updated_rows == 1
    assert summary.failed_symbols == 0
    assert fake_bs.login_calls == 2
    assert fake_bs.logout_calls == 2


def test_update_sqlite_daily_marks_symbol_failed_after_retry_exhausted(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class FakeBaostock:
        def __init__(self) -> None:
            self.login_calls = 0
            self.logout_calls = 0

        def login(self):
            self.login_calls += 1
            return type("Login", (), {"error_code": "0", "error_msg": ""})()

        def logout(self) -> None:
            self.logout_calls += 1

    fake_bs = FakeBaostock()
    db = tmp_path / "astocks_raw.db"
    monkeypatch.setattr(update_sqlite_daily, "_load_baostock", lambda: fake_bs)
    monkeypatch.setattr(
        update_sqlite_daily,
        "_sync_stock_list_compat",
        lambda conn, _bs, preserve_existing=True: ["600000.SH"],
    )
    monkeypatch.setattr(update_sqlite_daily.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        update_sqlite_daily,
        "_query_history_rows",
        lambda **_kwargs: (_ for _ in ()).throw(OSError(32, "Broken pipe")),
    )

    summary = update_sqlite_daily.update_sqlite_daily(
        db,
        target_day=date(2026, 6, 18),
        sleep_seconds=0.0,
        limit=0,
        price_mode="raw",
    )

    assert summary.updated_rows == 0
    assert summary.failed_symbols == 1
    assert fake_bs.login_calls == 3
    assert fake_bs.logout_calls == 3


def test_sync_stock_list_preserves_existing_historical_symbols_by_default(
    tmp_path: Path,
) -> None:
    class FakeResult:
        error_code = "0"

        def __init__(self) -> None:
            self.rows = [["sh.600000", "PF Bank"]]
            self.index = -1

        def next(self) -> bool:
            self.index += 1
            return self.index < len(self.rows)

        def get_row_data(self):
            return self.rows[self.index]

    class FakeBaostock:
        def query_stock_basic(self):
            return FakeResult()

    db = tmp_path / "x.db"
    with sqlite3.connect(db) as conn:
        update_sqlite_daily.ensure_schema(conn)
        conn.execute(
            "INSERT INTO stocks(ts_code, name) VALUES(?, ?)",
            ("000001.SZ", "Old Bank"),
        )
        conn.execute(
            """
            INSERT INTO daily_qfq(
                ts_code, trade_date, open, high, low, close_qfq, volume, amount, open_qfq, high_qfq, low_qfq, close
            ) VALUES(?, ?, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1)
            """,
            ("000001.SZ", "20180102"),
        )
        symbols = update_sqlite_daily.sync_stock_list(
            conn, FakeBaostock(), preserve_existing=True
        )
        stocks = conn.execute("SELECT ts_code FROM stocks ORDER BY ts_code").fetchall()
        rows = conn.execute(
            "SELECT COUNT(*) FROM daily_qfq WHERE ts_code='000001.SZ'"
        ).fetchone()[0]

    assert symbols == ["000001.SZ", "600000.SH"]
    assert stocks == [("000001.SZ",), ("600000.SH",)]
    assert rows == 1


def test_sync_stock_list_can_drop_missing_symbols_when_explicitly_disabled(
    tmp_path: Path,
) -> None:
    class FakeResult:
        error_code = "0"

        def __init__(self) -> None:
            self.rows = [["sh.600000", "PF Bank"]]
            self.index = -1

        def next(self) -> bool:
            self.index += 1
            return self.index < len(self.rows)

        def get_row_data(self):
            return self.rows[self.index]

    class FakeBaostock:
        def query_stock_basic(self):
            return FakeResult()

    db = tmp_path / "x.db"
    with sqlite3.connect(db) as conn:
        update_sqlite_daily.ensure_schema(conn)
        conn.execute(
            "INSERT INTO stocks(ts_code, name) VALUES(?, ?)",
            ("000001.SZ", "Old Bank"),
        )
        conn.execute(
            """
            INSERT INTO daily_qfq(
                ts_code, trade_date, open, high, low, close_qfq, volume, amount, open_qfq, high_qfq, low_qfq, close
            ) VALUES(?, ?, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1)
            """,
            ("000001.SZ", "20180102"),
        )
        symbols = update_sqlite_daily.sync_stock_list(
            conn, FakeBaostock(), preserve_existing=False
        )
        stocks = conn.execute("SELECT ts_code FROM stocks ORDER BY ts_code").fetchall()
        rows = conn.execute(
            "SELECT COUNT(*) FROM daily_qfq WHERE ts_code='000001.SZ'"
        ).fetchone()[0]

    assert symbols == ["600000.SH"]
    assert stocks == [("600000.SH",)]
    assert rows == 0


def test_update_sqlite_daily_configures_wal_and_busy_timeout(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    with sqlite3.connect(db) as conn:
        update_sqlite_daily.configure_sqlite_connection(conn)
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()
        synchronous = conn.execute("PRAGMA synchronous").fetchone()

    assert str(journal_mode[0]).lower() == "wal"
    assert int(busy_timeout[0]) == 30000
    assert int(synchronous[0]) >= 1


def test_run_with_timeout_raises_for_stalled_query() -> None:
    with pytest.raises(TimeoutError, match="query timed out"):
        update_sqlite_daily._run_with_timeout(lambda: time.sleep(0.2), 0.05)


def test_adjustflag_for_price_mode_keeps_raw_unadjusted() -> None:
    assert update_sqlite_daily._adjustflag_for_price_mode("raw") == "1"
    assert update_sqlite_daily._adjustflag_for_price_mode("qfq") == "2"
