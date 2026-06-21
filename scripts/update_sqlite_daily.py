#!/usr/bin/env python3
"""Backfill baostock daily data for the local sqlite source.

The legacy server updater only requested today's bar. If a symbol missed one or
more trading days, it stayed stale forever. This updater starts from each
symbol's latest stored trade_date + 1 day and asks baostock for the whole gap.
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


def _latest_symbol_date(conn: sqlite3.Connection, ts_code: str) -> date | None:
    row = conn.execute(
        """
        SELECT CAST(trade_date AS TEXT)
        FROM daily_qfq
        WHERE ts_code = ? AND trade_date != 'SKIP'
        ORDER BY trade_date DESC
        LIMIT 1
        """,
        (ts_code,),
    ).fetchone()
    return _parse_trade_date(row[0]) if row else None


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
                latest = _latest_symbol_date(conn, ts_code)
                start_day = _next_calendar_day(latest) if latest else target_day
                if start_day > target_day:
                    skipped += 1
                    continue
                rs = bs.query_history_k_data_plus(
                    code=_bs_code(ts_code),
                    fields="date,open,high,low,close,volume,amount",
                    start_date=start_day.isoformat(),
                    end_date=target_day.isoformat(),
                    frequency="d",
                    adjustflag="2",
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
    return UpdateSummary(updated_rows, skipped, failed, target_day)


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
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"database does not exist: {args.db}")
    target = _target_trade_day(args.target_date)
    print(f"sqlite daily backfill target={target.isoformat()} db={args.db}")
    summary = update_sqlite_daily(
        args.db,
        target_day=target,
        sleep_seconds=args.sleep_seconds,
        limit=args.limit,
        symbols=tuple(item.strip() for item in args.symbols.split(",") if item.strip()),
    )
    print(
        "sqlite daily backfill done: "
        f"updated_rows={summary.updated_rows} "
        f"skipped_symbols={summary.skipped_symbols} "
        f"failed_symbols={summary.failed_symbols} "
        f"target={summary.target_day.isoformat()}"
    )
    return 1 if summary.failed_symbols else 0


if __name__ == "__main__":
    raise SystemExit(main())
