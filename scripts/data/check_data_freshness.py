#!/usr/bin/env python3
"""Check that the raw sqlite source is not silently stale.

The 2026-09 data freeze went unnoticed for ~6 days because nothing
monitored the SOURCE update itself -- the dual-window staleness guard only
flags stale IC *output*, after the fact. This check compares the db's
MAX(trade_date) against the last *closed* trading day (holiday-aware, via
aqsp.core.time) and exits non-zero (+ writes a flag file) when the source
has fallen behind.

Self-contained: inserts repo/src into sys.path like update_sqlite_daily.py
(see PR #291) so it does not depend on the caller setting PYTHONPATH.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from aqsp.core.time import get_previous_trading_day, today_shanghai


def _n_trading_days_before(d: date, n: int) -> date:
    cur = d
    for _ in range(n):
        cur = get_previous_trading_day(cur)
    return cur


def _db_max_trade_date(db: Path, table: str) -> date | None:
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            f"SELECT MAX(CAST(trade_date AS TEXT)) FROM {table} "
            "WHERE trade_date != 'SKIP'"
        ).fetchone()
    if not row or not row[0]:
        return None
    s = str(row[0])
    if len(s) == 8 and s.isdigit():
        return date.fromisoformat(f"{s[:4]}-{s[4:6]}-{s[6:8]}")
    return date.fromisoformat(s[:10])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("db", type=Path, help="sqlite db path")
    p.add_argument("--table", default="daily_qfq", help="table holding trade_date")
    p.add_argument(
        "--max-lag-days",
        type=int,
        default=1,
        help="allow data to lag this many trading days behind the last closed "
        "session (default 1; set 0 for strictest)",
    )
    p.add_argument(
        "--flag-file",
        default="",
        help="write this file when stale, remove it when fresh (observable artifact)",
    )
    p.add_argument(
        "--warn-only",
        action="store_true",
        help="always exit 0 (report only, e.g. for a non-fatal log line)",
    )
    args = p.parse_args()

    expected = get_previous_trading_day(today_shanghai())
    # Freshness is measured in TRADING days (not calendar days) so a weekend or
    # holiday gap does not false-alarm. Data is fresh if its MAX(trade_date)
    # is within max_lag_days trading days of the last closed session.
    threshold = _n_trading_days_before(expected, args.max_lag_days)
    actual = _db_max_trade_date(args.db, args.table)
    stale = (actual is None) or (actual < threshold)

    lag_cal = (expected - actual).days if actual else 9999
    detail = f"expected_last_trading_day={expected.isoformat()}"
    detail += f" db_max={actual.isoformat() if actual else 'NONE'} behind_by_calendar_days={lag_cal}"

    if args.flag_file:
        flag = Path(args.flag_file)
        if stale:
            flag.write_text(f"STALE {detail}\n")
        else:
            flag.unlink(missing_ok=True)

    status = "STALE" if stale else "FRESH"
    print(f"[DATA {status}] {detail}", flush=True)
    return 0 if args.warn_only else (2 if stale else 0)


if __name__ == "__main__":
    raise SystemExit(main())
