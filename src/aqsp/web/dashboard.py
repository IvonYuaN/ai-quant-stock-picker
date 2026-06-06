"""Streamlit 仪表盘 - 只展示主链已落盘的真实运行数据。"""

from __future__ import annotations

import streamlit as st

from aqsp.core.time import now_shanghai
from aqsp.web.data_provider import DashboardDataProvider


st.set_page_config(
    page_title="A股量化主链看板",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def get_provider() -> DashboardDataProvider:
    return DashboardDataProvider()


def _render_source_status(source_status: dict[str, str]) -> None:
    st.subheader("数据源状态")
    if not source_status:
        st.info("暂无最近一次运行的数据源状态。先跑主链后再查看。")
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


def main() -> None:
    provider = get_provider()
    summary = provider.summarize()
    updated_at = now_shanghai().strftime("%Y-%m-%d %H:%M:%S %z")

    st.title("A股量化主链看板")
    st.caption(f"更新时间: {updated_at}")
    st.warning(
        "本页只展示已落盘的主链数据，不连接券商、不推断真实账户资产；"
        "如果没有 ledger / paper / 日志文件，就会显示空态。"
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("信号总数", summary.signal_count)
    col2.metric("最新信号日", summary.latest_signal_date or "-")
    col3.metric("虚拟持仓", summary.open_positions)
    col4.metric("执行日志(7天)", summary.execution_logs)

    col5, col6, col7 = st.columns(3)
    col5.metric("等待入场", summary.pending_entries)
    col6.metric("不可成交", summary.not_executable)
    col7.metric("已平仓", summary.closed_trades)

    st.divider()
    _render_source_status(provider.latest_source_status())

    st.divider()
    _render_frame("最新信号", provider.latest_signal_frame(limit=30))

    st.divider()
    _render_frame("当前虚拟持仓", provider.open_positions_frame())

    st.divider()
    _render_frame("虚拟盘事件", provider.paper_events_frame(limit=30))

    st.divider()
    _render_frame("最近执行日志", provider.recent_execution_frame(limit=30))


if __name__ == "__main__":
    main()
