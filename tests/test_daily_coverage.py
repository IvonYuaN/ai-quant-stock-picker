from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from aqsp.core.errors import DataError
from aqsp.data.daily_coverage import (
    latest_date_histogram,
    market_date_is_usable,
    summarize_daily_coverage,
)

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
"""


def _make_db(tmp_path: Path, rows: list[tuple[str, str]], *, with_table: bool = True) -> str:
    path = tmp_path / "astocks_raw.db"
    with sqlite3.connect(path) as conn:
        if with_table:
            conn.executescript(_SCHEMA)
            conn.executemany(
                "INSERT INTO daily_qfq (ts_code, trade_date, close) VALUES (?, ?, 10.0)",
                rows,
            )
    return str(path)


def test_summary_reports_full_coverage_when_all_symbols_reach_latest_day(tmp_path):
    db = _make_db(
        tmp_path,
        [(symbol, day) for symbol in ("000001.SZ", "600000.SH", "300001.SZ")
         for day in ("20261029", "20261030")],
    )

    summary = summarize_daily_coverage(db)

    assert summary.total_symbols == 3
    assert summary.market_max_date == "20261030"
    assert summary.symbols_on_max_date == 3
    assert summary.coverage_pct == 1.0
    assert market_date_is_usable(summary, min_coverage_ratio=0.8) is True


def test_single_sentinel_does_not_make_market_date_usable(tmp_path):
    """Regression: one refreshed symbol must not stand in for the whole market."""
    rows = [
        (symbol, "20260930")
        for symbol in ("000001.SZ", "600000.SH", "300001.SZ", "600519.SH")
    ]
    rows += [(symbol, day) for symbol, day in (
        ("000001.SZ", "20261008"),
        ("000001.SZ", "20261030"),
        ("000001.SZ", "20261102"),
    )]
    db = _make_db(tmp_path, rows)

    summary = summarize_daily_coverage(db)

    assert summary.market_max_date == "20261102"
    assert summary.total_symbols == 4
    assert summary.symbols_on_max_date == 1
    assert summary.sentinel_symbols == ("000001.SZ",)
    assert summary.coverage_pct == pytest.approx(0.25)
    assert market_date_is_usable(summary, min_coverage_ratio=0.8) is False
    assert market_date_is_usable(summary, min_coverage_ratio=0.2) is True


def test_summary_on_empty_table_has_no_market_date(tmp_path):
    db = _make_db(tmp_path, [])

    summary = summarize_daily_coverage(db)

    assert summary.market_max_date is None
    assert summary.total_symbols == 0
    assert summary.coverage_pct == 0.0
    assert market_date_is_usable(summary, min_coverage_ratio=0.5) is False


def test_missing_table_raises_data_error(tmp_path):
    db = _make_db(tmp_path, [], with_table=False)

    with pytest.raises(DataError, match="daily_qfq"):
        summarize_daily_coverage(db)
    with pytest.raises(DataError, match="daily_qfq"):
        latest_date_histogram(db)


def test_missing_database_raises_data_error(tmp_path):
    with pytest.raises(DataError, match="不存在"):
        summarize_daily_coverage(str(tmp_path / "absent.db"))


def test_diagnostics_are_read_only(tmp_path):
    db = _make_db(
        tmp_path,
        [("000001.SZ", "20261030"), ("600000.SH", "20260930")],
    )
    before = hashlib.sha256(Path(db).read_bytes()).hexdigest()

    summarize_daily_coverage(db)
    latest_date_histogram(db)

    assert hashlib.sha256(Path(db).read_bytes()).hexdigest() == before
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily_qfq").fetchone()[0] == 2


def test_invalid_coverage_ratio_is_rejected(tmp_path):
    summary = summarize_daily_coverage(_make_db(tmp_path, [("000001.SZ", "20261030")]))

    for ratio in (0, -0.1, 1.01, float("nan")):
        with pytest.raises(ValueError, match="最低覆盖比例"):
            market_date_is_usable(summary, min_coverage_ratio=ratio)


def test_histogram_counts_each_symbol_newest_date(tmp_path):
    db = _make_db(
        tmp_path,
        [
            ("000001.SZ", "20260930"),
            ("000001.SZ", "20261102"),
            ("600000.SH", "20260930"),
            ("300001.SZ", "20260911"),
        ],
    )

    histogram = latest_date_histogram(db)

    assert dict(histogram) == {"20261102": 1, "20260930": 1, "20260911": 1}
