"""Read-only historical daily abnormal signals from the local raw SQLite source."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from typing import Any

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.core.time import latest_completed_trading_day, now_shanghai
from aqsp.data import source_factory
from aqsp.data.cache import AdjustmentFactorRecord
from aqsp.strategies.thresholds import load_thresholds


# Compare the latest completed bar against the preceding 60 valid daily bars.
LOOKBACK_SESSIONS = 60
WARMUP_CALENDAR_DAYS = 180
SIGNAL_PRIORITY = ("new_high", "new_low", "volume_surge")


def scan_local_daily_signals() -> dict[str, Any]:
    """Scan latest available raw daily bars; stale results remain explicitly historical."""
    source = source_factory.build_data_source("sqlite_db", cache=None)
    set_workload = getattr(source, "set_workload", None)
    if callable(set_workload):
        set_workload("historical")
    source_id = str(getattr(source, "name", ""))
    price_mode_fn = getattr(source, "price_mode", None)
    price_mode = str(price_mode_fn()) if callable(price_mode_fn) else "unknown"
    if source_id != "sqlite_db" or price_mode != "raw":
        raise DataError("本地历史异动扫描要求可核验的 sqlite_db raw 不复权日线")

    generated_at = now_shanghai()
    expected_as_of = latest_completed_trading_day(generated_at)
    latest_data_date = source.get_latest_daily_date()
    if latest_data_date is None:
        raise DataError("本地 SQLite 历史日线没有有效日期")
    scan_as_of = min(latest_data_date, expected_as_of)
    symbols = source.get_available_symbols()
    if not symbols:
        raise DataError("本地 SQLite 数据库没有可扫描的股票代码")
    start = scan_as_of - timedelta(days=WARMUP_CALENDAR_DAYS)
    frames = source.fetch_daily(symbols, start, scan_as_of, adjust="")
    if not frames:
        raise DataError("本地 SQLite 未返回历史日线")
    session_dates = sorted(
        {
            parsed
            for frame in frames.values()
            if frame is not None and not frame.empty and "date" in frame
            for parsed in pd.to_datetime(frame["date"], errors="coerce")
            .dropna()
            .dt.date
        }
    )
    pit_adjustment_factors = source.get_point_in_time_adjustment_factors(
        list(frames), session_dates
    )
    fetched_latest_date = max(
        (
            parsed
            for frame in frames.values()
            if frame is not None and not frame.empty and "date" in frame
            for parsed in pd.to_datetime(frame["date"], errors="coerce")
            .dropna()
            .dt.date
        ),
        default=None,
    )
    if fetched_latest_date is None:
        raise DataError("本地 SQLite 历史日线没有有效日期")

    thresholds = load_thresholds()
    volume_threshold = float(thresholds.closing_premium.volume_signal_ratio)
    if not math.isfinite(volume_threshold) or volume_threshold <= 0:
        raise DataError("量能观察阈值无效，已停止计算")
    return build_historical_signal_snapshot(
        frames,
        generated_at=generated_at,
        expected_as_of=expected_as_of,
        latest_data_date=fetched_latest_date,
        requested_symbols=len(symbols),
        volume_threshold=volume_threshold,
        pit_adjustment_factors=pit_adjustment_factors,
    )


def build_historical_signal_snapshot(
    frames: dict[str, pd.DataFrame],
    *,
    generated_at: datetime,
    expected_as_of: date,
    latest_data_date: date,
    requested_symbols: int,
    volume_threshold: float,
    pit_adjustment_factors: dict[str, dict[date, AdjustmentFactorRecord]] | None = None,
) -> dict[str, Any]:
    """Build a historical signal view from per-symbol raw daily frames."""
    if not math.isfinite(volume_threshold) or volume_threshold <= 0:
        raise ValueError("volume_threshold must be finite and positive")
    rows: list[dict[str, Any]] = []
    eligible_symbols = 0
    skipped_stale_symbols = 0
    factor_covered_symbols = 0
    skipped_missing_factor_symbols = 0
    factor_sources: set[str] = set()
    counts = {signal: 0 for signal in SIGNAL_PRIORITY}
    as_of_text = latest_data_date.isoformat()

    for symbol, frame in frames.items():
        if frame is None or frame.empty:
            continue
        if not {"date", "close", "volume"} <= set(frame.columns):
            continue
        data = frame.copy()
        data["date"] = pd.to_datetime(data["date"], errors="coerce")
        data["close"] = pd.to_numeric(data["close"], errors="coerce")
        data["volume"] = pd.to_numeric(data["volume"], errors="coerce")
        data = data.dropna(subset=["date", "close", "volume"])
        data = data[(data["date"].dt.date <= latest_data_date) & (data["close"] > 0)]
        data = data.sort_values("date", kind="stable").drop_duplicates(
            "date", keep="last"
        )
        if "suspended" in data.columns:
            data = data[~data["suspended"].fillna(True).astype(bool)]
        else:
            data = data[data["volume"] > 0]
        if data.empty:
            continue
        if data["date"].iloc[-1].date() != latest_data_date:
            skipped_stale_symbols += 1
            continue
        eligible_symbols += 1
        close = data["close"]
        previous_volume_mean = (
            data["volume"].shift(1).rolling(5, min_periods=5).mean().iloc[-1]
        )
        current_close = float(close.iloc[-1])
        volume_ratio = (
            float(data["volume"].iloc[-1] / previous_volume_mean)
            if pd.notna(previous_volume_mean) and previous_volume_mean > 0
            else None
        )
        comparable_close: pd.Series | None = None
        high_60: float | None = None
        low_60: float | None = None
        if len(data) >= LOOKBACK_SESSIONS + 1:
            window = data.tail(LOOKBACK_SESSIONS + 1)
            window_dates = [value.date() for value in window["date"]]
            records = (pit_adjustment_factors or {}).get(str(symbol), {})
            window_factors = [records.get(day) for day in window_dates]
            if all(record is not None for record in window_factors):
                factor_values = [record.value for record in window_factors if record]
                current_factor = factor_values[-1]
                if math.isfinite(current_factor) and current_factor > 0:
                    factor_ratio = [value / current_factor for value in factor_values]
                    comparable_close = (
                        window["close"].reset_index(drop=True)
                        * pd.Series(factor_ratio, dtype="float64")
                    )
                    prior_closes = comparable_close.iloc[:-1]
                    high_60 = float(prior_closes.max())
                    low_60 = float(prior_closes.min())
                    factor_covered_symbols += 1
                    factor_sources.update(record.source for record in window_factors if record)
            if comparable_close is None:
                skipped_missing_factor_symbols += 1

        previous_close = (
            float(comparable_close.iloc[-2]) if comparable_close is not None else None
        )
        comparable_current_close = (
            float(comparable_close.iloc[-1]) if comparable_close is not None else None
        )
        change_pct = (
            (comparable_current_close / previous_close - 1.0) * 100.0
            if previous_close is not None and previous_close > 0
            else None
        )
        signals: list[str] = []
        if (
            comparable_current_close is not None
            and high_60 is not None
            and comparable_current_close > high_60
        ):
            signals.append("new_high")
        if (
            comparable_current_close is not None
            and low_60 is not None
            and comparable_current_close < low_60
        ):
            signals.append("new_low")
        if volume_ratio is not None and volume_ratio >= volume_threshold:
            signals.append("volume_surge")
        if not signals:
            continue

        name = str(data["name"].iloc[-1]) if "name" in data.columns else str(symbol)
        rows.append(
            {
                "symbol": str(symbol),
                "name": name,
                "close": current_close,
                "change_pct": change_pct,
                "volume_ratio_5d": volume_ratio,
                "high_60d": high_60,
                "low_60d": low_60,
                "price_comparable": comparable_close is not None,
                "signals": signals,
                "as_of": as_of_text,
            }
        )
        for signal in signals:
            counts[signal] += 1

    rows.sort(
        key=lambda row: (
            min(SIGNAL_PRIORITY.index(signal) for signal in row["signals"]),
            -abs(row["change_pct"] or 0.0),
        )
    )
    lag_days = max((expected_as_of - latest_data_date).days, 0)
    return {
        "source": "sqlite_db",
        "price_mode": "raw",
        "adjustment_method": "pit_factor_ratio",
        "generated_at": generated_at.isoformat(),
        "as_of": as_of_text,
        "expected_as_of": expected_as_of.isoformat(),
        "freshness": "fresh" if latest_data_date == expected_as_of else "stale",
        "lag_calendar_days": lag_days,
        "requested_symbols": requested_symbols,
        "eligible_symbols": eligible_symbols,
        "skipped_stale_symbols": skipped_stale_symbols,
        "factor_covered_symbols": factor_covered_symbols,
        "skipped_missing_factor_symbols": skipped_missing_factor_symbols,
        "pit_factor_sources": sorted(factor_sources),
        "lookback_sessions": LOOKBACK_SESSIONS,
        "volume_threshold_ratio": volume_threshold,
        "counts": counts,
        "rows": rows,
    }
