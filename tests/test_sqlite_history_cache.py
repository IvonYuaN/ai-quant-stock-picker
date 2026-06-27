from __future__ import annotations

from datetime import date
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from aqsp.data.cache import DataCache
from aqsp.data.sqlite_db_source import SqliteDbSource


def _make_sqlite_daily_db(path: Path, *, symbols: int, days: int) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            """
            CREATE TABLE daily_qfq (
                ts_code TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                open_qfq REAL,
                high_qfq REAL,
                low_qfq REAL,
                close_qfq REAL,
                volume REAL,
                amount REAL
            )
            """
        )
        for idx in range(symbols):
            market = "SH" if idx % 2 == 0 else "SZ"
            code = f"600{idx:03d}.{market}"
            conn.execute(
                "INSERT INTO stocks (ts_code, name) VALUES (?, ?)", (code, code)
            )
            for day in range(1, days + 1):
                conn.execute(
                    """
                    INSERT INTO daily_qfq (
                        ts_code, trade_date, open, high, low, close,
                        open_qfq, high_qfq, low_qfq, close_qfq, volume, amount
                    ) VALUES (?, ?, 10, 11, 9, ?, 8, 9, 7, ?, 1000, ?)
                    """,
                    (
                        code,
                        f"202401{day:02d}",
                        10 + idx,
                        8 + idx,
                        10000,
                    ),
                )


def test_sqlite_db_source_fetch_daily_reuses_historical_cache_without_db_reads(
    tmp_path: Path, monkeypatch
) -> None:
    import aqsp.data.sqlite_db_source as sqlite_mod

    db_path = tmp_path / "astocks_raw.db"
    cache_path = tmp_path / "walkforward_cache.db"
    _make_sqlite_daily_db(db_path, symbols=1, days=30)
    source = SqliteDbSource(db_path=db_path, cache=DataCache(db_path=cache_path))

    first = source.fetch_daily(
        ["600000"],
        date(2024, 1, 1),
        date(2024, 1, 30),
    )
    assert "600000" in first

    stale_cutoff = "2026-01-01T00:00:00+08:00"
    with sqlite3.connect(cache_path) as conn:
        conn.execute("UPDATE ohlcv SET fetched_at = ?", (stale_cutoff,))
        conn.commit()

    def fail_read_sql(*_args, **_kwargs):
        raise AssertionError("historical cache should prevent sqlite daily reload")

    monkeypatch.setenv("AQSP_SQLITE_PREFILTERED_SYMBOLS", "1")
    monkeypatch.setattr(sqlite_mod, "pd", SimpleNamespace(read_sql=fail_read_sql))

    second = source.fetch_daily(
        ["600000"],
        date(2024, 1, 1),
        date(2024, 1, 30),
    )

    assert "600000" in second
    assert second["600000"]["date"].tolist()[:2] == ["2024-01-01", "2024-01-02"]
