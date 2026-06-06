"""Streamlit 仪表盘 - 顶部任务导航 + 历史回看。"""

from __future__ import annotations

import streamlit as st

from aqsp.core.time import now_shanghai
from aqsp.web.data_provider import (
    DashboardCandidateCard,
    DashboardDataProvider,
    DashboardPaperSummary,
    DashboardTaskSnapshot,
)


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


def _render_summary_cards(task_view) -> None:
    overview_col, market_col = st.columns(2)
    with overview_col:
        _render_line_block(
            "报告摘要",
            task_view.report_summary_lines,
            "当前任务暂无结构化报告摘要。",
        )
    with market_col:
        if task_view.market_environment:
            st.subheader("市场态势")
            st.success(task_view.market_environment)
        else:
            st.subheader("市场态势")
            st.info("当前任务暂无结构化市场态势。")

        if task_view.runtime_lines:
            st.markdown("\n".join(f"- {line}" for line in task_view.runtime_lines))


def _render_focus_block(task_view) -> None:
    if (
        not task_view.next_day_focus_lines
        and not task_view.recommendation_lines
        and not task_view.watchlist_lines
    ):
        return

    st.divider()
    focus_col, nav_col = st.columns(2)
    with focus_col:
        _render_line_block(
            "明日重点",
            task_view.next_day_focus_lines,
            "当前任务暂无结构化明日重点。",
        )
    with nav_col:
        _render_line_block(
            "优先顺位",
            task_view.ranking_lines,
            "当前日期暂无优先顺位说明。",
        )


def _render_task_workbench(snapshots: tuple[DashboardTaskSnapshot, ...]) -> None:
    st.subheader("定时任务工作台")
    if not snapshots:
        st.info("当前暂无任务快照。")
        return

    columns = st.columns(len(snapshots))
    for column, snapshot in zip(columns, snapshots):
        with column:
            st.markdown(
                "\n".join(
                    [
                        f"### {snapshot.task_label}",
                        f"- 日期: {snapshot.latest_date or '-'}",
                        f"- 状态: {snapshot.status_label}",
                        f"- 可执行: {snapshot.actionable_count}",
                        f"- 观察: {snapshot.watch_count}",
                        f"- 阻塞: {snapshot.blocked_count}",
                        f"- 摘要: {snapshot.headline}",
                    ]
                )
            )


def _render_paper_summary(summary: DashboardPaperSummary) -> None:
    st.subheader("虚拟盘状态")

    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric("持仓中", summary.open_positions)
    metric_col2.metric("待开仓", summary.pending_entries)
    metric_col3.metric("不可成交", summary.not_executable)
    metric_col4.metric("已平仓", summary.closed_trades)

    detail_col1, detail_col2 = st.columns(2)
    with detail_col1:
        _render_line_block(
            "持仓跟踪",
            summary.open_position_lines,
            "当前暂无持仓。",
        )
    with detail_col2:
        _render_line_block(
            "关键事件",
            summary.event_lines,
            "当前暂无关键事件。",
        )


def _task_nav_label(
    task_id: str,
    snapshots: tuple[DashboardTaskSnapshot, ...],
) -> str:
    snapshot_map = {snapshot.task_id: snapshot for snapshot in snapshots}
    snapshot = snapshot_map.get(task_id)
    if snapshot is None:
        return task_id
    return f"{snapshot.task_label} · {snapshot.status_label}"


def _render_top_navigation(
    *,
    options: tuple,
    snapshots: tuple[DashboardTaskSnapshot, ...],
    provider: DashboardDataProvider,
) -> tuple[str, str]:
    task_ids = [option.task_id for option in options]
    selected_task_id = st.radio(
        "任务导航",
        task_ids,
        horizontal=True,
        format_func=lambda task_id: _task_nav_label(task_id, snapshots),
    )

    available_dates = provider.task_dates(selected_task_id)
    recent_dates = list(available_dates[:7])
    date_choices = ["最新", *recent_dates]

    selected_date_label = st.radio(
        "快速回看",
        date_choices,
        horizontal=True,
    )
    selected_date = "" if selected_date_label == "最新" else selected_date_label

    if len(available_dates) > 7:
        selected_date = st.selectbox(
            "更多日期",
            ["最新", *available_dates],
            index=0,
        )
        selected_date = "" if selected_date == "最新" else selected_date

    return selected_task_id, selected_date


def _render_candidate_cards(cards: tuple[DashboardCandidateCard, ...]) -> None:
    st.subheader("候选解读")
    if not cards:
        st.info("当前任务/日期暂无候选解读。")
        return

    for index in range(0, len(cards), 2):
        columns = st.columns(2)
        for column, card in zip(columns, cards[index : index + 2]):
            with column:
                st.markdown(
                    "\n".join(
                        [
                            f"### {card.display_name}",
                            f"- 评分: `{card.score:.1f}`",
                            f"- 主链动作: {card.action_label}",
                            f"- 候选状态: {card.status_label}",
                            (
                                f"- 复核节奏: {card.review_meta}"
                                if card.review_meta
                                else "- 复核节奏: -"
                            ),
                            (
                                f"- 下一步: {card.next_step}"
                                if card.next_step
                                else "- 下一步: -"
                            ),
                            (
                                f"- 阻塞原因: {card.blocker}"
                                if card.blocker
                                else "- 阻塞原因: -"
                            ),
                            (
                                "- 命中策略: " + "、".join(card.strategies)
                                if card.strategies
                                else "- 命中策略: -"
                            ),
                            (
                                "- 推荐理由: " + "；".join(card.reasons[:3])
                                if card.reasons
                                else "- 推荐理由: -"
                            ),
                            (
                                "- 风险提示: " + "；".join(card.risks[:3])
                                if card.risks
                                else "- 风险提示: -"
                            ),
                        ]
                    )
                )


def _render_review_sections(
    *,
    market_environment: str,
    strategy_breakdown_lines: tuple[str, ...],
    lesson_lines: tuple[str, ...],
    improvement_lines: tuple[str, ...],
) -> None:
    if not any(
        [market_environment, strategy_breakdown_lines, lesson_lines, improvement_lines]
    ):
        return

    st.divider()
    st.subheader("复盘总结")

    top_left, top_right = st.columns(2)
    with top_left:
        st.markdown(
            f"**市场环境**: {market_environment or '暂无'}"
        )
        _render_line_block(
            "关键教训",
            lesson_lines,
            "当前复盘暂无关键教训。",
        )
    with top_right:
        _render_line_block(
            "改进建议",
            improvement_lines,
            "当前复盘暂无改进建议。",
        )

    _render_line_block(
        "策略拆解",
        strategy_breakdown_lines,
        "当前复盘暂无策略拆解。",
    )


def main() -> None:
    provider = get_provider()
    summary = provider.summarize()
    task_snapshots = provider.task_snapshots()
    paper_summary = provider.paper_summary()
    updated_at = now_shanghai().strftime("%Y-%m-%d %H:%M:%S %z")

    st.title("A股量化主链看板")
    st.caption(f"更新时间: {updated_at}")
    st.warning(
        "本页只展示真实落盘的推荐、观察、复盘和虚拟盘结果；"
        "没有独立落盘的任务会显示空态或回退到对应报告。"
    )

    options = provider.task_options()
    selected_task_id, selected_date = _render_top_navigation(
        options=options,
        snapshots=task_snapshots,
        provider=provider,
    )

    task_view = provider.build_task_view(
        selected_task_id,
        signal_date=selected_date,
    )

    st.markdown(
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
    _render_task_workbench(task_snapshots)

    st.divider()
    _render_paper_summary(paper_summary)

    decision_tab, review_tab, execution_tab, report_tab = st.tabs(
        ["决策首页", "候选复盘", "虚拟盘执行", "原始报告"]
    )

    with decision_tab:
        st.subheader("执行摘要")
        st.info(task_view.headline)
        if task_view.summary_lines:
            st.markdown("\n".join(f"- {line}" for line in task_view.summary_lines))

        _render_summary_cards(task_view)
        _render_focus_block(task_view)

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

    with review_tab:
        _render_candidate_cards(task_view.detail_cards)
        _render_review_sections(
            market_environment=task_view.market_environment,
            strategy_breakdown_lines=task_view.strategy_breakdown_lines,
            lesson_lines=task_view.lesson_lines,
            improvement_lines=task_view.improvement_lines,
        )
        st.divider()
        _render_frame(
            "任务明细",
            provider.latest_signal_frame(
                limit=30,
                task_id=selected_task_id if selected_task_id != "briefing" else "main_chain",
                signal_date=task_view.selected_date,
            ),
        )

    with execution_tab:
        data_col, paper_col = st.columns(2)
        with data_col:
            _render_frame("当前虚拟持仓", provider.open_positions_frame())
        with paper_col:
            _render_frame(
                "虚拟盘事件",
                provider.paper_events_frame(limit=30, signal_date=task_view.selected_date),
            )

        st.divider()
        _render_frame("最近执行日志", provider.recent_execution_frame(limit=30))

    with report_tab:
        if task_view.report_markdown.strip():
            st.markdown(task_view.report_markdown)
        else:
            st.info("当前任务/日期暂无原始报告。")


if __name__ == "__main__":
    main()
