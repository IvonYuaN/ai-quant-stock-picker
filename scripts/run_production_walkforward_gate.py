#!/usr/bin/env python3
"""Run the production short-line walk-forward gate.

This wrapper is intentionally stricter than ad-hoc `aqsp walkforward` calls:
it requires a raw sqlite database with full-market coverage before it starts the
expensive gate run. A 300-symbol run is only a smoke test and must not be used as
before-live evidence.
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aqsp.data.sqlite_db_source import SqliteDbSource

MIN_PRODUCTION_GATE_SYMBOLS = 3000
DEFAULT_RAW_DB = Path("/opt/market-data/astocks_raw.db")
DEFAULT_START = "2018-01-01"
DEFAULT_END = "2024-12-31"


@dataclass(frozen=True)
class CoverageSummary:
    stock_symbols: int
    covered_symbols: int
    rows: int
    first_trade_date: str
    last_trade_date: str


def _compact_day(raw: str) -> str:
    return date.fromisoformat(raw).strftime("%Y%m%d")


def inspect_raw_coverage(db_path: Path, *, start: str, end: str) -> CoverageSummary:
    if not db_path.exists():
        raise SystemExit(f"raw sqlite db missing: {db_path}")
    source = SqliteDbSource(db_path=db_path, cache=None)
    price_mode = source.price_mode()
    if price_mode != "raw":
        raise SystemExit(
            f"production gate requires raw sqlite db, got price_mode={price_mode}: {db_path}"
        )

    start_day = date.fromisoformat(start)
    end_day = date.fromisoformat(end)
    available = source.get_available_symbols()
    covered = source.get_symbols_with_daily_coverage(
        available,
        start_day,
        end_day,
        min_rows=None,
    )
    start_str = _compact_day(start)
    end_str = _compact_day(end)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*), MIN(trade_date), MAX(trade_date)
            FROM daily_qfq
            WHERE trade_date >= ? AND trade_date <= ?
            """,
            (start_str, end_str),
        ).fetchone()
    return CoverageSummary(
        stock_symbols=len(available),
        covered_symbols=len(covered),
        rows=int(row[0] or 0),
        first_trade_date=str(row[1] or ""),
        last_trade_date=str(row[2] or ""),
    )


def build_walkforward_command(args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        "-m",
        "aqsp",
        "walkforward",
        "--source",
        "sqlite_db",
        "--pool",
        "all",
        "--start",
        args.start,
        "--end",
        args.end,
        "--grid-cscv",
        "--grid-profile",
        args.grid_profile,
        "--skip-pit-financials",
        "--report",
        args.report,
        "--cache-path",
        args.cache_path,
        "--log",
        args.log,
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_RAW_DB)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--min-symbols", type=int, default=MIN_PRODUCTION_GATE_SYMBOLS)
    parser.add_argument(
        "--grid-profile", choices=("stable", "exploratory"), default="stable"
    )
    parser.add_argument(
        "--report", default="reports/walkforward-grid-raw-production-latest.md"
    )
    parser.add_argument(
        "--cache-path", default="data/walkforward_raw_production_cache.db"
    )
    parser.add_argument("--log", default="logs/walkforward-raw-production.log")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    coverage = inspect_raw_coverage(args.db, start=args.start, end=args.end)
    print(
        "production gate raw coverage: "
        f"stocks={coverage.stock_symbols} covered={coverage.covered_symbols} "
        f"rows={coverage.rows} range={coverage.first_trade_date}..{coverage.last_trade_date}"
    )
    if coverage.covered_symbols < args.min_symbols:
        print(
            "BLOCK: raw full-market coverage is insufficient; "
            f"need {args.min_symbols}, got {coverage.covered_symbols}."
        )
        print(
            "Backfill missing raw history first: .venv/bin/python "
            "scripts/update_sqlite_daily.py "
            f"{args.db} --price-mode raw --start-date {args.start} "
            f"--target-date {args.end} --fill-history-gaps --limit 0"
        )
        print(
            "Only for a clean rebuild, append --force-from-start after taking a database backup."
        )
        return 2

    command = build_walkforward_command(args)
    print("production gate command:", " ".join(command))
    if args.dry_run:
        return 0
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
