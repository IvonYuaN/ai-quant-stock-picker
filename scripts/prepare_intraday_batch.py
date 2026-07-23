#!/usr/bin/env python3
"""Resolve the live universe once, select one batch, and print shell-safe symbols."""

from __future__ import annotations

import argparse
import csv
import json
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


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        return [], []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _merge_csv_snapshot(
    current_path: Path,
    previous_path: Path,
    output_path: Path,
    *,
    snapshot_state: Path,
    trade_date: str,
    cycle_id: int,
    universe_version: str,
    coverage_pct: float,
) -> int:
    current_fields, current_rows = _read_rows(current_path)
    if not current_fields or not current_rows:
        raise ValueError("盘中批次 CSV 为空，无法合并快照")
    previous_rows: list[dict[str, str]] = []
    try:
        state = json.loads(snapshot_state.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    same_cycle = (
        isinstance(state, dict)
        and state.get("trade_date") == trade_date
        and int(state.get("cycle_id") or 0) == cycle_id
        and state.get("universe_version") == universe_version
    )
    if same_cycle:
        _, previous_rows = _read_rows(previous_path)
    current_run = next(
        (
            row
            for row in current_rows
            if str(row.get("symbol") or "").strip() == "__RUN__"
        ),
        None,
    )
    if current_run is None:
        raise ValueError("盘中批次 CSV 缺少运行元数据行")
    by_symbol = {
        str(row.get("symbol") or "").strip(): row
        for row in previous_rows
        if str(row.get("symbol") or "").strip() not in {"", "__RUN__"}
    }
    for row in current_rows:
        symbol = str(row.get("symbol") or "").strip()
        if symbol and symbol != "__RUN__":
            by_symbol[symbol] = row
    snapshot_fields = {
        "intraday_snapshot_scope": "cycle",
        "intraday_snapshot_complete": "true" if coverage_pct >= 1.0 else "false",
        "intraday_snapshot_candidate_count": str(len(by_symbol)),
        "intraday_snapshot_coverage_pct": str(coverage_pct),
        "intraday_snapshot_cycle_id": str(cycle_id),
        "intraday_snapshot_universe_version": universe_version,
    }
    current_run.update(snapshot_fields)
    fields = list(dict.fromkeys(current_fields + list(snapshot_fields)))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerow(current_run)
        writer.writerows(by_symbol.values())
    return len(by_symbol)


def _merge_jsonl_snapshot(
    current_path: Path,
    previous_path: Path,
    output_path: Path,
    *,
    snapshot_state: Path,
    trade_date: str,
    cycle_id: int,
    universe_version: str,
    coverage_pct: float,
) -> None:
    try:
        state = json.loads(snapshot_state.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    same_cycle = (
        isinstance(state, dict)
        and state.get("trade_date") == trade_date
        and int(state.get("cycle_id") or 0) == cycle_id
        and state.get("universe_version") == universe_version
    )
    rows: dict[tuple[str, str, str], dict[str, object]] = {}
    paths = [previous_path, current_path] if same_cycle else [current_path]
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                key = (
                    str(row.get("signal_date") or row.get("date") or ""),
                    str(row.get("symbol") or ""),
                    str(row.get("strategy_id") or row.get("strategy") or ""),
                )
                rows[key] = row
    for row in rows.values():
        row.update(
            {
                "intraday_snapshot_scope": "cycle",
                "intraday_snapshot_complete": coverage_pct >= 1.0,
                "intraday_snapshot_coverage_pct": coverage_pct,
                "intraday_snapshot_cycle_id": cycle_id,
                "intraday_snapshot_universe_version": universe_version,
            }
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows.values():
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def merge_snapshot_artifacts(args: argparse.Namespace) -> int:
    coverage = min(max(float(args.coverage_pct), 0.0), 1.0)
    _merge_csv_snapshot(
        Path(args.current_csv),
        Path(args.previous_csv),
        Path(args.current_csv),
        snapshot_state=Path(args.snapshot_state),
        trade_date=args.trade_date,
        cycle_id=args.cycle_id,
        universe_version=args.universe_version,
        coverage_pct=coverage,
    )
    _merge_jsonl_snapshot(
        Path(args.current_ledger),
        Path(args.previous_ledger),
        Path(args.current_ledger),
        snapshot_state=Path(args.snapshot_state),
        trade_date=args.trade_date,
        cycle_id=args.cycle_id,
        universe_version=args.universe_version,
        coverage_pct=coverage,
    )
    return 0


def finalize_snapshot(args: argparse.Namespace) -> int:
    Path(args.snapshot_state).parent.mkdir(parents=True, exist_ok=True)
    Path(args.snapshot_state).write_text(
        json.dumps(
            {
                "trade_date": args.trade_date,
                "cycle_id": args.cycle_id,
                "universe_version": args.universe_version,
                "coverage_pct": min(max(float(args.coverage_pct), 0.0), 1.0),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", default=os.getenv("AQSP_INTRADAY_SOURCE", "online_first")
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("AQSP_INTRADAY_BATCH_SIZE", "256")),
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
    parser.add_argument("--merge-snapshot", action="store_true")
    parser.add_argument("--finalize-snapshot", action="store_true")
    parser.add_argument("--current-csv", default="")
    parser.add_argument("--previous-csv", default="")
    parser.add_argument("--current-ledger", default="")
    parser.add_argument("--previous-ledger", default="")
    parser.add_argument(
        "--snapshot-state", default="data/runtime/intraday_snapshot_state.json"
    )
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--cycle-id", type=int, default=0)
    parser.add_argument("--universe-version", default="")
    parser.add_argument("--coverage-pct", type=float, default=0.0)
    args = parser.parse_args()
    if args.merge_snapshot:
        return merge_snapshot_artifacts(args)
    if args.finalize_snapshot:
        return finalize_snapshot(args)
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
