#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from aqsp.cli import (
    _detect_runtime_regime,
    _drop_benchmark_frame,
    _enrich_pick_names,
    _resolve_execution_cost_bps,
    _resolve_run_symbols,
    _runtime_max_universe,
    _screen_universe_with_thresholds,
)
from aqsp.config import load_runtime_config
from aqsp.core.time import is_trading_day, today_shanghai
from aqsp.data.cache import DataCache
from aqsp.core.errors import DataError
from aqsp.data.source_factory import build_data_source
from aqsp.ledger.base import ExecutionConfig, append_predictions, read_ledger
from aqsp.ledger.runtime import (
    count_independent_signal_days,
    count_paper_tracking_days,
    ledger_signal_date,
)
from aqsp.models import PickResult, ScreeningConfig
from aqsp.paper import sync_paper_trades
from aqsp.strategy import strategy_weights_for_regime
from aqsp.strategies.thresholds import load_thresholds


DEFAULT_LOOKBACK_DAYS = 260
DEFAULT_FUTURE_BUFFER_DAYS = 10
DEFAULT_SCREEN_BATCH_SIZE = 400


def _history_window_start(signal_day: date, lookback_days: int) -> date:
    calendar_buffer_days = max(lookback_days + 180, 300)
    return signal_day - timedelta(days=calendar_buffer_days)


def _chunks(items: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        return [items]
    return [items[idx : idx + size] for idx in range(0, len(items), size)]


@dataclass(frozen=True)
class BackfillDayPlan:
    trading_days: list[date]
    existing_signal_days: set[str]
    existing_paper_days: set[str]
    missing_signal_days: set[str]
    missing_paper_days: set[str]


def build_backfill_plan(
    *,
    start_date: date,
    end_date: date,
    existing_signal_days: set[str],
    existing_paper_days: set[str],
    max_days: int,
) -> BackfillDayPlan:
    trading_days: list[date] = []
    missing_signal_days: set[str] = set()
    missing_paper_days: set[str] = set()
    cursor = start_date
    while cursor <= end_date:
        date_str = cursor.isoformat()
        if is_trading_day(cursor):
            signal_missing = date_str not in existing_signal_days
            paper_missing = date_str not in existing_paper_days
            if signal_missing:
                missing_signal_days.add(date_str)
            if paper_missing:
                missing_paper_days.add(date_str)
            if signal_missing or paper_missing:
                trading_days.append(cursor)
        cursor += timedelta(days=1)
    if max_days > 0:
        trading_days = trading_days[-max_days:]
    planned_days = {day.isoformat() for day in trading_days}
    return BackfillDayPlan(
        trading_days=trading_days,
        existing_signal_days=existing_signal_days,
        existing_paper_days=existing_paper_days,
        missing_signal_days=missing_signal_days & planned_days,
        missing_paper_days=missing_paper_days & planned_days,
    )


def collect_signal_days(path: str | Path) -> set[str]:
    days: set[str] = set()
    for row in read_ledger(path):
        signal_date = ledger_signal_date(row)
        if signal_date and not bool(row.get("is_simulated")):
            days.add(signal_date)
    return days


def truncate_frames_to_date(
    frames: dict[str, pd.DataFrame],
    *,
    end_date: date,
    lookback_days: int,
) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for symbol, frame in frames.items():
        if frame is None or frame.empty:
            continue
        normalized = frame.copy()
        normalized["date"] = pd.to_datetime(
            normalized["date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        filtered = normalized[normalized["date"] <= end_date.isoformat()].tail(
            lookback_days
        )
        if not filtered.empty:
            out[symbol] = filtered.reset_index(drop=True)
    return out


def collect_paper_sync_symbols(
    *,
    ledger_path: str | Path,
    paper_ledger_path: str | Path,
    new_picks: list[PickResult],
) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for row in read_ledger(ledger_path) + read_ledger(paper_ledger_path):
        symbol = str(row.get("symbol") or "").strip()
        if not symbol or symbol == "__RUN__" or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
    for pick in new_picks:
        if pick.symbol not in seen:
            seen.add(pick.symbol)
            symbols.append(pick.symbol)
    return symbols


def fetch_history_window(
    *,
    source: Any,
    symbols: list[str],
    signal_day: date,
    lookback_days: int,
    future_buffer_days: int,
) -> dict[str, pd.DataFrame]:
    start = _history_window_start(signal_day, lookback_days)
    end = signal_day + timedelta(days=max(future_buffer_days, 1) * 2)
    if hasattr(source, "get_symbols_with_daily_coverage"):
        try:
            symbols = list(
                source.get_symbols_with_daily_coverage(
                    symbols,
                    start,
                    end,
                    min_rows=1,
                )
            )
        except TypeError:
            symbols = list(source.get_symbols_with_daily_coverage(symbols, start, end))
    if not symbols:
        return {}
    try:
        return source.fetch_daily(symbols, start, end, adjust="")
    except DataError as exc:
        raise DataError(
            f"backfill fetch_history_window failed for {len(symbols)} symbols: {exc}"
        ) from exc


def resolve_backfill_symbols(
    *,
    source_name: str,
    source: Any,
    explicit_symbols: str,
    pool_name: str,
    signal_day: date,
    max_universe: int,
    min_avg_amount: float,
    lookback_days: int,
) -> list[str]:
    if source_name != "sqlite_db":
        return _resolve_run_symbols(
            source_name,
            explicit_symbols,
            pool_name=pool_name,
            as_of=signal_day,
            max_universe=max_universe,
            min_avg_amount=min_avg_amount,
        )

    if explicit_symbols.strip():
        return [item.strip() for item in explicit_symbols.split(",") if item.strip()]

    symbols = list(getattr(source, "get_available_symbols")())
    if hasattr(source, "get_symbols_with_daily_coverage"):
        start = _history_window_start(signal_day, lookback_days)
        symbols = source.get_symbols_with_daily_coverage(
            symbols,
            start,
            signal_day,
            min_rows=None,
        )
    if max_universe > 0:
        symbols = symbols[:max_universe]
    return symbols


def build_screening_config(
    *,
    thresholds: Any,
    mode: str,
    min_avg_amount: float,
    regime: str,
) -> ScreeningConfig:
    weights = strategy_weights_for_regime(thresholds, regime)
    return ScreeningConfig(
        mode=mode,
        min_avg_amount=min_avg_amount,
        min_price=thresholds.filter.min_price,
        max_price=thresholds.filter.max_price,
        max_bias20=thresholds.scoring.max_bias20,
        stop_loss_buffer=thresholds.risk.soft_stop_loss_pct,
        max_position_pct=thresholds.risk.max_position_pct,
        strategy_weights=weights,
        strategy_weight_reasons={},
    )


def screen_backfill_picks(
    *,
    source: Any,
    symbols: list[str],
    signal_day: date,
    lookback_days: int,
    future_buffer_days: int,
    benchmark_symbol: str,
    thresholds: Any,
    config: ScreeningConfig,
    limit: int,
    batch_size: int,
) -> tuple[list[PickResult], dict[str, pd.DataFrame]]:
    best_by_symbol: dict[str, PickResult] = {}
    screen_frames_by_symbol: dict[str, pd.DataFrame] = {}

    for symbol_batch in _chunks(symbols, batch_size):
        raw_frames = fetch_history_window(
            source=source,
            symbols=symbol_batch,
            signal_day=signal_day,
            lookback_days=lookback_days,
            future_buffer_days=future_buffer_days,
        )
        screen_frames = truncate_frames_to_date(
            raw_frames,
            end_date=signal_day,
            lookback_days=lookback_days,
        )
        if not screen_frames:
            continue
        picks = _enrich_pick_names(
            _screen_universe_with_thresholds(
                _drop_benchmark_frame(screen_frames, benchmark_symbol),
                config,
                thresholds,
            ),
            screen_frames,
        )
        for pick in picks:
            existing = best_by_symbol.get(pick.symbol)
            if existing is None or pick.score > existing.score:
                best_by_symbol[pick.symbol] = pick
                frame = screen_frames.get(pick.symbol)
                if frame is not None and not frame.empty:
                    screen_frames_by_symbol[pick.symbol] = frame

    ranked = sorted(best_by_symbol.values(), key=lambda item: item.score, reverse=True)
    return ranked[:limit], screen_frames_by_symbol


def backfill_real_sample_days(args: argparse.Namespace) -> int:
    thresholds = load_thresholds()
    runtime = load_runtime_config()
    project_root = Path(args.project_root).resolve()
    ledger_path = project_root / args.ledger
    paper_ledger_path = project_root / args.paper_ledger
    source = build_data_source(args.source, cache=DataCache(), overrides={})

    existing_signal_days = collect_signal_days(ledger_path)
    existing_paper_days = collect_signal_days(paper_ledger_path)
    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)
    plan = build_backfill_plan(
        start_date=start_date,
        end_date=end_date,
        existing_signal_days=existing_signal_days,
        existing_paper_days=existing_paper_days,
        max_days=args.max_days,
    )

    explicit_symbols = args.symbols or ",".join(runtime.symbols)
    min_avg_amount = float(args.min_avg_amount or runtime.min_avg_amount)
    max_universe = _runtime_max_universe(args.max_universe or runtime.max_universe)
    benchmark_symbol = args.benchmark_symbol
    execution_fee_bps, execution_slippage_bps = _resolve_execution_cost_bps(
        thresholds,
        fee_bps=None,
        slippage_bps=None,
    )

    print(
        f"backfill plan: trading_days={len(plan.trading_days)} "
        f"existing_signal_days={len(plan.existing_signal_days)} "
        f"existing_paper_days={len(plan.existing_paper_days)} "
        f"missing_signal_days={len(plan.missing_signal_days)} "
        f"missing_paper_days={len(plan.missing_paper_days)}",
        flush=True,
    )

    for signal_day in plan.trading_days:
        signal_day_str = signal_day.isoformat()
        current_signal_days = count_independent_signal_days(ledger_path)
        current_paper_days = count_paper_tracking_days(paper_ledger_path)
        if (
            current_signal_days >= args.target_signal_days
            and current_paper_days >= args.target_paper_days
        ):
            break
        need_signal_backfill = signal_day_str in plan.missing_signal_days
        need_paper_backfill = signal_day_str in plan.missing_paper_days

        symbols = resolve_backfill_symbols(
            source_name=args.source,
            source=source,
            explicit_symbols=explicit_symbols,
            pool_name=args.pool,
            max_universe=max_universe,
            min_avg_amount=min_avg_amount,
            signal_day=signal_day,
            lookback_days=args.lookback_days,
        )
        if not symbols:
            print(f"{signal_day.isoformat()}: skip, no symbols resolved", flush=True)
            continue
        print(
            f"{signal_day.isoformat()}: scanning {len(symbols)} symbols "
            f"(signal={'yes' if need_signal_backfill else 'no'}, "
            f"paper={'yes' if need_paper_backfill else 'no'})",
            flush=True,
        )

        raw_frames = fetch_history_window(
            source=source,
            symbols=symbols[: min(len(symbols), args.screen_batch_size)],
            signal_day=signal_day,
            lookback_days=args.lookback_days,
            future_buffer_days=args.future_buffer_days,
        )
        screen_frames = truncate_frames_to_date(
            raw_frames,
            end_date=signal_day,
            lookback_days=args.lookback_days,
        )
        regime = _detect_runtime_regime(
            screen_frames,
            benchmark_symbol=benchmark_symbol if benchmark_symbol else None,
            thresholds=thresholds,
        )
        config = build_screening_config(
            thresholds=thresholds,
            mode=args.mode,
            min_avg_amount=min_avg_amount,
            regime=regime,
        )
        picks: list[PickResult] = []
        if need_signal_backfill:
            picks, pick_frames = screen_backfill_picks(
                source=source,
                symbols=symbols,
                signal_day=signal_day,
                lookback_days=args.lookback_days,
                future_buffer_days=args.future_buffer_days,
                benchmark_symbol=benchmark_symbol,
                thresholds=thresholds,
                config=config,
                limit=args.limit,
                batch_size=args.screen_batch_size,
            )
            if not picks:
                print(f"{signal_day.isoformat()}: skip, no picks", flush=True)
                continue
            for pick in picks:
                frame = pick_frames.get(pick.symbol)
                if frame is not None and not frame.empty:
                    screen_frames[pick.symbol] = frame

            append_predictions(
                ledger_path,
                picks,
                execution=ExecutionConfig(
                    horizon_days=args.horizon_days,
                    fee_bps=execution_fee_bps,
                    slippage_bps=execution_slippage_bps,
                    benchmark_symbol=benchmark_symbol,
                    limit_up_pct=float(thresholds.execution.fallback_limit_main_pct),
                    limit_down_pct=float(thresholds.execution.fallback_limit_main_pct),
                ),
                thresholds_version=thresholds.version,
                regime=regime,
            )

        if need_paper_backfill:
            paper_symbols = collect_paper_sync_symbols(
                ledger_path=ledger_path,
                paper_ledger_path=paper_ledger_path,
                new_picks=picks,
            )
            paper_frames = fetch_history_window(
                source=source,
                symbols=paper_symbols,
                signal_day=signal_day,
                lookback_days=args.lookback_days,
                future_buffer_days=args.future_buffer_days,
            )
            summary = sync_paper_trades(
                signal_ledger=ledger_path,
                paper_ledger=paper_ledger_path,
                frames=paper_frames,
            )
        else:
            summary = None
        print(
            f"{signal_day.isoformat()}: picks={len(picks)} "
            f"paper(opened={getattr(summary, 'opened', 0)}, "
            f"closed={getattr(summary, 'closed', 0)}, "
            f"pending={getattr(summary, 'pending_entry', 0)}, "
            f"blocked={getattr(summary, 'not_executable', 0)}) "
            f"progress(signal={count_independent_signal_days(ledger_path)}, "
            f"paper={count_paper_tracking_days(paper_ledger_path)})",
            flush=True,
        )

    final_signal_days = count_independent_signal_days(ledger_path)
    final_paper_days = count_paper_tracking_days(paper_ledger_path)
    print(
        f"done: signal_days={final_signal_days}/{args.target_signal_days} "
        f"paper_days={final_paper_days}/{args.target_paper_days}",
        flush=True,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill real historical signal days into predictions/paper ledgers"
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--source", default="sqlite_db")
    parser.add_argument("--mode", choices=["open", "close"], default="close")
    parser.add_argument("--pool", default="")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-universe", type=int, default=0)
    parser.add_argument("--min-avg-amount", type=float, default=0.0)
    parser.add_argument("--benchmark-symbol", default="000300")
    parser.add_argument("--ledger", default="data/predictions.jsonl")
    parser.add_argument("--paper-ledger", default="data/paper_trades.jsonl")
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--max-days", type=int, default=40)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument(
        "--future-buffer-days", type=int, default=DEFAULT_FUTURE_BUFFER_DAYS
    )
    parser.add_argument("--horizon-days", type=int, default=3)
    parser.add_argument("--target-signal-days", type=int, default=30)
    parser.add_argument("--target-paper-days", type=int, default=30)
    parser.add_argument(
        "--screen-batch-size", type=int, default=DEFAULT_SCREEN_BATCH_SIZE
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.end:
        args.end = (today_shanghai() - timedelta(days=1)).isoformat()
    if not args.start:
        args.start = (
            date.fromisoformat(args.end) - timedelta(days=max(args.max_days * 3, 120))
        ).isoformat()
    return backfill_real_sample_days(args)


if __name__ == "__main__":
    raise SystemExit(main())
