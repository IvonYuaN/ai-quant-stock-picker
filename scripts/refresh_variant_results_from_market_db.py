#!/usr/bin/env python3
"""Refresh variant_results.json from the private market SQLite database.

This bridges the production ``daily_qfq`` market DB into the raw OHLCV schema
used by ``run_variant_suite.py``.  It is deliberately a runtime script: no
private market rows are written back to GitHub. Bounded refreshes rotate a
balanced main-board/ChiNext batch and advance only after an artifact is written.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import signal
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

import pandas as pd

from aqsp.utils.jsonl_io import atomic_write_text
from check_variant_results import validate_variant_payload
from run_variant_suite import run_suite

CODE_PREFIXES = ("000", "001", "002", "003", "300", "600", "601", "603", "605")
EXCLUDED_NAME_MARKERS = ("ST", "*ST", "退", "PT")
DEFAULT_LOOKBACK_CALENDAR_DAYS = 180
DEFAULT_MAX_SYMBOLS = 300
DEFAULT_MAX_FILLS_PER_VARIANT = 24
DEFAULT_MAX_RUNTIME_SECONDS = 600
DEFAULT_LOCK_WAIT_SECONDS = 0.0
LATEST_DATE_PROBE_SYMBOLS = 240
SQL_CHUNK_SIZE = 400


class VariantRefreshTimeout(TimeoutError):
    """Raised when the refresh exceeds its configured runtime budget."""


class VariantRefreshLocked(RuntimeError):
    """Raised when another bounded variant refresh owns the runtime lock."""


@dataclass(frozen=True)
class MarketSymbol:
    ts_code: str
    symbol: str
    name: str
    group: str


@dataclass(frozen=True)
class VariantUniverseBatch:
    symbols: tuple[MarketSymbol, ...]
    universe_version: str
    universe_count: int
    offset: int
    cycle_id: int

    @property
    def coverage_pct(self) -> float:
        if self.universe_count <= 0:
            return 0.0
        return round(
            min(1.0, (self.offset + len(self.symbols)) / self.universe_count), 6
        )


def normalize_trade_date(value: str) -> str:
    raw = str(value).strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw[:10]


def compact_trade_date(value: str) -> str:
    return normalize_trade_date(value).replace("-", "")


def symbol_group(symbol: str) -> str:
    if symbol.startswith("300"):
        return "创业板"
    if symbol.startswith(("600", "601", "603", "605")):
        return "沪市主板"
    return "深市主板"


def is_supported_symbol(ts_code: str, name: str) -> bool:
    symbol = ts_code.split(".", 1)[0]
    if not ts_code.endswith((".SZ", ".SH")):
        return False
    if not symbol.startswith(CODE_PREFIXES):
        return False
    upper_name = str(name).upper()
    return not any(marker in upper_name for marker in EXCLUDED_NAME_MARKERS)


def load_supported_symbols(db_path: Path) -> tuple[MarketSymbol, ...]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT ts_code, name FROM stocks ORDER BY ts_code"
        ).fetchall()
    symbols = [
        MarketSymbol(
            str(ts_code),
            str(ts_code).split(".", 1)[0],
            str(name),
            symbol_group(str(ts_code).split(".", 1)[0]),
        )
        for ts_code, name in rows
        if is_supported_symbol(str(ts_code), str(name))
    ]
    return tuple(symbols)


def balanced_symbols(
    symbols: tuple[MarketSymbol, ...], max_symbols: int
) -> tuple[MarketSymbol, ...]:
    ordered = _interleaved_symbols(symbols)
    if max_symbols <= 0 or len(ordered) <= max_symbols:
        return ordered
    return ordered[:max_symbols]


def _interleaved_symbols(
    symbols: tuple[MarketSymbol, ...],
) -> tuple[MarketSymbol, ...]:
    buckets: dict[str, list[MarketSymbol]] = {
        "深市主板": [],
        "创业板": [],
        "沪市主板": [],
    }
    for item in symbols:
        buckets.setdefault(item.group, []).append(item)
    picked: list[MarketSymbol] = []
    while any(buckets.values()):
        for key in ("深市主板", "创业板", "沪市主板"):
            bucket = buckets.get(key) or []
            if bucket:
                picked.append(bucket.pop(0))
    return tuple(picked)


def select_variant_batch(
    symbols: tuple[MarketSymbol, ...],
    max_symbols: int,
    cursor_path: Path,
) -> VariantUniverseBatch:
    ordered = _interleaved_symbols(symbols)
    if not ordered:
        raise ValueError("变体刷新股票池为空")
    universe_version = _market_symbols_version(ordered)
    state = _read_variant_cursor(cursor_path)
    if state.get("universe_version") != universe_version or int(
        state.get("universe_count") or 0
    ) != len(ordered):
        offset = 0
        cycle_id = 1
    else:
        offset = int(state.get("next_offset") or 0) % len(ordered)
        cycle_id = int(state.get("cycle_id") or 1)
    batch_size = (
        len(ordered) if max_symbols <= 0 else min(max_symbols, len(ordered) - offset)
    )
    return VariantUniverseBatch(
        symbols=ordered[offset : offset + batch_size],
        universe_version=universe_version,
        universe_count=len(ordered),
        offset=offset,
        cycle_id=cycle_id,
    )


def commit_variant_batch(cursor_path: Path, batch: VariantUniverseBatch) -> None:
    next_offset = batch.offset + len(batch.symbols)
    cycle_id = batch.cycle_id
    if next_offset >= batch.universe_count:
        next_offset = 0
        cycle_id += 1
    payload = {
        "universe_version": batch.universe_version,
        "universe_count": batch.universe_count,
        "batch_size": len(batch.symbols),
        "next_offset": next_offset,
        "cycle_id": cycle_id,
        "last_offset": batch.offset,
        "coverage_pct": batch.coverage_pct,
    }
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        cursor_path, json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    )


def _read_variant_cursor(cursor_path: Path) -> dict[str, object]:
    try:
        payload = json.loads(cursor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _market_symbols_version(symbols: tuple[MarketSymbol, ...]) -> str:
    digest = hashlib.sha256(
        "\n".join(item.ts_code for item in symbols).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def latest_trade_date(db_path: Path, symbols: tuple[MarketSymbol, ...]) -> str:
    # daily_qfq is indexed by (ts_code, trade_date), not trade_date alone.
    # Probe several live-looking symbols through the composite index instead of
    # scanning the full market table.
    dates: list[str] = []
    with sqlite3.connect(db_path) as conn:
        for item in symbols[:LATEST_DATE_PROBE_SYMBOLS]:
            raw = conn.execute(
                """
                SELECT trade_date
                FROM daily_qfq
                WHERE ts_code = ?
                ORDER BY trade_date DESC
                LIMIT 1
                """,
                (item.ts_code,),
            ).fetchone()
            if raw and raw[0]:
                dates.append(normalize_trade_date(str(raw[0])))
    if not dates:
        raise ValueError("daily_qfq 没有交易日期")
    return max(dates)


def copy_market_rows(
    *,
    source_db: Path,
    target_db: Path,
    symbols: tuple[MarketSymbol, ...],
    start: str,
    end: str,
) -> tuple[str, ...]:
    target_db.parent.mkdir(parents=True, exist_ok=True)
    by_ts_code = {item.ts_code: item for item in symbols}
    chunks = list(_chunks(tuple(by_ts_code), SQL_CHUNK_SIZE))
    frames: list[pd.DataFrame] = []
    start_raw = compact_trade_date(start)
    end_raw = compact_trade_date(end)
    with sqlite3.connect(source_db) as conn:
        for chunk in chunks:
            placeholders = ",".join("?" for _ in chunk)
            query = f"""
                SELECT ts_code, trade_date, open, high, low, close, volume, amount
                FROM daily_qfq
                WHERE ts_code IN ({placeholders})
                  AND trade_date BETWEEN ? AND ?
                ORDER BY ts_code, trade_date
            """
            frames.append(
                pd.read_sql_query(query, conn, params=(*chunk, start_raw, end_raw))
            )
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if frame.empty:
        raise ValueError("market DB 在目标窗口没有可用日线")
    frame["symbol"] = frame["ts_code"].map(lambda value: by_ts_code[str(value)].symbol)
    frame["name"] = frame["ts_code"].map(lambda value: by_ts_code[str(value)].name)
    frame["date"] = frame["trade_date"].map(normalize_trade_date)
    numeric_columns = ("open", "high", "low", "close", "volume", "amount")
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    frame = frame.sort_values(["symbol", "date"]).reset_index(drop=True)
    prev_close = frame.groupby("symbol", sort=False)["close"].shift(1)
    frame["limit_up"] = (prev_close * 1.10).round(4).fillna(0.0)
    frame["limit_down"] = (prev_close * 0.90).round(4).fillna(0.0)
    frame["suspended"] = 0
    frame["price_mode"] = "raw"
    frame["workload"] = "historical"
    selected_symbols = tuple(sorted(frame.loc[frame["date"] == end, "symbol"].unique()))
    if not selected_symbols:
        raise ValueError(f"目标日 {end} 没有任何过滤后标的")
    columns = [
        "symbol",
        "date",
        "name",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "suspended",
        "limit_up",
        "limit_down",
        "price_mode",
        "workload",
    ]
    with sqlite3.connect(target_db) as conn:
        conn.execute("DROP TABLE IF EXISTS ohlcv")
        frame[columns].to_sql("ohlcv", conn, index=False, if_exists="replace")
        conn.execute("CREATE INDEX idx_ohlcv_symbol_date ON ohlcv(symbol, date)")
        conn.execute("CREATE INDEX idx_ohlcv_date ON ohlcv(date)")
    return selected_symbols


def _chunks(values: tuple[str, ...], size: int) -> Iterable[tuple[str, ...]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def compact_variant_fills(payload: dict[str, object], max_fills: int) -> None:
    if max_fills < 0:
        return
    variants = payload.get("variants")
    if not isinstance(variants, list):
        return
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        fills = variant.get("fills")
        if isinstance(fills, list) and len(fills) > max_fills:
            variant["fills"] = fills[-max_fills:]
            variant["fills_compacted"] = True
            variant["fills_retained"] = max_fills


@contextlib.contextmanager
def refresh_lock(lock_path: Path, wait_seconds: float) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, wait_seconds)
    with lock_path.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise VariantRefreshLocked(
                        f"variant refresh already running: {lock_path}"
                    ) from exc
                time.sleep(0.25)
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} started_at={time.time():.0f}\n")
        handle.flush()
        try:
            yield
        finally:
            handle.seek(0)
            handle.truncate()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def runtime_budget(seconds: int) -> Iterator[None]:
    if seconds <= 0 or not hasattr(signal, "setitimer"):
        yield
        return

    def _raise_timeout(_signum: int, _frame: Any) -> None:
        raise VariantRefreshTimeout(
            f"variant refresh timed out after {seconds} seconds"
        )

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-db", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--temp-db", type=Path)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument(
        "--lookback-calendar-days", type=int, default=DEFAULT_LOOKBACK_CALENDAR_DAYS
    )
    parser.add_argument("--max-symbols", type=int, default=DEFAULT_MAX_SYMBOLS)
    parser.add_argument(
        "--max-fills-per-variant", type=int, default=DEFAULT_MAX_FILLS_PER_VARIANT
    )
    parser.add_argument(
        "--max-runtime-seconds", type=int, default=DEFAULT_MAX_RUNTIME_SECONDS
    )
    parser.add_argument("--lock-file", type=Path)
    parser.add_argument("--cursor-file", type=Path)
    parser.add_argument(
        "--lock-wait-seconds", type=float, default=DEFAULT_LOCK_WAIT_SECONDS
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    lock_path = args.lock_file or args.output.with_name(
        f".{args.output.name}.refresh.lock"
    )
    cursor_path = args.cursor_file or args.output.with_name(
        f".{args.output.name}.universe_cursor.json"
    )
    try:
        with (
            refresh_lock(lock_path, args.lock_wait_seconds),
            runtime_budget(args.max_runtime_seconds),
        ):
            supported = load_supported_symbols(args.market_db)
            end = args.end or latest_trade_date(args.market_db, supported)
            start = (
                args.start
                or (
                    date.fromisoformat(end)
                    - timedelta(days=args.lookback_calendar_days)
                ).isoformat()
            )
            batch = select_variant_batch(supported, args.max_symbols, cursor_path)
            selected = batch.symbols
            if args.temp_db:
                temp_db = args.temp_db
                selected_symbols = copy_market_rows(
                    source_db=args.market_db,
                    target_db=temp_db,
                    symbols=selected,
                    start=start,
                    end=end,
                )
                payload = run_suite(temp_db, selected_symbols, start, end)
            else:
                with tempfile.TemporaryDirectory(prefix="aqsp-variant-db-") as tmp:
                    temp_db = Path(tmp) / "variant_input.db"
                    selected_symbols = copy_market_rows(
                        source_db=args.market_db,
                        target_db=temp_db,
                        symbols=selected,
                        start=start,
                        end=end,
                    )
                    payload = run_suite(temp_db, selected_symbols, start, end)
            compact_variant_fills(payload, args.max_fills_per_variant)
            payload["universe"] = {
                "market_db": str(args.market_db),
                "supported_symbols": len(supported),
                "selected_symbols": len(selected_symbols),
                "filters": "沪市主板+深市主板+创业板；排除 ST/*ST/PT/退市/科创/北交/B股",
                "batch_active": args.max_symbols > 0 and len(supported) > len(selected),
                "batch_id": f"{batch.cycle_id}:{batch.offset}",
                "batch_size": len(selected),
                "cycle_id": batch.cycle_id,
                "coverage_pct": batch.coverage_pct,
            }
            validate_variant_payload(
                payload,
                path=str(args.output),
                expected_end=end,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                args.output, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            )
            commit_variant_batch(cursor_path, batch)
            print(
                "variant_results refreshed: "
                f"schema={payload['schema_version']} variants={len(payload['variants'])} "
                f"symbols={len(selected_symbols)} batch={batch.cycle_id}:{batch.offset} "
                f"end={payload['end_date']} output={args.output}"
            )
            return 0
    except VariantRefreshTimeout as exc:
        print(f"variant_results refresh timeout: {exc}", flush=True)
        return 124
    except VariantRefreshLocked as exc:
        print(f"variant_results refresh skipped_lock: {exc}", flush=True)
        return 0
    except ValueError as exc:
        print(f"variant_results refresh rejected: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
