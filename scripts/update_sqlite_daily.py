#!/usr/bin/env python3
"""Backfill baostock daily data for the local sqlite source.

The legacy server updater only requested today's bar. If a symbol missed one or
more trading days, it stayed stale forever. This updater starts from each symbol's latest stored trade_date + 1 day by default.
Use --start-date with --fill-history-gaps for production raw backfills that must
repair symbols with partial recent rows without refetching complete symbols.
Use --force-from-start only for a clean rebuild after taking a database backup.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aqsp.core.time import get_previous_trading_day, is_trading_day, today_shanghai


@dataclass(frozen=True)
class UpdateSummary:
    updated_rows: int
    skipped_symbols: int
    failed_symbols: int
    target_day: date
    price_mode: str


def _parse_trade_date(raw: object) -> date | None:
    text = str(raw or "").strip()
    if len(text) == 8 and text.isdigit():
        return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:8]}")
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _to_compact(d: date) -> str:
    return d.strftime("%Y%m%d")


def _next_calendar_day(d: date) -> date:
    return d + timedelta(days=1)


def _target_trade_day(raw: str) -> date:
    if raw:
        target = date.fromisoformat(raw)
    else:
        current = today_shanghai()
        target = (
            current if is_trading_day(current) else get_previous_trading_day(current)
        )
    if not is_trading_day(target):
        target = get_previous_trading_day(target)
    return target


def _normalize_requested_symbol(raw: str) -> str:
    text = str(raw).strip().upper()
    if not text:
        return ""
    if "." in text:
        code, market = text.split(".", 1)
        if market in {"SH", "SZ"}:
            return f"{code}.{market}"
        if code in {"SH", "SZ"}:
            return f"{market}.{code}"
    return text


def _bs_code(ts_code: str) -> str:
    code, market = ts_code.split(".")
    return f"{market.lower()}.{code}"


def _is_a_share_bs_code(code: str) -> bool:
    return re.match(r"(sh\.60|sz\.00|sz\.30|sh\.68)\d+", code) is not None


def _load_baostock() -> Any:
    try:
        import baostock as bs  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on server env
        raise SystemExit("baostock is required for sqlite daily update") from exc
    return bs


def ensure_schema(conn: sqlite3.Connection) -> None:
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
    conn.commit()


def sync_stock_list(conn: sqlite3.Connection, bs: Any) -> list[str]:
    rs = bs.query_stock_basic()
    bs_a_codes: set[str] = set()
    name_map: dict[str, str] = {}
    while rs.error_code == "0" and rs.next():
        row = rs.get_row_data()
        code = row[0]
        name = row[1]
        if _is_a_share_bs_code(code):
            market, number = code.split(".")
            ts_code = f"{number}.{market.upper()}"
            bs_a_codes.add(ts_code)
            name_map[ts_code] = name

    cur = conn.cursor()
    existing = {row[0] for row in cur.execute("SELECT ts_code FROM stocks")}
    for ts_code in sorted(bs_a_codes - existing):
        cur.execute(
            "INSERT OR IGNORE INTO stocks(ts_code, name) VALUES(?, ?)",
            (ts_code, name_map.get(ts_code, "")),
        )
    for ts_code in sorted(existing - bs_a_codes):
        cur.execute("DELETE FROM stocks WHERE ts_code = ?", (ts_code,))
        cur.execute("DELETE FROM daily_qfq WHERE ts_code = ?", (ts_code,))
    conn.commit()
    return [
        row[0] for row in cur.execute("SELECT ts_code FROM stocks ORDER BY ts_code")
    ]


def _symbol_date_bounds(
    conn: sqlite3.Connection, ts_code: str
) -> tuple[date | None, date | None]:
    row = conn.execute(
        """
        SELECT MIN(CAST(trade_date AS TEXT)), MAX(CAST(trade_date AS TEXT))
        FROM daily_qfq
        WHERE ts_code = ? AND trade_date != 'SKIP'
        """,
        (ts_code,),
    ).fetchone()
    if not row:
        return None, None
    return _parse_trade_date(row[0]), _parse_trade_date(row[1])


def _resolve_fetch_start_day(
    *,
    first: date | None,
    latest: date | None,
    start_day: date | None,
    target_day: date,
    force_from_start: bool,
    fill_history_gaps: bool,
) -> date:
    if force_from_start and start_day is not None:
        return start_day
    if fill_history_gaps and start_day is not None:
        # Calendar files may not include every old exchange holiday. Treat a
        # first stored row within the opening week as covered, while still
        # repairing symbols that only have recent partial history.
        prefix_grace_day = start_day + timedelta(days=7)
        if first is None or first > prefix_grace_day:
            return start_day
    if latest is not None:
        return _next_calendar_day(latest)
    return start_day or target_day


def _latest_symbol_date(conn: sqlite3.Connection, ts_code: str) -> date | None:
    return _symbol_date_bounds(conn, ts_code)[1]


def _adjustflag_for_price_mode(price_mode: str) -> str:
    if price_mode == "raw":
        return "1"
    if price_mode == "qfq":
        return "2"
    raise ValueError(f"unsupported price_mode: {price_mode}")


def _insert_bar(conn: sqlite3.Connection, ts_code: str, row: list[str]) -> bool:
    if len(row) < 7 or not row[4]:
        return False
    trade_day = row[0].replace("-", "")
    open_price = float(row[1]) if row[1] else None
    high = float(row[2]) if row[2] else None
    low = float(row[3]) if row[3] else None
    close = float(row[4]) if row[4] else None
    volume = int(float(row[5])) if row[5] else None
    amount = float(row[6]) if row[6] else None
    conn.execute(
        """
        INSERT OR REPLACE INTO daily_qfq(
            ts_code, trade_date, open, high, low, close_qfq, volume, amount, close
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ts_code, trade_day, open_price, high, low, close, volume, amount, close),
    )
    return True


def update_sqlite_daily(
    db_path: Path,
    *,
    target_day: date,
    sleep_seconds: float,
    limit: int,
    symbols: tuple[str, ...] = (),
    start_day: date | None = None,
    force_from_start: bool = False,
    fill_history_gaps: bool = False,
    price_mode: str = "qfq",
) -> UpdateSummary:
    bs = _load_baostock()
    login = bs.login()
    if login.error_code != "0":
        raise SystemExit(f"Baostock login failed: {login.error_msg}")

    updated_rows = 0
    skipped = 0
    failed = 0
    try:
        with sqlite3.connect(db_path) as conn:
            ensure_schema(conn)
            all_symbols = sync_stock_list(conn, bs)
            requested = {_normalize_requested_symbol(item) for item in symbols if item}
            selected_symbols = [
                ts_code
                for ts_code in all_symbols
                if not requested
                or ts_code in requested
                or ts_code.split(".")[0] in requested
            ]
            if limit > 0:
                selected_symbols = selected_symbols[:limit]
            for index, ts_code in enumerate(selected_symbols, start=1):
                first, latest = _symbol_date_bounds(conn, ts_code)
                fetch_start_day = _resolve_fetch_start_day(
                    first=first,
                    latest=latest,
                    start_day=start_day,
                    target_day=target_day,
                    force_from_start=force_from_start,
                    fill_history_gaps=fill_history_gaps,
                )
                if fetch_start_day > target_day:
                    skipped += 1
                    continue
                rs = bs.query_history_k_data_plus(
                    code=_bs_code(ts_code),
                    fields="date,open,high,low,close,volume,amount",
                    start_date=fetch_start_day.isoformat(),
                    end_date=target_day.isoformat(),
                    frequency="d",
                    adjustflag=_adjustflag_for_price_mode(price_mode),
                )
                if rs.error_code != "0":
                    failed += 1
                    continue
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
                inserted = 0
                for row in rows:
                    if _insert_bar(conn, ts_code, row):
                        inserted += 1
                if inserted:
                    updated_rows += inserted
                else:
                    skipped += 1
                if index % 200 == 0:
                    conn.commit()
                    print(
                        f"进度: {index}/{len(selected_symbols)} | 更新行:{updated_rows} 跳过:{skipped} 失败:{failed}",
                        flush=True,
                    )
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
            conn.commit()
    finally:
        bs.logout()
    return UpdateSummary(updated_rows, skipped, failed, target_day, price_mode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", type=Path, help="sqlite db path")
    parser.add_argument(
        "--target-date",
        default="",
        help="YYYY-MM-DD, default previous/current trading day",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.05)
    parser.add_argument(
        "--limit", type=int, default=0, help="test hook: update first N symbols"
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="comma-separated test/repair hook, accepts 600519 or 600519.SH",
    )
    parser.add_argument(
        "--start-date",
        default="",
        help="YYYY-MM-DD historical backfill start; default incremental only",
    )
    parser.add_argument(
        "--fill-history-gaps",
        action="store_true",
        help="with --start-date, repair symbols whose first stored row is later than the requested start",
    )
    parser.add_argument(
        "--force-from-start",
        action="store_true",
        help="refetch from --start-date even if newer rows already exist",
    )
    parser.add_argument(
        "--price-mode",
        choices=("qfq", "raw"),
        default="qfq",
        help="baostock adjustment mode: qfq keeps legacy behavior; raw writes unadjusted prices",
    )
    args = parser.parse_args()

    if not args.db.exists():
        if args.price_mode != "raw":
            raise SystemExit(f"database does not exist: {args.db}")
        args.db.parent.mkdir(parents=True, exist_ok=True)
    target = _target_trade_day(args.target_date)
    start_day = date.fromisoformat(args.start_date) if args.start_date else None
    if args.force_from_start and start_day is None:
        raise SystemExit("--force-from-start requires --start-date")
    if args.fill_history_gaps and start_day is None:
        raise SystemExit("--fill-history-gaps requires --start-date")
    print(
        f"sqlite daily backfill target={target.isoformat()} "
        f"start={start_day.isoformat() if start_day else 'incremental'} "
        f"fill_history_gaps={args.fill_history_gaps} "
        f"price_mode={args.price_mode} db={args.db}"
    )
    summary = update_sqlite_daily(
        args.db,
        target_day=target,
        sleep_seconds=args.sleep_seconds,
        limit=args.limit,
        symbols=tuple(item.strip() for item in args.symbols.split(",") if item.strip()),
        start_day=start_day,
        force_from_start=args.force_from_start,
        fill_history_gaps=args.fill_history_gaps,
        price_mode=args.price_mode,
    )
    print(
        "sqlite daily backfill done: "
        f"updated_rows={summary.updated_rows} "
        f"skipped_symbols={summary.skipped_symbols} "
        f"failed_symbols={summary.failed_symbols} "
        f"target={summary.target_day.isoformat()} "
        f"price_mode={summary.price_mode}"
    )
    return 1 if summary.failed_symbols else 0


if __name__ == "__main__":
    raise SystemExit(main())
