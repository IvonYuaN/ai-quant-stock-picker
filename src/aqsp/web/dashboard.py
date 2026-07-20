"""Streamlit 仪表盘 - 顶部任务导航 + 历史回看。"""

from __future__ import annotations

from datetime import datetime
import os
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path

import streamlit as st

from aqsp.config import load_debate_runtime_config
from aqsp.core.time import now_shanghai
from aqsp.data.source_readiness import source_supports_workload, workload_fit_for_source
from aqsp.goal_switches import GoalSwitchMatrix, load_goal_switches
from aqsp.presentation import normalize_research_tone
from aqsp.research.summary import (
    ResearchSummary,
    research_findings_display,
    research_findings_metric,
)
from aqsp.web.archive_safety import sanitize_archive_text
from aqsp.web.archive_safety import sanitize_research_text
from aqsp.web.data_provider import (
    DashboardCandidateCard,
    DashboardCandidateJourneyStep,
    DashboardCandidateSpotlight,
    DashboardDebateAgentView,
    DashboardDebateConclusion,
    DashboardDebateSummary,
    DashboardDateOverview,
    DashboardDataProvider,
    DashboardHomeStatus,
    DashboardHomeDigestPayload,
    DashboardPaperSummary,
    DashboardSameDayTaskRow,
    DashboardTimelineRow,
    DashboardTaskSnapshot,
    MISSING_BLOCKER_TEXT,
    build_debate_conclusion,
    debate_summary_chain_line,
    debate_summary_cross_market_line,
    debate_summary_evidence_line,
    debate_summary_signal_value_tier,
)
from aqsp.web.home_snapshot import (
    HomeDashboardSnapshot,
    HomeSnapshotCandidate,
    HomeSnapshotIndex,
    is_home_recommendation,
    load_home_dashboard_snapshot,
    load_home_snapshot_index,
)


@dataclass(frozen=True)
class _SameDayDigestEvent:
    event_type: str
    line: str
    time_key: str = ""
    type_priority: int = 0
    content_priority: tuple[int, ...] = ()


st.set_page_config(
    page_title="AQSP 日期任务研究台",
    layout="wide",
    initial_sidebar_state="collapsed",
)

_RUNTIME_DEBATE_ROLE_LABELS = {
    "bull": "技术多头",
    "bear": "基本面空头",
    "risk_control": "风控",
    "sector_leader": "板块轮动",
    "cross_market": "跨市传导",
    "policy_sensitive": "政策敏感",
    "margin_trading": "融资融券",
    "northbound": "北向资金",
    "retail_mood": "散户情绪",
}

_GOAL_TRACK_PRIORITY_LABELS = {
    "p0": "P0",
    "p1": "P1",
    "p2": "P2",
    "p3": "P3",
}

_DEFAULT_HOME_SNAPSHOT_PATH = "data/runtime/home_dashboard_snapshot.json"


def _runtime_role_labels(role_names: tuple[str, ...], *, limit: int = 3) -> str:
    labels = tuple(
        _RUNTIME_DEBATE_ROLE_LABELS.get(role, role)
        for role in role_names
        if str(role).strip()
    )
    if not labels:
        return ""
    preview = "、".join(labels[:limit])
    if len(labels) > limit:
        return f"{preview} 等"
    return preview


def _task_view_signal_date(task_view) -> str:
    return str(
        getattr(task_view, "selected_date", "") or getattr(task_view, "latest_date", "")
    ).strip()


def _candidate_card_source_key(card: DashboardCandidateCard | None) -> str:
    if card is None:
        return ""
    if card.rank_label == "同日联动":
        return "spotlight"
    if card.rank_label == "辩论主结论":
        return "debate"
    return "card"


@dataclass(frozen=True)
class _HomeActionRailItem:
    lane_id: str
    lane_label: str
    tone: str
    button_label: str
    target_workspace: str
    card: DashboardCandidateCard | None
    summary: str
    lines: tuple[str, ...]
    signal_date: str = ""
    task_id: str = ""
    task_label: str = ""
    focus_kind: str = ""
    debate_id: str = ""
    decision_source: str = ""
    visible: bool = True


@dataclass(frozen=True)
class _ResearchRadarCard:
    title: str
    metrics: tuple[tuple[str, str], ...]
    lines: tuple[str, ...]
    prereq_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class _HomeBriefCard:
    kicker: str
    title: str
    lines: tuple[str, ...]
    tone: str = "archive"


@dataclass(frozen=True)
class _DebateBriefCard:
    kicker: str
    title: str
    lines: tuple[str, ...]
    tone: str = "archive"


@dataclass(frozen=True)
class _ArchiveBriefCard:
    kicker: str
    title: str
    lines: tuple[str, ...]
    tone: str = "archive"


@dataclass(frozen=True)
class _ResearchPathStep:
    icon: str
    title: str
    headline: str
    lines: tuple[str, ...]
    tone: str = "archive"


@dataclass(frozen=True)
class _WorkspaceNavItem:
    code: str
    name: str


@dataclass(frozen=True)
class _WorkspaceHandoff:
    target_workspace: str
    source_workspace: str
    title: str
    lines: tuple[str, ...]
    symbol: str = ""
    signal_date: str = ""
    task_id: str = ""
    task_label: str = ""
    focus_kind: str = ""
    debate_id: str = ""
    decision_source: str = ""


@dataclass(frozen=True)
class _TwoLineNavLabel:
    code: str
    name: str


@dataclass(frozen=True)
class _TaskDateResolution:
    task_id: str
    reason: str = ""


def _inject_dashboard_styles() -> None:
    st.markdown(
        """
        <style>
        #MainMenu,
        footer,
        header,
        [data-testid="stToolbar"],
        [data-testid="stHeader"],
        [data-testid="stActionButton"],
        [data-testid="baseButton-header"],
        [data-testid="stMainMenu"],
        [data-testid="stDecoration"],
        [data-testid="stStatusWidget"],
        [data-testid="manage-app-button"],
        .stDeployButton {
            display: none !important;
            visibility: hidden !important;
            pointer-events: none !important;
        }
        .block-container {
            padding-top: 1rem;
            padding-bottom: 1.4rem;
        }
        div[data-testid="stCaptionContainer"] p {
            margin-bottom: 0.18rem;
        }
        .aqsp-banner {
            position: relative;
            overflow: hidden;
            padding: 0.8rem 0.95rem;
            border-radius: 16px;
            background:
                radial-gradient(circle at top right, rgba(43, 138, 194, 0.18), transparent 28%),
                linear-gradient(135deg, #f7f6ef 0%, #eef4fb 48%, #eef7f3 100%);
            border: 1px solid rgba(26, 71, 102, 0.14);
            box-shadow: 0 12px 28px rgba(20, 45, 66, 0.08);
            margin: 0.05rem 0 0.7rem 0;
        }
        .aqsp-banner::after {
            content: "";
            position: absolute;
            inset: 0;
            background-image:
                linear-gradient(rgba(24, 65, 97, 0.05) 1px, transparent 1px),
                linear-gradient(90deg, rgba(24, 65, 97, 0.05) 1px, transparent 1px);
            background-size: 18px 18px;
            mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.45), transparent 85%);
            pointer-events: none;
        }
        .aqsp-banner-title {
            position: relative;
            font-size: 0.82rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #6a7682;
            margin-bottom: 0.35rem;
        }
        .aqsp-banner-main {
            position: relative;
            font-size: 1.04rem;
            font-weight: 700;
            color: #163247;
            margin-bottom: 0.35rem;
        }
        .aqsp-banner-meta {
            position: relative;
            font-size: 0.89rem;
            color: #3f5364;
            line-height: 1.5;
        }
        .aqsp-overview-strip {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.9rem;
            margin: 0.05rem 0 0.85rem 0;
        }
        .aqsp-overview-item {
            padding: 0.9rem 1rem;
            border-radius: 16px;
            border: 1px solid rgba(26, 71, 102, 0.12);
            background: linear-gradient(180deg, #fffdf8 0%, #f3f7fb 100%);
            box-shadow: 0 10px 24px rgba(33, 46, 56, 0.05);
        }
        .aqsp-overview-label {
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #6a7682;
            margin-bottom: 0.35rem;
        }
        .aqsp-overview-value {
            font-size: 2rem;
            line-height: 1;
            font-weight: 700;
            color: #163247;
            margin-bottom: 0.3rem;
        }
        .aqsp-overview-meta {
            font-size: 0.82rem;
            color: #4a5e6f;
            line-height: 1.45;
        }
        .aqsp-task-card {
            min-height: 188px;
            padding: 0.95rem 1rem;
            border-radius: 16px;
            border: 1px solid rgba(26, 71, 102, 0.12);
            background: #fbfbf9;
            box-shadow: 0 10px 24px rgba(33, 46, 56, 0.05);
        }
        .aqsp-task-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 0.5rem;
            margin-bottom: 0.6rem;
        }
        .aqsp-task-title {
            font-size: 1rem;
            font-weight: 700;
            color: #163247;
        }
        .aqsp-task-date {
            font-size: 0.82rem;
            color: #6a7682;
            margin-top: 0.18rem;
        }
        .aqsp-task-status {
            display: inline-block;
            padding: 0.18rem 0.52rem;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 700;
            color: #284b63;
            background: #dcecf7;
            white-space: nowrap;
        }
        .aqsp-task-metrics {
            display: flex;
            gap: 0.7rem;
            margin-bottom: 0.6rem;
            flex-wrap: wrap;
        }
        .aqsp-task-metric {
            font-size: 0.82rem;
            color: #4a5e6f;
        }
        .aqsp-task-summary {
            font-size: 0.88rem;
            color: #3b4a58;
            line-height: 1.55;
        }
        .aqsp-runtime-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 0.65rem;
            margin: 0.3rem 0 0.85rem 0;
        }
        .aqsp-runtime-card {
            padding: 0.75rem 0.85rem;
            border-radius: 14px;
            border: 1px solid rgba(26, 71, 102, 0.11);
            background: #fbfbf8;
            box-shadow: 0 8px 20px rgba(33, 46, 56, 0.045);
        }
        .aqsp-runtime-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.45rem;
            margin-bottom: 0.38rem;
        }
        .aqsp-runtime-title {
            font-size: 0.9rem;
            font-weight: 750;
            color: #163247;
        }
        .aqsp-runtime-status {
            padding: 0.12rem 0.46rem;
            border-radius: 999px;
            background: #e7f0e8;
            color: #2e5d3f;
            font-size: 0.72rem;
            font-weight: 750;
            white-space: nowrap;
        }
        .aqsp-runtime-status.risk {
            background: #fff0d6;
            color: #8a5a00;
        }
        .aqsp-runtime-status.error {
            background: #ffe0df;
            color: #9b2d25;
        }
        .aqsp-runtime-status.skip {
            background: #e8edf2;
            color: #526371;
        }
        .aqsp-runtime-line {
            font-size: 0.8rem;
            color: #4a5e6f;
            line-height: 1.42;
        }
        .aqsp-nav-note {
            font-size: 0.84rem;
            color: #667785;
            margin: -0.12rem 0 0.4rem 0;
        }
        .aqsp-workspace-shell {
            margin: 0.25rem 0 0.65rem 0;
            padding: 0.6rem 0.8rem 0.15rem 0.8rem;
            border-radius: 16px;
            border: 1px solid rgba(26, 71, 102, 0.11);
            background: linear-gradient(180deg, rgba(251, 252, 255, 0.94) 0%, rgba(244, 248, 251, 0.92) 100%);
            box-shadow: 0 8px 18px rgba(33, 46, 56, 0.04);
        }
        .aqsp-workspace-label {
            margin-bottom: 0.35rem;
            font-size: 0.74rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: #5e7081;
        }
        .aqsp-workspace-card {
            margin-top: 0.25rem;
            text-align: center;
            font-size: 0.78rem;
            line-height: 1.32;
            color: #516272;
            min-height: 2.4rem;
        }
        .aqsp-workspace-card.active {
            color: #163247;
            font-weight: 700;
        }
        .aqsp-workspace-name {
            margin-top: 0.12rem;
        }
        .aqsp-nav-section-title {
            margin: 0.35rem 0 0.45rem 0;
            font-size: 0.74rem;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            color: #5e7081;
        }
        .aqsp-quick-symbol-name {
            margin-top: 0.2rem;
            text-align: center;
            font-size: 0.8rem;
            line-height: 1.25;
            color: #516272;
            min-height: 1.05rem;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .aqsp-quick-symbol-name.active {
            color: #163247;
            font-weight: 700;
        }
        .aqsp-nav-secondary {
            margin-top: 0.22rem;
            text-align: center;
            font-size: 0.79rem;
            line-height: 1.25;
            color: #586b7c;
            min-height: 2rem;
        }
        .aqsp-nav-secondary.active {
            color: #163247;
            font-weight: 700;
        }
        .aqsp-review-symbol {
            margin-bottom: 0.42rem;
            font-size: 1.12rem;
            font-weight: 700;
            color: #163247;
            line-height: 1.35;
        }
        .aqsp-date-card {
            min-height: 154px;
            padding: 0.9rem 1rem;
            border-radius: 16px;
            border: 1px solid rgba(26, 71, 102, 0.12);
            background: linear-gradient(180deg, #fffdf6 0%, #f4f7fa 100%);
            box-shadow: 0 10px 24px rgba(33, 46, 56, 0.05);
        }
        .aqsp-date-card.active {
            border-color: rgba(25, 92, 138, 0.38);
            box-shadow: 0 14px 30px rgba(23, 69, 101, 0.12);
        }
        .aqsp-date-title {
            font-size: 1rem;
            font-weight: 700;
            color: #163247;
            margin-bottom: 0.4rem;
        }
        .aqsp-date-meta {
            font-size: 0.8rem;
            color: #6a7682;
            margin-bottom: 0.45rem;
        }
        .aqsp-date-summary {
            font-size: 0.86rem;
            color: #3b4a58;
            line-height: 1.5;
        }
        .aqsp-day-card {
            min-height: 176px;
            padding: 0.95rem 1rem;
            border-radius: 16px;
            border: 1px solid rgba(26, 71, 102, 0.12);
            background: #fbfbf9;
            box-shadow: 0 10px 24px rgba(33, 46, 56, 0.05);
        }
        .aqsp-day-card.active {
            border-color: rgba(25, 92, 138, 0.38);
            background: linear-gradient(180deg, #fbfcff 0%, #f2f7fb 100%);
        }
        .aqsp-day-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 0.5rem;
            margin-bottom: 0.55rem;
        }
        .aqsp-day-title {
            font-size: 0.98rem;
            font-weight: 700;
            color: #163247;
        }
        .aqsp-day-status {
            display: inline-block;
            padding: 0.16rem 0.48rem;
            border-radius: 999px;
            font-size: 0.76rem;
            font-weight: 700;
            color: #284b63;
            background: #dcecf7;
            white-space: nowrap;
        }
        .aqsp-day-metrics {
            display: flex;
            gap: 0.55rem;
            flex-wrap: wrap;
            margin-bottom: 0.5rem;
        }
        .aqsp-day-metric {
            font-size: 0.81rem;
            color: #4a5e6f;
        }
        .aqsp-day-summary {
            font-size: 0.86rem;
            color: #3b4a58;
            line-height: 1.5;
        }
        .aqsp-replay-card {
            position: relative;
            overflow: hidden;
            padding: 0.98rem 1.08rem;
            border-radius: 18px;
            border: 1px solid rgba(25, 92, 138, 0.16);
            background:
                radial-gradient(circle at 8% 12%, rgba(43, 138, 194, 0.16), transparent 25%),
                radial-gradient(circle at 92% 20%, rgba(88, 150, 122, 0.12), transparent 24%),
                linear-gradient(135deg, #fffdf4 0%, #f4f8fb 52%, #eef7f3 100%);
            box-shadow: 0 14px 30px rgba(33, 46, 56, 0.07);
            margin-bottom: 0.85rem;
        }
        .aqsp-replay-kicker {
            position: relative;
            font-size: 0.76rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: #5e7081;
            margin-bottom: 0.42rem;
        }
        .aqsp-replay-title {
            position: relative;
            font-size: 1.05rem;
            font-weight: 760;
            color: #163247;
            margin-bottom: 0.46rem;
        }
        .aqsp-replay-line {
            position: relative;
            font-size: 0.9rem;
            color: #34495a;
            line-height: 1.55;
            margin-top: 0.16rem;
        }
        .aqsp-evidence-strip {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.58rem;
            margin: 0.7rem 0 0.85rem;
        }
        .aqsp-evidence-chip {
            padding: 0.72rem 0.82rem;
            border-radius: 15px;
            border: 1px solid rgba(25, 92, 138, 0.13);
            background: rgba(255, 253, 246, 0.78);
            box-shadow: 0 10px 22px rgba(33, 46, 56, 0.045);
        }
        .aqsp-evidence-title {
            font-size: 0.84rem;
            font-weight: 740;
            color: #17384f;
            margin-bottom: 0.22rem;
        }
        .aqsp-evidence-line {
            font-size: 0.8rem;
            color: #4a5e6f;
            line-height: 1.42;
        }
        .aqsp-research-path {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.68rem;
            margin: 0.72rem 0 0.95rem;
        }
        .aqsp-path-step {
            position: relative;
            overflow: hidden;
            min-height: 150px;
            padding: 0.86rem 0.92rem;
            border-radius: 18px;
            border: 1px solid rgba(25, 92, 138, 0.14);
            background: linear-gradient(145deg, #fffdf6 0%, #f5f8fb 100%);
            box-shadow: 0 12px 26px rgba(33, 46, 56, 0.052);
        }
        .aqsp-path-step.focus {
            border-color: rgba(57, 121, 92, 0.22);
            background: linear-gradient(145deg, #fffdf6 0%, #eef8f2 100%);
        }
        .aqsp-path-step.pressure {
            border-color: rgba(188, 129, 53, 0.24);
            background: linear-gradient(145deg, #fff9ee 0%, #f7f8fb 100%);
        }
        .aqsp-path-step.blocked {
            border-color: rgba(176, 90, 74, 0.24);
            background: linear-gradient(145deg, #fff7f5 0%, #f7f8fb 100%);
        }
        .aqsp-path-kicker {
            display: flex;
            gap: 0.36rem;
            align-items: center;
            font-size: 0.76rem;
            color: #657381;
            margin-bottom: 0.42rem;
        }
        .aqsp-path-title {
            font-size: 0.96rem;
            font-weight: 760;
            color: #17384f;
            line-height: 1.32;
            margin-bottom: 0.35rem;
        }
        .aqsp-path-headline {
            font-size: 0.84rem;
            font-weight: 650;
            color: #284b63;
            line-height: 1.38;
            margin-bottom: 0.3rem;
        }
        .aqsp-path-line {
            font-size: 0.8rem;
            color: #4a5e6f;
            line-height: 1.46;
            margin-top: 0.14rem;
        }
        .aqsp-ops-card {
            min-height: 174px;
            padding: 1rem 1.05rem;
            border-radius: 18px;
            border: 1px solid rgba(26, 71, 102, 0.12);
            background: linear-gradient(160deg, #fffaf0 0%, #f4f7fb 52%, #eef6f4 100%);
            box-shadow: 0 12px 26px rgba(33, 46, 56, 0.06);
        }
        .aqsp-ops-kicker {
            font-size: 0.76rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #6a7682;
            margin-bottom: 0.45rem;
        }
        .aqsp-ops-title {
            font-size: 1.04rem;
            font-weight: 700;
            color: #163247;
            margin-bottom: 0.45rem;
        }
        .aqsp-ops-summary {
            font-size: 0.86rem;
            color: #3b4a58;
            line-height: 1.58;
        }
        .aqsp-status-strip {
            padding: 0.95rem 1rem;
            border-radius: 16px;
            border: 1px solid rgba(26, 71, 102, 0.12);
            background: linear-gradient(160deg, #fffdf7 0%, #f6f9fc 55%, #eef6f4 100%);
            box-shadow: 0 10px 22px rgba(33, 46, 56, 0.05);
            margin-bottom: 0.85rem;
        }
        .aqsp-status-label {
            font-size: 0.76rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #6a7682;
            margin-bottom: 0.45rem;
        }
        .aqsp-status-main {
            font-size: 1.08rem;
            font-weight: 700;
            color: #163247;
            margin-bottom: 0.3rem;
        }
        .aqsp-status-sub {
            font-size: 0.87rem;
            color: #3b4a58;
            line-height: 1.56;
        }
        .aqsp-queue-card {
            min-height: 220px;
            padding: 1rem 1.05rem;
            border-radius: 18px;
            border: 1px solid rgba(26, 71, 102, 0.12);
            background: #fbfbf9;
            box-shadow: 0 12px 24px rgba(33, 46, 56, 0.05);
            margin-bottom: 0.85rem;
        }
        .aqsp-queue-card.recommend {
            background: linear-gradient(180deg, #fffdf6 0%, #f6fbf8 100%);
            border-color: rgba(57, 121, 92, 0.2);
        }
        .aqsp-queue-card.watch {
            background: linear-gradient(180deg, #fffefb 0%, #f6f8fb 100%);
            border-color: rgba(170, 124, 47, 0.18);
        }
        .aqsp-queue-card.blocked {
            background: linear-gradient(180deg, #fff9f8 0%, #f7f8fb 100%);
            border-color: rgba(176, 90, 74, 0.2);
        }
        .aqsp-queue-kicker {
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #6a7682;
            margin-bottom: 0.42rem;
        }
        .aqsp-queue-title {
            font-size: 1rem;
            font-weight: 700;
            color: #163247;
            margin-bottom: 0.4rem;
        }
        .aqsp-queue-summary {
            font-size: 0.85rem;
            color: #4a5e6f;
            line-height: 1.54;
            margin-bottom: 0.7rem;
        }
        .aqsp-queue-item {
            padding-top: 0.7rem;
            border-top: 1px solid rgba(26, 71, 102, 0.09);
            margin-top: 0.7rem;
        }
        .aqsp-queue-item:first-of-type {
            margin-top: 0;
            padding-top: 0;
            border-top: 0;
        }
        .aqsp-queue-head {
            display: flex;
            justify-content: space-between;
            gap: 0.65rem;
            margin-bottom: 0.35rem;
        }
        .aqsp-queue-name {
            font-size: 0.95rem;
            font-weight: 700;
            color: #163247;
        }
        .aqsp-queue-rank {
            font-size: 0.77rem;
            color: #6a7682;
            margin-top: 0.15rem;
        }
        .aqsp-queue-score {
            font-size: 0.84rem;
            font-weight: 700;
            color: #284b63;
            white-space: nowrap;
        }
        .aqsp-queue-meta {
            font-size: 0.83rem;
            color: #3f5364;
            line-height: 1.5;
        }
        .aqsp-workflow-card {
            min-height: 214px;
            padding: 0.95rem 1rem;
            border-radius: 18px;
            border: 1px solid rgba(26, 71, 102, 0.12);
            background: linear-gradient(180deg, #fffdf7 0%, #f4f8fb 100%);
            box-shadow: 0 12px 24px rgba(33, 46, 56, 0.05);
        }
        .aqsp-workflow-card.active {
            border-color: rgba(25, 92, 138, 0.38);
            box-shadow: 0 14px 30px rgba(23, 69, 101, 0.12);
            background: linear-gradient(180deg, #fbfcff 0%, #eef6fb 100%);
        }
        .aqsp-workflow-phase {
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #6a7682;
            margin-bottom: 0.4rem;
        }
        .aqsp-workflow-title {
            font-size: 1rem;
            font-weight: 700;
            color: #163247;
            margin-bottom: 0.35rem;
        }
        .aqsp-workflow-summary {
            font-size: 0.85rem;
            color: #3b4a58;
            line-height: 1.54;
            margin-top: 0.45rem;
        }
        .aqsp-cockpit-card {
            min-height: 188px;
            padding: 0.95rem 1rem;
            border-radius: 16px;
            border: 1px solid rgba(26, 71, 102, 0.12);
            background: linear-gradient(180deg, #fffdf7 0%, #f5f8fb 100%);
            box-shadow: 0 10px 22px rgba(33, 46, 56, 0.05);
            margin-bottom: 0.8rem;
        }
        .aqsp-cockpit-card.focus {
            border-color: rgba(57, 121, 92, 0.22);
            background: linear-gradient(180deg, #fffdf6 0%, #f4fbf6 100%);
        }
        .aqsp-cockpit-card.pressure {
            border-color: rgba(170, 124, 47, 0.2);
            background: linear-gradient(180deg, #fffdf8 0%, #f8fafc 100%);
        }
        .aqsp-cockpit-card.blocked {
            border-color: rgba(176, 90, 74, 0.22);
            background: linear-gradient(180deg, #fff9f8 0%, #f7f8fb 100%);
        }
        .aqsp-cockpit-card.archive {
            border-color: rgba(25, 92, 138, 0.18);
            background: linear-gradient(180deg, #fbfcff 0%, #eef5fb 100%);
        }
        .aqsp-cockpit-kicker {
            font-size: 0.74rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #6a7682;
            margin-bottom: 0.45rem;
        }
        .aqsp-cockpit-title {
            font-size: 0.98rem;
            font-weight: 700;
            color: #163247;
            margin-bottom: 0.32rem;
        }
        .aqsp-cockpit-body {
            font-size: 0.84rem;
            color: #3b4a58;
            line-height: 1.52;
        }
        .aqsp-simple-board-title {
            margin: 0.15rem 0 0.75rem 0;
            padding: 0.72rem 0.9rem;
            border-radius: 16px;
            border: 1px solid rgba(26, 71, 102, 0.12);
            background: linear-gradient(135deg, #fffdf6 0%, #f2f7fb 54%, #eef6f3 100%);
            color: #163247;
            font-size: 1.02rem;
            font-weight: 760;
        }
        .aqsp-simple-topbar {
            display: flex;
            align-items: end;
            justify-content: space-between;
            gap: 1rem;
            margin: 0.35rem 0 1.1rem;
            padding: 0.9rem 1.05rem;
            border-radius: 20px;
            background:
                linear-gradient(135deg, rgba(255, 253, 247, 0.96) 0%, rgba(240, 246, 248, 0.9) 100%);
            box-shadow: 0 14px 34px rgba(30, 51, 65, 0.07);
            border: 1px solid rgba(26, 71, 102, 0.1);
        }
        .aqsp-simple-brand {
            color: #152a3a;
            font-size: 1.28rem;
            font-weight: 850;
            letter-spacing: -0.02em;
        }
        .aqsp-simple-updated {
            color: #687986;
            font-size: 0.82rem;
            font-variant-numeric: tabular-nums;
            text-align: right;
        }
        .aqsp-simple-shell {
            margin-top: 0.25rem;
        }
        .aqsp-simple-rail {
            position: sticky;
            top: 0.9rem;
            padding: 1.05rem;
            border-radius: 22px;
            background:
                radial-gradient(circle at 12% 8%, rgba(31, 97, 141, 0.12), transparent 34%),
                linear-gradient(180deg, #fffdf7 0%, #eef5f7 100%);
            box-shadow: 0 18px 42px rgba(30, 51, 65, 0.08);
            border: 1px solid rgba(26, 71, 102, 0.12);
        }
        .aqsp-simple-date-label {
            color: #6b7884;
            font-size: 0.74rem;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            margin-bottom: 0.25rem;
        }
        .aqsp-simple-date {
            color: #163247;
            font-size: 1.22rem;
            font-weight: 820;
            font-variant-numeric: tabular-nums;
            margin-bottom: 0.25rem;
        }
        .aqsp-simple-boundary {
            color: #5c6d79;
            font-size: 0.82rem;
            line-height: 1.45;
            text-wrap: pretty;
            margin-bottom: 0.75rem;
        }
        .aqsp-simple-unlock {
            margin: 0.72rem 0 0.82rem;
            padding: 0.72rem 0.78rem;
            border-radius: 15px;
            border: 1px solid rgba(26, 71, 102, 0.1);
            background:
                radial-gradient(circle at 92% 12%, rgba(60, 129, 101, 0.14), transparent 34%),
                linear-gradient(135deg, #fffdf7 0%, #eef7f0 100%);
            box-shadow: 0 10px 22px rgba(31, 83, 62, 0.07);
        }
        .aqsp-simple-unlock.waiting {
            background:
                radial-gradient(circle at 92% 12%, rgba(176, 111, 59, 0.13), transparent 34%),
                linear-gradient(135deg, #fffdf7 0%, #f8f1e7 100%);
        }
        .aqsp-simple-unlock-title {
            color: #17384f;
            font-size: 0.9rem;
            font-weight: 820;
            margin-bottom: 0.34rem;
        }
        .aqsp-simple-unlock-line {
            color: #506575;
            font-size: 0.77rem;
            line-height: 1.45;
            text-wrap: pretty;
            margin-top: 0.16rem;
        }
        .aqsp-simple-status-card {
            display: grid;
            gap: 0.24rem;
            margin: 0.62rem 0 0.82rem;
            padding: 0.62rem 0.7rem;
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.62);
            box-shadow: inset 0 0 0 1px rgba(26, 71, 102, 0.08);
        }
        .aqsp-simple-status-line {
            color: #506575;
            font-size: 0.76rem;
            line-height: 1.36;
            font-variant-numeric: tabular-nums;
            overflow-wrap: anywhere;
        }
        .aqsp-simple-chip-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.42rem;
            margin: 0.72rem 0 0.88rem;
        }
        .aqsp-simple-chip {
            padding: 0.46rem 0.48rem;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.68);
            box-shadow: inset 0 0 0 1px rgba(26, 71, 102, 0.08);
        }
        .aqsp-simple-chip-value {
            color: #17384f;
            font-size: 1rem;
            font-weight: 780;
            font-variant-numeric: tabular-nums;
            line-height: 1.1;
        }
        .aqsp-simple-chip-label {
            color: #687986;
            font-size: 0.7rem;
            margin-top: 0.14rem;
        }
        .aqsp-simple-nav-title {
            color: #17384f;
            font-size: 0.85rem;
            font-weight: 760;
            margin: 0.65rem 0 0.45rem;
        }
        .aqsp-simple-nav-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.7rem;
            min-height: 44px;
            margin: 0.38rem 0 0.18rem;
            padding: 0.68rem 0.78rem;
            border-radius: 15px;
            background: rgba(255, 255, 255, 0.64);
            box-shadow: inset 0 0 0 1px rgba(26, 71, 102, 0.08);
        }
        .aqsp-simple-nav-item.active {
            background: #17384f;
            box-shadow: 0 12px 24px rgba(23, 56, 79, 0.16);
        }
        .aqsp-simple-nav-name {
            color: #17384f;
            font-size: 0.9rem;
            font-weight: 780;
        }
        .aqsp-simple-nav-sub {
            color: #657584;
            font-size: 0.73rem;
            line-height: 1.32;
            margin-top: 0.1rem;
        }
        .aqsp-simple-nav-item.active .aqsp-simple-nav-name,
        .aqsp-simple-nav-item.active .aqsp-simple-nav-sub {
            color: #fffdf7;
        }
        .aqsp-simple-nav-count {
            color: #17384f;
            font-size: 0.82rem;
            font-weight: 800;
            font-variant-numeric: tabular-nums;
        }
        .aqsp-simple-nav-item.active .aqsp-simple-nav-count {
            color: #fffdf7;
        }
        .aqsp-simple-date-list {
            display: grid;
            gap: 0.34rem;
            margin-top: 0.42rem;
        }
        .aqsp-simple-date-pill {
            padding: 0.5rem 0.62rem;
            border-radius: 13px;
            color: #17384f;
            background: rgba(255, 255, 255, 0.58);
            box-shadow: inset 0 0 0 1px rgba(26, 71, 102, 0.08);
            font-size: 0.8rem;
            font-weight: 740;
            font-variant-numeric: tabular-nums;
        }
        .aqsp-simple-date-pill-meta {
            color: #687986;
            font-size: 0.7rem;
            line-height: 1.25;
            margin: 0.42rem 0 -0.18rem;
            font-variant-numeric: tabular-nums;
        }
        .aqsp-simple-date-pill.active {
            color: #fffdf7;
            background: #2f6f7c;
            box-shadow: 0 10px 20px rgba(47, 111, 124, 0.15);
        }
        .aqsp-simple-nav-note {
            color: #6a7a86;
            font-size: 0.78rem;
            line-height: 1.42;
            margin-top: 0.48rem;
            text-wrap: pretty;
        }
        div[role="radiogroup"] {
            gap: 0.45rem;
        }
        div[role="radiogroup"] label {
            min-height: 42px;
            padding: 0.45rem 0.58rem;
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.62);
            box-shadow: inset 0 0 0 1px rgba(26, 71, 102, 0.09);
        }
        div[role="radiogroup"] label:has(input:checked) {
            background: #17384f;
            box-shadow: 0 10px 22px rgba(23, 56, 79, 0.16);
        }
        div[role="radiogroup"] label:has(input:checked) p {
            color: #fffdf7;
            font-weight: 760;
        }
        .aqsp-simple-panel-head {
            margin-bottom: 0.62rem;
            padding: 0.05rem 0 0.5rem;
            border-bottom: 1px solid rgba(26, 71, 102, 0.1);
        }
        .aqsp-simple-panel-kicker {
            color: #6b7884;
            font-size: 0.74rem;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            margin-bottom: 0.22rem;
        }
        .aqsp-simple-panel-title {
            color: #163247;
            font-size: 1.2rem;
            font-weight: 820;
            line-height: 1.2;
            text-wrap: balance;
        }
        .aqsp-simple-panel-note {
            color: #5d6f7f;
            font-size: 0.88rem;
            line-height: 1.5;
            margin-top: 0.34rem;
            text-wrap: pretty;
        }
        .aqsp-simple-shell .aqsp-cockpit-card {
            min-height: 0;
            padding: 0.85rem 0.95rem;
            margin-bottom: 0.7rem;
        }
        .aqsp-simple-summary-strip {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 0.8rem;
            align-items: center;
            margin: 0 0 0.7rem;
            padding: 0.7rem 0.85rem;
            border-radius: 16px;
            background: linear-gradient(135deg, #143347 0%, #1c4d5a 100%);
            box-shadow: 0 12px 28px rgba(20, 51, 71, 0.14);
        }
        .aqsp-simple-summary-title {
            color: #fffdf7;
            font-size: 0.96rem;
            font-weight: 820;
            line-height: 1.3;
        }
        .aqsp-simple-summary-line {
            color: rgba(255, 253, 247, 0.78);
            font-size: 0.78rem;
            line-height: 1.4;
            margin-top: 0.14rem;
            text-wrap: pretty;
        }
        .aqsp-simple-summary-counts {
            color: rgba(255, 253, 247, 0.86);
            font-size: 0.78rem;
            line-height: 1.45;
            text-align: right;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
        }
        .aqsp-simple-candidate-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.62rem;
            margin: 0.35rem 0 0.82rem;
        }
        .aqsp-simple-candidate-card {
            min-height: 150px;
            padding: 0.78rem 0.84rem;
            border-radius: 16px;
            border: 1px solid rgba(57, 121, 92, 0.2);
            background: linear-gradient(155deg, #fffdf6 0%, #f0f8f2 100%);
            box-shadow: 0 10px 20px rgba(33, 46, 56, 0.045);
        }
        .aqsp-simple-candidate-card.watch {
            border-color: rgba(25, 92, 138, 0.17);
            background: linear-gradient(155deg, #fffdf8 0%, #f2f7fb 100%);
        }
        .aqsp-simple-candidate-card.blocked {
            border-color: rgba(176, 90, 74, 0.2);
            background: linear-gradient(155deg, #fffaf8 0%, #f8f4f3 100%);
        }
        .aqsp-simple-candidate-top {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 0.45rem;
            margin-bottom: 0.42rem;
        }
        .aqsp-simple-candidate-name {
            color: #153246;
            font-size: 0.94rem;
            font-weight: 800;
            line-height: 1.3;
            text-wrap: balance;
        }
        .aqsp-simple-candidate-score {
            color: #17384f;
            font-size: 0.78rem;
            font-weight: 820;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
        }
        .aqsp-simple-candidate-status {
            color: #5a6d7a;
            font-size: 0.76rem;
            line-height: 1.35;
            margin-bottom: 0.34rem;
        }
        .aqsp-simple-candidate-line {
            color: #324b5c;
            font-size: 0.78rem;
            line-height: 1.4;
            margin-top: 0.16rem;
            text-wrap: pretty;
            overflow-wrap: anywhere;
        }
        @media (max-width: 980px) {
            .aqsp-simple-candidate-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        @media (max-width: 620px) {
            .aqsp-simple-summary-strip,
            .aqsp-simple-candidate-grid {
                grid-template-columns: 1fr;
            }
            .aqsp-simple-summary-counts {
                text-align: left;
            }
        }
        .aqsp-decision-hero {
            padding: 1rem 1.1rem;
            border-radius: 20px;
            background:
                radial-gradient(circle at 95% 10%, rgba(33, 91, 124, 0.14), transparent 32%),
                linear-gradient(135deg, #143347 0%, #1c4d5a 100%);
            color: #fffdf7;
            box-shadow: 0 20px 42px rgba(20, 51, 71, 0.18);
            margin-bottom: 0.8rem;
        }
        .aqsp-decision-kicker {
            font-size: 0.72rem;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            color: rgba(255, 253, 247, 0.68);
            margin-bottom: 0.35rem;
        }
        .aqsp-decision-title {
            font-size: 1.25rem;
            font-weight: 850;
            letter-spacing: -0.02em;
            margin-bottom: 0.42rem;
        }
        .aqsp-decision-line {
            font-size: 0.88rem;
            line-height: 1.5;
            color: rgba(255, 253, 247, 0.84);
            text-wrap: pretty;
        }
        .aqsp-observation-table {
            display: grid;
            gap: 0.55rem;
            margin-top: 0.55rem;
        }
        .aqsp-observation-row {
            display: grid;
            grid-template-columns: minmax(126px, 1fr) 0.34fr minmax(220px, 1.45fr);
            gap: 0.8rem;
            align-items: start;
            padding: 0.78rem 0.85rem;
            border-radius: 16px;
            background: linear-gradient(180deg, #fbfcff 0%, #f2f7fb 100%);
            border: 1px solid rgba(26, 71, 102, 0.1);
            box-shadow: 0 10px 22px rgba(30, 51, 65, 0.045);
        }
        .aqsp-observation-name {
            color: #153246;
            font-size: 0.92rem;
            font-weight: 780;
        }
        .aqsp-observation-meta {
            color: #607383;
            font-size: 0.78rem;
            line-height: 1.45;
            font-variant-numeric: tabular-nums;
        }
        .aqsp-observation-reason {
            color: #263f52;
            font-size: 0.82rem;
            line-height: 1.48;
            text-wrap: pretty;
            overflow-wrap: anywhere;
        }
        .aqsp-board-section {
            margin: 0.1rem 0 0.55rem 0;
            color: #17384f;
            font-size: 1.08rem;
            font-weight: 780;
            letter-spacing: 0.01em;
        }
        .aqsp-board-section-note {
            margin: -0.28rem 0 0.72rem 0;
            color: #5d6f7f;
            font-size: 0.84rem;
            line-height: 1.45;
        }
        .aqsp-compact-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.85rem;
            margin-bottom: 0.8rem;
        }
        .aqsp-reading-card {
            position: relative;
            overflow: hidden;
            padding: 1rem 1.1rem;
            border-radius: 18px;
            border: 1px solid rgba(25, 92, 138, 0.16);
            background:
                radial-gradient(circle at 12% 16%, rgba(40, 104, 143, 0.13), transparent 24%),
                linear-gradient(135deg, #fffdf4 0%, #f4f8fb 48%, #edf6f2 100%);
            box-shadow: 0 14px 30px rgba(33, 46, 56, 0.07);
            margin-bottom: 0.85rem;
        }
        .aqsp-reading-title {
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: #5e7081;
            margin-bottom: 0.45rem;
        }
        .aqsp-reading-main {
            font-size: 1.05rem;
            font-weight: 700;
            color: #163247;
            margin-bottom: 0.52rem;
        }
        .aqsp-reading-line {
            font-size: 0.9rem;
            color: #34495a;
            line-height: 1.58;
            margin-top: 0.18rem;
        }
        .aqsp-research-radar {
            position: relative;
            overflow: hidden;
            padding: 0.95rem 1.05rem;
            border-radius: 18px;
            border: 1px solid rgba(41, 97, 132, 0.15);
            background:
                radial-gradient(circle at 88% 18%, rgba(88, 150, 122, 0.13), transparent 23%),
                linear-gradient(135deg, #fbfcf7 0%, #f2f7fa 56%, #f7f4ec 100%);
            box-shadow: 0 12px 26px rgba(33, 46, 56, 0.055);
            margin-bottom: 0.85rem;
        }
        .aqsp-research-radar::before {
            content: "";
            position: absolute;
            top: 0.7rem;
            right: 0.8rem;
            width: 96px;
            height: 96px;
            border-radius: 50%;
            border: 1px dashed rgba(31, 87, 120, 0.22);
            opacity: 0.7;
        }
        .aqsp-research-kicker {
            position: relative;
            font-size: 0.76rem;
            text-transform: uppercase;
            letter-spacing: 0.11em;
            color: #5d6e7c;
            margin-bottom: 0.4rem;
        }
        .aqsp-research-title {
            position: relative;
            font-size: 1.02rem;
            font-weight: 750;
            color: #163247;
            margin-bottom: 0.58rem;
        }
        .aqsp-research-metrics {
            position: relative;
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.45rem;
            margin-bottom: 0.62rem;
        }
        .aqsp-research-metric {
            padding: 0.48rem 0.55rem;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.72);
            border: 1px solid rgba(25, 92, 138, 0.10);
            color: #284b63;
            font-size: 0.8rem;
            font-weight: 680;
        }
        .aqsp-research-line {
            position: relative;
            font-size: 0.87rem;
            color: #34495a;
            line-height: 1.55;
            margin-top: 0.16rem;
        }
        .aqsp-research-prereq {
            position: relative;
            margin-top: 0.56rem;
            padding-top: 0.52rem;
            border-top: 1px solid rgba(25, 92, 138, 0.12);
        }
        .aqsp-research-prereq-line {
            font-size: 0.84rem;
            color: #465b68;
            line-height: 1.48;
            margin-top: 0.14rem;
        }
        .aqsp-brief-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.75rem;
            margin: 0.15rem 0 0.9rem 0;
        }
        .aqsp-brief-card {
            min-height: 166px;
            padding: 0.92rem 0.98rem;
            border-radius: 18px;
            border: 1px solid rgba(26, 71, 102, 0.12);
            background: linear-gradient(180deg, #fffdf7 0%, #f5f8fb 100%);
            box-shadow: 0 12px 26px rgba(33, 46, 56, 0.055);
        }
        .aqsp-brief-card.focus {
            border-color: rgba(57, 121, 92, 0.24);
            background: linear-gradient(180deg, #fffdf4 0%, #f1fbf5 100%);
        }
        .aqsp-brief-card.pressure {
            border-color: rgba(170, 124, 47, 0.24);
            background: linear-gradient(180deg, #fffaf0 0%, #f8fafc 100%);
        }
        .aqsp-brief-card.blocked {
            border-color: rgba(176, 90, 74, 0.24);
            background: linear-gradient(180deg, #fff8f6 0%, #f7f8fb 100%);
        }
        .aqsp-brief-card.research {
            border-color: rgba(41, 97, 132, 0.2);
            background: linear-gradient(180deg, #fbfcf7 0%, #eff7f5 100%);
        }
        .aqsp-brief-kicker {
            font-size: 0.74rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: #617282;
            margin-bottom: 0.42rem;
        }
        .aqsp-brief-title {
            font-size: 1rem;
            font-weight: 760;
            color: #163247;
            margin-bottom: 0.45rem;
            line-height: 1.35;
        }
        .aqsp-brief-line {
            font-size: 0.84rem;
            color: #34495a;
            line-height: 1.5;
            margin-top: 0.15rem;
        }
        .aqsp-debate-brief-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.72rem;
            margin: 0.2rem 0 0.85rem 0;
        }
        .aqsp-debate-brief-card {
            min-height: 148px;
            padding: 0.88rem 0.96rem;
            border-radius: 17px;
            border: 1px solid rgba(26, 71, 102, 0.12);
            background: linear-gradient(180deg, #fbfcff 0%, #f4f8fb 100%);
            box-shadow: 0 11px 24px rgba(33, 46, 56, 0.05);
        }
        .aqsp-debate-brief-card.pressure {
            border-color: rgba(170, 124, 47, 0.24);
            background: linear-gradient(180deg, #fffaf0 0%, #f8fafc 100%);
        }
        .aqsp-debate-brief-card.blocked {
            border-color: rgba(176, 90, 74, 0.24);
            background: linear-gradient(180deg, #fff8f6 0%, #f7f8fb 100%);
        }
        .aqsp-debate-brief-card.focus {
            border-color: rgba(57, 121, 92, 0.23);
            background: linear-gradient(180deg, #fffdf4 0%, #f1fbf5 100%);
        }
        .aqsp-debate-kicker {
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: #617282;
            margin-bottom: 0.38rem;
        }
        .aqsp-debate-title {
            font-size: 0.96rem;
            font-weight: 750;
            color: #163247;
            margin-bottom: 0.42rem;
            line-height: 1.34;
        }
        .aqsp-debate-line {
            font-size: 0.83rem;
            color: #34495a;
            line-height: 1.48;
            margin-top: 0.14rem;
        }
        .aqsp-archive-brief-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.72rem;
            margin: 0.2rem 0 0.85rem 0;
        }
        .aqsp-archive-brief-card {
            min-height: 150px;
            padding: 0.88rem 0.96rem;
            border-radius: 17px;
            border: 1px solid rgba(26, 71, 102, 0.12);
            background: linear-gradient(180deg, #fffdf7 0%, #f5f8fb 100%);
            box-shadow: 0 11px 24px rgba(33, 46, 56, 0.05);
        }
        .aqsp-archive-brief-card.focus {
            border-color: rgba(57, 121, 92, 0.22);
            background: linear-gradient(180deg, #fffdf4 0%, #f1fbf5 100%);
        }
        .aqsp-archive-brief-card.pressure {
            border-color: rgba(170, 124, 47, 0.24);
            background: linear-gradient(180deg, #fffaf0 0%, #f8fafc 100%);
        }
        .aqsp-archive-brief-card.blocked {
            border-color: rgba(176, 90, 74, 0.24);
            background: linear-gradient(180deg, #fff8f6 0%, #f7f8fb 100%);
        }
        .aqsp-archive-kicker {
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: #617282;
            margin-bottom: 0.38rem;
        }
        .aqsp-archive-title {
            font-size: 0.96rem;
            font-weight: 750;
            color: #163247;
            margin-bottom: 0.42rem;
            line-height: 1.34;
        }
        .aqsp-archive-line {
            font-size: 0.83rem;
            color: #34495a;
            line-height: 1.48;
            margin-top: 0.14rem;
        }
        @media (max-width: 980px) {
            .aqsp-brief-grid,
            .aqsp-debate-brief-grid,
            .aqsp-archive-brief-grid,
            .aqsp-overview-strip,
            .aqsp-evidence-strip,
            .aqsp-research-path,
            .aqsp-research-metrics {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        @media (max-width: 620px) {
            .aqsp-brief-grid,
            .aqsp-debate-brief-grid,
            .aqsp-archive-brief-grid,
            .aqsp-overview-strip,
            .aqsp-research-metrics,
            .aqsp-evidence-strip,
            .aqsp-research-path,
            .aqsp-compact-grid {
                grid-template-columns: 1fr;
            }
        }
        /* 科技感增强：导航和按钮 */
        .aqsp-workspace-shell {
            position: relative;
            border-left: 3px solid transparent;
            border-image: linear-gradient(180deg, #1e6fff 0%, #00c2ff 100%) 1;
            background: linear-gradient(180deg, rgba(251, 252, 255, 0.94) 0%, rgba(244, 248, 251, 0.92) 100%);
            backdrop-filter: blur(2px);
        }
        /* 按钮样式增强 */
        div[data-testid="stButton"] button[kind="primary"] {
            background: linear-gradient(135deg, #1e6fff 0%, #00c2ff 100%);
            border: none;
            box-shadow: 0 4px 12px rgba(30, 111, 255, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.2);
            transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.3s ease;
        }
        div[data-testid="stButton"] button[kind="primary"]:hover {
            box-shadow: 0 6px 20px rgba(30, 111, 255, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.3);
            transform: translateY(-1px);
        }
        div[data-testid="stButton"] button[kind="secondary"] {
            border: 1.5px solid rgba(30, 111, 255, 0.3);
            background: rgba(255, 255, 255, 0.6);
            backdrop-filter: blur(8px);
            color: #1e6fff;
            transition: border-color 0.25s ease, background-color 0.25s ease, box-shadow 0.25s ease;
        }
        div[data-testid="stButton"] button[kind="secondary"]:hover {
            border-color: rgba(30, 111, 255, 0.6);
            background: rgba(30, 111, 255, 0.08);
            box-shadow: 0 4px 12px rgba(30, 111, 255, 0.2);
        }
        /* 状态指示器 */
        .aqsp-status-indicator {
            display: inline-block;
            width: 0.6rem;
            height: 0.6rem;
            border-radius: 50%;
            margin-right: 0.4rem;
            animation: pulse-indicator 2s ease-in-out infinite;
        }
        .aqsp-status-indicator.healthy {
            background: #00c248;
            box-shadow: 0 0 8px rgba(0, 194, 72, 0.6);
        }
        .aqsp-status-indicator.warning {
            background: #ffb800;
            box-shadow: 0 0 8px rgba(255, 184, 0, 0.6);
        }
        .aqsp-status-indicator.error {
            background: #ff4757;
            box-shadow: 0 0 8px rgba(255, 71, 87, 0.6);
        }
        @keyframes pulse-indicator {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.7; }
        }
        /* Caption样式增强（用于副标题） */
        div[data-testid="stCaptionContainer"] p {
            color: #7a8fa1;
            font-size: 0.78rem;
            letter-spacing: 0.02em;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_resource
def get_provider() -> DashboardDataProvider:
    return DashboardDataProvider()


def _render_top_overview_strip(
    *,
    review_date: str,
    date_overview: DashboardDateOverview,
    summary,
) -> None:
    cards = (
        ("回看日期", review_date or "-", "当前工作日"),
        ("当日任务", str(date_overview.task_count), "已进入同日导航"),
        (
            "待复核 / 阻塞",
            f"{date_overview.actionable_total} / {date_overview.blocked_total}",
            f"观察 {date_overview.watch_total}",
        ),
        (
            "持仓 / 日志",
            f"{summary.open_positions} / {summary.execution_logs}",
            "虚拟盘现实",
        ),
    )
    st.markdown(
        "\n".join(
            [
                '<div class="aqsp-overview-strip">',
                *[
                    (
                        '<div class="aqsp-overview-item">'
                        f'<div class="aqsp-overview-label">{escape(label)}</div>'
                        f'<div class="aqsp-overview-value">{escape(value)}</div>'
                        f'<div class="aqsp-overview-meta">{escape(meta)}</div>'
                        "</div>"
                    )
                    for label, value, meta in cards
                ],
                "</div>",
            ]
        ),
        unsafe_allow_html=True,
    )


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
    lag_display = _source_lag_display(source_status.get("lag_days", ""))
    verdict_line = _source_status_verdict_line(source_status)

    st.markdown(
        "\n".join(
            line
            for line in (
                f"- 链路结论: {verdict_line}" if verdict_line else "",
                f"- 原始链路: `{requested}` -> `{actual}`",
                f"- 健康度: `{health_label}`",
                f"- 最新交易日: `{latest_trade_date}`",
                f"- 数据滞后: `{lag_display}`",
                f"- 说明: {health_message}",
            )
            if line
        )
    )


def _source_lag_display(raw_lag_days: object) -> str:
    value = "" if raw_lag_days is None else str(raw_lag_days).strip()
    if not value or value in {"-", "未记录", "None", "nan"}:
        return "未记录"
    return f"{value} 天"


def _task_metric_labels(task_id: str) -> tuple[str, str, str]:
    if task_id == "closing_review":
        return ("已验证", "待复盘", "复盘阻塞")
    if task_id == "briefing":
        return ("已落盘", "待跟踪", "待补档")
    return ("待复核", "观察", "阻塞")


def _has_review_meta(value: str) -> bool:
    return value.strip() not in {"", "-", "暂无额外再看时间"}


def _review_meta_line(label: str, value: str) -> str:
    return f"{label}: {value.strip()}" if _has_review_meta(value) else ""


def _committee_supplement_label() -> str:
    return "委员会补充结论"


def _task_scope_summary(task_labels: tuple[str, ...]) -> str:
    labels = tuple(label.strip() for label in task_labels if label.strip())
    return "、".join(labels) if labels else "仅当前任务"


def _task_scope_line(task_summary: str) -> str:
    summary = task_summary.strip() or "仅当前任务"
    return f"涉及任务: {summary}"


def _render_frame(title: str, frame) -> None:
    st.subheader(title)
    if frame.empty:
        st.info("这块还没有数据。先确认对应任务已跑完，再回来回看。")
        return
    st.dataframe(frame, width="stretch", hide_index=True)


def _stretch_button(label: str, **kwargs) -> bool:
    """Render full-width buttons with the current Streamlit width API."""
    kwargs.setdefault("width", "stretch")
    return bool(st.button(label, **kwargs))


def _lazy_home_section_requested(
    *,
    button_label: str,
    state_key: str,
    idle_hint: str,
) -> bool:
    if bool(st.session_state.get(state_key)):
        return True
    st.caption(idle_hint)
    if _stretch_button(button_label, key=f"{state_key}_button"):
        st.session_state[state_key] = True
        st.rerun()
    return False


def _render_two_line_nav_label(
    label: _TwoLineNavLabel, *, active: bool = False
) -> None:
    active_class = " active" if active else ""
    st.markdown(
        (
            f'<div class="aqsp-nav-code{active_class}">{escape(label.code)}</div>'
            f'<div class="aqsp-nav-name{active_class}">{escape(label.name)}</div>'
        ),
        unsafe_allow_html=True,
    )


def _render_line_block(title: str, lines: tuple[str, ...], empty_text: str) -> None:
    st.subheader(title)
    cleaned_lines = _unique_lines(lines)
    if not cleaned_lines:
        st.info(empty_text)
        return
    st.markdown("\n".join(f"- {line}" for line in cleaned_lines))


def _render_cockpit_card(
    *,
    kicker: str,
    title: str,
    lines: tuple[str, ...],
    tone: str = "",
) -> None:
    cleaned_lines = _unique_lines(lines)
    body = (
        "<br/>".join(escape(line) for line in cleaned_lines)
        if cleaned_lines
        else "暂无补充说明。"
    )
    st.markdown(
        f"""
        <div class="aqsp-cockpit-card {tone}">
          <div class="aqsp-cockpit-kicker">{escape(kicker)}</div>
          <div class="aqsp-cockpit-title">{escape(title)}</div>
          <div class="aqsp-cockpit-body">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _signal_evidence_context(task_id: str) -> tuple[str, str]:
    if task_id in {"briefing", "closing_review"}:
        return "main_chain", "同日主链证据（当前任务无独立选股表）"
    return task_id, "当日任务证据"


def _research_task_id_for_review_card(
    *,
    review_card: DashboardCandidateCard,
    journey_steps: tuple[DashboardCandidateJourneyStep, ...],
    fallback_task_id: str,
) -> str:
    if not journey_steps:
        return fallback_task_id

    for step in journey_steps:
        if (
            abs(step.score - review_card.score) < 1e-9
            and step.action_label == review_card.action_label
            and step.status_label == review_card.status_label
        ):
            return step.task_id

    for step in journey_steps:
        if (
            step.action_label == review_card.action_label
            and step.status_label == review_card.status_label
        ):
            return step.task_id

    for step in journey_steps:
        if abs(step.score - review_card.score) < 1e-9:
            return step.task_id

    normalized_fallback, _ = _signal_evidence_context(fallback_task_id)
    if normalized_fallback in {step.task_id for step in journey_steps}:
        return normalized_fallback
    return journey_steps[-1].task_id


def _spotlight_decision_note(spotlight: DashboardCandidateSpotlight) -> str:
    lines = _unique_lines(
        (
            (
                f"跨市线索 {spotlight.cross_market_summary}"
                if spotlight.cross_market_summary
                else ""
            ),
            (
                f"讨论支持: {spotlight.support_points[0]}"
                if spotlight.support_points
                else ""
            ),
            (
                f"讨论反对: {spotlight.opposition_points[0]}"
                if spotlight.opposition_points
                else ""
            ),
            (
                f"讨论待确认: {spotlight.watch_items[0]}"
                if spotlight.watch_items
                else ""
            ),
        )
    )
    if lines:
        return "；".join(lines)
    return "当前仅列入观察池，等待下一次刷新确认量能、价格和风险卡点。"


def _spotlight_has_structured_summary(spotlight: DashboardCandidateSpotlight) -> bool:
    return bool(
        spotlight.cross_market_summary
        or spotlight.support_points
        or spotlight.opposition_points
        or spotlight.watch_items
    )


def _spotlight_as_candidate_card(
    spotlight: DashboardCandidateSpotlight,
) -> DashboardCandidateCard:
    symbol, _, remainder = spotlight.display_name.partition(" ")
    return DashboardCandidateCard(
        symbol=spotlight.symbol,
        name=remainder or spotlight.display_name,
        display_name=spotlight.display_name,
        rank_label="同日联动",
        score=spotlight.score,
        action_label=spotlight.action_label,
        status_label=spotlight.status_label,
        decision_note=_spotlight_decision_note(spotlight),
        next_step=spotlight.next_step,
        blocker=spotlight.blocker,
        review_meta=spotlight.review_meta,
        reasons=spotlight.reasons,
        risks=spotlight.risks,
        strategies=(),
        data_source="同日联动",
        news_catalyst_summary=spotlight.news_catalyst_summary,
        cross_market_summary=spotlight.cross_market_summary,
        cross_market_chain_summary=spotlight.cross_market_chain_summary,
        cross_market_validation_summary=spotlight.cross_market_validation_summary,
        cross_market_invalidation_summary=spotlight.cross_market_invalidation_summary,
    )


def _review_fallback_card(
    *,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
) -> DashboardCandidateCard | None:
    if selected_card is not None:
        return selected_card
    if selected_spotlight is not None:
        return _spotlight_as_candidate_card(selected_spotlight)
    if debate_summary is not None:
        return _debate_as_candidate_card(debate_summary)
    return None


def _debate_as_candidate_card(
    debate_summary: DashboardDebateSummary,
) -> DashboardCandidateCard:
    symbol, _, remainder = debate_summary.display_name.partition(" ")
    return DashboardCandidateCard(
        symbol=debate_summary.symbol,
        name=remainder or debate_summary.display_name,
        display_name=debate_summary.display_name,
        rank_label="辩论主结论",
        # This score is displayed only in the review workspace as an advisory
        # adjustment; the rank label keeps it out of deterministic queues.
        score=debate_summary.adjusted_score,
        action_label=debate_summary.recommended_adjustment_label,
        status_label=debate_summary.consensus
        or debate_summary.recommended_adjustment_label,
        decision_note=(
            debate_summary.research_verdict
            or "该标的当前没有独立候选卡，以下仅保留同日多 Agent 补充结论，不替代选股评分。"
        ),
        next_step=(
            debate_summary.next_trigger
            or debate_summary.adjustment_reason
            or (
                debate_summary.opportunity_highlights[0]
                if debate_summary.opportunity_highlights
                else ""
            )
        ),
        blocker=(
            debate_summary.primary_risk_gate
            or (debate_summary.risk_warnings[0] if debate_summary.risk_warnings else "")
        ),
        review_meta="补充结论 / 待复核",
        reasons=debate_summary.opportunity_highlights,
        risks=debate_summary.risk_warnings,
        strategies=(),
        data_source=debate_summary.data_source or "多 Agent 补充",
    )


def _candidate_symbol_order(
    cards: tuple[DashboardCandidateCard, ...],
    spotlights: tuple[DashboardCandidateSpotlight, ...],
    debates: tuple[DashboardDebateSummary, ...] = (),
) -> list[str]:
    symbol_order: list[str] = []
    for card in cards:
        if card.symbol and card.symbol not in symbol_order:
            symbol_order.append(card.symbol)
    for debate in debates:
        if debate.symbol and debate.symbol not in symbol_order:
            symbol_order.append(debate.symbol)
    for spotlight in spotlights:
        if spotlight.symbol and spotlight.symbol not in symbol_order:
            symbol_order.append(spotlight.symbol)
    return symbol_order


def _review_context_for_symbol(
    *,
    symbol: str,
    cards: tuple[DashboardCandidateCard, ...],
    spotlights: tuple[DashboardCandidateSpotlight, ...],
    debates: tuple[DashboardDebateSummary, ...] = (),
) -> tuple[
    DashboardCandidateCard | None,
    DashboardCandidateSpotlight | None,
    DashboardDebateSummary | None,
    DashboardCandidateCard | None,
]:
    selected_card = next((card for card in cards if card.symbol == symbol), None)
    selected_spotlight = next(
        (spotlight for spotlight in spotlights if spotlight.symbol == symbol),
        None,
    )
    selected_debate = next(
        (debate for debate in debates if debate.symbol == symbol), None
    )
    review_card = _review_fallback_card(
        selected_card=selected_card,
        selected_spotlight=selected_spotlight,
        debate_summary=selected_debate,
    )
    return (selected_card, selected_spotlight, selected_debate, review_card)


def _debate_overview_lines(
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    if debate_summary is None:
        return ()
    vote_line = (
        f"票数分布: 看多 {debate_summary.bull_count}"
        f" / 看空 {debate_summary.bear_count}"
        f" / 中性 {debate_summary.neutral_count}"
    )
    if debate_summary.research_verdict:
        review_line = f"研究口径: {debate_summary.research_verdict}"
    elif debate_summary.risk_warnings:
        review_line = "待核对风险: " + "；".join(debate_summary.risk_warnings[:2])
    elif debate_summary.adjustment_reason:
        review_line = f"待核对原因: {debate_summary.adjustment_reason}"
    elif debate_summary.opportunity_highlights:
        review_line = "待验证机会: " + "；".join(
            debate_summary.opportunity_highlights[:2]
        )
    else:
        review_line = "待补原因: 当前讨论未给出明确风险或机会，先回候选来龙去脉复核。"
    gate_line = (
        f"核心卡点: {debate_summary.primary_risk_gate}"
        if debate_summary.primary_risk_gate
        else ""
    )
    trigger_line = (
        f"下一触发: {debate_summary.next_trigger}"
        if debate_summary.next_trigger
        else ""
    )
    agent_lines = tuple(
        line
        for line in (
            (
                f"{view.role_label}: {view.stance_label} / 置信 {view.confidence:.0%} | "
                f"{view.key_argument or view.key_risk or view.key_opportunity or '未补充核心观点'}"
            )
            for view in sorted(
                debate_summary.agent_views,
                key=lambda item: item.confidence,
                reverse=True,
            )[:2]
        )
        if line
    )
    return _unique_lines(
        (
            f"结论: {debate_summary.recommended_adjustment_label} / 分歧 {debate_summary.disagreement_score:.2f}",
            (
                f"共识: {debate_summary.consensus}"
                if debate_summary.consensus
                else "共识: 暂未形成明确一致结论"
            ),
            vote_line,
            review_line,
            gate_line,
            trigger_line,
        ),
        agent_lines,
    )


def _pipeline_label(pipeline: str) -> str:
    return {
        "data_source": "数据源",
        "strategy": "策略",
        "timing": "择时",
        "execution_risk": "执行风控",
        "ai_research": "研究",
    }.get(pipeline, pipeline or "未分类")


def _research_stage_label(stage: str) -> str:
    return {
        "report_only": "只进报告",
        "gated_runtime": "门控中",
        "next_adapter": "待接数据源",
        "research_candidate": "研究候选",
        "future_optional": "远期可选",
    }.get(stage, stage or "待评估")


def _research_action_label(kind: str) -> str:
    return "数据源" if kind == "data_source" else "策略"


def _research_prereq_status_line(summary: ResearchSummary) -> str:
    if not summary.prereq_items:
        return "前置条件: 暂无登记项，先按研究 gate 审阅，不自动进入评分。"
    needs_env = [item for item in summary.prereq_items if item.status == "needs_env"]
    needs_fixture = [
        item for item in summary.prereq_items if item.status == "needs_fixture"
    ]
    ready = [item for item in summary.prereq_items if item.status == "ready"]
    parts: list[str] = []
    if needs_env:
        names = "、".join(
            f"{item.name} 缺 {','.join(item.missing_env_vars)}"
            for item in needs_env[:2]
        )
        parts.append(f"🔑 待配置: {names}")
    if needs_fixture:
        names = "、".join(item.name for item in needs_fixture[:2])
        parts.append(f"🧪 待 fixture: {names}")
    if ready:
        names = "、".join(item.name for item in ready[:2])
        parts.append(f"✅ 可推进: {names}")
    return "前置条件: " + "；".join(parts or ["暂无阻塞记录"])


def _research_prereq_action_line(summary: ResearchSummary) -> str:
    first_item = next(
        (
            item
            for item in summary.prereq_items
            if item.status in {"needs_env", "needs_fixture"}
        ),
        None,
    )
    if first_item is None:
        return "推进口径: 当前只允许做 report-only / shadow / fixture 验证，不直接改主链评分。"
    action = (
        first_item.user_action
        if first_item.status == "needs_env"
        else first_item.code_action
    )
    return (
        f"推进口径: {first_item.name} | {action or '先补齐验证材料，再评估是否接入。'}"
    )


def _research_radar_card(summary: ResearchSummary | None) -> _ResearchRadarCard:
    if summary is None:
        return _ResearchRadarCard(
            title="研究进展未更新",
            metrics=(
                ("研究发现", "-"),
                ("已吸收", "-"),
                ("只进报告", "-"),
                ("门控中", "-"),
            ),
            lines=(
                "当前只展示已落盘主链结果；研究队列缺失不影响当前主链评分。",
                "下一步: 先补齐研究配置，再决定是否只放在报告里或继续验证。",
            ),
        )

    leading_pipeline = (
        summary.pipeline_summaries[0] if summary.pipeline_summaries else None
    )
    leading_action = summary.next_actions[0] if summary.next_actions else None
    topic_names = (
        "、".join(item.name for item in summary.absorbed_families[:2]) or "暂无"
    )
    pipeline_line = (
        (
            f"热点管线: {_pipeline_label(leading_pipeline.pipeline)}"
            f" P1 {leading_pipeline.p1}/{leading_pipeline.total}"
            f" | 来源 {leading_pipeline.top_repo or '-'}"
        )
        if leading_pipeline is not None
        else "热点管线: 暂无研究管线摘要。"
    )
    action_line = (
        (
            f"下一步: {leading_action.priority} "
            f"{_research_action_label(leading_action.kind)}"
            f"「{leading_action.name or leading_action.item_id}」"
            f" | {_research_stage_label(leading_action.stage)}"
            f" | gate: {leading_action.blocker or '待补验证口径'}"
        )
        if leading_action is not None
        else "下一步: 暂无接入动作，先维持当前冻结主链。"
    )
    return _ResearchRadarCard(
        title=(
            f"研究发现 {research_findings_display(summary)}，"
            f"已吸收 {len(summary.absorbed_families)} 个策略族"
        ),
        metrics=(
            ("研究发现", research_findings_metric(summary)),
            ("已吸收", str(len(summary.absorbed_families))),
            ("只进报告", str(summary.report_only_family_count)),
            ("门控中", str(summary.gated_family_count)),
        ),
        lines=_unique_lines(
            (
                "边界: 研究结论不会直接改写评分；只有通过验证后才可能影响运行链。",
                f"已吸收主题: {topic_names}",
                pipeline_line,
                action_line,
            )
        ),
        prereq_lines=_unique_lines(
            (
                _research_prereq_status_line(summary),
                _research_prereq_action_line(summary),
            )
        ),
    )


def _debate_agent_focus_lines(
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    if debate_summary is None:
        return ()
    return tuple(
        line
        for line in (
            (
                f"{view.role_label}: {view.stance_label} / 置信 {view.confidence:.0%}"
                f" | {view.key_argument or view.key_risk or view.key_opportunity or '未补充核心观点'}"
            )
            for view in sorted(
                debate_summary.agent_views,
                key=lambda item: item.confidence,
                reverse=True,
            )[:2]
        )
        if line
    )


def _debate_evidence_composition_line(
    debate_summary: DashboardDebateSummary | None,
) -> str:
    return debate_summary_evidence_line(debate_summary)


def _debate_primary_takeaways(
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    if debate_summary is None:
        return ()
    conclusion = _debate_conclusion_summary(debate_summary)
    return tuple(
        line
        for line in (
            conclusion.decision_line,
            conclusion.cross_market_line,
            conclusion.chain_or_trigger_line,
            conclusion.validation_line,
            conclusion.invalidation_line,
            conclusion.consensus_line,
            conclusion.active_roles_line,
            (
                f"修正原因: {debate_summary.adjustment_reason}"
                if debate_summary.adjustment_reason
                else ""
            ),
            conclusion.history_line,
            conclusion.reliability_line,
            conclusion.support_line,
            conclusion.opposition_line,
            conclusion.watch_line,
        )
        if line
    )


def _archive_conclusion_context(
    *,
    task_view,
    selected_symbol: str,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None = None,
) -> tuple[str, tuple[str, ...]]:
    raw_archive_lines = tuple(
        _safe_archive_line(line)
        for line in (
            *task_view.report_summary_lines[:2],
            (
                f"市场态势: {task_view.market_environment}"
                if task_view.market_environment
                else ""
            ),
            *task_view.next_day_focus_lines[:2],
            *task_view.runtime_lines[:2],
        )
        if line
    )
    symbol_archive_lines, _ = _partition_archive_focus_lines(
        raw_archive_lines,
        selected_symbol=selected_symbol,
        selected_card=selected_card,
        selected_spotlight=selected_spotlight,
        debate_summary=debate_summary,
    )
    if symbol_archive_lines:
        return (
            "归档结论",
            _unique_lines(
                tuple(
                    line for line in raw_archive_lines if line.startswith("市场态势:")
                ),
                symbol_archive_lines,
            ),
        )

    if selected_card is not None:
        card_lines = tuple(
            line
            for line in (
                *_debate_primary_takeaways(debate_summary)[:2],
                (
                    f"当前限制: {_card_primary_blocker(selected_card)}"
                    if _card_primary_blocker(selected_card)
                    else ""
                ),
                (
                    f"候选摘要: {_safe_current_research_line(selected_card.decision_note)}"
                    if selected_card.decision_note
                    and selected_card.decision_note
                    != _card_primary_blocker(selected_card)
                    else ""
                ),
                (
                    f"下一步: {_card_next_action(selected_card)}"
                    if _card_next_action(selected_card) != "-"
                    else ""
                ),
                _review_meta_line("再看时间", selected_card.review_meta),
            )
            if line
        )
        if card_lines:
            return (
                "当前标的结论",
                _unique_lines(
                    tuple(
                        line
                        for line in raw_archive_lines
                        if line.startswith("市场态势:")
                    ),
                    card_lines,
                ),
            )

    if selected_spotlight is not None:
        spotlight_lines = tuple(
            line
            for line in (
                *_debate_primary_takeaways(debate_summary)[:2],
                (
                    f"跨市传导: {selected_spotlight.cross_market_summary}"
                    if selected_spotlight.cross_market_summary
                    else ""
                ),
                (
                    f"当前重点: {_safe_current_research_line(selected_spotlight.blocker or selected_spotlight.next_step)}"
                    if selected_spotlight.blocker or selected_spotlight.next_step
                    else ""
                ),
                _review_meta_line("统一复核", selected_spotlight.review_meta),
                (
                    _task_scope_line(
                        _task_scope_summary(selected_spotlight.task_labels)
                    )
                    if selected_spotlight.task_labels
                    else ""
                ),
            )
            if line
        )
        if spotlight_lines:
            return (
                "当前标的结论",
                _unique_lines(
                    tuple(
                        line
                        for line in raw_archive_lines
                        if line.startswith("市场态势:")
                    ),
                    spotlight_lines,
                ),
            )

    review_card = _review_fallback_card(
        selected_card=None,
        selected_spotlight=None,
        debate_summary=debate_summary,
    )
    if review_card is not None:
        debate_summary_lines = _debate_primary_takeaways(debate_summary)[:2]
        debate_card_lines = tuple(line for line in debate_summary_lines if line)
        if debate_card_lines:
            return (
                "当前标的结论",
                _unique_lines(
                    tuple(
                        line
                        for line in raw_archive_lines
                        if line.startswith("市场态势:")
                    ),
                    debate_card_lines,
                ),
            )

    return (
        "任务级归档结论",
        _unique_lines(_debate_primary_takeaways(debate_summary)[:2], raw_archive_lines),
    )


def _archive_debate_evidence_lines(
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    if debate_summary is None:
        return ()
    vote_lines = (
        (
            f"投票分布: 看多 {debate_summary.bull_count}"
            f" / 看空 {debate_summary.bear_count}"
            f" / 中性 {debate_summary.neutral_count}"
        ),
        f"讨论轮次: {debate_summary.round_count}",
    )
    return _unique_lines(
        vote_lines,
        _debate_agent_focus_lines(debate_summary)[:2],
        tuple(
            line
            for line in (
                (
                    f"调整原因: {debate_summary.adjustment_reason}"
                    if debate_summary.adjustment_reason
                    else ""
                ),
                _debate_evidence_composition_line(debate_summary),
            )
            if line
        ),
    )


def _archive_debate_summary_lines(
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    if debate_summary is None:
        return ()
    conclusion = _debate_conclusion_summary(debate_summary)
    compact_cross_market_line = _focus_cross_market_digest_line(
        debate_summary=debate_summary,
        focus_display=debate_summary.display_name,
    )
    context_line = (
        compact_cross_market_line
        or conclusion.support_line
        or conclusion.opposition_line
        or conclusion.cross_market_line
    )
    followup_line = _compact_debate_followup_line(
        debate_summary,
        conclusion=conclusion,
    )
    reason_line = (
        f"修正原因: {debate_summary.adjustment_reason}"
        if debate_summary.adjustment_reason
        else ""
    )
    return _unique_lines(
        (
            conclusion.decision_line.replace("当前结论: ", "委员会结论: "),
            context_line,
            followup_line,
            reason_line,
            conclusion.history_line,
        )
    )


def _render_debate_cockpit(
    *,
    debate_summary: DashboardDebateSummary | None,
    empty_text: str,
    kicker: str = "多 Agent 补充",
    tone: str = "archive",
) -> None:
    if debate_summary is None:
        _render_cockpit_card(
            kicker=kicker,
            title="当日未触发",
            lines=(empty_text,),
            tone="archive",
        )
        return

    title = f"{debate_summary.recommended_adjustment_label} / 分歧 {debate_summary.disagreement_score:.2f}"
    _render_cockpit_card(
        kicker=kicker,
        title=title,
        lines=_debate_overview_lines(debate_summary),
        tone=tone,
    )


def _card_primary_blocker(card: DashboardCandidateCard) -> str:
    if card.blocker and not _is_missing_blocker_text(card.blocker):
        return card.blocker
    if (
        "阻塞" in card.rank_label
        and card.risks
        and not _is_missing_blocker_text(card.risks[0])
    ):
        return card.risks[0]
    return ""


def _is_missing_blocker_text(text: str) -> bool:
    normalized = text.strip()
    return (
        "阻塞原因未记录" in normalized
        or "需补 candidate_blocker" in normalized
        or "需补充风险说明或复核条件" in normalized
    )


def _card_emphasis(card: DashboardCandidateCard) -> str:
    blocker = _card_primary_blocker(card)
    if blocker:
        return _safe_current_research_line(blocker)
    if card.next_step:
        return _safe_current_research_line(card.next_step)
    if card.decision_note and card.decision_note != "按当前顺位继续跟踪":
        return _safe_current_research_line(card.decision_note)
    if "阻塞" in card.rank_label:
        return "当前处于阻塞观察，先核对卡点条件。"
    return _safe_current_research_line(card.decision_note or "继续跟踪")


def _card_next_action(card: DashboardCandidateCard) -> str:
    if card.next_step and not _is_missing_blocker_text(card.next_step):
        return _safe_current_research_line(card.next_step)
    if _card_primary_blocker(card) and "阻塞" in card.rank_label:
        return "先确认复核条件，卡点解除后再决定是否恢复推进。"
    if card.decision_note and not _is_missing_blocker_text(card.decision_note):
        return _safe_current_research_line(card.decision_note)
    return "-"


def _safe_current_research_line(line: str) -> str:
    return _sanitize_day_replay_line(line)


def _safe_archive_line(line: str) -> str:
    return _neutral_archive_summary_line(line)


def _safe_archive_lines(lines: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_safe_archive_line(line) for line in lines if line)


def _normalized_readiness_lines(
    *,
    review_card: DashboardCandidateCard | None,
    readiness_lines: tuple[str, ...],
) -> tuple[str, ...]:
    blocker = _card_primary_blocker(review_card) if review_card is not None else ""
    generic_line = "研究已产出，但尚未进入纸面入场或阻塞队列。"
    if not blocker:
        return readiness_lines
    legacy_generic_line = "研究已产出，但尚未进入待纸面观察或阻塞队列。"
    normalized = tuple(
        line
        for line in readiness_lines
        if line not in {generic_line, legacy_generic_line}
    )
    if normalized:
        return normalized
    return (f"研究已产出，但当前被{blocker}拦住，暂不进入纸面入场验证。",)


def _symbol_focus_entry(card: DashboardCandidateCard) -> str:
    emphasis = _card_primary_blocker(card) or _card_emphasis(card)
    return _join_display_parts(card.display_name, emphasis)


def _home_blocked_summary(
    blocked_cards: tuple[DashboardCandidateCard, ...],
) -> str:
    if not blocked_cards:
        return "当前无明显阻塞"
    blocker_counts: dict[str, int] = {}
    for card in blocked_cards:
        blocker = _card_primary_blocker(card)
        if not blocker:
            continue
        blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
    if not blocker_counts:
        return f"当前共有 {len(blocked_cards)} 只阻塞候选，先逐只核对卡点条件。"
    top_blocker, top_count = max(
        blocker_counts.items(),
        key=lambda item: (item[1], item[0]),
    )
    if top_count == len(blocked_cards):
        return f"阻塞 {len(blocked_cards)} 只，当前都卡在：{top_blocker}"
    return f"阻塞 {len(blocked_cards)} 只，其中 {top_count} 只卡在：{top_blocker}"


def _home_watch_fallback_lines(
    *,
    task_view,
    blocked_focus: DashboardCandidateCard | None,
) -> tuple[str, ...]:
    if task_view.review_lines:
        return _singleton_lines(task_view.review_lines[0])
    if task_view.watchlist_lines:
        return _singleton_lines(task_view.watchlist_lines[0])
    return _singleton_lines("当前没有需要单独观察的对象，不用为了凑名单硬找方向。")


def _home_recommend_fallback_lines(
    *,
    task_view,
    blocked_focus: DashboardCandidateCard | None,
) -> tuple[str, ...]:
    if task_view.recommendation_lines:
        return _singleton_lines(task_view.recommendation_lines[0])
    if blocked_focus is not None:
        blocker = _card_primary_blocker(blocked_focus)
        if blocker:
            return _singleton_lines(
                f"当前没有纸面复核候选，先核对 {blocked_focus.display_name} 的卡点。"
            )
        return _singleton_lines(
            f"当前没有纸面复核候选，先核对 {blocked_focus.display_name} 的卡点。"
        )
    return _singleton_lines("当前没有纸面复核候选，先等下一轮主链信号。")


def _debate_vote_snapshot_lines(
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    if debate_summary is None:
        return ()
    lines: list[str] = [
        (
            f"投票分布: 看多 {debate_summary.bull_count}"
            f" / 看空 {debate_summary.bear_count}"
            f" / 中性 {debate_summary.neutral_count}"
        ),
        f"讨论轮次: {debate_summary.round_count}",
    ]
    top_agents = tuple(
        f"{view.role_label}: {view.stance_label} / 置信 {view.confidence:.0%}"
        for view in _debate_snapshot_agent_views(debate_summary)
    )
    return _unique_lines(tuple(lines), top_agents)


def _debate_snapshot_agent_views(
    debate_summary: DashboardDebateSummary,
) -> tuple[DashboardDebateAgentView, ...]:
    ordered = tuple(
        sorted(
            debate_summary.agent_views,
            key=lambda item: item.confidence,
            reverse=True,
        )
    )
    bullish = next((view for view in ordered if view.stance == "bullish"), None)
    bearish = next((view for view in ordered if view.stance == "bearish"), None)
    selected: list[DashboardDebateAgentView] = []
    if bullish is not None:
        selected.append(bullish)
    if bearish is not None and bearish not in selected:
        selected.append(bearish)
    for view in ordered:
        if view not in selected:
            selected.append(view)
        if len(selected) >= 2:
            break
    return tuple(selected[:2])


def _debate_brief_cards(
    debate_summary: DashboardDebateSummary | None,
) -> tuple[_DebateBriefCard, ...]:
    if debate_summary is None:
        return ()
    tone = (
        "blocked"
        if debate_summary.recommended_adjustment == "lower"
        else ("pressure" if debate_summary.disagreement_score >= 0.35 else "focus")
    )
    leading_views = tuple(
        f"{view.role_label}: {view.stance_label} / 置信 {view.confidence:.0%}"
        for view in sorted(
            debate_summary.agent_views,
            key=lambda item: item.confidence,
            reverse=True,
        )[:2]
    )
    conclusion = build_debate_conclusion(debate_summary)
    review_lines = _unique_lines(
        (
            conclusion.chain_or_trigger_line,
            conclusion.watch_line,
            conclusion.opposition_line,
            conclusion.invalidation_line,
            (
                f"核心卡点: {debate_summary.primary_risk_gate}"
                if debate_summary.primary_risk_gate
                else ""
            ),
            conclusion.support_line,
            conclusion.validation_line,
            (
                f"先核对风险: {'；'.join(debate_summary.risk_warnings[:2])}"
                if debate_summary.risk_warnings
                else ""
            ),
            (
                f"可观察机会: {'；'.join(debate_summary.opportunity_highlights[:2])}"
                if debate_summary.opportunity_highlights
                else ""
            ),
            (
                f"修正原因: {debate_summary.adjustment_reason}"
                if debate_summary.adjustment_reason
                else "修正原因: 当前未给出充分依据，回到候选来龙去脉。"
            ),
        )
    )
    return (
        _DebateBriefCard(
            kicker="辩论结论",
            title=f"{debate_summary.recommended_adjustment_label} / 分歧 {debate_summary.disagreement_score:.2f}",
            lines=_unique_lines(
                (
                    (
                        f"共识: {debate_summary.consensus}"
                        if debate_summary.consensus
                        else "共识: 当前未形成明确共识。"
                    ),
                    "边界: 这是解释层，不替代选股评分。",
                )
            ),
            tone=tone,
        ),
        _DebateBriefCard(
            kicker="票型结构",
            title=(
                f"看多 {debate_summary.bull_count}"
                f" / 看空 {debate_summary.bear_count}"
                f" / 中性 {debate_summary.neutral_count}"
            ),
            lines=leading_views
            or (f"讨论轮次: {debate_summary.round_count}", "暂无 agent 明细。"),
            tone="archive",
        ),
        _DebateBriefCard(
            kicker="接下来做什么",
            title="核对触发与失效",
            lines=review_lines,
            tone=(
                "pressure"
                if debate_summary.primary_risk_gate
                or debate_summary.opposition_points
                or debate_summary.risk_warnings
                else "archive"
            ),
        ),
    )


def _render_debate_brief(debate_summary: DashboardDebateSummary | None) -> None:
    cards = _debate_brief_cards(debate_summary)
    if not cards:
        return
    card_html = []
    for card in cards:
        line_html = "".join(
            f'<div class="aqsp-debate-line">{escape(line)}</div>'
            for line in card.lines[:3]
        )
        card_html.append(
            f"""
            <div class="aqsp-debate-brief-card {escape(card.tone)}">
              <div class="aqsp-debate-kicker">{escape(card.kicker)}</div>
              <div class="aqsp-debate-title">{escape(card.title)}</div>
              {line_html}
            </div>
            """
        )
    st.markdown(
        "\n".join(
            [
                '<div class="aqsp-debate-brief-grid">',
                *card_html,
                "</div>",
            ]
        ),
        unsafe_allow_html=True,
    )


def _current_mode_label(task_view) -> str:
    if task_view.task_id == "closing_review":
        if task_view.report_markdown.strip():
            return "已复盘"
        if task_view.actionable_count > 0 or task_view.blocked_count > 0:
            return "已验证未归档"
        if task_view.candidate_count > 0:
            return "待复盘"
        return "无新结论"
    if task_view.task_id == "briefing":
        return "待跟踪" if task_view.next_day_focus_lines else "已产出"
    if task_view.actionable_count > 0:
        return "有推荐"
    if task_view.blocked_count > 0:
        return "待核对"
    if task_view.watch_count > 0:
        return "观察中"
    if task_view.candidate_count > 0:
        return "已产出"
    return "无新结论"


def _classify_candidate_queues(
    cards: tuple[DashboardCandidateCard, ...],
) -> tuple[
    tuple[DashboardCandidateCard, ...],
    tuple[DashboardCandidateCard, ...],
    tuple[DashboardCandidateCard, ...],
]:
    recommend: list[DashboardCandidateCard] = []
    watch: list[DashboardCandidateCard] = []
    blocked: list[DashboardCandidateCard] = []
    for card in cards:
        bucket = _candidate_status_bucket(card)
        if bucket == "阻塞":
            blocked.append(card)
            continue
        if bucket == "推荐":
            recommend.append(card)
            continue
        watch.append(card)
    return tuple(recommend), tuple(watch), tuple(blocked)


def _queue_item_meta(card: DashboardCandidateCard, emphasis: str) -> str:
    meta_parts = [
        f"当前结论: {escape(_action_status_label(card.action_label, card.status_label))}"
    ]
    if _has_review_meta(card.review_meta):
        meta_parts.append(f"复核: {escape(card.review_meta)}")
    lines = [
        f'<div class="aqsp-queue-meta">{" / ".join(meta_parts)}</div>',
        f'<div class="aqsp-queue-meta">{escape(emphasis)}</div>',
    ]
    if card.decision_note and card.decision_note != emphasis:
        lines.append(
            f'<div class="aqsp-queue-meta">说明: {escape(_safe_current_research_line(card.decision_note))}</div>'
        )
    if card.reasons:
        lines.append(
            '<div class="aqsp-queue-meta">理由: '
            + escape(_safe_current_research_line("；".join(card.reasons[:2])))
            + "</div>"
        )
    if card.risks:
        lines.append(
            '<div class="aqsp-queue-meta">风险: '
            + escape(_safe_current_research_line("；".join(card.risks[:2])))
            + "</div>"
        )
    return "".join(lines)


def _render_priority_queue(
    *,
    title: str,
    kicker: str,
    summary: str,
    cards: tuple[DashboardCandidateCard, ...],
    empty_text: str,
    tone: str,
) -> None:
    st.subheader(title)
    if not cards:
        st.info(empty_text)
        return

    items: list[str] = []
    for card in cards[:3]:
        emphasis = _card_emphasis(card)
        items.append(
            f"""
            <div class="aqsp-queue-item">
              <div class="aqsp-queue-head">
                <div>
                  <div class="aqsp-queue-name">{escape(card.display_name)}</div>
                  <div class="aqsp-queue-rank">{escape(card.rank_label)}</div>
                </div>
                <div class="aqsp-queue-score">评分 {card.score:.1f}</div>
              </div>
              {_queue_item_meta(card, emphasis)}
            </div>
            """
        )

    st.markdown(
        f"""
        <div class="aqsp-queue-card {tone}">
          <div class="aqsp-queue-kicker">{escape(kicker)}</div>
          <div class="aqsp-queue-title">{escape(title)}</div>
          <div class="aqsp-queue-summary">{escape(summary)}</div>
          {"".join(items)}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_summary_cards(task_view) -> None:
    overview_col, market_col = st.columns(2)
    with overview_col:
        _render_line_block(
            "报告摘要",
            task_view.report_summary_lines,
            "当前任务还没有结构化摘要，先看原始报告或等待下一次跑批补齐。",
        )
    with market_col:
        if task_view.market_environment:
            st.subheader("市场态势")
            st.success(task_view.market_environment)
        else:
            st.subheader("市场态势")
            st.info("当前任务还没有市场态势标签，先不要把它当作完整结论。")

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
            "当前任务还没有明日重点，先按主链候选和风险卡点回看。",
        )
    with nav_col:
        _render_line_block(
            "优先顺位",
            task_view.ranking_lines,
            "当前日期还没有优先顺位，说明暂时不需要强行排序。",
        )


def _home_action_cards(
    task_view,
    spotlights: tuple[DashboardCandidateSpotlight, ...],
) -> tuple[DashboardCandidateCard, ...]:
    cards = list(getattr(task_view, "detail_cards", ()) or ())
    existing_symbols = {card.symbol for card in cards}
    for spotlight in spotlights:
        if spotlight.symbol in existing_symbols:
            continue
        cards.append(_spotlight_as_candidate_card(spotlight))
        existing_symbols.add(spotlight.symbol)
    return tuple(cards)


def _home_blocked_focus_card(
    blocked_cards: tuple[DashboardCandidateCard, ...],
) -> DashboardCandidateCard | None:
    return next(
        (card for card in blocked_cards if _card_primary_blocker(card)),
        blocked_cards[0] if blocked_cards else None,
    )


def _home_primary_focus_card(
    recommend_cards: tuple[DashboardCandidateCard, ...],
    watch_cards: tuple[DashboardCandidateCard, ...],
    blocked_cards: tuple[DashboardCandidateCard, ...],
) -> DashboardCandidateCard | None:
    if recommend_cards:
        return recommend_cards[0]
    if watch_cards:
        return watch_cards[0]
    return _home_blocked_focus_card(blocked_cards)


def _debate_cross_market_line(
    debate_summary: DashboardDebateSummary,
) -> str:
    return debate_summary_cross_market_line(debate_summary)


def _cross_market_chain_line(
    *,
    spotlight: DashboardCandidateSpotlight | None = None,
    debate_summary: DashboardDebateSummary | None = None,
) -> str:
    return debate_summary_chain_line(
        debate_summary,
        spotlight=spotlight,
    )


def _debate_conclusion_summary(
    debate_summary: DashboardDebateSummary | None,
    *,
    spotlight: DashboardCandidateSpotlight | None = None,
    focus_card: DashboardCandidateCard | None = None,
) -> DashboardDebateConclusion:
    fallback_verdict = (
        _action_status_label(
            focus_card.action_label,
            focus_card.status_label,
        )
        if focus_card is not None
        else ""
    )
    return build_debate_conclusion(
        debate_summary,
        spotlight=spotlight,
        fallback_verdict=fallback_verdict,
    )


def _home_focus_discussion_lines(
    *,
    spotlight: DashboardCandidateSpotlight | None = None,
    debate_summary: DashboardDebateSummary | None = None,
) -> tuple[str, ...]:
    debate_cross_market_line = (
        _debate_cross_market_line(debate_summary) if debate_summary is not None else ""
    )
    if spotlight is not None:
        lines = (
            (
                debate_cross_market_line
                or _focus_cross_market_digest_line(
                    selected_spotlight=spotlight,
                    focus_display=spotlight.display_name,
                )
            ),
            (
                f"讨论支持: {spotlight.support_points[0]}"
                if spotlight.support_points
                else ""
            ),
            (
                f"讨论反对: {spotlight.opposition_points[0]}"
                if spotlight.opposition_points
                else ""
            ),
            (
                f"讨论待确认: {spotlight.watch_items[0]}"
                if spotlight.watch_items
                else ""
            ),
            _cross_market_chain_line(spotlight=spotlight),
        )
        if any(lines):
            return _unique_lines(lines)

    if debate_summary is not None:
        lines = (
            debate_cross_market_line
            or _focus_cross_market_digest_line(
                debate_summary=debate_summary,
                focus_display=debate_summary.display_name,
            ),
            (
                f"讨论支持: {debate_summary.support_points[0]}"
                if debate_summary.support_points
                else ""
            ),
            (
                f"讨论反对: {debate_summary.opposition_points[0]}"
                if debate_summary.opposition_points
                else ""
            ),
            (
                f"讨论待确认: {debate_summary.watch_items[0]}"
                if debate_summary.watch_items
                else ""
            ),
            _cross_market_chain_line(debate_summary=debate_summary),
        )
        if any(lines):
            return tuple(line for line in lines if line)

    return ()


def _home_focus_conclusion_lines(
    *,
    focus_card: DashboardCandidateCard | None = None,
    spotlight: DashboardCandidateSpotlight | None = None,
    debate_summary: DashboardDebateSummary | None = None,
) -> tuple[str, ...]:
    if debate_summary is None:
        return _home_focus_discussion_lines(
            spotlight=spotlight,
            debate_summary=debate_summary,
        )
    conclusion = _debate_conclusion_summary(
        debate_summary,
        spotlight=spotlight,
        focus_card=focus_card,
    )
    compact_cross_market_line = _focus_cross_market_digest_line(
        debate_summary=debate_summary,
        focus_display=(
            focus_card.display_name
            if focus_card is not None
            else (
                spotlight.display_name
                if spotlight is not None
                else debate_summary.display_name
            )
        ),
    )
    return _unique_lines(
        (
            conclusion.decision_line,
            compact_cross_market_line or conclusion.cross_market_line,
            _compact_debate_followup_line(debate_summary, conclusion=conclusion),
        )
    )


def _compact_debate_followup_line(
    debate_summary: DashboardDebateSummary,
    *,
    conclusion: DashboardDebateConclusion | None = None,
) -> str:
    conclusion = conclusion or _debate_conclusion_summary(debate_summary)
    if debate_summary.next_trigger.strip():
        return f"下一触发: {debate_summary.next_trigger.strip()}"
    if conclusion.watch_line:
        return conclusion.watch_line
    if conclusion.support_line:
        return conclusion.support_line
    if conclusion.opposition_line:
        return conclusion.opposition_line
    return ""


def _home_action_item_lines(card: DashboardCandidateCard) -> tuple[str, ...]:
    verdict = _action_status_label(card.action_label, card.status_label)
    blocker = _card_primary_blocker(card)
    next_action = _card_next_action(card)
    decision_note = _safe_current_research_line(card.decision_note)
    lines = [f"当前结论: {verdict}"]

    if blocker:
        lines.append(f"当前卡点: {blocker}")
    elif decision_note and decision_note not in {
        verdict,
        next_action,
        "按当前顺位继续跟踪",
    }:
        lines.append(f"主线判断: {decision_note}")

    if next_action != "-":
        lines.append(f"下一步: {next_action}")

    review_line = _review_meta_line("复核窗口", card.review_meta)
    if review_line:
        lines.append(review_line)
    else:
        lines.append(f"来源: {_review_source_label(card)}")

    return _unique_lines(tuple(line for line in lines if line))


def _singleton_lines(text: str) -> tuple[str, ...]:
    return (text,)


def _home_debate_item_lines(debate_summary: DashboardDebateSummary) -> tuple[str, ...]:
    conclusion = _debate_conclusion_summary(debate_summary)
    compact_cross_market_line = _focus_cross_market_digest_line(
        debate_summary=debate_summary,
        focus_display=debate_summary.display_name,
    ).replace(f" | 先看 {debate_summary.display_name}", "", 1)
    followup_line = _debate_watch_focus_line(debate_summary)
    if followup_line.startswith("下一触发: "):
        followup_line = followup_line.replace("下一触发: ", "触发 ", 1)
    elif followup_line.startswith("确认信号: "):
        followup_line = followup_line.replace("确认信号: ", "确认 ", 1)
    elif followup_line.startswith("失效信号: "):
        followup_line = followup_line.replace("失效信号: ", "失效 ", 1)
    if compact_cross_market_line:
        if followup_line and followup_line in compact_cross_market_line:
            followup_line = ""
        if not followup_line and conclusion.watch_line:
            followup_line = conclusion.watch_line.replace("讨论待确认: ", "待确认 ")
        return _unique_lines(
            (
                conclusion.decision_line,
                compact_cross_market_line,
                followup_line or conclusion.opposition_line or conclusion.support_line,
            )
        )[:3]
    lead_view_line = (
        _debate_top_view_line(
            debate_summary,
            stance="bullish",
            prefix="支持方",
        )
        or _debate_top_view_line(
            debate_summary,
            stance="neutral",
            prefix="主导视角",
        )
        or _focus_cross_market_digest_line(
            debate_summary=debate_summary,
            focus_display=debate_summary.display_name,
        )
        or (
            f"分歧焦点: {debate_summary.adjustment_reason}"
            if debate_summary.adjustment_reason
            else ""
        )
    )
    counter_view_line = (
        _debate_top_view_line(
            debate_summary,
            stance="bearish",
            prefix="反对方",
        )
        or _debate_watch_focus_line(debate_summary)
        or _compact_debate_followup_line(debate_summary, conclusion=conclusion)
    )
    return _unique_lines(
        (
            conclusion.decision_line,
            lead_view_line,
            counter_view_line,
            (
                f"分歧焦点: {debate_summary.adjustment_reason}"
                if debate_summary.adjustment_reason
                else ""
            ),
        )
    )[:3]


def _home_action_rail_items(
    task_view,
    spotlights: tuple[DashboardCandidateSpotlight, ...],
    debates: tuple[DashboardDebateSummary, ...] = (),
) -> tuple[_HomeActionRailItem, ...]:
    merged_cards = _home_action_cards(task_view, spotlights)
    recommend_cards, watch_cards, blocked_cards = _classify_candidate_queues(
        merged_cards
    )
    blocked_focus = _home_blocked_focus_card(blocked_cards)
    signal_date = _task_view_signal_date(task_view)
    task_id = str(getattr(task_view, "task_id", "") or "").strip()
    task_label = str(getattr(task_view, "task_label", "") or "").strip()
    recommend_source = (
        _candidate_card_source_key(recommend_cards[0]) if recommend_cards else ""
    )
    watch_source = _candidate_card_source_key(watch_cards[0]) if watch_cards else ""
    blocked_source = (
        _candidate_card_source_key(blocked_focus) if blocked_focus is not None else ""
    )
    rail_items: list[_HomeActionRailItem] = [
        _HomeActionRailItem(
            lane_id="recommend",
            lane_label="今日先看",
            tone="focus",
            button_label="去复盘",
            target_workspace="候选复盘",
            card=recommend_cards[0] if recommend_cards else None,
            summary=(
                recommend_cards[0].display_name if recommend_cards else "等待主链信号"
            ),
            lines=(
                _home_action_item_lines(recommend_cards[0])
                if recommend_cards
                else _home_recommend_fallback_lines(
                    task_view=task_view,
                    blocked_focus=blocked_focus,
                )
            ),
            signal_date=signal_date,
            task_id=task_id if recommend_source == "card" else "",
            task_label=task_label if recommend_source == "card" else "",
            focus_kind=recommend_source,
            decision_source=recommend_source,
            visible=bool(recommend_cards or task_view.recommendation_lines),
        ),
        _HomeActionRailItem(
            lane_id="watch",
            lane_label="继续观察",
            tone="archive",
            button_label="归档回看",
            target_workspace="归档回看",
            card=watch_cards[0] if watch_cards else None,
            summary=(watch_cards[0].display_name if watch_cards else "无需硬找方向"),
            lines=(
                _home_action_item_lines(watch_cards[0])
                if watch_cards
                else _home_watch_fallback_lines(
                    task_view=task_view,
                    blocked_focus=blocked_focus,
                )
            ),
            signal_date=signal_date,
            task_id=task_id if watch_source == "card" else "",
            task_label=task_label if watch_source == "card" else "",
            focus_kind=watch_source,
            decision_source=watch_source,
            visible=bool(
                watch_cards or task_view.watchlist_lines or task_view.review_lines
            ),
        ),
        _HomeActionRailItem(
            lane_id="blocked",
            lane_label="先解卡点",
            tone="blocked",
            button_label="查看卡点",
            target_workspace="候选复盘",
            card=blocked_focus,
            summary=(
                blocked_focus.display_name
                if blocked_focus is not None
                else "当前没有明显卡点"
            ),
            lines=(
                _home_action_item_lines(blocked_focus)
                if blocked_focus is not None
                else _singleton_lines(
                    task_view.blocker_lines[0]
                    if task_view.blocker_lines
                    else "当前节奏相对顺畅，可按候选优先级推进。"
                )
            ),
            signal_date=signal_date,
            task_id=task_id if blocked_source == "card" else "",
            task_label=task_label if blocked_source == "card" else "",
            focus_kind=blocked_source,
            decision_source=blocked_source,
            visible=bool(blocked_focus is not None or task_view.blocker_lines),
        ),
    ]
    if debates and not any(item.visible for item in rail_items):
        debate_focus = _ordered_home_debates(debates)[0]
        rail_items.insert(
            2,
            _HomeActionRailItem(
                lane_id="debate",
                lane_label="委员会分歧",
                tone="pressure"
                if debate_focus.disagreement_score >= 0.35
                else "archive",
                button_label="候选复盘",
                target_workspace="候选复盘",
                card=_debate_as_candidate_card(debate_focus),
                summary=debate_focus.display_name,
                lines=_home_debate_item_lines(debate_focus),
                signal_date=debate_focus.signal_date,
                focus_kind="debate",
                debate_id=debate_focus.debate_id,
                decision_source="debate",
                visible=True,
            ),
        )
    return tuple(rail_items)


def _queue_home_action_rail_handoff(item: _HomeActionRailItem) -> None:
    if item.card is None:
        return
    _queue_workspace_handoff(
        target_workspace=item.target_workspace,
        source_workspace="决策首页",
        symbol=item.card.symbol,
        signal_date=item.signal_date,
        task_id=item.task_id,
        task_label=item.task_label,
        focus_kind=item.focus_kind,
        debate_id=item.debate_id,
        decision_source=item.decision_source,
        title=f"带着{item.lane_label}结论去看{item.target_workspace}",
        lines=item.lines,
    )


def _queue_home_spotlight_handoff(
    *,
    workspace: str,
    spotlight: DashboardCandidateSpotlight,
    signal_date: str,
) -> None:
    _queue_workspace_handoff(
        target_workspace=workspace,
        source_workspace="决策首页",
        symbol=spotlight.symbol,
        signal_date=signal_date,
        focus_kind="spotlight",
        decision_source="spotlight",
        title=f"带着同日联动结论去看{workspace}",
        lines=_home_spotlight_lines(spotlight),
    )


def _queue_home_debate_handoff(
    *,
    debate_summary: DashboardDebateSummary,
    title: str,
    lines: tuple[str, ...],
) -> None:
    _queue_workspace_handoff(
        target_workspace="候选复盘",
        source_workspace="决策首页",
        symbol=debate_summary.symbol,
        signal_date=debate_summary.signal_date,
        focus_kind="debate",
        debate_id=debate_summary.debate_id,
        decision_source="debate",
        title=title,
        lines=lines,
    )


def _render_home_action_rail(
    task_view,
    spotlights: tuple[DashboardCandidateSpotlight, ...],
    debates: tuple[DashboardDebateSummary, ...] = (),
    *,
    show_heading: bool = True,
    layout: str = "row",
) -> None:
    rail_items = tuple(
        item
        for item in _home_action_rail_items(task_view, spotlights, debates)
        if item.visible
    )
    if not rail_items:
        return
    if show_heading:
        st.divider()
        st.subheader("今日动作")
    if layout == "stack":
        for item in rail_items:
            _render_cockpit_card(
                kicker=item.lane_label,
                title=item.summary,
                lines=item.lines,
                tone=item.tone,
            )
            if item.card is not None and _stretch_button(
                item.button_label,
                key=f"home-action-rail-{item.lane_id}-{item.card.symbol}",
            ):
                _queue_home_action_rail_handoff(item)
                st.rerun()
        return
    columns = st.columns(len(rail_items))
    for column, item in zip(columns, rail_items):
        with column:
            _render_cockpit_card(
                kicker=item.lane_label,
                title=item.summary,
                lines=item.lines,
                tone=item.tone,
            )
            if item.card is not None and _stretch_button(
                item.button_label,
                key=f"home-action-rail-{item.lane_id}-{item.card.symbol}",
            ):
                _queue_home_action_rail_handoff(item)
                st.rerun()


def _render_lifecycle_overview(task_view) -> None:
    lifecycle_col, unlock_col = st.columns(2)
    with lifecycle_col:
        _render_line_block(
            "候选生命周期",
            task_view.lifecycle_lines,
            "当前日期还没有候选生命周期，先确认主链是否产出候选。",
        )
    with unlock_col:
        _render_line_block(
            "卡点提示",
            task_view.unlock_lines,
            "当前日期没有额外卡点提示，按候选优先级正常回看即可。",
        )


def _render_history_overview(task_view, provider: DashboardDataProvider) -> None:
    history_col, delta_col = st.columns(2)
    with history_col:
        _render_frame(
            "任务历史",
            provider.task_history_frame(task_view.task_id, limit=8),
        )
    with delta_col:
        _render_line_block(
            "较前日变化",
            task_view.delta_lines,
            "当前日期还没有可对比的上一交易日，先看当日结果。",
        )


def _render_timeline_overview(task_view, provider: DashboardDataProvider) -> None:
    selected_date = task_view.selected_date or task_view.latest_date
    with st.expander("查看原始时间线与对照表", expanded=False):
        timeline_col, compare_col = st.columns(2)
        with timeline_col:
            _render_frame("日期时间线", provider.timeline_frame(limit=12))
        with compare_col:
            _render_frame("同日任务对照", provider.same_day_task_frame(selected_date))


def _render_operation_overview(overview: DashboardDateOverview) -> None:
    if not overview.signal_date:
        return
    overview_col, focus_col, blocker_col = st.columns(3)
    with overview_col:
        st.markdown(
            f"""
            <div class="aqsp-ops-card">
              <div class="aqsp-ops-kicker">日期总览</div>
              <div class="aqsp-ops-title">{overview.signal_date}</div>
              <div class="aqsp-ops-summary">
                涉及任务 {overview.task_count} 个<br/>
                待复核 {overview.actionable_total} / 观察 {overview.watch_total} / 阻塞 {overview.blocked_total}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with focus_col:
        st.markdown(
            f"""
            <div class="aqsp-ops-card">
              <div class="aqsp-ops-kicker">优先入口</div>
              <div class="aqsp-ops-title">{overview.top_task_label or "暂无主线"}</div>
              <div class="aqsp-ops-summary">{overview.focus_headline or overview.top_headline or "当前无明显优先入口。"}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with blocker_col:
        st.markdown(
            f"""
            <div class="aqsp-ops-card">
              <div class="aqsp-ops-kicker">主要阻塞</div>
              <div class="aqsp-ops-title">{"优先核对" if overview.blocked_total else "阻塞较轻"}</div>
              <div class="aqsp-ops-summary">{overview.blocker_headline or overview.archive_summary or "当前没有明显阻塞任务，可按优先入口推进。"}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _home_workspace_hint(
    task_view,
    overview: DashboardDateOverview,
    paper_summary: DashboardPaperSummary,
) -> tuple[str, str, str]:
    if paper_summary.pending_entries or paper_summary.not_executable:
        return (
            "先看虚拟盘跟踪",
            _safe_paper_summary_detail(
                paper_summary,
                fallback="当前纸面验证已有事件，先看入场假设和不可成交处理。",
            ),
            "pressure",
        )
    if _report_archive_status(task_view) != "无归档":
        return (
            "先去归档回看",
            _safe_research_hint_line(
                task_view.next_day_focus_lines[0]
                if task_view.next_day_focus_lines
                else (
                    overview.archive_summary
                    or "当前归档已生成，先看回看结论与后续关注。"
                )
            ),
            "archive",
        )
    detail_cards = getattr(task_view, "detail_cards", ())
    blocked_focus = _home_blocked_focus_card(
        _classify_candidate_queues(detail_cards)[2]
    )
    if blocked_focus is not None:
        blocker = _card_primary_blocker(blocked_focus)
        return (
            "先去候选复盘",
            (
                f"先复盘 {blocked_focus.display_name} 的阻塞卡点“{blocker}”，再决定是否恢复推进。"
                if blocker
                else f"先复盘 {blocked_focus.display_name} 的卡点条件，再决定是否恢复推进。"
            ),
            "focus",
        )
    if paper_summary.open_positions:
        return (
            "先看虚拟盘跟踪",
            _safe_paper_summary_detail(
                paper_summary,
                fallback="当前有纸面持有假设，先核对最多亏到、先看目标与退出条件。",
            ),
            "focus",
        )
    return (
        "先去候选复盘",
        (
            _safe_research_hint_line(
                task_view.review_lines[0]
                if task_view.review_lines
                else (
                    task_view.recommendation_lines[0]
                    if task_view.recommendation_lines
                    else "当前仍以研究结论为主，先回看候选的来龙去脉。"
                )
            )
        ),
        "focus",
    )


def _safe_paper_summary_detail(
    paper_summary: DashboardPaperSummary,
    *,
    fallback: str,
) -> str:
    return _day_replay_paper_detail(paper_summary.action_summary_lines) or fallback


def _safe_research_hint_line(line: str) -> str:
    return _sanitize_day_replay_line(line)


def _render_home_navigation_summary(
    task_view,
    overview: DashboardDateOverview,
    paper_summary: DashboardPaperSummary,
) -> None:
    recommend_cards, watch_cards, blocked_cards = _classify_candidate_queues(
        task_view.detail_cards
    )
    blocked_focus = _home_blocked_focus_card(blocked_cards)
    focus_title = "当前无显著主推候选"
    focus_body = "当前日期没有进入优先推进的候选，先看观察对象和阻塞原因。"
    if task_view.recommendation_lines:
        focus_title = "优先推进"
        focus_body = task_view.recommendation_lines[0]
    elif task_view.watchlist_lines:
        focus_title = "继续观察"
        focus_body = task_view.watchlist_lines[0]
    elif task_view.blocker_lines:
        focus_title = "先核对卡点"
        focus_body = (
            _symbol_focus_entry(blocked_focus)
            if blocked_focus is not None
            else task_view.blocker_lines[0]
        )
    cols = st.columns(2)
    cards = (
        (
            "全局",
            overview.signal_date,
            escape(
                f"覆盖 {overview.task_count} 个任务 / 待复核 {overview.actionable_total} / 观察 {overview.watch_total} / 阻塞 {overview.blocked_total}"
            ),
            "archive",
        ),
        (
            "入口",
            focus_title,
            escape(focus_body),
            "focus",
        ),
    )
    for column, (kicker, title, body, tone) in zip(cols, cards):
        with column:
            st.markdown(
                f"""
                <div class="aqsp-cockpit-card {tone}">
                  <div class="aqsp-cockpit-kicker">{escape(kicker)}</div>
                  <div class="aqsp-cockpit-title">{escape(title)}</div>
                  <div class="aqsp-cockpit-body">{body}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _render_daily_workflow(
    rows: tuple[DashboardSameDayTaskRow, ...],
    current_task_id: str,
    overview: DashboardDateOverview,
    *,
    show_heading: bool = True,
) -> None:
    if not rows:
        return
    if show_heading:
        st.subheader("今天走到哪一步")
    visible_rows = rows[:3]
    for start in range(0, len(visible_rows), 3):
        columns = st.columns(min(len(visible_rows[start : start + 3]), 3))
        for column, row in zip(columns, visible_rows[start : start + 3]):
            is_active = row.task_id == current_task_id
            action_label, watch_label, blocked_label = _task_metric_labels(row.task_id)
            with column:
                st.markdown(
                    f"""
                    <div class="aqsp-workflow-card {"active" if is_active else ""}">
                      <div class="aqsp-workflow-phase">{escape(row.phase_label)}</div>
                      <div class="aqsp-day-header">
                        <div class="aqsp-workflow-title">{escape(row.task_label)}</div>
                      </div>
                      <div class="aqsp-day-metrics">
                        <div class="aqsp-day-metric">{action_label} {row.actionable_count}</div>
                        <div class="aqsp-day-metric">{watch_label} {row.watch_count}</div>
                        <div class="aqsp-day-metric">{blocked_label} {row.blocked_count}</div>
                      </div>
                      <div class="aqsp-day-summary">{escape(row.headline)}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if not is_active and _stretch_button(
                    "切到这段",
                    key=f"workflow-task-{row.task_id}-{row.signal_date}",
                ):
                    _queue_home_selection_handoff(
                        signal_date=row.signal_date,
                        task_id=row.task_id,
                        task_label=row.task_label,
                        title=f"切到 {row.phase_label} 看这段结论",
                        lines=(
                            f"切到这段先看: {row.headline or row.phase_summary or row.task_label}",
                        ),
                    )
                    st.rerun()
    hidden_rows = rows[3:]
    if hidden_rows:
        with st.expander(f"看更多段 ({len(hidden_rows)})", expanded=False):
            extra_columns = st.columns(min(len(hidden_rows), 3))
            for column, row in zip(extra_columns, hidden_rows):
                action_label, watch_label, blocked_label = _task_metric_labels(
                    row.task_id
                )
                with column:
                    st.markdown(
                        f"""
                        <div class="aqsp-day-card">
                          <div class="aqsp-day-header">
                            <div>
                              <div class="aqsp-day-title">{row.task_label}</div>
                              <div class="aqsp-date-meta">{row.phase_label}</div>
                            </div>
                          </div>
                          <div class="aqsp-day-metrics">
                            <div class="aqsp-day-metric">{action_label} {row.actionable_count}</div>
                            <div class="aqsp-day-metric">{watch_label} {row.watch_count}</div>
                            <div class="aqsp-day-metric">{blocked_label} {row.blocked_count}</div>
                          </div>
                          <div class="aqsp-day-summary">{row.phase_summary}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    if _stretch_button(
                        "切到这段",
                        key=f"workflow-extra-task-{row.task_id}-{row.signal_date}",
                    ):
                        _queue_home_selection_handoff(
                            signal_date=row.signal_date,
                            task_id=row.task_id,
                            task_label=row.task_label,
                            title=f"切到 {row.phase_label} 看这段结论",
                            lines=(
                                f"切到这段先看: {row.headline or row.phase_summary or row.task_label}",
                            ),
                        )
                        st.rerun()


def _render_day_archive_summary(overview: DashboardDateOverview) -> None:
    if not overview.signal_date or not overview.archive_summary:
        return
    st.markdown(
        f"""
        <div class="aqsp-status-strip">
          <div class="aqsp-status-label">当日归档</div>
          <div class="aqsp-status-main">{escape(overview.signal_date)} 回看摘要</div>
          <div class="aqsp-status-sub">{escape(_safe_archive_line(overview.archive_summary))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_trading_cockpit(
    task_view,
    overview: DashboardDateOverview,
    paper_summary: DashboardPaperSummary,
) -> None:
    recommend_cards, watch_cards, blocked_cards = _classify_candidate_queues(
        task_view.detail_cards
    )
    focus_card = _home_primary_focus_card(
        recommend_cards,
        watch_cards,
        blocked_cards,
    )
    focus_title = focus_card.display_name if focus_card is not None else "暂无主推候选"
    focus_body = (
        "<br/>".join(
            line
            for line in [
                f"当前结论: {escape(_action_status_label(focus_card.action_label, focus_card.status_label))}",
                (
                    f"评分 {focus_card.score:.1f} / 复核 {escape(focus_card.review_meta)}"
                    if _has_review_meta(focus_card.review_meta)
                    else f"评分 {focus_card.score:.1f}"
                ),
                escape(
                    _safe_current_research_line(
                        focus_card.next_step
                        or focus_card.decision_note
                        or "当前无额外推进动作。"
                    )
                ),
            ]
            if line
        )
        if focus_card is not None
        else "当前没有进入优先顺位的候选。"
    )

    pressure_title = "纸面压力可控"
    pressure_lines = [
        f"纸面持有 {paper_summary.open_positions} / 入场待核对 {paper_summary.pending_entries}",
        f"阻塞 {paper_summary.not_executable} / 纸面关闭 {paper_summary.closed_trades}",
    ]
    if paper_summary.pending_entries or paper_summary.not_executable:
        pressure_title = "纸面事件需要优先处理"
        pressure_lines.append(
            escape(
                _safe_paper_summary_detail(
                    paper_summary,
                    fallback="先处理纸面假设与不可成交事件。",
                )
            )
        )
    elif paper_summary.open_positions:
        pressure_lines.append("当前以纸面持有假设、最多亏到和先看目标回看为主。")
    else:
        pressure_lines.append("当前纸面侧较轻，可优先回到研究判断。")

    blocker_title = "无明显阻塞"
    blocker_body = "当前没有显著阻塞项，可按优先顺位推进。"
    if blocked_cards:
        blocker_title = blocked_cards[0].display_name
        blocker_body = "<br/>".join(
            [
                escape(
                    _safe_current_research_line(
                        _card_primary_blocker(blocked_cards[0]) or "存在待解除阻塞"
                    )
                ),
                escape(
                    _safe_current_research_line(_card_next_action(blocked_cards[0]))
                ),
            ]
        )
    elif overview.blocker_headline:
        blocker_title = "阶段阻塞"
        blocker_body = escape(overview.blocker_headline)

    archive_title = f"{review_date_label(task_view)} 归档"
    archive_body = "<br/>".join(
        [
            escape(_report_archive_status(task_view)),
            escape(
                _safe_research_hint_line(task_view.report_summary_lines[0])
                if task_view.report_summary_lines
                else (overview.archive_summary or "当前还没有结构化归档摘要。")
            ),
            escape(
                _safe_research_hint_line(task_view.next_day_focus_lines[0])
                if task_view.next_day_focus_lines
                else "当前暂无额外次日重点。"
            ),
        ]
    )

    cols = st.columns(4)
    cards = (
        ("焦点候选", focus_title, focus_body, "focus"),
        ("纸面压力", pressure_title, "<br/>".join(pressure_lines), "pressure"),
        ("主要阻塞", blocker_title, blocker_body, "blocked"),
        ("归档状态", archive_title, archive_body, "archive"),
    )
    for column, (kicker, title, body, tone) in zip(cols, cards):
        with column:
            st.markdown(
                f"""
                <div class="aqsp-cockpit-card {tone}">
                  <div class="aqsp-cockpit-kicker">{escape(kicker)}</div>
                  <div class="aqsp-cockpit-title">{escape(title)}</div>
                  <div class="aqsp-cockpit-body">{body}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def review_date_label(task_view) -> str:
    return task_view.selected_date or task_view.latest_date or "-"


def _html_line_breaks(lines: tuple[str, ...], fallback: str) -> str:
    cleaned = [escape(line) for line in lines if line.strip()]
    if not cleaned:
        return escape(fallback)
    return "<br/>".join(cleaned)


def _command_center_brief_lines(
    *,
    task_view,
    summary_lines: tuple[str, ...],
) -> tuple[str, ...]:
    is_archived = _report_archive_status(task_view) != "无归档"
    label = "历史报告摘要" if is_archived else "研究摘要"
    source_lines = (
        task_view.report_summary_lines[:2]
        if is_archived and task_view.report_summary_lines
        else summary_lines[:2]
    )
    brief_lines = (
        tuple(_neutral_archive_summary_line(line) for line in source_lines)
        if is_archived
        else source_lines
    )
    return tuple(f"{label}: {line}" for line in brief_lines if line)


def _neutral_archive_summary_line(line: str) -> str:
    clean = sanitize_archive_text(re.sub(r"\*\*(.*?)\*\*", r"\1", line).strip())
    replacements = (
        (r"^[🎯⭐]\s*首选\s*[:：]\s*", "历史首选记录: "),
        (r"^[❌🚫]\s*移出候选\s*[:：]\s*", "历史移出记录: "),
        (r"^🆕\s*新晋候选\s*[:：]\s*", "历史新晋记录: "),
        (r"^📈\s*排名异动\s*[:：]\s*", "历史排名变化: "),
        (r"^📊\s*评分变化\s*[:：]\s*", "历史评分变化: "),
        (r"^✅\s*维持候选\s*[:：]\s*", "历史维持记录: "),
    )
    for pattern, replacement in replacements:
        clean = re.sub(pattern, replacement, clean)
    return clean


_DAY_REPLAY_SAFE_KEYWORDS = (
    "待核对",
    "不可成交",
    "阻塞",
    "等待",
    "纸面入场",
    "纸面退出",
    "纸面持有",
    "纸面验证",
)


def _sanitize_day_replay_line(line: str) -> str:
    clean = sanitize_research_text(re.sub(r"\*\*(.*?)\*\*", r"\1", line).strip())
    clean = re.sub(
        r"\bBUY\b\s*[\d,.]*(?:\s*@\s*[\d,.]+)?",
        "纸面入场记录已回写",
        clean,
        flags=re.IGNORECASE,
    )
    clean = re.sub(
        r"\bSELL\b\s*[\d,.]*(?:\s*@\s*[\d,.]+)?",
        "纸面退出记录已回写",
        clean,
        flags=re.IGNORECASE,
    )
    replacements = (
        ("重点跟踪线索", "复核线索"),
        ("跟踪优先级", "复核顺位"),
        ("重点跟踪名单", "复核名单"),
        ("重点跟踪对象", "复核对象"),
        ("今日重点名单", "当日复核名单"),
        ("纸面复核优先级", "复核顺位"),
        ("执行顺位", "复核顺位"),
        ("执行顺序", "复核顺序"),
        ("执行名单", "复核名单"),
        ("执行", "复核"),
        ("新开仓", "纸面新建观察"),
        ("开仓", "纸面观察"),
        ("下单", "纸面记录"),
        ("买入", "纸面入场记录"),
        ("卖出", "纸面退出记录"),
    )
    for source, replacement in replacements:
        clean = clean.replace(source, replacement)
    return clean


def _day_replay_paper_detail(lines: tuple[str, ...]) -> str:
    safe_lines = tuple(_sanitize_day_replay_line(line) for line in lines if line)
    for line in safe_lines:
        if any(keyword in line for keyword in _DAY_REPLAY_SAFE_KEYWORDS[:4]):
            return line
    for line in safe_lines:
        if (
            any(keyword in line for keyword in _DAY_REPLAY_SAFE_KEYWORDS)
            and "回写" not in line
        ):
            return line
    return safe_lines[0] if safe_lines else ""


def _day_replay_next_step_line(
    *,
    task_view,
    overview: DashboardDateOverview,
    paper_summary: DashboardPaperSummary,
) -> str:
    if paper_summary.pending_entries or paper_summary.not_executable:
        detail = _day_replay_paper_detail(paper_summary.action_summary_lines)
        if not detail:
            detail = (
                f"待核对 {paper_summary.pending_entries} / "
                f"阻塞 {paper_summary.not_executable}"
            )
        return f"🧪 复核提示: 纸面验证记录待核对，{detail}"
    if overview.blocked_total:
        blocker = _sanitize_day_replay_line(
            overview.blocker_headline or "回到候选复盘核对卡点。"
        )
        return f"⚠️ 阻塞提示: 待核对卡点，{blocker}"
    if task_view.next_day_focus_lines:
        return (
            "📚 归档回看: 原报告下一交易日重点，"
            f"{_sanitize_day_replay_line(task_view.next_day_focus_lines[0])}"
        )
    if task_view.review_lines:
        return f"🧭 复核线索: {_sanitize_day_replay_line(task_view.review_lines[0])}"
    return "🧭 候选复盘: 暂不形成纸面验证对象。"


def _day_replay_digest_lines(
    *,
    task_view,
    overview: DashboardDateOverview,
    paper_summary: DashboardPaperSummary,
    same_day_rows: tuple[DashboardSameDayTaskRow, ...],
) -> tuple[str, ...]:
    phase_text = " → ".join(row.phase_label for row in same_day_rows[:5])
    if not phase_text:
        phase_text = overview.workflow_summary.replace("当日流程:", "").strip()
    if not phase_text:
        phase_text = task_view.task_label

    conclusion = _join_display_parts(
        "📍 当日结论",
        f"{overview.actionable_total} 待复核",
        f"{overview.watch_total} 观察",
        f"{overview.blocked_total} 阻塞",
    )
    workflow = _join_display_parts(
        "🧩 任务回放",
        phase_text,
        task_view.task_label if phase_text != task_view.task_label else "",
    )
    next_step = _day_replay_next_step_line(
        task_view=task_view,
        overview=overview,
        paper_summary=paper_summary,
    )
    archive = _join_display_parts(
        "🗂 全日覆盖",
        _report_archive_status(task_view),
        _safe_archive_line(overview.archive_summary),
    )
    return _unique_lines((conclusion, workflow, next_step, archive))


def _render_day_replay_digest(
    *,
    task_view,
    overview: DashboardDateOverview,
    paper_summary: DashboardPaperSummary,
    same_day_rows: tuple[DashboardSameDayTaskRow, ...],
) -> None:
    lines = _day_replay_digest_lines(
        task_view=task_view,
        overview=overview,
        paper_summary=paper_summary,
        same_day_rows=same_day_rows,
    )
    st.markdown(
        "\n".join(
            [
                '<div class="aqsp-replay-card">',
                '<div class="aqsp-replay-kicker">Day Replay</div>',
                f'<div class="aqsp-replay-title">{escape(review_date_label(task_view))} 一眼回放</div>',
                *[
                    f'<div class="aqsp-replay-line">{escape(line)}</div>'
                    for line in lines[:4]
                ],
                "</div>",
            ]
        ),
        unsafe_allow_html=True,
    )


def _task_message_summary(task_view) -> str:
    for line in (
        *task_view.report_summary_lines[:1],
        *task_view.summary_lines[:1],
        task_view.headline,
    ):
        if line and line.strip():
            return _safe_current_research_line(line)
    return "当前任务还没有可扫读摘要。"


def _same_day_message_tone(row: DashboardSameDayTaskRow) -> str:
    if row.task_id == "intraday":
        return "archive"
    if row.blocked_count > 0:
        return "blocked"
    if row.actionable_count > 0:
        return "focus"
    if row.watch_count > 0:
        return "pressure"
    return "archive"


def _same_day_debate_time_prefix(debate_summary: DashboardDebateSummary | None) -> str:
    if debate_summary is None:
        return ""
    raw = str(debate_summary.created_at or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return ""
    return f"{parsed.strftime('%H:%M')} 更新 | "


def _same_day_task_time_prefix(row: DashboardSameDayTaskRow | None) -> str:
    if row is None:
        return ""
    raw = str(row.created_at or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return ""
    return f"{parsed.strftime('%H:%M')} 更新 | "


def _same_day_time_sort_key(raw: str) -> int:
    normalized = raw.strip()
    if not normalized:
        return 0
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return 0
    return int(parsed.strftime("%Y%m%d%H%M%S"))


def _same_day_digest_debate_lines(
    debate_summary: DashboardDebateSummary,
) -> tuple[str, ...]:
    time_prefix = _same_day_debate_time_prefix(debate_summary)
    line = (
        "讨论结果: "
        + time_prefix
        + _timeline_debate_result_line(debate_summary).removeprefix("- ").strip()
    )
    return (line,) if line.strip() else ()


def _same_day_digest_events(
    *,
    ordered_rows: tuple[tuple[DashboardSameDayTaskRow, object], ...],
    task_limit: int,
    debates: tuple[DashboardDebateSummary, ...],
) -> tuple[_SameDayDigestEvent, ...]:
    events: list[_SameDayDigestEvent] = []
    for debate_summary in _salient_home_debates(debates)[:1]:
        debate_priority = _home_debate_priority_key(debate_summary)
        debate_lines = _same_day_digest_debate_lines(debate_summary)
        if debate_lines:
            events.append(
                _SameDayDigestEvent(
                    event_type="debate_result",
                    line=debate_lines[0],
                    time_key=str(debate_summary.created_at or ""),
                    type_priority=2,
                    content_priority=debate_priority,
                )
            )
    for row, task_view in ordered_rows[:task_limit]:
        events.append(
            _SameDayDigestEvent(
                event_type="task",
                line=_timeline_task_digest_line(task_view, row)
                .removeprefix("- ")
                .strip(),
                time_key=str(row.created_at or ""),
                type_priority=0,
                content_priority=_same_day_message_priority_key(task_view, row),
            )
        )

    return tuple(
        sorted(
            (event for event in events if event.line.strip()),
            key=lambda event: (
                int(bool(event.time_key)),
                event.time_key,
                event.type_priority,
                event.content_priority,
            ),
            reverse=True,
        )
    )


def _same_day_summary_row_priority_key(
    row: DashboardSameDayTaskRow,
) -> tuple[int, ...]:
    return (
        int(row.blocked_count > 0),
        int(row.actionable_count > 0),
        int(row.watch_count > 0),
        row.blocked_count,
        row.actionable_count,
        row.watch_count,
        _same_day_time_sort_key(str(row.created_at or "")),
        -row.phase_order,
    )


def _ordered_same_day_summary_rows(
    rows: tuple[DashboardSameDayTaskRow, ...],
) -> tuple[DashboardSameDayTaskRow, ...]:
    return tuple(sorted(rows, key=_same_day_summary_row_priority_key, reverse=True))


def _timeline_task_row_digest_line(row: DashboardSameDayTaskRow) -> str:
    time_prefix = _same_day_task_time_prefix(row)
    if row.blocked_count > 0:
        message = f"有 {row.blocked_count} 个阻塞对象待复核。"
    elif row.actionable_count > 0:
        message = f"有 {row.actionable_count} 个待复核对象已落盘。"
    elif row.watch_count > 0:
        message = f"有 {row.watch_count} 个观察对象待继续确认。"
    else:
        message = _clean_task_message_judgment(row.phase_summary) or (
            _clean_task_message_judgment(row.headline) or "阶段结论已落盘。"
        )
    return f"{row.task_label}: {time_prefix}{message}"


def _same_day_digest_summary_events(
    *,
    ordered_rows: tuple[DashboardSameDayTaskRow, ...],
    task_limit: int,
    debates: tuple[DashboardDebateSummary, ...],
) -> tuple[_SameDayDigestEvent, ...]:
    events: list[_SameDayDigestEvent] = []
    for debate_summary in _salient_home_debates(debates)[:1]:
        debate_priority = _home_debate_priority_key(debate_summary)
        debate_lines = _same_day_digest_debate_lines(debate_summary)
        if debate_lines:
            events.append(
                _SameDayDigestEvent(
                    event_type="debate_result",
                    line=debate_lines[0],
                    time_key=str(debate_summary.created_at or ""),
                    type_priority=2,
                    content_priority=debate_priority,
                )
            )

    for row in ordered_rows[:task_limit]:
        events.append(
            _SameDayDigestEvent(
                event_type="task",
                line=_timeline_task_row_digest_line(row),
                time_key=str(row.created_at or ""),
                type_priority=0,
                content_priority=_same_day_summary_row_priority_key(row),
            )
        )

    return tuple(
        sorted(
            (event for event in events if event.line.strip()),
            key=lambda event: (
                int(bool(event.time_key)),
                event.time_key,
                event.type_priority,
                event.content_priority,
            ),
            reverse=True,
        )
    )


def _same_day_summary_card_lines(
    *,
    overview: DashboardDateOverview,
    digest_lines: tuple[str, ...],
    debates: tuple[DashboardDebateSummary, ...],
    task_view,
) -> tuple[str, ...]:
    lead_debate = next(iter(_salient_home_debates(debates)), None)
    source_line = next(
        (line for line in digest_lines if line.startswith("数据链路: ")),
        "",
    )
    market_line = next(
        (line for line in digest_lines if line.startswith("跨市主线: ")),
        "",
    )
    if lead_debate is None:
        summary_lines = _same_day_digest_conclusion_lines(digest_lines) or digest_lines
        if source_line and any(
            marker in source_line for marker in ("只适合历史验证", "已降级")
        ):
            return _unique_lines((source_line,), summary_lines)[:4]
        return summary_lines[:4]

    conclusion = _debate_conclusion_summary(lead_debate)
    committee_line = (
        conclusion.decision_line.replace("研究口径: ", "委员会结论: ", 1).replace(
            "当前结论: ", "委员会结论: ", 1
        )
        if conclusion.decision_line
        else ""
    )
    time_prefix = _same_day_debate_time_prefix(lead_debate)
    primary_line = (
        f"{time_prefix}{committee_line}".strip()
        if committee_line
        else f"当前结论: {overview.focus_headline or overview.top_headline or task_view.headline}"
    )
    caution_source_line = (
        source_line
        if source_line
        and any(marker in source_line for marker in ("只适合历史验证", "已降级"))
        else ""
    )
    gate_line = (
        f"当前卡点: {lead_debate.primary_risk_gate}"
        if lead_debate.primary_risk_gate
        else (
            f"当前卡点: {overview.blocker_headline}"
            if overview.blocker_headline
            else ""
        )
    )
    trigger_line = (
        f"下一触发: {lead_debate.next_trigger}"
        if lead_debate.next_trigger
        else (
            conclusion.watch_line.replace("讨论待确认: ", "下一触发: ", 1)
            if conclusion.watch_line
            else ""
        )
    )
    return _unique_lines(
        (
            primary_line,
            market_line or conclusion.cross_market_line,
            caution_source_line,
            gate_line,
            trigger_line,
        )
    )[:4]


def _same_day_summary_focus_context(
    *,
    task_view,
    spotlights: tuple[DashboardCandidateSpotlight, ...],
    debates: tuple[DashboardDebateSummary, ...],
) -> tuple[
    DashboardCandidateCard | None,
    DashboardCandidateSpotlight | None,
    DashboardDebateSummary | None,
]:
    merged_cards = _home_action_cards(task_view, spotlights)
    recommend_cards, watch_cards, blocked_cards = _classify_candidate_queues(
        merged_cards
    )
    focus_card = _home_primary_focus_card(recommend_cards, watch_cards, blocked_cards)
    focus_symbol = focus_card.symbol if focus_card is not None else ""
    if not focus_symbol and debates:
        focus_symbol = _salient_home_debates(debates)[0].symbol
    if not focus_symbol:
        return (None, None, None)
    selected_card, selected_spotlight, debate_summary, _ = _review_context_for_symbol(
        symbol=focus_symbol,
        cards=tuple(getattr(task_view, "detail_cards", ()) or ()),
        spotlights=spotlights,
        debates=debates,
    )
    return (selected_card, selected_spotlight, debate_summary)


def _same_day_summary_handoff_lines(
    *,
    workspace: str,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    display_name = (
        selected_card.display_name
        if selected_card is not None
        else (
            selected_spotlight.display_name
            if selected_spotlight is not None
            else (debate_summary.display_name if debate_summary is not None else "")
        )
    )
    next_focus = ""
    if selected_card is not None:
        next_focus = _card_primary_blocker(selected_card) or _card_next_action(
            selected_card
        )
    if not next_focus and selected_spotlight is not None:
        next_focus = _safe_current_research_line(
            selected_spotlight.blocker or selected_spotlight.next_step
        )
    if not next_focus and debate_summary is not None:
        next_focus = (
            debate_summary.primary_risk_gate
            or debate_summary.next_trigger
            or debate_summary.adjustment_reason
        )
    prefix = {
        "候选复盘": "切到复盘先看",
        "虚拟盘跟踪": "切到纸面先看",
        "归档回看": "切到归档先看",
    }.get(workspace, "切到这里先看")
    return tuple(
        line
        for line in (
            (f"当前标的: {display_name}" if display_name else ""),
            _candidate_effective_decision_line(
                selected_card=selected_card,
                spotlight=selected_spotlight,
                debate_summary=debate_summary,
            ),
            (
                f"{prefix}: {next_focus}"
                if next_focus
                else f"{prefix}: 先核对当前结论、阻塞与下一触发。"
            ),
        )
        if line
    )


def _queue_same_day_summary_handoff(
    *,
    workspace: str,
    signal_date: str,
    task_view,
    spotlights: tuple[DashboardCandidateSpotlight, ...],
    debates: tuple[DashboardDebateSummary, ...],
) -> None:
    selected_card, selected_spotlight, debate_summary = _same_day_summary_focus_context(
        task_view=task_view,
        spotlights=spotlights,
        debates=debates,
    )
    source_key = _candidate_effective_decision_source_key(
        selected_card=selected_card,
        spotlight=selected_spotlight,
        debate_summary=debate_summary,
    )
    symbol = (
        selected_card.symbol
        if selected_card is not None
        else (
            selected_spotlight.symbol
            if selected_spotlight is not None
            else (debate_summary.symbol if debate_summary is not None else "")
        )
    )
    _queue_workspace_handoff(
        target_workspace=workspace,
        source_workspace="决策首页",
        symbol=symbol,
        signal_date=signal_date,
        task_id=str(getattr(task_view, "task_id", "") or ""),
        task_label=str(getattr(task_view, "task_label", "") or ""),
        focus_kind=source_key,
        debate_id=debate_summary.debate_id if debate_summary is not None else "",
        decision_source=source_key,
        title=f"带着当天总控去看{workspace}",
        lines=_same_day_summary_handoff_lines(
            workspace=workspace,
            selected_card=selected_card,
            selected_spotlight=selected_spotlight,
            debate_summary=debate_summary,
        ),
    )


def _render_same_day_message_digest(
    *,
    provider: DashboardDataProvider,
    signal_date: str,
    rows: tuple[DashboardSameDayTaskRow, ...],
    task_view,
    overview: DashboardDateOverview,
    paper_summary: DashboardPaperSummary,
    spotlights: tuple[DashboardCandidateSpotlight, ...],
    debates: tuple[DashboardDebateSummary, ...],
    show_reading_order: bool = True,
    show_detail_loader: bool = True,
) -> None:
    st.subheader("同日速读")
    st.caption("只保留今天最重要的结论、卡点和少量阶段证据。")
    if not rows:
        fallback_lines = tuple(
            getattr(provider, "runtime_fallback_digest_lines", lambda _: ())(
                signal_date
            )
            or ()
        )
        if fallback_lines:
            _render_cockpit_card(
                kicker="运行状态",
                title=fallback_lines[0].replace("结论: ", "", 1),
                lines=fallback_lines[1:] or ("当前日期暂无结构化同日速读。",),
                tone=_runtime_frontdesk_tone(fallback_lines),
            )
        else:
            st.info("当前日期暂无结构化同日速读。")
        return

    digest_lines = _same_day_digest_snapshot_lines(
        provider,
        signal_date,
        rows,
        debates,
        spotlights=spotlights,
        source_task_view=task_view,
    )
    if show_reading_order:
        _render_home_reading_order(
            task_view=task_view,
            overview=overview,
            paper_summary=paper_summary,
            spotlights=spotlights,
            debates=debates,
        )
    if digest_lines:
        digest_card_lines = _same_day_summary_card_lines(
            overview=overview,
            digest_lines=digest_lines,
            debates=debates,
            task_view=task_view,
        )
        digest_tone = (
            "blocked"
            if overview.blocked_total
            else ("focus" if overview.actionable_total else "archive")
        )
        _render_cockpit_card(
            kicker="当天总控",
            title=overview.focus_headline or overview.top_headline or "今天先看这几条",
            lines=digest_card_lines,
            tone=digest_tone,
        )
        selected_card, selected_spotlight, debate_summary = (
            _same_day_summary_focus_context(
                task_view=task_view,
                spotlights=spotlights,
                debates=debates,
            )
        )
        if any(
            item is not None
            for item in (selected_card, selected_spotlight, debate_summary)
        ):
            action_cols = st.columns(len(_home_focus_action_targets()))
            for action_col, (label, workspace) in zip(
                action_cols,
                _home_focus_action_targets(),
            ):
                with action_col:
                    if _stretch_button(
                        label,
                        key=f"same-day-summary-{workspace}-{signal_date}",
                    ):
                        _queue_same_day_summary_handoff(
                            workspace=workspace,
                            signal_date=signal_date,
                            task_view=task_view,
                            spotlights=spotlights,
                            debates=debates,
                        )
                        st.rerun()

    summary_rows = tuple(
        sorted(rows, key=_same_day_summary_row_priority_key, reverse=True)
    )[:2]
    if summary_rows:
        for start in range(0, len(summary_rows), 3):
            batch = summary_rows[start : start + 3]
            columns = st.columns(min(len(batch), 3))
            for column, row in zip(columns, batch):
                lines = _same_day_message_lite_lines(row)
                with column:
                    _render_cockpit_card(
                        kicker=f"{row.phase_label} · {row.task_label}",
                        title=f"{_same_day_task_time_prefix(row)}{row.status_label or '已落盘'}",
                        lines=lines,
                        tone=_same_day_message_tone(row),
                    )
                    if _stretch_button(
                        "看这段",
                        key=f"same-day-message-lite-{row.task_id}-{row.signal_date}",
                    ):
                        _queue_home_selection_handoff(
                            signal_date=row.signal_date,
                            task_id=row.task_id,
                            task_label=row.task_label,
                            title=f"切到 {row.phase_label} 看这段结论",
                            lines=lines,
                        )
                        st.rerun()

    if not show_detail_loader:
        return

    if not _lazy_home_section_requested(
        button_label="加载同日任务明细",
        state_key=f"dashboard_same_day_detail_loaded_{signal_date}",
        idle_hint="同日任务明细按需加载；默认只看当天总控，减少首页首屏卡顿。",
    ):
        return

    ordered_rows = _ordered_same_day_message_rows(provider, signal_date, rows)
    visible_rows = ordered_rows[:4]
    if visible_rows:
        for start in range(0, len(visible_rows), 2):
            batch = visible_rows[start : start + 2]
            columns = st.columns(min(len(batch), 2))
            for column, (row, row_task_view) in zip(columns, batch):
                with column:
                    _render_cockpit_card(
                        kicker=f"{row.phase_label} · {row.task_label}",
                        title=f"{_same_day_task_time_prefix(row)}{row.status_label or '已落盘'}",
                        lines=_same_day_message_lines(row_task_view, row),
                        tone=_same_day_message_tone(row),
                    )
                    if _stretch_button(
                        "看这段",
                        key=f"same-day-message-{row.task_id}-{row.signal_date}",
                    ):
                        _queue_home_selection_handoff(
                            signal_date=row.signal_date,
                            task_id=row.task_id,
                            task_label=row.task_label,
                            title=f"切到 {row.phase_label} 看这段结论",
                            lines=_same_day_message_lines(row_task_view, row),
                        )
                        st.rerun()
    hidden_count = len(ordered_rows) - len(visible_rows)
    if hidden_count > 0:
        st.caption(f"其余 {hidden_count} 条同日消息已省略，保持首页只看最重要卡片。")


def _render_home_runtime_truth(
    provider: DashboardDataProvider,
    *,
    signal_date: str,
) -> None:
    runtime_overview = getattr(provider, "runtime_overview", lambda _: None)(
        signal_date
    )
    fallback_lines = tuple(
        getattr(provider, "runtime_fallback_digest_lines", lambda _: ())(signal_date)
        or ()
    )
    runs: tuple = ()
    if runtime_overview is None and not fallback_lines:
        load_runs = getattr(provider, "runtime_task_runs", None)
        runs = tuple(load_runs(signal_date) if callable(load_runs) else ())[:2]

    if runtime_overview is None and not fallback_lines and not runs:
        return

    if runtime_overview is not None and runtime_overview.conclusion:
        title = runtime_overview.conclusion
    elif fallback_lines:
        title = fallback_lines[0].replace("结论: ", "", 1)
    else:
        title = f"最近任务: {runs[0].task_label} / {runs[0].status_label}"

    lines: list[str] = []
    if runtime_overview is not None:
        status_parts = []
        if runtime_overview.task_label:
            status_parts.append(runtime_overview.task_label)
        if runtime_overview.signal_date:
            status_parts.append(runtime_overview.signal_date)
        if runtime_overview.run_status:
            status_parts.append(runtime_overview.run_status)
        if status_parts:
            lines.append("运行状态: " + " / ".join(status_parts))

        source = runtime_overview.effective_source or runtime_overview.requested_source
        data_parts = []
        if source:
            data_parts.append(_dashboard_source_boundary_label(source))
        if runtime_overview.data_latest_trade_date:
            data_parts.append(f"数据日 {runtime_overview.data_latest_trade_date}")
        if runtime_overview.lag_days:
            data_parts.append(f"延迟 {runtime_overview.lag_days} 天")
        if data_parts:
            lines.append("数据: " + " / ".join(data_parts))
        elif runtime_overview.source_reason:
            lines.append("数据: " + runtime_overview.source_reason)

        if runtime_overview.risk_reason:
            lines.append(f"风险/阻塞: {runtime_overview.risk_reason}")
        if runtime_overview.cooldown_until:
            lines.append(
                "后续安排: "
                f"组合保护解除日 {runtime_overview.cooldown_until}，"
                "解除日前不追加新增候选；解除后恢复收盘主链复核。"
            )
        if runtime_overview.coldstart_progress:
            coldstart_line = f"冷启动: {runtime_overview.coldstart_progress}"
            if _coldstart_progress_ready(runtime_overview.coldstart_progress):
                gate_blocker = str(
                    getattr(runtime_overview, "gate_blocker_line", "") or ""
                ).strip()
                coldstart_line += (
                    "，样本门已达标；"
                    + (
                        f"{gate_blocker}；"
                        if gate_blocker
                        else "后续看双门 gate 与组合保护状态，"
                    )
                    + "不再追加冷启动样本。"
                )
            lines.append(coldstart_line)
        elif getattr(runtime_overview, "gate_blocker_line", ""):
            lines.append(str(runtime_overview.gate_blocker_line))
        coldstart_handoff_line = str(
            getattr(runtime_overview, "coldstart_handoff_line", "") or ""
        ).strip()
        if coldstart_handoff_line:
            lines.append(coldstart_handoff_line)
        walkforward_line = str(
            getattr(runtime_overview, "walkforward_runtime_line", "") or ""
        ).strip()
        if walkforward_line:
            lines.append(walkforward_line)
        intraday_line = str(
            getattr(runtime_overview, "intraday_runtime_line", "") or ""
        ).strip()
        if intraday_line:
            lines.append(intraday_line)
        market_context_line = str(
            getattr(runtime_overview, "market_context_runtime_line", "") or ""
        ).strip()
        if market_context_line:
            lines.append(market_context_line)

    if not lines:
        lines = list(fallback_lines[1:4])
    for run in runs:
        if run.action == "coldstart":
            lines.append(f"冷启动任务: {run.status_label} / {run.headline}")
            lines.extend(
                line for line in run.detail_lines if line.startswith("冷启动:")
            )
            break
    if not lines and runs:
        lines.append(f"{runs[0].task_label}: {runs[0].headline}")
    if len(lines) > 5 and any(
        line.startswith(("跨市规则:", "生产 gate:")) for line in lines
    ):
        lines = [line for line in lines if not line.startswith("后续安排:")]
    if len(lines) > 5 and any(
        line.startswith(("跨市规则:", "生产 gate:")) for line in lines
    ):
        lines = [line for line in lines if not line.startswith("数据:")]

    tone = _runtime_frontdesk_tone((title, *lines))
    _render_cockpit_card(
        kicker="运行真相",
        title=title,
        lines=tuple(lines[:5]),
        tone=tone,
    )


def _runtime_frontdesk_tone(lines: tuple[str, ...]) -> str:
    blocking_markers = (
        "阻塞",
        "组合保护",
        "盘中短线不可用",
        "只适合历史验证",
        "已降级",
        "资源不足",
        "超时",
    )
    return (
        "blocked"
        if any(marker in line for line in lines for marker in blocking_markers)
        else "archive"
    )


def _coldstart_progress_ready(progress: str) -> bool:
    match = re.fullmatch(r"(\d+)/(\d+)", str(progress or "").strip())
    if not match:
        return False
    current, target = (int(match.group(1)), int(match.group(2)))
    return target > 0 and current >= target


def _ordered_same_day_message_rows(
    provider: DashboardDataProvider,
    signal_date: str,
    rows: tuple[DashboardSameDayTaskRow, ...],
) -> tuple[tuple[DashboardSameDayTaskRow, object], ...]:
    build_digest_view = _provider_build_task_digest_view(provider)
    ordered = [
        (row, build_digest_view(row.task_id, signal_date=signal_date)) for row in rows
    ]
    ordered.sort(key=lambda item: item[0].phase_order)
    ordered.sort(
        key=lambda item: _same_day_message_priority_key(item[1], item[0]),
        reverse=True,
    )
    return tuple(ordered)


def _provider_build_task_digest_view(provider: DashboardDataProvider):
    build_digest_view = getattr(provider, "build_task_digest_view", None)
    if callable(build_digest_view):
        return build_digest_view
    return provider.build_task_view


def _same_day_message_priority_key(
    task_view,
    row: DashboardSameDayTaskRow,
) -> tuple[int, ...]:
    detail_cards = tuple(getattr(task_view, "detail_cards", ()) or ())
    lead_card = detail_cards[0] if detail_cards else None
    structured_focus = _structured_task_message_focus_line(task_view)
    return (
        int(bool(structured_focus)),
        int(bool(lead_card and lead_card.review_meta)),
        int(bool(lead_card and lead_card.blocker)),
        int(row.actionable_count > 0),
        int(row.blocked_count > 0),
        int(row.watch_count > 0),
        row.actionable_count,
        row.blocked_count,
        row.watch_count,
        _same_day_time_sort_key(str(row.created_at or "")),
        -row.phase_order,
    )


def _same_day_message_lite_lines(row: DashboardSameDayTaskRow) -> tuple[str, ...]:
    metrics = (
        f"待复核 {row.actionable_count}"
        f" / 观察 {row.watch_count}"
        f" / 阻塞 {row.blocked_count}"
    )
    focus = _clean_task_message_judgment(row.phase_summary or row.headline)
    next_line = _safe_current_research_line(
        row.headline or row.phase_label or row.task_label or "对应任务的复核与阻塞记录"
    )
    return _unique_lines(
        (
            f"这一段: {focus}" if focus else "",
            f"数量: {metrics}",
            f"切到这段看: {next_line}",
        )
    )


def _same_day_message_lines(
    task_view,
    row: DashboardSameDayTaskRow,
) -> tuple[str, ...]:
    next_line = _safe_current_research_line(
        row.headline or row.phase_label or row.task_label or "对应任务的复核与阻塞记录"
    )
    return _unique_lines(
        (
            _same_day_message_delta_line(task_view, row),
            _same_day_message_focus_line(task_view, row),
            f"切到这段看: {next_line}",
        )
    )


def _same_day_message_delta_line(
    task_view,
    row: DashboardSameDayTaskRow,
) -> str:
    structured_focus = _structured_task_message_focus_line(task_view)
    if structured_focus:
        return f"这一段新增: {structured_focus}"

    candidate_lines = (
        row.phase_summary,
        *(getattr(task_view, "review_lines", ())[:1]),
        *(getattr(task_view, "agenda_lines", ())[:1]),
        row.headline,
    )
    for line in candidate_lines:
        cleaned = _clean_task_message_judgment(str(line or ""))
        if cleaned:
            return f"这一段新增: {cleaned}"
    return "这一段新增: 切到对应任务看这一阶段的新增结论。"


def _same_day_message_focus_line(
    task_view,
    row: DashboardSameDayTaskRow,
) -> str:
    review_lines = tuple(getattr(task_view, "review_lines", ())[:1])
    recommendation_lines = tuple(getattr(task_view, "recommendation_lines", ())[:1])
    agenda_lines = tuple(getattr(task_view, "agenda_lines", ())[:1])
    watchlist_lines = tuple(getattr(task_view, "watchlist_lines", ())[:1])
    blocker_lines = tuple(getattr(task_view, "blocker_lines", ())[:1])
    next_day_focus_lines = tuple(getattr(task_view, "next_day_focus_lines", ())[:1])

    if row.blocked_count > 0:
        for line in blocker_lines + review_lines + agenda_lines:
            cleaned = _clean_task_message_judgment(line)
            if cleaned:
                return f"本段卡点: {cleaned}"
        return f"本段卡点: 有 {row.blocked_count} 个阻塞对象待复核。"

    for line in (
        review_lines
        + recommendation_lines
        + watchlist_lines
        + agenda_lines
        + next_day_focus_lines
    ):
        cleaned = _clean_task_message_judgment(line)
        if cleaned:
            return f"本段焦点: {cleaned}"

    if row.actionable_count > 0:
        return f"本段焦点: 有 {row.actionable_count} 个待复核对象已落盘。"
    if row.watch_count > 0:
        return f"本段焦点: 有 {row.watch_count} 个观察对象待继续确认。"
    return "本段焦点: 这段以阶段复核与阻塞整理为主。"


def _same_day_cross_market_digest_line(
    *,
    spotlights: tuple[DashboardCandidateSpotlight, ...] = (),
    debates: tuple[DashboardDebateSummary, ...] = (),
) -> str:
    lead_spotlight = next(
        (
            item
            for item in spotlights
            if (
                item.cross_market_summary
                or item.cross_market_chain_summary
                or item.cross_market_validation_summary
                or item.cross_market_invalidation_summary
            )
        ),
        None,
    )
    if lead_spotlight is not None:
        return _focus_cross_market_digest_line(
            selected_spotlight=lead_spotlight,
            focus_display=lead_spotlight.display_name,
        )

    lead_debate = next(
        (
            item
            for item in _ordered_home_debates(debates)
            if (
                item.cross_market_summary
                or item.cross_market_chain_summary
                or item.cross_market_validation_summary
                or item.cross_market_invalidation_summary
            )
        ),
        None,
    )
    if lead_debate is None:
        return ""
    return _focus_cross_market_digest_line(
        debate_summary=lead_debate,
        focus_display=lead_debate.display_name,
    )


def _source_status_verdict_line(source_status: dict[str, str]) -> str:
    source_status = dict(source_status or {})
    actual = str(source_status.get("actual_source", "") or "").strip()
    if not actual:
        return ""
    fit = workload_fit_for_source(actual).get("live_short", "unknown")
    health_label = str(source_status.get("health_label", "") or "").strip()
    lag_days = str(source_status.get("lag_days", "") or "").strip()
    lag_suffix = ""
    if lag_days and lag_days not in {"-", "未记录", "None", "nan"}:
        lag_suffix = f"；滞后 {lag_days} 天"
    if source_supports_workload(actual, "live_short"):
        if health_label == "fallback":
            return f"数据链路: 备用实时源 {actual}（live_short={fit}）{lag_suffix}"
        if health_label == "degraded":
            return f"数据链路: 实时源 {actual} 已降级（live_short={fit}）{lag_suffix}"
        return f"数据链路: 实时源 {actual}（live_short={fit}）{lag_suffix}"
    return f"数据链路: 当前实际源 {actual} 只适合历史验证，盘中短线不可用（live_short={fit}）{lag_suffix}"


def _same_day_source_status_digest_line(task_view) -> str:
    source_status = dict(getattr(task_view, "source_status", {}) or {})
    return _source_status_verdict_line(source_status)


def _same_day_digest_decision_line(
    *,
    rows: tuple[DashboardSameDayTaskRow, ...],
    spotlights: tuple[DashboardCandidateSpotlight, ...] = (),
    debates: tuple[DashboardDebateSummary, ...] = (),
) -> str:
    has_tasks = bool(rows)
    has_spotlights = bool(spotlights)
    has_debates = bool(debates)
    if has_spotlights:
        if has_debates:
            return (
                "当前采用口径: 同日跨任务联动；先看跨任务共振，再回到各任务落盘，"
                "委员会只补充分歧与风险。"
            )
        return "当前采用口径: 同日跨任务联动；先看跨任务共振，再回到各任务落盘。"
    if has_tasks:
        if has_debates:
            return (
                "当前采用口径: 当天任务落盘；当天以各任务已落盘结论为主，"
                "委员会只补充分歧、风险与触发。"
            )
        return "当前采用口径: 当天任务落盘；当天以各任务已落盘结论为主。"
    if has_debates:
        return "当前采用口径: 委员会补充结论；当天没有独立任务结论，委员会只作解释，不改写评分。"
    return ""


def _same_day_digest_snapshot_lines(
    provider: DashboardDataProvider,
    signal_date: str,
    rows: tuple[DashboardSameDayTaskRow, ...],
    debates: tuple[DashboardDebateSummary, ...],
    *,
    spotlights: tuple[DashboardCandidateSpotlight, ...] = (),
    source_task_view=None,
) -> tuple[str, ...]:
    del provider, signal_date
    ordered_rows = _ordered_same_day_summary_rows(rows)
    task_limit = 2 if debates else 3
    source_line = (
        _same_day_source_status_digest_line(source_task_view)
        if source_task_view is not None
        else ""
    )
    market_line = _same_day_cross_market_digest_line(
        spotlights=spotlights,
        debates=debates,
    )
    decision_line = _same_day_digest_decision_line(
        rows=rows,
        spotlights=spotlights,
        debates=debates,
    )
    event_lines = tuple(
        event.line
        for event in _same_day_digest_summary_events(
            ordered_rows=ordered_rows,
            task_limit=task_limit,
            debates=debates,
        )
        if not event.line.startswith("讨论过程:")
    )
    return _unique_lines(
        ((source_line,) if source_line else ()),
        ((market_line,) if market_line else ()),
        ((decision_line,) if decision_line else ()),
        event_lines,
    )


def _same_day_digest_conclusion_lines(lines: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        line
        for line in lines
        if not (line.startswith("讨论过程:") or line.startswith("讨论结果:"))
    )


def _task_message_judgment_line(
    task_view,
    row: DashboardSameDayTaskRow,
) -> str:
    structured_focus = _structured_task_message_focus_line(task_view)
    if structured_focus:
        return f"关键判断: {structured_focus}"

    review_lines = tuple(getattr(task_view, "review_lines", ())[:1])
    recommendation_lines = tuple(getattr(task_view, "recommendation_lines", ())[:1])
    agenda_lines = tuple(getattr(task_view, "agenda_lines", ())[:1])
    watchlist_lines = tuple(getattr(task_view, "watchlist_lines", ())[:1])
    blocker_lines = tuple(getattr(task_view, "blocker_lines", ())[:1])
    next_day_focus_lines = tuple(getattr(task_view, "next_day_focus_lines", ())[:1])
    candidate_lines: tuple[str, ...]
    if row.blocked_count > 0:
        candidate_lines = blocker_lines + review_lines
    elif row.actionable_count > 0:
        candidate_lines = review_lines + recommendation_lines + agenda_lines
    elif row.watch_count > 0:
        candidate_lines = review_lines + watchlist_lines + agenda_lines
    else:
        candidate_lines = review_lines + agenda_lines + next_day_focus_lines

    for line in candidate_lines:
        cleaned = _clean_task_message_judgment(line)
        if cleaned:
            return f"关键判断: {cleaned}"

    if row.blocked_count > 0:
        return f"关键判断: 有 {row.blocked_count} 个阻塞对象待复核。"
    if row.actionable_count > 0:
        return f"关键判断: 有 {row.actionable_count} 个待复核对象已落盘。"
    if row.watch_count > 0:
        return f"关键判断: 有 {row.watch_count} 个观察对象待继续确认。"
    return "关键判断: 这是当天阶段结论的落盘摘要。"


def _structured_task_message_focus_line(task_view) -> str:
    detail_cards = tuple(getattr(task_view, "detail_cards", ()) or ())
    if not detail_cards:
        return ""
    lead_card = detail_cards[0]
    note = _safe_current_research_line(
        lead_card.decision_note or lead_card.blocker or lead_card.next_step
    ).strip()
    if not note:
        return ""
    structured_markers = (
        "倾向优先纸面复核",
        "但先卡住",
        "跨市线索",
        "历史校验",
        "讨论视角",
        "同向 ",
        "反向 ",
    )
    if not any(marker in note for marker in structured_markers):
        return ""
    parts = [lead_card.display_name]
    if lead_card.review_meta:
        parts.append(lead_card.review_meta)
    parts.append(note)
    return " | ".join(part for part in parts if part)


def _clean_task_message_judgment(line: str) -> str:
    cleaned = _safe_current_research_line(line).strip()
    if not cleaned:
        return ""
    prefixes = (
        "先看推荐: ",
        "先核对卡点: ",
        "安排复核: ",
        "明日重点: ",
    )
    for prefix in prefixes:
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :].strip()
    return cleaned


def _timeline_task_digest_line(task_view, row: DashboardSameDayTaskRow) -> str:
    time_prefix = _same_day_task_time_prefix(row)
    judgment = _task_message_judgment_line(task_view, row)
    if judgment.startswith("关键判断: "):
        return f"- {row.task_label}: {time_prefix}{judgment[len('关键判断: ') :]}"
    summary = _task_message_summary(task_view)
    return f"- {row.task_label}: {time_prefix}{summary}"


def _timeline_debate_process_line(
    debate_summary: DashboardDebateSummary,
) -> str:
    parts = []
    round_flow_line = _debate_round_flow_line(debate_summary).replace(
        "过程主线: ", "过程主线 ", 1
    )
    if round_flow_line:
        parts.append(round_flow_line)
    bullish_line = _debate_top_view_line(
        debate_summary,
        stance="bullish",
        prefix="关键支持",
    )
    bearish_line = _debate_top_view_line(
        debate_summary,
        stance="bearish",
        prefix="关键反对",
    )
    watch_line = _debate_watch_focus_line(debate_summary)
    if watch_line.startswith("下一触发: "):
        watch_line = watch_line.replace("下一触发: ", "触发 ", 1)
    if bullish_line and bearish_line:
        parts.append(
            "过程对照: "
            + bullish_line.replace("关键支持: ", "支持 ", 1)
            + " | "
            + bearish_line.replace("关键反对: ", "反对 ", 1)
        )
    elif bullish_line:
        parts.append(bullish_line)
    elif bearish_line:
        parts.append(bearish_line)
    elif watch_line:
        parts.append(watch_line)
    if watch_line and (bullish_line or bearish_line):
        parts.append(watch_line)
    elif not (bullish_line or bearish_line or watch_line):
        parts.append(
            (
                f"投票 看多 {debate_summary.bull_count}"
                f" / 看空 {debate_summary.bear_count}"
                f" / 中性 {debate_summary.neutral_count}"
            )
        )
    return f"- {debate_summary.display_name}: {' | '.join(parts[:3])}"


def _timeline_debate_result_line(
    debate_summary: DashboardDebateSummary,
) -> str:
    conclusion = _debate_conclusion_summary(debate_summary)
    has_bear_view = bool(
        _debate_top_view_line(
            debate_summary,
            stance="bearish",
            prefix="反对方",
        )
    )
    lead = (
        conclusion.decision_line.replace("研究口径: ", "")
        .replace("当前结论: ", "")
        .replace("核心卡点: ", "卡点 ")
        or debate_summary.recommended_adjustment_label
    )
    parts = [lead]
    compact_cross_market_line = _focus_cross_market_digest_line(
        debate_summary=debate_summary,
        focus_display=debate_summary.display_name,
    )
    if compact_cross_market_line:
        parts.append(compact_cross_market_line)
    elif conclusion.opposition_line and not has_bear_view:
        parts.append(conclusion.opposition_line.replace("讨论反对: ", "反对 "))
    followup_line = _compact_debate_followup_line(
        debate_summary,
        conclusion=conclusion,
    )
    if followup_line:
        parts.append(
            followup_line.replace("下一触发: ", "触发 ").replace(
                "讨论待确认: ", "待确认 "
            )
        )
    return f"- {debate_summary.display_name}: {' | '.join(parts[:3])}"


def _timeline_debate_conclusion_lines(
    debate_summary: DashboardDebateSummary,
) -> tuple[str, ...]:
    conclusion = _debate_conclusion_summary(debate_summary)
    watch_line = _debate_watch_focus_line(debate_summary)
    if watch_line.startswith("下一触发: "):
        watch_line = watch_line.replace("下一触发: ", "触发 ", 1)
    lead_view_line = _debate_top_view_line(
        debate_summary,
        stance="bullish",
        prefix="支持方",
    ) or _debate_top_view_line(
        debate_summary,
        stance="neutral",
        prefix="主导视角",
    )
    counter_view_line = _debate_top_view_line(
        debate_summary,
        stance="bearish",
        prefix="反对方",
    )
    stance_line = ""
    if lead_view_line and counter_view_line:
        stance_line = (
            "讨论站位: "
            + lead_view_line.replace("支持方: ", "支持 ", 1)
            + " | "
            + counter_view_line.replace("反对方: ", "反对 ", 1)
        )
    elif lead_view_line:
        stance_line = lead_view_line
    elif counter_view_line:
        stance_line = counter_view_line
    return _unique_lines(
        (
            _timeline_debate_result_line(debate_summary).removeprefix("- ").strip(),
            stance_line,
            conclusion.opposition_line if not counter_view_line else "",
            watch_line,
        )
    )


def _render_command_center(
    task_view,
    overview: DashboardDateOverview,
    summary_lines: tuple[str, ...],
) -> None:
    recommend_cards, watch_cards, blocked_cards = _classify_candidate_queues(
        task_view.detail_cards
    )
    blocked_focus = _home_blocked_focus_card(blocked_cards)
    focus_card = _home_primary_focus_card(
        recommend_cards,
        watch_cards,
        blocked_cards,
    )
    focus_text = (
        _join_display_parts(
            focus_card.display_name,
            _action_status_label(focus_card.action_label, focus_card.status_label),
        )
        if focus_card is not None
        else "当前无显著主推候选"
    )
    blocker_text = (
        _card_primary_blocker(blocked_focus)
        if blocked_focus is not None and _card_primary_blocker(blocked_focus)
        else (overview.blocker_headline or "当前无明显阻塞")
    )
    review_text = (
        focus_card.review_meta
        if focus_card is not None and focus_card.review_meta
        else (task_view.review_lines[0] if task_view.review_lines else "")
    )
    agenda_lines = task_view.agenda_lines[:2]
    overview_lines = tuple(
        line
        for line in (
            f"当前任务: {task_view.task_label} · {_current_mode_label(task_view)}",
            f"回看日期: {review_date_label(task_view)}",
            (
                f"涉及任务 {overview.task_count} / 待复核 {overview.actionable_total} / 观察 {overview.watch_total} / 阻塞 {overview.blocked_total}"
                if overview.signal_date
                else f"待复核 {task_view.actionable_count} / 观察 {task_view.watch_count} / 阻塞 {task_view.blocked_count}"
            ),
            (
                f"归档状态: {_report_archive_status(task_view)}"
                if _report_archive_status(task_view) != "无归档"
                else ""
            ),
            (
                f"阶段摘要: {overview.focus_headline or overview.top_headline}"
                if overview.focus_headline or overview.top_headline
                else ""
            ),
        )
        if line
    )
    plan_lines = _unique_lines(
        (
            f"主焦点: {focus_text}",
            f"主阻塞: {blocker_text}",
            f"再看时间: {review_text or '按默认时间回看'}",
        ),
        _command_center_brief_lines(
            task_view=task_view,
            summary_lines=summary_lines,
        ),
        tuple(f"今日待办: {line}" for line in agenda_lines if line),
    )
    tone = (
        "blocked"
        if task_view.blocked_count
        else ("focus" if task_view.actionable_count else "archive")
    )

    overview_col, plan_col = st.columns(2)
    with overview_col:
        _render_cockpit_card(
            kicker="任务总览",
            title=f"{task_view.task_label} · {_current_mode_label(task_view)}",
            lines=overview_lines,
            tone="archive",
        )
    with plan_col:
        _render_cockpit_card(
            kicker="今日推进板",
            title=focus_text,
            lines=plan_lines,
            tone=tone,
        )


def _render_decision_queues(task_view) -> None:
    recommend_cards, watch_cards, blocked_cards = _classify_candidate_queues(
        task_view.detail_cards
    )

    recommend_summary = (
        f"当前优先再看 {len(recommend_cards)} 只，先看 {recommend_cards[0].display_name}"
        if recommend_cards
        else "当前没有进入优先再看的候选，先看观察与阻塞队列。"
    )
    watch_summary = (
        f"继续观察名单 {len(watch_cards)} 只，先看时间和确认条件。"
        if watch_cards
        else "当前继续观察名单较轻，重点转向推荐或阻塞处理。"
    )
    blocked_summary = (
        f"阻塞 {len(blocked_cards)} 只，先核对最上面的研究卡点。"
        if blocked_cards
        else "当前没有明显阻塞，纸面验证节奏相对顺畅。"
    )

    recommend_col, watch_col, blocked_col = st.columns(3)
    with recommend_col:
        _render_priority_queue(
            title="优先再看队列",
            kicker="研究候选",
            summary=recommend_summary,
            cards=recommend_cards,
            empty_text="当前日期没有优先再看候选，不要为了凑名单硬找方向。",
            tone="recommend",
        )
    with watch_col:
        _render_priority_queue(
            title="观察队列",
            kicker="继续观察",
            summary=watch_summary,
            cards=watch_cards,
            empty_text="当前日期没有继续观察候选，先等下一轮主链信号。",
            tone="watch",
        )
    with blocked_col:
        _render_priority_queue(
            title="阻塞队列",
            kicker="核对卡点",
            summary=blocked_summary,
            cards=blocked_cards,
            empty_text="当前日期没有明显卡点，按候选顺位回看即可。",
            tone="blocked",
        )


def _render_decision_flow(
    task_view,
    spotlights: tuple[DashboardCandidateSpotlight, ...],
) -> None:
    home_spotlights = _home_focus_spotlights(spotlights)
    if not home_spotlights:
        return
    st.subheader("焦点候选")
    signal_date = _task_view_signal_date(task_view)

    for start in range(0, min(len(home_spotlights), 4), 2):
        columns = st.columns(min(len(home_spotlights[start : start + 2]), 2))
        for column, item in zip(columns, home_spotlights[start : start + 2]):
            with column:
                _render_cockpit_card(
                    kicker="跨任务联动",
                    title=item.display_name,
                    lines=_home_spotlight_lines(item),
                    tone="blocked" if item.blocker else "focus",
                )
                action_cols = st.columns(len(_home_focus_action_targets()))
                for action_col, (label, workspace) in zip(
                    action_cols,
                    _home_focus_action_targets(),
                ):
                    with action_col:
                        if _stretch_button(
                            label,
                            key=f"home-{workspace}-{item.symbol}-{start}",
                        ):
                            _queue_home_spotlight_handoff(
                                workspace=workspace,
                                spotlight=item,
                                signal_date=signal_date,
                            )
                            st.rerun()


def _home_focus_action_targets() -> tuple[tuple[str, str], ...]:
    return (
        ("复盘", "候选复盘"),
        ("虚拟盘", "虚拟盘跟踪"),
        ("归档", "归档回看"),
    )


def _home_focus_spotlights(
    spotlights: tuple[DashboardCandidateSpotlight, ...],
) -> tuple[DashboardCandidateSpotlight, ...]:
    return tuple(item for item in spotlights if len(item.task_labels) > 1)


def _spotlight_discussion_or_evidence_lines(
    spotlight: DashboardCandidateSpotlight,
    *,
    reason_label: str,
    risk_label: str,
) -> tuple[str, ...]:
    discussion_lines = tuple(
        line
        for line in (
            (
                f"讨论支持: {spotlight.support_points[0]}"
                if spotlight.support_points
                else ""
            ),
            (
                f"讨论反对: {spotlight.opposition_points[0]}"
                if spotlight.opposition_points
                else ""
            ),
            (
                f"讨论待确认: {spotlight.watch_items[0]}"
                if spotlight.watch_items
                else ""
            ),
        )
        if line
    )
    if discussion_lines:
        return discussion_lines
    return tuple(
        line
        for line in (
            (
                f"{reason_label}: {'；'.join(spotlight.reasons[:2])}"
                if spotlight.reasons
                else ""
            ),
            (
                f"{risk_label}: {'；'.join(spotlight.risks[:2])}"
                if spotlight.risks
                else ""
            ),
        )
        if line
    )


def _spotlight_global_view_lines(
    spotlight: DashboardCandidateSpotlight,
    *,
    reason_label: str,
    risk_label: str,
) -> tuple[str, ...]:
    return tuple(
        line
        for line in (
            (
                f"跨市传导: {spotlight.cross_market_summary}"
                if spotlight.cross_market_summary
                else ""
            ),
            (
                f"传导链: {spotlight.cross_market_chain_summary}"
                if spotlight.cross_market_chain_summary
                else ""
            ),
            (
                f"确认信号: {spotlight.cross_market_validation_summary}"
                if spotlight.cross_market_validation_summary
                else ""
            ),
            (
                f"失效信号: {spotlight.cross_market_invalidation_summary}"
                if spotlight.cross_market_invalidation_summary
                else ""
            ),
            *_spotlight_discussion_or_evidence_lines(
                spotlight,
                reason_label=reason_label,
                risk_label=risk_label,
            ),
        )
        if line
    )


def _home_spotlight_lines(
    spotlight: DashboardCandidateSpotlight,
) -> tuple[str, ...]:
    return _same_day_spotlight_card_lines(spotlight)


def _same_day_spotlight_card_lines(
    spotlight: DashboardCandidateSpotlight,
) -> tuple[str, ...]:
    conclusion_line = f"当前结论: {_action_status_label(spotlight.action_label, spotlight.status_label)}"
    cross_market_line = (
        f"跨市主线: {spotlight.cross_market_summary}"
        if spotlight.cross_market_summary
        else (
            f"讨论支持: {spotlight.support_points[0]}"
            if spotlight.support_points
            else (
                f"当前理由: {'；'.join(spotlight.reasons[:2])}"
                if spotlight.reasons
                else ""
            )
        )
    )
    blocker_or_followup_line = ""
    if spotlight.blocker:
        blocker_or_followup_line = f"当前卡点: {spotlight.blocker}"
    elif spotlight.watch_items:
        blocker_or_followup_line = f"讨论待确认: {spotlight.watch_items[0]}"
    elif spotlight.opposition_points:
        blocker_or_followup_line = f"讨论反对: {spotlight.opposition_points[0]}"
    elif spotlight.next_step:
        blocker_or_followup_line = f"下一步: {spotlight.next_step}"
    meta_line = _join_display_parts(
        _task_scope_line(_task_scope_summary(spotlight.task_labels)),
        _review_meta_line("复核", spotlight.review_meta),
        separator=" / ",
    )
    return _unique_lines(
        (
            conclusion_line,
            cross_market_line,
            blocker_or_followup_line,
            meta_line,
        )
    )


def _same_day_spotlight_card_tone(
    spotlight: DashboardCandidateSpotlight,
) -> str:
    if spotlight.blocker:
        return "blocked"
    if spotlight.cross_market_summary or spotlight.support_points:
        return "focus"
    if spotlight.watch_items or spotlight.opposition_points:
        return "pressure"
    return "archive"


def _render_same_day_candidate_spotlights(
    spotlights: tuple[DashboardCandidateSpotlight, ...],
    *,
    signal_date: str = "",
) -> None:
    st.subheader("同日联动焦点")
    st.caption("这里只保留跨任务共振、当前卡点和下一步，不再重复铺开长摘要。")
    if not spotlights:
        st.info("当前日期暂无跨任务候选总览。")
        return

    for start in range(0, len(spotlights), 2):
        columns = st.columns(min(len(spotlights[start : start + 2]), 2))
        for column, item in zip(columns, spotlights[start : start + 2]):
            with column:
                _render_cockpit_card(
                    kicker=f"同日联动 · {item.score:.1f}分",
                    title=item.display_name,
                    lines=_same_day_spotlight_card_lines(item),
                    tone=_same_day_spotlight_card_tone(item),
                )
                action_cols = st.columns(len(_home_focus_action_targets()))
                for action_col, (label, workspace) in zip(
                    action_cols,
                    _home_focus_action_targets(),
                ):
                    with action_col:
                        if _stretch_button(
                            label,
                            key=f"same-day-spotlight-{workspace}-{item.symbol}-{start}",
                        ):
                            _queue_home_spotlight_handoff(
                                workspace=workspace,
                                spotlight=item,
                                signal_date=signal_date,
                            )
                            st.rerun()


def _provider_same_day_spotlights(
    provider: DashboardDataProvider,
    signal_date: str,
) -> tuple[DashboardCandidateSpotlight, ...]:
    loader = getattr(provider, "same_day_candidate_spotlights", None)
    if callable(loader):
        return tuple(loader(signal_date) or ())
    return ()


def _provider_same_day_candidate_journey(
    provider: DashboardDataProvider,
    signal_date: str,
    symbol: str,
) -> tuple[DashboardCandidateJourneyStep, ...]:
    loader = getattr(provider, "same_day_candidate_journey", None)
    if callable(loader):
        return tuple(loader(signal_date, symbol) or ())
    return ()


def _provider_date_overview(
    provider: DashboardDataProvider,
    signal_date: str,
    *,
    spotlights: tuple[DashboardCandidateSpotlight, ...] | None = None,
    debates: tuple[DashboardDebateSummary, ...] | None = None,
) -> DashboardDateOverview | None:
    loader = getattr(provider, "date_overview", None)
    if callable(loader):
        try:
            return loader(signal_date, spotlights=spotlights, debates=debates)
        except TypeError:
            return loader(signal_date)
    return None


def _timeline_overview_card_lines(
    row: DashboardTimelineRow,
    digest_conclusion_lines: tuple[str, ...],
    progress_lines: tuple[str, ...],
) -> tuple[str, ...]:
    context_lines = tuple(
        line
        for line in digest_conclusion_lines
        if line.startswith("数据链路: ") or line.startswith("跨市主线: ")
    )
    fallback_line = ""
    if not context_lines:
        fallback_line = next(
            (
                line
                for line in digest_conclusion_lines
                if (
                    line.strip()
                    and line.strip() != row.headline.strip()
                    and line.strip() not in progress_lines
                )
            ),
            "",
        )
    return _unique_lines(
        (
            _task_scope_line(_task_scope_summary(row.task_labels)),
            *context_lines,
            fallback_line,
        )
    )


def _timeline_progress_card_lines(
    provider: DashboardDataProvider,
    signal_date: str,
    rows: tuple[DashboardSameDayTaskRow, ...],
) -> tuple[str, ...]:
    return _unique_lines(
        tuple(
            _timeline_task_digest_line(
                provider.build_task_view(task_row.task_id, signal_date=signal_date),
                task_row,
            )
            .removeprefix("- ")
            .strip()
            for task_row in rows[:3]
        )
    )


def _timeline_candidate_journey_lines(
    journey_steps: tuple[DashboardCandidateJourneyStep, ...],
) -> tuple[str, ...]:
    return _unique_lines(
        tuple(
            " | ".join(
                part
                for part in (
                    f"{step.phase_label}: {_action_status_label(step.action_label, step.status_label)}",
                    step.review_meta.strip(),
                    (step.blocker or step.next_step or "继续跟踪").strip(),
                )
                if part
            )
            for step in journey_steps[:3]
        )
    )


def _timeline_debate_card_lines(
    debate_summary: DashboardDebateSummary,
) -> tuple[str, ...]:
    prefix = f"- {debate_summary.display_name}: "
    conclusion_lines = _timeline_debate_conclusion_lines(debate_summary)
    decision_line = (
        conclusion_lines[0].removeprefix(f"{debate_summary.display_name}: ").strip()
        if conclusion_lines
        else ""
    )
    decision_line = decision_line.replace(
        f" | 先看 {debate_summary.display_name}",
        "",
    )
    process_line = (
        _timeline_debate_process_line(debate_summary).removeprefix(prefix).strip()
    )
    round_flow_line = _debate_round_flow_line(debate_summary).replace(
        "过程主线: ", "过程主线 ", 1
    )
    watch_line = _debate_watch_focus_line(debate_summary)
    if watch_line.startswith("下一触发: "):
        watch_line = watch_line.replace("下一触发: ", "触发 ", 1)
    normalized_decision_line = decision_line.strip().rstrip("。；;,.，、")
    normalized_watch_line = watch_line.strip().rstrip("。；;,.，、")
    if normalized_watch_line and normalized_watch_line in normalized_decision_line:
        watch_line = ""
    compact_process_line = process_line
    if round_flow_line and watch_line:
        compact_process_line = f"{round_flow_line} | {watch_line}"
    elif round_flow_line:
        compact_process_line = round_flow_line
    stance_line = conclusion_lines[1] if len(conclusion_lines) > 1 else ""
    if (
        stance_line.startswith(("支持方: ", "主导视角: ", "反对方: "))
        and " | " in stance_line
    ):
        stance_prefix, _, stance_detail = stance_line.partition(" | ")
        normalized_stance_detail = stance_detail.strip().rstrip("。；;,.，、")
        if (
            normalized_stance_detail
            and normalized_stance_detail in normalized_decision_line
        ):
            stance_line = stance_prefix
    return _unique_lines(
        (
            decision_line,
            stance_line,
            compact_process_line,
        )
    )


def _timeline_debate_card_tone(
    debate_summary: DashboardDebateSummary,
) -> str:
    if debate_summary.recommended_adjustment == "lower":
        return "blocked"
    if debate_summary.disagreement_score >= 0.35:
        return "pressure"
    return "focus"


def _render_date_timeline_cards(
    provider: DashboardDataProvider,
    selected_date: str,
    current_task_id: str,
) -> None:
    timeline_rows = provider.timeline_rows(limit=6)
    if not timeline_rows:
        return
    st.subheader("按日期展开")
    st.caption("历史只保留压缩卡片，直接扫读；需要深入时再切到那一天。")
    selected_key = "dashboard_home_timeline_date"
    available_dates = tuple(row.signal_date for row in timeline_rows if row.signal_date)
    selected_timeline_date = (
        str(st.session_state.get(selected_key, "") or "").strip()
        if available_dates
        else ""
    )
    if selected_timeline_date not in available_dates:
        selected_timeline_date = (
            selected_date if selected_date in available_dates else available_dates[0]
        )
        st.session_state[selected_key] = selected_timeline_date

    if len(available_dates) > 1:
        st.caption("点击日期切换，只展开当天详情。")
        date_columns = st.columns(len(available_dates))
        for column, timeline_date in zip(date_columns, available_dates):
            with column:
                if _stretch_button(
                    timeline_date,
                    key=f"timeline-select-{timeline_date}",
                    type="primary"
                    if timeline_date == selected_timeline_date
                    else "secondary",
                ):
                    st.session_state[selected_key] = timeline_date
                    st.rerun()

    row = next(
        (item for item in timeline_rows if item.signal_date == selected_timeline_date),
        timeline_rows[0],
    )
    resolution = _resolve_task_for_date_with_reason(
        provider=provider,
        current_task_id=current_task_id,
        signal_date=row.signal_date,
    )
    secondary_label = _date_jump_secondary_label(
        provider,
        current_task_id,
        row.signal_date,
        resolution=resolution,
    )
    date_overview = _provider_date_overview(provider, row.signal_date)
    debate_summaries = _provider_prioritized_debates(provider, row.signal_date)[:1]
    same_day_rows = provider.same_day_task_rows(row.signal_date)
    digest_lines = _same_day_digest_snapshot_lines(
        provider,
        row.signal_date,
        same_day_rows,
        debate_summaries,
    )
    digest_conclusion_lines = _same_day_digest_conclusion_lines(digest_lines)
    progress_lines = _timeline_progress_card_lines(
        provider,
        row.signal_date,
        same_day_rows,
    )
    overview_tone = (
        "blocked"
        if row.blocked_total
        else ("focus" if row.actionable_total else "archive")
    )
    overview_col, detail_col = st.columns(2)
    with overview_col:
        _render_cockpit_card(
            kicker=row.signal_date,
            title=(
                (
                    date_overview.focus_headline.strip()
                    if date_overview is not None
                    else ""
                )
                or row.headline
                or "这天先看这里"
            ),
            lines=_timeline_overview_card_lines(
                row,
                digest_conclusion_lines,
                progress_lines,
            ),
            tone=overview_tone,
        )
    with detail_col:
        if debate_summaries:
            debate_summary = debate_summaries[0]
            _render_cockpit_card(
                kicker="多 Agent",
                title=debate_summary.display_name,
                lines=_timeline_debate_card_lines(debate_summary),
                tone=_timeline_debate_card_tone(debate_summary),
            )
        elif progress_lines:
            _render_cockpit_card(
                kicker="任务进展",
                title=secondary_label or "按阶段推进",
                lines=progress_lines,
                tone=overview_tone,
            )
        else:
            _render_cockpit_card(
                kicker="任务进展",
                title=secondary_label or "当日压缩",
                lines=("当天没有额外结构化讨论，先看总览卡片。",),
                tone="archive",
            )
    if _stretch_button("切到这天", key=f"timeline-date-{row.signal_date}"):
        _queue_home_selection_handoff(
            signal_date=row.signal_date,
            task_id=resolution.task_id,
            task_label=(
                next(
                    (
                        item.task_label
                        for item in same_day_rows
                        if item.task_id == resolution.task_id
                    ),
                    resolution.task_id,
                )
            ),
            title=f"切到 {row.signal_date} 看这天总控",
            lines=_timeline_overview_card_lines(
                row,
                digest_conclusion_lines,
                progress_lines,
            ),
        )
        st.rerun()
    if resolution.reason:
        st.caption(resolution.reason)


def _render_same_day_task_matrix(
    rows: tuple[DashboardSameDayTaskRow, ...],
    current_task_id: str,
) -> None:
    if not rows:
        return
    st.subheader("当天各段")
    for start in range(0, len(rows), 4):
        columns = st.columns(min(len(rows[start : start + 4]), 4))
        for column, row in zip(columns, rows[start : start + 4]):
            is_active = row.task_id == current_task_id
            action_label, watch_label, blocked_label = _task_metric_labels(row.task_id)
            with column:
                st.markdown(
                    f"""
                    <div class="aqsp-day-card {"active" if is_active else ""}">
                      <div class="aqsp-day-header">
                        <div>
                          <div class="aqsp-day-title">{row.task_label}</div>
                          <div class="aqsp-date-meta">{row.phase_label}</div>
                        </div>
                      </div>
                      <div class="aqsp-day-metrics">
                        <div class="aqsp-day-metric">{action_label} {row.actionable_count}</div>
                        <div class="aqsp-day-metric">{watch_label} {row.watch_count}</div>
                        <div class="aqsp-day-metric">{blocked_label} {row.blocked_count}</div>
                      </div>
                      <div class="aqsp-day-summary">{row.phase_summary}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if not is_active and _stretch_button(
                    f"切到{row.task_label}",
                    key=f"same-day-task-{row.task_id}-{row.signal_date}",
                ):
                    _queue_home_selection_handoff(
                        signal_date=row.signal_date,
                        task_id=row.task_id,
                        task_label=row.task_label,
                        title=f"切到 {row.phase_label} 看这段结论",
                        lines=(
                            f"切到这段先看: {row.headline or row.phase_summary or row.task_label}",
                        ),
                    )
                    st.rerun()


def _task_workbench_handoff_lines(
    snapshot: DashboardTaskSnapshot,
    *,
    signal_date: str,
) -> tuple[str, ...]:
    headline = snapshot.headline or snapshot.task_label
    latest_date_line = (
        f"最近独立结果日: {snapshot.latest_date}"
        if snapshot.latest_date and snapshot.latest_date != signal_date
        else ""
    )
    return tuple(
        line
        for line in (
            f"切过去先看: {headline}",
            f"当前状态: {snapshot.status_label}",
            latest_date_line,
        )
        if line
    )


def _queue_task_workbench_handoff(
    snapshot: DashboardTaskSnapshot,
    *,
    signal_date: str,
) -> None:
    _queue_home_selection_handoff(
        signal_date=signal_date or "最新",
        task_id=snapshot.task_id,
        task_label=snapshot.task_label,
        title=f"切到 {snapshot.task_label} 看任务快照",
        lines=_task_workbench_handoff_lines(
            snapshot, signal_date=signal_date or "最新"
        ),
    )


def _render_task_workbench(
    snapshots: tuple[DashboardTaskSnapshot, ...],
    *,
    signal_date: str,
) -> None:
    st.subheader(f"同日任务状态快照 · {signal_date or '最新'}")
    st.caption("只看已落盘结果。")
    if not snapshots:
        st.info("当前还没有任务快照。先确认宝塔任务已运行，再回来看同日状态。")
        return

    active_snapshots = tuple(
        snapshot
        for snapshot in snapshots
        if snapshot.status_label not in {"该日未产出", "未产出"}
    )
    hidden_snapshots = tuple(
        snapshot for snapshot in snapshots if snapshot not in active_snapshots
    )
    if not active_snapshots:
        active_snapshots = snapshots

    columns = st.columns(len(active_snapshots))
    for column, snapshot in zip(columns, active_snapshots):
        action_label, watch_label, blocked_label = _task_metric_labels(snapshot.task_id)
        with column:
            st.markdown(
                f"""
                <div class="aqsp-task-card">
                  <div class="aqsp-task-header">
                    <div>
                      <div class="aqsp-task-title">{snapshot.task_label}</div>
                      <div class="aqsp-task-date">最近日期: {snapshot.latest_date or "-"}</div>
                    </div>
                    <div class="aqsp-task-status">{snapshot.status_label}</div>
                  </div>
                  <div class="aqsp-task-metrics">
                    <div class="aqsp-task-metric">{action_label} {snapshot.actionable_count}</div>
                    <div class="aqsp-task-metric">{watch_label} {snapshot.watch_count}</div>
                    <div class="aqsp-task-metric">{blocked_label} {snapshot.blocked_count}</div>
                  </div>
                  <div class="aqsp-task-summary">{snapshot.headline}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if _stretch_button(
                "打开任务",
                key=f"task-switch-{snapshot.task_id}",
            ):
                _queue_task_workbench_handoff(
                    snapshot,
                    signal_date=signal_date or "最新",
                )
                st.rerun()

    if hidden_snapshots:
        with st.expander(
            f"查看当日无独立结果的任务 ({len(hidden_snapshots)})", expanded=False
        ):
            hidden_columns = st.columns(len(hidden_snapshots))
            for column, snapshot in zip(hidden_columns, hidden_snapshots):
                action_label, watch_label, blocked_label = _task_metric_labels(
                    snapshot.task_id
                )
                with column:
                    st.markdown(
                        f"""
                        <div class="aqsp-task-card">
                          <div class="aqsp-task-header">
                            <div>
                              <div class="aqsp-task-title">{snapshot.task_label}</div>
                              <div class="aqsp-task-date">回看日期: {signal_date or "-"}</div>
                            </div>
                            <div class="aqsp-task-status">{snapshot.status_label}</div>
                          </div>
                          <div class="aqsp-task-metrics">
                            <div class="aqsp-task-metric">{action_label} {snapshot.actionable_count}</div>
                            <div class="aqsp-task-metric">{watch_label} {snapshot.watch_count}</div>
                            <div class="aqsp-task-metric">{blocked_label} {snapshot.blocked_count}</div>
                          </div>
                          <div class="aqsp-task-summary">{snapshot.headline}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    if _stretch_button(
                        "打开任务",
                        key=f"task-switch-hidden-{snapshot.task_id}-{signal_date or 'latest'}",
                    ):
                        _queue_task_workbench_handoff(
                            snapshot,
                            signal_date=signal_date or "最新",
                        )
                        st.rerun()


def _render_paper_summary(summary: DashboardPaperSummary) -> None:
    title = "纸面验证状态"
    if summary.signal_date:
        title = f"纸面验证状态 · {summary.signal_date}"
    st.subheader(title)
    if summary.signal_date:
        st.caption("当前持有假设看 ledger 现状，事件看回看日。")

    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric("纸面持有", summary.open_positions)
    metric_col2.metric("入场待核对", summary.pending_entries)
    metric_col3.metric("不可成交", summary.not_executable)
    metric_col4.metric("纸面关闭", summary.closed_trades)

    detail_col1, detail_col2 = st.columns(2)
    with detail_col1:
        _render_line_block(
            "纸面验证摘要",
            summary.action_summary_lines,
            "当前还没有纸面验证摘要。先等候选进入纸面跟踪后再看。",
        )
        _render_line_block(
            "纸面持有假设",
            summary.open_position_lines,
            "当前没有纸面持有假设。说明暂时没有需要跟踪的纸面仓位。",
        )
    with detail_col2:
        _render_line_block(
            "纸面关键事件",
            summary.event_lines,
            "当前没有纸面关键事件。等出现入场、阻塞或退出记录后再复盘。",
        )


def _home_execution_snapshot_context(
    summary: DashboardPaperSummary,
    *,
    blocked_summary: str = "",
) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...], str]:
    status_title = "当前无纸面压力"
    tone = "archive"
    if summary.pending_entries or summary.not_executable:
        status_title = "纸面事件待处理"
        tone = "pressure"
    elif summary.open_positions:
        status_title = "纸面持有跟踪中"
        tone = "focus"
    elif summary.closed_trades:
        status_title = "已有纸面回写"
        tone = "archive"
    elif blocked_summary:
        status_title = "研究阻塞待核对"
        tone = "blocked"

    status_seed = [
        f"纸面持有 {summary.open_positions} / 入场待核对 {summary.pending_entries} / 不可成交 {summary.not_executable} / 纸面关闭 {summary.closed_trades}"
    ]
    if blocked_summary and not (
        summary.pending_entries or summary.not_executable or summary.open_positions
    ):
        status_seed.append("当前没有新的纸面事件，主要因为研究侧仍有阻塞未核对。")
        status_seed.append(blocked_summary)
    blocked_without_execution = blocked_summary and not (
        summary.pending_entries or summary.not_executable or summary.open_positions
    )
    action_summary_lines = (
        () if blocked_without_execution else summary.action_summary_lines[:1]
    )
    status_lines = _unique_lines(tuple(status_seed), action_summary_lines)
    holding_lines = summary.open_position_lines[:2] or (
        "当前没有纸面持有假设。说明暂时没有需要跟踪的纸面仓位。",
    )
    event_lines = summary.event_lines[:2]
    if blocked_without_execution:
        generic_event_lines = {
            "当前暂无关键事件。",
            f"{summary.signal_date} 暂无纸面入场、阻塞或关闭事件。",
        }
        filtered_event_lines = tuple(
            line for line in event_lines if line not in generic_event_lines
        )
        event_lines = filtered_event_lines or ("先回到候选复盘核对卡点与复核条件。",)
    elif not event_lines:
        event_lines = ("当前没有纸面关键事件。等出现入场、阻塞或退出记录后再复盘。",)
    return (status_title, status_lines, holding_lines, event_lines, tone)


def _home_execution_blocked_summary(
    *,
    task_view,
    overview: DashboardDateOverview,
) -> str:
    _, _, blocked_cards = _classify_candidate_queues(task_view.detail_cards)
    if blocked_cards:
        return _home_blocked_summary(blocked_cards)
    if overview.blocked_total > 0:
        return f"研究侧仍有 {overview.blocked_total} 个阻塞对象，先回候选复盘处理。"
    if task_view.blocker_lines:
        return (
            f"研究侧仍有 {len(task_view.blocker_lines)} 条阻塞线索，先回候选复盘处理。"
        )
    return overview.blocker_headline


def _render_home_execution_snapshot(
    summary: DashboardPaperSummary,
    *,
    task_view,
    overview: DashboardDateOverview,
    show_heading: bool = True,
) -> None:
    if show_heading:
        st.subheader("虚拟盘跟踪")
    blocked_summary = _home_execution_blocked_summary(
        task_view=task_view,
        overview=overview,
    )
    (
        status_title,
        status_lines,
        holding_lines,
        event_lines,
        status_tone,
    ) = _home_execution_snapshot_context(
        summary,
        blocked_summary=blocked_summary,
    )
    compact_mode = bool(
        blocked_summary
        and not summary.open_positions
        and not summary.pending_entries
        and not summary.not_executable
        and not summary.closed_trades
    )
    if compact_mode:
        status_col, event_col = st.columns(2)
        with status_col:
            _render_cockpit_card(
                kicker="纸面压力",
                title=status_title,
                lines=status_lines,
                tone=status_tone,
            )
        with event_col:
            _render_cockpit_card(
                kicker="研究阻塞联动",
                title="先回已有判断核对",
                lines=_unique_lines(
                    event_lines,
                    ("当前没有持仓或纸面回写，纸面侧暂不构成独立工作面。",),
                ),
                tone="blocked",
            )
        return

    status_col, holding_col, event_col = st.columns(3)
    with status_col:
        _render_cockpit_card(
            kicker="纸面压力",
            title=status_title,
            lines=status_lines,
            tone=status_tone,
        )
    with holding_col:
        _render_cockpit_card(
            kicker="纸面持有跟踪",
            title="当前纸面持有",
            lines=holding_lines,
            tone="focus" if summary.open_positions else "archive",
        )
    with event_col:
        _render_cockpit_card(
            kicker="关键事件",
            title="当日纸面线索",
            lines=event_lines,
            tone="pressure"
            if summary.pending_entries or summary.not_executable
            else "archive",
        )


def _debate_active_roles_line(
    debate_summary: DashboardDebateSummary,
) -> str:
    labels = _unique_lines(
        tuple(view.role_label for view in debate_summary.agent_views if view.role_label)
    )
    if not labels:
        return ""
    if len(labels) <= 5:
        return "讨论视角: " + "、".join(labels)
    return "讨论视角: " + "、".join(labels[:5]) + f" 等 {len(labels)} 个角色"


def _ordered_debate_agent_views(
    debate_summary: DashboardDebateSummary,
) -> tuple:
    return tuple(
        sorted(
            debate_summary.agent_views,
            key=lambda item: item.confidence,
            reverse=True,
        )
    )


def _debate_view_focus_text(view) -> str:
    if view.stance == "bearish":
        return (
            view.key_risk
            or view.key_argument
            or view.key_opportunity
            or "未补充核心观点"
        )
    if view.stance == "bullish":
        return (
            view.key_argument
            or view.key_opportunity
            or view.key_risk
            or "未补充核心观点"
        )
    return (
        view.key_argument or view.key_risk or view.key_opportunity or "未补充核心观点"
    )


def _debate_top_view_line(
    debate_summary: DashboardDebateSummary,
    *,
    stance: str,
    prefix: str,
) -> str:
    ordered_views = _ordered_debate_agent_views(debate_summary)
    top_view = next((view for view in ordered_views if view.stance == stance), None)
    if top_view is None:
        return ""
    return (
        f"{prefix}: {top_view.role_label} {top_view.stance_label}"
        f" / 置信 {top_view.confidence:.0%} | {_debate_view_focus_text(top_view)}"
    )


def _debate_watch_focus_line(
    debate_summary: DashboardDebateSummary,
) -> str:
    conclusion = _debate_conclusion_summary(debate_summary)
    for line in (
        conclusion.validation_line,
        conclusion.invalidation_line,
        conclusion.watch_line,
        conclusion.chain_or_trigger_line,
    ):
        if line:
            return line
    return ""


def _debate_process_title(
    debate_summary: DashboardDebateSummary,
) -> str:
    for line in (
        debate_summary.adjustment_reason,
        debate_summary.consensus,
        debate_summary.primary_risk_gate,
        debate_summary.next_trigger,
    ):
        cleaned = _safe_current_research_line(line).strip()
        if cleaned:
            return cleaned
    return "为什么继续看"


def _debate_process_outcome_line(
    debate_summary: DashboardDebateSummary,
) -> str:
    conclusion = _debate_conclusion_summary(debate_summary)
    decision_line = (
        conclusion.decision_line.replace("研究口径: ", "")
        .replace("当前结论: ", "")
        .strip()
    )
    if decision_line:
        return f"当前结论: {decision_line}"
    process_title = _debate_process_title(debate_summary)
    return f"当前结论: {process_title}" if process_title else ""


def _debate_round_flow_line(
    debate_summary: DashboardDebateSummary,
) -> str:
    rounds = tuple(
        f"第 {index} 轮 {summary}"
        for index, summary in enumerate(debate_summary.round_summaries[:2], start=1)
        if str(summary).strip()
    )
    if not rounds:
        return ""
    return "过程主线: " + " → ".join(rounds)


def _debate_process_lines(
    debate_summary: DashboardDebateSummary,
) -> tuple[str, ...]:
    outcome_line = _debate_process_outcome_line(debate_summary)
    round_flow_line = _debate_round_flow_line(debate_summary)
    bullish_line = _debate_top_view_line(
        debate_summary,
        stance="bullish",
        prefix="关键支持",
    )
    secondary_focus_line = (
        _debate_top_view_line(
            debate_summary,
            stance="bearish",
            prefix="关键反对",
        )
        or _debate_watch_focus_line(debate_summary)
        or (
            f"投票分布: 看多 {debate_summary.bull_count} / 看空 {debate_summary.bear_count}"
            f" / 中性 {debate_summary.neutral_count}"
        )
    )
    role_selection_line = (
        f"选角理由: {debate_summary.role_selection_summary}"
        if debate_summary.role_selection_summary
        else ""
    )
    return _unique_lines(
        (
            outcome_line,
            round_flow_line,
            bullish_line,
            role_selection_line,
            secondary_focus_line,
        ),
    )


def _home_debate_process_card_lines(
    debate_summary: DashboardDebateSummary,
) -> tuple[str, ...]:
    lines = _debate_process_lines(debate_summary)
    trimmed = lines[1:] if lines and lines[0].startswith("当前结论: ") else lines
    plan_line = (
        f"角色分工: {debate_summary.role_selection_plan}"
        if debate_summary.role_selection_plan
        else ""
    )
    if plan_line and len(trimmed) >= 2:
        return _unique_lines((trimmed[0], trimmed[1], plan_line))
    return trimmed[:3]


def _home_debate_priority_key(
    debate_summary: DashboardDebateSummary,
) -> tuple[int, ...] | tuple[int, ... | str]:
    verdict = debate_summary.research_verdict.strip()
    verdict_rank = 0
    if verdict:
        if "优先" in verdict and ("复核" in verdict or "跟踪" in verdict):
            verdict_rank = 3
        elif any(
            keyword in verdict
            for keyword in ("纸面复核", "纸面跟踪", "重点跟踪", "重点观察")
        ):
            verdict_rank = 2
        else:
            verdict_rank = 1
    cross_market_present = int(
        any(view.role_id == "cross_market" for view in debate_summary.agent_views)
    )
    structure_count = sum(
        1
        for value in (
            debate_summary.primary_risk_gate,
            debate_summary.next_trigger,
            debate_summary.historical_context_note,
            debate_summary.role_reliability_lines,
            debate_summary.support_points,
            debate_summary.opposition_points,
            debate_summary.watch_items,
        )
        if value
    )
    return (
        verdict_rank,
        int(bool(debate_summary.next_trigger)),
        int(bool(debate_summary.primary_risk_gate)),
        int(bool(debate_summary.historical_context_note)),
        int(bool(debate_summary.role_reliability_lines)),
        cross_market_present,
        structure_count,
        int(debate_summary.disagreement_score * 100),
        debate_summary.round_count,
        int(debate_summary.adjusted_score * 100),
        debate_summary.created_at,
    )


def _salient_home_debates(
    debates: tuple[DashboardDebateSummary, ...],
) -> tuple[DashboardDebateSummary, ...]:
    ordered = _ordered_home_debates(debates)
    prioritized = tuple(
        debate_summary
        for debate_summary in ordered
        if debate_summary_signal_value_tier(debate_summary) != "low"
    )
    if prioritized:
        return prioritized
    return ordered[:1]


def _debate_signal_value_tier(
    debate_summary: DashboardDebateSummary,
) -> str:
    return debate_summary_signal_value_tier(debate_summary)


def _ordered_home_debates(
    debates: tuple[DashboardDebateSummary, ...],
) -> tuple[DashboardDebateSummary, ...]:
    return tuple(
        sorted(
            debates,
            key=_home_debate_priority_key,
            reverse=True,
        )
    )


def _provider_prioritized_debates(
    provider: DashboardDataProvider,
    signal_date: str,
    *,
    limit: int = 8,
) -> tuple[DashboardDebateSummary, ...]:
    prioritized = getattr(provider, "prioritized_debate_summaries", None)
    if callable(prioritized):
        try:
            return prioritized(signal_date, limit=limit, salient_only=True)
        except TypeError:
            return prioritized(signal_date, salient_only=True)
    try:
        return _salient_home_debates(
            provider.debate_summaries(signal_date, limit=limit)
        )
    except TypeError:
        return _salient_home_debates(provider.debate_summaries(signal_date))[:limit]


def _provider_same_day_task_rows(
    provider: DashboardDataProvider,
    signal_date: str,
    *,
    include_report_insights: bool = True,
) -> tuple[DashboardSameDayTaskRow, ...]:
    try:
        return provider.same_day_task_rows(
            signal_date,
            include_report_insights=include_report_insights,
        )
    except TypeError:
        return provider.same_day_task_rows(signal_date)


def _empty_home_overview(signal_date: str) -> DashboardDateOverview:
    return DashboardDateOverview(
        signal_date=signal_date,
        task_count=0,
        actionable_total=0,
        watch_total=0,
        blocked_total=0,
        top_task_label="",
        top_headline="",
        blocker_headline="",
        focus_headline="",
        workflow_summary="",
        archive_summary="",
    )


def _empty_home_paper_summary(signal_date: str) -> DashboardPaperSummary:
    return DashboardPaperSummary(
        signal_date=signal_date,
        open_positions=0,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(),
    )


def _provider_home_digest_payload(
    provider: DashboardDataProvider,
    task_id: str,
    signal_date: str,
) -> DashboardHomeDigestPayload:
    loader = getattr(provider, "home_digest_payload", None)
    if callable(loader):
        return loader(task_id, signal_date=signal_date)

    task_view = _provider_build_task_digest_view(provider)(
        task_id,
        signal_date=signal_date,
    )
    review_date = _task_view_signal_date(task_view) or signal_date
    rows = _provider_same_day_task_rows(
        provider,
        review_date,
        include_report_insights=False,
    )
    debates: tuple[DashboardDebateSummary, ...] = ()
    if rows:
        spotlights = provider.same_day_candidate_spotlights(review_date, limit=3)
        try:
            overview = provider.date_overview(
                review_date,
                rows=rows,
                spotlights=spotlights,
                debates=debates,
            )
        except TypeError:
            overview = _provider_date_overview(
                provider,
                review_date,
                spotlights=spotlights,
                debates=debates,
            )
        if overview is None:
            overview = _empty_home_overview(review_date)
        paper_summary = provider.paper_summary(review_date)
    else:
        spotlights = ()
        overview = _empty_home_overview(review_date)
        paper_summary = _empty_home_paper_summary(review_date)
    return DashboardHomeDigestPayload(
        task_view=task_view,
        same_day_rows=rows,
        spotlights=spotlights,
        debates=debates,
        overview=overview,
        paper_summary=paper_summary,
    )


def _compact_home_digest_part(value: str, *, limit: int = 56) -> str:
    text = normalize_research_tone(value).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _debate_priority_digest_lines(
    debates: tuple[DashboardDebateSummary, ...],
) -> tuple[str, ...]:
    ordered = _ordered_home_debates(debates)[:3]
    lines: list[str] = []
    for index, debate_summary in enumerate(ordered, start=1):
        conclusion = _debate_conclusion_summary(debate_summary)
        lead = (
            debate_summary.research_verdict.strip()
            or conclusion.cross_market_line.replace("跨市传导: ", "").strip()
            or debate_summary.next_trigger.strip()
            or debate_summary.primary_risk_gate.strip()
            or "先看讨论分歧来源。"
        )
        compact_cross_market_line = _focus_cross_market_digest_line(
            debate_summary=debate_summary,
            focus_display=debate_summary.display_name,
        ).replace(f" | 先看 {debate_summary.display_name}", "", 1)
        compact_context_parts = tuple(
            part.strip()
            for part in compact_cross_market_line.split(" | ")
            if part.strip()
        )
        support_or_context = (
            conclusion.support_line.replace("讨论支持: ", "支持 ")
            if conclusion.support_line
            else conclusion.cross_market_line.replace("跨市传导: ", "传导 ")
        )
        watch_or_risk = ""
        if conclusion.watch_line:
            watch_or_risk = conclusion.watch_line.replace("讨论待确认: ", "待确认 ")
        elif conclusion.opposition_line:
            watch_or_risk = conclusion.opposition_line.replace("讨论反对: ", "反对 ")
        elif conclusion.validation_line:
            watch_or_risk = conclusion.validation_line.replace("确认信号: ", "确认 ")
        elif conclusion.invalidation_line:
            watch_or_risk = conclusion.invalidation_line.replace("失效信号: ", "失效 ")
        elif debate_summary.next_trigger.strip():
            watch_or_risk = f"触发 {debate_summary.next_trigger.strip()}"
        elif debate_summary.primary_risk_gate.strip():
            watch_or_risk = f"卡点 {debate_summary.primary_risk_gate.strip()}"
        if compact_cross_market_line and watch_or_risk:
            normalized_watch = watch_or_risk.strip()
            if normalized_watch in compact_cross_market_line:
                watch_or_risk = ""
        parts = [_compact_home_digest_part(lead)]
        parts.extend(
            _compact_home_digest_part(part) for part in compact_context_parts[:2]
        )
        if len(parts) < 3 and support_or_context:
            parts.append(_compact_home_digest_part(support_or_context))
        if len(parts) < 3 and watch_or_risk:
            parts.append(_compact_home_digest_part(watch_or_risk))
        lines.append(
            f"{index}. {debate_summary.display_name}: "
            f"{' | '.join(part for part in parts[:3] if part)}"
        )
    return tuple(lines)


def _debate_result_lines(
    debate_summary: DashboardDebateSummary,
) -> tuple[str, ...]:
    conclusion = _debate_conclusion_summary(debate_summary)
    return _unique_lines(
        (
            f"结论: {debate_summary.recommended_adjustment_label}",
            (
                f"共识: {debate_summary.consensus}"
                if debate_summary.consensus
                else "共识: 当前仍未形成明确一致结论。"
            ),
            conclusion.decision_line,
            conclusion.active_roles_line,
            conclusion.cross_market_line,
            conclusion.chain_or_trigger_line,
            conclusion.validation_line,
            conclusion.invalidation_line,
            conclusion.support_line,
            conclusion.opposition_line,
            conclusion.watch_line,
            f"分歧分数: {debate_summary.disagreement_score:.2f}",
            conclusion.evidence_line,
            conclusion.history_line,
            conclusion.reliability_line,
        ),
    )


def _home_debate_result_card_lines(
    debate_summary: DashboardDebateSummary,
) -> tuple[str, ...]:
    conclusion = _debate_conclusion_summary(debate_summary)
    has_structured_evidence = any(
        (
            debate_summary.agent_views,
            debate_summary.support_points,
            debate_summary.opposition_points,
            debate_summary.watch_items,
            debate_summary.cross_market_summary,
            debate_summary.cross_market_chain_summary,
            debate_summary.cross_market_validation_summary,
            debate_summary.cross_market_invalidation_summary,
            debate_summary.research_verdict,
            debate_summary.primary_risk_gate,
            debate_summary.next_trigger,
            debate_summary.historical_context_note,
            debate_summary.role_reliability_lines,
        )
    )
    if not has_structured_evidence:
        return (
            f"投票: 看多 {debate_summary.bull_count}"
            f" / 看空 {debate_summary.bear_count}"
            f" / 中性 {debate_summary.neutral_count}",
        )
    decision_line = (
        conclusion.decision_line.replace("研究口径: ", "委员会结论: ", 1)
        .replace("当前结论: ", "委员会结论: ", 1)
        .replace("核心卡点: ", "委员会卡点: ", 1)
    )
    gate_line = (
        f"当前卡点: {debate_summary.primary_risk_gate.strip()}"
        if debate_summary.primary_risk_gate.strip()
        else ""
    )
    trigger_line = (
        f"下一触发: {debate_summary.next_trigger.strip()}"
        if debate_summary.next_trigger.strip()
        else ""
    )
    return _unique_lines(
        (
            decision_line,
            gate_line,
            trigger_line or conclusion.watch_line or conclusion.validation_line,
        )
    )[:3]


def _home_debate_result_card_title(
    debate_summary: DashboardDebateSummary,
) -> str:
    return (
        debate_summary.recommended_adjustment_label.strip()
        or debate_summary.research_verdict.strip()
        or debate_summary.consensus.strip()
        or "等待补齐委员会结论"
    )


def _debate_lane_status_context(
    task_view,
    debates: tuple[DashboardDebateSummary, ...],
) -> tuple[str, tuple[str, ...], str]:
    debate_runtime = load_debate_runtime_config(getattr(task_view, "task_id", ""))
    configured_roles = tuple(debate_runtime.roles)
    configured_labels = tuple(
        _RUNTIME_DEBATE_ROLE_LABELS.get(role, role) for role in configured_roles
    )
    triggered_role_ids = {
        view.role_id
        for debate_summary in debates
        for view in debate_summary.agent_views
    }
    triggered_labels = tuple(
        _RUNTIME_DEBATE_ROLE_LABELS.get(role, role)
        for role in configured_roles
        if role in triggered_role_ids
    )
    missing_labels = tuple(
        _RUNTIME_DEBATE_ROLE_LABELS.get(role, role)
        for role in configured_roles
        if role not in triggered_role_ids
    )
    symbol_count = len(
        {debate_summary.symbol for debate_summary in debates if debate_summary.symbol}
    )
    disagreement_focus = tuple(
        debate_summary.display_name
        for debate_summary in _ordered_home_debates(debates)
        if debate_summary.disagreement_score >= 0.35
    )
    salient_debates = _salient_home_debates(debates)
    background_count = max(0, len(debates) - len(salient_debates))

    if debate_runtime.enabled:
        title = f"{len(configured_roles)} 轨道待命 / {len(triggered_labels)} 已出场"
    elif configured_roles:
        title = "讨论执行关闭"
    else:
        title = "讨论轨道未配置"

    if not configured_roles:
        lines = ("当前任务没有可用讨论角色，先回到候选与纸面证据。",)
        return title, lines, "pressure"

    role_preview = "、".join(configured_labels[:4])
    if len(configured_labels) > 4:
        role_preview = f"{role_preview} 等 {len(configured_labels)} 个角色"
    default_line = f"默认轨道: {role_preview}"
    tuning_line = _runtime_debate_tuning_line(debate_runtime)

    if debates:
        activity_line = f"今日讨论: {len(debates)} 场 / 覆盖 {symbol_count} 个标的 / 已出场 {len(triggered_labels)} 个角色"
    elif debate_runtime.enabled:
        activity_line = "今日讨论: 当前还没触发同日讨论，角色轨道已待命。"
    else:
        activity_line = "今日讨论: 当前关闭执行，只保留角色编排供后续启用。"

    if triggered_labels:
        triggered_preview = "、".join(triggered_labels[:4])
        if len(triggered_labels) > 4:
            triggered_preview = f"{triggered_preview} 等 {len(triggered_labels)} 个角色"
        triggered_line = f"实际出场: {triggered_preview}"
    else:
        triggered_line = "实际出场: 当前还没有角色发言记录。"

    if debates:
        coverage_summary_line = (
            f"价值分层: 高价值 {len(salient_debates)} / 背景 {background_count}"
        )
    else:
        coverage_summary_line = ""

    if missing_labels:
        missing_preview = "、".join(missing_labels[:4])
        if len(missing_labels) > 4:
            missing_preview = f"{missing_preview} 等 {len(missing_labels)} 个角色"
        coverage_line = f"待补轨道: {missing_preview}"
    else:
        coverage_line = "轨道覆盖: 当前默认角色已全部出场。"

    disagreement_line = (
        f"高分歧焦点: {'、'.join(disagreement_focus[:2])}"
        if disagreement_focus
        else "高分歧焦点: 当前没有高分歧标的，优先看共识结论。"
    )
    tone = (
        "pressure"
        if not debate_runtime.enabled or disagreement_focus
        else ("focus" if debates else "archive")
    )
    return (
        title,
        _unique_lines(
            (
                default_line,
                tuning_line,
                activity_line,
                triggered_line,
                coverage_summary_line,
                coverage_line,
                disagreement_line,
            )
        )[:4],
        tone,
    )


def _render_home_debate_process(
    task_view,
    debates: tuple[DashboardDebateSummary, ...],
) -> None:
    st.subheader("多 Agent 讨论过程")
    st.caption("看完委员会裁决后，再回头看为什么会吵起来，只保留影响复核顺序的过程。")
    lane_title, lane_lines, lane_tone = _debate_lane_status_context(task_view, debates)
    _render_cockpit_card(
        kicker="当前轨道",
        title=lane_title,
        lines=lane_lines,
        tone=lane_tone,
    )
    if not debates:
        st.info("当天没有多 Agent 讨论过程。")
        return

    ordered = _salient_home_debates(debates)[:3]
    for start in range(0, len(ordered), 2):
        columns = st.columns(min(len(ordered[start : start + 2]), 2))
        for column, debate_summary in zip(columns, ordered[start : start + 2]):
            with column:
                _render_cockpit_card(
                    kicker=f"{debate_summary.display_name} · {debate_summary.round_count} 轮讨论",
                    title=_debate_process_title(debate_summary),
                    lines=_home_debate_process_card_lines(debate_summary),
                    tone="pressure"
                    if debate_summary.disagreement_score >= 0.35
                    else "archive",
                )
                if _stretch_button(
                    "看讨论",
                    key=f"home-debate-process-{debate_summary.symbol}",
                ):
                    _queue_home_debate_handoff(
                        debate_summary=debate_summary,
                        title="带着多 Agent 讨论过程去看候选复盘",
                        lines=_home_debate_process_card_lines(debate_summary),
                    )
                    st.rerun()


def _render_home_debate_results(
    debates: tuple[DashboardDebateSummary, ...],
) -> None:
    st.subheader("多 Agent 讨论结果")
    st.caption("这里只保留结论、支持/反对和待确认，不把原始辩词堆回首屏。")
    if not debates:
        st.info("候选已更新，讨论回填中；多 Agent 不阻塞盘中候选落盘。")
        return

    ordered = _salient_home_debates(debates)
    _render_cockpit_card(
        kicker="先看顺序",
        title=ordered[0].display_name,
        lines=_debate_priority_digest_lines(ordered),
        tone="focus",
    )
    ordered = ordered[:1]
    for start in range(0, len(ordered), 2):
        columns = st.columns(min(len(ordered[start : start + 2]), 2))
        for column, debate_summary in zip(columns, ordered[start : start + 2]):
            with column:
                _render_cockpit_card(
                    kicker=debate_summary.display_name,
                    title=_home_debate_result_card_title(debate_summary),
                    lines=_home_debate_result_card_lines(debate_summary),
                    tone="blocked"
                    if debate_summary.recommended_adjustment == "lower"
                    else (
                        "pressure"
                        if debate_summary.disagreement_score >= 0.35
                        else "focus"
                    ),
                )
                if _stretch_button(
                    "看结果",
                    key=f"home-debate-result-{debate_summary.symbol}",
                ):
                    _queue_home_debate_handoff(
                        debate_summary=debate_summary,
                        title="带着多 Agent 讨论结果去看候选复盘",
                        lines=_home_debate_result_card_lines(debate_summary),
                    )
                    st.rerun()


def _simple_candidate_card_lines(card: DashboardCandidateCard) -> tuple[str, ...]:
    return _unique_lines(
        (
            (
                f"催化: {card.news_catalyst_summary}"
                if card.news_catalyst_summary
                else ""
            ),
            (
                f"跨市主线: {card.cross_market_summary}"
                if card.cross_market_summary
                else ""
            ),
            (
                f"传导: {_cross_market_chain_lead_summary(card.cross_market_chain_summary)}"
                if card.cross_market_chain_summary
                else ""
            ),
            (
                f"确认: {card.cross_market_validation_summary}"
                if card.cross_market_validation_summary
                else ""
            ),
            (
                f"失效: {card.cross_market_invalidation_summary}"
                if card.cross_market_invalidation_summary
                else ""
            ),
            f"状态: {_action_status_label(card.action_label, card.status_label)}",
            (f"下一步: {_card_next_action(card)}" if _card_next_action(card) else ""),
            (
                f"卡点: {_card_primary_blocker(card)}"
                if _card_primary_blocker(card)
                else ""
            ),
        )
    )[:5]


def _simple_candidate_bucket(
    card: DashboardCandidateCard,
    *,
    cooldown_until: str,
) -> str:
    if card.research_recommendation:
        return "实时推荐"
    if _card_primary_blocker(card):
        return "PM/风控阻塞"
    return "盘中观察"


def _simple_candidate_card_tone(
    card: DashboardCandidateCard,
    *,
    cooldown_until: str,
) -> str:
    if card.research_recommendation:
        return "focus"
    if _card_primary_blocker(card):
        return "blocked"
    if _action_status_label(card.action_label, card.status_label) != "纸面复核":
        return "watch"
    return "focus"


_HOME_STATUS_FILTERS = ("全部", "推荐", "观察", "阻塞")


def _home_status_filter() -> str:
    value = str(st.session_state.get("dashboard_home_status_filter", "全部") or "")
    return value if value in _HOME_STATUS_FILTERS else "全部"


def _set_home_status_filter(value: str) -> None:
    if value in _HOME_STATUS_FILTERS:
        st.session_state["dashboard_home_status_filter"] = value


def _candidate_status_bucket(card: DashboardCandidateCard) -> str:
    status = _action_status_label(card.action_label, card.status_label)
    if card.research_recommendation:
        return "推荐"
    if _card_primary_blocker(card) or "阻塞" in status:
        return "阻塞"
    if (
        status == "纸面复核"
        or "推荐" in card.rank_label
        or "顺位" in card.rank_label
        or card.action_label in {"纸面复核", "优先复核", "上调优先级"}
        or "纸面复核" in card.action_label
        or "优先复核" in card.action_label
    ):
        return "推荐"
    return "观察"


def _snapshot_status_bucket(candidate) -> str:
    status = str(candidate.research_status or "").strip()
    if "阻塞" in status or "不可" in status or "过期" in status:
        return "阻塞"
    if is_home_recommendation(candidate):
        return "推荐"
    return "观察"


def _snapshot_display_status(candidate, *, historical: bool = False) -> str:
    """Use archive wording for recommendation labels on historical dates."""
    status = str(candidate.research_status or "").strip()
    return "历史复核" if historical and _snapshot_status_bucket(candidate) == "推荐" else status


def _snapshot_display_next_step(candidate, *, historical: bool = False) -> str:
    next_step = str(candidate.next_step or "").strip()
    return next_step


def _status_filter_matches(bucket: str, status_filter: str) -> bool:
    return status_filter == "全部" or bucket == status_filter


def _render_home_status_switch(
    *,
    counts: tuple[int, int, int],
    key_prefix: str,
) -> str:
    """Render a small, explicit status filter instead of hiding cards in accordions."""
    current = _home_status_filter()
    labels = (
        ("全部", sum(counts)),
        ("推荐", counts[0]),
        ("观察", counts[1]),
        ("阻塞", counts[2]),
    )
    st.markdown('<div class="aqsp-simple-nav-title">状态</div>', unsafe_allow_html=True)
    for label, count in labels:
        button_label = f"{label}  {count}"
        if _stretch_button(
            button_label,
            key=f"{key_prefix}-{label}",
            type="primary" if label == current else "secondary",
        ):
            _set_home_status_filter(label)
            st.rerun()
    return current


def _historical_home_label(signal_date: str, latest_date: str) -> str:
    selected = signal_date.strip()
    latest = latest_date.strip()
    return "历史回看" if selected and latest and selected != latest else "当前日"


def _simple_candidate_grid(
    cards: tuple[DashboardCandidateCard, ...],
    *,
    cooldown_until: str,
    status_filter: str = "全部",
) -> str:
    rendered_cards: list[str] = []
    for card in cards:
        if not _status_filter_matches(_candidate_status_bucket(card), status_filter):
            continue
        status = _action_status_label(card.action_label, card.status_label)
        bucket = _simple_candidate_bucket(card, cooldown_until=cooldown_until)
        next_step = _card_next_action(card) or "等待下一次实时刷新"
        context = next(
            (
                line
                for line in (
                    (
                        f"跨市 {card.cross_market_summary}"
                        if card.cross_market_summary
                        else ""
                    ),
                    (
                        f"催化 {card.news_catalyst_summary}"
                        if card.news_catalyst_summary
                        else ""
                    ),
                    "；".join(card.reasons[:2]),
                    card.decision_note,
                )
                if line
            ),
            "暂无新增消息，按量价确认复核。",
        )
        rendered_cards.append(
            f'<div class="aqsp-simple-candidate-card '
            f'{_simple_candidate_card_tone(card, cooldown_until=cooldown_until)}">'
            '<div class="aqsp-simple-candidate-top">'
            f'<div class="aqsp-simple-candidate-name">{escape(card.display_name)}</div>'
            f'<div class="aqsp-simple-candidate-score">{card.score:.2f}</div>'
            "</div>"
            f'<div class="aqsp-simple-candidate-status">{escape(bucket)} / {escape(status)}</div>'
            f'<div class="aqsp-simple-candidate-line">下一步: {escape(next_step)}</div>'
            f'<div class="aqsp-simple-candidate-line">{escape(context)}</div>'
            "</div>"
        )
    return (
        '<div class="aqsp-simple-candidate-grid">' + "".join(rendered_cards) + "</div>"
    )


def _provider_home_history_label(
    provider: DashboardDataProvider,
    signal_date: str,
) -> str:
    dates = tuple(getattr(provider, "dashboard_dates", lambda: ())() or ())
    latest = dates[0] if dates else signal_date
    return _historical_home_label(signal_date, latest)


def _render_simple_recommendation_panel(
    *,
    provider: DashboardDataProvider,
    signal_date: str,
    task_view,
    spotlights: tuple[DashboardCandidateSpotlight, ...],
    overview: DashboardDateOverview,
) -> None:
    cards = _home_action_cards(task_view, spotlights)
    recommend_cards, watch_cards, blocked_cards = _classify_candidate_queues(cards)
    status_filter = _home_status_filter()
    history_label = _provider_home_history_label(provider, signal_date)
    runtime_overview = getattr(provider, "runtime_overview", lambda _: None)(
        signal_date
    )
    cooldown = str(getattr(runtime_overview, "cooldown_until", "") or "").strip()
    risk_reason = str(getattr(runtime_overview, "risk_reason", "") or "").strip()
    all_candidates = tuple((*recommend_cards, *watch_cards, *blocked_cards))
    visible_candidates = tuple(
        card
        for card in all_candidates
        if _status_filter_matches(_candidate_status_bucket(card), status_filter)
    )[:5]
    candidate_count = len(recommend_cards) + len(watch_cards) + len(blocked_cards)
    blocker_line = (
        f"原因: 组合保护解除日 {cooldown}"
        if cooldown
        else (f"原因: {risk_reason}" if risk_reason else "原因: 今日条件未到复核档")
    )
    if cooldown and candidate_count > 0:
        hero_title = "今日候选已产生，组合保护仅限制纸面动作"
    elif recommend_cards:
        hero_title = "今日候选已产生"
    else:
        hero_title = "今日无可纸面复核候选"
    st.markdown(
        f'<div class="aqsp-board-section">当天结论 · {escape(history_label)}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <div class="aqsp-simple-summary-strip">
          <div>
            <div class="aqsp-simple-summary-title">{escape(hero_title)}</div>
            <div class="aqsp-simple-summary-line">{escape(blocker_line)}</div>
          </div>
          <div class="aqsp-simple-summary-counts">候选 {candidate_count}<br>纸面 {len(recommend_cards)} / 观察 {max(len(watch_cards), overview.watch_total)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="aqsp-board-section">候选卡</div>'
        f'<div class="aqsp-board-section-note">当前筛选: {escape(status_filter)} · '
        f'{escape(history_label)}只作回看 · 直接展示，不折叠。</div>',
        unsafe_allow_html=True,
    )
    if visible_candidates:
        st.markdown(
            _simple_candidate_grid(
                visible_candidates,
                cooldown_until=cooldown,
                status_filter=status_filter,
            ),
            unsafe_allow_html=True,
        )
    elif all_candidates:
        _render_cockpit_card(
            kicker="候选卡",
            title=f"当前没有“{status_filter}”状态的候选",
            lines=("左侧切换状态可查看其他候选。",),
            tone="archive",
        )
    else:
        _render_cockpit_card(
            kicker="候选卡",
            title="当天暂无候选",
            lines=("实时任务尚未产出可展示的候选卡。",),
            tone="archive",
        )


def _render_simple_agent_panel(
    *,
    provider: DashboardDataProvider,
    signal_date: str,
    debates: tuple[DashboardDebateSummary, ...],
) -> None:
    history_label = _provider_home_history_label(provider, signal_date)
    st.markdown(
        f'<div class="aqsp-board-section">'
        f'{escape("历史 Agent 讨论" if history_label == "历史回看" else "Agent 讨论结果")}</div>',
        unsafe_allow_html=True,
    )
    if not debates:
        runtime_overview = getattr(provider, "runtime_overview", lambda _: None)(
            signal_date
        )
        cooldown = str(getattr(runtime_overview, "cooldown_until", "") or "").strip()
        risk_reason = str(getattr(runtime_overview, "risk_reason", "") or "").strip()
        lines = (
            (
                f"今日没有新的委员会结论；组合保护解除日 {cooldown}。"
                if cooldown
                else "今日没有新的委员会结论。"
            ),
            (
                f"当前卡点: {risk_reason}"
                if risk_reason
                else "最近一次讨论可在归档回看。"
            ),
        )
        _render_cockpit_card(
            kicker="委员会",
            title=(
                "历史无 Agent 讨论记录"
                if history_label == "历史回看"
                else "当天无 Agent 讨论结论"
            ),
            lines=lines,
            tone="blocked" if cooldown else "archive",
        )
        return

    for debate_summary in debates[:1]:
        _render_cockpit_card(
            kicker=debate_summary.display_name,
            title=_home_debate_result_card_title(debate_summary),
            lines=_home_debate_result_card_lines(debate_summary),
            tone=(
                "blocked"
                if debate_summary.recommended_adjustment == "lower"
                else (
                    "pressure" if debate_summary.disagreement_score >= 0.35 else "focus"
                )
            ),
        )


def _render_simple_today_digest(
    *,
    provider: DashboardDataProvider,
    signal_date: str,
    rows: tuple[DashboardSameDayTaskRow, ...],
    task_view,
    overview: DashboardDateOverview,
    spotlights: tuple[DashboardCandidateSpotlight, ...],
    debates: tuple[DashboardDebateSummary, ...],
) -> None:
    """Keep the homepage summary to one compact card; detail belongs in review."""
    history_label = _provider_home_history_label(provider, signal_date)
    st.markdown(
        f'<div class="aqsp-board-section">'
        f'{escape("历史消息汇总" if history_label == "历史回看" else "消息汇总")}</div>',
        unsafe_allow_html=True,
    )
    digest_lines = _same_day_digest_snapshot_lines(
        provider,
        signal_date,
        rows,
        debates,
        spotlights=spotlights,
        source_task_view=task_view,
    )
    message_lines = tuple(
        line for line in digest_lines if not line.startswith("讨论结果:")
    )
    visible_lines = message_lines or digest_lines
    if visible_lines:
        _render_cockpit_card(
            kicker="同日速读",
            title=overview.focus_headline or overview.top_headline or "今天先看这几条",
            lines=_unique_lines(visible_lines)[:3],
            tone=(
                "blocked"
                if overview.blocked_total
                else ("focus" if overview.actionable_total else "archive")
            ),
        )
        return

    fallback_lines = tuple(
        getattr(provider, "runtime_fallback_digest_lines", lambda _: ())(signal_date)
        or ()
    )
    _render_cockpit_card(
        kicker="运行状态",
        title=(
            fallback_lines[0].replace("结论: ", "", 1)
            if fallback_lines
            else "等待当日摘要"
        ),
        lines=(
            fallback_lines[1:4]
            if fallback_lines
            else ("候选与委员会结论会在下一次刷新后汇总。",)
        ),
        tone=_runtime_frontdesk_tone(fallback_lines),
    )


def _set_simple_home_date(provider: DashboardDataProvider, signal_date: str) -> None:
    st.session_state["dashboard_selected_date"] = signal_date
    st.session_state["dashboard_task_id"] = provider.preferred_task_for_date(
        signal_date
    )


def _render_simple_home_date_picker(
    *,
    provider: DashboardDataProvider,
    signal_date: str,
) -> None:
    dates = tuple(provider.dashboard_dates())[:4]
    if not dates:
        return
    st.markdown('<div class="aqsp-simple-nav-title">日期</div>', unsafe_allow_html=True)
    latest_date = dates[0]
    for date_value in dates:
        marker = "当前日" if date_value == latest_date else "历史回看"
        st.markdown(
            f'<div class="aqsp-simple-date-pill-meta">{escape(marker)} · '
            f'{escape(date_value)}</div>',
            unsafe_allow_html=True,
        )
        if _stretch_button(
            date_value,
            key=f"simple-home-date-{date_value}",
            type="primary" if date_value == signal_date else "secondary",
        ):
            _set_simple_home_date(provider, date_value)
            st.rerun()


def _simple_research_unlock_context(
    *,
    provider: DashboardDataProvider,
    signal_date: str,
    overview: DashboardDateOverview,
    queue_counts: tuple[int, int, int] | None = None,
) -> tuple[str, tuple[str, ...], str]:
    runtime_overview = getattr(provider, "runtime_overview", lambda _: None)(
        signal_date
    )
    if queue_counts is None:
        actionable_total = overview.actionable_total
        watch_total = overview.watch_total
        blocked_total = overview.blocked_total
    else:
        actionable_total, watch_total, blocked_total = queue_counts
    candidate_total = actionable_total + watch_total + blocked_total
    if candidate_total > 0:
        title = "研究候选已解锁"
        candidate_line = (
            f"今日 {candidate_total} 张候选卡片可看："
            f"纸面 {actionable_total} / 观察 {watch_total} / 阻塞 {blocked_total}。"
        )
        tone = "unlocked"
    else:
        title = "等待当日候选"
        candidate_line = "当前没有同日候选卡片，先看运行状态和消息雷达。"
        tone = "waiting"

    coldstart_line = ""
    gate_line = ""
    if runtime_overview is not None:
        if getattr(runtime_overview, "coldstart_progress", ""):
            progress = str(runtime_overview.coldstart_progress)
            ready_label = "已达标" if _coldstart_progress_ready(progress) else "积累中"
            coldstart_line = f"冷启动样本 {progress} / {ready_label}"
        gate_detail = (
            str(getattr(runtime_overview, "gate_blocker_line", "") or "").strip()
            or str(
                getattr(runtime_overview, "walkforward_runtime_line", "") or ""
            ).strip()
        )
        if gate_detail:
            gate_line = normalize_research_tone(gate_detail)
            if gate_line.startswith("生产 gate:"):
                gate_line = "生产 gate 未放行:" + gate_line.split(":", 1)[1]

    return (
        title,
        tuple(line for line in (candidate_line, coldstart_line, gate_line) if line),
        tone,
    )


def _simple_runtime_status_lines(
    *,
    provider: DashboardDataProvider,
    signal_date: str,
) -> tuple[str, ...]:
    runtime_overview = getattr(provider, "runtime_overview", lambda _: None)(
        signal_date
    )
    if runtime_overview is None:
        return ("运行状态: 暂无",)

    source = (
        str(getattr(runtime_overview, "effective_source", "") or "").strip()
        or str(getattr(runtime_overview, "requested_source", "") or "").strip()
        or "-"
    )
    data_day = str(
        getattr(runtime_overview, "data_latest_trade_date", "") or ""
    ).strip()
    coldstart = str(getattr(runtime_overview, "coldstart_progress", "") or "").strip()
    cooldown = str(getattr(runtime_overview, "cooldown_until", "") or "").strip()
    lines = [
        f"实时源: {_dashboard_source_boundary_label(source) if source != '-' else '-'}",
        f"数据日: {data_day or signal_date or '-'}",
    ]
    if coldstart:
        ready = "已完成" if _coldstart_progress_ready(coldstart) else "积累中"
        lines.append(f"冷启动: {coldstart} / {ready}")
    if cooldown:
        lines.append(f"组合保护: 至 {cooldown}")
    return tuple(lines[:4])


def _render_simple_home_rail(
    *,
    provider: DashboardDataProvider,
    signal_date: str,
    overview: DashboardDateOverview,
    debates: tuple[DashboardDebateSummary, ...],
    queue_counts: tuple[int, int, int] | None = None,
) -> str:
    actionable_total, watch_total, _blocked_total = queue_counts or (
        overview.actionable_total,
        overview.watch_total,
        overview.blocked_total,
    )
    home_status = getattr(provider, "home_status", None)
    if callable(home_status):
        status = home_status(signal_date, overview=overview)
    else:
        runtime = getattr(provider, "runtime_overview", lambda _: None)(signal_date)
        source = (
            str(getattr(runtime, "effective_source", "") or "").strip()
            or "未记录"
        )
        status = DashboardHomeStatus(
            label=(
                "阻塞"
                if overview.blocked_total
                else "实时推荐"
                if overview.actionable_total
                else "观察"
                if overview.watch_total
                else "等待刷新"
            ),
            detail=(
                f"数据日 {getattr(runtime, 'data_latest_trade_date', '') or signal_date} · "
                f"推荐 {overview.actionable_total} / 观察 {overview.watch_total} / "
                f"阻塞 {overview.blocked_total}"
            ),
            tone="blocked" if overview.blocked_total else "focus",
            actionable_count=overview.actionable_total,
            watch_count=overview.watch_total,
            blocked_count=overview.blocked_total,
            source_label=_dashboard_source_boundary_label(source),
        )
    with st.container(border=True):
        st.markdown(
            f"""
            <div class="aqsp-simple-date-label">DATE</div>
            <div class="aqsp-simple-date">{escape(signal_date or "-")}</div>
            <div class="aqsp-simple-boundary">研究复核 · 历史日期仅作回看</div>
            <div class="aqsp-simple-status-card">
              <div class="aqsp-simple-status-line"><strong>{escape(status.label)}</strong></div>
              <div class="aqsp-simple-status-line">{escape(status.detail)}</div>
              <div class="aqsp-simple-status-line">{escape(status.source_label)}</div>
            </div>
            <div class="aqsp-simple-chip-grid">
              <div class="aqsp-simple-chip">
                <div class="aqsp-simple-chip-value">{status.actionable_count}</div>
                <div class="aqsp-simple-chip-label">推荐</div>
              </div>
              <div class="aqsp-simple-chip">
                <div class="aqsp-simple-chip-value">{status.watch_count}</div>
                <div class="aqsp-simple-chip-label">观察</div>
              </div>
              <div class="aqsp-simple-chip">
                <div class="aqsp-simple-chip-value">{status.blocked_count}</div>
                <div class="aqsp-simple-chip-label">阻塞</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        _render_simple_home_date_picker(provider=provider, signal_date=signal_date)
        _render_home_status_switch(
            counts=(actionable_total, watch_total, _blocked_total),
            key_prefix="simple-home-status",
        )
    return "home"


def _render_simple_app_header(*, updated_at: str) -> None:
    st.markdown(
        f"""
        <div class="aqsp-simple-topbar">
          <div>
            <div class="aqsp-simple-date-label">AQSP</div>
            <div class="aqsp-simple-brand">短线决策看板</div>
          </div>
          <div class="aqsp-simple-updated">
            更新时间 {escape(updated_at)}<br>
            纸面观察 / 非交易指令
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_simple_panel_header() -> None:
    st.markdown(
        """
        <div class="aqsp-simple-panel-head">
            <div class="aqsp-simple-panel-kicker">当天关键结果</div>
          <div class="aqsp-simple-panel-title">结论 · 候选 · 消息汇总 · Agent 讨论结果</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_simple_workspace_shortcuts() -> None:
    for label, workspace in zip(
        ("候选复盘", "纸面跟踪", "归档回看"),
        ("候选复盘", "虚拟盘跟踪", "归档回看"),
    ):
        if _stretch_button(label, key=f"simple-home-shortcut-{workspace}"):
            _set_dashboard_workspace(workspace)
            st.rerun()


def _home_snapshot_path() -> str:
    """Return the runtime-owned home snapshot path without touching the provider."""
    return os.environ.get("AQSP_HOME_SNAPSHOT_PATH", _DEFAULT_HOME_SNAPSHOT_PATH)


def _home_snapshot_index_path(snapshot_path: str) -> str:
    """Resolve the optional exact-date index beside the single snapshot file."""
    configured = os.environ.get("AQSP_HOME_SNAPSHOT_INDEX_PATH", "").strip()
    if configured:
        return configured
    return str(
        Path(snapshot_path).expanduser().with_name("home_dashboard_snapshot_index.json")
    )


def _snapshot_candidate_queue_counts(
    snapshot: HomeDashboardSnapshot,
) -> tuple[int, int, int]:
    """Classify bounded snapshot cards only for the left-rail count display."""
    paper = 0
    watch = 0
    blocked = 0
    for candidate in snapshot.candidates:
        bucket = _snapshot_status_bucket(candidate)
        if bucket == "阻塞":
            blocked += 1
        elif bucket == "观察":
            watch += 1
        else:
            paper += 1
    return paper, watch, blocked


def _snapshot_candidate_grid(
    snapshot: HomeDashboardSnapshot,
    *,
    status_filter: str = "全部",
    historical: bool = False,
) -> str:
    """Render the already bounded candidate cards without recreating task views."""
    cards: list[str] = []
    for candidate in snapshot.candidates:
        if _snapshot_status_bucket(candidate) != "推荐":
            continue
        if not _status_filter_matches("推荐", status_filter):
            continue
        cards.append(_snapshot_candidate_card_markup(candidate, historical=historical))
    return '<div class="aqsp-simple-candidate-grid">' + "".join(cards) + "</div>"


def _snapshot_observation_grid(
    snapshot: HomeDashboardSnapshot,
    *,
    status_filter: str = "全部",
    historical: bool = False,
) -> str:
    """Show bounded live observations when protection blocks recommendations."""
    cards: list[str] = []
    for candidate in snapshot.candidates:
        bucket = _snapshot_status_bucket(candidate)
        if (bucket == "推荐" and not historical) or not _status_filter_matches(
            bucket, status_filter
        ):
            continue
        cards.append(
            _snapshot_candidate_card_markup(candidate, historical=historical, observation=True)
        )
    return '<div class="aqsp-simple-candidate-grid">' + "".join(cards) + "</div>"


def _snapshot_candidate_card_markup(
    candidate: HomeSnapshotCandidate,
    *,
    historical: bool,
    observation: bool = False,
) -> str:
    class_name = (
        "aqsp-simple-candidate-card aqsp-observation-card"
        if observation
        else "aqsp-simple-candidate-card"
    )
    contribution = (
        f'<div class="aqsp-simple-candidate-line">贡献: '
        f'{escape(_compact_snapshot_text("；".join(candidate.score_breakdown)))}</div>'
        if candidate.score_breakdown
        else ""
    )
    return (
        f'<article class="{class_name}">'
        f'<div class="aqsp-simple-candidate-rank">{escape(_compact_snapshot_text(_snapshot_display_status(candidate, historical=historical)))}</div>'
        f'<div class="aqsp-simple-candidate-name">{escape(_compact_snapshot_text(candidate.display_name))}</div>'
        f'<div class="aqsp-simple-candidate-score">{candidate.score:.1f}</div>'
        f'<div class="aqsp-simple-candidate-line">{escape(_compact_snapshot_text(candidate.context))}</div>'
        f'{contribution}'
        f'<div class="aqsp-simple-candidate-line">{("历史记录" if historical else "下一步")}: '
        f'{escape(_compact_snapshot_text(_snapshot_display_next_step(candidate, historical=historical)))}</div>'
        "</article>"
    )


def _snapshot_message_status_label(status: str) -> str:
    return {
        "ok": "可用",
        "empty": "无高影响消息",
        "partial": "部分可用",
        "timeout": "超时",
        "failed": "失败",
        "未产出": "未产出",
    }.get(str(status or "").strip(), str(status or "未产出").strip() or "未产出")


def _snapshot_market_context_lines(snapshot: HomeDashboardSnapshot) -> tuple[str, ...]:
    """Keep structured domestic/global transmission evidence visible and compact."""
    context = snapshot.market_context
    if context is None:
        return ()
    lines: list[str] = []
    if context.overview:
        lines.append(f"跨市综述: {_compact_snapshot_text(context.overview)}")
    for item in context.cross_market[:2]:
        parts = tuple(
            part
            for part in (
                (
                    f"{_compact_snapshot_text(item.theme)} · "
                    f"{_compact_snapshot_text(item.action)}"
                    if item.theme
                    else _compact_snapshot_text(item.action)
                ),
                _compact_snapshot_text(item.summary),
                f"来源: {_compact_snapshot_text(item.source_region)}"
                if item.source_region
                else "",
            )
            if part
        )
        if parts:
            lines.append("跨市传导: " + " · ".join(parts))
    if not lines and context.status:
        lines.append(f"传导状态: {context.status}")
    return tuple(lines[:3])


def _compact_snapshot_text(value: object, *, limit: int = 72) -> str:
    """Keep generated snapshot copy scannable without changing source data."""
    text = normalize_research_tone(str(value or "").strip())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip('，。；、 ')}…"


def _snapshot_selected_date(
    snapshot: HomeDashboardSnapshot,
    available_dates: tuple[str, ...] | None = None,
) -> str:
    """Read the UI-selected date without changing the file-only snapshot contract."""
    date_options = available_dates or snapshot.available_dates
    selected = str(
        st.session_state.get("dashboard_snapshot_selected_date", "") or ""
    ).strip()
    if selected in date_options:
        return selected
    return snapshot.selected_date


def _snapshot_index_selected_date(index: HomeSnapshotIndex) -> str:
    """Resolve a session date against the bounded index without blanking home."""
    available_dates = index.available_dates
    requested = str(
        st.session_state.get("dashboard_snapshot_selected_date", "") or ""
    ).strip()
    if requested in available_dates:
        return requested
    selected = index.selected_date.strip()
    resolved = (
        selected
        if selected in available_dates
        else available_dates[0]
        if available_dates
        else ""
    )
    if resolved and resolved != requested:
        st.session_state["dashboard_snapshot_selected_date"] = resolved
        st.session_state["dashboard_selected_date"] = resolved
    return resolved


def _set_snapshot_home_date(
    snapshot: HomeDashboardSnapshot,
    signal_date: str,
    *,
    available_dates: tuple[str, ...] | None = None,
) -> None:
    """Persist a bounded date selection; content remains file-only until refresh."""
    date_options = available_dates or snapshot.available_dates
    if signal_date not in date_options:
        return
    st.session_state["dashboard_snapshot_selected_date"] = signal_date
    st.session_state["dashboard_selected_date"] = signal_date


def _snapshot_is_expired(
    snapshot: HomeDashboardSnapshot,
    *,
    historical: bool = False,
) -> bool:
    """Block stale live data, but keep exact historical snapshots reviewable."""
    if historical:
        return False
    if not snapshot.stale_after:
        return True
    try:
        if snapshot.is_stale():
            return True
    except ValueError:
        return True
    status = snapshot.source.status.strip().lower()
    stale_markers = (
        "过期",
        "待刷新",
        "等待刷新",
        "stale",
        "expired",
        "unavailable",
        "error",
        "failed",
        "失败",
    )
    return snapshot.source.lag_days > 0 or any(
        marker in status for marker in stale_markers
    )


def _snapshot_freshness_line(
    snapshot: HomeDashboardSnapshot,
    *,
    historical: bool = False,
) -> str:
    """Keep freshness as one compact, user-facing line on the left rail."""
    if _snapshot_is_expired(snapshot, historical=historical):
        return "数据快照已过期/等待刷新"
    source = snapshot.source.effective or "未记录"
    status = snapshot.source.status or "未记录"
    latest = snapshot.source.latest_trade_date or "未记录"
    if historical:
        return f"历史快照 · 源 {source} · 数据日 {latest} · 滞后 {snapshot.source.lag_days} 天"
    return (
        f"新鲜度: {status} · 源 {source} · 数据日 {latest} · "
        f"滞后 {snapshot.source.lag_days} 天"
    )


def _render_snapshot_home_rail(
    snapshot: HomeDashboardSnapshot,
    *,
    available_dates: tuple[str, ...] | None = None,
) -> None:
    """Render snapshot-only date, runtime status, and workspace entry controls."""
    date_options = available_dates or snapshot.available_dates
    selected_date = _snapshot_selected_date(snapshot, date_options)
    latest_date = date_options[0] if date_options else snapshot.selected_date
    historical = _historical_home_label(selected_date, latest_date) == "历史回看"
    queue_counts = _snapshot_candidate_queue_counts(snapshot)
    status_label = (
        "历史回看"
        if historical
        else "阻塞"
        if queue_counts[2] or _snapshot_is_expired(snapshot, historical=historical)
        else "实时推荐"
        if queue_counts[0]
        else "观察"
        if queue_counts[1]
        else "等待刷新"
    )
    history_label = _historical_home_label(selected_date, latest_date)
    st.markdown(
        f"""
        <div class="aqsp-simple-date-label">DATE</div>
        <div class="aqsp-simple-date">{escape(selected_date)}</div>
        <div class="aqsp-simple-boundary">{escape(history_label)} · 研究复核</div>
        <div class="aqsp-simple-status-card">
          <div class="aqsp-simple-status-line"><strong>{escape(status_label)}</strong></div>
          <div class="aqsp-simple-status-line">{escape(_snapshot_freshness_line(snapshot, historical=historical))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('<div class="aqsp-simple-nav-title">日期</div>', unsafe_allow_html=True)
    for date_value in date_options:
        if _stretch_button(
            date_value,
            key=f"snapshot-home-date-{date_value}",
            type="primary" if date_value == selected_date else "secondary",
        ):
            _set_snapshot_home_date(
                snapshot,
                date_value,
                available_dates=date_options,
            )
            st.rerun()
    _render_home_status_switch(
        counts=queue_counts,
        key_prefix="snapshot-home-status",
    )


def _render_snapshot_waiting_card(title: str = "数据快照已过期/等待刷新") -> None:
    """Render a loud stop state without falling through to historical aggregation."""
    _render_cockpit_card(
        kicker="快照状态",
        title=title,
        lines=("首页不会静默回退到全量历史聚合。",),
        tone="blocked",
    )


def _render_snapshot_unavailable_home() -> None:
    """Keep a present-but-invalid snapshot from silently opening the history home."""
    st.markdown('<div class="aqsp-simple-shell">', unsafe_allow_html=True)
    rail_col, content_col = st.columns((0.28, 0.72), gap="large")
    with rail_col:
        st.markdown(
            """
            <div class="aqsp-simple-date-label">DATE</div>
            <div class="aqsp-simple-date">-</div>
            <div class="aqsp-simple-status-card">
              <div class="aqsp-simple-status-line">数据快照已过期/等待刷新</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with content_col:
        _render_simple_panel_header()
        _render_snapshot_waiting_card()
    st.markdown("</div>", unsafe_allow_html=True)


def _render_snapshot_home_board(
    snapshot: HomeDashboardSnapshot,
    *,
    available_dates: tuple[str, ...] | None = None,
) -> None:
    """Render the home page from one bounded runtime snapshot file only."""
    st.markdown('<div class="aqsp-simple-shell">', unsafe_allow_html=True)
    rail_col, content_col = st.columns((0.28, 0.72), gap="large")
    with rail_col:
        if available_dates is None:
            _render_snapshot_home_rail(snapshot)
        else:
            _render_snapshot_home_rail(snapshot, available_dates=available_dates)
    with content_col:
        _render_simple_panel_header()
        selected_date = _snapshot_selected_date(snapshot, available_dates)
        date_options = available_dates or snapshot.available_dates
        latest_date = date_options[0] if date_options else snapshot.selected_date
        history_label = _historical_home_label(selected_date, latest_date)
        historical = history_label == "历史回看"
        status_filter = _home_status_filter()
        if _snapshot_is_expired(snapshot, historical=historical):
            _render_snapshot_waiting_card()
        elif selected_date != snapshot.selected_date:
            _render_snapshot_waiting_card(
                f"已选择 {selected_date}，等待该日期快照刷新"
            )
        else:
            message_status_label = _snapshot_message_status_label(
                snapshot.message_status
            )
            recommend_candidates = tuple(
                candidate
                for candidate in snapshot.candidates
                if not historical
                and _snapshot_status_bucket(candidate) == "推荐"
                and _status_filter_matches("推荐", status_filter)
            )
            non_recommend_candidates = tuple(
                candidate
                for candidate in snapshot.candidates
                if (historical or _snapshot_status_bucket(candidate) != "推荐")
                and _status_filter_matches(
                    _snapshot_status_bucket(candidate), status_filter
                )
            )
            if snapshot.summaries:
                conclusion_title = _compact_snapshot_text(snapshot.summaries[0])
            elif historical and non_recommend_candidates:
                conclusion_title = f"历史日期保留 {len(non_recommend_candidates)} 个候选"
            elif recommend_candidates:
                conclusion_title = f"当天有 {len(recommend_candidates)} 个实时候选"
            elif non_recommend_candidates:
                conclusion_title = "当前无实时推荐，保留观察对象"
            else:
                conclusion_title = "当天暂无推荐"
            conclusion_lines = tuple(
                line
                for line in (
                    f"状态: {history_label} · 筛选 {status_filter}",
                    f"数据: {_snapshot_freshness_line(snapshot, historical=historical)}",
                    *(_compact_snapshot_text(line) for line in snapshot.summaries[1:3]),
                )
                if line
            )[:4]
            st.markdown(
                f'<div class="aqsp-board-section">{("历史结论" if historical else "当天结论")} · {escape(history_label)}</div>',
                unsafe_allow_html=True,
            )
            _render_cockpit_card(
                kicker="当天结论",
                title=conclusion_title,
                lines=conclusion_lines or ("等待下一次实时刷新。",),
                tone=(
                    "blocked"
                    if not recommend_candidates and non_recommend_candidates
                    else "focus"
                    if recommend_candidates
                    else "archive"
                ),
            )

            st.markdown(
                f'<div class="aqsp-board-section">{("历史候选卡" if historical else "候选卡")}</div>'
                f'<div class="aqsp-board-section-note">当前筛选: {escape(status_filter)}</div>',
                unsafe_allow_html=True,
            )
            if recommend_candidates:
                st.markdown(
                    _snapshot_candidate_grid(
                        snapshot,
                        status_filter=status_filter,
                    ),
                    unsafe_allow_html=True,
                )
            if non_recommend_candidates:
                st.markdown(
                    '<div class="aqsp-board-section-note">观察 / 阻塞对象</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    _snapshot_observation_grid(
                        snapshot,
                        status_filter=status_filter,
                        historical=historical,
                    ),
                    unsafe_allow_html=True,
                )
            if not recommend_candidates and not non_recommend_candidates:
                _render_cockpit_card(
                    kicker="候选卡",
                    title=(
                        "当前筛选没有候选"
                        if snapshot.candidates
                        else "当天暂无候选卡"
                    ),
                    lines=(
                        "左侧切换状态查看观察或阻塞对象。"
                        if snapshot.candidates
                        else "实时任务尚未产出候选。"
                    ,),
                    tone="archive",
                )

            market_context_lines = _snapshot_market_context_lines(snapshot)
            if snapshot.messages:
                news_lines = tuple(
                    " · ".join(
                        part
                        for part in (
                            _compact_snapshot_text(message.impact),
                            _compact_snapshot_text(message.title),
                            _compact_snapshot_text(message.summary),
                            _compact_snapshot_text(message.source),
                        )
                        if part
                    )
                    for message in snapshot.messages[:3]
                )
            else:
                message_empty_line = (
                    "消息源已返回，但当前没有筛出高影响事件。"
                    if message_status_label not in {"未产出", "失败", "超时"}
                    else "等待消息雷达产出当前交易日结果。"
                )
                news_lines = (message_empty_line,)
            news_lines = tuple((*market_context_lines, *news_lines)[:5])

            snapshot_debates = tuple(snapshot.debates)
            if not snapshot_debates:
                debate_title = (
                    "历史无 Agent 讨论记录"
                    if history_label == "历史回看"
                    else "当天无 Agent 讨论结论"
                )
                debate_lines = (
                    "该日期没有落盘讨论结果。"
                    if history_label == "历史回看"
                    else "下一次实时任务完成后更新。",
                )
                debate_kicker = "Agent 讨论"
                debate_tone = "archive"
            else:
                debate_lines = tuple(
                    " | ".join(
                        part
                        for part in (
                            _compact_snapshot_text(debate.display_name),
                            _compact_snapshot_text(debate.conclusion),
                            (
                                f"卡点: {_compact_snapshot_text(debate.primary_risk_gate)}"
                                if debate.primary_risk_gate
                                else ""
                            ),
                            (
                                f"下一触发: {_compact_snapshot_text(debate.next_trigger)}"
                                if debate.next_trigger
                                else ""
                            ),
                            (
                                f"{debate.process_summary}"
                                if debate.process_summary
                                else (
                                    f"轮次 {debate.round_count} · 角色 "
                                    + "、".join(debate.active_roles[:3])
                                    if debate.round_count or debate.active_roles
                                    else ""
                                )
                            ),
                        )
                        if part
                    )
                    for debate in snapshot_debates[:3]
                )
                debate_title = f"{len(snapshot_debates)} 个候选完成多 Agent 讨论"
                debate_kicker = "Agent 讨论结果"
                debate_tone = (
                    "pressure"
                    if any(debate.primary_risk_gate for debate in snapshot_debates)
                    else "focus"
                )

            message_title = (
                "历史消息汇总"
                if history_label == "历史回看"
                else "当天消息汇总"
            ) if snapshot.messages else (
                "历史暂无有效消息事件"
                if history_label == "历史回看"
                else "当天暂无有效消息事件"
            )
            message_tone = (
                "focus"
                if snapshot.messages and message_status_label in {"可用", "部分可用"}
                else "pressure"
                if snapshot.messages
                else "archive"
            )
            message_col, agent_col = st.columns(2, gap="medium")
            with message_col:
                _render_cockpit_card(
                    kicker=f"消息与传导 · {message_status_label}",
                    title=message_title,
                    lines=news_lines,
                    tone=message_tone,
                )
            with agent_col:
                _render_cockpit_card(
                    kicker=debate_kicker,
                    title=debate_title,
                    lines=debate_lines,
                    tone=debate_tone,
                )
    st.markdown("</div>", unsafe_allow_html=True)


def _render_simple_home_board(
    *,
    provider: DashboardDataProvider,
    signal_date: str,
    task_view,
    same_day_rows: tuple[DashboardSameDayTaskRow, ...],
    same_day_spotlights: tuple[DashboardCandidateSpotlight, ...],
    same_day_debates: tuple[DashboardDebateSummary, ...],
    overview: DashboardDateOverview,
    paper_summary: DashboardPaperSummary,
) -> None:
    st.markdown('<div class="aqsp-simple-shell">', unsafe_allow_html=True)
    cards = _home_action_cards(task_view, same_day_spotlights)
    recommend_cards, watch_cards, blocked_cards = _classify_candidate_queues(cards)
    queue_counts = (len(recommend_cards), len(watch_cards), len(blocked_cards))
    rail_col, content_col = st.columns((0.28, 0.72), gap="large")
    with rail_col:
        _render_simple_home_rail(
            provider=provider,
            signal_date=signal_date,
            overview=overview,
            debates=same_day_debates,
            queue_counts=queue_counts,
        )
    with content_col:
        _render_simple_panel_header()
        _render_simple_recommendation_panel(
            provider=provider,
            signal_date=signal_date,
            task_view=task_view,
            spotlights=same_day_spotlights,
            overview=overview,
        )
        committee_col, digest_col = st.columns(2, gap="medium")
        with committee_col:
            _render_simple_agent_panel(
                provider=provider,
                signal_date=signal_date,
                debates=same_day_debates,
            )
        with digest_col:
            _render_simple_today_digest(
                provider=provider,
                signal_date=signal_date,
                rows=same_day_rows,
                task_view=task_view,
                overview=overview,
                spotlights=same_day_spotlights,
                debates=same_day_debates,
            )
    st.markdown("</div>", unsafe_allow_html=True)


def _home_reading_order_lines(
    *,
    task_view,
    overview: DashboardDateOverview,
    paper_summary: DashboardPaperSummary,
    spotlights: tuple[DashboardCandidateSpotlight, ...],
    debates: tuple[DashboardDebateSummary, ...],
) -> tuple[str, ...]:
    merged_cards = _home_action_cards(task_view, spotlights)
    recommend_cards, watch_cards, blocked_cards = _classify_candidate_queues(
        merged_cards
    )
    focus_card = _home_primary_focus_card(recommend_cards, watch_cards, blocked_cards)
    blocked_focus = _home_blocked_focus_card(blocked_cards)

    if paper_summary.pending_entries or paper_summary.not_executable:
        paper_line = _join_display_parts(
            "🧪 先看纸面验证",
            _safe_paper_summary_detail(
                paper_summary,
                fallback=(
                    f"入场待核对 {paper_summary.pending_entries} / "
                    f"不可成交 {paper_summary.not_executable}"
                ),
            ),
        )
    elif paper_summary.open_positions:
        paper_line = _join_display_parts(
            "🧪 先看纸面持有",
            _safe_paper_summary_detail(
                paper_summary,
                fallback=(
                    f"当前纸面持有 {paper_summary.open_positions} 笔，先核对退出条件。"
                ),
            ),
        )
    else:
        paper_line = "纸面验证: 暂无新的纸面入场或不可成交事件。"

    if focus_card is not None:
        focus_line = _join_display_parts(
            "🎯 主看候选",
            focus_card.display_name,
            _action_status_label(focus_card.action_label, focus_card.status_label),
            _card_primary_blocker(focus_card) or _card_next_action(focus_card),
        )
    elif debates:
        debate_focus = _ordered_home_debates(debates)[0]
        followup_line = _debate_watch_focus_line(debate_focus)
        if followup_line.startswith("确认信号: ") or followup_line.startswith(
            "失效信号: "
        ):
            followup_line = (
                _debate_conclusion_summary(debate_focus).watch_line
                or _debate_conclusion_summary(debate_focus).trigger_line
            )
        followup_line = (
            followup_line.replace("确认信号: ", "确认 ")
            .replace("失效信号: ", "失效 ")
            .replace("下一触发: ", "触发 ")
            .replace("讨论待确认: ", "待确认 ")
        )
        focus_line = _join_display_parts(
            "🎯 主看分歧",
            debate_focus.display_name,
            debate_focus.primary_risk_gate
            or followup_line
            or _safe_current_research_line(
                debate_focus.next_trigger or debate_focus.adjustment_reason
            ),
        )
    else:
        focus_line = _join_display_parts(
            "🎯 主看研究结论",
            overview.focus_headline
            or overview.top_headline
            or task_view.headline
            or "当前没有显著候选，先看任务摘要。",
        )

    if blocked_focus is not None:
        close_line = _join_display_parts(
            "🔒 最后核对卡点",
            blocked_focus.display_name,
            _card_primary_blocker(blocked_focus) or _card_emphasis(blocked_focus),
        )
    elif _report_archive_status(task_view) != "无归档":
        close_line = _join_display_parts(
            "📚 收盘后回看归档",
            _safe_research_hint_line(
                task_view.next_day_focus_lines[0]
                if task_view.next_day_focus_lines
                else (
                    task_view.report_summary_lines[0]
                    if task_view.report_summary_lines
                    else overview.archive_summary
                )
            ),
        )
    else:
        close_line = "📚 收盘后补归档: 当前先按研究证据走，等收盘复盘补齐历史记录。"

    return _unique_lines((paper_line, focus_line, close_line))


def _runtime_boundary_card_context(task_view) -> tuple[str, tuple[str, ...], str]:
    goal_switches = load_goal_switches()
    debate_runtime = load_debate_runtime_config(getattr(task_view, "task_id", ""))
    live_short_enabled = goal_switches.switch_enabled(
        "live_short_runtime", default=True
    )
    historical_validation_only = goal_switches.switch_enabled(
        "historical_validation_only",
        default=True,
    )
    boundary_guard_enabled = goal_switches.switch_enabled(
        "enforce_live_vs_history_boundary",
        default=True,
    )
    auto_optimization_enabled = goal_switches.switch_enabled(
        "auto_optimization_proposals",
        default=True,
    )
    auto_apply_enabled = goal_switches.switch_enabled(
        "auto_optimization_apply_runtime",
        default=False,
    )
    runtime_switch_line = _goal_switch_runtime_line(goal_switches)

    if live_short_enabled and historical_validation_only:
        realtime_line = (
            "实时链路: 盘中任务优先实时数据；历史链路只做回测、验证和阈值冻结。"
        )
    elif live_short_enabled:
        realtime_line = "实时链路: 当前仍以实时任务为主，但历史边界声明不完整。"
    else:
        realtime_line = "实时链路: 当前未显式强调实时优先，需回看运行配置。"

    role_summary = _runtime_role_labels(debate_runtime.roles)
    if debate_runtime.enabled and debate_runtime.roles:
        debate_line = (
            f"讨论层: 已启用 {len(debate_runtime.roles)} 个角色，"
            f"当前任务默认 {role_summary}；结论仅供复核，不改写候选排序。"
        )
    elif debate_runtime.roles:
        debate_line = (
            f"讨论层: 当前关闭执行；仍保留 {len(debate_runtime.roles)} 个角色编排，"
            f"默认 {role_summary}；结论仅供复核，不改写候选排序。"
        )
    else:
        debate_line = "讨论层: 当前未配置可用角色；候选排序仅使用确定性评分。"
    debate_tuning_line = _runtime_debate_tuning_line(debate_runtime)

    if auto_apply_enabled:
        optimization_line = "优化层: 结果已允许接近运行参数，需优先核对冻结与验证边界。"
    elif auto_optimization_enabled:
        optimization_line = "优化层: 只产出 proposal，不直接写回运行参数。"
    else:
        optimization_line = "优化层: 当前关闭自动优化提案，先维持冻结参数运行。"

    track_line = _goal_track_focus_line(goal_switches)

    tone = "archive"
    title = "实时优先 / 研究增强"
    if not boundary_guard_enabled or auto_apply_enabled:
        tone = "pressure"
        title = "本地实验边界"
    elif not debate_runtime.enabled:
        title = "实时优先 / 讨论层待启用"
    else:
        tone = "focus"

    return (
        title,
        (
            realtime_line,
            runtime_switch_line,
            debate_line,
            debate_tuning_line or optimization_line,
            optimization_line if debate_tuning_line else track_line,
            track_line if debate_tuning_line else "",
        ),
        tone,
    )


def _runtime_debate_tuning_line(debate_runtime) -> str:
    parts: list[str] = []
    if getattr(debate_runtime, "explicit_roles", False):
        requested = _runtime_role_labels(getattr(debate_runtime, "requested_roles", ()))
        if requested:
            parts.append(f"显式轨道 {requested}")
    focus_summary = _runtime_role_labels(getattr(debate_runtime, "focus_roles", ()))
    if focus_summary:
        parts.append(f"聚焦 {focus_summary}")
    disabled_summary = _runtime_role_labels(
        getattr(debate_runtime, "disabled_roles", ())
    )
    if disabled_summary:
        parts.append(f"停用 {disabled_summary}")
    if not parts:
        return ""
    return "轨道裁剪: " + " / ".join(parts) + "。"


def _goal_switch_runtime_line(goal_switches: GoalSwitchMatrix) -> str:
    boundary_guard_enabled = goal_switches.switch_enabled(
        "enforce_live_vs_history_boundary",
        default=True,
    )
    fallback_chain_enabled = goal_switches.switch_enabled(
        "realtime_fallback_chain",
        default=True,
    )
    domestic_enabled = goal_switches.switch_enabled(
        "domestic_market_intelligence",
        default=True,
    )
    global_enabled = goal_switches.switch_enabled(
        "global_market_intelligence",
        default=True,
    )
    guard_label = "开" if boundary_guard_enabled else "关(仅本地实验)"
    fallback_label = "开" if fallback_chain_enabled else "关"
    domestic_label = "开" if domestic_enabled else "关"
    global_label = "开" if global_enabled else "关"
    return (
        "运行开关: "
        f"守卫 {guard_label} / 回退链 {fallback_label} / "
        f"国内情报 {domestic_label} / 海外情报 {global_label}。"
    )


def _goal_track_focus_line(goal_switches: GoalSwitchMatrix) -> str:
    tracks = goal_switches.prioritized_tracks(limit=3)
    if not tracks:
        return "推进主线: 当前未显式配置主线轨道，需回看 goal switches。"
    parts = []
    for item in tracks:
        priority = _GOAL_TRACK_PRIORITY_LABELS.get(
            item.priority.lower(), item.priority.upper()
        )
        label = item.label.strip() or item.track_id
        parts.append(f"{priority} {label}")
    return "推进主线: " + "；".join(parts) + "。"


def _home_brief_cards(
    *,
    task_view,
    overview: DashboardDateOverview,
    paper_summary: DashboardPaperSummary,
    research_summary: ResearchSummary | None,
    spotlights: tuple[DashboardCandidateSpotlight, ...],
    debates: tuple[DashboardDebateSummary, ...],
) -> tuple[_HomeBriefCard, ...]:
    merged_cards = _home_action_cards(task_view, spotlights)
    recommend_cards, watch_cards, blocked_cards = _classify_candidate_queues(
        merged_cards
    )
    focus_card = _home_primary_focus_card(recommend_cards, watch_cards, blocked_cards)
    blocked_focus = _home_blocked_focus_card(blocked_cards)
    radar = _research_radar_card(research_summary)

    if focus_card is not None:
        focus_title = focus_card.display_name
        focus_spotlight = next(
            (item for item in spotlights if item.symbol == focus_card.symbol),
            None,
        )
        focus_debate = next(
            (
                item
                for item in _ordered_home_debates(debates)
                if item.symbol == focus_card.symbol
            ),
            None,
        )
        discussion_lines = _home_focus_discussion_lines(
            spotlight=focus_spotlight,
            debate_summary=focus_debate,
        )
        conclusion_lines = _home_focus_conclusion_lines(
            focus_card=focus_card,
            spotlight=focus_spotlight,
            debate_summary=focus_debate,
        )
        if conclusion_lines:
            focus_lines = conclusion_lines[:3]
        elif len(discussion_lines) >= 3:
            focus_lines = discussion_lines[:3]
        elif discussion_lines:
            focus_lines = _unique_lines(
                (
                    _join_display_parts(
                        "状态",
                        _action_status_label(
                            focus_card.action_label,
                            focus_card.status_label,
                        ),
                        separator=": ",
                    ),
                ),
                discussion_lines,
            )
        else:
            focus_lines = _unique_lines(
                (
                    _join_display_parts(
                        "状态",
                        _action_status_label(
                            focus_card.action_label,
                            focus_card.status_label,
                        ),
                        separator=": ",
                    ),
                    (
                        f"复核: {focus_card.review_meta}"
                        if _has_review_meta(focus_card.review_meta)
                        else ""
                    ),
                    f"复核线索: {_card_next_action(focus_card)}",
                )
            )
    elif debates:
        debate_focus = _ordered_home_debates(debates)[0]
        focus_title = debate_focus.display_name
        discussion_lines = _home_focus_conclusion_lines(debate_summary=debate_focus)
        focus_lines = (
            discussion_lines[:3]
            if discussion_lines
            else _unique_lines(
                (
                    f"多 Agent: {debate_focus.recommended_adjustment_label} / 分歧 {debate_focus.disagreement_score:.2f}",
                    (
                        f"共识: {debate_focus.consensus}"
                        if debate_focus.consensus
                        else "共识: 当前补充结论只作解释，不替代评分。"
                    ),
                    (
                        f"待核对: {debate_focus.risk_warnings[0]}"
                        if debate_focus.risk_warnings
                        else "待核对: 回到候选来龙去脉。"
                    ),
                )
            )
        )
    else:
        focus_title = "当前无显著主推候选"
        focus_lines = _unique_lines(
            (
                overview.focus_headline or overview.top_headline or task_view.headline,
                "阅读提示: 先看继续观察名单和任务摘要，不为了凑数新增纸面对象。",
            )
        )

    paper_title = "纸面侧较轻"
    paper_tone = "archive"
    if paper_summary.pending_entries or paper_summary.not_executable:
        paper_title = "纸面事件待核对"
        paper_tone = "pressure"
    elif paper_summary.open_positions:
        paper_title = "纸面持有跟踪中"
        paper_tone = "focus"
    elif paper_summary.closed_trades:
        paper_title = "已有纸面回写"
    paper_lines = _unique_lines(
        (
            f"持有 {paper_summary.open_positions} / 待核对 {paper_summary.pending_entries} / 阻塞 {paper_summary.not_executable} / 关闭 {paper_summary.closed_trades}",
        ),
        tuple(
            _safe_current_research_line(line)
            for line in paper_summary.action_summary_lines[:2]
        ),
    )[:3]

    blocker_title = "阻塞较轻"
    blocker_tone = "archive"
    if blocked_focus is not None:
        blocker_title = blocked_focus.display_name
        blocker_tone = "blocked"
        blocker_lines = _unique_lines(
            (
                f"卡点: {_safe_current_research_line(_card_primary_blocker(blocked_focus) or _card_emphasis(blocked_focus))}",
                f"复核线索: {_card_next_action(blocked_focus)}",
                (
                    f"复核: {blocked_focus.review_meta}"
                    if _has_review_meta(blocked_focus.review_meta)
                    else ""
                ),
            )
        )
    else:
        blocker_lines = _unique_lines(
            (
                overview.blocker_headline,
                "当前没有明显阻塞，以焦点候选和纸面记录作回看线索。",
            )
        )

    research_title = radar.title
    research_lines = _unique_lines(
        radar.lines[:2],
        radar.prereq_lines[:1],
    )[:3]

    return (
        _HomeBriefCard(
            kicker="01 先看什么",
            title=focus_title,
            lines=focus_lines[:3],
            tone="focus" if focus_card is not None else "archive",
        ),
        _HomeBriefCard(
            kicker="02 纸面记录",
            title=paper_title,
            lines=paper_lines
            or ("当前没有纸面关键事件。等出现入场、阻塞或退出记录后再复盘。",),
            tone=paper_tone,
        ),
        _HomeBriefCard(
            kicker="03 风险卡点",
            title=blocker_title,
            lines=blocker_lines[:3],
            tone=blocker_tone,
        ),
        _HomeBriefCard(
            kicker="04 研究进化",
            title=research_title,
            lines=research_lines,
            tone="research",
        ),
    )


def _render_home_brief(
    *,
    task_view,
    overview: DashboardDateOverview,
    paper_summary: DashboardPaperSummary,
    research_summary: ResearchSummary | None,
    spotlights: tuple[DashboardCandidateSpotlight, ...],
    debates: tuple[DashboardDebateSummary, ...],
) -> None:
    cards = _home_brief_cards(
        task_view=task_view,
        overview=overview,
        paper_summary=paper_summary,
        research_summary=research_summary,
        spotlights=spotlights,
        debates=debates,
    )
    card_html = []
    for card in cards:
        line_html = "".join(
            f'<div class="aqsp-brief-line">{escape(line)}</div>'
            for line in card.lines[:3]
        )
        card_html.append(
            f"""
            <div class="aqsp-brief-card {escape(card.tone)}">
              <div class="aqsp-brief-kicker">{escape(card.kicker)}</div>
              <div class="aqsp-brief-title">{escape(card.title)}</div>
              {line_html}
            </div>
            """
        )
    st.markdown(
        "\n".join(
            [
                '<div class="aqsp-brief-grid">',
                *card_html,
                "</div>",
            ]
        ),
        unsafe_allow_html=True,
    )


def _home_evidence_entry_lines(
    *,
    task_view,
    overview: DashboardDateOverview,
    paper_summary: DashboardPaperSummary,
    research_summary: ResearchSummary | None,
) -> tuple[tuple[str, str], ...]:
    paper_line = (
        f"纸面: 持有 {paper_summary.open_positions} / "
        f"待核对 {paper_summary.pending_entries} / 阻塞 {paper_summary.not_executable}"
    )
    candidate_line = _join_display_parts(
        "候选",
        f"{overview.actionable_total} 复核",
        f"{overview.watch_total} 观察",
        f"{overview.blocked_total} 阻塞",
        separator=" · ",
    )
    research_line = (
        research_findings_display(research_summary)
        if research_summary is not None
        else "研究: 当前暂无落盘摘要"
    )
    archive_line = _join_display_parts(
        "归档",
        _report_archive_status(task_view),
        _safe_archive_line(overview.archive_summary),
        separator=" · ",
    )
    return (
        ("🧪 纸面", paper_line),
        ("🧭 候选", candidate_line),
        ("🗂 归档", archive_line or research_line),
    )


def _render_home_evidence_entry(
    *,
    task_view,
    overview: DashboardDateOverview,
    paper_summary: DashboardPaperSummary,
    research_summary: ResearchSummary | None,
) -> None:
    chips = _home_evidence_entry_lines(
        task_view=task_view,
        overview=overview,
        paper_summary=paper_summary,
        research_summary=research_summary,
    )
    st.markdown(
        "\n".join(
            [
                '<div class="aqsp-evidence-strip">',
                *[
                    (
                        '<div class="aqsp-evidence-chip">'
                        f'<div class="aqsp-evidence-title">{escape(title)}</div>'
                        f'<div class="aqsp-evidence-line">{escape(line)}</div>'
                        "</div>"
                    )
                    for title, line in chips
                    if line
                ],
                "</div>",
            ]
        ),
        unsafe_allow_html=True,
    )


def _render_home_reading_order(
    *,
    task_view,
    overview: DashboardDateOverview,
    paper_summary: DashboardPaperSummary,
    spotlights: tuple[DashboardCandidateSpotlight, ...],
    debates: tuple[DashboardDebateSummary, ...],
) -> None:
    lines = _home_reading_order_lines(
        task_view=task_view,
        overview=overview,
        paper_summary=paper_summary,
        spotlights=spotlights,
        debates=debates,
    )
    boundary_title, boundary_lines, boundary_tone = _runtime_boundary_card_context(
        task_view
    )
    reading_col, boundary_col = st.columns((1.15, 0.85))
    with reading_col:
        st.markdown(
            "\n".join(
                [
                    '<div class="aqsp-reading-card">',
                    '<div class="aqsp-reading-title">先看顺序</div>',
                    '<div class="aqsp-reading-main">今天先这样看</div>',
                    *[
                        f'<div class="aqsp-reading-line">{escape(line)}</div>'
                        for line in lines
                    ],
                    "</div>",
                ]
            ),
            unsafe_allow_html=True,
        )
    with boundary_col:
        _render_cockpit_card(
            kicker="当前运行边界",
            title=boundary_title,
            lines=boundary_lines,
            tone=boundary_tone,
        )


def _render_research_radar(summary: ResearchSummary | None) -> None:
    card = _research_radar_card(summary)
    metric_html = "".join(
        f'<div class="aqsp-research-metric">{escape(label)} {escape(value)}</div>'
        for label, value in card.metrics
    )
    line_html = "".join(
        f'<div class="aqsp-research-line">{escape(line)}</div>' for line in card.lines
    )
    prereq_html = (
        '<div class="aqsp-research-prereq">'
        + "".join(
            f'<div class="aqsp-research-prereq-line">{escape(line)}</div>'
            for line in card.prereq_lines
        )
        + "</div>"
        if card.prereq_lines
        else ""
    )
    st.markdown(
        "\n".join(
            [
                '<div class="aqsp-research-radar">',
                '<div class="aqsp-research-kicker">Research Radar</div>',
                f'<div class="aqsp-research-title">{escape(card.title)}</div>',
                f'<div class="aqsp-research-metrics">{metric_html}</div>',
                line_html,
                prereq_html,
                "</div>",
            ]
        ),
        unsafe_allow_html=True,
    )


def _render_home_task_board(
    *,
    rows: tuple[DashboardSameDayTaskRow, ...],
    current_task_id: str,
    task_view,
    spotlights: tuple[DashboardCandidateSpotlight, ...],
    debates: tuple[DashboardDebateSummary, ...],
    paper_summary: DashboardPaperSummary,
    overview: DashboardDateOverview,
) -> None:
    st.subheader("今天先看什么")
    st.caption("先看今天走到哪一步，再看先看这些和纸面记录。")
    _render_daily_workflow(
        rows,
        current_task_id,
        overview,
        show_heading=False,
    )
    action_col, execution_col = st.columns((1.15, 0.85))
    with action_col:
        st.markdown("**先看这些**")
        _render_home_action_rail(
            task_view,
            spotlights,
            debates,
            show_heading=False,
            layout="stack",
        )
    with execution_col:
        st.markdown("**纸面记录**")
        _render_home_execution_snapshot(
            paper_summary,
            task_view=task_view,
            overview=overview,
            show_heading=False,
        )


def _set_dashboard_selection(*, task_id: str, signal_date: str) -> None:
    st.session_state["dashboard_pending_task_id"] = task_id
    st.session_state["dashboard_pending_selected_date"] = signal_date


def _queue_home_selection_handoff(
    *,
    signal_date: str,
    task_id: str,
    task_label: str,
    title: str,
    lines: tuple[str, ...] = (),
) -> None:
    _queue_workspace_handoff(
        target_workspace="决策首页",
        source_workspace="决策首页",
        signal_date=signal_date,
        task_id=task_id,
        task_label=task_label,
        title=title,
        lines=lines,
    )


def _set_dashboard_workspace(workspace: str) -> None:
    st.session_state["dashboard_pending_workspace"] = workspace


def _workspace_nav_items() -> tuple[_WorkspaceNavItem, ...]:
    return (
        _WorkspaceNavItem("首页", "决策首页"),
        _WorkspaceNavItem("候选", "候选复盘"),
        _WorkspaceNavItem("纸面", "虚拟盘跟踪"),
        _WorkspaceNavItem("归档", "归档回看"),
    )


def _workspace_handoff_payload(
    *,
    target_workspace: str,
    source_workspace: str,
    title: str,
    lines: tuple[str, ...],
    symbol: str = "",
    signal_date: str = "",
    task_id: str = "",
    task_label: str = "",
    focus_kind: str = "",
    debate_id: str = "",
    decision_source: str = "",
) -> dict[str, str | tuple[str, ...]]:
    clean_title = title.strip()
    clean_lines = tuple(line.strip() for line in lines if line and line.strip())
    clean_symbol = symbol.strip()
    clean_signal_date = signal_date.strip()
    clean_task_id = task_id.strip()
    clean_task_label = task_label.strip()
    clean_focus_kind = focus_kind.strip()
    clean_debate_id = debate_id.strip()
    clean_decision_source = decision_source.strip()
    if not (
        clean_title
        or clean_lines
        or clean_symbol
        or clean_signal_date
        or clean_task_id
        or clean_task_label
        or clean_focus_kind
        or clean_debate_id
        or clean_decision_source
    ):
        return {}
    payload: dict[str, str | tuple[str, ...]] = {
        "dashboard_pending_handoff_target": target_workspace,
        "dashboard_pending_handoff_source": source_workspace,
        "dashboard_pending_handoff_title": clean_title,
        "dashboard_pending_handoff_lines": clean_lines,
    }
    if clean_symbol:
        payload["dashboard_pending_handoff_symbol"] = clean_symbol
    if clean_signal_date:
        payload["dashboard_pending_handoff_signal_date"] = clean_signal_date
    if clean_task_id:
        payload["dashboard_pending_handoff_task_id"] = clean_task_id
    if clean_task_label:
        payload["dashboard_pending_handoff_task_label"] = clean_task_label
    if clean_focus_kind:
        payload["dashboard_pending_handoff_focus_kind"] = clean_focus_kind
    if clean_debate_id:
        payload["dashboard_pending_handoff_debate_id"] = clean_debate_id
    if clean_decision_source:
        payload["dashboard_pending_handoff_decision_source"] = clean_decision_source
    return payload


def _workspace_jump_state(workspace: str, symbol: str) -> dict[str, str]:
    state = {"dashboard_pending_workspace": workspace}
    selected_symbol = symbol.strip()
    if not selected_symbol:
        return state
    if workspace == "候选复盘":
        state["dashboard_pending_review_symbol"] = selected_symbol
    elif workspace == "虚拟盘跟踪":
        state["dashboard_pending_execution_symbol"] = selected_symbol
    elif workspace == "归档回看":
        state["dashboard_pending_archive_symbol"] = selected_symbol
    return state


def _queue_workspace_jump(workspace: str, symbol: str = "") -> None:
    for key, value in _workspace_jump_state(workspace, symbol).items():
        st.session_state[key] = value


def _queue_workspace_handoff(
    *,
    target_workspace: str,
    source_workspace: str,
    symbol: str = "",
    title: str = "",
    lines: tuple[str, ...] = (),
    signal_date: str = "",
    task_id: str = "",
    task_label: str = "",
    focus_kind: str = "",
    debate_id: str = "",
    decision_source: str = "",
) -> None:
    _queue_workspace_jump(target_workspace, symbol)
    if signal_date:
        st.session_state["dashboard_pending_selected_date"] = signal_date
    if task_id:
        st.session_state["dashboard_pending_task_id"] = task_id
    for key, value in _workspace_handoff_payload(
        target_workspace=target_workspace,
        source_workspace=source_workspace,
        title=title,
        lines=lines,
        symbol=symbol,
        signal_date=signal_date,
        task_id=task_id,
        task_label=task_label,
        focus_kind=focus_kind,
        debate_id=debate_id,
        decision_source=decision_source,
    ).items():
        st.session_state[key] = value


def _consume_workspace_handoff(
    target_workspace: str,
) -> _WorkspaceHandoff | None:
    pending_target = st.session_state.get("dashboard_pending_handoff_target")
    if pending_target != target_workspace:
        return None
    source_workspace = str(
        st.session_state.pop("dashboard_pending_handoff_source", "") or ""
    )
    title = str(st.session_state.pop("dashboard_pending_handoff_title", "") or "")
    raw_lines = st.session_state.pop("dashboard_pending_handoff_lines", ())
    symbol = str(st.session_state.pop("dashboard_pending_handoff_symbol", "") or "")
    signal_date = str(
        st.session_state.pop("dashboard_pending_handoff_signal_date", "") or ""
    )
    task_id = str(st.session_state.pop("dashboard_pending_handoff_task_id", "") or "")
    task_label = str(
        st.session_state.pop("dashboard_pending_handoff_task_label", "") or ""
    )
    focus_kind = str(
        st.session_state.pop("dashboard_pending_handoff_focus_kind", "") or ""
    )
    debate_id = str(
        st.session_state.pop("dashboard_pending_handoff_debate_id", "") or ""
    )
    decision_source = str(
        st.session_state.pop("dashboard_pending_handoff_decision_source", "") or ""
    )
    st.session_state.pop("dashboard_pending_handoff_target", None)
    lines = tuple(str(line).strip() for line in raw_lines if str(line).strip())
    return _WorkspaceHandoff(
        target_workspace=target_workspace,
        source_workspace=source_workspace,
        title=title,
        lines=lines,
        symbol=symbol,
        signal_date=signal_date,
        task_id=task_id,
        task_label=task_label,
        focus_kind=focus_kind,
        debate_id=debate_id,
        decision_source=decision_source,
    )


def _workspace_handoff_focus_label(focus_kind: str) -> str:
    return {
        "card": "研究候选卡",
        "spotlight": "同日跨任务联动",
        "debate": "委员会补充结论",
    }.get(focus_kind.strip(), "")


def _workspace_handoff_notice_lines(
    handoff: _WorkspaceHandoff,
) -> tuple[str, ...]:
    meta_parts = tuple(
        part
        for part in (
            handoff.signal_date,
            handoff.task_label or handoff.task_id,
            _workspace_handoff_focus_label(handoff.focus_kind),
        )
        if part
    )
    detail_lines = tuple(
        line
        for line in (
            (f"交接焦点: {' / '.join(meta_parts)}" if meta_parts else ""),
            (
                f"当前采用口径: {_workspace_handoff_focus_label(handoff.decision_source)}"
                if _workspace_handoff_focus_label(handoff.decision_source)
                else ""
            ),
            (f"讨论批次: {handoff.debate_id}" if handoff.debate_id else ""),
            *handoff.lines,
        )
        if line
    )
    return _unique_lines(detail_lines)


def _render_workspace_handoff_notice(
    *,
    target_workspace: str,
) -> None:
    handoff = _consume_workspace_handoff(target_workspace)
    if handoff is None:
        return
    kicker = (
        f"{handoff.source_workspace} -> {target_workspace}"
        if handoff.source_workspace
        else target_workspace
    )
    _render_cockpit_card(
        kicker=kicker,
        title=handoff.title or "沿上一个工作区继续回放",
        lines=_workspace_handoff_notice_lines(handoff) or ("当前没有额外交接说明。",),
        tone="archive",
    )


def _review_to_archive_handoff_lines(
    *,
    selected_card: DashboardCandidateCard,
    spotlight: DashboardCandidateSpotlight | None = None,
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    return tuple(
        line
        for line in (
            f"当前标的: {selected_card.display_name}",
            _candidate_effective_decision_line(
                selected_card=selected_card,
                spotlight=spotlight,
                debate_summary=debate_summary,
            ),
            (
                f"当前结论: {debate_summary.recommended_adjustment_label} / 分歧 {debate_summary.disagreement_score:.2f}"
                if debate_summary is not None
                else f"当前结论: {_action_status_label(selected_card.action_label, selected_card.status_label)}"
            ),
            (
                f"归档时重点看: {_card_primary_blocker(selected_card)}"
                if _card_primary_blocker(selected_card)
                else f"归档时重点看: {_card_next_action(selected_card)}"
            ),
        )
        if line
    )


def _execution_to_review_handoff_lines(
    *,
    selected_symbol: str,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
    review_card: DashboardCandidateCard | None = None,
    execution_focus=None,
) -> tuple[str, ...]:
    symbol_line = (
        review_card.display_name
        if review_card is not None
        else (
            selected_card.display_name if selected_card is not None else selected_symbol
        )
    )
    paper_line = ""
    if execution_focus is not None:
        paper_line = (
            str(getattr(execution_focus, "execution_status", "") or "").strip()
            or str(getattr(execution_focus, "readiness_status", "") or "").strip()
            or str(getattr(execution_focus, "research_status", "") or "").strip()
        )
    fallback_card = review_card or selected_card
    review_focus_line = ""
    if fallback_card is not None:
        review_focus_line = _card_primary_blocker(fallback_card) or _card_next_action(
            fallback_card
        )
    return tuple(
        line
        for line in (
            f"当前标的: {symbol_line}",
            _candidate_effective_decision_line(
                selected_card=selected_card,
                spotlight=selected_spotlight,
                debate_summary=debate_summary,
            ),
            (f"当前纸面: {paper_line}" if paper_line else ""),
            (
                f"回到复盘先看: {review_focus_line}"
                if review_focus_line
                else "回到复盘先看: 当前研究结论与纸面记录是否一致。"
            ),
        )
        if line
    )


def _execution_to_archive_handoff_lines(
    *,
    selected_symbol: str,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
    execution_focus=None,
) -> tuple[str, ...]:
    symbol_line = (
        selected_card.display_name if selected_card is not None else selected_symbol
    )
    paper_line = ""
    if execution_focus is not None:
        paper_line = (
            str(getattr(execution_focus, "execution_status", "") or "").strip()
            or str(getattr(execution_focus, "holding_status", "") or "").strip()
            or str(getattr(execution_focus, "research_status", "") or "").strip()
        )
    return tuple(
        line
        for line in (
            f"当前标的: {symbol_line}",
            _candidate_effective_decision_line(
                selected_card=selected_card,
                spotlight=selected_spotlight,
                debate_summary=debate_summary,
            ),
            (f"当前纸面: {paper_line}" if paper_line else ""),
            "归档先看: 纸面验证是否支持当前研究结论与后续回看重点。",
        )
        if line
    )


def _archive_to_review_handoff_lines(
    *,
    task_view,
    selected_symbol: str,
    selected_card: DashboardCandidateCard | None,
    review_card: DashboardCandidateCard | None = None,
) -> tuple[str, ...]:
    archive_lines = _archive_next_action_lines(
        task_view=task_view,
        selected_symbol=selected_symbol,
        selected_card=selected_card,
        review_card=review_card,
    )
    lead_line = (
        archive_lines[0] if archive_lines else "回到已有判断和原始记录核对当前结论。"
    )
    return tuple(
        line
        for line in (
            f"当前标的: {selected_symbol}",
            f"归档状态: {_report_archive_status(task_view)}",
            f"回到复盘先看: {lead_line}",
        )
        if line
    )


def _review_to_execution_handoff_lines(
    *,
    selected_card: DashboardCandidateCard,
    spotlight: DashboardCandidateSpotlight | None = None,
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    return tuple(
        line
        for line in (
            f"当前标的: {selected_card.display_name}",
            _candidate_effective_decision_line(
                selected_card=selected_card,
                spotlight=spotlight,
                debate_summary=debate_summary,
            ),
            f"纸面先看: {_card_next_action(selected_card)}",
            (
                f"当前限制: {_card_primary_blocker(selected_card)}"
                if _card_primary_blocker(selected_card)
                else ""
            ),
        )
        if line
    )


def _archive_to_execution_handoff_lines(
    *,
    task_view,
    selected_symbol: str,
    selected_card: DashboardCandidateCard | None,
    spotlight: DashboardCandidateSpotlight | None = None,
    debate_summary: DashboardDebateSummary | None,
    review_card: DashboardCandidateCard | None = None,
) -> tuple[str, ...]:
    archive_lines = _archive_next_action_lines(
        task_view=task_view,
        selected_symbol=selected_symbol,
        selected_card=selected_card,
        review_card=review_card,
    )
    lead_line = (
        archive_lines[0] if archive_lines else "先核对归档结论与纸面验证是否一致。"
    )
    return tuple(
        line
        for line in (
            f"当前标的: {selected_symbol}",
            _candidate_effective_decision_line(
                selected_card=selected_card,
                spotlight=spotlight,
                debate_summary=debate_summary,
            ),
            f"归档状态: {_report_archive_status(task_view)}",
            f"纸面先看: {lead_line}",
        )
        if line
    )


def _workspace_symbol_handoff_title(workspace: str) -> str:
    return {
        "候选复盘": "切到这个标的继续复盘",
        "虚拟盘跟踪": "切到这个标的继续看纸面验证",
        "归档回看": "切到这个标的继续看归档",
    }.get(workspace, "切到这个标的继续回看")


def _workspace_symbol_handoff_lines(
    *,
    workspace: str,
    symbol: str,
    cards: tuple[DashboardCandidateCard, ...],
    spotlights: tuple[DashboardCandidateSpotlight, ...] = (),
    debates: tuple[DashboardDebateSummary, ...] = (),
) -> tuple[str, ...]:
    selected_card, selected_spotlight, debate_summary, review_card = (
        _review_context_for_symbol(
            symbol=symbol,
            cards=cards,
            spotlights=spotlights,
            debates=debates,
        )
    )
    focus_card = review_card or selected_card
    display_name = (
        focus_card.display_name
        if focus_card is not None
        else (
            selected_spotlight.display_name
            if selected_spotlight is not None
            else (debate_summary.display_name if debate_summary is not None else symbol)
        )
    )
    next_focus = ""
    if focus_card is not None:
        next_focus = _card_primary_blocker(focus_card) or _card_next_action(focus_card)
    if not next_focus and selected_spotlight is not None:
        next_focus = _safe_current_research_line(
            selected_spotlight.blocker or selected_spotlight.next_step
        )
    if not next_focus and debate_summary is not None:
        next_focus = (
            debate_summary.primary_risk_gate
            or debate_summary.next_trigger
            or debate_summary.adjustment_reason
        )
    prefix = {
        "候选复盘": "切到复盘先看",
        "虚拟盘跟踪": "切到纸面先看",
        "归档回看": "切到归档先看",
    }.get(workspace, "切到这里先看")
    return tuple(
        line
        for line in (
            f"当前标的: {display_name}",
            _candidate_effective_decision_line(
                selected_card=selected_card,
                spotlight=selected_spotlight,
                debate_summary=debate_summary,
            ),
            (
                f"{prefix}: {next_focus}"
                if next_focus
                else f"{prefix}: 先核对当前结论、阻塞与下一触发。"
            ),
        )
        if line
    )


def _queue_workspace_symbol_handoff(
    *,
    workspace: str,
    symbol: str,
    cards: tuple[DashboardCandidateCard, ...],
    spotlights: tuple[DashboardCandidateSpotlight, ...] = (),
    debates: tuple[DashboardDebateSummary, ...] = (),
    signal_date: str = "",
    task_id: str = "",
    task_label: str = "",
) -> None:
    selected_card, selected_spotlight, debate_summary, _ = _review_context_for_symbol(
        symbol=symbol,
        cards=cards,
        spotlights=spotlights,
        debates=debates,
    )
    source_key = _candidate_effective_decision_source_key(
        selected_card=selected_card,
        spotlight=selected_spotlight,
        debate_summary=debate_summary,
    )
    _queue_workspace_handoff(
        target_workspace=workspace,
        source_workspace=workspace,
        symbol=symbol,
        signal_date=signal_date,
        task_id=task_id,
        task_label=task_label,
        focus_kind=source_key,
        debate_id=debate_summary.debate_id if debate_summary is not None else "",
        decision_source=source_key,
        title=_workspace_symbol_handoff_title(workspace),
        lines=_workspace_symbol_handoff_lines(
            workspace=workspace,
            symbol=symbol,
            cards=cards,
            spotlights=spotlights,
            debates=debates,
        ),
    )


def _workspace_widget_state(
    *,
    pending_workspace: str | None,
    current_workspace: str | None,
    workspace_options: tuple[str, ...],
) -> str:
    if pending_workspace in workspace_options:
        return str(pending_workspace)
    if current_workspace in workspace_options:
        return str(current_workspace)
    return workspace_options[0]


def _render_workspace_navigation(*, pending_workspace: str | None = None) -> str:
    nav_items = _workspace_nav_items()
    workspace_options = [item.name for item in nav_items]
    widget_key = "dashboard_workspace_widget"
    current_workspace = _workspace_widget_state(
        pending_workspace=pending_workspace,
        current_workspace=st.session_state.get(widget_key),
        workspace_options=tuple(workspace_options),
    )
    st.session_state[widget_key] = current_workspace
    st.markdown(
        '<div class="aqsp-nav-section-title">工作区</div>',
        unsafe_allow_html=True,
    )
    columns = st.columns(len(nav_items))
    for column, item in zip(columns, nav_items):
        is_active = item.name == current_workspace
        with column:
            if _stretch_button(
                item.code,
                key=f"workspace-nav-{item.name}",
                type="primary" if is_active else "secondary",
            ):
                st.session_state[widget_key] = item.name
                st.rerun()
            _render_two_line_nav_label(
                _TwoLineNavLabel(code=item.code, name=item.name),
                active=is_active,
            )
    return current_workspace


def _render_date_jump_bar(
    *,
    all_dates: tuple[str, ...],
    selected_date: str,
    provider: DashboardDataProvider,
    current_task_id: str,
) -> None:
    if not all_dates:
        return
    with st.expander("更多日期", expanded=False):
        visible_dates = all_dates[:7]
        columns = st.columns(len(visible_dates))
        for column, signal_date in zip(columns, visible_dates):
            with column:
                is_active = signal_date == selected_date
                resolution = _resolve_task_for_date_with_reason(
                    provider=provider,
                    current_task_id=current_task_id,
                    signal_date=signal_date,
                )
                if _stretch_button(
                    signal_date,
                    key=f"date-jump-{signal_date}",
                    type="primary" if is_active else "secondary",
                ):
                    same_day_rows = provider.same_day_task_rows(signal_date)
                    selected_row = next(
                        (
                            row
                            for row in same_day_rows
                            if row.task_id == resolution.task_id
                        ),
                        None,
                    )
                    _queue_home_selection_handoff(
                        signal_date=signal_date,
                        task_id=resolution.task_id,
                        task_label=(
                            selected_row.task_label
                            if selected_row is not None
                            else resolution.task_id
                        ),
                        title=f"切到 {signal_date} 看这天总控",
                        lines=tuple(
                            line
                            for line in (
                                (
                                    f"切到这天先看: {selected_row.headline or selected_row.phase_summary or selected_row.task_label}"
                                    if selected_row is not None
                                    else ""
                                ),
                                (
                                    f"当前说明: {resolution.reason}"
                                    if resolution.reason
                                    else ""
                                ),
                            )
                            if line
                        ),
                    )
                    st.rerun()
                # 日期按钮后显示次要标签（如"主链推荐"）用caption
                secondary_label = _date_jump_secondary_label(
                    provider,
                    current_task_id,
                    signal_date,
                    resolution=resolution,
                )
                if secondary_label:
                    st.markdown(
                        (
                            '<div class="aqsp-nav-name">'
                            f"{escape(signal_date)} · {escape(secondary_label)}"
                            "</div>"
                        ),
                        unsafe_allow_html=True,
                    )
                if resolution.reason:
                    st.markdown(
                        (
                            '<div class="aqsp-nav-name">'
                            f"⚠ {escape(resolution.reason)}"
                            "</div>"
                        ),
                        unsafe_allow_html=True,
                    )


def _render_execution_focus(
    *,
    provider: DashboardDataProvider,
    task_view,
) -> None:
    st.subheader("虚拟盘跟踪")
    signal_date = task_view.selected_date or task_view.latest_date
    same_day_spotlights = provider.same_day_candidate_spotlights(signal_date)
    same_day_debates = _provider_prioritized_debates(provider, signal_date)
    same_day_task_count = len(provider.same_day_task_rows(signal_date))
    open_positions_frame = provider.open_positions_frame(signal_date=signal_date)
    paper_events_frame = provider.paper_events_frame(limit=50, signal_date=signal_date)
    execution_frame = provider.recent_execution_frame(limit=50, signal_date=signal_date)

    symbol_order = _candidate_symbol_order(
        task_view.detail_cards,
        same_day_spotlights,
        same_day_debates,
    )
    for frame in (paper_events_frame, open_positions_frame, execution_frame):
        if frame.empty or "代码" not in frame.columns:
            continue
        for symbol in frame["代码"].astype(str).tolist():
            if symbol and symbol not in symbol_order:
                symbol_order.append(symbol)

    if not symbol_order:
        st.info("当前日期暂无可聚焦的纸面验证对象。")
        _render_paper_summary(provider.paper_summary(signal_date))
        return

    select_key = f"dashboard-execution-symbol-{task_view.task_id}-{signal_date}"
    pending_symbol = st.session_state.pop("dashboard_pending_execution_symbol", None)
    selected_symbol = _render_workspace_symbol_selector(
        label="当前标的",
        workspace="虚拟盘跟踪",
        symbol_order=symbol_order,
        select_key=select_key,
        pending_symbol=pending_symbol,
        cards=task_view.detail_cards,
        spotlights=same_day_spotlights,
        debates=same_day_debates,
        signal_date=signal_date,
        task_id=task_view.task_id,
        task_label=str(getattr(task_view, "task_label", "") or ""),
    )

    selected_card, selected_spotlight, debate_summary, review_card = (
        _review_context_for_symbol(
            symbol=selected_symbol,
            cards=task_view.detail_cards,
            spotlights=same_day_spotlights,
            debates=same_day_debates,
        )
    )
    scoped_open_positions = _filter_frame_by_symbol(
        open_positions_frame, selected_symbol
    )
    scoped_paper_events = _filter_frame_by_symbol(paper_events_frame, selected_symbol)
    scoped_execution = _filter_frame_by_symbol(
        execution_frame,
        selected_symbol,
        time_prefix=signal_date,
    )
    execution_focus = provider.execution_focus(
        signal_date=signal_date,
        symbol=selected_symbol,
        task_id=task_view.task_id,
    )
    research_context = provider.candidate_research_context(
        signal_date=signal_date,
        symbol=selected_symbol,
        preferred_task_id=task_view.task_id,
    )
    has_execution_activity = not scoped_paper_events.empty or not scoped_execution.empty
    has_holding_activity = not scoped_open_positions.empty
    has_debate_activity = debate_summary is not None
    compact_mode = (
        not has_execution_activity
        and not has_holding_activity
        and not has_debate_activity
    )

    _render_workspace_focus_header(
        title="纸面验证焦点",
        selected_date=signal_date,
        selected_card=selected_card,
        selected_spotlight=selected_spotlight,
        review_card=review_card,
        execution_focus=execution_focus,
        event_count=len(scoped_paper_events.index),
        log_count=len(scoped_execution.index),
        open_position_count=len(scoped_open_positions.index),
        same_day_task_count=same_day_task_count,
    )
    _render_research_path(
        _research_path_steps(
            task_view=task_view,
            selected_symbol=selected_symbol,
            review_card=review_card,
            selected_card=selected_card,
            selected_spotlight=selected_spotlight,
            debate_summary=debate_summary,
            execution_focus=execution_focus,
            event_count=len(scoped_paper_events.index),
            log_count=len(scoped_execution.index),
            open_position_count=len(scoped_open_positions.index),
            archive_status=_report_archive_status(task_view),
        )
    )
    nav_col1, nav_col2, nav_col3 = st.columns(3)
    with nav_col1:
        if _stretch_button(
            "复盘",
            key=f"execution-to-review-{selected_symbol}-{task_view.task_id}-{signal_date}",
            disabled=review_card is None,
        ):
            source_key = _candidate_effective_decision_source_key(
                selected_card=selected_card,
                spotlight=selected_spotlight,
                debate_summary=debate_summary,
            )
            _queue_workspace_handoff(
                target_workspace="候选复盘",
                source_workspace="虚拟盘跟踪",
                symbol=selected_symbol,
                signal_date=signal_date,
                task_id=task_view.task_id,
                task_label=task_view.task_label,
                focus_kind=source_key,
                debate_id=debate_summary.debate_id
                if debate_summary is not None
                else "",
                decision_source=source_key,
                title="带着纸面验证回看研究结论",
                lines=_execution_to_review_handoff_lines(
                    selected_symbol=selected_symbol,
                    selected_card=selected_card,
                    selected_spotlight=selected_spotlight,
                    debate_summary=debate_summary,
                    review_card=review_card,
                    execution_focus=execution_focus,
                ),
            )
            st.rerun()
    with nav_col2:
        if _stretch_button(
            "归档",
            key=f"execution-to-archive-{selected_symbol}-{task_view.task_id}-{signal_date}",
        ):
            source_key = _candidate_effective_decision_source_key(
                selected_card=selected_card,
                spotlight=selected_spotlight,
                debate_summary=debate_summary,
            )
            _queue_workspace_handoff(
                target_workspace="归档回看",
                source_workspace="虚拟盘跟踪",
                symbol=selected_symbol,
                signal_date=signal_date,
                task_id=task_view.task_id,
                task_label=task_view.task_label,
                focus_kind=source_key,
                debate_id=debate_summary.debate_id
                if debate_summary is not None
                else "",
                decision_source=source_key,
                title="带着纸面验证去看归档结论",
                lines=_execution_to_archive_handoff_lines(
                    selected_symbol=selected_symbol,
                    selected_card=selected_card,
                    selected_spotlight=selected_spotlight,
                    debate_summary=debate_summary,
                    execution_focus=execution_focus,
                ),
            )
            st.rerun()
    with nav_col3:
        if _stretch_button(
            "首页",
            key=f"execution-to-home-{selected_symbol}-{task_view.task_id}-{signal_date}",
        ):
            _set_dashboard_workspace("决策首页")
            st.rerun()

    research_col, path_col = st.columns(2)
    with research_col:
        _render_cockpit_card(
            kicker="研究结论",
            title=_workspace_research_status(
                selected_card=selected_card,
                selected_spotlight=selected_spotlight,
                review_card=review_card,
                execution_focus=execution_focus,
            ),
            lines=_execution_research_context_lines(
                selected_card=selected_card,
                selected_spotlight=selected_spotlight,
                debate_summary=debate_summary,
                execution_focus=execution_focus,
            ),
            tone=(
                "pressure"
                if debate_summary is not None
                and debate_summary.disagreement_score >= 0.35
                else ("focus" if selected_card is not None else "archive")
            ),
        )
    with path_col:
        _render_cockpit_card(
            kicker="纸面验证",
            title="当前如何复核",
            lines=_unique_lines(
                _workspace_context_brief(
                    review_card=review_card,
                    selected_card=selected_card,
                    selected_spotlight=selected_spotlight,
                    open_position_count=len(scoped_open_positions.index),
                    has_execution_activity=has_execution_activity,
                    holding_status=execution_focus.holding_status,
                )[1][:2],
                _execution_path_context_lines(
                    selected_card=selected_card,
                    selected_spotlight=selected_spotlight,
                    debate_summary=debate_summary,
                    execution_focus=execution_focus,
                ),
            )
            or ("当前没有新增复核提示，先看纸面入场、纸面事件和日志。",),
            tone=(
                "pressure"
                if not scoped_paper_events.empty
                or (
                    debate_summary is not None
                    and debate_summary.disagreement_score >= 0.35
                )
                else "archive"
            ),
        )

    if compact_mode:
        _render_cockpit_card(
            kicker="纸面记录",
            title="当前尚未进入纸面验证链",
            lines=_unique_lines(
                execution_focus.execution_lines[:2],
                execution_focus.holding_lines[:2],
                (
                    "当前没有纸面入场、纸面日志或纸面持有记录，先按研究结论与复核条件推进。",
                ),
            ),
            tone="archive",
        )
    else:
        execution_col, holding_col, debate_col = st.columns(3)
        with execution_col:
            _render_cockpit_card(
                kicker="纸面事件",
                title=execution_focus.execution_status,
                lines=execution_focus.execution_lines,
                tone="blocked"
                if "阻" in execution_focus.execution_status
                else "archive",
            )
        with holding_col:
            _render_cockpit_card(
                kicker="纸面退出",
                title=execution_focus.holding_status,
                lines=execution_focus.holding_lines,
                tone="focus" if has_holding_activity else "archive",
            )
        with debate_col:
            _render_debate_cockpit(
                debate_summary=debate_summary,
                empty_text="当前标的没有同日多 Agent 记录，纸面验证暂以研究结论和虚拟盘记录为主。",
                tone="pressure" if debate_summary is not None else "archive",
            )

    with st.expander("纸面验证证据", expanded=False):
        evidence_col, logs_col = st.columns(2)
        with evidence_col:
            if research_context is None:
                signal_task_id, evidence_title = _signal_evidence_context(
                    task_view.task_id
                )
            else:
                signal_task_id = research_context["task_id"]
                evidence_title = f"同日研究证据（{research_context['task_label']}）"
            _render_frame(
                evidence_title,
                _filter_frame_by_symbol(
                    provider.latest_signal_frame(
                        limit=30,
                        task_id=signal_task_id,
                        signal_date=signal_date,
                    ),
                    selected_symbol,
                ),
            )
            _render_frame("当日虚拟盘事件", scoped_paper_events)
        with logs_col:
            _render_frame("信号日绑定纸面持有记录", scoped_open_positions)
            _render_frame("当日纸面日志", scoped_execution)


def _task_nav_label(
    task_id: str,
    snapshots: tuple[DashboardTaskSnapshot, ...],
) -> str:
    snapshot_map = {snapshot.task_id: snapshot for snapshot in snapshots}
    snapshot = snapshot_map.get(task_id)
    if snapshot is None:
        return task_id
    return f"{snapshot.task_label} · {snapshot.status_label}"


def _phase_nav_label(row: DashboardSameDayTaskRow) -> str:
    return row.phase_label or row.task_label or row.task_id


def _phase_nav_name(row: DashboardSameDayTaskRow) -> str:
    return row.task_label or row.phase_label or row.task_id


def _top_navigation_context(
    *,
    selected_date: str,
    selected_task_id: str,
    same_day_rows: tuple[DashboardSameDayTaskRow, ...],
    snapshots: tuple[DashboardTaskSnapshot, ...],
) -> tuple[str, tuple[str, ...]]:
    row_map = {row.task_id: row for row in same_day_rows}
    selected_row = row_map.get(selected_task_id)
    if selected_row is not None:
        return (
            f"{selected_date} · {selected_row.phase_label}",
            (
                f"当前位置: {selected_row.task_label} / {selected_row.status_label}",
                f"阅读顺序: 先看当天总控，再展开 {selected_row.phase_label}",
                f"当前焦点: {selected_row.headline}",
            ),
        )

    snapshot_map = {snapshot.task_id: snapshot for snapshot in snapshots}
    selected_snapshot = snapshot_map.get(selected_task_id)
    if selected_snapshot is not None:
        return (
            f"{selected_date or '最新'} · {selected_snapshot.task_label}",
            (
                f"当前位置: {selected_snapshot.task_label} / {selected_snapshot.status_label}",
                f"阅读顺序: 先看当天总控，再展开 {selected_snapshot.task_label}",
                f"当前焦点: {selected_snapshot.headline}",
            ),
        )

    return (
        selected_date or "最新",
        ("暂无可切换的阶段摘要。",),
    )


def _render_same_day_phase_jump_bar(
    *,
    signal_date: str,
    rows: tuple[DashboardSameDayTaskRow, ...],
    current_task_id: str,
) -> None:
    if not rows:
        return
    st.markdown(
        '<div class="aqsp-nav-section-title">阶段</div>', unsafe_allow_html=True
    )
    columns = st.columns(len(rows))
    for column, row in zip(columns, rows):
        with column:
            is_active = row.task_id == current_task_id
            if _stretch_button(
                row.phase_label,
                key=f"phase-jump-{row.task_id}-{signal_date}",
                type="primary" if is_active else "secondary",
            ):
                _queue_home_selection_handoff(
                    signal_date=signal_date,
                    task_id=row.task_id,
                    task_label=row.task_label,
                    title=f"切到 {row.phase_label} 看这段结论",
                    lines=(
                        f"切到这段先看: {row.headline or row.phase_summary or row.task_label}",
                    ),
                )
                st.rerun()
            # 阶段按钮已显示phase_label，删除冗余的two_line_label


def _render_top_navigation_banner(
    *,
    selected_date: str,
    selected_task_id: str,
    same_day_rows: tuple[DashboardSameDayTaskRow, ...],
    snapshots: tuple[DashboardTaskSnapshot, ...],
) -> None:
    title, lines = _top_navigation_context(
        selected_date=selected_date,
        selected_task_id=selected_task_id,
        same_day_rows=same_day_rows,
        snapshots=snapshots,
    )
    st.markdown(
        f"""
        <div class="aqsp-banner">
          <div class="aqsp-banner-title">当前入口</div>
          <div class="aqsp-banner-main">{escape(title)}</div>
          <div class="aqsp-banner-meta">{"<br/>".join(escape(line) for line in lines if line.strip())}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _runtime_status_class(status_label: str) -> str:
    if status_label == "风控阻塞":
        return "risk"
    if status_label == "失败":
        return "error"
    if status_label == "正常跳过":
        return "skip"
    return ""


def _dashboard_source_boundary_label(source_id: str) -> str:
    source = str(source_id or "").strip()
    if not source:
        return ""
    fit = workload_fit_for_source(source).get("live_short", "unknown")
    if source_supports_workload(source, "live_short"):
        return f"实时源 {source}（live_short={fit}）"
    return f"当前实际源 {source} 只适合历史验证，盘中短线不可用（live_short={fit}）"


def _render_runtime_task_runs(
    provider: DashboardDataProvider,
    *,
    log_date: str,
    limit: int = 5,
) -> None:
    load_runs = getattr(provider, "runtime_task_runs", None)
    if not callable(load_runs):
        return
    try:
        runs = load_runs(log_date, limit=limit)
    except TypeError:
        runs = load_runs(log_date)[:limit]
    if not runs:
        return
    st.markdown("#### 最近宝塔任务")
    cards: list[str] = []
    for run in runs:
        details = tuple(line for line in run.detail_lines if line.strip())[:2]
        lines = (run.headline, *details)
        body = "".join(
            f'<div class="aqsp-runtime-line">{escape(line)}</div>'
            for line in lines
            if line.strip()
        )
        status_class = _runtime_status_class(run.status_label)
        cards.append(
            f"""
            <div class="aqsp-runtime-card">
              <div class="aqsp-runtime-top">
                <div class="aqsp-runtime-title">{escape(run.task_label)}</div>
                <div class="aqsp-runtime-status {escape(status_class)}">{escape(run.status_label)}</div>
              </div>
              {body}
            </div>
            """
        )
    st.markdown(
        '<div class="aqsp-runtime-grid">' + "".join(cards) + "</div>",
        unsafe_allow_html=True,
    )


def _render_top_navigation(
    *,
    options: tuple,
    snapshots: tuple[DashboardTaskSnapshot, ...],
    provider: DashboardDataProvider,
    render_controls: bool = True,
) -> tuple[str, str]:
    """Resolve date/task state and optionally render the legacy top controls.

    Workspace pages use the same state resolver but render their own compact
    workspace navigation. Keeping the resolver here prevents button handoffs
    from resetting the selected date or phase.
    """
    pending_date = st.session_state.pop("dashboard_pending_selected_date", None)
    pending_task_id = st.session_state.pop("dashboard_pending_task_id", None)
    if pending_date is not None:
        st.session_state["dashboard_selected_date"] = pending_date
        st.session_state["dashboard_selected_date_select"] = pending_date
        st.session_state["dashboard_selected_date_more"] = pending_date
    if pending_task_id is not None:
        st.session_state["dashboard_task_id"] = pending_task_id
        st.session_state["dashboard_task_id_select"] = pending_task_id

    if not render_controls:
        all_dates = provider.dashboard_dates()
        selected_date_state = str(
            st.session_state.get("dashboard_selected_date", "最新") or "最新"
        )
        selected_date = (
            all_dates[0]
            if all_dates and selected_date_state == "最新"
            else selected_date_state
            if selected_date_state in all_dates
            else (all_dates[0] if all_dates else "")
        )
        same_day_rows = provider.same_day_task_rows(selected_date)
        available_task_ids = [row.task_id for row in same_day_rows]
        if not available_task_ids:
            available_task_ids = [option.task_id for option in options]
        selected_task_id = str(
            st.session_state.get("dashboard_task_id", "") or ""
        )
        if selected_task_id not in available_task_ids:
            selected_task_id = provider.preferred_task_for_date(selected_date)
        st.session_state["dashboard_selected_date"] = selected_date or "最新"
        st.session_state["dashboard_task_id"] = selected_task_id
        return selected_task_id, selected_date

    st.markdown(
        '<div class="aqsp-nav-note">先看当天总控，再按日期和阶段展开。</div>',
        unsafe_allow_html=True,
    )

    all_dates = provider.dashboard_dates()
    if not all_dates:
        task_ids = [option.task_id for option in options]
        selected_task_id = st.selectbox(
            "看哪一段",
            task_ids,
            format_func=lambda task_id: _task_nav_label(task_id, snapshots),
            key="dashboard_task_id_empty",
        )
        st.session_state["dashboard_task_id"] = selected_task_id
        return selected_task_id, ""

    selected_date_state = st.session_state.get("dashboard_selected_date", "最新")
    if selected_date_state != "最新" and selected_date_state not in all_dates:
        selected_date_state = "最新"
        st.session_state["dashboard_selected_date"] = "最新"
        st.session_state["dashboard_selected_date_select"] = "最新"

    date_options = ["最新", *all_dates]
    if st.session_state.get("dashboard_selected_date_select") != selected_date_state:
        st.session_state["dashboard_selected_date_select"] = selected_date_state
    previous_date_label = selected_date_state
    date_col, task_col = st.columns([1.15, 1.35])
    with date_col:
        selected_date_label = st.selectbox(
            "看哪一天",
            date_options,
            key="dashboard_selected_date_select",
        )
    st.session_state["dashboard_selected_date"] = selected_date_label
    st.session_state["dashboard_selected_date_more"] = selected_date_label
    selected_date = (
        all_dates[0] if selected_date_label == "最新" else selected_date_label
    )
    _render_date_jump_bar(
        all_dates=all_dates,
        selected_date=selected_date,
        provider=provider,
        current_task_id=st.session_state.get(
            "dashboard_task_id", provider.default_task_id()
        ),
    )

    same_day_rows = provider.same_day_task_rows(selected_date)
    available_task_ids = [row.task_id for row in same_day_rows]
    if not available_task_ids:
        available_task_ids = [option.task_id for option in options]

    current_task_id = st.session_state.get("dashboard_task_id", "")
    if current_task_id not in available_task_ids:
        current_task_id = provider.preferred_task_for_date(selected_date)
        st.session_state["dashboard_task_id"] = current_task_id
    if st.session_state.get("dashboard_task_id_select") != current_task_id:
        st.session_state["dashboard_task_id_select"] = current_task_id
    previous_task_id = current_task_id

    row_map = {row.task_id: row for row in same_day_rows}
    with task_col:
        selected_task_id = st.selectbox(
            "看哪一段",
            available_task_ids,
            format_func=lambda task_id: (
                _phase_nav_label(row_map[task_id])
                if task_id in row_map
                else _task_nav_label(task_id, snapshots)
            ),
            key="dashboard_task_id_select",
        )
    st.session_state["dashboard_task_id"] = selected_task_id
    if (
        selected_date_label != previous_date_label
        or selected_task_id != previous_task_id
    ):
        selected_row = row_map.get(selected_task_id)
        selected_snapshot = next(
            (
                snapshot
                for snapshot in snapshots
                if snapshot.task_id == selected_task_id
            ),
            None,
        )
        _queue_home_selection_handoff(
            signal_date=selected_date,
            task_id=selected_task_id,
            task_label=(
                selected_row.task_label
                if selected_row is not None
                else (
                    selected_snapshot.task_label
                    if selected_snapshot is not None
                    else selected_task_id
                )
            ),
            title=(
                f"切到 {selected_row.phase_label} 看这段结论"
                if selected_row is not None
                else f"切到 {selected_date} 看这天总控"
            ),
            lines=tuple(
                line
                for line in (
                    (
                        f"切到这段先看: {selected_row.headline or selected_row.phase_summary or selected_row.task_label}"
                        if selected_row is not None
                        else ""
                    ),
                    (
                        f"切到这天先看: {selected_snapshot.headline}"
                        if selected_row is None and selected_snapshot is not None
                        else ""
                    ),
                )
                if line
            ),
        )
    _render_same_day_phase_jump_bar(
        signal_date=selected_date,
        rows=same_day_rows,
        current_task_id=selected_task_id,
    )
    _render_top_navigation_banner(
        selected_date=selected_date,
        selected_task_id=selected_task_id,
        same_day_rows=same_day_rows,
        snapshots=snapshots,
    )

    return selected_task_id, selected_date


def _default_home_selection(provider: DashboardDataProvider) -> tuple[str, str]:
    all_dates = provider.dashboard_dates()
    selected_date_state = str(
        st.session_state.get("dashboard_selected_date", "最新") or "最新"
    )
    selected_date = (
        selected_date_state
        if selected_date_state != "最新" and selected_date_state in all_dates
        else (all_dates[0] if all_dates else "")
    )
    selected_task_id = (
        provider.preferred_task_for_date(selected_date)
        if selected_date
        else provider.default_task_id()
    )
    st.session_state["dashboard_selected_date"] = selected_date or "最新"
    st.session_state["dashboard_task_id"] = selected_task_id
    return selected_task_id, selected_date


def _candidate_option_label(card: DashboardCandidateCard) -> str:
    action_status = _action_status_label(card.action_label, card.status_label)
    return _join_display_parts(
        card.rank_label,
        card.display_name,
        action_status,
        separator=" · ",
    )


def _filter_frame_by_symbol(frame, symbol: str, *, time_prefix: str = ""):
    if frame.empty:
        return frame
    symbol_column = (
        "代码"
        if "代码" in frame.columns
        else "symbol"
        if "symbol" in frame.columns
        else ""
    )
    if not symbol_column:
        return frame.iloc[0:0].copy()
    filtered = frame[frame[symbol_column].astype(str) == symbol]
    if time_prefix and "时间" in filtered.columns:
        filtered = filtered[filtered["时间"].astype(str).str.startswith(time_prefix)]
    return filtered.reset_index(drop=True)


def _spotlight_option_label(spotlight: DashboardCandidateSpotlight) -> str:
    action_status = _action_status_label(
        spotlight.action_label,
        spotlight.status_label,
    )
    return _join_display_parts(
        spotlight.display_name,
        "同日联动" if spotlight.task_labels else "",
        action_status,
        separator=" · ",
    )


def _symbol_option_label(
    *,
    symbol: str,
    cards: tuple[DashboardCandidateCard, ...],
    spotlights: tuple[DashboardCandidateSpotlight, ...] = (),
    debates: tuple[DashboardDebateSummary, ...] = (),
) -> str:
    card = next((item for item in cards if item.symbol == symbol), None)
    if card is not None:
        return _candidate_option_label(card)
    spotlight = next((item for item in spotlights if item.symbol == symbol), None)
    if spotlight is not None:
        return _spotlight_option_label(spotlight)
    debate = next((item for item in debates if item.symbol == symbol), None)
    if debate is not None:
        return _join_display_parts(
            debate.display_name,
            "辩论主结论",
            debate.recommended_adjustment_label,
            separator=" · ",
        )
    return symbol


def _focus_summary_lines(
    *,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    execution_focus,
) -> tuple[str, ...]:
    if selected_card is not None:
        return tuple(
            line
            for line in (
                f"当前结论: {_action_status_label(selected_card.action_label, selected_card.status_label)}",
                _candidate_score_context_line(selected_card),
                _focus_cross_market_digest_line(
                    selected_spotlight=selected_spotlight,
                    focus_display=selected_card.display_name,
                )
                or _focus_candidate_summary_line(selected_card),
                (
                    f"下一步: {_card_next_action(selected_card)}"
                    if _card_next_action(selected_card)
                    else ""
                ),
                _review_meta_line("再看时间", selected_card.review_meta),
                (
                    f"当前限制: {_safe_current_research_line(_card_primary_blocker(selected_card))}"
                    if _card_primary_blocker(selected_card)
                    else ""
                ),
            )
            if line
        )
    if selected_spotlight is not None:
        return tuple(
            line
            for line in (
                _focus_cross_market_digest_line(
                    selected_spotlight=selected_spotlight,
                    focus_display=selected_spotlight.display_name,
                ),
                _task_scope_line(_task_scope_summary(selected_spotlight.task_labels)),
                f"当前结论: {_action_status_label(selected_spotlight.action_label, selected_spotlight.status_label)}",
                (
                    f"当前重点: {_safe_current_research_line(selected_spotlight.blocker or selected_spotlight.next_step)}"
                    if selected_spotlight.blocker or selected_spotlight.next_step
                    else ""
                ),
                _review_meta_line("统一复核", selected_spotlight.review_meta),
            )
            if line
        )
    return tuple(line for line in execution_focus.research_lines[:3] if line)


def _focus_cross_market_digest_line(
    *,
    selected_spotlight: DashboardCandidateSpotlight | None = None,
    debate_summary: DashboardDebateSummary | None = None,
    focus_display: str = "",
) -> str:
    if selected_spotlight is not None:
        theme = (
            selected_spotlight.cross_market_summary.strip()
            or _cross_market_chain_lead_summary(
                selected_spotlight.cross_market_chain_summary.strip()
            )
        )
        validation = selected_spotlight.cross_market_validation_summary.strip() or (
            _extract_cross_market_chain_marker(
                selected_spotlight.cross_market_chain_summary.strip(),
                "确认",
            )
        )
        invalidation = (
            selected_spotlight.cross_market_invalidation_summary.strip()
            or _extract_cross_market_chain_marker(
                selected_spotlight.cross_market_chain_summary.strip(),
                "失效",
            )
        )
        if theme or validation or invalidation:
            parts = []
            if theme:
                parts.append(f"跨市主线: {theme}")
            if focus_display.strip():
                parts.append(f"先看 {focus_display.strip()}")
            if validation:
                parts.append(f"确认 {validation}")
            if invalidation:
                parts.append(f"失效 {invalidation}")
            return " | ".join(parts)

    if debate_summary is not None:
        conclusion = _debate_conclusion_summary(debate_summary)
        theme = conclusion.cross_market_line.replace(
            "跨市传导: ", ""
        ).strip() or _cross_market_chain_lead_summary(
            debate_summary.cross_market_chain_summary.strip()
        )
        validation = conclusion.validation_line.replace(
            "确认信号: ", ""
        ).strip() or _extract_cross_market_chain_marker(
            debate_summary.cross_market_chain_summary.strip(),
            "确认",
        )
        invalidation = conclusion.invalidation_line.replace(
            "失效信号: ", ""
        ).strip() or _extract_cross_market_chain_marker(
            debate_summary.cross_market_chain_summary.strip(),
            "失效",
        )
        display = focus_display.strip() or debate_summary.display_name
        if theme or validation or invalidation:
            parts = []
            if theme:
                parts.append(f"跨市主线: {theme}")
            if display:
                parts.append(f"先看 {display}")
            if validation:
                parts.append(f"确认 {validation}")
            if invalidation:
                parts.append(f"失效 {invalidation}")
            return " | ".join(parts)

    return ""


def _cross_market_chain_lead_summary(chain_summary: str) -> str:
    normalized = chain_summary.strip()
    if not normalized:
        return ""
    parts = tuple(
        segment.strip()
        for segment in normalized.split("｜")
        if segment.strip()
        and not segment.strip().startswith(("确认 ", "失效 ", "同向 ", "反向 "))
    )
    return "｜".join(parts[:3])


def _extract_cross_market_chain_marker(chain_summary: str, marker: str) -> str:
    normalized = chain_summary.strip()
    if not normalized:
        return ""
    prefix = f"{marker} "
    for segment in normalized.split("｜"):
        clean = segment.strip()
        if clean.startswith(prefix):
            return clean[len(prefix) :].strip()
    return ""


def _focus_candidate_summary_line(card: DashboardCandidateCard) -> str:
    note = _safe_current_research_line(card.decision_note)
    if not note:
        return ""
    if note == _card_primary_blocker(card):
        return ""
    if note == _card_next_action(card):
        return ""
    if not any(
        marker in note
        for marker in ("跨市线索", "证据堆栈", "倾向优先纸面复核", "优先纸面复核")
    ):
        return ""
    return f"候选摘要: {note}"


def _workspace_focus_title(
    *,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    review_card: DashboardCandidateCard | None,
    execution_focus,
) -> str:
    if review_card is not None:
        return review_card.display_name
    if selected_spotlight is not None:
        return selected_spotlight.display_name
    if selected_card is not None:
        return selected_card.display_name
    return execution_focus.display_name


def _workspace_focus_lines(
    *,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    review_card: DashboardCandidateCard | None,
    execution_focus,
) -> tuple[str, ...]:
    if selected_card is not None or selected_spotlight is not None:
        return _focus_summary_lines(
            selected_card=selected_card,
            selected_spotlight=selected_spotlight,
            execution_focus=execution_focus,
        )
    if review_card is not None:
        is_debate_review = review_card.rank_label == "辩论主结论"
        focus_action_line = (
            "复核状态: 待独立验证 / 等待下一次任务确认"
            if is_debate_review
            else f"复核状态: {_action_status_label(review_card.action_label, review_card.status_label)}"
        )
        follow_up_line = (
            "验证动作: 等待下一次任务或纸面验证记录补充独立依据。"
            if is_debate_review
            else (
                f"下一步: {_safe_current_research_line(review_card.next_step or review_card.decision_note)}"
                if review_card.next_step or review_card.decision_note
                else ""
            )
        )
        focus_lines = [
            focus_action_line,
            _candidate_score_context_line(review_card),
            follow_up_line,
        ]
        if not is_debate_review:
            focus_lines.extend(
                [
                    _review_meta_line("再看时间", review_card.review_meta),
                    (
                        f"当前限制: {_safe_current_research_line(_card_primary_blocker(review_card))}"
                        if _card_primary_blocker(review_card)
                        else ""
                    ),
                ]
            )
        return tuple(line for line in focus_lines if line)
    return tuple(line for line in execution_focus.research_lines[:3] if line)


def _workspace_research_status(
    *,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    review_card: DashboardCandidateCard | None,
    execution_focus,
) -> str:
    if selected_card is not None:
        return execution_focus.research_status
    if selected_spotlight is not None and review_card is not None:
        return "同日联动已补齐"
    if review_card is not None:
        return f"{_committee_supplement_label()}已补齐"
    return execution_focus.research_status


def _workspace_reality_lines(
    *,
    selected_date: str,
    research_status: str,
    event_count: int,
    log_count: int,
    open_position_count: int,
    archive_status: str = "",
) -> tuple[str, ...]:
    return tuple(
        line
        for line in (
            f"回看日期: {selected_date or '-'}",
            f"当前阶段: {research_status}",
            f"纸面记录: 事件 {event_count} / 日志 {log_count} / 纸面持有 {open_position_count}",
            f"归档状态: {archive_status}" if archive_status else "",
        )
        if line
    )


def _holding_metric_label(execution_focus) -> str:
    if execution_focus.holding_status in {
        "当前持仓未绑定本日",
        "纸面持有未绑定本日",
    }:
        return "本日绑定纸面持有"
    return "纸面持有记录"


def _workspace_reality_tone(
    *,
    execution_status: str,
    event_count: int,
    log_count: int,
    open_position_count: int,
) -> str:
    if "阻" in execution_status:
        return "blocked"
    if event_count or log_count or open_position_count:
        return "pressure"
    return "archive"


def _review_source_label(review_card: DashboardCandidateCard | None) -> str:
    if review_card is None:
        return "纸面记录"
    if review_card.rank_label == "辩论主结论":
        return _committee_supplement_label()
    if review_card.rank_label == "同日联动":
        return "同日联动"
    return "研究候选卡"


def _workspace_context_brief(
    *,
    review_card: DashboardCandidateCard | None,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    open_position_count: int,
    has_execution_activity: bool,
    holding_status: str = "",
) -> tuple[str, tuple[str, ...], str]:
    if review_card is None:
        return (
            "纸面记录",
            ("当前没有已有结论可参考。", "先看纸面持有假设、纸面事件和日志。"),
            "archive",
        )
    if open_position_count > 0:
        return (
            "纸面持有优先",
            ("当前仍有纸面持有假设。", "先看退出条件与约束。"),
            "pressure",
        )
    if holding_status == "纸面持有未绑定本日":
        return (
            "纸面持有优先",
            (
                "纸面 ledger 仍有未绑定本日的持有假设。",
                "先确认旧持有假设退出条件，再判断本日信号是否独立推进。",
            ),
            "pressure",
        )
    if has_execution_activity:
        return (
            "纸面验证优先",
            ("已经进入纸面入场、阻塞或关闭记录。", "先顺着纸面事件与日志回看。"),
            "pressure",
        )
    if (
        review_card is not None
        and _card_primary_blocker(review_card)
        and selected_card is not None
    ):
        return (
            "阻塞卡点回看",
            ("当前仍受研究阻塞影响。", "先核对卡点、复核条件和复核窗口。"),
            "blocked",
        )
    if selected_card is None and selected_spotlight is None:
        return (
            f"{_committee_supplement_label()}回看",
            (
                f"当前判断主要由{_committee_supplement_label()}补齐。",
                "先看委员会结论、修正原因和风险分歧。",
            ),
            "focus",
        )
    if selected_card is None:
        cross_market_line = (
            f"跨市传导: {selected_spotlight.cross_market_summary}"
            if selected_spotlight is not None
            and selected_spotlight.cross_market_summary
            else ""
        )
        return (
            "跨任务联动回看",
            _unique_lines(
                (
                    "当前判断主要来自同日一起出现的信息。",
                    cross_market_line,
                    "先核对跨任务结论，再回到单任务原始记录。",
                )
            ),
            "archive",
        )
    return (
        "研究结论回看",
        ("当前以研究候选卡为主。", "先看来龙去脉、复核节奏和入场条件。"),
        "archive",
    )


def _research_path_review_step(
    *,
    review_card: DashboardCandidateCard | None,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
) -> _ResearchPathStep:
    if review_card is None:
        return _ResearchPathStep(
            icon="🧭",
            title="研究结论",
            headline="暂无候选结论",
            lines=("先看纸面记录和同日任务摘要。",),
            tone="archive",
        )
    if debate_summary is not None and review_card.rank_label == "辩论主结论":
        return _ResearchPathStep(
            icon="🧠",
            title="研究结论",
            headline=f"{debate_summary.recommended_adjustment_label} / 分歧 {debate_summary.disagreement_score:.2f}",
            lines=_unique_lines(
                (
                    f"共识: {debate_summary.consensus}"
                    if debate_summary.consensus
                    else ""
                ),
                (
                    f"风险: {'；'.join(debate_summary.risk_warnings[:2])}"
                    if debate_summary.risk_warnings
                    else ""
                ),
                ("仅作辩论补齐，不替代确定性评分。",),
            )[:2],
            tone="pressure" if debate_summary.disagreement_score >= 0.35 else "archive",
        )
    source = _review_source_label(review_card)
    source_hint = (
        f"同日联动: {'、'.join(selected_spotlight.task_labels)}"
        if selected_spotlight is not None and selected_spotlight.task_labels
        else source
    )
    spotlight_summary_line = (
        f"候选摘要: {_spotlight_decision_note(selected_spotlight)}"
        if selected_spotlight is not None
        and _spotlight_has_structured_summary(selected_spotlight)
        else _focus_candidate_summary_line(review_card)
    )
    return _ResearchPathStep(
        icon="🧭",
        title="研究结论",
        headline=_join_display_parts(
            review_card.display_name,
            _action_status_label(review_card.action_label, review_card.status_label),
            separator=" · ",
        ),
        lines=_unique_lines(
            (_candidate_score_context_line(review_card),),
            (spotlight_summary_line,),
            (source_hint,),
            (
                f"卡点: {_card_primary_blocker(review_card)}"
                if _card_primary_blocker(review_card)
                else ""
            ),
        )[:3],
        tone="blocked" if _card_primary_blocker(review_card) else "focus",
    )


def _research_path_paper_step(
    *,
    event_count: int,
    log_count: int,
    open_position_count: int,
    execution_focus,
) -> _ResearchPathStep:
    has_paper = bool(event_count or log_count or open_position_count)
    headline = (
        f"事件 {event_count} / 日志 {log_count} / 纸面持有 {open_position_count}"
        if has_paper
        else "尚未进入纸面验证链"
    )
    lines = _unique_lines(
        tuple(line for line in execution_focus.execution_lines[:1] if line),
        tuple(line for line in execution_focus.holding_lines[:1] if line),
        tuple(line for line in execution_focus.readiness_lines[:1] if line),
    )
    if not lines:
        lines = ("先按研究证据复核，不补生成纸面对象。",)
    return _ResearchPathStep(
        icon="🧪",
        title="纸面记录",
        headline=headline,
        lines=lines[:2],
        tone=_workspace_reality_tone(
            execution_status=execution_focus.execution_status,
            event_count=event_count,
            log_count=log_count,
            open_position_count=open_position_count,
        ),
    )


def _sanitize_research_path_line(line: str) -> str:
    clean = sanitize_archive_text(re.sub(r"\*\*(.*?)\*\*", r"\1", line).strip())
    clean = clean.replace("无可执行标的", "当时未形成复核对象")
    clean = clean.replace("可执行标的", "历史复核对象")
    clean = clean.replace("可执行主链", "历史主链复核")
    clean = clean.replace("可执行", "历史复核")
    clean = clean.replace("重点跟踪对象", "历史复核对象")
    clean = clean.replace("重点跟踪名单", "历史复核名单")
    clean = clean.replace("跟踪优先级", "历史复核顺位")
    return clean


def _research_path_archive_step(
    *,
    task_view,
    review_card: DashboardCandidateCard | None,
    selected_symbol: str,
    archive_status: str = "",
) -> _ResearchPathStep:
    status = archive_status or _report_archive_status(task_view)
    symbol_lines, global_lines = _partition_symbol_lines(
        _unique_lines(
            task_view.report_summary_lines[:2],
            task_view.next_day_focus_lines[:2],
            task_view.runtime_lines[:2],
        ),
        selected_symbol,
    )
    fallback_line = (
        f"再看时间: {review_card.review_meta}"
        if review_card is not None and _has_review_meta(review_card.review_meta)
        else ""
    )
    lines = _unique_lines(
        tuple(_sanitize_research_path_line(line) for line in symbol_lines[:2]),
        tuple(_sanitize_research_path_line(line) for line in global_lines[:1]),
        (fallback_line,),
    )
    if not lines:
        lines = ("归档尚未命中该标的，先保留研究结论和纸面记录。",)
    return _ResearchPathStep(
        icon="🗂",
        title="归档结果",
        headline=status,
        lines=lines[:2],
        tone="archive",
    )


def _research_path_steps(
    *,
    task_view,
    selected_symbol: str,
    review_card: DashboardCandidateCard | None,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
    execution_focus,
    event_count: int,
    log_count: int,
    open_position_count: int,
    archive_status: str = "",
) -> tuple[_ResearchPathStep, ...]:
    return (
        _research_path_review_step(
            review_card=review_card,
            selected_card=selected_card,
            selected_spotlight=selected_spotlight,
            debate_summary=debate_summary,
        ),
        _research_path_paper_step(
            event_count=event_count,
            log_count=log_count,
            open_position_count=open_position_count,
            execution_focus=execution_focus,
        ),
        _research_path_archive_step(
            task_view=task_view,
            review_card=review_card,
            selected_symbol=selected_symbol,
            archive_status=archive_status,
        ),
    )


def _render_research_path(steps: tuple[_ResearchPathStep, ...]) -> None:
    st.markdown(
        "\n".join(
            [
                '<div class="aqsp-research-path">',
                *[
                    (
                        f'<div class="aqsp-path-step {escape(step.tone)}">'
                        f'<div class="aqsp-path-kicker"><span>{escape(step.icon)}</span><span>{escape(step.title)}</span></div>'
                        f'<div class="aqsp-path-headline">{escape(step.headline)}</div>'
                        + "".join(
                            f'<div class="aqsp-path-line">{escape(line)}</div>'
                            for line in step.lines[:2]
                        )
                        + "</div>"
                    )
                    for step in steps
                ],
                "</div>",
            ]
        ),
        unsafe_allow_html=True,
    )


def _workspace_quick_symbol_label(
    *,
    symbol: str,
    cards: tuple[DashboardCandidateCard, ...],
    spotlights: tuple[DashboardCandidateSpotlight, ...] = (),
    debates: tuple[DashboardDebateSummary, ...] = (),
) -> tuple[str, str]:
    card = next((item for item in cards if item.symbol == symbol), None)
    if card is not None:
        return (card.symbol, card.name or card.display_name)
    spotlight = next((item for item in spotlights if item.symbol == symbol), None)
    if spotlight is not None:
        _, _, remainder = spotlight.display_name.partition(" ")
        return (spotlight.symbol, remainder or spotlight.display_name)
    debate = next((item for item in debates if item.symbol == symbol), None)
    if debate is not None:
        _, _, remainder = debate.display_name.partition(" ")
        return (debate.symbol, remainder or debate.display_name)
    return (symbol, symbol)


def _quick_bar_symbols(
    *,
    workspace: str,
    symbol_order: list[str],
    selected_symbol: str,
    debates: tuple[DashboardDebateSummary, ...] = (),
    limit: int = 6,
) -> list[str]:
    quick_symbols = symbol_order[:limit]
    if workspace == "候选复盘" and debates and quick_symbols:
        debate_focus = _salient_home_debates(debates)[0]
        if (
            debate_focus.symbol in symbol_order
            and debate_focus.symbol not in quick_symbols
        ):
            quick_symbols = [*quick_symbols[:-1], debate_focus.symbol]
    if (
        selected_symbol
        and selected_symbol in symbol_order
        and selected_symbol not in quick_symbols
        and quick_symbols
    ):
        quick_symbols = [*quick_symbols[:-1], selected_symbol]
    return quick_symbols


def _render_symbol_quick_bar(
    *,
    title: str,
    workspace: str,
    symbol_order: list[str],
    selected_symbol: str,
    cards: tuple[DashboardCandidateCard, ...],
    spotlights: tuple[DashboardCandidateSpotlight, ...] = (),
    debates: tuple[DashboardDebateSummary, ...] = (),
    signal_date: str = "",
    task_id: str = "",
    task_label: str = "",
    limit: int = 6,
) -> None:
    quick_symbols = _quick_bar_symbols(
        workspace=workspace,
        symbol_order=symbol_order,
        selected_symbol=selected_symbol,
        debates=debates,
        limit=limit,
    )
    if len(quick_symbols) <= 1:
        return
    if title:
        st.caption(title)
    columns = st.columns(len(quick_symbols))
    for column, symbol in zip(columns, quick_symbols):
        with column:
            is_active = symbol == selected_symbol
            code_label, name_label = _workspace_quick_symbol_label(
                symbol=symbol,
                cards=cards,
                spotlights=spotlights,
                debates=debates,
            )
            if _stretch_button(
                code_label,
                key=f"{workspace}-quick-symbol-{symbol}",
                type="primary" if is_active else "secondary",
            ):
                _queue_workspace_symbol_handoff(
                    workspace=workspace,
                    symbol=symbol,
                    cards=cards,
                    spotlights=spotlights,
                    debates=debates,
                    signal_date=signal_date,
                    task_id=task_id,
                    task_label=task_label,
                )
                st.rerun()
            st.markdown(
                f'<div class="aqsp-quick-symbol-name{" active" if is_active else ""}">{escape(name_label)}</div>',
                unsafe_allow_html=True,
            )


def _render_candidate_evidence_drawers(
    *,
    review_card: DashboardCandidateCard,
    spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
    journey_steps: tuple[DashboardCandidateJourneyStep, ...],
    signal_frame,
    task_frame,
    paper_frame,
    execution_frame,
    evidence_title: str,
) -> None:
    has_journey = _should_render_candidate_journey(
        spotlight=spotlight,
        debate_summary=debate_summary,
        journey_steps=journey_steps,
    ) and bool(journey_steps)
    if has_journey:
        with st.expander("当日怎么走到这里", expanded=False):
            _render_candidate_journey(
                journey_steps,
                review_card=review_card,
                spotlight=spotlight,
                debate_summary=debate_summary,
            )
    if debate_summary is not None:
        with st.expander("多 Agent 摘要与证据", expanded=False):
            _render_line_block(
                "委员会摘要",
                _candidate_debate_evidence_lines(debate_summary),
                "当前没有可回看的委员会摘要。",
            )
            _render_line_block(
                "过程细节",
                _candidate_debate_detail_lines(debate_summary),
                "当前没有需要展开的讨论过程。",
            )
    with st.expander("原始记录", expanded=False):
        _render_candidate_research_stream(
            review_card=review_card,
            spotlight=spotlight,
            debate_summary=debate_summary,
            signal_frame=signal_frame,
            task_frame=task_frame,
            paper_frame=paper_frame,
            execution_frame=execution_frame,
            evidence_title=evidence_title,
        )


def _resolve_workspace_symbol(
    *,
    symbol_order: list[str],
    pending_symbol: str | None,
    current_value: str | None,
) -> str:
    if not symbol_order:
        return ""
    if pending_symbol:
        return pending_symbol
    default_symbol = (
        pending_symbol if pending_symbol in symbol_order else symbol_order[0]
    )
    if current_value not in symbol_order or pending_symbol is not None:
        return default_symbol
    return str(current_value)


def _include_pending_symbol(
    symbol_order: list[str],
    pending_symbol: str | None,
) -> list[str]:
    selected_symbol = (pending_symbol or "").strip()
    if selected_symbol and selected_symbol not in symbol_order:
        return [selected_symbol, *symbol_order]
    return symbol_order


def _sync_workspace_symbol_state(
    *,
    select_key: str,
    resolved_symbol: str,
) -> None:
    if resolved_symbol:
        st.session_state[select_key] = resolved_symbol


def _render_workspace_symbol_selector(
    *,
    label: str,
    workspace: str,
    symbol_order: list[str],
    select_key: str,
    pending_symbol: str | None,
    cards: tuple[DashboardCandidateCard, ...],
    spotlights: tuple[DashboardCandidateSpotlight, ...] = (),
    debates: tuple[DashboardDebateSummary, ...] = (),
    signal_date: str = "",
    task_id: str = "",
    task_label: str = "",
) -> str:
    symbol_order = _include_pending_symbol(symbol_order, pending_symbol)
    default_symbol = _resolve_workspace_symbol(
        symbol_order=symbol_order,
        pending_symbol=pending_symbol,
        current_value=st.session_state.get(select_key),
    )
    _sync_workspace_symbol_state(select_key=select_key, resolved_symbol=default_symbol)
    selected_symbol = st.selectbox(
        label,
        symbol_order,
        index=symbol_order.index(default_symbol),
        format_func=lambda symbol: _symbol_option_label(
            symbol=symbol,
            cards=cards,
            spotlights=spotlights,
            debates=debates,
        ),
        key=select_key,
    )
    _render_symbol_quick_bar(
        title="",
        workspace=workspace,
        symbol_order=symbol_order,
        selected_symbol=selected_symbol,
        cards=cards,
        spotlights=spotlights,
        debates=debates,
        signal_date=signal_date,
        task_id=task_id,
        task_label=task_label,
    )
    return selected_symbol


def _candidate_has_expanded_path(
    *,
    spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
    journey_steps: tuple[DashboardCandidateJourneyStep, ...],
) -> bool:
    if debate_summary is not None:
        return True
    if spotlight is not None and len(spotlight.task_labels) > 1:
        return True
    return len(journey_steps) > 1


def _should_render_candidate_journey(
    *,
    spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
    journey_steps: tuple[DashboardCandidateJourneyStep, ...],
) -> bool:
    if not journey_steps:
        return True
    return _candidate_has_expanded_path(
        spotlight=spotlight,
        debate_summary=debate_summary,
        journey_steps=journey_steps,
    )


def _review_phase_switch_rows(
    *,
    same_day_rows: tuple[DashboardSameDayTaskRow, ...],
    current_task_id: str,
    journey_steps: tuple[DashboardCandidateJourneyStep, ...],
    research_task_id: str = "",
) -> tuple[DashboardSameDayTaskRow, ...]:
    relevant_task_ids = {step.task_id for step in journey_steps}
    if current_task_id:
        relevant_task_ids.add(current_task_id)
    if research_task_id:
        relevant_task_ids.add(research_task_id)
    return tuple(row for row in same_day_rows if row.task_id in relevant_task_ids)


def _render_review_phase_bar(
    *,
    signal_date: str,
    current_task_id: str,
    selected_symbol: str,
    same_day_rows: tuple[DashboardSameDayTaskRow, ...],
    journey_steps: tuple[DashboardCandidateJourneyStep, ...],
    research_task_id: str = "",
    selected_card: DashboardCandidateCard | None = None,
    selected_spotlight: DashboardCandidateSpotlight | None = None,
    debate_summary: DashboardDebateSummary | None = None,
) -> None:
    phase_rows = _review_phase_switch_rows(
        same_day_rows=same_day_rows,
        current_task_id=current_task_id,
        journey_steps=journey_steps,
        research_task_id=research_task_id,
    )
    if len(phase_rows) <= 1:
        return
    st.markdown(
        '<div class="aqsp-nav-section-title">阶段</div>', unsafe_allow_html=True
    )
    columns = st.columns(len(phase_rows))
    for column, row in zip(columns, phase_rows):
        with column:
            is_active = row.task_id == current_task_id
            if _stretch_button(
                row.phase_label,
                key=f"review-phase-{selected_symbol}-{row.task_id}-{signal_date}",
                type="primary" if is_active else "secondary",
            ):
                source_key = _candidate_effective_decision_source_key(
                    selected_card=selected_card,
                    spotlight=selected_spotlight,
                    debate_summary=debate_summary,
                )
                _queue_workspace_handoff(
                    target_workspace="候选复盘",
                    source_workspace="候选复盘",
                    symbol=selected_symbol,
                    signal_date=signal_date,
                    task_id=row.task_id,
                    task_label=row.task_label,
                    focus_kind=source_key,
                    debate_id=debate_summary.debate_id
                    if debate_summary is not None
                    else "",
                    decision_source=source_key,
                    title=f"切到 {row.phase_label} 看这段结论",
                    lines=tuple(
                        line
                        for line in (
                            f"当前标的: {selected_symbol}",
                            _candidate_effective_decision_line(
                                selected_card=selected_card,
                                spotlight=selected_spotlight,
                                debate_summary=debate_summary,
                            ),
                            f"切到这段先看: {row.headline or row.phase_summary or row.task_label}",
                        )
                        if line
                    ),
                )
                _set_dashboard_selection(
                    task_id=row.task_id,
                    signal_date=signal_date,
                )
                st.rerun()
            # 候选阶段按钮已显示phase_label，删除冗余的two_line_label


def _execution_research_context_lines(
    *,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
    execution_focus,
) -> tuple[str, ...]:
    focus_lines = _focus_summary_lines(
        selected_card=selected_card,
        selected_spotlight=selected_spotlight,
        execution_focus=execution_focus,
    )
    if (
        debate_summary is not None
        and selected_card is None
        and selected_spotlight is None
    ):
        return _unique_lines(
            (
                _focus_cross_market_digest_line(
                    debate_summary=debate_summary,
                    focus_display=debate_summary.display_name,
                ),
            ),
            _debate_primary_takeaways(debate_summary)[:2],
            ("当前没有研究候选卡，当前判断主要由同日多方讨论补齐。",),
        ) or ("当前没有结构化研究结论。",)
    research_seed = _prioritized_research_lines(execution_focus.research_lines)
    if not research_seed:
        research_seed = _focus_summary_lines(
            selected_card=selected_card,
            selected_spotlight=selected_spotlight,
            execution_focus=execution_focus,
        )[:2]
    return (
        _unique_lines(
            (
                _focus_cross_market_digest_line(
                    selected_spotlight=selected_spotlight,
                    debate_summary=debate_summary,
                    focus_display=(
                        selected_card.display_name
                        if selected_card is not None
                        else (
                            selected_spotlight.display_name
                            if selected_spotlight is not None
                            else ""
                        )
                    ),
                ),
            ),
            _debate_primary_takeaways(debate_summary)[:2],
            research_seed,
        )
        or execution_focus.research_lines
        or focus_lines
        or ("当前没有结构化研究结论。",)
    )


def _execution_path_context_lines(
    *,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
    execution_focus,
) -> tuple[str, ...]:
    review_card = _review_fallback_card(
        selected_card=selected_card,
        selected_spotlight=selected_spotlight,
        debate_summary=debate_summary,
    )
    readiness_lines = _normalized_readiness_lines(
        review_card=review_card,
        readiness_lines=execution_focus.readiness_lines,
    )
    blocker = _card_primary_blocker(review_card) if review_card is not None else ""
    include_blocker = bool(
        blocker and not any(blocker in line for line in readiness_lines)
    )
    return _unique_lines(
        (
            _focus_cross_market_digest_line(
                selected_spotlight=selected_spotlight,
                debate_summary=debate_summary,
                focus_display=(
                    review_card.display_name
                    if review_card is not None
                    else (
                        selected_spotlight.display_name
                        if selected_spotlight is not None
                        else ""
                    )
                ),
            ),
        ),
        _debate_primary_takeaways(debate_summary)[2:4],
        tuple(
            line
            for line in (
                (
                    f"下一步: {_card_next_action(review_card)}"
                    if review_card is not None
                    else ""
                ),
                (
                    _review_meta_line("再看时间", review_card.review_meta)
                    if review_card is not None
                    else ""
                ),
                (f"当前限制: {blocker}" if include_blocker else ""),
            )
            if line
        ),
        readiness_lines,
    )


def _render_workspace_focus_header(
    *,
    title: str,
    selected_date: str,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    review_card: DashboardCandidateCard | None,
    execution_focus,
    event_count: int,
    log_count: int,
    open_position_count: int,
    same_day_task_count: int = 0,
    archive_status: str = "",
    show_archive_metric: bool = False,
) -> None:
    focus_title = _workspace_focus_title(
        selected_card=selected_card,
        selected_spotlight=selected_spotlight,
        review_card=review_card,
        execution_focus=execution_focus,
    )
    focus_lines = _workspace_focus_lines(
        selected_card=selected_card,
        selected_spotlight=selected_spotlight,
        review_card=review_card,
        execution_focus=execution_focus,
    )
    research_status = _workspace_research_status(
        selected_card=selected_card,
        selected_spotlight=selected_spotlight,
        review_card=review_card,
        execution_focus=execution_focus,
    )
    reality_lines = _workspace_reality_lines(
        selected_date=selected_date,
        research_status=research_status,
        event_count=event_count,
        log_count=log_count,
        open_position_count=open_position_count,
        archive_status=archive_status if show_archive_metric else "",
    )

    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric("虚拟盘事件", event_count)
    metric_col2.metric("纸面日志", log_count)
    metric_col3.metric(_holding_metric_label(execution_focus), open_position_count)
    if show_archive_metric:
        metric_col4.metric("归档状态", archive_status or "-")
    else:
        metric_col4.metric("同日任务", same_day_task_count)

    focus_col, reality_col = st.columns(2)
    with focus_col:
        _render_cockpit_card(
            kicker=title,
            title=focus_title,
            lines=focus_lines
            or ("当前还没有结构化候选结论，先按纸面记录和已有判断回看。",),
            tone="focus" if review_card is not None else "archive",
        )
    with reality_col:
        _render_cockpit_card(
            kicker="纸面记录",
            title=f"{execution_focus.execution_status} / {execution_focus.holding_status}",
            lines=reality_lines,
            tone=_workspace_reality_tone(
                execution_status=execution_focus.execution_status,
                event_count=event_count,
                log_count=log_count,
                open_position_count=open_position_count,
            ),
        )


def _resolve_task_for_date(
    *,
    provider: DashboardDataProvider,
    current_task_id: str,
    signal_date: str,
) -> str:
    return _resolve_task_for_date_with_reason(
        provider=provider,
        current_task_id=current_task_id,
        signal_date=signal_date,
    ).task_id


def _resolve_task_for_date_with_reason(
    *,
    provider: DashboardDataProvider,
    current_task_id: str,
    signal_date: str,
) -> _TaskDateResolution:
    same_day_rows = provider.same_day_task_rows(signal_date)
    if any(row.task_id == current_task_id for row in same_day_rows):
        return _TaskDateResolution(task_id=current_task_id)
    preferred_task_id = provider.preferred_task_for_date(signal_date)
    current_label = _task_label_for_date(
        provider=provider,
        task_id=current_task_id,
        signal_date=signal_date,
        rows=same_day_rows,
    )
    preferred_label = _task_label_for_date(
        provider=provider,
        task_id=preferred_task_id,
        signal_date=signal_date,
        rows=same_day_rows,
    )
    return _TaskDateResolution(
        task_id=preferred_task_id,
        reason=f"该日无 {current_label}，已到 {preferred_label}",
    )


def _task_label_for_date(
    *,
    provider: DashboardDataProvider,
    task_id: str,
    signal_date: str,
    rows: tuple[DashboardSameDayTaskRow, ...],
) -> str:
    row = next((item for item in rows if item.task_id == task_id), None)
    if row is not None:
        return getattr(row, "task_label", task_id)
    task_snapshots = getattr(provider, "task_snapshots", None)
    snapshots = task_snapshots(signal_date) if callable(task_snapshots) else ()
    snapshot = next((item for item in snapshots if item.task_id == task_id), None)
    if snapshot is not None:
        return snapshot.task_label
    return task_id


def _date_jump_secondary_label(
    provider: DashboardDataProvider,
    current_task_id: str,
    signal_date: str,
    *,
    resolution: _TaskDateResolution | None = None,
) -> str:
    target_task_id = (
        resolution.task_id
        if resolution is not None
        else _resolve_task_for_date(
            provider=provider,
            current_task_id=current_task_id,
            signal_date=signal_date,
        )
    )
    same_day_rows = provider.same_day_task_rows(signal_date)
    row = next((item for item in same_day_rows if item.task_id == target_task_id), None)
    if row is not None:
        return row.task_label
    snapshot = next(
        (
            item
            for item in provider.task_snapshots(signal_date)
            if item.task_id == target_task_id
        ),
        None,
    )
    if snapshot is not None:
        return snapshot.task_label
    return signal_date


def _prioritized_research_lines(lines: tuple[str, ...]) -> tuple[str, ...]:
    prioritized: list[str] = []
    for prefix in (
        "研究动作:",
        "研究下一步:",
        "当前限制:",
        "当前卡点:",
        "再看时间:",
        "复核节奏:",
        "跨市逻辑:",
        "确认信号:",
        "失效信号:",
        "支持观点:",
        "反对观点:",
        "待确认:",
        "角色可信度:",
    ):
        for line in lines:
            if line.startswith(prefix) and line not in prioritized:
                prioritized.append(line)
                break
    if not prioritized and lines:
        prioritized.extend(line for line in lines[:2] if line)
    return tuple(prioritized)


def _render_candidate_focus_summary(
    card: DashboardCandidateCard,
    spotlight: DashboardCandidateSpotlight | None,
) -> None:
    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric("当前评分", f"{card.score:.1f}")
    metric_col2.metric("队列层级", card.rank_label)
    metric_col3.metric("组合动作", card.action_label)
    metric_col4.metric("候选状态", card.status_label)

    thesis_col, action_col = st.columns(2)
    with thesis_col:
        st.markdown(f"### {card.display_name}")
        st.markdown(
            "\n".join(
                line
                for line in [
                    f"- 复核状态: {_action_status_label(card.action_label, card.status_label)}",
                    (
                        f"- {_review_meta_line('复核节奏', card.review_meta)}"
                        if _review_meta_line("复核节奏", card.review_meta)
                        else ""
                    ),
                    f"- 命中策略: {'、'.join(card.strategies) if card.strategies else '-'}",
                    f"- 数据源: {card.data_source or '-'}",
                ]
                if line
            )
        )
        _render_line_block("核心理由", card.reasons, "当前未记录结构化理由。")
        _render_line_block("风险提示", card.risks, "当前未记录结构化风险。")

    with action_col:
        if spotlight is None:
            st.info("该标的当前只在本任务中出现，没有额外同日参考信息。")
        else:
            st.markdown(
                "\n".join(
                    [
                        "#### 同日联动",
                        *[
                            f"- {line}"
                            for line in _candidate_focus_spotlight_lines(
                                card, spotlight
                            )
                        ],
                    ]
                )
            )
            if len(spotlight.task_labels) > 1:
                st.warning("该标的在多个定时任务中重复出现，适合放在一起回看。")


def _candidate_research_context_lines(
    *,
    review_card: DashboardCandidateCard,
    spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None = None,
    paper_frame,
    execution_frame,
) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
    execution_active = not paper_frame.empty or not execution_frame.empty
    execution_lines = (
        (
            f"虚拟盘事件 {len(paper_frame.index)} 条",
            f"纸面日志 {len(execution_frame.index)} 条",
            "当前已经进入纸面验证联动，可结合纸面记录核对研究结论。",
        )
        if execution_active
        else (
            "当前回看日暂无虚拟盘纸面动作。",
            "纸面侧仍为空白，暂无持仓或日志可交叉验证。",
        )
    )
    if review_card.rank_label == "辩论主结论" and debate_summary is not None:
        conclusion = _debate_conclusion_summary(
            debate_summary,
            spotlight=spotlight,
            focus_card=review_card,
        )
        context_lines = tuple(
            line
            for line in (
                conclusion.decision_line.replace(
                    "研究口径: ",
                    "委员会结论: ",
                    1,
                ).replace("当前结论: ", "委员会结论: ", 1),
                (
                    f"监控焦点: {_card_primary_blocker(review_card)}"
                    if _card_primary_blocker(review_card)
                    else "监控焦点: 当前只在本任务中出现，优先等待下一次任务验证。"
                ),
                (
                    f"下一触发: {debate_summary.next_trigger.strip()}"
                    if debate_summary.next_trigger.strip()
                    else ""
                ),
                conclusion.support_line,
                conclusion.opposition_line or conclusion.invalidation_line,
                conclusion.watch_line or conclusion.validation_line,
                "验证动作: 等待下一次任务或纸面验证记录补充独立依据。",
            )
            if line
        )
    else:
        cross_market_digest_line = _focus_cross_market_digest_line(
            selected_spotlight=spotlight,
            focus_display=review_card.display_name,
        )
        context_lines = tuple(
            line
            for line in (
                f"当前来源: {_review_source_label(review_card)}",
                f"当前结论: {_action_status_label(review_card.action_label, review_card.status_label)}",
                (
                    f"再看时间: {review_card.review_meta}"
                    if _has_review_meta(review_card.review_meta)
                    else ""
                ),
                cross_market_digest_line,
                (
                    f"核心理由: {'；'.join(review_card.reasons[:2])}"
                    if review_card.reasons
                    else ""
                ),
                (
                    f"风险提示: {'；'.join(review_card.risks[:2])}"
                    if review_card.risks
                    else ""
                ),
                (
                    f"同日联动: {'、'.join(spotlight.task_labels)}"
                    if spotlight is not None and spotlight.task_labels
                    else "同日联动: 当前只在本任务中出现"
                ),
                (
                    f"重点复核: {_safe_current_research_line(spotlight.blocker or spotlight.next_step)}"
                    if spotlight is not None
                    and (spotlight.blocker or spotlight.next_step)
                    else ""
                ),
            )
            if line
        )
    title = "纸面侧已联动" if execution_active else "当前仍处研究阶段"
    tone = (
        "pressure"
        if execution_active
        else ("blocked" if _card_primary_blocker(review_card) else "archive")
    )
    return title, execution_lines, context_lines, tone


def _candidate_debate_evidence_lines(
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    if debate_summary is None:
        return ()
    summary_title, summary_lines, _ = _candidate_discussion_snapshot_context(
        None,
        debate_summary,
    )
    return _unique_lines((f"摘要标题: {summary_title}",), summary_lines)


def _candidate_debate_detail_lines(
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    if debate_summary is None:
        return ()
    conclusion = _debate_conclusion_summary(debate_summary)
    process_line = (
        "讨论过程: "
        + _timeline_debate_process_line(debate_summary)
        .removeprefix("- ")
        .replace(f"{debate_summary.display_name}: ", "", 1)
        .strip()
    )
    round_lines = tuple(
        f"轮次摘要: 第{index + 1}轮 {line}"
        for index, line in enumerate(debate_summary.round_summaries[:2])
        if str(line).strip()
    )
    detail_lines = tuple(
        line
        for line in (
            conclusion.cross_market_line,
            conclusion.active_roles_line,
            conclusion.history_line,
            conclusion.reliability_line,
            conclusion.support_line,
            conclusion.opposition_line,
            conclusion.watch_line,
            _debate_evidence_composition_line(debate_summary),
        )
        if line
    )
    return _unique_lines(
        ((process_line,) if process_line.strip() else ()),
        round_lines,
        _debate_agent_focus_lines(debate_summary),
        detail_lines,
    )


def _render_candidate_research_stream(
    *,
    review_card: DashboardCandidateCard,
    spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
    signal_frame,
    task_frame,
    paper_frame,
    execution_frame,
    evidence_title: str,
) -> None:
    st.subheader("原始记录")
    research_col, context_col = st.columns(2)
    with research_col:
        has_task_evidence = not signal_frame.empty or not task_frame.empty
        if not signal_frame.empty:
            _render_frame(evidence_title, signal_frame)
        elif debate_summary is not None and spotlight is None:
            _render_cockpit_card(
                kicker="研究证据状态",
                title="当前没有独立任务信号表",
                lines=(
                    "当前标的主要依赖同日多 Agent 讨论补齐；原始讨论已单独放在上方抽屉。",
                    "如果后续补到任务信号或纸面记录，再回到这里交叉验证。",
                ),
                tone="archive",
            )
        if not task_frame.empty:
            _render_frame("任务明细", task_frame)
        if spotlight is not None:
            global_view_lines = _spotlight_global_view_lines(
                spotlight,
                reason_label="汇总理由",
                risk_label="汇总风险",
            )
            st.markdown(
                "\n".join(
                    [
                        "#### 同日全局视角",
                        f"- {_task_scope_line(_task_scope_summary(spotlight.task_labels))}",
                        *(
                            f"- {line}"
                            for line in (
                                global_view_lines or ("当前还没有提炼出同日联动摘要。",)
                            )
                        ),
                    ]
                )
            )
        elif not has_task_evidence and debate_summary is None:
            st.info("该标的当前只在本任务中出现，先以本任务落盘结果为主。")
    with context_col:
        context_title, execution_lines, context_lines, context_tone = (
            _candidate_research_context_lines(
                review_card=review_card,
                spotlight=spotlight,
                debate_summary=debate_summary,
                paper_frame=paper_frame,
                execution_frame=execution_frame,
            )
        )
        if paper_frame.empty and execution_frame.empty:
            _render_cockpit_card(
                kicker="纸面侧现实",
                title=context_title,
                lines=execution_lines,
                tone=context_tone,
            )
            _render_cockpit_card(
                kicker=(
                    "监控要点"
                    if debate_summary is not None and spotlight is None
                    else "一起参考"
                ),
                title=review_card.display_name,
                lines=context_lines,
                tone="focus" if spotlight is not None else "archive",
            )
        else:
            if not paper_frame.empty:
                _render_frame("当日虚拟盘事件", paper_frame)
            if not execution_frame.empty:
                _render_frame("当日纸面日志", execution_frame)
            _render_cockpit_card(
                kicker=(
                    "监控要点"
                    if debate_summary is not None and spotlight is None
                    else "一起参考"
                ),
                title=review_card.display_name,
                lines=context_lines,
                tone="focus" if spotlight is not None else "archive",
            )


def _candidate_focus_spotlight_lines(
    card: DashboardCandidateCard,
    spotlight: DashboardCandidateSpotlight,
) -> tuple[str, ...]:
    focus_detail = spotlight.blocker or spotlight.next_step or "继续跟踪"
    cross_market_digest_line = _focus_cross_market_digest_line(
        selected_spotlight=spotlight,
        focus_display=card.display_name,
    )
    duplicate_hints = {
        primary
        for primary in (
            _card_primary_blocker(card),
            card.next_step.strip(),
        )
        if primary
    }
    lines = [
        *([cross_market_digest_line] if cross_market_digest_line else []),
        _task_scope_line(_task_scope_summary(spotlight.task_labels)),
        f"跨任务结论: {_action_status_label(spotlight.action_label, spotlight.status_label)}",
    ]
    if focus_detail not in duplicate_hints:
        lines.append(f"重点复核: {_safe_current_research_line(focus_detail)}")
    if review_line := _review_meta_line("统一复核", spotlight.review_meta):
        lines.append(review_line)
    return tuple(_unique_lines(tuple(lines)))


def _candidate_effective_decision_source_key(
    *,
    selected_card: DashboardCandidateCard | None,
    spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
) -> str:
    if selected_card is not None:
        if selected_card.rank_label == "辩论主结论" and debate_summary is not None:
            return "debate"
        return "card"
    if spotlight is not None:
        return "spotlight"
    if debate_summary is not None:
        return "debate"
    return ""


def _candidate_effective_decision_source_label(source_key: str) -> str:
    return {
        "card": "研究候选卡",
        "spotlight": "同日跨任务联动",
        "debate": "委员会补充结论",
    }.get(source_key, "当前上下文")


def _candidate_effective_decision_line(
    *,
    selected_card: DashboardCandidateCard | None,
    spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
) -> str:
    source_key = _candidate_effective_decision_source_key(
        selected_card=selected_card,
        spotlight=spotlight,
        debate_summary=debate_summary,
    )
    if not source_key:
        return ""
    label = _candidate_effective_decision_source_label(source_key)
    if source_key == "card":
        if (
            selected_card is not None
            and _card_primary_blocker(selected_card)
            and debate_summary is not None
        ):
            reason = "主链卡点优先，委员会结论只补充分歧与下一触发。"
        elif spotlight is not None and debate_summary is not None:
            reason = "跨任务联动和委员会结论都只作补充，不替代评分。"
        elif debate_summary is not None:
            reason = "委员会结论只作补充，不替代评分。"
        elif spotlight is not None:
            reason = "跨任务联动只作一起参考，不替代当前任务结论。"
        else:
            reason = "当前判断以本任务研究结论为主。"
    elif source_key == "spotlight":
        if debate_summary is not None:
            reason = "先核对跨任务结论，委员会只补充分歧和风险。"
        else:
            reason = "先核对跨任务结论，再回到单任务原始记录。"
    else:
        reason = "当前没有独立候选卡，委员会结论只作解释，不改写评分。"
    return f"当前采用口径: {label}；{reason}"


def _candidate_review_path_lines(
    *,
    selected_card: DashboardCandidateCard,
    spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    spotlight_summary = (
        f"候选摘要: {_spotlight_decision_note(spotlight)}"
        if spotlight is not None and _spotlight_has_structured_summary(spotlight)
        else ""
    )
    compact_cross_market_line = ""
    debate_followup_line = ""
    if debate_summary is not None:
        compact_cross_market_line = _focus_cross_market_digest_line(
            selected_spotlight=spotlight,
            debate_summary=debate_summary,
            focus_display=selected_card.display_name,
        ).replace(f" | 先看 {selected_card.display_name}", "", 1)
        conclusion = _debate_conclusion_summary(
            debate_summary,
            spotlight=spotlight,
            focus_card=selected_card,
        )
        debate_followup_line = (
            conclusion.watch_line
            or conclusion.chain_or_trigger_line
            or conclusion.opposition_line
            or conclusion.support_line
        )
    return tuple(
        line
        for line in (
            _candidate_effective_decision_line(
                selected_card=selected_card,
                spotlight=spotlight,
                debate_summary=debate_summary,
            ),
            compact_cross_market_line,
            debate_followup_line,
            *_debate_vote_snapshot_lines(debate_summary),
            (
                f"修正原因: {debate_summary.adjustment_reason}"
                if debate_summary is not None and debate_summary.adjustment_reason
                else ""
            ),
            spotlight_summary,
            (
                _task_scope_line(_task_scope_summary(spotlight.task_labels))
                if spotlight is not None and len(spotlight.task_labels) > 1
                else ""
            ),
            (
                f"跨任务重点: {spotlight.blocker or spotlight.next_step}"
                if spotlight is not None
                and len(spotlight.task_labels) > 1
                and (spotlight.blocker or spotlight.next_step)
                and (spotlight.blocker or spotlight.next_step)
                not in {
                    _card_primary_blocker(selected_card),
                    _card_next_action(selected_card),
                }
                else ""
            ),
        )
        if line
    )


def _render_candidate_journey(
    journey_steps: tuple[DashboardCandidateJourneyStep, ...],
    *,
    review_card: DashboardCandidateCard | None,
    spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
) -> None:
    if not _should_render_candidate_journey(
        spotlight=spotlight,
        debate_summary=debate_summary,
        journey_steps=journey_steps,
    ):
        return
    if not journey_steps and debate_summary is not None:
        return

    if not journey_steps:
        st.info(
            _candidate_empty_journey_message(
                review_card=review_card,
                spotlight=spotlight,
                debate_summary=debate_summary,
            )
        )
        return

    columns = st.columns(len(journey_steps))
    for column, step in zip(columns, journey_steps):
        with column:
            st.markdown(
                "\n".join(
                    line
                    for line in [
                        f"### {step.phase_label}",
                        f"- 任务: {step.task_label}",
                        f"- 评分: `{step.score:.1f}`",
                        f"- 当前结论: {_action_status_label(step.action_label, step.status_label)}",
                        (
                            f"- {_review_meta_line('复核节奏', step.review_meta)}"
                            if _review_meta_line("复核节奏", step.review_meta)
                            else ""
                        ),
                        f"- 当前重点: {step.blocker or step.next_step or '继续跟踪'}",
                        (
                            f"- 理由: {'；'.join(step.reasons[:2])}"
                            if step.reasons
                            else "- 理由: -"
                        ),
                        (
                            f"- 风险: {'；'.join(step.risks[:2])}"
                            if step.risks
                            else "- 风险: -"
                        ),
                    ]
                    if line
                )
            )


def _candidate_empty_journey_message(
    *,
    review_card: DashboardCandidateCard | None,
    spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
) -> str:
    if debate_summary is not None and review_card is not None:
        return "该标的当前只有讨论结论可参考，先等下一次行情刷新给出独立候选证据。"
    if (
        spotlight is not None
        and review_card is not None
        and review_card.rank_label == "同日联动"
    ):
        return "该标的当前只有同日观察线索，先等下一次刷新确认是否进入复核。"
    return "该标的在当前回看日只有单任务记录，暂无跨阶段来龙去脉。"


def _candidate_linkage_context(
    *,
    spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
    task_summary: str,
) -> tuple[str, tuple[str, ...], str]:
    if spotlight is not None and len(spotlight.task_labels) > 1:
        linkage_lines = _spotlight_global_view_lines(
            spotlight,
            reason_label="汇总理由",
            risk_label="汇总风险",
        )
        return (
            "跨任务视角",
            tuple(
                line
                for line in (
                    _task_scope_line(task_summary),
                    *linkage_lines,
                )
                if line
            )
            or ("当前已进入同日联动，但暂未提炼出更多结构化结论。",),
            "archive",
        )
    if spotlight is not None:
        linkage_lines = _spotlight_global_view_lines(
            spotlight,
            reason_label="同日摘要",
            risk_label="主要风险",
        )
        return (
            "单任务证据",
            tuple(
                line
                for line in (
                    _task_scope_line(task_summary),
                    *linkage_lines,
                )
                if line
            )
            or (
                _task_scope_line(task_summary),
                "当前只在本任务中出现，没有额外同日参考信息。",
            ),
            "archive",
        )
    if debate_summary is not None:
        debate_lines = tuple(
            line
            for line in (
                (
                    f"主要机会: {'；'.join(debate_summary.opportunity_highlights[:2])}"
                    if debate_summary.opportunity_highlights
                    else ""
                ),
                (
                    f"主要风险: {'；'.join(debate_summary.risk_warnings[:2])}"
                    if debate_summary.risk_warnings
                    else ""
                ),
                _task_scope_line(task_summary),
            )
            if line
        )
        return (
            "风险与机会",
            debate_lines
            or (
                "当前判断来自多 Agent 委员会补齐。",
                _task_scope_line(task_summary),
            ),
            "archive",
        )
    return (
        "单任务证据",
        (
            _task_scope_line(task_summary),
            "当前只在本任务中出现，没有额外同日参考信息。",
        ),
        "archive",
    )


def _candidate_discussion_snapshot_context(
    selected_card: DashboardCandidateCard | None,
    debate_summary: DashboardDebateSummary | None,
    spotlight: DashboardCandidateSpotlight | None = None,
) -> tuple[str, tuple[str, ...], str]:
    if debate_summary is None:
        return ("多 Agent 摘要", (), "archive")
    conclusion = _debate_conclusion_summary(debate_summary)
    result_line = (
        conclusion.decision_line.replace("研究口径: ", "")
        .replace("当前结论: ", "")
        .strip()
        or debate_summary.recommended_adjustment_label
    )
    compact_cross_market_line = _focus_cross_market_digest_line(
        selected_spotlight=spotlight,
        debate_summary=debate_summary,
        focus_display=(
            selected_card.display_name
            if selected_card is not None
            else debate_summary.display_name
        ),
    ).replace(
        f" | 先看 {selected_card.display_name if selected_card is not None else debate_summary.display_name}",
        "",
        1,
    )
    watch_line = conclusion.watch_line or conclusion.chain_or_trigger_line
    trigger_line = (
        conclusion.chain_or_trigger_line
        if conclusion.chain_or_trigger_line != watch_line
        else ""
    )
    lines = _unique_lines(
        (
            f"委员会结论: {result_line}",
            _candidate_effective_decision_line(
                selected_card=selected_card,
                spotlight=spotlight,
                debate_summary=debate_summary,
            ),
            compact_cross_market_line,
            trigger_line,
            watch_line,
        )
    )[:5]
    tone = (
        "blocked"
        if debate_summary.recommended_adjustment == "lower"
        else ("pressure" if debate_summary.disagreement_score >= 0.35 else "focus")
    )
    title = (
        f"{debate_summary.recommended_adjustment_label}"
        f" / 分歧 {debate_summary.disagreement_score:.2f}"
    )
    return title, lines, tone


def _render_candidate_review_snapshot(
    selected_card: DashboardCandidateCard,
    spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
    task_id: str = "",
    task_label: str = "",
    signal_date: str = "",
    journey_steps: tuple[DashboardCandidateJourneyStep, ...] = (),
    paper_frame=None,
    execution_frame=None,
) -> None:
    paper_empty = bool(getattr(paper_frame, "empty", True))
    execution_empty = bool(getattr(execution_frame, "empty", True))
    paper_event_count = len(getattr(paper_frame, "index", ()))
    execution_event_count = len(getattr(execution_frame, "index", ()))
    path_summary = (
        " -> ".join(step.phase_label for step in journey_steps)
        if journey_steps
        else "仅在当前任务中出现"
    )
    has_expanded_path = _candidate_has_expanded_path(
        spotlight=spotlight,
        debate_summary=debate_summary,
        journey_steps=journey_steps,
    )
    task_summary = (
        "、".join(spotlight.task_labels) if spotlight is not None else "仅当前任务"
    )
    execution_summary = (
        f"虚拟盘事件 {paper_event_count} / 纸面日志 {execution_event_count}"
        if not paper_empty or not execution_empty
        else (
            "当前仍处研究阻塞阶段，尚未进入纸面动作"
            if _card_primary_blocker(selected_card)
            else "当前仍处研究阶段，暂无纸面动作"
        )
    )
    linkage_title, linkage_lines, linkage_tone = _candidate_linkage_context(
        spotlight=spotlight,
        debate_summary=debate_summary,
        task_summary=task_summary,
    )
    has_execution_activity = not paper_empty or not execution_empty
    has_blocker = bool(_card_primary_blocker(selected_card))
    compact_mode = (
        not has_execution_activity and debate_summary is None and not has_expanded_path
    )
    debate_compact_mode = (
        debate_summary is not None
        and not has_execution_activity
        and spotlight is None
        and not journey_steps
    )
    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric(
        _candidate_score_metric_label(
            selected_card=selected_card,
            debate_summary=debate_summary,
        ),
        f"{selected_card.score:.1f}",
    )
    metric_col2.metric(
        "涉及任务", len(spotlight.task_labels) if spotlight is not None else 1
    )
    metric_col3.metric("虚拟盘事件", paper_event_count)
    metric_col4.metric("纸面日志", execution_event_count)

    if not debate_compact_mode:
        _render_debate_brief(debate_summary)

    summary_col, status_col = st.columns(2)
    with summary_col:
        st.markdown(
            f'<div class="aqsp-review-symbol">{escape(selected_card.display_name)}</div>',
            unsafe_allow_html=True,
        )
        if debate_compact_mode:
            summary_lines = [
                f"- 当前定位: {_committee_supplement_label()}",
                "- 使用边界: 辩论调整分，非主选股评分",
                "- 下一步: 等待独立候选路径或纸面记录补足依据。",
            ]
        else:
            review_source = _review_source_label(selected_card)
            summary_lines = [
                f"- 当前来源: {review_source}",
                (
                    f"- 当日经过: {path_summary}"
                    if has_expanded_path
                    else "- 当日经过: 单任务单阶段"
                ),
                f"- 纸面联动: {execution_summary}",
            ]
        st.markdown("\n".join(summary_lines))
    with status_col:
        status_title = linkage_title
        status_lines = linkage_lines
        status_tone = linkage_tone
        if debate_summary is not None and spotlight is None:
            status_title, status_lines, status_tone = (
                _candidate_discussion_snapshot_context(
                    selected_card,
                    debate_summary,
                    spotlight,
                )
            )
        _render_cockpit_card(
            kicker="多 Agent 摘要"
            if debate_summary is not None and spotlight is None
            else "证据落点",
            title=status_title,
            lines=status_lines,
            tone=status_tone,
        )

    research_lines = _candidate_research_lines(
        selected_card=selected_card,
        debate_summary=debate_summary,
        compact_mode=debate_compact_mode,
    )
    if has_expanded_path:
        research_col, path_col = st.columns(2)
    else:
        research_col = st.container()
        path_col = None

    with research_col:
        _render_cockpit_card(
            kicker="研究结论",
            title=_candidate_research_title(
                selected_card=selected_card,
                debate_summary=debate_summary,
                compact_mode=debate_compact_mode,
            ),
            lines=research_lines,
            tone=(
                "pressure"
                if debate_summary is not None
                and debate_summary.disagreement_score >= 0.35
                else ("blocked" if _card_primary_blocker(selected_card) else "focus")
            ),
        )
    if path_col is not None:
        path_lines = _candidate_review_path_lines(
            selected_card=selected_card,
            spotlight=spotlight,
            debate_summary=debate_summary,
        )
        if debate_compact_mode:
            path_lines = tuple(
                line for line in path_lines if not line.startswith("修正原因:")
            )
        with path_col:
            _render_cockpit_card(
                kicker="当天怎么串起来",
                title="委员会怎么看" if debate_summary is not None else path_summary,
                lines=path_lines,
                tone="archive",
            )

    next_step_lines = _candidate_next_step_lines(selected_card)
    if debate_compact_mode:
        debate_action_lines = tuple(
            line
            for line in (
                "验证动作: 等待下一次任务或纸面验证记录补充独立依据。",
                _review_meta_line("复核节奏", selected_card.review_meta),
                (
                    f"当前限制: {_card_primary_blocker(selected_card)}"
                    if has_blocker
                    else ""
                ),
                "当前状态: 尚未进入纸面动作，先等独立验证。",
            )
            if line
        )
        _render_cockpit_card(
            kicker="下一步怎么核",
            title="先等独立依据",
            lines=debate_action_lines,
            tone="blocked" if has_blocker else "archive",
        )
    elif compact_mode:
        _render_cockpit_card(
            kicker="接下来怎么看",
            title=_candidate_action_plan_title(selected_card),
            lines=next_step_lines,
            tone="pressure" if has_blocker else "archive",
        )
    else:
        execution_col, linkage_col = st.columns(2)
        with execution_col:
            _render_cockpit_card(
                kicker="纸面记录联动",
                title="纸面记录",
                lines=(execution_summary,),
                tone="pressure" if has_execution_activity else "archive",
            )
        with linkage_col:
            _render_cockpit_card(
                kicker="接下来怎么看",
                title=_candidate_action_plan_title(
                    selected_card,
                    default="如何回看依据",
                ),
                lines=next_step_lines,
                tone="pressure" if has_blocker else "archive",
            )

    nav_col1, nav_col2, nav_col3 = st.columns(3)
    with nav_col1:
        if _stretch_button(
            "虚拟盘",
            key=f"review-to-execution-{selected_card.symbol}",
        ):
            source_key = _candidate_effective_decision_source_key(
                selected_card=selected_card,
                spotlight=spotlight,
                debate_summary=debate_summary,
            )
            _queue_workspace_handoff(
                target_workspace="虚拟盘跟踪",
                source_workspace="候选复盘",
                symbol=selected_card.symbol,
                signal_date=signal_date,
                task_id=task_id,
                task_label=task_label,
                focus_kind=source_key,
                debate_id=debate_summary.debate_id
                if debate_summary is not None
                else "",
                decision_source=source_key,
                title="带着研究结论去看纸面验证",
                lines=_review_to_execution_handoff_lines(
                    selected_card=selected_card,
                    spotlight=spotlight,
                    debate_summary=debate_summary,
                ),
            )
            st.rerun()
    with nav_col2:
        if _stretch_button(
            "归档",
            key=f"review-to-report-{selected_card.symbol}",
        ):
            source_key = _candidate_effective_decision_source_key(
                selected_card=selected_card,
                spotlight=spotlight,
                debate_summary=debate_summary,
            )
            _queue_workspace_handoff(
                target_workspace="归档回看",
                source_workspace="候选复盘",
                symbol=selected_card.symbol,
                signal_date=signal_date,
                task_id=task_id,
                task_label=task_label,
                focus_kind=source_key,
                debate_id=debate_summary.debate_id
                if debate_summary is not None
                else "",
                decision_source=source_key,
                title="带着当前判断去看归档",
                lines=_review_to_archive_handoff_lines(
                    selected_card=selected_card,
                    spotlight=spotlight,
                    debate_summary=debate_summary,
                ),
            )
            st.rerun()
    with nav_col3:
        if _stretch_button(
            "首页",
            key=f"review-to-home-{selected_card.symbol}",
        ):
            _set_dashboard_workspace("决策首页")
            st.rerun()


def _render_candidate_deep_dive(
    *,
    provider: DashboardDataProvider,
    task_view,
    task_id: str,
    same_day_spotlights: tuple[DashboardCandidateSpotlight, ...],
) -> None:
    st.subheader("候选复盘")
    _render_workspace_handoff_notice(target_workspace="候选复盘")
    signal_date = task_view.selected_date or task_view.latest_date
    same_day_debates = _provider_prioritized_debates(provider, signal_date)
    review_cards = provider.candidate_review_cards(signal_date)
    symbol_order = _candidate_symbol_order(
        review_cards,
        same_day_spotlights,
        same_day_debates,
    )
    if not symbol_order:
        st.info("当前任务/日期暂无候选深度复盘对象。")
        return

    select_key = f"dashboard-review-symbol-{task_id}-{task_view.selected_date or task_view.latest_date}"
    pending_symbol = st.session_state.pop("dashboard_pending_review_symbol", None)
    selected_symbol = _render_workspace_symbol_selector(
        label="当前标的",
        workspace="候选复盘",
        symbol_order=symbol_order,
        select_key=select_key,
        pending_symbol=pending_symbol,
        cards=review_cards,
        spotlights=same_day_spotlights,
        debates=same_day_debates,
        signal_date=signal_date,
        task_id=task_id,
        task_label=str(getattr(task_view, "task_label", "") or ""),
    )
    selected_card, spotlight, debate_summary, review_card = _review_context_for_symbol(
        symbol=selected_symbol,
        cards=review_cards,
        spotlights=same_day_spotlights,
        debates=same_day_debates,
    )
    if review_card is None:
        st.info("当前标的缺少可回看的研究结论和同日参考信息。")
        return

    journey_steps = provider.same_day_candidate_journey(
        signal_date,
        selected_symbol,
    )
    same_day_rows = provider.same_day_task_rows(signal_date)
    review_source_task_id = _research_task_id_for_review_card(
        review_card=review_card,
        journey_steps=journey_steps,
        fallback_task_id=task_id,
    )
    research_context = provider.candidate_research_context(
        signal_date=signal_date,
        symbol=selected_symbol,
        preferred_task_id=review_source_task_id,
    )
    if research_context is None:
        signal_task_id, evidence_title = _signal_evidence_context(review_source_task_id)
    else:
        signal_task_id = research_context["task_id"]
        evidence_title = f"同日研究证据（{research_context['task_label']}）"
    _render_review_phase_bar(
        signal_date=signal_date,
        current_task_id=task_id,
        selected_symbol=selected_symbol,
        same_day_rows=same_day_rows,
        journey_steps=journey_steps,
        research_task_id=signal_task_id,
        selected_card=selected_card,
        selected_spotlight=spotlight,
        debate_summary=debate_summary,
    )
    signal_frame = provider.latest_signal_frame(
        limit=30,
        task_id=signal_task_id,
        signal_date=task_view.selected_date,
    )
    signal_frame = _filter_frame_by_symbol(signal_frame, selected_symbol)

    paper_frame = provider.paper_events_frame(
        limit=50,
        signal_date=task_view.selected_date,
    )
    paper_frame = _filter_frame_by_symbol(paper_frame, selected_symbol)

    execution_frame = provider.recent_execution_frame(
        limit=50,
        signal_date=task_view.selected_date,
    )
    execution_frame = _filter_frame_by_symbol(
        execution_frame,
        selected_symbol,
        time_prefix=task_view.selected_date,
    )
    task_frame = provider.latest_signal_frame(
        limit=30,
        task_id=task_id if task_id != "briefing" else "main_chain",
        signal_date=task_view.selected_date,
    )
    task_frame = _filter_frame_by_symbol(task_frame, selected_symbol)
    if signal_task_id == (task_id if task_id != "briefing" else "main_chain"):
        task_frame = task_frame.iloc[0:0]
    execution_focus = provider.execution_focus(
        signal_date=signal_date,
        symbol=selected_symbol,
        task_id=task_id,
    )
    open_positions_frame = provider.open_positions_frame(signal_date=signal_date)
    open_positions_frame = _filter_frame_by_symbol(
        open_positions_frame, selected_symbol
    )

    _render_research_path(
        _research_path_steps(
            task_view=task_view,
            selected_symbol=selected_symbol,
            review_card=review_card,
            selected_card=selected_card,
            selected_spotlight=spotlight,
            debate_summary=debate_summary,
            execution_focus=execution_focus,
            event_count=len(paper_frame.index),
            log_count=len(execution_frame.index),
            open_position_count=len(open_positions_frame.index),
            archive_status=_report_archive_status(task_view),
        )
    )

    _render_candidate_review_snapshot(
        review_card,
        spotlight,
        debate_summary,
        task_id,
        task_view.task_label,
        signal_date,
        journey_steps,
        paper_frame,
        execution_frame,
    )
    _render_candidate_evidence_drawers(
        review_card=review_card,
        spotlight=spotlight,
        debate_summary=debate_summary,
        journey_steps=journey_steps,
        signal_frame=signal_frame,
        task_frame=task_frame,
        paper_frame=paper_frame,
        execution_frame=execution_frame,
        evidence_title=evidence_title,
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
        st.markdown(f"**市场环境**: {market_environment or '暂无'}")
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


def _report_archive_status(task_view) -> str:
    if task_view.report_markdown.strip():
        return "已归档"
    if (
        task_view.report_summary_lines
        or task_view.runtime_lines
        or task_view.next_day_focus_lines
    ):
        return "有摘要"
    return "无归档"


def _archive_conclusion_title(
    *,
    task_view,
    archive_title: str,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
) -> str:
    status = _report_archive_status(task_view)
    if archive_title in {"归档结论", "任务级归档结论"}:
        return status

    if selected_card is not None:
        source = (
            "辩论补齐结论"
            if selected_card.rank_label == "辩论主结论"
            else "研究候选结论"
        )
    elif selected_spotlight is not None:
        source = "同日联动结论"
    elif debate_summary is not None:
        source = "辩论补齐结论"
    else:
        return status

    if archive_title == "当前标的结论" and status == "已归档":
        supplement = {
            "研究候选结论": "候选补齐",
            "同日联动结论": "联动补齐",
            "辩论补齐结论": "辩论补齐",
        }.get(source, "补充说明")
        return f"归档未命中该标的 / {supplement}"

    suffix = {
        "已归档": "已归档",
        "有摘要": "有归档摘要",
        "无归档": "未归档",
    }.get(status, status)
    return f"{source} / {suffix}"


def _unique_lines(*groups: tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    for group in groups:
        for raw_line in group:
            line = raw_line.strip()
            if line and line not in ordered:
                ordered.append(line)
    return tuple(ordered)


def _line_mentions_symbol(line: str, symbol: str) -> bool:
    normalized = line.strip()
    selected_symbol = symbol.strip()
    if not normalized or not selected_symbol:
        return False
    searchable = (
        normalized.replace("**", "")
        .replace("【", " ")
        .replace("】", " ")
        .replace("（", " ")
        .replace("）", " ")
    )
    pattern = rf"(^|[\s:：|｜,，;；(]){re.escape(selected_symbol)}($|[\s:：|｜,，;；)])"
    return re.search(pattern, searchable) is not None


def _partition_symbol_lines(
    lines: tuple[str, ...],
    symbol: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    matched: list[str] = []
    remainder: list[str] = []
    for line in lines:
        if _line_mentions_symbol(line, symbol):
            matched.append(line)
        else:
            remainder.append(line)
    return tuple(matched), tuple(remainder)


def _archive_focus_aliases(
    *,
    selected_symbol: str,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    aliases = [selected_symbol.strip()]
    for display_name in (
        selected_card.display_name if selected_card is not None else "",
        selected_card.name if selected_card is not None else "",
        selected_spotlight.display_name if selected_spotlight is not None else "",
        debate_summary.display_name if debate_summary is not None else "",
    ):
        clean = str(display_name or "").strip()
        if not clean:
            continue
        aliases.append(clean)
        if " " in clean:
            aliases.append(clean.partition(" ")[2].strip())
    return tuple(alias for alias in _unique_lines(tuple(aliases)) if alias)


def _line_mentions_archive_focus(line: str, aliases: tuple[str, ...]) -> bool:
    normalized = line.strip().replace("**", "")
    if not normalized:
        return False
    for alias in aliases:
        if not alias:
            continue
        if alias.isdigit():
            if _line_mentions_symbol(normalized, alias):
                return True
        elif alias in normalized:
            return True
    return False


def _partition_archive_focus_lines(
    lines: tuple[str, ...],
    *,
    selected_symbol: str,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    aliases = _archive_focus_aliases(
        selected_symbol=selected_symbol,
        selected_card=selected_card,
        selected_spotlight=selected_spotlight,
        debate_summary=debate_summary,
    )
    matched: list[str] = []
    remainder: list[str] = []
    for line in lines:
        if _line_mentions_archive_focus(line, aliases):
            matched.append(line)
        else:
            remainder.append(line)
    return tuple(matched), tuple(remainder)


def _action_status_label(action_label: str, status_label: str) -> str:
    action = action_label.strip()
    status = status_label.strip()
    if action and status:
        if action == status:
            return action
        if action in {"继续观察", "继续观察名单"} and status == "结果不变":
            return status
        return f"{action} / {status}"
    return action or status or "-"


def _candidate_research_title(
    *,
    selected_card: DashboardCandidateCard,
    debate_summary: DashboardDebateSummary | None,
    compact_mode: bool,
) -> str:
    if compact_mode and debate_summary is not None:
        return (
            f"{debate_summary.recommended_adjustment_label} / "
            f"分歧 {debate_summary.disagreement_score:.2f}"
        )
    if _candidate_is_explicitly_blocked(selected_card):
        return "阻塞待核对"
    return _action_status_label(selected_card.action_label, selected_card.status_label)


def _candidate_is_explicitly_blocked(card: DashboardCandidateCard) -> bool:
    if not _card_primary_blocker(card):
        return False
    return "阻塞" in card.rank_label or "阻塞" in card.status_label


def _candidate_research_lines(
    *,
    selected_card: DashboardCandidateCard,
    debate_summary: DashboardDebateSummary | None,
    compact_mode: bool,
) -> tuple[str, ...]:
    if debate_summary is not None:
        conclusion = _debate_conclusion_summary(
            debate_summary,
            focus_card=selected_card,
        )
        compact_cross_market_line = _focus_cross_market_digest_line(
            debate_summary=debate_summary,
            focus_display=selected_card.display_name,
        )
        debate_lines = (
            conclusion.decision_line,
            compact_cross_market_line or conclusion.cross_market_line,
            ""
            if compact_mode
            else _compact_debate_followup_line(
                debate_summary,
                conclusion=conclusion,
            ),
        )
    else:
        debate_lines = ()
    return tuple(
        line
        for line in (
            _candidate_score_context_line(selected_card),
            *debate_lines,
        )
        if line
    )


def _candidate_score_context_line(selected_card: DashboardCandidateCard) -> str:
    if selected_card.rank_label == "辩论主结论":
        return f"辩论调整分（非选股评分）: {selected_card.score:.1f}"
    return f"排队层级: {selected_card.rank_label} / 评分 {selected_card.score:.1f}"


def _candidate_score_metric_label(
    *,
    selected_card: DashboardCandidateCard,
    debate_summary: DashboardDebateSummary | None,
) -> str:
    if selected_card.rank_label == "辩论主结论" and debate_summary is not None:
        return "辩论调整分"
    return "当前评分"


def _candidate_next_step_lines(
    selected_card: DashboardCandidateCard,
) -> tuple[str, ...]:
    blocker = _card_primary_blocker(selected_card)
    next_action = _card_next_action(selected_card)
    if blocker:
        lines = (
            f"当前限制: {_safe_current_research_line(blocker)}",
            f"再看动作: {next_action}" if next_action != "-" else "",
            _review_meta_line("再看时间", selected_card.review_meta),
        )
    else:
        missing_evidence_line = (
            f"待补证据: {MISSING_BLOCKER_TEXT}"
            if _is_missing_blocker_text(selected_card.next_step)
            or _is_missing_blocker_text(selected_card.decision_note)
            else ""
        )
        if missing_evidence_line and next_action == "按当前顺位继续跟踪":
            next_action = "-"
        lines = (
            f"下一步: {next_action}" if next_action != "-" else "",
            missing_evidence_line,
            _review_meta_line("再看时间", selected_card.review_meta),
        )
    return tuple(line for line in lines if line) or ("当前没有额外推进动作。",)


def _candidate_action_plan_title(
    selected_card: DashboardCandidateCard,
    *,
    default: str = "按研究计划推进",
) -> str:
    return "先核对卡点" if _card_primary_blocker(selected_card) else default


def _join_display_parts(*parts: str, separator: str = " | ") -> str:
    return separator.join(part.strip() for part in parts if part and part.strip())


def _archive_symbol_order(
    task_view,
    review_cards: tuple[DashboardCandidateCard, ...],
    same_day_spotlights: tuple[DashboardCandidateSpotlight, ...],
    same_day_debates: tuple[DashboardDebateSummary, ...],
    open_positions_frame,
    paper_events_frame,
    execution_frame,
) -> list[str]:
    symbol_order = _candidate_symbol_order(
        review_cards or task_view.detail_cards,
        same_day_spotlights,
        same_day_debates,
    )
    for frame in (paper_events_frame, open_positions_frame, execution_frame):
        if frame.empty or "代码" not in frame.columns:
            continue
        for symbol in frame["代码"].astype(str).tolist():
            if symbol and symbol not in symbol_order:
                symbol_order.append(symbol)
    return symbol_order


def _archive_next_action_lines(
    *,
    task_view,
    selected_symbol: str,
    selected_card: DashboardCandidateCard | None,
    review_card: DashboardCandidateCard | None = None,
) -> tuple[str, ...]:
    raw_action_lines = _unique_lines(
        _safe_archive_lines(task_view.agenda_lines[:2]),
        _safe_archive_lines(task_view.review_lines[:2]),
        _safe_archive_lines(task_view.unlock_lines[:2]),
    )
    symbol_action_lines, _ = _partition_symbol_lines(raw_action_lines, selected_symbol)
    if symbol_action_lines:
        return symbol_action_lines
    action_card = selected_card or review_card
    if action_card is None:
        return ()
    if action_card.rank_label == "辩论主结论":
        return tuple(
            line
            for line in (
                (
                    f"优先处理阻塞: {_safe_current_research_line(_card_primary_blocker(action_card))}"
                    if _card_primary_blocker(action_card)
                    else ""
                ),
                "回到候选复盘核对分歧是否收敛。",
                _review_meta_line("按节奏复核", action_card.review_meta),
            )
            if line
        )
    return tuple(
        line
        for line in (
            (
                f"研究下一步: {_safe_current_research_line(action_card.next_step)}"
                if action_card.next_step
                else ""
            ),
            (
                f"优先处理阻塞: {_safe_current_research_line(_card_primary_blocker(action_card))}"
                if _card_primary_blocker(action_card)
                else ""
            ),
            _review_meta_line("按节奏复核", action_card.review_meta),
        )
        if line
    )


def _archive_followup_action_context(
    action_lines: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    if action_lines:
        return "接下来做什么", action_lines
    return (
        "待补归档动作",
        ("当前归档没有新增复盘动作，先看原文、研究链和纸面记录。",),
    )


def _archive_brief_cards(
    *,
    task_view,
    archive_lines: tuple[str, ...],
    conclusion_title: str,
    action_title: str,
    action_lines: tuple[str, ...],
    debate_summary: DashboardDebateSummary | None,
    has_execution_activity: bool,
    has_holding_activity: bool,
) -> tuple[_ArchiveBriefCard, ...]:
    reality_lines = tuple(
        line
        for line in (
            (
                "纸面记录: 当日有纸面事件或执行日志，优先核对纸面记录链。"
                if has_execution_activity
                else ""
            ),
            (
                "持有假设: 存在信号日绑定纸面持有，回看退出条件。"
                if has_holding_activity
                else ""
            ),
            (
                "纸面记录: 当前没有绑定纸面事件，归档侧先看研究结论和复核动作。"
                if not has_execution_activity and not has_holding_activity
                else ""
            ),
        )
        if line
    )
    debate_lines = _archive_debate_summary_lines(debate_summary)[:3]
    return (
        _ArchiveBriefCard(
            kicker="归档结论",
            title=conclusion_title,
            lines=archive_lines[:3]
            or ("当前归档没有结构化结论，先回看原文和同日任务。",),
            tone="archive",
        ),
        _ArchiveBriefCard(
            kicker="接下来做什么",
            title=action_title,
            lines=action_lines,
            tone="pressure" if getattr(task_view, "blocker_lines", ()) else "focus",
        ),
        _ArchiveBriefCard(
            kicker="纸面记录",
            title="有纸面联动"
            if has_execution_activity or has_holding_activity
            else "纸面侧较轻",
            lines=reality_lines,
            tone="pressure" if has_execution_activity else "archive",
        ),
        _ArchiveBriefCard(
            kicker="委员会怎么看",
            title=(
                f"{debate_summary.recommended_adjustment_label} / 分歧 {debate_summary.disagreement_score:.2f}"
                if debate_summary is not None
                else "当日未触发"
            ),
            lines=debate_lines
            or ("当前标的没有同日多 Agent 讨论归档，先按研究结论与纸面记录复盘。",),
            tone="pressure" if debate_summary is not None else "archive",
        ),
    )


def _render_archive_brief_cards(cards: tuple[_ArchiveBriefCard, ...]) -> None:
    card_html = []
    for card in cards:
        line_html = "".join(
            f'<div class="aqsp-archive-line">{escape(line)}</div>'
            for line in card.lines[:3]
        )
        card_html.append(
            f"""
            <div class="aqsp-archive-brief-card {escape(card.tone)}">
              <div class="aqsp-archive-kicker">{escape(card.kicker)}</div>
              <div class="aqsp-archive-title">{escape(card.title)}</div>
              {line_html}
            </div>
            """
        )
    st.markdown(
        "\n".join(
            [
                '<div class="aqsp-archive-brief-grid">',
                *card_html,
                "</div>",
            ]
        ),
        unsafe_allow_html=True,
    )


def _render_archive_focus_brief(
    *,
    task_view,
    selected_symbol: str,
    selected_card: DashboardCandidateCard | None,
    selected_spotlight: DashboardCandidateSpotlight | None,
    execution_focus,
    debate_summary: DashboardDebateSummary | None,
    has_execution_activity: bool,
    has_holding_activity: bool,
) -> None:
    archive_review_card = selected_card or _review_fallback_card(
        selected_card=selected_card,
        selected_spotlight=selected_spotlight,
        debate_summary=debate_summary,
    )
    action_lines = _archive_next_action_lines(
        task_view=task_view,
        selected_symbol=selected_symbol,
        selected_card=selected_card,
        review_card=archive_review_card,
    )
    archive_title, archive_lines = _archive_conclusion_context(
        task_view=task_view,
        selected_symbol=selected_symbol,
        selected_card=selected_card,
        selected_spotlight=selected_spotlight,
        debate_summary=debate_summary,
    )
    action_title, resolved_action_lines = _archive_followup_action_context(action_lines)
    conclusion_title = _archive_conclusion_title(
        task_view=task_view,
        archive_title=archive_title,
        selected_card=selected_card,
        selected_spotlight=selected_spotlight,
        debate_summary=debate_summary,
    )
    _render_archive_brief_cards(
        _archive_brief_cards(
            task_view=task_view,
            archive_lines=archive_lines,
            conclusion_title=conclusion_title,
            action_title=action_title,
            action_lines=resolved_action_lines,
            debate_summary=debate_summary,
            has_execution_activity=has_execution_activity,
            has_holding_activity=has_holding_activity,
        )
    )
    compact_mode = (
        debate_summary is None
        and not has_execution_activity
        and not has_holding_activity
    )
    if compact_mode:
        archive_col, action_col = st.columns(2)
        with archive_col:
            _render_cockpit_card(
                kicker=archive_title,
                title=conclusion_title,
                lines=archive_lines
                or ("当前归档没有结构化结论，先回看原文和同日任务。",),
                tone="archive",
            )
        with action_col:
            _render_cockpit_card(
                kicker="回看重点",
                title=action_title,
                lines=resolved_action_lines,
                tone="pressure" if task_view.blocker_lines else "focus",
            )
        return

    archive_col, action_col, debate_col = st.columns(3)
    with archive_col:
        _render_cockpit_card(
            kicker=archive_title,
            title=conclusion_title,
            lines=archive_lines or ("当前归档没有结构化结论，先回看原文和同日任务。",),
            tone="archive",
        )
    with action_col:
        _render_cockpit_card(
            kicker="回看重点",
            title=action_title,
            lines=resolved_action_lines,
            tone="pressure" if task_view.blocker_lines else "focus",
        )
    with debate_col:
        _render_cockpit_card(
            kicker="委员会怎么看",
            title=(
                f"{debate_summary.recommended_adjustment_label} / 分歧 {debate_summary.disagreement_score:.2f}"
                if debate_summary is not None
                else "当日未触发"
            ),
            lines=_archive_debate_summary_lines(debate_summary)
            or ("当前标的没有同日多 Agent 讨论归档，先按研究结论与纸面记录复盘。",),
            tone="archive",
        )


def _render_archive_workbench(
    *,
    provider: DashboardDataProvider,
    task_view,
    review_date: str,
    same_day_rows: tuple[DashboardSameDayTaskRow, ...],
    same_day_spotlights: tuple[DashboardCandidateSpotlight, ...],
) -> None:
    st.subheader("归档回看")
    _render_workspace_handoff_notice(target_workspace="归档回看")
    status = _report_archive_status(task_view)
    same_day_debates = _provider_prioritized_debates(provider, review_date)
    review_cards = provider.candidate_review_cards(review_date)
    open_positions_frame = provider.open_positions_frame(signal_date=review_date)
    paper_events_frame = provider.paper_events_frame(limit=50, signal_date=review_date)
    execution_frame = provider.recent_execution_frame(limit=50, signal_date=review_date)
    symbol_order = _archive_symbol_order(
        task_view,
        review_cards,
        same_day_spotlights,
        same_day_debates,
        open_positions_frame,
        paper_events_frame,
        execution_frame,
    )

    if not symbol_order:
        st.info("当前日期没有可聚焦的候选或纸面验证对象，先看归档摘要与原文。")
        _render_cockpit_card(
            kicker="归档结论",
            title=status,
            lines=_safe_archive_lines(
                tuple(
                    line
                    for line in (
                        *task_view.report_summary_lines[:2],
                        *task_view.next_day_focus_lines[:2],
                        *task_view.runtime_lines[:2],
                    )
                    if line
                )
            )
            or ("当前归档没有结构化结论。",),
            tone="archive",
        )
        return

    select_key = f"dashboard-archive-symbol-{task_view.task_id}-{review_date}"
    pending_symbol = st.session_state.pop("dashboard_pending_archive_symbol", None)
    selected_symbol = _render_workspace_symbol_selector(
        label="当前标的",
        workspace="归档回看",
        symbol_order=symbol_order,
        select_key=select_key,
        pending_symbol=pending_symbol,
        cards=review_cards,
        spotlights=same_day_spotlights,
        debates=same_day_debates,
        signal_date=review_date,
        task_id=task_view.task_id,
        task_label=str(getattr(task_view, "task_label", "") or ""),
    )

    selected_card, selected_spotlight, debate_summary, review_card = (
        _review_context_for_symbol(
            symbol=selected_symbol,
            cards=review_cards,
            spotlights=same_day_spotlights,
            debates=same_day_debates,
        )
    )
    scoped_open_positions = _filter_frame_by_symbol(
        open_positions_frame, selected_symbol
    )
    scoped_paper_events = _filter_frame_by_symbol(paper_events_frame, selected_symbol)
    scoped_execution = _filter_frame_by_symbol(
        execution_frame,
        selected_symbol,
        time_prefix=review_date,
    )
    execution_focus = provider.execution_focus(
        signal_date=review_date,
        symbol=selected_symbol,
        task_id=task_view.task_id,
    )
    research_context = provider.candidate_research_context(
        signal_date=review_date,
        symbol=selected_symbol,
        preferred_task_id=task_view.task_id,
    )

    _render_workspace_focus_header(
        title="归档焦点",
        selected_date=review_date,
        selected_card=selected_card,
        selected_spotlight=selected_spotlight,
        review_card=review_card,
        execution_focus=execution_focus,
        event_count=len(scoped_paper_events.index),
        log_count=len(scoped_execution.index),
        open_position_count=len(scoped_open_positions.index),
        same_day_task_count=len(same_day_rows),
        archive_status=status,
        show_archive_metric=True,
    )
    _render_research_path(
        _research_path_steps(
            task_view=task_view,
            selected_symbol=selected_symbol,
            review_card=review_card,
            selected_card=selected_card,
            selected_spotlight=selected_spotlight,
            debate_summary=debate_summary,
            execution_focus=execution_focus,
            event_count=len(scoped_paper_events.index),
            log_count=len(scoped_execution.index),
            open_position_count=len(scoped_open_positions.index),
            archive_status=status,
        )
    )
    _render_archive_focus_brief(
        task_view=task_view,
        selected_symbol=selected_symbol,
        selected_card=selected_card,
        selected_spotlight=selected_spotlight,
        execution_focus=execution_focus,
        debate_summary=debate_summary,
        has_execution_activity=not scoped_paper_events.empty
        or not scoped_execution.empty,
        has_holding_activity=not scoped_open_positions.empty,
    )

    raw_action_lines = _unique_lines(
        task_view.agenda_lines[:3],
        task_view.review_lines[:3],
        task_view.unlock_lines[:3],
    )
    _, global_action_lines = _partition_symbol_lines(
        raw_action_lines,
        selected_symbol,
    )

    nav_col1, nav_col2, nav_col3 = st.columns(3)
    with nav_col1:
        if _stretch_button(
            "首页",
            key=f"archive-to-home-{task_view.task_id}-{review_date}",
        ):
            _set_dashboard_workspace("决策首页")
            st.rerun()
    with nav_col2:
        if _stretch_button(
            "复盘",
            key=f"archive-to-review-{selected_symbol}-{task_view.task_id}-{review_date}",
            disabled=review_card is None,
        ):
            source_key = _candidate_effective_decision_source_key(
                selected_card=selected_card,
                spotlight=selected_spotlight,
                debate_summary=debate_summary,
            )
            _queue_workspace_handoff(
                target_workspace="候选复盘",
                source_workspace="归档回看",
                symbol=selected_symbol,
                signal_date=review_date,
                task_id=task_view.task_id,
                task_label=task_view.task_label,
                focus_kind=source_key,
                debate_id=debate_summary.debate_id
                if debate_summary is not None
                else "",
                decision_source=source_key,
                title="带着归档结论回看已有判断",
                lines=_archive_to_review_handoff_lines(
                    task_view=task_view,
                    selected_symbol=selected_symbol,
                    selected_card=selected_card,
                    review_card=review_card,
                ),
            )
            st.rerun()
    with nav_col3:
        if _stretch_button(
            "虚拟盘",
            key=f"archive-to-execution-{selected_symbol}-{task_view.task_id}-{review_date}",
        ):
            source_key = _candidate_effective_decision_source_key(
                selected_card=selected_card,
                spotlight=selected_spotlight,
                debate_summary=debate_summary,
            )
            _queue_workspace_handoff(
                target_workspace="虚拟盘跟踪",
                source_workspace="归档回看",
                symbol=selected_symbol,
                signal_date=review_date,
                task_id=task_view.task_id,
                task_label=task_view.task_label,
                focus_kind=source_key,
                debate_id=debate_summary.debate_id
                if debate_summary is not None
                else "",
                decision_source=source_key,
                title="带着归档结论去看纸面验证",
                lines=_archive_to_execution_handoff_lines(
                    task_view=task_view,
                    selected_symbol=selected_symbol,
                    selected_card=selected_card,
                    spotlight=selected_spotlight,
                    debate_summary=debate_summary,
                    review_card=review_card,
                ),
            )
            st.rerun()

    with st.expander("归档证据", expanded=False):
        reality_col, context_col = st.columns(2)
        with reality_col:
            _render_line_block(
                "纸面验证现实",
                tuple(
                    line
                    for line in (
                        *_prioritized_research_lines(execution_focus.research_lines),
                        *execution_focus.readiness_lines[:2],
                        *execution_focus.execution_lines[:2],
                        *execution_focus.holding_lines[:2],
                    )
                    if line
                ),
                "当前暂无纸面验证证据。",
            )
        with context_col:
            _render_line_block(
                "同日全局关注",
                _unique_lines(
                    global_action_lines[:4],
                    tuple(
                        f"{row.task_label}: {row.headline}"
                        for row in same_day_rows
                        if row.task_id != task_view.task_id
                    )[:3],
                ),
                "当前日期没有额外全局关注项。",
            )
        journey_steps = provider.same_day_candidate_journey(
            review_date, selected_symbol
        )
        if journey_steps:
            _render_candidate_journey(
                journey_steps,
                review_card=review_card,
                spotlight=selected_spotlight,
                debate_summary=debate_summary,
            )
        evidence_left, evidence_right = st.columns(2)
        if research_context is None:
            signal_task_id, evidence_title = _signal_evidence_context(task_view.task_id)
        else:
            signal_task_id = research_context["task_id"]
            evidence_title = f"同日研究证据（{research_context['task_label']}）"
        with evidence_left:
            _render_frame(
                evidence_title,
                _filter_frame_by_symbol(
                    provider.latest_signal_frame(
                        limit=30,
                        task_id=signal_task_id,
                        signal_date=review_date,
                    ),
                    selected_symbol,
                ),
            )
            _render_frame("当日虚拟盘事件", scoped_paper_events)
        with evidence_right:
            _render_frame("信号日绑定纸面持有记录", scoped_open_positions)
            _render_frame("当日纸面日志", scoped_execution)


def _render_report_archive_center(
    *,
    provider: DashboardDataProvider,
    review_date: str,
    same_day_rows: tuple[DashboardSameDayTaskRow, ...],
    current_task_id: str,
) -> None:
    st.subheader("同日归档中心")
    if not same_day_rows:
        st.info("当前日期暂无可回看的同日归档。")
        return

    for start in range(0, len(same_day_rows), 3):
        columns = st.columns(min(len(same_day_rows[start : start + 3]), 3))
        for column, row in zip(columns, same_day_rows[start : start + 3]):
            view = provider.build_task_view(row.task_id, signal_date=review_date)
            status = _report_archive_status(view)
            with column:
                summary_line = next(
                    iter(_safe_archive_lines(view.report_summary_lines[:1])),
                    "当前无结构化摘要",
                )
                focus_line = next(
                    iter(_safe_archive_lines(view.next_day_focus_lines[:1])),
                    "-",
                )
                st.markdown(
                    "\n".join(
                        [
                            f"### {row.task_label}",
                            f"- 阶段: {row.phase_label}",
                            f"- 归档状态: {status}",
                            f"- 回看结论: {view.headline}",
                            f"- 历史摘要: {summary_line}",
                            f"- 历史下一日重点: {focus_line}",
                        ]
                    )
                )
                if row.task_id != current_task_id and _stretch_button(
                    "打开归档",
                    key=f"report-center-{row.task_id}-{review_date}",
                ):
                    _set_dashboard_selection(
                        task_id=row.task_id,
                        signal_date=review_date,
                    )
                    st.rerun()


def _raw_report_boundary_lines(task_view) -> tuple[str, ...]:
    selected_date = (
        getattr(task_view, "selected_date", "")
        or getattr(task_view, "latest_date", "")
        or "-"
    )
    task_label = getattr(task_view, "task_label", "归档任务")
    return (
        f"历史原文: {task_label} / {selected_date}",
        "以下内容只用于回看当时研究语境，不是今日动作、不是交易指令。",
        "原文中的行动词已在展示层中性化为研究口径，原始文件未被改写。",
    )


def _sanitize_raw_report_markdown(markdown_text: str) -> str:
    return sanitize_archive_text(markdown_text)


def _render_raw_report(task_view) -> None:
    st.subheader("归档原文")
    source = getattr(task_view, "report_source", "")
    mtime = getattr(task_view, "report_mtime", "")
    if source or mtime:
        st.caption(
            " / ".join(
                part
                for part in (
                    f"任务: {task_view.task_label}",
                    f"日期: {task_view.selected_date or task_view.latest_date or '-'}",
                    f"来源: {source}" if source else "",
                    f"生成时间: {mtime}" if mtime else "",
                )
                if part
            )
        )
    if task_view.report_markdown.strip():
        _render_line_block(
            "原文边界",
            _raw_report_boundary_lines(task_view),
            "当前暂无原文边界信息。",
        )
        st.markdown(_sanitize_raw_report_markdown(task_view.report_markdown))
    else:
        st.info("当前任务/日期暂无归档原文。")


def main() -> None:
    updated_at = now_shanghai().strftime("%Y-%m-%d %H:%M:%S %z")
    _inject_dashboard_styles()

    _render_simple_app_header(updated_at=updated_at)

    workspace_options = tuple(item.name for item in _workspace_nav_items())
    pending_workspace = st.session_state.pop("dashboard_pending_workspace", None)
    workspace = _workspace_widget_state(
        pending_workspace=pending_workspace,
        current_workspace=st.session_state.get("dashboard_workspace_widget"),
        workspace_options=workspace_options,
    )
    st.session_state["dashboard_workspace_widget"] = workspace

    if workspace == "决策首页":
        _render_workspace_navigation()
        snapshot_path = _home_snapshot_path()
        index_path = _home_snapshot_index_path(snapshot_path)
        snapshot_index = load_home_snapshot_index(index_path)
        if snapshot_index is not None:
            selected_date = _snapshot_index_selected_date(snapshot_index)
            indexed_snapshot = snapshot_index.snapshot_for_date(selected_date)
            if indexed_snapshot is not None:
                _render_snapshot_home_board(
                    indexed_snapshot,
                    available_dates=snapshot_index.available_dates,
                )
            elif snapshot_index.days:
                _render_snapshot_unavailable_home()
            else:
                _render_snapshot_unavailable_home()
            return
        if Path(index_path).expanduser().is_file():
            _render_snapshot_unavailable_home()
            return

        snapshot = load_home_dashboard_snapshot(snapshot_path)
        if snapshot is not None:
            _render_snapshot_home_board(snapshot)
            return
        if Path(snapshot_path).expanduser().is_file():
            _render_snapshot_unavailable_home()
            return

    provider = get_provider()
    if workspace == "决策首页":
        selected_task_id, selected_date = _default_home_selection(provider)
    else:
        latest_task_snapshots = provider.task_snapshots()
        options = provider.task_options()
        selected_task_id, selected_date = _render_top_navigation(
            options=options,
            snapshots=latest_task_snapshots,
            provider=provider,
            render_controls=False,
        )
        st.markdown('<div class="aqsp-workspace-shell">', unsafe_allow_html=True)
        st.markdown(
            '<div class="aqsp-workspace-label">工作台视角</div>',
            unsafe_allow_html=True,
        )
        workspace = _render_workspace_navigation(pending_workspace=workspace)
        st.markdown("</div>", unsafe_allow_html=True)

    home_payload = None
    if workspace == "决策首页":
        home_payload = _provider_home_digest_payload(
            provider,
            selected_task_id,
            selected_date,
        )
        task_view = home_payload.task_view
    else:
        task_view = provider.build_task_view(
            selected_task_id,
            signal_date=selected_date,
        )
    review_date = task_view.selected_date or task_view.latest_date or selected_date

    if workspace == "决策首页":
        same_day_rows = home_payload.same_day_rows if home_payload else ()
        same_day_spotlights = home_payload.spotlights if home_payload else ()
        same_day_debates = (
            home_payload.debates
            if home_payload and home_payload.debates
            else _provider_prioritized_debates(provider, review_date, limit=3)
        )
        date_overview = (
            home_payload.overview if home_payload else _empty_home_overview(review_date)
        )
        paper_summary = (
            home_payload.paper_summary
            if home_payload
            else _empty_home_paper_summary(review_date)
        )
        _render_simple_home_board(
            provider=provider,
            signal_date=review_date,
            task_view=task_view,
            same_day_rows=same_day_rows,
            same_day_spotlights=same_day_spotlights,
            same_day_debates=same_day_debates,
            overview=date_overview,
            paper_summary=paper_summary,
        )
    elif workspace == "候选复盘":
        same_day_spotlights = provider.same_day_candidate_spotlights(review_date)
        _render_candidate_deep_dive(
            provider=provider,
            task_view=task_view,
            task_id=selected_task_id,
            same_day_spotlights=same_day_spotlights,
        )
        _render_review_sections(
            market_environment=task_view.market_environment,
            strategy_breakdown_lines=task_view.strategy_breakdown_lines,
            lesson_lines=task_view.lesson_lines,
            improvement_lines=task_view.improvement_lines,
        )
    elif workspace == "虚拟盘跟踪":
        _render_execution_focus(
            provider=provider,
            task_view=task_view,
        )
        with st.expander("纸面验证总览", expanded=False):
            paper_summary = provider.paper_summary(review_date)
            _render_paper_summary(paper_summary)
            data_col, paper_col = st.columns(2)
            with data_col:
                _render_frame(
                    "当前回看日绑定持仓",
                    provider.open_positions_frame(signal_date=task_view.selected_date),
                )
            with paper_col:
                _render_frame(
                    "虚拟盘事件",
                    provider.paper_events_frame(
                        limit=30, signal_date=task_view.selected_date
                    ),
                )

            _render_frame(
                "当前回看日纸面日志",
                provider.recent_execution_frame(
                    limit=30,
                    signal_date=task_view.selected_date,
                ),
            )
    else:
        same_day_rows = provider.same_day_task_rows(review_date)
        same_day_spotlights = provider.same_day_candidate_spotlights(review_date)
        _render_archive_workbench(
            provider=provider,
            task_view=task_view,
            review_date=review_date,
            same_day_rows=same_day_rows,
            same_day_spotlights=same_day_spotlights,
        )
        with st.expander("同日归档", expanded=False):
            _render_report_archive_center(
                provider=provider,
                review_date=review_date,
                same_day_rows=same_day_rows,
                current_task_id=task_view.task_id,
            )
        with st.expander("归档原文", expanded=False):
            _render_raw_report(task_view)


if __name__ == "__main__":
    main()
