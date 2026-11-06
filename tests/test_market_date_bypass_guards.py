"""Regression guard: every path that once used a bare MAX(trade_date) must fail closed."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.data.daily_coverage import verified_market_date
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
    close REAL,
    UNIQUE(ts_code, trade_date)
);
CREATE INDEX idx_daily_qfq_date ON daily_qfq(trade_date);
CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT);
"""

_SYMBOLS = tuple(f"60000{index}.SH" for index in range(10))


@pytest.fixture(autouse=True)
def fixed_threshold(monkeypatch):
    monkeypatch.delenv("AQSP_MIN_MARKET_DAILY_COVERAGE", raising=False)


def _make_db(
    tmp_path: Path, rows: list[tuple[str, str]], *, name: str = "astocks_raw.db"
) -> str:
    path = tmp_path / name
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
        conn.executemany(
            "INSERT INTO daily_qfq (ts_code, trade_date, close, amount) VALUES (?, ?, 10.0, 1000.0)",
            rows,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO stocks (ts_code, name) VALUES (?, '测试')",
            [(symbol,) for symbol, _ in rows],
        )
    return str(path)


def _complete(tmp_path: Path, *, name: str = "astocks_raw.db") -> str:
    return _make_db(
        tmp_path, [(symbol, "20261105") for symbol in _SYMBOLS], name=name
    )


def _sentinel(tmp_path: Path, *, name: str = "astocks_raw.db") -> str:
    rows = [(symbol, "20260930") for symbol in _SYMBOLS]
    rows.append((_SYMBOLS[0], "20261105"))
    return _make_db(tmp_path, rows, name=name)


def test_verified_market_date_returns_date_for_complete_cross_section(tmp_path):
    assert verified_market_date(_complete(tmp_path)) == "20261105"


def test_verified_market_date_rejects_single_sentinel(tmp_path):
    assert verified_market_date(_sentinel(tmp_path)) is None


def test_verified_market_date_rejects_unreadable_database(tmp_path):
    assert verified_market_date(str(tmp_path / "absent.db")) is None
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert verified_market_date(str(empty)) is None


def test_ic_diagnosis_as_of_rejects_incomplete_market(tmp_path):
    from scripts.analysis import ic_diagnosis

    with pytest.raises(DataError, match="横截面覆盖不足"):
        ic_diagnosis._as_of(_sentinel(tmp_path, name="sentinel.db"))
    assert ic_diagnosis._as_of(_complete(tmp_path, name="complete.db")) == "2026-11-05"


def test_check_data_freshness_reports_no_date_for_incomplete_market(tmp_path):
    from scripts.data import check_data_freshness

    assert check_data_freshness._db_max_trade_date(
        Path(_sentinel(tmp_path, name="sentinel.db")), "daily_qfq"
    ) is None
    assert check_data_freshness._db_max_trade_date(
        Path(_complete(tmp_path, name="complete.db")), "daily_qfq"
    ) == date(2026, 11, 5)


def test_get_liquid_symbols_returns_empty_for_incomplete_market(tmp_path):
    source = SqliteDbSource(db_path=_sentinel(tmp_path), cache=None)

    assert source.get_liquid_symbols(limit=10, min_amount=0) == []


def test_persist_target_snapshot_refuses_partial_snapshot(tmp_path):
    from scripts.data.refresh_sqlite_batch import _persist_target_snapshot

    db_path = Path(_complete(tmp_path))
    eligible = {symbol.split(".")[0] for symbol in _SYMBOLS}

    def snapshot(count: int) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "symbol": symbol.split(".")[0],
                    "name": "测试",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 1000,
                    "amount": 10500.0,
                }
                for symbol in _SYMBOLS[:count]
            ]
        )

    assert _persist_target_snapshot(
        db_path=db_path,
        target_day=date(2026, 11, 6),
        snapshot=snapshot(1),
        eligible_symbols=eligible,
    ) == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM daily_qfq WHERE trade_date = '20261106'"
        ).fetchone()[0] == 0

    written = _persist_target_snapshot(
        db_path=db_path,
        target_day=date(2026, 11, 6),
        snapshot=snapshot(10),
        eligible_symbols=eligible,
    )
    assert len(written) == 10
