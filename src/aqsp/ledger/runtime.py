from __future__ import annotations

from datetime import date
from typing import Any

from aqsp.core.time import now_shanghai
from aqsp.ledger.base import read_ledger


def count_independent_signal_days(ledger_path: str) -> int:
    rows = read_ledger(ledger_path)
    signal_dates: set[str] = set()
    for row in rows:
        if bool(row.get("is_simulated")):
            continue
        if not str(row.get("symbol") or "").strip():
            continue
        if str(row.get("status") or "").strip() == "not_executable":
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
    return len(signal_dates)


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
