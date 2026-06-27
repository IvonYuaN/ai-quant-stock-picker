from __future__ import annotations

import json
import os
from datetime import date
from typing import Any

import pandas as pd

from aqsp.core.time import now_shanghai
from aqsp.ledger.base import read_ledger


REAL_SIGNAL_STATUSES = frozenset(
    {
        "pending",
        "validated",
        "not_executable",
        "watch_only",
        "run_completed_no_picks",
    }
)

PAPER_TRACKING_STATUSES = frozenset(
    {
        "open",
        "closed",
        "pending_entry",
        "not_executable",
    }
)

EXECUTABILITY_FEEDBACK_STATUSES = frozenset({"validated", "not_executable"})

DEFAULT_COLD_START_MIN_DAYS = 30
MIN_EXECUTABILITY_FEEDBACK_ATTEMPTS = 5
MAX_EXECUTABILITY_BLOCK_RATE = 0.35
EXECUTABILITY_WEIGHT_MULTIPLIER = 0.5


def cold_start_min_days() -> int:
    raw = os.getenv("AQSP_COLD_START_MIN_DAYS", "").strip()
    if not raw:
        return DEFAULT_COLD_START_MIN_DAYS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_COLD_START_MIN_DAYS
    if str(os.getenv("PYTEST_CURRENT_TEST", "")).strip():
        return max(value, 1)
    return max(value, DEFAULT_COLD_START_MIN_DAYS)


def count_independent_signal_days(ledger_path: str) -> int:
    return len(collect_independent_signal_dates(ledger_path))


def collect_independent_signal_dates(ledger_path: str) -> set[str]:
    rows = read_ledger(ledger_path)
    return _collect_independent_dates(
        rows,
        allowed_statuses=REAL_SIGNAL_STATUSES,
        require_status=False,
    )


def collect_simulated_signal_dates(ledger_path: str) -> set[str]:
    rows = read_ledger(ledger_path)
    signal_dates: set[str] = set()
    for row in rows:
        if not bool(row.get("is_simulated")):
            continue
        signal_date = ledger_signal_date(row)
        if signal_date:
            signal_dates.add(signal_date)
    return signal_dates


def count_paper_tracking_days(paper_ledger_path: str) -> int:
    return len(collect_paper_tracking_dates(paper_ledger_path))


def collect_paper_tracking_dates(paper_ledger_path: str) -> set[str]:
    rows = read_ledger(paper_ledger_path)
    return _collect_independent_dates(
        rows,
        allowed_statuses=PAPER_TRACKING_STATUSES,
        require_status=True,
    )


def _collect_independent_dates(
    rows: list[dict[str, Any]],
    *,
    allowed_statuses: frozenset[str],
    require_status: bool,
) -> set[str]:
    signal_dates: set[str] = set()
    for row in rows:
        if bool(row.get("is_simulated")):
            continue
        status = str(row.get("status") or "").strip()
        if require_status and not status:
            continue
        if status and status not in allowed_statuses:
            continue
        if not str(row.get("symbol") or "").strip() and status != "run_completed_no_picks":
            continue
        has_signal_payload = any(
            row.get(key) not in (None, "")
            for key in ("thresholds_version", "status", "rating", "score", "strategies")
        )
        if not has_signal_payload:
            continue
        signal_date = ledger_signal_date(row)
        if signal_date:
            signal_dates.add(signal_date)
    return signal_dates


def ledger_signal_date(row: dict[str, Any]) -> str:
    for key in ("signal_date", "signal_day_group", "date", "created_at"):
        raw = str(row.get(key) or "").strip()
        if len(raw) >= 10:
            candidate = raw[:10]
            try:
                date.fromisoformat(candidate)
            except ValueError:
                continue
            return candidate
    return ""


def strategy_executability_weight_adjustments(
    ledger_path: str,
    *,
    min_attempts: int = MIN_EXECUTABILITY_FEEDBACK_ATTEMPTS,
    max_block_rate: float = MAX_EXECUTABILITY_BLOCK_RATE,
    penalty_multiplier: float = EXECUTABILITY_WEIGHT_MULTIPLIER,
) -> tuple[dict[str, float], dict[str, str]]:
    rows = read_ledger(ledger_path)
    attempts: dict[str, int] = {}
    blocked: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "").strip()
        if status not in EXECUTABILITY_FEEDBACK_STATUSES:
            continue
        strategies = _row_strategies(row.get("strategies"))
        if not strategies:
            continue
        for strategy in strategies:
            attempts[strategy] = attempts.get(strategy, 0) + 1
            if status == "not_executable":
                blocked[strategy] = blocked.get(strategy, 0) + 1

    adjustments: dict[str, float] = {}
    reasons: dict[str, str] = {}
    for strategy, total in sorted(attempts.items()):
        if total < min_attempts:
            continue
        block_count = blocked.get(strategy, 0)
        rate = block_count / total
        if rate <= max_block_rate:
            continue
        adjustments[strategy] = penalty_multiplier
        reasons[strategy] = (
            f"recent not_executable rate {rate:.0%} ({block_count}/{total})"
        )
    return adjustments, reasons


def compute_real_pnl(ledger_path: str) -> tuple[float, float, float]:
    rows = read_ledger(ledger_path)
    if not rows:
        return 0.0, 0.0, 0.0

    today = now_shanghai().date()
    validated: list[tuple[date, float]] = []
    for row in rows:
        if row.get("status") != "validated":
            continue
        ret_pct = row.get("return_pct")
        if ret_pct is None:
            continue
        signal_date_str = row.get("signal_date", "")
        if not signal_date_str:
            continue
        try:
            signal_date = date.fromisoformat(signal_date_str)
        except (ValueError, TypeError):
            continue
        validated.append((signal_date, float(ret_pct)))

    if not validated:
        return 0.0, 0.0, 0.0

    validated.sort(key=lambda x: x[0])

    latest_signal_date = validated[-1][0]
    same_day_returns = [r for d, r in validated if d == latest_signal_date]
    daily_cum = 1.0
    for value in same_day_returns:
        daily_cum *= 1 + value / 100
    daily_pnl = (daily_cum - 1) * 100

    weekly_returns = [r for d, r in validated if (today - d).days <= 7]
    weekly_cum = 1.0
    for value in weekly_returns:
        weekly_cum *= 1 + value / 100
    weekly_pnl = (weekly_cum - 1) * 100 if weekly_returns else 0.0

    monthly_returns = [r for d, r in validated if (today - d).days <= 30]
    monthly_cum = 1.0
    for value in monthly_returns:
        monthly_cum *= 1 + value / 100
    monthly_pnl = (monthly_cum - 1) * 100 if monthly_returns else 0.0

    return daily_pnl, weekly_pnl, monthly_pnl


def _row_strategies(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [item.strip() for item in text.split(",") if item.strip()]
        if isinstance(parsed, (list, tuple)):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return []
    return []


def compute_paper_mark_to_market_pnl(
    paper_ledger_path: str,
    frames: dict[str, pd.DataFrame],
) -> tuple[float, float, float] | None:
    rows = read_ledger(paper_ledger_path)
    open_rows = [row for row in rows if row.get("status") == "open"]
    if not open_rows:
        return None

    today = now_shanghai().date()
    daily_returns: list[float] = []
    weekly_returns: list[float] = []
    monthly_returns: list[float] = []
    for row in open_rows:
        symbol = str(row.get("symbol") or "")
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            continue
        entry_price = _safe_float(row.get("entry_price"))
        if entry_price <= 0:
            continue
        frame = frame.sort_values("date").reset_index(drop=True)
        latest = frame.iloc[-1]
        latest_close = _safe_float(latest.get("close"))
        if latest_close <= 0:
            continue
        signal_date = _parse_row_date(row.get("signal_date") or row.get("entry_date"))
        if signal_date == today:
            entry_ret = (latest_close - entry_price) / entry_price * 100
            daily_returns.append(entry_ret)
        else:
            prev_close = _previous_close(frame, latest_close)
            if prev_close > 0:
                daily_returns.append((latest_close - prev_close) / prev_close * 100)
        weekly_ret = _period_mark_to_market_return(
            row,
            frame,
            latest_close=latest_close,
            entry_price=entry_price,
            today=today,
            lookback_days=7,
        )
        if weekly_ret is not None:
            weekly_returns.append(weekly_ret)
        monthly_ret = _period_mark_to_market_return(
            row,
            frame,
            latest_close=latest_close,
            entry_price=entry_price,
            today=today,
            lookback_days=30,
        )
        if monthly_ret is not None:
            monthly_returns.append(monthly_ret)

    if not daily_returns:
        return None
    return (
        _compound_returns(daily_returns),
        _compound_returns(weekly_returns),
        _compound_returns(monthly_returns),
    )


def _compound_returns(values: list[float]) -> float:
    if not values:
        return 0.0
    cumulative = 1.0
    for value in values:
        cumulative *= 1 + value / 100
    return (cumulative - 1) * 100


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _previous_close(frame: pd.DataFrame, latest_close: float) -> float:
    if len(frame) < 2:
        return 0.0
    previous = frame.iloc[-2]
    prev_close = _safe_float(previous.get("close"))
    if prev_close <= 0:
        return 0.0
    return prev_close


def _period_mark_to_market_return(
    row: dict[str, Any],
    frame: pd.DataFrame,
    *,
    latest_close: float,
    entry_price: float,
    today: date,
    lookback_days: int,
) -> float | None:
    entry_date = _parse_row_date(row.get("entry_date") or row.get("signal_date"))
    period_start = today - pd.Timedelta(days=lookback_days).to_pytimedelta()
    if entry_date is not None and entry_date >= period_start:
        base_price = entry_price
    else:
        base_price = _close_on_or_before(frame, period_start)
    if base_price <= 0:
        return None
    return (latest_close - base_price) / base_price * 100


def _close_on_or_before(frame: pd.DataFrame, target: date) -> float:
    if "date" not in frame.columns:
        return 0.0
    dated = frame.copy()
    dated["_date"] = pd.to_datetime(dated["date"], errors="coerce").dt.date
    dated = dated.dropna(subset=["_date"])
    if dated.empty:
        return 0.0
    before = dated[dated["_date"] <= target]
    if before.empty:
        return 0.0
    return _safe_float(before.iloc[-1].get("close"))


def _parse_row_date(value: object) -> date | None:
    raw = str(value or "").strip()
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None
