"""Regression guard: incomplete daily data must never look like a complete market day."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sqlite3

import pytest

from aqsp.data.sqlite_db_source import SqliteDbSource

_SCHEMA = """
CREATE TABLE daily_qfq (
    id INTEGER PRIMARY KEY,
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close_qfq REAL,
    volume REAL,
    amount REAL,
    open_qfq REAL,
    high_qfq REAL,
    low_qfq REAL,
    close REAL
);
CREATE INDEX idx_daily_qfq_date ON daily_qfq(trade_date);
CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT);
"""

_SYMBOLS = ("000001.SZ", "600000.SH", "300001.SZ", "600519.SH")


def _source(tmp_path: Path, rows: list[tuple[str, str]]) -> SqliteDbSource:
    path = tmp_path / "astocks_raw.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
        conn.executemany(
            "INSERT INTO daily_qfq (ts_code, trade_date, close) VALUES (?, ?, 10.0)",
            rows,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO stocks (ts_code, name) VALUES (?, '测试')",
            [(symbol,) for symbol, _ in rows],
        )
    return SqliteDbSource(db_path=str(path), cache=None)


def test_complete_cross_section_returns_newest_market_date(tmp_path):
    rows = [(symbol, day) for symbol in _SYMBOLS for day in ("20260930", "20261102")]

    assert _source(tmp_path, rows).get_latest_daily_date() == date(2026, 11, 2)


def test_single_sentinel_symbol_fails_closed(tmp_path):
    """One symbol refreshed to 11-02 must not stand in for the whole market."""
    rows = [(symbol, "20260930") for symbol in _SYMBOLS]
    rows.append(("000001.SZ", "20261102"))

    assert _source(tmp_path, rows).get_latest_daily_date() is None


def test_partial_coverage_below_threshold_fails_closed(tmp_path):
    rows = [(symbol, "20261102") for symbol in _SYMBOLS[:2]]
    rows += [(symbol, "20260930") for symbol in _SYMBOLS]

    assert _source(tmp_path, rows).get_latest_daily_date() is None


def test_explicit_relaxation_is_honoured(tmp_path, monkeypatch):
    rows = [(symbol, "20260930") for symbol in _SYMBOLS]
    rows.append(("000001.SZ", "20261102"))
    monkeypatch.setenv("AQSP_MIN_MARKET_DAILY_COVERAGE", "0.2")

    assert _source(tmp_path, rows).get_latest_daily_date() == date(2026, 11, 2)


def test_invalid_relaxation_falls_back_to_strict_default(tmp_path, monkeypatch):
    rows = [(symbol, "20260930") for symbol in _SYMBOLS]
    rows.append(("000001.SZ", "20261102"))
    monkeypatch.setenv("AQSP_MIN_MARKET_DAILY_COVERAGE", "1.5")

    assert _source(tmp_path, rows).get_latest_daily_date() is None


def test_index_rows_cannot_raise_the_market_date(tmp_path):
    rows = [(symbol, "20260930") for symbol in _SYMBOLS]
    rows.append(("000001.SH", "20261231"))  # 上证指数，必须被排除

    assert _source(tmp_path, rows).get_latest_daily_date() == date(2026, 9, 30)


def test_empty_database_returns_none(tmp_path):
    assert _source(tmp_path, []).get_latest_daily_date() is None


@pytest.mark.parametrize("env_value", ["", "abc", "0", "-1", "2"])
def test_bad_env_values_never_widen_the_gate(tmp_path, monkeypatch, env_value):
    rows = [(symbol, "20260930") for symbol in _SYMBOLS]
    rows.append(("000001.SZ", "20261102"))
    monkeypatch.setenv("AQSP_MIN_MARKET_DAILY_COVERAGE", env_value)

    assert _source(tmp_path, rows).get_latest_daily_date() is None
