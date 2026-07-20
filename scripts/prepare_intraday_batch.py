#!/usr/bin/env python3
"""Resolve the live universe once, select one batch, and print shell-safe symbols."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from aqsp.core.time import today_shanghai  # noqa: E402
from aqsp.data.source_factory import build_data_source  # noqa: E402
from aqsp.universe import DEFAULT_SYMBOLS  # noqa: E402
from aqsp.universe.intraday_cursor import IntradayUniverseCursor  # noqa: E402
from aqsp.universe.runtime import resolve_run_symbols  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", default=os.getenv("AQSP_INTRADAY_SOURCE", "online_first")
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("AQSP_INTRADAY_BATCH_SIZE", "128")),
    )
    parser.add_argument(
        "--min-avg-amount",
        type=float,
        default=float(os.getenv("AQSP_INTRADAY_MIN_AVG_AMOUNT", "0")),
    )
    parser.add_argument(
        "--cursor",
        default=os.getenv(
            "AQSP_INTRADAY_CURSOR_PATH", "data/runtime/intraday_universe_cursor.json"
        ),
    )
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--fail", default="")
    args = parser.parse_args()
    cursor = IntradayUniverseCursor(args.cursor)
    if args.commit:
        cursor.commit_current(
            scanned_count=int(os.getenv("AQSP_INTRADAY_BATCH_SCANNED", "0"))
        )
        return 0
    if args.fail:
        cursor.fail_current(args.fail)
        return 0

    def get_source(name: str):
        return build_data_source(name)

    symbols = resolve_run_symbols(
        args.source,
        "",
        get_source_fn=get_source,
        default_symbols=DEFAULT_SYMBOLS,
        max_universe=0,
        min_avg_amount=args.min_avg_amount,
    )
    batch = cursor.select(
        symbols, trade_date=today_shanghai(), batch_size=args.batch_size
    )
    print(",".join(batch.symbols))
    print(
        f"batch_id={batch.batch_id} universe_count={batch.universe_count} "
        f"offset={batch.offset} coverage_pct={batch.coverage_pct}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
