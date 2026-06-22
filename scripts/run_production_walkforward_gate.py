#!/usr/bin/env python3
"""Run the production short-line walk-forward gate.

This wrapper is intentionally stricter than ad-hoc `aqsp walkforward` calls:
it requires a raw sqlite database with full-market coverage before it starts the
expensive gate run. A 300-symbol run is only a smoke test and must not be used as
before-live evidence.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
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


def _raw_sqlite_source(db_path: Path) -> SqliteDbSource:
    if not db_path.exists():
        raise SystemExit(f"raw sqlite db missing: {db_path}")
    source = SqliteDbSource(db_path=db_path, cache=None)
    price_mode = source.price_mode()
    if price_mode != "raw":
        raise SystemExit(
            f"production gate requires raw sqlite db, got price_mode={price_mode}: {db_path}"
        )
    return source


def select_covered_symbols(db_path: Path, *, start: str, end: str) -> list[str]:
    source = _raw_sqlite_source(db_path)
    return source.get_symbols_with_daily_coverage(
        source.get_available_symbols(),
        date.fromisoformat(start),
        date.fromisoformat(end),
        min_rows=None,
    )


def inspect_raw_coverage(db_path: Path, *, start: str, end: str) -> CoverageSummary:
    source = _raw_sqlite_source(db_path)
    available = source.get_available_symbols()
    covered = source.get_symbols_with_daily_coverage(
        available,
        date.fromisoformat(start),
        date.fromisoformat(end),
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
        *(
            ["--symbols-file", args.symbols_file]
            if getattr(args, "symbols_file", "")
            else []
        ),
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
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=7200,
        help="stop the production walk-forward run if it hangs during data loading/backtest",
    )
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

    covered_symbols = select_covered_symbols(args.db, start=args.start, end=args.end)
    if len(covered_symbols) < args.min_symbols:
        print(
            "BLOCK: selected production symbols are insufficient; "
            f"need {args.min_symbols}, got {len(covered_symbols)}."
        )
        return 2

    tmp_symbols_path: Path | None = None
    if not args.dry_run:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            prefix="aqsp-walkforward-symbols-",
            suffix=".txt",
            delete=False,
        ) as tmp_symbols:
            tmp_symbols.write("\n".join(covered_symbols) + "\n")
            tmp_symbols_path = Path(tmp_symbols.name)
        args.symbols_file = str(tmp_symbols_path)
        print(f"production gate selected symbols: {len(covered_symbols)}")

    command = build_walkforward_command(args)
    print("production gate command:", " ".join(command))
    if args.dry_run:
        return 0
    env = os.environ.copy()
    env["AQSP_SQLITE_DB_PATH"] = str(args.db)
    try:
        return subprocess.run(
            command,
            check=False,
            env=env,
            timeout=args.timeout_seconds if args.timeout_seconds > 0 else None,
        ).returncode
    except subprocess.TimeoutExpired:
        print(
            "BLOCK: production walk-forward timed out; "
            f"timeout_seconds={args.timeout_seconds}. "
            "Reduce the covered symbol batch or inspect sqlite fetch performance."
        )
        return 124
    finally:
        if tmp_symbols_path is not None:
            tmp_symbols_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
