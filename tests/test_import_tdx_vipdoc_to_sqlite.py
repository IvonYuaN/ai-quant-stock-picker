from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import pytest

from aqsp.data.tdx_vipdoc_source import TDX_DAY_RECORD
from scripts.import_tdx_vipdoc_to_sqlite import import_vipdoc_to_sqlite


def _write_day_file(
    path: Path,
    rows: list[tuple[int, int, int, int, int, float, int, int]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(struct.pack(TDX_DAY_RECORD.format, *row) for row in rows))


def test_import_tdx_vipdoc_to_sqlite_builds_raw_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AQSP_STOCK_NAME_DB_PATH", str(tmp_path / "missing-names.db"))
    vipdoc = tmp_path / "vipdoc"
    _write_day_file(
        vipdoc / "sh/lday/sh600519.day",
        [
            (20260102, 140000, 141000, 139000, 140500, 1405000.0, 1000, 0),
            (20260103, 140500, 142000, 140000, 141500, 1415000.0, 1200, 0),
        ],
    )
    db = tmp_path / "astocks_raw.db"

    summary = import_vipdoc_to_sqlite(vipdoc_path=vipdoc, db_path=db, rebuild=True)

    assert summary.symbols_seen == 1
    assert summary.symbols_imported == 1
    assert summary.rows_written == 2
    with sqlite3.connect(db) as conn:
        stocks = conn.execute("select ts_code, name from stocks").fetchall()
        rows = conn.execute(
            """
            select ts_code, trade_date, open, high, low, close, close_qfq
            from daily_qfq order by trade_date
            """
        ).fetchall()
    assert stocks == [("600519.SH", "600519")]
    assert rows == [
        ("600519.SH", "20260102", 1400.0, 1410.0, 1390.0, 1405.0, 1405.0),
        ("600519.SH", "20260103", 1405.0, 1420.0, 1400.0, 1415.0, 1415.0),
    ]


def test_import_tdx_vipdoc_to_sqlite_skips_already_imported_symbols(
    tmp_path: Path,
) -> None:
    vipdoc = tmp_path / "vipdoc"
    _write_day_file(
        vipdoc / "sh/lday/sh600519.day",
        [(20260102, 140000, 141000, 139000, 140500, 1405000.0, 1000, 0)],
    )
    _write_day_file(
        vipdoc / "sz/lday/sz000001.day",
        [(20260102, 1000, 1010, 990, 1005, 100500.0, 1000, 0)],
    )
    db = tmp_path / "astocks_raw.db"

    first = import_vipdoc_to_sqlite(vipdoc_path=vipdoc, db_path=db, rebuild=True)
    second = import_vipdoc_to_sqlite(vipdoc_path=vipdoc, db_path=db, rebuild=False)

    assert first.symbols_imported == 2
    assert second.symbols_imported == 0


def test_import_tdx_vipdoc_to_sqlite_appends_new_days_for_existing_symbol(
    tmp_path: Path,
) -> None:
    vipdoc = tmp_path / "vipdoc"
    day_file = vipdoc / "sh/lday/sh600519.day"
    _write_day_file(
        day_file,
        [(20260102, 140000, 141000, 139000, 140500, 1405000.0, 1000, 0)],
    )
    db = tmp_path / "astocks_raw.db"

    first = import_vipdoc_to_sqlite(vipdoc_path=vipdoc, db_path=db, rebuild=True)

    _write_day_file(
        day_file,
        [
            (20260102, 140000, 141000, 139000, 140500, 1405000.0, 1000, 0),
            (20260103, 140500, 142000, 140000, 141500, 1415000.0, 1200, 0),
        ],
    )
    second = import_vipdoc_to_sqlite(vipdoc_path=vipdoc, db_path=db, rebuild=False)

    assert first.symbols_imported == 1
    assert second.symbols_imported == 1
    assert second.rows_written == 1

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "select trade_date, close from daily_qfq where ts_code='600519.SH' order by trade_date"
        ).fetchall()

    assert rows == [("20260102", 1405.0), ("20260103", 1415.0)]
