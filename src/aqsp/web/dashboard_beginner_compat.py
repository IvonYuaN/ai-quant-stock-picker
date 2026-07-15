"""Small compatibility helpers retained for historical beginner callers.

This module deliberately contains no Streamlit page renderer.  The only
executable dashboard is ``aqsp.web.dashboard``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime

import pandas as pd
import streamlit as st

from aqsp.core.time import now_shanghai
from aqsp.web.data_provider import DashboardDataProvider


@dataclass(frozen=True)
class BeginnerPosition:
    symbol: str
    name: str
    entry_date: str
    entry_price: float
    stop_loss: float | None
    take_profit: float | None
    horizon_days: int | None


@dataclass(frozen=True)
class TimeLane:
    code: str
    name: str
    task_id: str
    summary: str


_TIME_LANES: tuple[TimeLane, ...] = (
    TimeLane("09:25", "开盘前", "main_chain", "先看今天最重要的股票和卡点。"),
    TimeLane("10:00", "早盘看一眼", "morning_breakout", "只看早上有没有明显走强的股票。"),
    TimeLane("12:00", "午盘回看", "intraday", "中午只回看上午变化，不急着下结论。"),
    TimeLane("14:40", "尾盘确认", "closing_premium", "收盘前确认下午有没有继续走强。"),
    TimeLane("15:30", "收盘复盘", "closing_review", "看今天哪些判断对了，哪些需要改。"),
    TimeLane("21:00", "明日预案", "briefing", "睡前只看明天重点，不看噪音。"),
)

BEGINNER_GLOSSARY: dict[str, tuple[tuple[str, str], ...]] = {
    "技术指标": (
        ("bias20", "现在价格离最近 20 天平均价格有多远。"),
        ("rps", "强弱排名。数字越高，说明最近相对更强。"),
        ("均线多头排列", "短期平均价格在长期平均价格上面。"),
    ),
    "形态描述": (
        ("均线缩量回踩", "价格回落但成交量缩小，先观察是否承接。"),
        ("N字反弹", "先跌、再涨、再整理的短线形态。"),
        ("突破平台", "价格走出横盘区，需继续确认承接。"),
    ),
    "纸面规则": (
        ("纸面止损线", "研究失效线，跌破后停止纸面跟踪。"),
        ("T+1", "A 股纸面入场后等下一个交易日验证。"),
        ("纸面持有", "系统继续跟踪，不代表券商账户真实买入。"),
    ),
}


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


def default_lane_task_id() -> str:
    current_time = now_shanghai().time()
    if current_time < dtime(9, 30):
        return "main_chain"
    if current_time < dtime(11, 30):
        return "morning_breakout"
    if current_time < dtime(15, 0):
        return "intraday"
    if current_time < dtime(20, 0):
        return "closing_review"
    return "briefing"
