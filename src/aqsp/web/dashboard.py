"""Streamlit 仪表盘 - 顶部任务导航 + 历史回看。"""

from __future__ import annotations

import streamlit as st

from aqsp.core.time import now_shanghai
from aqsp.web.data_provider import DashboardDataProvider


st.set_page_config(
    page_title="A股量化主链看板",
    layout="wide",
    initial_sidebar_state="collapsed",
)


@st.cache_resource
def get_provider() -> DashboardDataProvider:
    return DashboardDataProvider()


def _render_source_status(source_status: dict[str, str]) -> None:
    st.subheader("数据源状态")
    if not source_status:
        st.info("当前任务/日期暂无对应数据源状态。")
        return

    requested = source_status.get("requested_source", "") or "-"
    actual = source_status.get("actual_source", "") or "-"
    health_label = source_status.get("health_label", "") or "-"
    health_message = source_status.get("health_message", "") or "无"
    latest_trade_date = source_status.get("data_latest_trade_date", "") or "-"
    lag_days = source_status.get("lag_days", "") or "-"

    st.markdown(
        "\n".join(
            [
                f"- 请求源: `{requested}`",
                f"- 实际源: `{actual}`",
                f"- 健康度: `{health_label}`",
                f"- 最新交易日: `{latest_trade_date}`",
                f"- 数据滞后: `{lag_days}` 天",
                f"- 说明: {health_message}",
            ]
        )
    )


def _render_frame(title: str, frame) -> None:
    st.subheader(title)
    if frame.empty:
        st.info("暂无数据。")
        return
    st.dataframe(frame, use_container_width=True, hide_index=True)


def _render_line_block(title: str, lines: tuple[str, ...], empty_text: str) -> None:
    st.subheader(title)
    if not lines:
        st.info(empty_text)
        return
    st.markdown("\n".join(f"- {line}" for line in lines))


def main() -> None:
    provider = get_provider()
    summary = provider.summarize()
    updated_at = now_shanghai().strftime("%Y-%m-%d %H:%M:%S %z")

    st.title("A股量化主链看板")
    st.caption(f"更新时间: {updated_at}")
    st.warning(
        "本页只展示真实落盘的推荐、观察、复盘和虚拟盘结果；"
        "没有独立落盘的任务会显示空态或回退到对应报告。"
    )

    options = provider.task_options()
    task_labels = {option.label: option.task_id for option in options}

    nav_col1, nav_col2, nav_col3 = st.columns([2.2, 2.2, 3.6])
    selected_task_label = nav_col1.selectbox(
        "任务导航",
        list(task_labels.keys()),
        index=0,
    )
    selected_task_id = task_labels[selected_task_label]

    available_dates = provider.task_dates(selected_task_id)
    selected_date = nav_col2.selectbox(
        "回看日期",
        list(available_dates) if available_dates else ["最新"],
        index=0,
    )
    selected_date = "" if selected_date == "最新" else selected_date

    task_view = provider.build_task_view(
        selected_task_id,
        signal_date=selected_date,
    )

    nav_col3.markdown(
        "\n".join(
            [
                f"**当前视图**: {task_view.task_label}",
                f"**日期**: {task_view.selected_date or '最新'}",
                f"**摘要**: {task_view.headline}",
            ]
        )
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("信号总数", summary.signal_count)
    col2.metric("最新信号日", summary.latest_signal_date or "-")
    col3.metric("虚拟持仓", summary.open_positions)
    col4.metric("执行日志(7天)", summary.execution_logs)

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("当前任务候选", task_view.candidate_count)
    col6.metric("可执行", task_view.actionable_count)
    col7.metric("观察池", task_view.watch_count)
    col8.metric("阻塞项", task_view.blocked_count)

    st.divider()
    st.subheader("执行摘要")
    st.info(task_view.headline)
    if task_view.summary_lines:
        st.markdown("\n".join(f"- {line}" for line in task_view.summary_lines))

    rec_col, watch_col = st.columns(2)
    with rec_col:
        _render_line_block(
            "今日推荐",
            task_view.recommendation_lines,
            "当前日期暂无可执行推荐。",
        )
    with watch_col:
        _render_line_block(
            "观察池",
            task_view.watchlist_lines,
            "当前日期暂无观察候选。",
        )

    blocker_col, review_col = st.columns(2)
    with blocker_col:
        _render_line_block(
            "阻塞原因",
            task_view.blocker_lines,
            "当前日期暂无明显阻塞项。",
        )
    with review_col:
        _render_line_block(
            "复核动作",
            task_view.review_lines,
            "当前日期暂无额外复核动作。",
        )

    st.divider()
    _render_source_status(task_view.source_status)

    st.divider()
    _render_frame(
        "任务明细",
        provider.latest_signal_frame(
            limit=30,
            task_id=selected_task_id if selected_task_id != "briefing" else "main_chain",
            signal_date=task_view.selected_date,
        ),
    )

    st.divider()
    _render_frame("当前虚拟持仓", provider.open_positions_frame())

    st.divider()
    _render_frame(
        "虚拟盘事件",
        provider.paper_events_frame(limit=30, signal_date=task_view.selected_date),
    )

    st.divider()
    _render_frame("最近执行日志", provider.recent_execution_frame(limit=30))

    if task_view.report_markdown.strip():
        st.divider()
        with st.expander("查看原始报告/简报", expanded=False):
            st.markdown(task_view.report_markdown)


if __name__ == "__main__":
    main()
