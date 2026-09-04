#!/usr/bin/env python3
"""Backfill real index daily bars into the AQSP raw sqlite daily schema.

Rationale
---------
``SqliteDbSource.fetch_index`` resolves an index code (e.g. ``000300``) to a
``ts_code`` (``000300.SH``) through the ``stocks`` table, then reads its bars
from ``daily_qfq``.  The raw production DB only carries stock bars, so the
default walkforward benchmark ``000300`` (and the universe-pool indices) resolve
to nothing and the walkforward gate fails with ``sqlite_db 指数获取失败``.

This script fetches index daily bars from Tencent and upserts them into the
same two tables using the raw-DB convention (``close_qfq = close``, qfq
open/high/low left NULL — identical to ``refresh_sqlite_batch``).  It is
idempotent (``UNIQUE(ts_code, trade_date)``) and incremental (fetches only since
each index's latest stored date), so it is safe to run on a schedule for timely
updates.

Notes
-----
- Tencent caps a single daily request at 640 bars and returns the *most recent*
  640, so full history is fetched by walking backward in calendar-day chunks.
- ``SqliteDbSource._load_symbol_map`` keys the ``stocks`` table by the *bare*
  code (``ts_code.split(".")[0]``), so an index whose code collides with a real
  stock symbol would silently shadow that stock.  Three universe-pool index
  codes collide and are therefore excluded:
    * ``000001`` 上证指数 vs ``000001.SZ`` 平安银行
    * ``000905`` 中证500  vs ``000905.SZ`` 厦门港务
    * ``000852`` 中证1000 vs ``000852.SZ`` 石化机械
  Only ``000300`` (沪深300) — the walkforward benchmark, which is the sole
  index fetched via ``fetch_index`` — is backfilled.  The remaining pool
  indices are resolved through ``load_optional_index_constituents`` (Tushare
  point-in-time), not through ``fetch_index``, so they need no sqlite bars.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aqsp.core.errors import DataError
from aqsp.core.time import today_shanghai
from aqsp.data.cache import DataCache
from aqsp.data.tencent_source import TencentSource

SQLITE_TIMEOUT_SECONDS = 30.0
DEFAULT_START_DATE = date(2018, 1, 1)
# ~340 trading days per chunk, comfortably under Tencent's 640-bar response cap.
CHUNK_CALENDAR_DAYS = 500

# The walkforward benchmark ``000300`` is the only index fetched through
# ``fetch_index``.  Universe-pool index codes that collide with a real stock
# symbol in the shared ``stocks`` table (``000001``/``000905``/``000852``) are
# excluded — see the module docstring for the full collision list.
INDEX_UNIVERSE: tuple[tuple[str, str], ...] = (("000300", "沪深300"),)

_INSERT_DAILY_SQL = """
    INSERT OR REPLACE INTO daily_qfq(
        ts_code, trade_date, open, high, low, close_qfq, volume, amount, close
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


@dataclass(frozen=True)
class BackfillSummary:
    indices_seen: int
    indices_updated: int
    rows_written: int
    latest_trade_date: str


def _index_ts_code(code: str) -> str:
    if code.startswith(("399", "390")):
        return f"{code}.SZ"
    if code.startswith("899"):
        return f"{code}.BJ"
    return f"{code}.SH"


def _parse_trade_date(raw: str) -> date | None:
    text = str(raw or "").strip()
    if len(text) == 8 and text.isdigit():
        return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
    return None


def _to_trade_date(value: object) -> str:
    text = str(value).strip()
    return text.replace("-", "")[:8]


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stocks (ts_code TEXT PRIMARY KEY, name TEXT)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_qfq (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close_qfq REAL,
            volume INTEGER,
            amount REAL,
            open_qfq REAL,
            high_qfq REAL,
            low_qfq REAL,
            close REAL,
            UNIQUE(ts_code, trade_date)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_daily_qfq_tscode ON daily_qfq(ts_code)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_daily_qfq_date ON daily_qfq(trade_date)"
    )
    conn.commit()


def _latest_trade_date_by_ts_code(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT ts_code, MAX(trade_date) FROM daily_qfq GROUP BY ts_code"
    ).fetchall()
    return {
        str(ts_code).strip(): str(trade_date).strip()
        for ts_code, trade_date in rows
        if str(ts_code).strip() and str(trade_date).strip()
    }


def _iter_date_chunks(
    start: date, end: date, *, chunk_days: int = CHUNK_CALENDAR_DAYS
) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cursor = end
    while cursor >= start:
        chunk_start = max(start, cursor - timedelta(days=chunk_days))
        chunks.append((chunk_start, cursor))
        cursor = chunk_start - timedelta(days=1)
    return chunks


def _build_source() -> TencentSource:
    # A per-run throwaway cache keeps the backfill independent of any
    # persistent cache state; the raw sqlite DB is the source of truth.
    cache_db = Path(tempfile.gettempdir()) / "aqsp_index_backfill_cache.db"
    return TencentSource(cache=DataCache(db_path=cache_db))


def _fetch_index_rows(
    source: TencentSource, code: str, start: date, end: date
) -> list[tuple[object, ...]]:
    """Fetch one index's bars over ``[start, end]`` in backward chunks."""
    ts_code = _index_ts_code(code)
    end_str = end.strftime("%Y%m%d")
    by_date: dict[str, tuple[object, ...]] = {}
    for chunk_start, chunk_end in _iter_date_chunks(start, end):
        try:
            frame = source.fetch_index([code], chunk_start, chunk_end).get(code)
        except DataError:
            continue
        if frame is None or frame.empty:
            continue
        for rec in frame.to_dict(orient="records"):
            trade_date = _to_trade_date(rec.get("date"))
            if len(trade_date) != 8 or not trade_date.isdigit():
                continue
            if trade_date > end_str:
                continue
            close = float(rec["close"])
            by_date[trade_date] = (
                ts_code,
                trade_date,
                float(rec["open"]),
                float(rec["high"]),
                float(rec["low"]),
                close,
                int(float(rec["volume"])),
                float(rec["amount"]),
                close,
            )
    return [by_date[key] for key in sorted(by_date)]


def backfill_index(
    *,
    db_path: Path,
    start: date,
    end: date,
    index_universe: tuple[tuple[str, str], ...] = INDEX_UNIVERSE,
    rebuild: bool = False,
    source: TencentSource | None = None,
) -> BackfillSummary:
    source = source or _build_source()
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=SQLITE_TIMEOUT_SECONDS) as conn:
        ensure_schema(conn)
        latest_by_ts = {} if rebuild else _latest_trade_date_by_ts_code(conn)

    collected: dict[str, tuple[str, list[tuple[object, ...]]]] = {}
    latest_overall = ""
    for code, name in index_universe:
        ts_code = _index_ts_code(code)
        min_date = start
        if not rebuild:
            # Incremental: fetch strictly after each index's latest stored bar,
            # so ``rows_written`` reports only genuinely new rows and a fully
            # current index becomes a no-op.
            parsed_latest = _parse_trade_date(latest_by_ts.get(ts_code, ""))
            if parsed_latest is not None and parsed_latest >= start:
                min_date = parsed_latest + timedelta(days=1)
        rows = _fetch_index_rows(source, code, min_date, end)
        if not rows:
            continue
        collected[ts_code] = (name, rows)
        latest_overall = max(latest_overall, str(rows[-1][1]))

    rows_written = 0
    with sqlite3.connect(db_path, timeout=SQLITE_TIMEOUT_SECONDS) as conn:
        ensure_schema(conn)
        for ts_code, (name, rows) in collected.items():
            conn.execute(
                "INSERT OR REPLACE INTO stocks(ts_code, name) VALUES(?, ?)",
                (ts_code, name),
            )
            conn.executemany(_INSERT_DAILY_SQL, rows)
            rows_written += len(rows)
        conn.commit()

    return BackfillSummary(
        indices_seen=len(index_universe),
        indices_updated=len(collected),
        rows_written=rows_written,
        latest_trade_date=latest_overall,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument(
        "--start",
        default=DEFAULT_START_DATE.isoformat(),
        help="earliest date to backfill (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        default="",
        help="latest date to backfill (YYYY-MM-DD); defaults to today",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="drop existing index rows and refetch the full window",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else today_shanghai()
    if end < start:
        raise SystemExit("--end must be on or after --start")
    summary = backfill_index(
        db_path=args.db,
        start=start,
        end=end,
        rebuild=bool(args.rebuild),
    )
    print(
        "index_backfill "
        f"indices_seen={summary.indices_seen} "
        f"indices_updated={summary.indices_updated} "
        f"rows_written={summary.rows_written} "
        f"latest_trade_date={summary.latest_trade_date or '-'}"
    )
    return 0 if summary.indices_updated > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
