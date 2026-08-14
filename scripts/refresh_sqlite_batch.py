#!/usr/bin/env python3
"""Refresh one bounded SQLite daily-data batch and persist its rotation cursor."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root / "src"))
    sys.path.insert(0, str(project_root))

from aqsp.core.time import get_previous_trading_day, now_shanghai, today_shanghai
from aqsp.data.sqlite_db_source import SqliteDbSource
from aqsp.utils.jsonl_io import atomic_write_text
from scripts.update_sqlite_daily import UpdateSummary, update_sqlite_daily


_MAIN_BOARD_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")
_CHINEXT_PREFIXES = ("300", "301")


def _refresh_universe(
    source: SqliteDbSource,
    *,
    universe_limit: int,
    reference_day: date | None = None,
) -> list[str]:
    """Return the active, deterministic A-share refresh pool without turnover bias.

    ``stocks`` deliberately retains historical symbols for research.  A daily
    refresh must not treat those delisted records as missing current-day bars,
    so a prior completed trading day is used as the active-listing baseline.
    """
    boards: tuple[list[str], list[str], list[str]] = ([], [], [])
    for symbol in source.get_available_symbols():
        code = str(symbol).strip()
        name = source.get_symbol_name(code).upper().replace(" ", "")
        if "ST" in name or "退" in name:
            continue
        if code.startswith(_MAIN_BOARD_PREFIXES[:4]):
            boards[0].append(code)
        elif code.startswith(_MAIN_BOARD_PREFIXES[4:]):
            boards[1].append(code)
        elif code.startswith(_CHINEXT_PREFIXES):
            boards[2].append(code)

    ordered = [sorted(board) for board in boards]
    result: list[str] = []
    max_length = max((len(board) for board in ordered), default=0)
    for index in range(max_length):
        for board in ordered:
            if index < len(board):
                result.append(board[index])
    if reference_day is not None:
        covered = set(
            source.get_symbols_with_daily_coverage(
                result,
                reference_day,
                reference_day,
                min_rows=1,
                min_coverage_ratio=1.0,
            )
        )
        result = [symbol for symbol in result if symbol in covered]
    if not result:
        raise RuntimeError(
            "sqlite daily source has no active eligible main-board or ChiNext symbols"
        )
    return result[:universe_limit] if universe_limit > 0 else result


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
    batch_symbols: list[str] | None = None,
) -> None:
    existing_symbols: list[str] = []
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if (
            isinstance(existing, dict)
            and existing.get("target_day") == target_day.isoformat()
        ):
            raw_symbols = existing.get("target_day_symbols", [])
            if isinstance(raw_symbols, list):
                existing_symbols = [str(symbol) for symbol in raw_symbols if symbol]
    except (OSError, json.JSONDecodeError):
        pass
    target_day_symbols = list(dict.fromkeys(existing_symbols + (batch_symbols or [])))
    eligible_covered_count = len(target_day_symbols)
    payload = {
        "target_day": target_day.isoformat(),
        "offset": next_offset if next_offset < universe_size else 0,
        "universe_size": universe_size,
        "updated_at": now_shanghai().isoformat(timespec="seconds"),
        "target_day_symbols": target_day_symbols,
        "eligible_covered_count": eligible_covered_count,
        "eligible_coverage_pct": (
            eligible_covered_count / universe_size if universe_size else 0.0
        ),
        "last_batch": asdict(summary),
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _has_no_target_day_coverage(summary: UpdateSummary) -> bool:
    """Return whether an upstream outage left a processed batch entirely uncovered."""
    return (
        summary.processed_symbols > 0
        and summary.target_day_symbol_count == 0
        and (summary.empty_response_symbols > 0 or summary.failed_symbols > 0)
    )


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
    universe_symbols: list[str] | None = None,
) -> UpdateSummary:
    source = SqliteDbSource(db_path=db_path, cache=None)
    # Raw daily refresh must rotate the full supported market rather than a
    # turnover-ranked head. Liquidity is applied later by the research pipeline.
    del min_amount
    symbols = universe_symbols or _refresh_universe(
        source,
        universe_limit=universe_limit,
        reference_day=get_previous_trading_day(target_day),
    )
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
    covered_batch = source.get_symbols_with_daily_coverage(
        batch,
        target_day,
        target_day,
        min_rows=1,
        min_coverage_ratio=1.0,
    )
    next_offset = offset + summary.processed_symbols
    if _has_no_target_day_coverage(summary):
        # Do not rotate past a whole unavailable batch.  Advancing the cursor
        # turns a delayed provider publication into a full-market no-op cycle.
        next_offset = offset
    _write_cursor(
        state_path,
        target_day=target_day,
        next_offset=next_offset,
        universe_size=len(symbols),
        summary=summary,
        batch_symbols=covered_batch,
    )
    return summary


def refresh_batches(
    *,
    db_path: Path,
    state_path: Path,
    target_day: date,
    batch_size: int,
    universe_limit: int,
    min_amount: float,
    query_timeout_seconds: float,
    max_runtime_seconds: float,
    batches: int,
) -> UpdateSummary:
    """Run bounded sequential batches under one shared wall-clock budget."""
    started = time.monotonic()
    source = SqliteDbSource(db_path=db_path, cache=None)
    symbols = _refresh_universe(
        source,
        universe_limit=universe_limit,
        reference_day=get_previous_trading_day(target_day),
    )
    universe_size = len(symbols)
    max_batches = (
        (universe_size + batch_size - 1) // batch_size if batches <= 0 else batches
    )
    summaries: list[UpdateSummary] = []
    for _ in range(max_batches):
        remaining = max_runtime_seconds - (time.monotonic() - started)
        if remaining <= 0:
            break
        summary = refresh_batch(
            db_path=db_path,
            state_path=state_path,
            target_day=target_day,
            batch_size=batch_size,
            universe_limit=universe_limit,
            min_amount=min_amount,
            query_timeout_seconds=query_timeout_seconds,
            max_runtime_seconds=remaining,
            universe_symbols=symbols,
        )
        summaries.append(summary)
        if (
            summary.processed_symbols == 0
            or summary.budget_exhausted
            or _has_no_target_day_coverage(summary)
        ):
            break
    if not summaries:
        raise RuntimeError(
            "sqlite daily refresh exhausted its runtime before the first batch"
        )
    latest = summaries[-1]
    aggregate = UpdateSummary(
        updated_rows=sum(item.updated_rows for item in summaries),
        skipped_symbols=sum(item.skipped_symbols for item in summaries),
        failed_symbols=sum(item.failed_symbols for item in summaries),
        target_day=target_day,
        price_mode=latest.price_mode,
        target_day_symbol_count=latest.target_day_symbol_count,
        total_symbols=universe_size,
        raw_max_trade_date=latest.raw_max_trade_date,
        coverage_error=latest.coverage_error,
        processed_symbols=sum(item.processed_symbols for item in summaries),
        budget_exhausted=(
            time.monotonic() - started >= max_runtime_seconds or latest.budget_exhausted
        ),
        already_current_symbols=sum(item.already_current_symbols for item in summaries),
        empty_response_symbols=sum(item.empty_response_symbols for item in summaries),
    )
    next_offset = _read_cursor(state_path, target_day=target_day)
    _write_cursor(
        state_path,
        target_day=target_day,
        next_offset=next_offset,
        universe_size=universe_size,
        summary=aggregate,
    )
    return aggregate


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
    parser.add_argument(
        "--batches",
        type=int,
        default=1,
        help="sequential chunks; 0 means until one shared runtime budget expires",
    )
    args = parser.parse_args()
    if (
        args.batch_size <= 0
        or args.query_timeout_seconds <= 0
        or args.max_runtime_seconds <= 0
        or args.batches < 0
    ):
        raise SystemExit("batch size and timeouts must be positive")
    target_day = (
        date.fromisoformat(args.target_date) if args.target_date else today_shanghai()
    )
    summary = refresh_batches(
        db_path=args.db,
        state_path=args.state,
        target_day=target_day,
        batch_size=args.batch_size,
        universe_limit=args.universe_limit,
        min_amount=args.min_amount,
        query_timeout_seconds=args.query_timeout_seconds,
        max_runtime_seconds=args.max_runtime_seconds,
        batches=args.batches,
    )
    print(json.dumps(asdict(summary), default=str, ensure_ascii=False, sort_keys=True))
    if _has_no_target_day_coverage(summary):
        print(
            "sqlite daily refresh deferred: target-day data is unavailable; "
            "cursor retained for the next retry",
            file=sys.stderr,
        )
        # 75 is the conventional temporary-unavailable exit status.  The BT
        # wrapper records it as a bounded waiting state, not as a task failure.
        return 75
    return 0 if summary.failed_symbols == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

