#!/usr/bin/env python3
"""Build a clean raw SQLite market database in resumable bounded batches."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root / "src"))
    sys.path.insert(0, str(project_root))

from aqsp.core.time import now_shanghai
from aqsp.data.sqlite_db_source import SqliteDbSource
from aqsp.utils.jsonl_io import atomic_write_text
from scripts.update_sqlite_daily import (
    UpdateSummary,
    ensure_schema,
    update_sqlite_daily,
)


_MAIN_BOARD_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")
_CHINEXT_PREFIXES = ("300", "301")


@dataclass(frozen=True)
class RebuildSummary:
    target_day: date
    processed_symbols: int
    covered_symbols: int
    universe_size: int
    coverage_ratio: float
    next_offset: int
    complete: bool
    publish_ready: bool
    update: UpdateSummary


def _eligible_symbols(db_path: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT ts_code, name FROM stocks ORDER BY ts_code"
        ).fetchall()
    symbols: list[tuple[str, str]] = []
    for raw_code, raw_name in rows:
        ts_code = str(raw_code or "").strip().upper()
        code = ts_code.split(".", 1)[0]
        name = str(raw_name or "").upper().replace(" ", "")
        if not code.startswith(_MAIN_BOARD_PREFIXES + _CHINEXT_PREFIXES):
            continue
        if "ST" in name or "退" in name:
            continue
        symbols.append((ts_code, str(raw_name or "")))
    if not symbols:
        raise RuntimeError(
            "source sqlite has no eligible main-board or ChiNext symbols"
        )
    return symbols


def _seed_candidate_database(source_db: Path, candidate_db: Path) -> list[str]:
    symbols = _eligible_symbols(source_db)
    candidate_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(candidate_db) as conn:
        ensure_schema(conn)
        existing = int(conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0])
        if existing == 0:
            conn.executemany(
                "INSERT OR REPLACE INTO stocks(ts_code, name) VALUES(?, ?)", symbols
            )
            conn.commit()
    return [code for code, _ in symbols]


def _read_state(
    state_path: Path, target_day: date, universe_size: int
) -> tuple[int, set[str]]:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        if payload.get("target_day") != target_day.isoformat():
            return 0, set()
        covered = payload.get("covered_ts_codes", [])
        symbols = {str(item) for item in covered if isinstance(item, str)}
        return max(0, int(payload.get("next_offset", 0))) % universe_size, symbols
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0, set()


def rebuild_batch(
    *,
    source_db: Path,
    candidate_db: Path,
    state_path: Path,
    target_day: date,
    start_day: date,
    batch_size: int,
    query_timeout_seconds: float,
    max_runtime_seconds: float,
    min_coverage_ratio: float,
) -> RebuildSummary:
    if (
        candidate_db.exists()
        and SqliteDbSource(candidate_db, cache=None).price_mode() == "invalid"
    ):
        raise RuntimeError("candidate rebuild database has an invalid price basis")
    symbols = _seed_candidate_database(source_db, candidate_db)
    if not 0 < min_coverage_ratio <= 1:
        raise ValueError("min coverage ratio must be in (0, 1]")
    offset, covered_before = _read_state(state_path, target_day, len(symbols))
    batch = symbols[offset : offset + batch_size]
    if not batch:
        batch = symbols[:batch_size]
        offset = 0
    update = update_sqlite_daily(
        candidate_db,
        target_day=target_day,
        sleep_seconds=0.05,
        limit=0,
        symbols=tuple(batch),
        start_day=start_day,
        force_from_start=True,
        price_mode="raw",
        query_timeout_seconds=query_timeout_seconds,
        max_runtime_seconds=max_runtime_seconds,
        require_target_coverage=False,
    )
    covered_now = SqliteDbSource(
        candidate_db, cache=None
    ).get_symbols_with_daily_coverage(
        batch, target_day, target_day, min_rows=1, min_coverage_ratio=1.0
    )
    covered = covered_before | set(covered_now)
    next_offset = offset + len(batch)
    complete = next_offset >= len(symbols)
    coverage_ratio = len(covered) / len(symbols)
    summary = RebuildSummary(
        target_day=target_day,
        processed_symbols=update.processed_symbols,
        covered_symbols=len(covered),
        universe_size=len(symbols),
        coverage_ratio=coverage_ratio,
        next_offset=0 if next_offset >= len(symbols) else next_offset,
        complete=complete,
        publish_ready=complete and coverage_ratio >= min_coverage_ratio,
        update=update,
    )
    payload = asdict(summary)
    payload["target_day"] = target_day.isoformat()
    payload["updated_at"] = now_shanghai().isoformat(timespec="seconds")
    payload["covered_ts_codes"] = sorted(covered)
    atomic_write_text(
        state_path, json.dumps(payload, ensure_ascii=False, default=str) + "\n"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", required=True, type=Path)
    parser.add_argument("--candidate-db", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--query-timeout-seconds", type=float, default=4.0)
    parser.add_argument("--max-runtime-seconds", type=float, default=420.0)
    parser.add_argument("--min-coverage-ratio", type=float, default=0.98)
    args = parser.parse_args()
    if (
        args.batch_size <= 0
        or args.query_timeout_seconds <= 0
        or args.max_runtime_seconds <= 0
        or not 0 < args.min_coverage_ratio <= 1
    ):
        raise SystemExit("batch size and timeouts must be positive")
    summary = rebuild_batch(
        source_db=args.source_db,
        candidate_db=args.candidate_db,
        state_path=args.state,
        target_day=date.fromisoformat(args.target_date),
        start_day=date.fromisoformat(args.start_date),
        batch_size=args.batch_size,
        query_timeout_seconds=args.query_timeout_seconds,
        max_runtime_seconds=args.max_runtime_seconds,
        min_coverage_ratio=args.min_coverage_ratio,
    )
    print(json.dumps(asdict(summary), ensure_ascii=False, default=str, sort_keys=True))
    return 0 if not summary.complete or summary.publish_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
