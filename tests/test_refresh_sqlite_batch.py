from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from aqsp.data.sqlite_db_source import SqliteDbSource
from scripts.refresh_sqlite_batch import _refresh_universe


def _write_market_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            """
            CREATE TABLE daily_qfq (
                ts_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                amount REAL,
                PRIMARY KEY (ts_code, trade_date)
            )
            """
        )
        conn.executemany(
            "INSERT INTO stocks(ts_code, name) VALUES (?, ?)",
            [
                ("600001.SH", "有效主板"),
                ("300001.SZ", "有效创业板"),
                ("000003.SZ", "PT样本"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO daily_qfq(
                ts_code, trade_date, open, high, low, close, volume, amount
            ) VALUES (?, '20260817', 1, 1, 1, 1, 100, 100)
            """,
            [("600001.SH",), ("300001.SZ",), ("000003.SZ",)],
        )


def test_refresh_universe_falls_back_when_previous_trade_day_is_missing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "astocks_raw.db"
    _write_market_db(db_path)
    source = SqliteDbSource(db_path=db_path, cache=None)

    symbols = _refresh_universe(
        source,
        universe_limit=0,
        reference_day=date(2026, 8, 18),
    )

    assert symbols == ["600001", "300001"]
