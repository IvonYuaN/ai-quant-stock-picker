#!/usr/bin/env python3
"""Check that the raw sqlite source is not silently stale.

The 2026-09 data freeze went unnoticed for ~6 days because nothing
monitored the SOURCE update itself -- the dual-window staleness guard only
flags stale IC *output*, after the fact. This check compares the db's
MAX(trade_date) against the last *closed* trading day (holiday-aware, via
aqsp.core.time) and exits non-zero (+ writes a flag file) when the source
has fallen behind.

#402：freshness 曾经「假绿」——周六推库把 runner 的库换成 prod 的新库后
`[DATA FRESH] behind=0`，但日更其实几天没成功过。现在同时读
`update_sqlite_daily` 干净成功时写的 `<db>.update_stamp.json`：库内
MAX(trade_date) 与印章记录不一致 ⇒ 数据是推库换来的、不是日更，日志行里
直接点亮（`data_source=push(...)`），不再把推库的新鲜误当成日更的健康。

Self-contained: inserts repo/src into sys.path like update_sqlite_daily.py
(see PR #291) so it does not depend on the caller setting PYTHONPATH.
"""
from __future__ import annotations

import argparse
import json
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


def _read_update_stamp(db: Path) -> dict | None:
    """#402：读「上次成功日更」印章（update_sqlite_daily 干净成功时才写）。"""
    stamp = db.with_name(db.name + ".update_stamp.json")
    try:
        payload = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _provenance_note(db: Path, actual: date | None) -> str:
    """数据来源注记：库内 MAX(trade_date) 与上次成功日更记录不一致 ⇒ 推库换的。"""
    if actual is None:
        return ""
    stamp = _read_update_stamp(db)
    if stamp is None:
        return " last_daily_update=NONE"
    finished = str(stamp.get("finished_at") or "?")
    stamp_max = str(stamp.get("db_max_trade_date") or "")
    if not stamp_max or stamp_max == "None":
        return f" last_daily_update={finished} stamp_db_max=NONE"
    if stamp_max != actual.isoformat():
        return (
            f" last_daily_update={finished} stamp_db_max={stamp_max}"
            f" data_source=push(库内 MAX(trade_date)={actual.isoformat()} 与上次成功"
            f"日更记录 {stamp_max} 不一致：数据很可能来自推库换库而非日更)"
        )
    return f" last_daily_update={finished} stamp_db_max={stamp_max}"


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
    detail += _provenance_note(args.db, actual)

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
