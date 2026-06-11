"""Streamlit 新手友好仪表盘 - 基于真实落盘数据的简洁导航页。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime

import pandas as pd
import streamlit as st

from aqsp.core.time import now_shanghai
from aqsp.web.data_provider import DashboardDataProvider, DashboardTaskSnapshot

st.set_page_config(
    page_title="AQSP 新手看板",
    layout="wide",
    initial_sidebar_state="collapsed",
)


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
    TimeLane("09:25", "开盘前", "main_chain", "先看今天主链推荐和阻塞原因。"),
    TimeLane("10:00", "早盘看一眼", "morning_breakout", "只看确认走强的早盘突破。"),
    TimeLane("12:00", "午盘回看", "intraday", "中午只回看上午变化，下午再观察。"),
    TimeLane("14:40", "尾盘确认", "closing_premium", "收盘前确认承接和隔夜价值。"),
    TimeLane("15:30", "收盘复盘", "closing_review", "看今天哪些判断成立、哪些失效。"),
    TimeLane("21:00", "明日预案", "briefing", "睡前只看明天重点，不看噪音。"),
)


def _inject_beginner_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.1rem;
            padding-bottom: 1.6rem;
            max-width: 1220px;
        }
        .aqsp-hero {
            padding: 1rem 1.1rem;
            border-radius: 20px;
            background:
                radial-gradient(circle at top right, rgba(217, 119, 6, 0.16), transparent 30%),
                linear-gradient(135deg, #fbf7ef 0%, #f3f7fb 48%, #eef6f1 100%);
            border: 1px solid rgba(32, 58, 76, 0.12);
            box-shadow: 0 16px 36px rgba(32, 58, 76, 0.08);
            margin-bottom: 0.9rem;
        }
        .aqsp-hero-title {
            font-size: 0.82rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: #6a7682;
            margin-bottom: 0.35rem;
        }
        .aqsp-hero-main {
            font-size: 1.45rem;
            line-height: 1.35;
            font-weight: 700;
            color: #173247;
            margin-bottom: 0.28rem;
        }
        .aqsp-hero-sub {
            font-size: 0.94rem;
            color: #4b5f6e;
            line-height: 1.55;
        }
        .aqsp-strip {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.8rem;
            margin: 0.45rem 0 1rem 0;
        }
        .aqsp-strip-card {
            border-radius: 18px;
            padding: 0.95rem 1rem;
            border: 1px solid rgba(32, 58, 76, 0.1);
            background: linear-gradient(180deg, #fffdf8 0%, #f6f8fb 100%);
            box-shadow: 0 10px 24px rgba(32, 58, 76, 0.05);
        }
        .aqsp-strip-label {
            font-size: 0.76rem;
            color: #6a7682;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 0.35rem;
        }
        .aqsp-strip-value {
            font-size: 1.85rem;
            line-height: 1;
            font-weight: 700;
            color: #173247;
            margin-bottom: 0.28rem;
        }
        .aqsp-strip-meta {
            font-size: 0.86rem;
            color: #526575;
            line-height: 1.45;
        }
        .aqsp-nav-grid {
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            gap: 0.65rem;
            margin: 0.45rem 0 0.8rem 0;
        }
        .aqsp-nav-card {
            border-radius: 16px;
            padding: 0.75rem 0.7rem;
            border: 1px solid rgba(32, 58, 76, 0.12);
            background: #f8fafb;
            min-height: 82px;
        }
        .aqsp-nav-card.active {
            background: linear-gradient(180deg, #193549 0%, #264f68 100%);
            border-color: rgba(25, 53, 73, 0.92);
        }
        .aqsp-nav-code {
            font-size: 1rem;
            font-weight: 700;
            color: #173247;
            margin-bottom: 0.22rem;
        }
        .aqsp-nav-name {
            font-size: 0.9rem;
            color: #526575;
            line-height: 1.35;
        }
        .aqsp-nav-card.active .aqsp-nav-code,
        .aqsp-nav-card.active .aqsp-nav-name {
            color: #f7fafc;
        }
        .aqsp-panel {
            border-radius: 18px;
            border: 1px solid rgba(32, 58, 76, 0.1);
            background: #fcfcfa;
            box-shadow: 0 10px 28px rgba(32, 58, 76, 0.05);
            padding: 1rem 1rem 0.75rem 1rem;
            margin-bottom: 0.9rem;
        }
        .aqsp-panel-title {
            font-size: 1rem;
            font-weight: 700;
            color: #173247;
            margin-bottom: 0.2rem;
        }
        .aqsp-panel-sub {
            font-size: 0.9rem;
            color: #586a79;
            line-height: 1.5;
            margin-bottom: 0.7rem;
        }
        @media (max-width: 960px) {
            .aqsp-strip, .aqsp-nav-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_resource(show_spinner=False)
def get_provider() -> DashboardDataProvider:
    return DashboardDataProvider()


def _default_lane_task_id() -> str:
    current_time = now_shanghai().time()
    if current_time < dtime(9, 30):
        return "main_chain"
    if current_time < dtime(11, 30):
        return "morning_breakout"
    if current_time < dtime(13, 0):
        return "intraday"
    if current_time < dtime(14, 50):
        return "intraday"
    if current_time < dtime(15, 0):
        return "closing_premium"
    if current_time < dtime(20, 0):
        return "closing_review"
    return "briefing"


@st.cache_data(ttl=120, show_spinner=False)
def load_runtime_snapshot() -> dict[str, object]:
    provider = get_provider()
    summary = provider.summarize()
    signal_date = summary.latest_signal_date
    task_snapshots = provider.task_snapshots(signal_date)
    paper_summary = provider.paper_summary(signal_date)
    open_positions = provider.open_positions_frame(signal_date=signal_date)
    date_overview = provider.date_overview(signal_date) if signal_date else None
    timeline = provider.timeline_frame(limit=12)
    return {
        "summary": summary,
        "signal_date": signal_date,
        "task_snapshots": task_snapshots,
        "paper_summary": paper_summary,
        "open_positions": open_positions,
        "date_overview": date_overview,
        "timeline": timeline,
    }


def _to_float(value: object) -> float:
    try:
        if value in (None, "", "-"):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_optional_float(value: object) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_optional_int(value: object) -> int | None:
    try:
        if value in (None, "", "-"):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=120, show_spinner=False)
def build_positions() -> list[BeginnerPosition]:
    runtime = load_runtime_snapshot()
    frame = runtime["open_positions"]
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    positions: list[BeginnerPosition] = []
    for _, row in frame.iterrows():
        positions.append(
            BeginnerPosition(
                symbol=str(row.get("代码", "") or "").strip(),
                name=str(row.get("名称", "") or "").strip(),
                entry_date=str(row.get("纸面入场日", "") or "").strip(),
                entry_price=_to_float(row.get("纸面入场价")),
                stop_loss=_to_optional_float(row.get("止损")),
                take_profit=_to_optional_float(row.get("止盈")),
                horizon_days=_to_optional_int(row.get("持有周期")),
            )
        )
    return positions


@st.cache_data(ttl=120, show_spinner=False)
def lane_options() -> list[tuple[str, str]]:
    runtime = load_runtime_snapshot()
    signal_date = str(runtime.get("signal_date", "") or "")
    provider = get_provider()
    options: list[tuple[str, str]] = []
    for lane in _TIME_LANES:
        has_date = signal_date and signal_date in provider.task_dates(lane.task_id)
        suffix = "" if has_date else "（暂无）"
        options.append((lane.task_id, f"{lane.code}｜{lane.name}{suffix}"))
    return options


def _selected_task_id() -> str:
    session_value = str(st.session_state.get("beginner_task_id", "") or "")
    return session_value or _default_lane_task_id()


def _task_snapshot(task_id: str) -> DashboardTaskSnapshot | None:
    snapshots = load_runtime_snapshot()["task_snapshots"]
    for item in snapshots:
        if item.task_id == task_id:
            return item
    return None


def _task_table(task_id: str) -> pd.DataFrame:
    provider = get_provider()
    signal_date = str(load_runtime_snapshot().get("signal_date", "") or "")
    return provider.latest_signal_frame(limit=8, task_id=task_id, signal_date=signal_date)


def _render_hero() -> None:
    runtime = load_runtime_snapshot()
    signal_date = str(runtime.get("signal_date", "") or "")
    date_overview = runtime.get("date_overview")
    headline = "今天先看主链，再看盘中，再看复盘。"
    if date_overview is not None and getattr(date_overview, "focus_headline", ""):
        headline = str(getattr(date_overview, "focus_headline", "") or headline)
    sub = "现在展示的都是 AQSP 真实落盘结果，不再使用示例账户。"
    if signal_date:
        sub = f"最新结果日期：{signal_date}。先看顶部时间导航，再决定现在该看哪一块。"
    st.markdown(
        f"""
        <div class="aqsp-hero">
          <div class="aqsp-hero-title">AQSP Beginner Dashboard</div>
          <div class="aqsp-hero-main">{headline}</div>
          <div class="aqsp-hero-sub">{sub}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_overview_strip() -> None:
    runtime = load_runtime_snapshot()
    summary = runtime["summary"]
    paper_summary = runtime["paper_summary"]
    st.markdown(
        f"""
        <div class="aqsp-strip">
          <div class="aqsp-strip-card">
            <div class="aqsp-strip-label">待核对</div>
            <div class="aqsp-strip-value">{getattr(paper_summary, 'pending_entries', 0)}</div>
            <div class="aqsp-strip-meta">下一交易日开盘后优先确认这些纸面入场。</div>
          </div>
          <div class="aqsp-strip-card">
            <div class="aqsp-strip-label">纸面持有</div>
            <div class="aqsp-strip-value">{getattr(paper_summary, 'open_positions', 0)}</div>
            <div class="aqsp-strip-meta">系统正在纸面跟踪，不代表你的券商实际仓位。</div>
          </div>
          <div class="aqsp-strip-card">
            <div class="aqsp-strip-label">不可成交</div>
            <div class="aqsp-strip-value">{getattr(paper_summary, 'not_executable', 0)}</div>
            <div class="aqsp-strip-meta">通常是涨跌停或停牌，不算系统判断失败。</div>
          </div>
          <div class="aqsp-strip-card">
            <div class="aqsp-strip-label">最近有结果</div>
            <div class="aqsp-strip-value">{getattr(summary, 'latest_signal_date', '') or '-'}</div>
            <div class="aqsp-strip-meta">今天没刷新的话，先确认服务器任务是否真的跑过。</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_time_nav(selected_task_id: str) -> None:
    cards: list[str] = []
    for lane in _TIME_LANES:
        active = " active" if lane.task_id == selected_task_id else ""
        cards.append(
            f'<div class="aqsp-nav-card{active}"><div class="aqsp-nav-code">{lane.code}</div><div class="aqsp-nav-name">{lane.name}</div></div>'
        )
    st.markdown(
        '<div class="aqsp-nav-grid">' + "".join(cards) + "</div>",
        unsafe_allow_html=True,
    )
    options = lane_options()
    option_ids = [task_id for task_id, _ in options]
    labels = {task_id: label for task_id, label in options}
    default_index = option_ids.index(selected_task_id) if selected_task_id in option_ids else 0
    chosen = st.selectbox(
        "顶部导航",
        option_ids,
        index=default_index,
        format_func=lambda task_id: labels.get(task_id, task_id),
        label_visibility="collapsed",
    )
    st.session_state["beginner_task_id"] = chosen


def _render_task_focus(task_id: str) -> None:
    snapshot = _task_snapshot(task_id)
    lane = next((item for item in _TIME_LANES if item.task_id == task_id), None)
    title = lane.name if lane is not None else task_id
    summary = lane.summary if lane is not None else "先看最新任务结果。"
    status_label = snapshot.status_label if snapshot is not None else "暂无结果"
    headline = snapshot.headline if snapshot is not None else "当前没有真实落盘内容。"
    st.markdown(
        f"""
        <div class="aqsp-panel">
          <div class="aqsp-panel-title">{title}</div>
          <div class="aqsp-panel-sub">{summary}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    col1, col2, col3 = st.columns(3)
    col1.metric("当前状态", status_label)
    col2.metric("待复核", getattr(snapshot, "actionable_count", 0) if snapshot else 0)
    col3.metric(
        "观察 / 阻塞",
        f"{getattr(snapshot, 'watch_count', 0) if snapshot else 0} / "
        f"{getattr(snapshot, 'blocked_count', 0) if snapshot else 0}",
    )
    st.info(headline)
    table = _task_table(task_id)
    if isinstance(table, pd.DataFrame) and not table.empty:
        renamed = table.rename(
            columns={
                "主链复核": "现在怎么看",
                "候选状态": "阶段",
                "阻塞原因": "卡点",
            }
        )
        st.dataframe(renamed, use_container_width=True, hide_index=True)
    else:
        st.caption("这个时间点还没有独立结果，说明今天这条任务还没产出或不需要单独看。")


def _render_positions(positions: list[BeginnerPosition]) -> None:
    st.markdown(
        '<div class="aqsp-panel"><div class="aqsp-panel-title">纸面持有提醒</div>'
        '<div class="aqsp-panel-sub">这里只显示真实 paper ledger 里的纸面持有，不代表券商实际仓位。</div></div>',
        unsafe_allow_html=True,
    )
    if not positions:
        st.info("当前没有纸面持有记录。新手可以把重点放在左侧候选和阻塞原因。")
        return
    for position in positions:
        with st.expander(f"{position.symbol} {position.name}", expanded=False):
            col1, col2, col3 = st.columns(3)
            col1.metric("纸面入场价", f"¥{position.entry_price:.2f}")
            col2.metric(
                "止损线",
                f"¥{position.stop_loss:.2f}" if position.stop_loss is not None else "未记录",
            )
            col3.metric(
                "止盈线",
                f"¥{position.take_profit:.2f}" if position.take_profit is not None else "未记录",
            )
            hints: list[str] = []
            if position.entry_date:
                hints.append(f"纸面入场日：{position.entry_date}")
            if position.horizon_days is not None:
                hints.append(f"计划持有周期：{position.horizon_days} 天")
            hints.append("这里是纸面跟踪，不是自动交易，也不是你的券商实际仓位。")
            for hint in hints:
                st.markdown(f"- {hint}")


def _render_paper_summary() -> None:
    summary = load_runtime_snapshot()["paper_summary"]
    st.markdown(
        '<div class="aqsp-panel"><div class="aqsp-panel-title">今天的纸面现实</div>'
        '<div class="aqsp-panel-sub">这部分最适合新手理解“系统现在到底卡在哪”。</div></div>',
        unsafe_allow_html=True,
    )
    for line in getattr(summary, "action_summary_lines", ()):
        st.markdown(f"- {line}")
    for line in getattr(summary, "event_lines", ()):
        st.caption(line)


def _render_history() -> None:
    timeline = load_runtime_snapshot()["timeline"]
    st.markdown(
        '<div class="aqsp-panel"><div class="aqsp-panel-title">最近几天回看</div>'
        '<div class="aqsp-panel-sub">想复盘时，从这里找日期，不需要翻日志。</div></div>',
        unsafe_allow_html=True,
    )
    if isinstance(timeline, pd.DataFrame) and not timeline.empty:
        st.dataframe(timeline, use_container_width=True, hide_index=True)
    else:
        st.caption("还没有足够的历史看板结果。")


def _render_beginner_tips() -> None:
    st.markdown(
        '<div class="aqsp-panel"><div class="aqsp-panel-title">新手只记住这 4 条</div>'
        '<div class="aqsp-panel-sub">你不需要理解全部策略，只需要先避免明显错误。</div></div>',
        unsafe_allow_html=True,
    )
    tips = (
        "先看卡点再看分数。卡点没解除，再高分也先别冲动。",
        "盘中观察只是观察，不会进入正式收盘结果。",
        "纸面持有只是系统跟踪，不代表你的券商真的买了。",
        "不可成交样本不是判断失败，通常是涨跌停或停牌导致买不到。",
    )
    for tip in tips:
        st.markdown(f"- {tip}")


def _render_footer() -> None:
    st.caption("免责声明：本看板仅供研究参考，不构成投资建议。所有下单决定都应由你自己确认。")


def main() -> None:
    _inject_beginner_styles()
    positions = build_positions()
    selected_task_id = _selected_task_id()

    _render_hero()
    _render_overview_strip()
    _render_time_nav(selected_task_id)
    selected_task_id = _selected_task_id()

    left_col, right_col = st.columns((1.35, 1.0), gap="large")
    with left_col:
        _render_task_focus(selected_task_id)
        _render_paper_summary()
        _render_history()
    with right_col:
        _render_positions(positions)
        _render_beginner_tips()

    _render_footer()


if __name__ == "__main__":
    main()
