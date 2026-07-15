"""Compatibility entrypoint; the old beginner dashboard is retired.

本页面仅供研究参考，不构成交易指令或投资建议；纸面跟踪结果需人工复核。
Historical helper imports remain available,
but every executable entrypoint delegates to the canonical Dashboard.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from aqsp.web.data_provider import DashboardDataProvider
from aqsp.web.dashboard_beginner_compat import (
    BEGINNER_GLOSSARY,
    BeginnerPosition,
    TimeLane,
    _TIME_LANES,
    default_lane_task_id,
)

__all__ = [
    "BEGINNER_GLOSSARY",
    "BeginnerPosition",
    "TimeLane",
    "_TIME_LANES",
    "build_positions",
    "default_lane_task_id",
    "get_provider",
    "load_runtime_snapshot",
    "main",
]


@st.cache_resource(show_spinner=False)
def get_provider() -> DashboardDataProvider:
    return DashboardDataProvider()


@st.cache_data(ttl=120, show_spinner=False)
def load_runtime_snapshot() -> dict[str, object]:
    provider = get_provider()
    summary = provider.summarize()
    signal_date = summary.latest_signal_date
    return {
        "summary": summary,
        "signal_date": signal_date,
        "task_snapshots": provider.task_snapshots(signal_date),
        "paper_summary": provider.paper_summary(signal_date),
        "open_positions": provider.open_positions_frame(signal_date=signal_date),
        "date_overview": provider.date_overview(signal_date) if signal_date else None,
        "timeline": provider.timeline_frame(limit=12),
    }


def _to_float(value: object) -> float:
    try:
        return 0.0 if value in (None, "", "-") else float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_optional_float(value: object) -> float | None:
    try:
        return None if value in (None, "", "-") else float(value)
    except (TypeError, ValueError):
        return None


def _to_optional_int(value: object) -> int | None:
    try:
        return None if value in (None, "", "-") else int(float(value))
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=120, show_spinner=False)
def build_positions() -> list[BeginnerPosition]:
    frame = load_runtime_snapshot()["open_positions"]
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    columns = ["代码", "名称", "纸面入场日", "纸面入场价", "止损", "止盈", "持有周期"]
    return [
        BeginnerPosition(
            symbol=str(symbol or "").strip(),
            name=str(name or "").strip(),
            entry_date=str(entry_date or "").strip(),
            entry_price=_to_float(entry_price),
            stop_loss=_to_optional_float(stop_loss),
            take_profit=_to_optional_float(take_profit),
            horizon_days=_to_optional_int(horizon_days),
        )
        for (
            symbol,
            name,
            entry_date,
            entry_price,
            stop_loss,
            take_profit,
            horizon_days,
        ) in frame.reindex(columns=columns).itertuples(index=False, name=None)
    ]


def main() -> None:
    """Run the one canonical Dashboard implementation."""
    from aqsp.web.dashboard import main as canonical_dashboard_main

    canonical_dashboard_main()


if __name__ == "__main__":
    main()
