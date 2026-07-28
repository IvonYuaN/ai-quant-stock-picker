#!/usr/bin/env python3
"""Report and reclaim SQLite space held by redundant OHLCV indexes.

The command is dry-run by default. Compaction is intentionally explicit because
VACUUM needs substantial temporary disk space and an exclusive database lock.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_FREE_SPACE_MULTIPLIER = 2
REDUNDANT_OHLCV_INDEXES = frozenset(
    {
        "idx_ohlcv_symbol_date",
        "idx_ohlcv_symbol_date_price_mode",
        "idx_ohlcv_symbol_date_price_mode_workload",
    }
)


@dataclass(frozen=True)
class CacheSpaceReport:
    path: str
    size_bytes: int
    page_size: int
    page_count: int
    freelist_count: int
    removable_indexes: tuple[str, ...]
    reclaimable_bytes: int


def inspect_cache(path: Path) -> CacheSpaceReport:
    """Inspect one SQLite cache without changing it."""
    if not path.is_file():
        raise ValueError(f"SQLite cache missing: {path}")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        indexes = {
            str(row[1])
            for row in conn.execute("PRAGMA index_list(ohlcv)").fetchall()
            if str(row[1]) in REDUNDANT_OHLCV_INDEXES
        }
    return CacheSpaceReport(
        path=str(path),
        size_bytes=path.stat().st_size,
        page_size=page_size,
        page_count=page_count,
        freelist_count=freelist_count,
        removable_indexes=tuple(sorted(indexes)),
        reclaimable_bytes=freelist_count * page_size,
    )


def reclaim_cache(
    path: Path, *, vacuum: bool, free_space_multiplier: int
) -> CacheSpaceReport:
    """Drop known redundant indexes and optionally compact the database."""
    report = inspect_cache(path)
    if vacuum:
        required = report.size_bytes * free_space_multiplier
        available = shutil.disk_usage(path.parent).free
        if available < required:
            raise ValueError(
                f"insufficient free space for VACUUM: {available} < {required}"
            )
    with sqlite3.connect(path) as conn:
        for index in report.removable_indexes:
            conn.execute(f'DROP INDEX IF EXISTS "{index}"')
        conn.commit()
        if vacuum:
            conn.execute("VACUUM")
    return inspect_cache(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="reclaim_sqlite_cache")
    parser.add_argument("path", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--vacuum", action="store_true")
    parser.add_argument(
        "--free-space-multiplier",
        type=int,
        default=DEFAULT_FREE_SPACE_MULTIPLIER,
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.vacuum and not args.apply:
        raise ValueError("--vacuum requires --apply")
    if args.free_space_multiplier < DEFAULT_FREE_SPACE_MULTIPLIER:
        raise ValueError(
            f"--free-space-multiplier must be >= {DEFAULT_FREE_SPACE_MULTIPLIER}"
        )
    report = (
        reclaim_cache(
            args.path,
            vacuum=args.vacuum,
            free_space_multiplier=args.free_space_multiplier,
        )
        if args.apply
        else inspect_cache(args.path)
    )
    payload = asdict(report)
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "sqlite cache "
            f"size={report.size_bytes} free_pages={report.freelist_count} "
            f"indexes={','.join(report.removable_indexes) or '-'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
