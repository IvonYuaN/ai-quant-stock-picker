#!/usr/bin/env python3
"""Refresh one bounded SQLite daily-data batch and persist its rotation cursor."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aqsp.core.time import latest_completed_trading_day, now_shanghai
from aqsp.data.sqlite_db_source import SqliteDbSource
from aqsp.utils.jsonl_io import atomic_write_text
from scripts.update_sqlite_daily import UpdateSummary, update_sqlite_daily


def _read_cursor(path: Path, *, target_day: date) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if (
        not isinstance(payload, dict)
        or payload.get("target_day") != target_day.isoformat()
    ):
        return 0
    try:
        return max(0, int(payload.get("offset", 0)))
    except (TypeError, ValueError):
        return 0


def _write_cursor(
    path: Path,
    *,
    target_day: date,
    next_offset: int,
    universe_size: int,
    summary: UpdateSummary,
) -> None:
    payload = {
        "target_day": target_day.isoformat(),
        "offset": next_offset if next_offset < universe_size else 0,
        "universe_size": universe_size,
        "updated_at": now_shanghai().isoformat(timespec="seconds"),
        "last_batch": asdict(summary),
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False) + "\n")


def refresh_batch(
    *,
    db_path: Path,
    state_path: Path,
    target_day: date,
    batch_size: int,
    universe_limit: int,
    min_amount: float,
    query_timeout_seconds: float,
    max_runtime_seconds: float,
) -> UpdateSummary:
    source = SqliteDbSource(db_path=db_path, cache=None)
    symbols = source.get_liquid_symbols(limit=universe_limit, min_amount=min_amount)
    if not symbols:
        raise RuntimeError("sqlite daily source has no eligible A-share symbols")
    offset = _read_cursor(state_path, target_day=target_day) % len(symbols)
    batch = symbols[offset : offset + batch_size]
    if len(batch) < batch_size and offset:
        batch.extend(symbols[: batch_size - len(batch)])
    summary = update_sqlite_daily(
        db_path,
        target_day=target_day,
        sleep_seconds=0.05,
        limit=0,
        symbols=tuple(batch),
        price_mode="raw",
        query_timeout_seconds=query_timeout_seconds,
        max_runtime_seconds=max_runtime_seconds,
        require_target_coverage=False,
    )
    _write_cursor(
        state_path,
        target_day=target_day,
        next_offset=offset + summary.processed_symbols,
        universe_size=len(symbols),
        summary=summary,
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--target-date", default="")
    parser.add_argument("--batch-size", type=int, default=120)
    parser.add_argument("--universe-limit", type=int, default=0)
    parser.add_argument("--min-amount", type=float, default=50_000_000)
    parser.add_argument("--query-timeout-seconds", type=float, default=4.0)
    parser.add_argument("--max-runtime-seconds", type=float, default=480.0)
    args = parser.parse_args()
    if (
        args.batch_size <= 0
        or args.query_timeout_seconds <= 0
        or args.max_runtime_seconds <= 0
    ):
        raise SystemExit("batch size and timeouts must be positive")
    target_day = (
        date.fromisoformat(args.target_date)
        if args.target_date
        else latest_completed_trading_day()
    )
    summary = refresh_batch(
        db_path=args.db,
        state_path=args.state,
        target_day=target_day,
        batch_size=args.batch_size,
        universe_limit=args.universe_limit,
        min_amount=args.min_amount,
        query_timeout_seconds=args.query_timeout_seconds,
        max_runtime_seconds=args.max_runtime_seconds,
    )
    print(json.dumps(asdict(summary), default=str, ensure_ascii=False, sort_keys=True))
    return 0 if summary.failed_symbols == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
