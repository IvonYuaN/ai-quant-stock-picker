from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from aqsp.core.time import today_shanghai


@dataclass(frozen=True)
class FreshnessReport:
    symbol: str
    last_date: str
    delay_days: int
    status: str


def _count_calendar_days(last: date, today: date) -> int:
    return (today - last).days


def _resolve_status(delay_days: int) -> str:
    if delay_days <= 1:
        return "fresh"
    if delay_days <= 3:
        return "stale"
    return "critical"


def _adjust_for_weekend(last: date, today: date) -> int:
    raw = _count_calendar_days(last, today)
    if raw <= 1:
        return raw
    if last.weekday() == 4 and today.weekday() == 0:
        return 1
    if last.weekday() == 4 and today.weekday() == 1:
        return 2
    if last.weekday() == 5 and today.weekday() == 0:
        return 1
    if last.weekday() == 5 and today.weekday() == 1:
        return 2
    if last.weekday() == 4 and today.weekday() == 2:
        return 3
    return raw


def check_freshness(frames: dict[str, pd.DataFrame]) -> list[FreshnessReport]:
    today = today_shanghai()
    reports: list[FreshnessReport] = []
    for symbol, df in frames.items():
        if df.empty or "date" not in df.columns:
            reports.append(
                FreshnessReport(
                    symbol=symbol,
                    last_date="",
                    delay_days=-1,
                    status="critical",
                )
            )
            continue
        last_ts = pd.to_datetime(df["date"], errors="coerce").dropna().max()
        if pd.isna(last_ts):
            reports.append(
                FreshnessReport(
                    symbol=symbol,
                    last_date="",
                    delay_days=-1,
                    status="critical",
                )
            )
            continue
        last = last_ts.date()
        delay = _adjust_for_weekend(last, today)
        reports.append(
            FreshnessReport(
                symbol=symbol,
                last_date=last.isoformat(),
                delay_days=delay,
                status=_resolve_status(delay),
            )
        )
    return reports


def format_freshness_report(reports: list[FreshnessReport]) -> str:
    if not reports:
        return ""
    status_order = {"critical": 0, "stale": 1, "fresh": 2}
    sorted_reports = sorted(
        reports, key=lambda r: (status_order.get(r.status, 9), r.symbol)
    )
    lines = ["## 数据新鲜度", ""]
    lines.append("| 标的 | 最新日期 | 延迟天数 | 状态 |")
    lines.append("|------|----------|----------|------|")
    status_labels = {
        "fresh": "✅ 新鲜",
        "stale": "🟡 过期",
        "critical": "🔴 严重过期",
    }
    for r in sorted_reports:
        delay_display = str(r.delay_days) if r.delay_days >= 0 else "N/A"
        lines.append(
            f"| {r.symbol} "
            f"| {r.last_date or 'N/A'} "
            f"| {delay_display} "
            f"| {status_labels.get(r.status, r.status)} |"
        )
    return "\n".join(lines)
