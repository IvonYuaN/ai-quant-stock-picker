"""PIT-safe multi-day exchange-deviation observations from local raw bars."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from typing import Any

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.core.time import latest_completed_trading_day, now_shanghai
from aqsp.data import source_factory
from aqsp.data.cache import AdjustmentFactorRecord


WINDOWS = (3, 10, 30)
LOOKBACK_SESSIONS = 31
WARMUP_CALENDAR_DAYS = 120
DEFAULT_MIN_CLOSENESS = 0.5
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
RULE_BASIS = "TickFlow shy3130/tick-stock-panel@2d629197/backend/app/services/abnormal_moves.py"

# TickFlow shy3130/tick-stock-panel@2d629197: mirrors its documented exchange-rule
# thresholds. These are risk-observation thresholds, not strategy or scoring inputs.
_THRESHOLDS: dict[str, dict[int, tuple[float, float]]] = {
    "SH": {3: (0.20, 0.20), 10: (1.00, 0.50), 30: (2.00, 0.70)},
    "STAR": {3: (0.30, 0.30), 10: (1.00, 0.50), 30: (2.00, 0.70)},
    "SZ": {3: (0.30, 0.30), 10: (1.00, 0.50), 30: (2.00, 0.70)},
    "GEM": {3: (0.30, 0.30), 10: (1.00, 0.50), 30: (2.00, 0.70)},
    "BJ": {3: (0.40, 0.40), 10: (1.00, 0.50), 30: (2.00, 0.70)},
}
_BENCHMARK_PREFERENCE: dict[str, tuple[str, ...]] = {
    "SH": ("000002", "000001"),
    "STAR": ("000688", "000002"),
    "SZ": ("399107", "399001"),
    "GEM": ("399102", "399107"),
    "BJ": ("899050", "000001"),
}
_STATUS_TRIGGERED = "triggered"
_STATUS_EDGE = "edge"
_STATUS_WATCH = "watch"


def board_key_for(symbol: str) -> str:
    """Map an A-share code to the index benchmark group used by TickFlow."""
    code = str(symbol).split(".", 1)[0].strip()
    if code.startswith(("43", "83", "87", "92")):
        return "BJ"
    if code.startswith("68"):
        return "STAR"
    if code.startswith("30"):
        return "GEM"
    if code.startswith("6"):
        return "SH"
    return "SZ"


def _index_close_map(frame: pd.DataFrame) -> dict[date, float]:
    if frame is None or frame.empty or not {"date", "close"} <= set(frame.columns):
        return {}
    dates = pd.to_datetime(frame["date"], errors="coerce")
    closes = pd.to_numeric(frame["close"], errors="coerce")
    result: dict[date, float] = {}
    for raw_date, raw_close in zip(dates, closes, strict=False):
        if pd.isna(raw_date) or pd.isna(raw_close):
            continue
        close = float(raw_close)
        if math.isfinite(close) and close > 0:
            result[raw_date.date()] = close
    return result


def _stock_close_map(frame: pd.DataFrame) -> dict[date, float]:
    if frame is None or frame.empty or not {"date", "close"} <= set(frame.columns):
        return {}
    dates = pd.to_datetime(frame["date"], errors="coerce")
    closes = pd.to_numeric(frame["close"], errors="coerce")
    valid = dates.notna() & closes.notna()
    if "suspended" in frame.columns:
        valid &= ~frame["suspended"].fillna(True).astype(bool)
    elif "volume" in frame.columns:
        volume = pd.to_numeric(frame["volume"], errors="coerce")
        valid &= volume.notna() & (volume > 0)
    result: dict[date, float] = {}
    for raw_date, raw_close, keep in zip(dates, closes, valid, strict=False):
        if not keep:
            continue
        close = float(raw_close)
        if math.isfinite(close) and close > 0:
            result[raw_date.date()] = close
    return result


def _resolve_benchmarks(
    index_frames: dict[str, pd.DataFrame],
) -> dict[str, dict[str, Any]]:
    available = [code for code in dict.fromkeys(
        code for candidates in _BENCHMARK_PREFERENCE.values() for code in candidates
    ) if _index_close_map(index_frames.get(code, pd.DataFrame()))]
    selections: dict[str, dict[str, Any]] = {}
    for board, candidates in _BENCHMARK_PREFERENCE.items():
        symbol = next((code for code in candidates if code in available), None)
        is_fallback = symbol is not None and symbol != candidates[0]
        if symbol is None and available:
            symbol = available[0]
            is_fallback = True
        if symbol is not None:
            selections[board] = {
                "symbol": symbol,
                "fallback": is_fallback,
            }
    return selections


def _status(closeness: float) -> str:
    if closeness >= 1.0:
        return _STATUS_TRIGGERED
    if closeness >= 0.7:
        return _STATUS_EDGE
    return _STATUS_WATCH


def _empty_snapshot(
    *,
    generated_at: datetime,
    as_of: date | None,
    expected_as_of: date,
    requested_symbols: int,
    min_closeness: float,
    reason: str,
    sessions: list[date] | None = None,
) -> dict[str, Any]:
    as_of_value = as_of or (sessions[-1] if sessions else None)
    return {
        "availability": "blocked",
        "source": "sqlite_db",
        "price_mode": "raw",
        "adjustment_method": "pit_factor_ratio",
        "rule_basis": RULE_BASIS,
        "generated_at": generated_at.isoformat(),
        "as_of": as_of_value.isoformat() if as_of_value else None,
        "expected_as_of": expected_as_of.isoformat(),
        "freshness": "stale" if as_of_value != expected_as_of else "fresh",
        "lag_calendar_days": max((expected_as_of - as_of_value).days, 0) if as_of_value else None,
        "requested_symbols": requested_symbols,
        "current_bar_symbols": 0,
        "lookback_sessions": len(sessions or []),
        "benchmark_selection": {},
        "pit_factor_sources": [],
        "window_coverage": {
            f"{window}d": {
                "raw_pairs": 0,
                "pit_pairs": 0,
                "index_pairs": 0,
                "computed": 0,
                "missing_index": 0,
            }
            for window in WINDOWS
        },
        "counts": {"triggered": 0, "edge": 0, "watch": 0},
        "min_closeness": min_closeness,
        "candidate_count": 0,
        "truncated": False,
        "blocking_reasons": [reason],
        "rows": [],
    }


def build_abnormal_deviation_snapshot(
    stock_frames: dict[str, pd.DataFrame],
    index_frames: dict[str, pd.DataFrame],
    *,
    session_dates: list[date],
    pit_adjustment_factors: dict[str, dict[date, AdjustmentFactorRecord]],
    generated_at: datetime,
    expected_as_of: date,
    requested_symbols: int,
    min_closeness: float = DEFAULT_MIN_CLOSENESS,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Build historical exchange-deviation observations from raw dated inputs."""
    if not math.isfinite(min_closeness) or not 0 <= min_closeness <= 1:
        raise ValueError("min_closeness must be finite and between 0 and 1")
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    sessions = sorted(set(session_dates))
    if len(sessions) < LOOKBACK_SESSIONS:
        return _empty_snapshot(
            generated_at=generated_at,
            as_of=sessions[-1] if sessions else None,
            expected_as_of=expected_as_of,
            requested_symbols=requested_symbols,
            min_closeness=min_closeness,
            reason="insufficient_market_sessions",
            sessions=sessions,
        )

    benchmark_selection = _resolve_benchmarks(index_frames)
    if not benchmark_selection:
        return _empty_snapshot(
            generated_at=generated_at,
            as_of=sessions[-1],
            expected_as_of=expected_as_of,
            requested_symbols=requested_symbols,
            min_closeness=min_closeness,
            reason="no_index_benchmarks",
            sessions=sessions,
        )

    as_of = sessions[-1]
    index_maps = {code: _index_close_map(frame) for code, frame in index_frames.items()}
    stock_maps = {symbol: _stock_close_map(frame) for symbol, frame in stock_frames.items()}
    coverage = {
        f"{window}d": {
            "raw_pairs": 0,
            "pit_pairs": 0,
            "index_pairs": 0,
            "computed": 0,
            "missing_index": 0,
        }
        for window in WINDOWS
    }
    rows: list[dict[str, Any]] = []
    current_bar_symbols = 0
    pit_sources: set[str] = set()
    valid_window_count = 0

    for symbol, closes in stock_maps.items():
        current_close = closes.get(as_of)
        if current_close is None:
            continue
        current_bar_symbols += 1
        board = board_key_for(symbol)
        benchmark = benchmark_selection.get(board)
        benchmark_symbol = str(benchmark["symbol"]) if benchmark else ""
        benchmark_closes = index_maps.get(benchmark_symbol, {})
        symbol_factors = pit_adjustment_factors.get(symbol, {})
        windows: dict[str, dict[str, Any]] = {}

        for window in WINDOWS:
            start_date = sessions[-(window + 1)]
            base_close = closes.get(start_date)
            if base_close is None:
                continue
            coverage[f"{window}d"]["raw_pairs"] += 1
            base_index = benchmark_closes.get(start_date)
            current_index = benchmark_closes.get(as_of)
            if base_index is None or current_index is None:
                coverage[f"{window}d"]["missing_index"] += 1
            else:
                coverage[f"{window}d"]["index_pairs"] += 1

            base_factor = symbol_factors.get(start_date)
            current_factor = symbol_factors.get(as_of)
            if base_factor is None or current_factor is None:
                continue
            base_factor_value = float(base_factor.value)
            current_factor_value = float(current_factor.value)
            if (
                not math.isfinite(base_factor_value)
                or not math.isfinite(current_factor_value)
                or base_factor_value <= 0
                or current_factor_value <= 0
            ):
                continue
            coverage[f"{window}d"]["pit_pairs"] += 1
            if base_index is None or current_index is None:
                continue
            pit_sources.update((base_factor.source, current_factor.source))

            stock_return = (
                current_close * current_factor_value / (base_close * base_factor_value) - 1.0
            )
            index_return = current_index / base_index - 1.0
            deviation = stock_return - index_return
            up_threshold, down_threshold = _THRESHOLDS[board][window]
            threshold = up_threshold if deviation >= 0 else down_threshold
            closeness = abs(deviation) / threshold
            windows[f"{window}d"] = {
                "deviation": round(deviation, 6),
                "threshold": threshold,
                "closeness": round(closeness, 6),
                "benchmark_return": round(index_return, 6),
            }
            coverage[f"{window}d"]["computed"] += 1
            valid_window_count += 1

        if not windows:
            continue
        max_closeness = max(item["closeness"] for item in windows.values())
        if max_closeness < min_closeness:
            continue
        rows.append(
            {
                "symbol": symbol,
                "name": str(stock_frames[symbol]["name"].iloc[-1])
                if "name" in stock_frames[symbol].columns and not stock_frames[symbol].empty
                else symbol,
                "board": board,
                "benchmark_symbol": benchmark_symbol,
                "benchmark_fallback": bool(benchmark and benchmark["fallback"]),
                "close": current_close,
                "windows": windows,
                "max_closeness": round(max_closeness, 6),
                "status": _status(max_closeness),
                "as_of": as_of.isoformat(),
            }
        )

    rows.sort(key=lambda row: row["max_closeness"], reverse=True)
    counts = {
        _STATUS_TRIGGERED: sum(row["status"] == _STATUS_TRIGGERED for row in rows),
        _STATUS_EDGE: sum(row["status"] == _STATUS_EDGE for row in rows),
        _STATUS_WATCH: sum(row["status"] == _STATUS_WATCH for row in rows),
    }
    reasons: list[str] = []
    if current_bar_symbols == 0:
        reasons.append("no_current_stock_bars")
    if valid_window_count == 0 and current_bar_symbols > 0:
        total_raw_pairs = sum(item["raw_pairs"] for item in coverage.values())
        total_pit_pairs = sum(item["pit_pairs"] for item in coverage.values())
        if total_raw_pairs and not total_pit_pairs:
            reasons.append("missing_pit_factors")
        if any(stats["missing_index"] for stats in coverage.values()):
            reasons.append("missing_benchmark_dates")
        if not reasons:
            reasons.append("no_comparable_windows")
    partial_coverage = current_bar_symbols < requested_symbols or any(
        stats["computed"] < stats["raw_pairs"] for stats in coverage.values()
    )
    if valid_window_count and partial_coverage:
        reasons.append("partial_market_coverage")
    availability = (
        "blocked" if not valid_window_count else "partial" if partial_coverage else "ready"
    )
    return {
        "availability": availability,
        "source": "sqlite_db",
        "price_mode": "raw",
        "adjustment_method": "pit_factor_ratio",
        "rule_basis": RULE_BASIS,
        "generated_at": generated_at.isoformat(),
        "as_of": as_of.isoformat(),
        "expected_as_of": expected_as_of.isoformat(),
        "freshness": "fresh" if as_of == expected_as_of else "stale",
        "lag_calendar_days": max((expected_as_of - as_of).days, 0),
        "requested_symbols": requested_symbols,
        "current_bar_symbols": current_bar_symbols,
        "lookback_sessions": len(sessions),
        "benchmark_selection": benchmark_selection,
        "pit_factor_sources": sorted(pit_sources),
        "window_coverage": coverage,
        "counts": counts,
        "min_closeness": min_closeness,
        "candidate_count": len(rows),
        "truncated": len(rows) > limit,
        "blocking_reasons": reasons,
        "rows": rows[:limit],
    }


def scan_local_abnormal_deviations(
    *, min_closeness: float = DEFAULT_MIN_CLOSENESS, limit: int = DEFAULT_LIMIT
) -> dict[str, Any]:
    """Scan the local raw SQLite universe using same-date benchmark and PIT data."""
    if not math.isfinite(min_closeness) or not 0 <= min_closeness <= 1:
        raise ValueError("min_closeness must be finite and between 0 and 1")
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    source = source_factory.build_data_source("sqlite_db", cache=None)
    set_workload = getattr(source, "set_workload", None)
    if callable(set_workload):
        set_workload("historical")
    if source.name != "sqlite_db" or source.price_mode() != "raw":
        raise DataError("多日偏移扫描要求可核验的 sqlite_db raw 不复权日线")

    generated_at = now_shanghai()
    expected_as_of = latest_completed_trading_day(generated_at)
    latest_data_date = source.get_latest_daily_date()
    if latest_data_date is None:
        raise DataError("本地 SQLite 历史日线没有有效日期")
    as_of = min(latest_data_date, expected_as_of)
    start = as_of - timedelta(days=WARMUP_CALENDAR_DAYS)
    sessions = source.get_available_daily_dates(start, as_of)
    symbols = source.get_available_symbols()
    if len(sessions) < LOOKBACK_SESSIONS:
        return _empty_snapshot(
            generated_at=generated_at,
            as_of=sessions[-1] if sessions else None,
            expected_as_of=expected_as_of,
            requested_symbols=len(symbols),
            min_closeness=min_closeness,
            reason="insufficient_market_sessions",
            sessions=sessions,
        )
    sessions = sessions[-LOOKBACK_SESSIONS:]
    start = sessions[0]
    stock_frames = source.fetch_daily(symbols, start, as_of, adjust="")

    candidate_codes = list(dict.fromkeys(
        code for candidates in _BENCHMARK_PREFERENCE.values() for code in candidates
    ))
    index_frames: dict[str, pd.DataFrame] = {}
    for code in candidate_codes:
        try:
            result = source.fetch_index([code], start, as_of)
        except (DataError, OSError, ValueError):
            continue
        frame = result.get(code)
        if frame is not None and not frame.empty:
            index_frames[code] = frame
    factor_records = source.get_point_in_time_adjustment_factors(symbols, sessions)
    return build_abnormal_deviation_snapshot(
        stock_frames,
        index_frames,
        session_dates=sessions,
        pit_adjustment_factors=factor_records,
        generated_at=generated_at,
        expected_as_of=expected_as_of,
        requested_symbols=len(symbols),
        min_closeness=min_closeness,
        limit=limit,
    )
