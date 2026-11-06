from __future__ import annotations

import sqlite3
from datetime import date

from aqsp.data.sqlite_db_source import SqliteDbSource


def test_sqlite_db_source_returns_distinct_daily_dates_when_range_contains_duplicates(tmp_path):
    db_path = tmp_path / "raw.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE daily_qfq (trade_date TEXT NOT NULL, ts_code TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO daily_qfq (trade_date, ts_code) VALUES (?, ?)",
            [
                ("2026-11-02", "600000.SH"),
                ("2026-11-02", "000001.SZ"),
                ("20261102", "300750.SZ"),
                ("2026-11-03", "600000.SH"),
                ("2026-11-06", "600000.SH"),
                ("2026-11-07", "600000.SH"),
            ],
        )

    source = SqliteDbSource.__new__(SqliteDbSource)
    source.db_path = db_path

    result = source.get_available_daily_dates(
        date(2026, 11, 2), date(2026, 11, 6)
    )

    assert result == [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 6)]


def test_sqlite_db_source_returns_each_requested_symbol_latest_daily_date(tmp_path):
    db_path = tmp_path / "raw.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE stocks (ts_code TEXT NOT NULL, name TEXT NOT NULL)")
        connection.execute("CREATE TABLE daily_qfq (trade_date TEXT NOT NULL, ts_code TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO stocks (ts_code, name) VALUES (?, ?)",
            [("600000.SH", "浦发银行"), ("600519.SH", "贵州茅台"), ("000001.SH", "上证指数")],
        )
        connection.executemany(
            "INSERT INTO daily_qfq (trade_date, ts_code) VALUES (?, ?)",
            [
                ("20261102", "600000.SH"),
                ("20260911", "600519.SH"),
                ("20261102", "000001.SH"),
            ],
        )
    source = SqliteDbSource.__new__(SqliteDbSource)
    source.db_path = db_path
    source._symbol_map = None

    result = source.get_latest_daily_dates(["600000", "600519", "999999"])

    assert result == {"600000": date(2026, 11, 2), "600519": date(2026, 9, 11)}


def test_sqlite_db_source_historical_universe_comes_from_raw_stock_bars_not_current_master(tmp_path):
    db_path = tmp_path / "raw.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE stocks (ts_code TEXT NOT NULL, name TEXT NOT NULL)")
        connection.execute("CREATE TABLE daily_qfq (trade_date TEXT NOT NULL, ts_code TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO stocks (ts_code, name) VALUES (?, ?)",
            [("600000.SH", "当前银行")],
        )
        connection.executemany(
            "INSERT INTO daily_qfq (trade_date, ts_code) VALUES (?, ?)",
            [
                ("20260105", "600000.SH"),
                ("20260105", "000001.SZ"),
                ("20260106", "000002.SZ"),
                ("20260107", "300750.SZ"),
                ("20260105", "000001.SH"),
                ("20260105", "510300.SH"),
                ("20260108", "600519.SH"),
            ],
        )
    source = SqliteDbSource.__new__(SqliteDbSource)
    source.db_path = db_path

    assert source.get_symbols_with_daily_bars(date(2026, 1, 5), date(2026, 1, 7)) == [
        "000001", "000002", "300750", "600000",
    ]


def test_sqlite_db_source_resolves_historical_raw_symbol_missing_from_stocks_master(tmp_path):
    db_path = tmp_path / "raw.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE stocks (ts_code TEXT NOT NULL, name TEXT NOT NULL)")
        connection.execute("CREATE TABLE daily_qfq (trade_date TEXT NOT NULL, ts_code TEXT NOT NULL)")
        connection.execute("INSERT INTO stocks VALUES (?, ?)", ("600000.SH", "当前银行"))
        connection.executemany(
            "INSERT INTO daily_qfq VALUES (?, ?)",
            [
                ("20260105", "000002.SZ"),
                ("20260106", "000002.SZ"),
                ("20260108", "000002.SZ"),
            ],
        )
    source = SqliteDbSource.__new__(SqliteDbSource)
    source.db_path = db_path
    source._symbol_map = None

    assert source.get_latest_daily_dates(["000002"]) == {"000002": date(2026, 1, 8)}
    assert source.get_latest_daily_dates(
        ["000002"], as_of=date(2026, 1, 6)
    ) == {"000002": date(2026, 1, 6)}


def test_sqlite_db_source_fetches_historical_symbol_missing_from_stocks_master(tmp_path, monkeypatch):
    db_path = tmp_path / "raw.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE stocks (ts_code TEXT NOT NULL, name TEXT NOT NULL)")
        connection.execute(
            """
            CREATE TABLE daily_qfq (
                id INTEGER PRIMARY KEY, ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                open_qfq REAL, high_qfq REAL, low_qfq REAL, close_qfq REAL,
                volume REAL, amount REAL
            )
            """
        )
        connection.execute("INSERT INTO stocks VALUES (?, ?)", ("600000.SH", "当前银行"))
        connection.execute(
            "INSERT INTO daily_qfq (ts_code, trade_date, open, high, low, close, volume, amount) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("000002.SZ", "20260105", 9.5, 10.5, 9, 10, 1000, 10000),
        )
    source = SqliteDbSource.__new__(SqliteDbSource)
    source.db_path = db_path
    source._symbol_map = None
    source._use_cache = False
    source.cache = None
    source._active_workload = None
    source._last_coverage_snapshot = None
    monkeypatch.setenv("AQSP_SQLITE_PREFILTERED_SYMBOLS", "1")

    frame = source.fetch_daily(["000002"], date(2026, 1, 5), date(2026, 1, 5), adjust="")["000002"]

    assert frame["close"].tolist() == [10]
    assert frame["name"].tolist() == ["000002"]
