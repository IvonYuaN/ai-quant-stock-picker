from __future__ import annotations

from datetime import date

import pandas as pd

from aqsp.core.time import today_shanghai


def latest_trade_date(frames: dict[str, pd.DataFrame]) -> date | None:
    dates: list[date] = []
    for frame in frames.values():
        if frame.empty or "date" not in frame.columns:
            continue
        value = pd.to_datetime(frame["date"], errors="coerce").dropna().max()
        if pd.notna(value):
            dates.append(value.date())
    return max(dates) if dates else None


def assert_fresh_data(frames: dict[str, pd.DataFrame], max_lag_days: int) -> date:
    latest = latest_trade_date(frames)
    if latest is None:
        raise RuntimeError("no valid market data loaded")

    lag = (today_shanghai() - latest).days
    if lag > max_lag_days:
        raise RuntimeError(
            f"market data is stale: latest={latest.isoformat()}, lag={lag} days, max={max_lag_days}"
        )
    return latest
