"""Streamlit 仪表盘 - 顶部任务导航 + 历史回看。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape

import streamlit as st

from aqsp.core.time import now_shanghai
from aqsp.research.summary import (
    ResearchSummary,
    load_research_summary,
    research_findings_display,
    research_findings_metric,
)
from aqsp.web.archive_safety import sanitize_archive_text
from aqsp.web.data_provider import (
    DashboardCandidateCard,
    DashboardCandidateJourneyStep,
    DashboardCandidateSpotlight,
    DashboardDebateSummary,
    DashboardDateOverview,
    DashboardDataProvider,
    DashboardPaperSummary,
    DashboardSameDayTaskRow,
    DashboardTaskSnapshot,
    MISSING_BLOCKER_TEXT,
)


st.set_page_config(
    page_title="AQSP 日期任务研究台",
    layout="wide",
    initial_sidebar_state="collapsed",
)


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


def _inject_dashboard_styles() -> None:
    st.markdown(
        """
        <style>
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
            .aqsp-compact-grid {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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

    st.markdown(
        "\n".join(
            [
                f"- 请求源: `{requested}`",
                f"- 实际源: `{actual}`",
                f"- 健康度: `{health_label}`",
                f"- 最新交易日: `{latest_trade_date}`",
                f"- 数据滞后: `{lag_display}`",
                f"- 说明: {health_message}",
            ]
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
    return value.strip() not in {"", "-", "暂无额外复核节奏"}


def _review_meta_line(label: str, value: str) -> str:
    return f"{label}: {value.strip()}" if _has_review_meta(value) else ""


def _render_frame(title: str, frame) -> None:
    st.subheader(title)
    if frame.empty:
        st.info("暂无数据。")
        return
    st.dataframe(frame, width="stretch", hide_index=True)


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
        decision_note="该标的当前未在本任务独立落盘，以下复盘来自同日联动聚合与纸面验证记录。",
        next_step=spotlight.next_step,
        blocker=spotlight.blocker,
        review_meta=spotlight.review_meta,
        reasons=spotlight.reasons,
        risks=spotlight.risks,
        strategies=(),
        data_source="同日联动",
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
        score=debate_summary.adjusted_score,
        action_label=debate_summary.recommended_adjustment_label,
        status_label=debate_summary.consensus
        or debate_summary.recommended_adjustment_label,
        decision_note="该标的当前没有独立候选卡，以下仅是同日多 Agent 讨论摘要，不替代选股评分。",
        next_step=(
            debate_summary.adjustment_reason
            or (
                debate_summary.opportunity_highlights[0]
                if debate_summary.opportunity_highlights
                else ""
            )
        ),
        blocker=debate_summary.risk_warnings[0] if debate_summary.risk_warnings else "",
        review_meta="辩论主结论 / 待复核",
        reasons=debate_summary.opportunity_highlights,
        risks=debate_summary.risk_warnings,
        strategies=(),
        data_source=debate_summary.data_source or "多 Agent 讨论",
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
        f"分歧来源: 看多 {debate_summary.bull_count}"
        f" / 看空 {debate_summary.bear_count}"
        f" / 中性 {debate_summary.neutral_count}"
    )
    if debate_summary.risk_warnings:
        review_line = "待核对风险: " + "；".join(debate_summary.risk_warnings[:2])
    elif debate_summary.adjustment_reason:
        review_line = f"待核对依据: {debate_summary.adjustment_reason}"
    elif debate_summary.opportunity_highlights:
        review_line = "待验证机会: " + "；".join(
            debate_summary.opportunity_highlights[:2]
        )
    else:
        review_line = "待补证据: 当前辩论未给出明确风险或机会，先回候选证据链复核。"
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
        ),
        agent_lines,
    )


def _pipeline_label(pipeline: str) -> str:
    return {
        "data_source": "数据源",
        "strategy": "策略",
        "timing": "择时",
        "execution_risk": "执行风控",
        "ai_research": "AI 研究",
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
            title="研究吸收未更新",
            metrics=(
                ("研究发现", "-"),
                ("已吸收", "-"),
                ("只进报告", "-"),
                ("门控中", "-"),
            ),
            lines=(
                "当前只展示已落盘主链结果；研究队列缺失不影响当前主链评分。",
                "下一步: 先补齐研究吸收配置，再决定是否进入 report-only 或门控验证。",
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
                "边界: 研究吸收不会直接入分；只有通过验证和门控后才可能影响运行链。",
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
    if debate_summary is None:
        return ""
    parts = [
        f"{debate_summary.round_count} 轮讨论",
        f"{len(debate_summary.agent_views)} 个 agent 观点",
    ]
    if debate_summary.data_source:
        parts.append(f"数据源 {debate_summary.data_source}")
    if debate_summary.thresholds_version:
        parts.append(f"阈值 {debate_summary.thresholds_version}")
    return f"证据构成: {' / '.join(parts)}"


def _debate_primary_takeaways(
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    if debate_summary is None:
        return ()
    return tuple(
        line
        for line in (
            (
                f"辩论结论: {debate_summary.recommended_adjustment_label}"
                f" / 分歧 {debate_summary.disagreement_score:.2f}"
            ),
            (
                f"辩论共识: {debate_summary.consensus}"
                if debate_summary.consensus
                else ""
            ),
            (
                f"修正原因: {debate_summary.adjustment_reason}"
                if debate_summary.adjustment_reason
                else ""
            ),
            (
                f"辩论风险: {'；'.join(debate_summary.risk_warnings[:2])}"
                if debate_summary.risk_warnings
                else ""
            ),
            (
                f"辩论机会: {'；'.join(debate_summary.opportunity_highlights[:2])}"
                if debate_summary.opportunity_highlights
                else ""
            ),
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
        line
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
                    f"当前阻塞: {_card_primary_blocker(selected_card)}"
                    if _card_primary_blocker(selected_card)
                    else ""
                ),
                (
                    f"候选摘要: {selected_card.decision_note}"
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
                _review_meta_line("复核节奏", selected_card.review_meta),
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
                    f"当前重点: {selected_spotlight.blocker or selected_spotlight.next_step}"
                    if selected_spotlight.blocker or selected_spotlight.next_step
                    else ""
                ),
                _review_meta_line("统一复核", selected_spotlight.review_meta),
                (
                    f"来源任务: {'、'.join(selected_spotlight.task_labels)}"
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
                    f"调整依据: {debate_summary.adjustment_reason}"
                    if debate_summary.adjustment_reason
                    else ""
                ),
                _debate_evidence_composition_line(debate_summary),
            )
            if line
        ),
    )


def _render_debate_cockpit(
    *,
    debate_summary: DashboardDebateSummary | None,
    empty_text: str,
    kicker: str = "多 Agent 讨论",
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
        return blocker
    if card.next_step:
        return card.next_step
    if card.decision_note and card.decision_note != "按当前顺位继续跟踪":
        return card.decision_note
    if "阻塞" in card.rank_label:
        return "当前处于阻塞观察，先核对卡点条件。"
    return card.decision_note or "继续跟踪"


def _card_next_action(card: DashboardCandidateCard) -> str:
    if card.next_step and not _is_missing_blocker_text(card.next_step):
        return card.next_step
    if _card_primary_blocker(card) and "阻塞" in card.rank_label:
        return "先确认复核条件，卡点解除后再决定是否恢复推进。"
    if card.decision_note and not _is_missing_blocker_text(card.decision_note):
        return card.decision_note
    return "-"


def _normalized_readiness_lines(
    *,
    review_card: DashboardCandidateCard | None,
    readiness_lines: tuple[str, ...],
) -> tuple[str, ...]:
    blocker = _card_primary_blocker(review_card) if review_card is not None else ""
    generic_line = "研究已产出，但尚未进入纸面入场或阻塞队列。"
    if not blocker:
        return readiness_lines
    legacy_generic_line = "研究已产出，但尚未进入待开仓或阻塞队列。"
    normalized = tuple(
        line
        for line in readiness_lines
        if line not in {generic_line, legacy_generic_line}
    )
    if normalized:
        return normalized
    return (f"研究已产出，但当前被{blocker}拦住，暂不进入纸面入场验证链路。",)


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
    return _singleton_lines("当前没有独立观察对象。")


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
                f"当前无 ready 候选，先核对 {blocked_focus.display_name} 的卡点。"
            )
        return _singleton_lines(
            f"当前无 ready 候选，先核对 {blocked_focus.display_name} 的卡点。"
        )
    return _singleton_lines("当前无 ready 候选。")


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
        for view in debate_summary.agent_views[:2]
    )
    return _unique_lines(tuple(lines), top_agents)


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
    review_lines = _unique_lines(
        (
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
                else "修正原因: 当前未给出充分依据，回到候选证据链。"
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
            kicker="复核动作",
            title="先回证据链核对",
            lines=review_lines,
            tone="pressure" if debate_summary.risk_warnings else "archive",
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
        return "暂无结果"
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
    return "暂无结果"


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
        if card.blocker or "阻塞" in card.rank_label:
            blocked.append(card)
            continue
        if (
            card.rank_label in {"首选", "次选", "备选"}
            or card.action_label == "上调优先级"
        ):
            recommend.append(card)
            continue
        watch.append(card)
    return tuple(recommend), tuple(watch), tuple(blocked)


def _queue_item_meta(card: DashboardCandidateCard, emphasis: str) -> str:
    meta_parts = [
        f"动作 / 状态: {escape(_action_status_label(card.action_label, card.status_label))}"
    ]
    if _has_review_meta(card.review_meta):
        meta_parts.append(f"复核: {escape(card.review_meta)}")
    lines = [
        f'<div class="aqsp-queue-meta">{" / ".join(meta_parts)}</div>',
        f'<div class="aqsp-queue-meta">{escape(emphasis)}</div>',
    ]
    if card.decision_note and card.decision_note != emphasis:
        lines.append(
            f'<div class="aqsp-queue-meta">说明: {escape(card.decision_note)}</div>'
        )
    if card.reasons:
        lines.append(
            '<div class="aqsp-queue-meta">理由: '
            + escape("；".join(card.reasons[:2]))
            + "</div>"
        )
    if card.risks:
        lines.append(
            '<div class="aqsp-queue-meta">风险: '
            + escape("；".join(card.risks[:2]))
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


def _home_action_cards(
    task_view,
    spotlights: tuple[DashboardCandidateSpotlight, ...],
) -> tuple[DashboardCandidateCard, ...]:
    cards = list(task_view.detail_cards)
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


def _home_action_item_lines(card: DashboardCandidateCard) -> tuple[str, ...]:
    emphasis = _card_emphasis(card)
    return tuple(
        line
        for line in (
            f"研究入口: {_review_source_label(card)}",
            f"动作 / 状态: {_action_status_label(card.action_label, card.status_label)}",
            f"当前重点: {emphasis}",
            _review_meta_line("复核节奏", card.review_meta),
        )
        if line
    )


def _singleton_lines(text: str) -> tuple[str, ...]:
    return (text,)


def _home_debate_item_lines(debate_summary: DashboardDebateSummary) -> tuple[str, ...]:
    return _unique_lines(
        _debate_primary_takeaways(debate_summary)[:4],
        (
            "研究入口: 辩论主结论",
            "先看分歧来源，再决定是否回到候选证据链。",
        ),
    )


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
    rail_items: list[_HomeActionRailItem] = [
        _HomeActionRailItem(
            lane_id="recommend",
            lane_label="优先复核",
            tone="focus",
            button_label="去复盘",
            target_workspace="候选复盘",
            card=recommend_cards[0] if recommend_cards else None,
            summary=(
                recommend_cards[0].display_name if recommend_cards else "暂无优先复核项"
            ),
            lines=(
                _home_action_item_lines(recommend_cards[0])
                if recommend_cards
                else _home_recommend_fallback_lines(
                    task_view=task_view,
                    blocked_focus=blocked_focus,
                )
            ),
            visible=bool(recommend_cards or task_view.recommendation_lines),
        ),
        _HomeActionRailItem(
            lane_id="watch",
            lane_label="继续观察",
            tone="archive",
            button_label="归档回看",
            target_workspace="归档回看",
            card=watch_cards[0] if watch_cards else None,
            summary=(watch_cards[0].display_name if watch_cards else "暂无观察项"),
            lines=(
                _home_action_item_lines(watch_cards[0])
                if watch_cards
                else _home_watch_fallback_lines(
                    task_view=task_view,
                    blocked_focus=blocked_focus,
                )
            ),
            visible=bool(
                watch_cards or task_view.watchlist_lines or task_view.review_lines
            ),
        ),
        _HomeActionRailItem(
            lane_id="blocked",
            lane_label="核对卡点",
            tone="blocked",
            button_label="查看卡点",
            target_workspace="候选复盘",
            card=blocked_focus,
            summary=(
                blocked_focus.display_name
                if blocked_focus is not None
                else "暂无阻塞项"
            ),
            lines=(
                _home_action_item_lines(blocked_focus)
                if blocked_focus is not None
                else _singleton_lines(
                    task_view.blocker_lines[0]
                    if task_view.blocker_lines
                    else "当前路径相对顺畅，可按候选优先级推进。"
                )
            ),
            visible=bool(blocked_focus is not None or task_view.blocker_lines),
        ),
    ]
    if debates:
        debate_focus = sorted(
            debates,
            key=lambda item: (item.disagreement_score, item.adjusted_score),
            reverse=True,
        )[0]
        rail_items.insert(
            2,
            _HomeActionRailItem(
                lane_id="debate",
                lane_label="分歧复核",
                tone="pressure"
                if debate_focus.disagreement_score >= 0.35
                else "archive",
                button_label="候选复盘",
                target_workspace="候选复盘",
                card=_debate_as_candidate_card(debate_focus),
                summary=debate_focus.display_name,
                lines=_home_debate_item_lines(debate_focus),
                visible=True,
            ),
        )
    return tuple(rail_items)


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
            if item.card is not None and st.button(
                item.button_label,
                key=f"home-action-rail-{item.lane_id}-{item.card.symbol}",
                width="stretch",
            ):
                _queue_workspace_jump(item.target_workspace, item.card.symbol)
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
            if item.card is not None and st.button(
                item.button_label,
                key=f"home-action-rail-{item.lane_id}-{item.card.symbol}",
                width="stretch",
            ):
                _queue_workspace_jump(item.target_workspace, item.card.symbol)
                st.rerun()


def _render_lifecycle_overview(task_view) -> None:
    lifecycle_col, unlock_col = st.columns(2)
    with lifecycle_col:
        _render_line_block(
            "候选生命周期",
            task_view.lifecycle_lines,
            "当前日期暂无候选生命周期信息。",
        )
    with unlock_col:
        _render_line_block(
            "卡点提示",
            task_view.unlock_lines,
            "当前日期暂无额外卡点提示。",
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
            "当前日期暂无可对比的上一交易日。",
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
                覆盖任务 {overview.task_count} 个<br/>
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
            (
                paper_summary.action_summary_lines[0]
                if paper_summary.action_summary_lines
                else "当前纸面验证链路已有事件，先看入场假设和不可成交处理。"
            ),
            "pressure",
        )
    if _report_archive_status(task_view) != "无归档":
        return (
            "先去归档回看",
            (
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
            (
                paper_summary.action_summary_lines[0]
                if paper_summary.action_summary_lines
                else "当前有纸面持有假设，先核对防守位、观察目标与退出条件。"
            ),
            "focus",
        )
    return (
        "先去候选复盘",
        (
            task_view.review_lines[0]
            if task_view.review_lines
            else (
                task_view.recommendation_lines[0]
                if task_view.recommendation_lines
                else "当前仍以研究判断为主，先沿候选路径回看证据。"
            )
        ),
        "focus",
    )


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
    focus_body = "当前日期没有进入优先推进的候选，先看观察与阻塞链路。"
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
        st.subheader("阶段状态")
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
                        <div class="aqsp-day-status">{escape(row.status_label)}</div>
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
                if not is_active and st.button(
                    "打开阶段",
                    key=f"workflow-task-{row.task_id}-{row.signal_date}",
                    width="stretch",
                ):
                    _set_dashboard_selection(
                        task_id=row.task_id,
                        signal_date=row.signal_date,
                    )
                    st.rerun()
    hidden_rows = rows[3:]
    if hidden_rows:
        with st.expander(f"查看其余阶段 ({len(hidden_rows)})", expanded=False):
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
                            <div class="aqsp-day-status">{row.status_label}</div>
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
                    if st.button(
                        "打开阶段",
                        key=f"workflow-extra-task-{row.task_id}-{row.signal_date}",
                        width="stretch",
                    ):
                        _set_dashboard_selection(
                            task_id=row.task_id,
                            signal_date=row.signal_date,
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
          <div class="aqsp-status-sub">{escape(overview.archive_summary)}</div>
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
                f"动作 / 状态: {escape(_action_status_label(focus_card.action_label, focus_card.status_label))}",
                (
                    f"评分 {focus_card.score:.1f} / 复核 {escape(focus_card.review_meta)}"
                    if _has_review_meta(focus_card.review_meta)
                    else f"评分 {focus_card.score:.1f}"
                ),
                escape(
                    focus_card.next_step
                    or focus_card.decision_note
                    or "当前无额外推进动作。"
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
                paper_summary.action_summary_lines[0]
                if paper_summary.action_summary_lines
                else "先处理纸面假设与不可成交事件。"
            )
        )
    elif paper_summary.open_positions:
        pressure_lines.append("当前以纸面持有假设、防守位和观察目标复核为主。")
    else:
        pressure_lines.append("当前纸面侧较轻，可优先回到研究判断。")

    blocker_title = "无明显阻塞"
    blocker_body = "当前没有显著阻塞项，可按优先顺位推进。"
    if blocked_cards:
        blocker_title = blocked_cards[0].display_name
        blocker_body = "<br/>".join(
            [
                escape(_card_primary_blocker(blocked_cards[0]) or "存在待解除阻塞"),
                escape(_card_next_action(blocked_cards[0])),
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
                task_view.report_summary_lines[0]
                if task_view.report_summary_lines
                else (overview.archive_summary or "当前还没有结构化归档摘要。")
            ),
            escape(
                task_view.next_day_focus_lines[0]
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
    clean = sanitize_archive_text(re.sub(r"\*\*(.*?)\*\*", r"\1", line).strip())
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
        return f"🧪 下一步: 先核对纸面验证，{detail}"
    if overview.blocked_total:
        blocker = _sanitize_day_replay_line(
            overview.blocker_headline or "回到候选复盘核对卡点。"
        )
        return (
            "⚠️ 下一步: 先处理阻塞，"
            f"{blocker}"
        )
    if task_view.next_day_focus_lines:
        return (
            "📚 归档回看: 原报告下一交易日重点，"
            f"{_sanitize_day_replay_line(task_view.next_day_focus_lines[0])}"
        )
    if task_view.review_lines:
        return f"🧭 下一步: {_sanitize_day_replay_line(task_view.review_lines[0])}"
    return "🧭 下一步: 先看候选复盘，不为了凑单推进。"


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
        phase_text = f"{task_view.task_label} 当前视角"

    conclusion = _join_display_parts(
        "📍 当日结论",
        f"{overview.actionable_total} 待复核",
        f"{overview.watch_total} 观察",
        f"{overview.blocked_total} 阻塞",
    )
    workflow = _join_display_parts(
        "🧩 任务回放",
        phase_text,
        f"当前停在 {task_view.task_label}",
    )
    next_step = _day_replay_next_step_line(
        task_view=task_view,
        overview=overview,
        paper_summary=paper_summary,
    )
    archive = _join_display_parts(
        "🗂 全日覆盖",
        _report_archive_status(task_view),
        sanitize_archive_text(overview.archive_summary),
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
            f"研究入口: {task_view.task_label} · {_current_mode_label(task_view)}",
            f"回看日期: {review_date_label(task_view)}",
            (
                f"覆盖任务 {overview.task_count} / 待复核 {overview.actionable_total} / 观察 {overview.watch_total} / 阻塞 {overview.blocked_total}"
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
            f"复核节奏: {review_text or '按默认节奏复核'}",
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
        f"当前优先复核 {len(recommend_cards)} 只，先看 {recommend_cards[0].display_name}"
        if recommend_cards
        else "当前没有进入优先复核的候选，先看观察与阻塞队列。"
    )
    watch_summary = (
        f"观察池 {len(watch_cards)} 只，优先跟踪节奏和确认条件。"
        if watch_cards
        else "当前观察池较轻，复核重点转向推荐或阻塞处理。"
    )
    blocked_summary = (
        f"阻塞 {len(blocked_cards)} 只，先核对最上面的研究卡点。"
        if blocked_cards
        else "当前没有明显阻塞，纸面验证路径相对顺畅。"
    )

    recommend_col, watch_col, blocked_col = st.columns(3)
    with recommend_col:
        _render_priority_queue(
            title="优先复核队列",
            kicker="研究候选",
            summary=recommend_summary,
            cards=recommend_cards,
            empty_text="当前日期暂无优先复核候选。",
            tone="recommend",
        )
    with watch_col:
        _render_priority_queue(
            title="观察队列",
            kicker="继续观察",
            summary=watch_summary,
            cards=watch_cards,
            empty_text="当前日期暂无观察候选。",
            tone="watch",
        )
    with blocked_col:
        _render_priority_queue(
            title="阻塞队列",
            kicker="核对卡点",
            summary=blocked_summary,
            cards=blocked_cards,
            empty_text="当前日期暂无明显阻塞项。",
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
                        if st.button(
                            label,
                            key=f"home-{workspace}-{item.symbol}-{start}",
                            width="stretch",
                        ):
                            _queue_workspace_jump(workspace, item.symbol)
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


def _home_spotlight_lines(
    spotlight: DashboardCandidateSpotlight,
) -> tuple[str, ...]:
    return tuple(
        line
        for line in (
            f"覆盖任务: {'、'.join(spotlight.task_labels)}",
            f"联动状态: {_action_status_label(spotlight.action_label, spotlight.status_label)}",
            (
                f"汇总理由: {'；'.join(spotlight.reasons[:2])}"
                if spotlight.reasons
                else ""
            ),
            (f"汇总风险: {'；'.join(spotlight.risks[:2])}" if spotlight.risks else ""),
        )
        if line
    )


def _render_same_day_candidate_spotlights(
    spotlights: tuple[DashboardCandidateSpotlight, ...],
) -> None:
    st.subheader("同日全局候选总览")
    if not spotlights:
        st.info("当前日期暂无跨任务候选总览。")
        return

    for start in range(0, len(spotlights), 2):
        columns = st.columns(min(len(spotlights[start : start + 2]), 2))
        for column, item in zip(columns, spotlights[start : start + 2]):
            with column:
                summary_bits = [
                    f"来源任务: {'、'.join(item.task_labels)}",
                    f"动作 / 状态: {_action_status_label(item.action_label, item.status_label)}",
                ]
                if review_line := _review_meta_line("复核", item.review_meta):
                    summary_bits.append(review_line)
                emphasis = item.blocker or item.next_step or "继续跟踪"
                notes = [
                    f"### {item.display_name}",
                    f"- 评分: `{item.score:.1f}`",
                    f"- {' / '.join(summary_bits)}",
                    f"- 当前重点: {emphasis}",
                ]
                if item.reasons:
                    notes.append(f"- 理由: {'；'.join(item.reasons[:2])}")
                if item.risks:
                    notes.append(f"- 风险: {'；'.join(item.risks[:2])}")
                st.markdown("\n".join(notes))


def _render_date_timeline_cards(
    provider: DashboardDataProvider,
    selected_date: str,
    current_task_id: str,
) -> None:
    timeline_rows = provider.timeline_rows(limit=8)
    if not timeline_rows:
        return
    st.subheader("日期时间线")
    for start in range(0, len(timeline_rows), 4):
        columns = st.columns(min(len(timeline_rows[start : start + 4]), 4))
        for column, row in zip(columns, timeline_rows[start : start + 4]):
            is_active = row.signal_date == selected_date
            with column:
                st.markdown(
                    f"""
                    <div class="aqsp-date-card {"active" if is_active else ""}">
                      <div class="aqsp-date-title">{row.signal_date}</div>
                      <div class="aqsp-date-meta">覆盖任务: {"、".join(row.task_labels)}</div>
                      <div class="aqsp-date-summary">
                        待复核 {row.actionable_total} / 观察 {row.watch_total} / 阻塞 {row.blocked_total}<br/>
                        {row.headline}
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if st.button(
                    "查看日期",
                    key=f"timeline-date-{row.signal_date}",
                    width="stretch",
                ):
                    _set_dashboard_selection(
                        task_id=_resolve_task_for_date(
                            provider=provider,
                            current_task_id=current_task_id,
                            signal_date=row.signal_date,
                        ),
                        signal_date=row.signal_date,
                    )
                    st.rerun()


def _render_same_day_task_matrix(
    rows: tuple[DashboardSameDayTaskRow, ...],
    current_task_id: str,
) -> None:
    if not rows:
        return
    st.subheader("同日任务矩阵")
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
                        <div class="aqsp-day-status">{row.status_label}</div>
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
                if not is_active and st.button(
                    f"查看{row.task_label}",
                    key=f"same-day-task-{row.task_id}-{row.signal_date}",
                    width="stretch",
                ):
                    _set_dashboard_selection(
                        task_id=row.task_id,
                        signal_date=row.signal_date,
                    )
                    st.rerun()


def _set_dashboard_latest_task(task_id: str, *, signal_date: str = "最新") -> None:
    st.session_state["dashboard_pending_task_id"] = task_id
    st.session_state["dashboard_pending_selected_date"] = signal_date


def _render_task_workbench(
    snapshots: tuple[DashboardTaskSnapshot, ...],
    *,
    signal_date: str,
) -> None:
    st.subheader(f"同日任务状态快照 · {signal_date or '最新'}")
    st.caption("只看已落盘结果。")
    if not snapshots:
        st.info("当前暂无任务快照。")
        return

    active_snapshots = tuple(
        snapshot
        for snapshot in snapshots
        if snapshot.status_label not in {"该日无结果", "暂无结果"}
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
            if st.button(
                "打开任务",
                key=f"task-switch-{snapshot.task_id}",
                width="stretch",
            ):
                _set_dashboard_latest_task(
                    snapshot.task_id,
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
                    if st.button(
                        "打开任务",
                        key=f"task-switch-hidden-{snapshot.task_id}-{signal_date or 'latest'}",
                        width="stretch",
                    ):
                        _set_dashboard_latest_task(
                            snapshot.task_id,
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
            "当前暂无纸面验证摘要。",
        )
        _render_line_block(
            "纸面持有假设",
            summary.open_position_lines,
            "当前暂无纸面持有假设。",
        )
    with detail_col2:
        _render_line_block(
            "纸面关键事件",
            summary.event_lines,
            "当前暂无纸面关键事件。",
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
    holding_lines = summary.open_position_lines[:2] or ("当前暂无纸面持有假设。",)
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
        event_lines = ("当前暂无纸面关键事件。",)
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
                title="先回研究链核对",
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
            (
                paper_summary.action_summary_lines[0]
                if paper_summary.action_summary_lines
                else f"入场待核对 {paper_summary.pending_entries} / 不可成交 {paper_summary.not_executable}"
            ),
        )
    elif paper_summary.open_positions:
        paper_line = _join_display_parts(
            "🧪 先看纸面持有",
            (
                paper_summary.action_summary_lines[0]
                if paper_summary.action_summary_lines
                else f"当前纸面持有 {paper_summary.open_positions} 笔，先核对退出条件。"
            ),
        )
    else:
        paper_line = "🧪 纸面侧较轻: 当前没有新的纸面入场或不可成交事件。"

    if focus_card is not None:
        focus_line = _join_display_parts(
            "🧭 再看候选路径",
            focus_card.display_name,
            _action_status_label(focus_card.action_label, focus_card.status_label),
            _card_emphasis(focus_card),
        )
    elif debates:
        debate_focus = sorted(
            debates,
            key=lambda item: (item.disagreement_score, item.adjusted_score),
            reverse=True,
        )[0]
        focus_line = _join_display_parts(
            "🧭 再看分歧路径",
            debate_focus.display_name,
            f"{debate_focus.recommended_adjustment_label} / 分歧 {debate_focus.disagreement_score:.2f}",
        )
    else:
        focus_line = _join_display_parts(
            "🧭 再看研究路径",
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
            "📚 最后回看归档",
            task_view.next_day_focus_lines[0]
            if task_view.next_day_focus_lines
            else (
                task_view.report_summary_lines[0]
                if task_view.report_summary_lines
                else overview.archive_summary
            ),
        )
    else:
        close_line = "📚 归档待补: 当前先按研究证据走，等收盘复盘补齐历史记录。"

    return _unique_lines((paper_line, focus_line, close_line))


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
                f"下一步: {_card_next_action(focus_card)}",
            )
        )
    elif debates:
        debate_focus = sorted(
            debates,
            key=lambda item: (item.disagreement_score, item.adjusted_score),
            reverse=True,
        )[0]
        focus_title = debate_focus.display_name
        focus_lines = _unique_lines(
            (
                f"多 Agent: {debate_focus.recommended_adjustment_label} / 分歧 {debate_focus.disagreement_score:.2f}",
                (
                    f"共识: {debate_focus.consensus}"
                    if debate_focus.consensus
                    else "共识: 当前辩论只作解释，不替代评分。"
                ),
                (
                    f"待核对: {debate_focus.risk_warnings[0]}"
                    if debate_focus.risk_warnings
                    else "待核对: 回到候选证据链。"
                ),
            )
        )
    else:
        focus_title = "当前无显著主推候选"
        focus_lines = _unique_lines(
            (
                overview.focus_headline or overview.top_headline or task_view.headline,
                "动作: 先看观察池和任务摘要，不为了凑单推进。",
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
        paper_summary.action_summary_lines[:2],
    )[:3]

    blocker_title = "阻塞较轻"
    blocker_tone = "archive"
    if blocked_focus is not None:
        blocker_title = blocked_focus.display_name
        blocker_tone = "blocked"
        blocker_lines = _unique_lines(
            (
                f"卡点: {_card_primary_blocker(blocked_focus) or _card_emphasis(blocked_focus)}",
                f"动作: {_card_next_action(blocked_focus)}",
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
                "当前没有明显阻塞，先按焦点候选和纸面现实推进。",
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
            kicker="02 纸面现实",
            title=paper_title,
            lines=paper_lines or ("当前暂无纸面关键事件。",),
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
    st.markdown(
        "\n".join(
            [
                '<div class="aqsp-reading-card">',
                '<div class="aqsp-reading-title">Reading Order</div>',
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
    st.subheader("当日任务板")
    st.caption("先看阶段状态，再看动作优先级与纸面现实。")
    _render_daily_workflow(
        rows,
        current_task_id,
        overview,
        show_heading=False,
    )
    action_col, execution_col = st.columns((1.15, 0.85))
    with action_col:
        st.markdown("**动作优先级**")
        _render_home_action_rail(
            task_view,
            spotlights,
            debates,
            show_heading=False,
            layout="stack",
        )
    with execution_col:
        st.markdown("**纸面现实**")
        _render_home_execution_snapshot(
            paper_summary,
            task_view=task_view,
            overview=overview,
            show_heading=False,
        )


def _set_dashboard_selection(*, task_id: str, signal_date: str) -> None:
    st.session_state["dashboard_pending_task_id"] = task_id
    st.session_state["dashboard_pending_selected_date"] = signal_date


def _set_dashboard_workspace(workspace: str) -> None:
    st.session_state["dashboard_pending_workspace"] = workspace


def _workspace_handoff_payload(
    *,
    target_workspace: str,
    source_workspace: str,
    title: str,
    lines: tuple[str, ...],
) -> dict[str, str | tuple[str, ...]]:
    clean_title = title.strip()
    clean_lines = tuple(line.strip() for line in lines if line and line.strip())
    if not clean_title and not clean_lines:
        return {}
    return {
        "dashboard_pending_handoff_target": target_workspace,
        "dashboard_pending_handoff_source": source_workspace,
        "dashboard_pending_handoff_title": clean_title,
        "dashboard_pending_handoff_lines": clean_lines,
    }


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
) -> None:
    _queue_workspace_jump(target_workspace, symbol)
    for key, value in _workspace_handoff_payload(
        target_workspace=target_workspace,
        source_workspace=source_workspace,
        title=title,
        lines=lines,
    ).items():
        st.session_state[key] = value


def _consume_workspace_handoff(
    target_workspace: str,
) -> tuple[str, str, tuple[str, ...]] | None:
    pending_target = st.session_state.get("dashboard_pending_handoff_target")
    if pending_target != target_workspace:
        return None
    source_workspace = str(
        st.session_state.pop("dashboard_pending_handoff_source", "") or ""
    )
    title = str(st.session_state.pop("dashboard_pending_handoff_title", "") or "")
    raw_lines = st.session_state.pop("dashboard_pending_handoff_lines", ())
    st.session_state.pop("dashboard_pending_handoff_target", None)
    lines = tuple(str(line).strip() for line in raw_lines if str(line).strip())
    return (source_workspace, title, lines)


def _render_workspace_handoff_notice(
    *,
    target_workspace: str,
) -> None:
    handoff = _consume_workspace_handoff(target_workspace)
    if handoff is None:
        return
    source_workspace, title, lines = handoff
    kicker = (
        f"{source_workspace} -> {target_workspace}"
        if source_workspace
        else target_workspace
    )
    _render_cockpit_card(
        kicker=kicker,
        title=title or "沿上一个工作区继续回放",
        lines=lines or ("当前没有额外交接说明。",),
        tone="archive",
    )


def _review_to_archive_handoff_lines(
    *,
    selected_card: DashboardCandidateCard,
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    return tuple(
        line
        for line in (
            f"当前标的: {selected_card.display_name}",
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
    lead_line = archive_lines[0] if archive_lines else "回到研究证据链核对当前判断。"
    return tuple(
        line
        for line in (
            f"当前标的: {selected_symbol}",
            f"归档状态: {_report_archive_status(task_view)}",
            f"回到复盘先看: {lead_line}",
        )
        if line
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


def _render_workspace_navigation() -> str:
    workspace_options = ["决策首页", "候选复盘", "虚拟盘跟踪", "归档回看"]
    pending_workspace = st.session_state.pop("dashboard_pending_workspace", None)
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
    workspace = st.radio(
        "工作区",
        workspace_options,
        horizontal=True,
        label_visibility="collapsed",
        key=widget_key,
    )
    return workspace


def _render_date_jump_bar(
    *,
    all_dates: tuple[str, ...],
    selected_date: str,
    provider: DashboardDataProvider,
    current_task_id: str,
) -> None:
    if not all_dates:
        return
    st.markdown(
        '<div class="aqsp-nav-section-title">日期</div>', unsafe_allow_html=True
    )
    visible_dates = all_dates[:7]
    columns = st.columns(len(visible_dates))
    for column, signal_date in zip(columns, visible_dates):
        with column:
            is_active = signal_date == selected_date
            if st.button(
                signal_date,
                key=f"date-jump-{signal_date}",
                width="stretch",
                type="primary" if is_active else "secondary",
            ):
                _set_dashboard_selection(
                    task_id=_resolve_task_for_date(
                        provider=provider,
                        current_task_id=current_task_id,
                        signal_date=signal_date,
                    ),
                    signal_date=signal_date,
                )
                st.rerun()
            st.markdown(
                (
                    f'<div class="aqsp-nav-secondary{" active" if is_active else ""}">'
                    f"{escape(_date_jump_secondary_label(provider, current_task_id, signal_date))}"
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
    same_day_debates = provider.debate_summaries(signal_date)
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
    nav_col1, nav_col2, nav_col3 = st.columns(3)
    with nav_col1:
        if st.button(
            "复盘",
            key=f"execution-to-review-{selected_symbol}-{task_view.task_id}-{signal_date}",
            width="stretch",
            disabled=review_card is None,
        ):
            _queue_workspace_jump("候选复盘", selected_symbol)
            st.rerun()
    with nav_col2:
        if st.button(
            "归档",
            key=f"execution-to-archive-{selected_symbol}-{task_view.task_id}-{signal_date}",
            width="stretch",
        ):
            _queue_workspace_jump("归档回看", selected_symbol)
            st.rerun()
    with nav_col3:
        if st.button(
            "首页",
            key=f"execution-to-home-{selected_symbol}-{task_view.task_id}-{signal_date}",
            width="stretch",
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
            kicker="纸面验证路径",
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
            or ("当前没有新增路径提示，先看纸面入场、纸面事件与日志。",),
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
            kicker="纸面现实",
            title="当前尚未进入纸面验证链",
            lines=_unique_lines(
                execution_focus.execution_lines[:2],
                execution_focus.holding_lines[:2],
                (
                    "当前没有纸面入场、纸面日志或纸面持有记录，先按研究判断与复核条件推进。",
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
                empty_text="当前标的没有同日多 Agent 辩论记录，纸面验证暂以研究与虚拟盘链路为主。",
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
    return f"{row.phase_label} · {row.task_label} · {row.status_label}"


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
        action_label, watch_label, blocked_label = _task_metric_labels(
            selected_row.task_id
        )
        return (
            f"{selected_date} · {selected_row.phase_label}",
            (
                f"{selected_row.task_label} / {selected_row.status_label}",
                (
                    f"队列: {action_label} {selected_row.actionable_count} / "
                    f"{watch_label} {selected_row.watch_count} / "
                    f"{blocked_label} {selected_row.blocked_count}"
                ),
                f"焦点: {selected_row.headline}",
            ),
        )

    snapshot_map = {snapshot.task_id: snapshot for snapshot in snapshots}
    selected_snapshot = snapshot_map.get(selected_task_id)
    if selected_snapshot is not None:
        action_label, watch_label, blocked_label = _task_metric_labels(
            selected_snapshot.task_id
        )
        return (
            f"{selected_date or '最新'} · {selected_snapshot.task_label}",
            (
                f"{selected_snapshot.task_label} / {selected_snapshot.status_label}",
                (
                    f"队列: {action_label} {selected_snapshot.actionable_count} / "
                    f"{watch_label} {selected_snapshot.watch_count} / "
                    f"{blocked_label} {selected_snapshot.blocked_count}"
                ),
                f"焦点: {selected_snapshot.headline}",
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
            if st.button(
                row.phase_label,
                key=f"phase-jump-{row.task_id}-{signal_date}",
                width="stretch",
                type="primary" if is_active else "secondary",
            ):
                _set_dashboard_selection(task_id=row.task_id, signal_date=signal_date)
                st.rerun()
            st.markdown(
                f'<div class="aqsp-nav-secondary{" active" if is_active else ""}">{escape(row.task_label)}</div>',
                unsafe_allow_html=True,
            )


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
          <div class="aqsp-banner-title">导航</div>
          <div class="aqsp-banner-main">{escape(title)}</div>
          <div class="aqsp-banner-meta">{"<br/>".join(escape(line) for line in lines if line.strip())}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_top_navigation(
    *,
    options: tuple,
    snapshots: tuple[DashboardTaskSnapshot, ...],
    provider: DashboardDataProvider,
) -> tuple[str, str]:
    st.markdown(
        '<div class="aqsp-nav-note">先选日期，再切阶段。</div>',
        unsafe_allow_html=True,
    )
    pending_date = st.session_state.pop("dashboard_pending_selected_date", None)
    pending_task_id = st.session_state.pop("dashboard_pending_task_id", None)
    if pending_date is not None:
        st.session_state["dashboard_selected_date"] = pending_date
        st.session_state["dashboard_selected_date_select"] = pending_date
        st.session_state["dashboard_selected_date_more"] = pending_date
    if pending_task_id is not None:
        st.session_state["dashboard_task_id"] = pending_task_id
        st.session_state["dashboard_task_id_select"] = pending_task_id

    all_dates = provider.dashboard_dates()
    if not all_dates:
        task_ids = [option.task_id for option in options]
        selected_task_id = st.selectbox(
            "任务导航",
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
    date_col, task_col = st.columns([1.15, 1.35])
    with date_col:
        selected_date_label = st.selectbox(
            "回看日期",
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

    row_map = {row.task_id: row for row in same_day_rows}
    with task_col:
        selected_task_id = st.selectbox(
            "阶段导航",
            available_task_ids,
            format_func=lambda task_id: (
                _phase_nav_label(row_map[task_id])
                if task_id in row_map
                else _task_nav_label(task_id, snapshots)
            ),
            key="dashboard_task_id_select",
        )
    st.session_state["dashboard_task_id"] = selected_task_id
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
                f"动作 / 状态: {_action_status_label(selected_card.action_label, selected_card.status_label)}",
                _candidate_score_context_line(selected_card),
                (
                    f"下一步: {_card_next_action(selected_card)}"
                    if _card_next_action(selected_card)
                    else ""
                ),
                _review_meta_line("复核节奏", selected_card.review_meta),
                (
                    f"当前阻塞: {_card_primary_blocker(selected_card)}"
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
                f"来源任务: {'、'.join(selected_spotlight.task_labels)}",
                f"动作 / 状态: {_action_status_label(selected_spotlight.action_label, selected_spotlight.status_label)}",
                (
                    f"当前重点: {selected_spotlight.blocker or selected_spotlight.next_step}"
                    if selected_spotlight.blocker or selected_spotlight.next_step
                    else ""
                ),
                _review_meta_line("统一复核", selected_spotlight.review_meta),
            )
            if line
        )
    return tuple(line for line in execution_focus.research_lines[:3] if line)


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
            "动作 / 状态: 待独立验证 / 等待下一次任务确认"
            if is_debate_review
            else f"动作 / 状态: {_action_status_label(review_card.action_label, review_card.status_label)}"
        )
        follow_up_line = (
            "验证动作: 等待下一次任务或纸面验证链路补充独立证据。"
            if is_debate_review
            else (
                f"下一步: {review_card.next_step or review_card.decision_note}"
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
                    _review_meta_line("复核节奏", review_card.review_meta),
                    (
                        f"当前阻塞: {_card_primary_blocker(review_card)}"
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
        return "辩论主结论已补齐"
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
            f"研究状态: {research_status}",
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
        return "仅纸面记录"
    if review_card.rank_label == "辩论主结论":
        return "辩论主结论"
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
            "仅纸面记录",
            ("当前没有研究或辩论上下文。", "先看纸面持有假设、纸面事件和日志。"),
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
            ("已经进入纸面入场/阻塞/关闭链路。", "先顺着纸面事件与日志回看。"),
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
            "辩论主结论回看",
            ("当前判断主要由辩论主结论补齐。", "先看辩论共识、修正原因和风险分歧。"),
            "focus",
        )
    if selected_card is None:
        return (
            "跨任务联动回看",
            ("当前判断来自同日联动聚合。", "先核对跨任务结论，再回到单任务证据。"),
            "archive",
        )
    return (
        "研究判断回看",
        ("当前以研究候选卡为主。", "先看路径、复核节奏和入场条件。"),
        "archive",
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
        debate_focus = sorted(
            debates,
            key=lambda item: (item.disagreement_score, item.adjusted_score),
            reverse=True,
        )[0]
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
            if st.button(
                code_label,
                key=f"{workspace}-quick-symbol-{symbol}",
                width="stretch",
                type="primary" if is_active else "secondary",
            ):
                _queue_workspace_jump(workspace, symbol)
                st.rerun()
            st.markdown(
                f'<div class="aqsp-quick-symbol-name{" active" if is_active else ""}">{escape(name_label)}</div>',
                unsafe_allow_html=True,
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
            if st.button(
                row.phase_label,
                key=f"review-phase-{selected_symbol}-{row.task_id}-{signal_date}",
                width="stretch",
                type="primary" if is_active else "secondary",
            ):
                _set_dashboard_selection(
                    task_id=row.task_id,
                    signal_date=signal_date,
                )
                _queue_workspace_jump("候选复盘", selected_symbol)
                st.rerun()
            st.markdown(
                f'<div class="aqsp-nav-secondary{" active" if is_active else ""}">{escape(row.task_label)}</div>',
                unsafe_allow_html=True,
            )


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
            _debate_primary_takeaways(debate_summary)[:2],
            ("当前没有研究候选卡，研究链路已由辩论主结论补齐。",),
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
                    _review_meta_line("复核节奏", review_card.review_meta)
                    if review_card is not None
                    else ""
                ),
                (f"当前阻塞: {blocker}" if include_blocker else ""),
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
            or ("当前还没有结构化候选结论，先按纸面记录与证据回看。",),
            tone="focus" if review_card is not None else "archive",
        )
    with reality_col:
        _render_cockpit_card(
            kicker="纸面现实",
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
    same_day_rows = provider.same_day_task_rows(signal_date)
    if any(row.task_id == current_task_id for row in same_day_rows):
        return current_task_id
    return provider.preferred_task_for_date(signal_date)


def _date_jump_secondary_label(
    provider: DashboardDataProvider,
    current_task_id: str,
    signal_date: str,
) -> str:
    target_task_id = _resolve_task_for_date(
        provider=provider,
        current_task_id=current_task_id,
        signal_date=signal_date,
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
    for prefix in ("研究动作:", "研究下一步:", "当前卡点:", "复核节奏:"):
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
                    f"- 动作 / 状态: {_action_status_label(card.action_label, card.status_label)}",
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
            st.info("该标的当前只在本任务中出现，没有额外同日联动上下文。")
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
                st.warning("该标的在多个定时任务中重复出现，适合做日内链路复核。")


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
        context_lines = tuple(
            line
            for line in (
                (
                    f"监控焦点: {_card_primary_blocker(review_card)}"
                    if _card_primary_blocker(review_card)
                    else "监控焦点: 当前只在本任务中出现，优先等待下一次任务验证。"
                ),
                "验证动作: 等待下一次任务或纸面验证链路补充独立证据。",
            )
            if line
        )
    else:
        context_lines = tuple(
            line
            for line in (
                f"研究入口: {_review_source_label(review_card)}",
                f"动作 / 状态: {_action_status_label(review_card.action_label, review_card.status_label)}",
                (
                    f"复核节奏: {review_card.review_meta}"
                    if _has_review_meta(review_card.review_meta)
                    else ""
                ),
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
                    f"重点关注: {spotlight.blocker or spotlight.next_step}"
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
    st.subheader("研究证据链")
    research_col, context_col = st.columns(2)
    with research_col:
        has_task_evidence = not signal_frame.empty or not task_frame.empty
        if not signal_frame.empty:
            _render_frame(evidence_title, signal_frame)
        elif debate_summary is not None and spotlight is None:
            _render_cockpit_card(
                kicker="辩论补齐证据",
                title="当前没有独立任务信号表",
                lines=_unique_lines(
                    ("结论来源: 同日多 Agent 辩论补齐",),
                    _debate_agent_focus_lines(debate_summary),
                    (_debate_evidence_composition_line(debate_summary),),
                ),
                tone="archive",
            )
        if not task_frame.empty:
            _render_frame("任务明细", task_frame)
        if spotlight is not None:
            st.markdown(
                "\n".join(
                    [
                        "#### 同日全局视角",
                        f"- 任务覆盖: {'、'.join(spotlight.task_labels)}",
                        f"- 汇总理由: {'；'.join(spotlight.reasons[:3]) if spotlight.reasons else '-'}",
                        f"- 汇总风险: {'；'.join(spotlight.risks[:3]) if spotlight.risks else '-'}",
                    ]
                )
            )
        elif not has_task_evidence and debate_summary is None:
            st.info("该标的当前只在本任务中出现，研究链以本任务落盘结果为主。")
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
                    else "联动上下文"
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
                    else "联动上下文"
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
    duplicate_hints = {
        primary
        for primary in (
            _card_primary_blocker(card),
            card.next_step.strip(),
        )
        if primary
    }
    lines = [
        f"来源任务: {'、'.join(spotlight.task_labels)}",
        f"跨任务结论: {_action_status_label(spotlight.action_label, spotlight.status_label)}",
    ]
    if focus_detail not in duplicate_hints:
        lines.append(f"重点关注: {focus_detail}")
    if review_line := _review_meta_line("统一复核", spotlight.review_meta):
        lines.append(review_line)
    return tuple(_unique_lines(tuple(lines)))


def _candidate_review_path_lines(
    *,
    selected_card: DashboardCandidateCard,
    spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
) -> tuple[str, ...]:
    return tuple(
        line
        for line in (
            *_debate_vote_snapshot_lines(debate_summary),
            (
                f"修正原因: {debate_summary.adjustment_reason}"
                if debate_summary is not None and debate_summary.adjustment_reason
                else ""
            ),
            (
                f"来源任务: {'、'.join(spotlight.task_labels)}"
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

    st.subheader("当日候选路径")
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
                        f"- 动作 / 状态: {_action_status_label(step.action_label, step.status_label)}",
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
        return (
            "该标的在当前回看日没有独立候选路径，当前判断主要由同日多 Agent 讨论补齐。"
        )
    if (
        spotlight is not None
        and review_card is not None
        and review_card.rank_label == "同日联动"
    ):
        return "该标的在当前回看日没有独立候选路径，当前判断主要来自同日联动聚合。"
    return "该标的在当前回看日只有单任务记录，暂无跨阶段路径。"


def _candidate_linkage_context(
    *,
    spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
    task_summary: str,
) -> tuple[str, tuple[str, ...], str]:
    if spotlight is not None and len(spotlight.task_labels) > 1:
        return (
            "跨任务视角",
            tuple(
                line
                for line in (
                    f"任务覆盖: {task_summary}",
                    (
                        f"汇总理由: {'；'.join(spotlight.reasons[:2])}"
                        if spotlight.reasons
                        else ""
                    ),
                    (
                        f"汇总风险: {'；'.join(spotlight.risks[:2])}"
                        if spotlight.risks
                        else ""
                    ),
                )
                if line
            )
            or ("当前已进入同日联动，但暂未提炼出更多结构化结论。",),
            "archive",
        )
    if spotlight is not None:
        return (
            "单任务证据",
            tuple(
                line
                for line in (
                    f"任务覆盖: {task_summary}",
                    (
                        f"同日摘要: {'；'.join(spotlight.reasons[:2])}"
                        if spotlight.reasons
                        else ""
                    ),
                    (
                        f"主要风险: {'；'.join(spotlight.risks[:2])}"
                        if spotlight.risks
                        else ""
                    ),
                )
                if line
            )
            or (
                f"任务覆盖: {task_summary}",
                "当前只在本任务中出现，没有额外同日联动上下文。",
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
                f"当前覆盖: {task_summary}",
            )
            if line
        )
        return (
            "风险与机会",
            debate_lines
            or (
                "当前判断来自多 Agent 辩论补齐。",
                f"当前覆盖: {task_summary}",
            ),
            "archive",
        )
    return (
        "单任务证据",
        (
            f"任务覆盖: {task_summary}",
            "当前只在本任务中出现，没有额外同日联动上下文。",
        ),
        "archive",
    )


def _render_candidate_review_snapshot(
    selected_card: DashboardCandidateCard,
    spotlight: DashboardCandidateSpotlight | None,
    debate_summary: DashboardDebateSummary | None,
    journey_steps: tuple[DashboardCandidateJourneyStep, ...],
    paper_frame,
    execution_frame,
) -> None:
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
        f"虚拟盘事件 {len(paper_frame.index)} / 纸面日志 {len(execution_frame.index)}"
        if not paper_frame.empty or not execution_frame.empty
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
    has_execution_activity = not paper_frame.empty or not execution_frame.empty
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
        "覆盖任务", len(spotlight.task_labels) if spotlight is not None else 1
    )
    metric_col3.metric("虚拟盘事件", len(paper_frame.index))
    metric_col4.metric("纸面日志", len(execution_frame.index))

    _render_debate_brief(debate_summary)

    summary_col, status_col = st.columns(2)
    with summary_col:
        st.markdown(
            f'<div class="aqsp-review-symbol">{escape(selected_card.display_name)}</div>',
            unsafe_allow_html=True,
        )
        if debate_compact_mode:
            summary_lines = [
                f"- 结论: {debate_summary.recommended_adjustment_label} / 分歧 {debate_summary.disagreement_score:.2f}",
                "- 来源: 同日多 Agent 辩论主结论",
                "- 性质: 辩论调整分，非选股评分，待独立验证",
            ]
        else:
            review_source = (
                "辩论主结论"
                if selected_card.rank_label == "辩论主结论"
                else (
                    "同日联动"
                    if selected_card.rank_label == "同日联动"
                    else "研究候选卡"
                )
            )
            summary_lines = [
                f"- 研究入口: {review_source}",
                (
                    f"- 当日路径: {path_summary}"
                    if has_expanded_path
                    else "- 当日路径: 单任务单阶段"
                ),
                f"- 纸面联动: {execution_summary}",
            ]
        st.markdown("\n".join(summary_lines))
    with status_col:
        _render_cockpit_card(
            kicker="证据落点",
            title=linkage_title,
            lines=linkage_lines,
            tone=linkage_tone,
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
            kicker="研究判断",
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
                kicker="同日路径与辩论",
                title="分歧地图" if debate_summary is not None else path_summary,
                lines=path_lines,
                tone="archive",
            )

    next_step_lines = _candidate_next_step_lines(selected_card)
    if debate_compact_mode:
        debate_action_lines = tuple(
            line
            for line in (
                "验证动作: 等待下一次任务或纸面验证链路补充独立证据。",
                _review_meta_line("复核节奏", selected_card.review_meta),
                (
                    f"当前卡点: {_card_primary_blocker(selected_card)}"
                    if has_blocker
                    else ""
                ),
                "当前尚未进入纸面动作，优先等待下一次任务验证。",
            )
            if line
        )
        _render_cockpit_card(
            kicker="当前行动",
            title="先做独立验证",
            lines=debate_action_lines,
            tone="blocked" if has_blocker else "archive",
        )
    elif compact_mode:
        _render_cockpit_card(
            kicker="推进计划",
            title=_candidate_action_plan_title(selected_card),
            lines=next_step_lines,
            tone="pressure" if has_blocker else "archive",
        )
    else:
        execution_col, linkage_col = st.columns(2)
        with execution_col:
            _render_cockpit_card(
                kicker="虚拟盘联动",
                title="纸面现实",
                lines=(execution_summary,),
                tone="pressure" if has_execution_activity else "archive",
            )
        with linkage_col:
            _render_cockpit_card(
                kicker="下一步",
                title=_candidate_action_plan_title(selected_card, default="怎么推进"),
                lines=next_step_lines,
                tone="pressure" if has_blocker else "archive",
            )

    nav_col1, nav_col2, nav_col3 = st.columns(3)
    with nav_col1:
        if st.button(
            "虚拟盘",
            key=f"review-to-execution-{selected_card.symbol}",
            width="stretch",
        ):
            _queue_workspace_jump("虚拟盘跟踪", selected_card.symbol)
            st.rerun()
    with nav_col2:
        if st.button(
            "归档",
            key=f"review-to-report-{selected_card.symbol}",
            width="stretch",
        ):
            _queue_workspace_handoff(
                target_workspace="归档回看",
                source_workspace="候选复盘",
                symbol=selected_card.symbol,
                title="带着当前判断去看归档",
                lines=_review_to_archive_handoff_lines(
                    selected_card=selected_card,
                    debate_summary=debate_summary,
                ),
            )
            st.rerun()
    with nav_col3:
        if st.button(
            "首页",
            key=f"review-to-home-{selected_card.symbol}",
            width="stretch",
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
    same_day_debates = provider.debate_summaries(signal_date)
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
    )
    selected_card, spotlight, debate_summary, review_card = _review_context_for_symbol(
        symbol=selected_symbol,
        cards=review_cards,
        spotlights=same_day_spotlights,
        debates=same_day_debates,
    )
    if review_card is None:
        st.info("当前标的缺少可回看的研究与同日联动上下文。")
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

    _render_candidate_review_snapshot(
        review_card,
        spotlight,
        debate_summary,
        journey_steps,
        paper_frame,
        execution_frame,
    )
    _render_candidate_journey(
        journey_steps,
        review_card=review_card,
        spotlight=spotlight,
        debate_summary=debate_summary,
    )
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
        }.get(source, "上下文补齐")
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
    debate_lines = (
        _debate_primary_takeaways(debate_summary)[1:3]
        if compact_mode
        else _debate_primary_takeaways(debate_summary)[:2]
    )
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
            f"当前卡点: {blocker}",
            f"复核动作: {next_action}" if next_action != "-" else "",
            _review_meta_line("复核节奏", selected_card.review_meta),
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
            _review_meta_line("复核节奏", selected_card.review_meta),
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
        task_view.agenda_lines[:2],
        task_view.review_lines[:2],
        task_view.unlock_lines[:2],
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
                    f"优先处理阻塞: {_card_primary_blocker(action_card)}"
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
            (f"研究下一步: {action_card.next_step}" if action_card.next_step else ""),
            (
                f"优先处理阻塞: {_card_primary_blocker(action_card)}"
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
        ("当前归档没有新增复盘动作，先看原文、研究链与纸面现实。",),
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
                "纸面现实: 当日有纸面事件或执行日志，优先核对真实落盘链。"
                if has_execution_activity
                else ""
            ),
            (
                "持有假设: 存在信号日绑定纸面持有，回看退出条件。"
                if has_holding_activity
                else ""
            ),
            (
                "纸面现实: 当前没有绑定纸面事件，归档侧先看研究结论和复核动作。"
                if not has_execution_activity and not has_holding_activity
                else ""
            ),
        )
        if line
    )
    debate_lines = _archive_debate_evidence_lines(debate_summary)[:3]
    return (
        _ArchiveBriefCard(
            kicker="归档结论",
            title=conclusion_title,
            lines=archive_lines[:3]
            or ("当前归档没有结构化结论，先回看原文和同日任务。",),
            tone="archive",
        ),
        _ArchiveBriefCard(
            kicker="复核动作",
            title=action_title,
            lines=action_lines,
            tone="pressure" if getattr(task_view, "blocker_lines", ()) else "focus",
        ),
        _ArchiveBriefCard(
            kicker="纸面现实",
            title="有纸面联动"
            if has_execution_activity or has_holding_activity
            else "纸面侧较轻",
            lines=reality_lines,
            tone="pressure" if has_execution_activity else "archive",
        ),
        _ArchiveBriefCard(
            kicker="辩论证据",
            title=(
                f"{debate_summary.recommended_adjustment_label} / 分歧 {debate_summary.disagreement_score:.2f}"
                if debate_summary is not None
                else "当日未触发"
            ),
            lines=debate_lines
            or ("当前标的没有同日多 Agent 讨论归档，先按研究结论与纸面现实复盘。",),
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
                kicker="后续动作",
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
            kicker="后续动作",
            title=action_title,
            lines=resolved_action_lines,
            tone="pressure" if task_view.blocker_lines else "focus",
        )
    with debate_col:
        _render_cockpit_card(
            kicker="分歧证据",
            title=(
                f"{debate_summary.recommended_adjustment_label} / 分歧 {debate_summary.disagreement_score:.2f}"
                if debate_summary is not None
                else "当日未触发"
            ),
            lines=_archive_debate_evidence_lines(debate_summary)
            or ("当前标的没有同日多 Agent 讨论归档，先按研究结论与纸面现实复盘。",),
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
    same_day_debates = provider.debate_summaries(review_date)
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
            lines=tuple(
                line
                for line in (
                    *task_view.report_summary_lines[:2],
                    *task_view.next_day_focus_lines[:2],
                    *task_view.runtime_lines[:2],
                )
                if line
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
        if st.button(
            "首页",
            key=f"archive-to-home-{task_view.task_id}-{review_date}",
            width="stretch",
        ):
            _set_dashboard_workspace("决策首页")
            st.rerun()
    with nav_col2:
        if st.button(
            "复盘",
            key=f"archive-to-review-{selected_symbol}-{task_view.task_id}-{review_date}",
            width="stretch",
            disabled=review_card is None,
        ):
            _queue_workspace_handoff(
                target_workspace="候选复盘",
                source_workspace="归档回看",
                symbol=selected_symbol,
                title="带着归档结论回到研究链",
                lines=_archive_to_review_handoff_lines(
                    task_view=task_view,
                    selected_symbol=selected_symbol,
                    selected_card=selected_card,
                    review_card=review_card,
                ),
            )
            st.rerun()
    with nav_col3:
        if st.button(
            "虚拟盘",
            key=f"archive-to-execution-{selected_symbol}-{task_view.task_id}-{review_date}",
            width="stretch",
        ):
            _queue_workspace_jump("虚拟盘跟踪", selected_symbol)
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
                st.markdown(
                    "\n".join(
                        [
                            f"### {row.task_label}",
                            f"- 阶段: {row.phase_label}",
                            f"- 归档状态: {status}",
                            f"- 回看结论: {view.headline}",
                            (
                                f"- 摘要: {view.report_summary_lines[0]}"
                                if view.report_summary_lines
                                else "- 摘要: 当前无结构化摘要"
                            ),
                            (
                                f"- 明日重点: {view.next_day_focus_lines[0]}"
                                if view.next_day_focus_lines
                                else "- 明日重点: -"
                            ),
                        ]
                    )
                )
                if row.task_id != current_task_id and st.button(
                    "打开归档",
                    key=f"report-center-{row.task_id}-{review_date}",
                    width="stretch",
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
        "以下内容只用于回看当时研究语境，不是今日动作、不是下单指令。",
        "原文中的行动词已在展示层中性化为纸面复核口径，原始文件未被改写。",
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
    provider = get_provider()
    summary = provider.summarize()
    latest_task_snapshots = provider.task_snapshots()
    updated_at = now_shanghai().strftime("%Y-%m-%d %H:%M:%S %z")
    _inject_dashboard_styles()

    st.title("AQSP 日期任务研究台")
    st.caption(f"更新时间: {updated_at}")
    st.caption("非交易指令 / 不下单 / 只做纸面观察与复核。仅展示落盘结果。")

    options = provider.task_options()
    selected_task_id, selected_date = _render_top_navigation(
        options=options,
        snapshots=latest_task_snapshots,
        provider=provider,
    )

    task_view = provider.build_task_view(
        selected_task_id,
        signal_date=selected_date,
    )
    review_date = task_view.selected_date or task_view.latest_date
    same_day_rows = provider.same_day_task_rows(review_date)
    same_day_spotlights = provider.same_day_candidate_spotlights(review_date)
    same_day_debates = provider.debate_summaries(review_date)
    date_overview = provider.date_overview(review_date)
    task_snapshots = provider.task_snapshots(review_date)
    paper_summary = provider.paper_summary(review_date)
    research_summary = load_research_summary()

    _render_top_overview_strip(
        review_date=review_date,
        date_overview=date_overview,
        summary=summary,
    )

    st.markdown('<div class="aqsp-workspace-shell">', unsafe_allow_html=True)
    st.markdown(
        '<div class="aqsp-workspace-label">工作台视角</div>', unsafe_allow_html=True
    )
    workspace = _render_workspace_navigation()
    st.markdown("</div>", unsafe_allow_html=True)
    with st.expander("当日快照", expanded=False):
        _render_task_workbench(task_snapshots, signal_date=review_date)

    if workspace == "决策首页":
        _render_day_replay_digest(
            task_view=task_view,
            overview=date_overview,
            paper_summary=paper_summary,
            same_day_rows=same_day_rows,
        )
        _render_home_brief(
            task_view=task_view,
            overview=date_overview,
            paper_summary=paper_summary,
            research_summary=research_summary,
            spotlights=same_day_spotlights,
            debates=same_day_debates,
        )
        with st.expander("展开推进细节", expanded=False):
            _render_command_center(task_view, date_overview, task_view.summary_lines)
            _render_home_reading_order(
                task_view=task_view,
                overview=date_overview,
                paper_summary=paper_summary,
                spotlights=same_day_spotlights,
                debates=same_day_debates,
            )
        _render_research_radar(research_summary)
        _render_home_task_board(
            rows=same_day_rows,
            current_task_id=task_view.task_id,
            task_view=task_view,
            spotlights=same_day_spotlights,
            debates=same_day_debates,
            paper_summary=paper_summary,
            overview=date_overview,
        )
        _render_decision_flow(task_view, same_day_spotlights)

        with st.expander("日期时间线", expanded=False):
            _render_date_timeline_cards(
                provider,
                review_date,
                task_view.task_id,
            )

        with st.expander("任务矩阵", expanded=False):
            _render_same_day_task_matrix(same_day_rows, task_view.task_id)

        with st.expander("证据与历史", expanded=False):
            _render_operation_overview(date_overview)
            _render_day_archive_summary(date_overview)
            st.subheader("研究摘要")
            st.info(task_view.headline)

            show_summary_lines = task_view.summary_lines
            if task_view.task_id == "briefing":
                show_summary_lines = tuple(
                    line
                    for line in task_view.summary_lines
                    if "任务:" not in line and "数据源:" not in line
                )

            summary_col, agenda_col = st.columns(2)
            with summary_col:
                _render_summary_cards(task_view)
                _render_focus_block(task_view)
                _render_lifecycle_overview(task_view)
            with agenda_col:
                _render_line_block(
                    "今日待办",
                    task_view.agenda_lines,
                    "当前暂无明确待办。",
                )
                _render_line_block(
                    "原始摘要",
                    show_summary_lines,
                    "当前暂无原始摘要。",
                )
                _render_line_block(
                    "阻塞原因",
                    task_view.blocker_lines,
                    "当前日期暂无明显阻塞项。",
                )
                _render_line_block(
                    "复核动作",
                    task_view.review_lines,
                    "当前日期暂无额外复核动作。",
                )

            _render_same_day_candidate_spotlights(same_day_spotlights)
            _render_history_overview(task_view, provider)
            _render_timeline_overview(task_view, provider)

        st.divider()
        _render_source_status(task_view.source_status)
    elif workspace == "候选复盘":
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
        with st.expander("执行总览", expanded=False):
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
