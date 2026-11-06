"""Backfill point-in-time adjustment factors in explicit, resumable batches."""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from aqsp.core.errors import DataError
from aqsp.data import AdjustmentService
from aqsp.data.cache import DataCache
from aqsp.data.source_factory import build_data_source
from aqsp.data.sqlite_db_source import SqliteDbSource

DEFAULT_BATCH_LIMIT = 100
# Conservative and overridable; providers may throttle large per-symbol backfills.
DEFAULT_REQUEST_DELAY_SECONDS = 0.5


@dataclass(frozen=True)
class BackfillSummary:
    selected: int
    succeeded: int
    failed_symbols: tuple[str, ...]


def backfill_adjustment_factors(
    symbols: list[str],
    *,
    start: date,
    end: date,
    service: AdjustmentService,
    offset: int = 0,
    limit: int = DEFAULT_BATCH_LIMIT,
    delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
) -> BackfillSummary:
    """Fetch one bounded symbol batch; offset resumes the next batch after interruption."""
    if end < start:
        raise ValueError("end must be on or after start")
    if offset < 0 or limit < 1:
        raise ValueError("offset must be non-negative and limit must be positive")
    if delay_seconds < 0:
        raise ValueError("delay_seconds must be non-negative")

    selected_symbols = list(dict.fromkeys(symbols))[offset : offset + limit]
    failed_symbols: list[str] = []
    for index, symbol in enumerate(selected_symbols):
        try:
            service.fetch_and_cache_factors(symbol, start, end)
        except (DataError, OSError, ValueError):
            failed_symbols.append(symbol)
        if delay_seconds and index + 1 < len(selected_symbols):
            time.sleep(delay_seconds)
    return BackfillSummary(
        selected=len(selected_symbols),
        succeeded=len(selected_symbols) - len(failed_symbols),
        failed_symbols=tuple(failed_symbols),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="raw SQLite 数据库，仅 --all 需要")
    symbol_group = parser.add_mutually_exclusive_group(required=True)
    symbol_group.add_argument("--all", action="store_true", help="从 raw SQLite 股票目录读取全市场代码")
    symbol_group.add_argument("--symbols", help="逗号分隔的六位代码；失败项可单独重试")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--offset", type=int, default=0, help="跳过前 N 个代码，用于分批续跑")
    parser.add_argument("--limit", type=int, default=DEFAULT_BATCH_LIMIT, help="本次最多处理代码数")
    parser.add_argument("--delay-seconds", type=float, default=DEFAULT_REQUEST_DELAY_SECONDS)
    parser.add_argument("--cache-db", type=Path, help="PIT 因子缓存数据库；默认沿用 AQSP_CACHE_DB")
    args = parser.parse_args()

    if args.all:
        if args.db is None:
            parser.error("--all 必须同时提供 --db")
        symbols = SqliteDbSource(db_path=args.db, cache=None).get_available_symbols()
    else:
        symbols = [item.strip() for item in str(args.symbols).split(",") if item.strip()]
    if not symbols:
        parser.error("没有可处理的代码")

    factor_source = build_data_source("akshare", cache=None)
    cache = DataCache(db_path=args.cache_db) if args.cache_db else DataCache()
    service = AdjustmentService(cache=cache, factor_source=factor_source)
    summary = backfill_adjustment_factors(
        symbols,
        start=args.start,
        end=args.end,
        service=service,
        offset=args.offset,
        limit=args.limit,
        delay_seconds=args.delay_seconds,
    )
    print(
        f"selected={summary.selected} succeeded={summary.succeeded} "
        f"failed={len(summary.failed_symbols)} failed_symbols={','.join(summary.failed_symbols)}"
    )
    return 1 if summary.failed_symbols else 0


if __name__ == "__main__":
    raise SystemExit(main())
