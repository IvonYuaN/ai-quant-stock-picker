from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from aqsp.core.time import today_shanghai
from aqsp.data.trading_calendar import load_optional_trade_calendar, trading_day_lag


@dataclass(frozen=True)
class FreshnessReport:
    symbol: str
    last_date: str
    delay_days: int
    status: str


def _resolve_status(delay_days: int) -> str:
    if delay_days <= 1:
        return "fresh"
    if delay_days <= 3:
        return "stale"
    return "critical"


def check_freshness(frames: dict[str, pd.DataFrame]) -> list[FreshnessReport]:
    today = today_shanghai()
    latest_dates = [
        _frame_latest_date(df)
        for df in frames.values()
        if not df.empty and "date" in df.columns
    ]
    valid_latest_dates = [value for value in latest_dates if value is not None]
    calendar_df = (
        load_optional_trade_calendar(
            min(valid_latest_dates) - timedelta(days=31),
            today,
        )
        if valid_latest_dates
        else None
    )
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
        last = _frame_latest_date(df)
        if last is None:
            reports.append(
                FreshnessReport(
                    symbol=symbol,
                    last_date="",
                    delay_days=-1,
                    status="critical",
                )
            )
            continue
        delay = -1 if last > today else trading_day_lag(
            last, today, calendar_df=calendar_df
        )
        reports.append(
            FreshnessReport(
                symbol=symbol,
                last_date=last.isoformat(),
                delay_days=delay,
                status="critical" if delay < 0 else _resolve_status(delay),
            )
        )
    return reports


def _frame_latest_date(df: pd.DataFrame) -> date | None:
    values: list[date] = []
    for value in df["date"]:
        try:
            parsed = pd.Timestamp(value)
        except (TypeError, ValueError):
            continue
        if pd.isna(parsed):
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.tz_convert("Asia/Shanghai")
        values.append(parsed.date())
    return max(values) if values else None


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
