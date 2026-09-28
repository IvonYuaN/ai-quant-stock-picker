#!/usr/bin/env python3
"""Import local TDX vipdoc raw bars into AQSP sqlite daily schema."""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aqsp.data.tdx_vipdoc_source import (
    TDX_DAY_RECORD,
    TDX_DAY_RECORD_SIZE,
    TdxVipdocSource,
)

SQLITE_TIMEOUT_SECONDS = 30.0
INSERT_BATCH_SIZE = 5000
START_DATE = date(2010, 1, 1)


@dataclass(frozen=True)
class ImportSummary:
    symbols_seen: int
    symbols_imported: int
    rows_written: int
    latest_trade_date: str


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


def _ts_code(symbol: str) -> str:
    if symbol.startswith(("6", "5", "9")):
        return f"{symbol}.SH"
    if symbol.startswith(("4", "8")):
        return f"{symbol}.BJ"
    return f"{symbol}.SZ"


def _existing_imported_ts_codes(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT DISTINCT ts_code FROM daily_qfq").fetchall()
    return {str(row[0]).strip() for row in rows if str(row[0]).strip()}


def _latest_trade_date_by_ts_code(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT ts_code, MAX(trade_date)
        FROM daily_qfq
        GROUP BY ts_code
        """
    ).fetchall()
    return {
        str(ts_code).strip(): str(trade_date).strip()
        for ts_code, trade_date in rows
        if str(ts_code).strip() and str(trade_date).strip()
    }


def _iter_symbol_rows(
    source: TdxVipdocSource,
    symbol: str,
    *,
    min_trade_date: str = "",
) -> list[tuple[object, ...]]:
    path = source._symbol_paths.get(symbol)  # noqa: SLF001
    if path is None or not path.exists():
        return []
    raw = path.read_bytes()
    if len(raw) % TDX_DAY_RECORD_SIZE != 0:
        return []
    rows: list[tuple[object, ...]] = []
    ts_code = _ts_code(symbol)
    for offset in range(0, len(raw), TDX_DAY_RECORD_SIZE):
        trade_date, open_, high, low, close, amount, volume, _reserved = (
            TDX_DAY_RECORD.unpack_from(raw, offset)
        )
        trade_day = date(
            trade_date // 10000,
            trade_date % 10000 // 100,
            trade_date % 100,
        )
        if trade_day < START_DATE:
            continue
        trade_day_text = trade_day.strftime("%Y%m%d")
        if min_trade_date and trade_day_text <= min_trade_date:
            continue
        open_price = open_ / 100.0
        high_price = high / 100.0
        low_price = low / 100.0
        close_price = close / 100.0
        rows.append(
            (
                ts_code,
                trade_day_text,
                open_price,
                high_price,
                low_price,
                close_price,
                int(volume),
                float(amount),
                open_price,
                high_price,
                low_price,
                close_price,
            )
        )
    return rows


def import_vipdoc_to_sqlite(
    *,
    vipdoc_path: Path,
    db_path: Path,
    rebuild: bool,
) -> ImportSummary:
    source = TdxVipdocSource(vipdoc_path=vipdoc_path)
    latest_trade_date = source._latest_market_date()  # noqa: SLF001
    all_symbols = sorted(source.get_available_symbols())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=SQLITE_TIMEOUT_SECONDS) as conn:
        ensure_schema(conn)
        if rebuild:
            conn.execute("DELETE FROM daily_qfq")
            conn.execute("DELETE FROM stocks")
            conn.commit()
        latest_trade_dates = {} if rebuild else _latest_trade_date_by_ts_code(conn)
        rows_written = 0
        symbols_imported = 0
        pending_rows: list[tuple[object, ...]] = []
        processed = 0
        for symbol in all_symbols:
            processed += 1
            ts_code = _ts_code(symbol)
            name = source._symbol_names.get(symbol, symbol)  # noqa: SLF001
            conn.execute(
                "INSERT OR REPLACE INTO stocks(ts_code, name) VALUES(?, ?)",
                (ts_code, name),
            )
            rows = _iter_symbol_rows(
                source,
                symbol,
                min_trade_date=latest_trade_dates.get(ts_code, ""),
            )
            if not rows:
                continue
            symbols_imported += 1
            latest_trade_dates[ts_code] = str(rows[-1][1])
            pending_rows.extend(rows)
            while len(pending_rows) >= INSERT_BATCH_SIZE:
                chunk = pending_rows[:INSERT_BATCH_SIZE]
                del pending_rows[:INSERT_BATCH_SIZE]
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO daily_qfq(
                        ts_code, trade_date, open, high, low, close_qfq,
                        volume, amount, open_qfq, high_qfq, low_qfq, close
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    chunk,
                )
                rows_written += len(chunk)
                conn.commit()
            if processed % 200 == 0:
                print(
                    f"vipdoc_import_progress processed={processed}/{len(all_symbols)} "
                    f"imported={symbols_imported} rows_written={rows_written}",
                    flush=True,
                )
        if pending_rows:
            conn.executemany(
                """
                INSERT OR REPLACE INTO daily_qfq(
                    ts_code, trade_date, open, high, low, close_qfq,
                    volume, amount, open_qfq, high_qfq, low_qfq, close
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                pending_rows,
            )
            rows_written += len(pending_rows)
        conn.commit()
    return ImportSummary(
        symbols_seen=len(source.get_available_symbols()),
        symbols_imported=symbols_imported,
        rows_written=rows_written,
        latest_trade_date=latest_trade_date.isoformat() if latest_trade_date else "",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vipdoc", default="private_data/tdx")
    parser.add_argument("--db", default="A股量化分析数据/astocks_raw.db")
    parser.add_argument("--rebuild", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = import_vipdoc_to_sqlite(
        vipdoc_path=Path(args.vipdoc).expanduser(),
        db_path=Path(args.db).expanduser(),
        rebuild=bool(args.rebuild),
    )
    print(
        "vipdoc_import "
        f"symbols_seen={summary.symbols_seen} "
        f"symbols_imported={summary.symbols_imported} "
        f"rows_written={summary.rows_written} "
        f"latest_trade_date={summary.latest_trade_date or '-'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
