from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from aqsp.core.errors import DataError
from aqsp.data.sqlite_db_source import (
    _ALLOW_EMPTY_SYMBOLS_ENV,
    _PREFILTERED_SYMBOLS_ENV,
    SqliteDbSource,
)


def _build_stock_db(
    db_path: Path,
    *,
    rows_a: list[tuple[str, ...]],
    rows_b: list[tuple[str, ...]],
) -> None:
    """Create a minimal sqlite DB with stocks + 2 symbols' rows."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            "CREATE TABLE daily_qfq ("
            "trade_date TEXT, ts_code TEXT, open REAL, high REAL, low REAL,"
            " close REAL, volume REAL, amount REAL, open_qfq REAL,"
            " high_qfq REAL, low_qfq REAL, close_qfq REAL)"
        )
        conn.executemany(
            "INSERT INTO stocks (ts_code, name) VALUES (?, ?)",
            [("000001.SZ", "甲"), ("000002.SZ", "乙")],
        )
        conn.executemany(
            "INSERT INTO daily_qfq VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows_a + rows_b,
        )


def _rows(symbol: str, start: str, end: str) -> list[tuple[str, ...]]:
    """Generate ~20 daily rows of synthetic OHLCV between YYYYMMDD dates."""
    out: list[tuple[str, ...]] = []
    y, m, d = int(start[:4]), int(start[4:6]), int(start[6:8])
    ey, em, ed = int(end[:4]), int(end[4:6]), int(end[6:8])
    cur = date(y, m, d)
    end_day = date(ey, em, ed)
    while cur <= end_day:
        td = cur.strftime("%Y%m%d")
        out.append(
            (
                td,
                symbol,
                10.0,
                10.5,
                9.5,
                10.2,
                1000.0,
                10200.0,
                10.0,
                10.5,
                9.5,
                10.2,
            )
        )
        cur = date.fromordinal(cur.toordinal() + 1)
    return out


@pytest.fixture()
def stock_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "astocks.db"
    _build_stock_db(
        db_path,
        rows_a=_rows("000001.SZ", "20240102", "20240131"),
        rows_b=_rows("000002.SZ", "20240601", "20240630"),
    )
    monkeypatch.setenv("AQSP_SQLITE_DB_PATH", str(db_path))
    return db_path


def test_fetch_daily_raises_when_some_symbols_empty_without_opt_in(
    stock_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_PREFILTERED_SYMBOLS_ENV, "1")
    monkeypatch.delenv(_ALLOW_EMPTY_SYMBOLS_ENV, raising=False)

    source = SqliteDbSource()
    with pytest.raises(DataError, match="缺少"):
        source.fetch_daily(
            ["000001", "000002"],
            date(2024, 1, 2),
            date(2024, 3, 1),
        )


def test_fetch_daily_skips_empty_symbols_with_opt_in(
    stock_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(_PREFILTERED_SYMBOLS_ENV, "1")
    monkeypatch.setenv(_ALLOW_EMPTY_SYMBOLS_ENV, "1")

    source = SqliteDbSource()
    with caplog.at_level("WARNING"):
        result = source.fetch_daily(
            ["000001", "000002"],
            date(2024, 1, 2),
            date(2024, 3, 1),
        )

    assert set(result) == {"000001"}
    assert any("缺" in rec.message for rec in caplog.records)


def test_fetch_daily_still_raises_when_all_symbols_empty(
    stock_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_PREFILTERED_SYMBOLS_ENV, "1")
    monkeypatch.setenv(_ALLOW_EMPTY_SYMBOLS_ENV, "1")

    source = SqliteDbSource()
    with pytest.raises(DataError, match="整个批次为空"):
        source.fetch_daily(
            ["000002"],
            date(2024, 1, 2),
            date(2024, 1, 15),
        )


def test_fetch_daily_does_not_allow_empty_without_prefiltered(
    stock_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Opt-in only takes effect when the caller has also declared prefiltered;
    # otherwise the strict coverage precheck runs first and rejects.
    monkeypatch.delenv(_PREFILTERED_SYMBOLS_ENV, raising=False)
    monkeypatch.setenv(_ALLOW_EMPTY_SYMBOLS_ENV, "1")

    source = SqliteDbSource()
    with pytest.raises(DataError):
        source.fetch_daily(
            ["000001", "000002"],
            date(2024, 1, 2),
            date(2024, 3, 1),
        )
