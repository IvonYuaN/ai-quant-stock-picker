from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.run_production_walkforward_gate import (
    build_walkforward_command,
    inspect_raw_coverage,
)


def _make_raw_db(path: Path, symbols: int = 3) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE stocks(ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            """
            CREATE TABLE daily_qfq(
                ts_code TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                close_qfq REAL,
                volume INTEGER,
                amount REAL
            )
            """
        )
        for idx in range(symbols):
            market = "SH" if idx % 2 == 0 else "SZ"
            code = f"600{idx:03d}.{market}"
            conn.execute("INSERT INTO stocks(ts_code, name) VALUES(?, ?)", (code, code))
            for day in range(1, 31):
                conn.execute(
                    """
                    INSERT INTO daily_qfq(
                        ts_code, trade_date, open, high, low, close, close_qfq, volume, amount
                    ) VALUES(?, ?, 10, 11, 9, 10, 10, 1000000, 10000000)
                    """,
                    (code, f"202401{day:02d}"),
                )
        conn.commit()


def test_inspect_raw_coverage_counts_covered_symbols(tmp_path: Path) -> None:
    db = tmp_path / "astocks_raw.db"
    _make_raw_db(db, symbols=4)

    coverage = inspect_raw_coverage(db, start="2024-01-01", end="2024-01-30")

    assert coverage.stock_symbols == 4
    assert coverage.covered_symbols == 4
    assert coverage.rows == 120
    assert coverage.first_trade_date == "20240101"
    assert coverage.last_trade_date == "20240130"


def test_build_walkforward_command_uses_full_market_raw_gate() -> None:
    class Args:
        start = "2018-01-01"
        end = "2024-12-31"
        grid_profile = "stable"
        report = "reports/prod.md"
        cache_path = "data/prod.db"
        log = "logs/prod.log"

    command = build_walkforward_command(Args())

    assert "walkforward" in command
    assert "--pool" in command
    assert "all" in command
    assert "--grid-cscv" in command
    assert "--grid-profile" in command
    assert "stable" in command
    assert "--skip-pit-financials" in command
