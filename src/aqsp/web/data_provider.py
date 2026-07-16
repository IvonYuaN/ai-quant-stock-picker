"""仪表盘数据工具 - 基于真实落盘数据构建任务导航视图。"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from aqsp.audit.trade_logger import TradeLogger
from aqsp.briefing.closing_review import ClosingReviewer
from aqsp.core.time import SHANGHAI_TZ, now_shanghai, today_shanghai, to_iso8601
from aqsp.ledger.base import read_ledger
from aqsp.ledger.runtime import cold_start_min_days
from aqsp.market_context import cross_market_rule_runtime_summary
from aqsp.paper import read_paper_trades
from aqsp.presentation import (
    format_review_meta,
    format_symbol_name,
    humanize_runtime_snapshot_line,
    normalize_research_tone,
)
from aqsp.ratings import is_tradable_rating, portfolio_action_label, rating_label
from aqsp.strategies.thresholds import load_thresholds
from aqsp.strategy import rating_for_score
from aqsp.data.source_readiness import source_supports_workload, workload_fit_for_source
from aqsp.web.archive_safety import sanitize_research_lines
from aqsp.web.live_candidate_view import (
    LiveArtifactMetadata,
    LiveCandidateView,
    build_live_candidate_view,
)

logger = logging.getLogger(__name__)


def _rating_rank(rating: object) -> int:
    ranks = {
        "avoid": 0,
        "watch": 1,
        "buy_candidate": 2,
        "strong_buy_candidate": 3,
    }
    return ranks.get(str(rating or "").strip(), -1)


_TASK_LABELS = {
    "main_chain": "主链推荐",
    "intraday": "盘中观察",
    "morning_breakout": "早盘策略",
    "closing_premium": "尾盘策略",
    "closing_review": "收盘复盘",
    "briefing": "简报回看",
}
MISSING_BLOCKER_TEXT = "阻塞原因未记录，需补充风险说明或复核条件"
_SIGNAL_TASK_IDS = ("main_chain", "morning_breakout", "closing_premium")
_OBSERVATION_TASK_IDS = ("intraday",)
_TASK_PHASE_META: dict[str, tuple[int, str, str]] = {
    "main_chain": (1, "盘前主链", "先确认当日主推候选与纸面复核优先级"),
    "intraday": (2, "盘中观察", "未收盘快照，可纸面复核，不进入正式 ledger"),
    "morning_breakout": (3, "早盘观察", "开盘后核对强势突破是否成立"),
    "closing_premium": (4, "尾盘确认", "收盘前评估溢价承接与隔夜价值"),
    "closing_review": (5, "收盘复盘", "核对执行结果与失效样本"),
    "briefing": (6, "次日预案", "整理明日重点与待跟踪事项"),
}
_RUNTIME_TASK_LABELS = {
    "news": "消息雷达",
    "intraday": "盘中观察",
    "midday": "午间快照",
    "daily": "收盘主链",
    "coldstart": "冷启动",
    "walkforward-gate": "生产回测 gate",
    "monitor": "运行监控",
}
_RUNTIME_TASK_ORDER = tuple(_RUNTIME_TASK_LABELS)
_RUNTIME_LOG_TAIL_BYTES = 65536
_TASK_METRIC_LABELS: dict[str, tuple[str, str, str]] = {
    "intraday": ("纸面复核", "盘中观察", "盘中阻塞"),
    "closing_review": ("已验证", "待复盘", "复盘阻塞"),
    "briefing": ("已落盘", "待跟踪", "待补档"),
}
_DEBATE_ROLE_ORDER = (
    "bull",
    "bear",
    "risk_control",
    "sector_leader",
    "cross_market",
    "policy_sensitive",
    "margin_trading",
    "northbound",
    "retail_mood",
)
_DEBATE_ROLE_LABELS = {
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
_DEBATE_STANCE_LABELS = {
    "bullish": "看多",
    "bearish": "看空",
    "neutral": "中性",
}
_DEBATE_ADJUSTMENT_LABELS = {
    "raise": "辩论倾向上调",
    "lower": "辩论倾向下调",
    "keep": "辩论倾向维持",
}
_DEBATE_BACKFILL_FIELDS = frozenset(
    {
        "debate_research_verdict",
        "debate_primary_risk_gate",
        "debate_next_trigger",
        "debate_active_role_summary",
        "support_points",
        "opposition_points",
        "watch_items",
        "role_reliability_lines",
        "debate_historical_context_note",
        "debate_historical_context_bucket",
        "debate_historical_context_sample_count",
        "debate_historical_context_accuracy",
    }
)


def _runtime_float(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _technical_metric_value(
    row: dict[str, Any],
    key: str,
    *aliases: str,
) -> float | None:
    for field in (key, *aliases):
        value = _runtime_float(row.get(field))
        if value is not None and math.isfinite(value):
            return value
    return None


def _runtime_pct(value: Any) -> str:
    number = _runtime_float(value)
    return "-" if number is None else f"{number:.2f}%"


def _runtime_live_source_boundary_label(source_id: str) -> str:
    source = str(source_id or "").strip()
    if not source:
        return ""
    fit = workload_fit_for_source(source).get("live_short", "unknown")
    if source_supports_workload(source, "live_short"):
        return f"实时源 {source}（live_short={fit}）"
    return f"当前实际源 {source} 只适合历史验证，盘中短线不可用（live_short={fit}）"


def _market_context_runtime_line() -> str:
    summary = cross_market_rule_runtime_summary()
    if not summary.global_enabled:
        return "跨市规则: 全球信息规则已关闭；仅保留本地确定性链路"
    themes = " / ".join(summary.rule_themes[:3])
    if len(summary.rule_themes) > 3:
        themes += " 等"
    boundary = (
        "确定性上下文优先级增强"
        if summary.advisory_boundary == "deterministic_context_priority_only"
        else summary.advisory_boundary
    )
    source_line = ""
    try:
        from aqsp.data.news_source import rss_news_runtime_summary

        rss_summary = rss_news_runtime_summary()
    except Exception:
        rss_summary = None
    if rss_summary is not None:
        source_line = (
            f" | RSS源: {rss_summary.feed_count} 个 / "
            f"覆盖 {len(rss_summary.covered_triggers)}/4 类"
        )
        if rss_summary.missing_triggers:
            source_line += f" / 缺 {','.join(rss_summary.missing_triggers)}"
    return (
        f"跨市规则: {summary.rule_count} 条在线 | "
        f"{themes or '未配置核心主题'} | 边界: {boundary}{source_line}"
    )


@dataclass(frozen=True)
class DashboardSummary:
    signal_count: int
    latest_signal_date: str
    open_positions: int
    pending_entries: int
    not_executable: int
    closed_trades: int
    execution_logs: int


@dataclass(frozen=True)
class DashboardTaskOption:
    task_id: str
    label: str


@dataclass(frozen=True)
class DashboardTaskSnapshot:
    task_id: str
    task_label: str
    latest_date: str
    status_label: str
    headline: str
    actionable_count: int
    watch_count: int
    blocked_count: int


@dataclass(frozen=True)
class DashboardTaskLiteSummary:
    status_label: str
    headline: str
    phase_summary: str
    candidate_count: int
    actionable_count: int
    watch_count: int
    blocked_count: int
    created_at: str = ""


@dataclass(frozen=True)
class DashboardRuntimeTaskRun:
    action: str
    task_label: str
    log_date: str
    log_mtime: str
    status_label: str
    headline: str
    detail_lines: tuple[str, ...]


@dataclass(frozen=True)
class DashboardRuntimeOverview:
    signal_date: str
    conclusion: str
    task_id: str
    task_label: str
    run_status: str
    requested_source: str
    effective_source: str
    source_reason: str
    data_latest_trade_date: str
    lag_days: str
    risk_reason: str
    cooldown_until: str
    coldstart_progress: str
    gate_blocker_line: str = ""
    coldstart_handoff_line: str = ""
    market_context_runtime_line: str = ""
    walkforward_runtime_line: str = ""
    intraday_runtime_line: str = ""


@dataclass(frozen=True)
class DashboardTaskHistoryRow:
    signal_date: str
    candidate_count: int
    actionable_count: int
    watch_count: int
    blocked_count: int
    headline: str


@dataclass(frozen=True)
class DashboardTimelineRow:
    signal_date: str
    task_labels: tuple[str, ...]
    actionable_total: int
    watch_total: int
    blocked_total: int
    headline: str


@dataclass(frozen=True)
class DashboardSameDayTaskRow:
    signal_date: str
    task_id: str
    task_label: str
    phase_order: int
    phase_label: str
    phase_summary: str
    status_label: str
    headline: str
    candidate_count: int
    actionable_count: int
    watch_count: int
    blocked_count: int
    created_at: str = ""


@dataclass(frozen=True)
class DashboardDateOverview:
    signal_date: str
    task_count: int
    actionable_total: int
    watch_total: int
    blocked_total: int
    top_task_label: str
    top_headline: str
    blocker_headline: str
    focus_headline: str
    workflow_summary: str
    archive_summary: str


@dataclass(frozen=True)
class DashboardHomeStatus:
    """Small read-only status contract used by the two-column home board."""

    label: str
    detail: str
    tone: str
    actionable_count: int
    watch_count: int
    blocked_count: int
    source_label: str


@dataclass(frozen=True)
class DashboardCandidateSpotlight:
    symbol: str
    display_name: str
    score: float
    action_label: str
    status_label: str
    blocker: str
    next_step: str
    review_meta: str
    task_labels: tuple[str, ...]
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    strategies: tuple[str, ...] = ()
    cross_market_summary: str = ""
    news_catalyst_summary: str = ""
    cross_market_chain_summary: str = ""
    cross_market_validation_summary: str = ""
    cross_market_invalidation_summary: str = ""
    support_points: tuple[str, ...] = ()
    opposition_points: tuple[str, ...] = ()
    watch_items: tuple[str, ...] = ()
    freshness_label: str = ""
    evidence_quality_label: str = ""
    artifact_date: str = ""
    updated_at: str = ""
    candidate_fingerprint: str = ""
    close: float | None = None
    ret5_pct: float | None = None
    ret20_pct: float | None = None
    volume_ratio: float | None = None
    rsi12: float | None = None
    bias20_pct: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None


@dataclass(frozen=True)
class DashboardCandidateJourneyStep:
    task_id: str
    task_label: str
    phase_label: str
    score: float
    action_label: str
    status_label: str
    blocker: str
    next_step: str
    review_meta: str
    reasons: tuple[str, ...]
    risks: tuple[str, ...]


@dataclass(frozen=True)
class DashboardPaperSummary:
    signal_date: str
    open_positions: int
    pending_entries: int
    not_executable: int
    closed_trades: int
    open_position_lines: tuple[str, ...]
    event_lines: tuple[str, ...]
    action_summary_lines: tuple[str, ...]


@dataclass(frozen=True)
class DashboardHomeDigestPayload:
    task_view: DashboardTaskView
    same_day_rows: tuple[DashboardSameDayTaskRow, ...]
    spotlights: tuple[DashboardCandidateSpotlight, ...]
    debates: tuple[DashboardDebateSummary, ...]
    overview: DashboardDateOverview
    paper_summary: DashboardPaperSummary


@dataclass(frozen=True)
class DashboardExecutionFocus:
    symbol: str
    display_name: str
    research_status: str
    execution_status: str
    holding_status: str
    research_lines: tuple[str, ...]
    readiness_lines: tuple[str, ...]
    execution_lines: tuple[str, ...]
    holding_lines: tuple[str, ...]


@dataclass(frozen=True)
class DashboardDebateAgentView:
    role_id: str
    role_label: str
    stance: str
    stance_label: str
    confidence: float
    key_argument: str
    key_risk: str
    key_opportunity: str


@dataclass(frozen=True)
class DashboardDebateSummary:
    signal_date: str
    symbol: str
    display_name: str
    debate_id: str
    rating: str
    original_score: float
    adjusted_score: float
    adjustment_weight: float
    recommended_adjustment: str
    recommended_adjustment_label: str
    disagreement_score: float
    consensus: str
    adjustment_reason: str
    bull_count: int
    bear_count: int
    neutral_count: int
    round_count: int
    regime: str
    data_source: str
    thresholds_version: str
    summary_lines: tuple[str, ...]
    round_summaries: tuple[str, ...]
    risk_warnings: tuple[str, ...]
    opportunity_highlights: tuple[str, ...]
    agent_views: tuple[DashboardDebateAgentView, ...]
    cross_market_summary: str = ""
    cross_market_chain_summary: str = ""
    cross_market_validation_summary: str = ""
    cross_market_invalidation_summary: str = ""
    research_verdict: str = ""
    primary_risk_gate: str = ""
    next_trigger: str = ""
    historical_context_note: str = ""
    role_reliability_lines: tuple[str, ...] = ()
    role_selection_summary: str = ""
    role_selection_plan: str = ""
    support_points: tuple[str, ...] = ()
    opposition_points: tuple[str, ...] = ()
    watch_items: tuple[str, ...] = ()
    created_at: str = ""
    candidate_fingerprint: str = ""


@dataclass(frozen=True)
class DashboardDebateConclusion:
    decision_line: str = ""
    consensus_line: str = ""
    cross_market_line: str = ""
    chain_or_trigger_line: str = ""
    validation_line: str = ""
    invalidation_line: str = ""
    active_roles_line: str = ""
    history_line: str = ""
    reliability_line: str = ""
    support_line: str = ""
    opposition_line: str = ""
    watch_line: str = ""
    evidence_line: str = ""


def debate_summary_cross_market_line(
    summary: DashboardDebateSummary,
) -> str:
    cross_market_view = next(
        (view for view in summary.agent_views if view.role_id == "cross_market"),
        None,
    )
    if cross_market_view is None:
        return (
            f"跨市传导: {summary.cross_market_summary}"
            if summary.cross_market_summary
            else ""
        )
    lead_argument = (
        cross_market_view.key_argument
        or cross_market_view.key_opportunity
        or cross_market_view.key_risk
        or ""
    ).strip()
    if not lead_argument:
        return (
            f"跨市传导: {summary.cross_market_summary}"
            if summary.cross_market_summary
            else ""
        )
    return f"跨市传导: {lead_argument}"


def debate_summary_chain_line(
    summary: DashboardDebateSummary | None = None,
    *,
    spotlight: DashboardCandidateSpotlight | None = None,
) -> str:
    chain_summary = ""
    if spotlight is not None:
        chain_summary = spotlight.cross_market_chain_summary
    elif summary is not None:
        chain_summary = summary.cross_market_chain_summary
    if not chain_summary:
        return ""
    return f"传导链: {chain_summary}"


def debate_summary_evidence_line(
    summary: DashboardDebateSummary | None,
) -> str:
    if summary is None:
        return ""
    parts = [
        f"{summary.round_count} 轮讨论",
        f"{len(summary.agent_views)} 个观点",
    ]
    if summary.data_source:
        parts.append(f"数据源 {summary.data_source}")
    if summary.thresholds_version:
        parts.append(f"阈值 {summary.thresholds_version}")
    return f"证据构成: {' / '.join(parts)}"


def debate_summary_active_roles_line(
    summary: DashboardDebateSummary | None,
) -> str:
    if summary is None:
        return ""
    labels = tuple(
        dict.fromkeys(
            view.role_label for view in summary.agent_views if view.role_label
        )
    )
    if not labels:
        return ""
    if len(labels) <= 5:
        return "讨论视角: " + "、".join(labels)
    return "讨论视角: " + "、".join(labels[:5]) + f" 等 {len(labels)} 个角色"


def build_debate_conclusion(
    summary: DashboardDebateSummary | None,
    *,
    spotlight: DashboardCandidateSpotlight | None = None,
    fallback_verdict: str = "",
) -> DashboardDebateConclusion:
    if summary is None:
        return DashboardDebateConclusion()

    verdict = summary.research_verdict.strip() or fallback_verdict.strip()
    if verdict:
        decision_line = (
            f"研究口径: {verdict}；卡点 {summary.primary_risk_gate}"
            if summary.primary_risk_gate
            else f"研究口径: {verdict}"
        )
    elif summary.primary_risk_gate:
        decision_line = f"核心卡点: {summary.primary_risk_gate}"
    else:
        fallback_conclusion = summary.consensus or summary.recommended_adjustment_label
        decision_line = (
            f"当前结论: {fallback_conclusion}" if fallback_conclusion else ""
        )

    cross_market_line = debate_summary_cross_market_line(summary)
    if (
        not cross_market_line
        and spotlight is not None
        and spotlight.cross_market_summary
    ):
        cross_market_line = f"跨市传导: {spotlight.cross_market_summary}"

    chain_line = debate_summary_chain_line(summary, spotlight=spotlight)
    trigger_line = f"触发 {summary.next_trigger}" if summary.next_trigger else ""
    if chain_line and trigger_line:
        chain_or_trigger_line = f"{chain_line}；{trigger_line}"
    elif chain_line:
        chain_or_trigger_line = chain_line
    elif trigger_line:
        chain_or_trigger_line = f"下一触发: {summary.next_trigger}"
    elif summary.support_points:
        chain_or_trigger_line = f"讨论支持: {summary.support_points[0]}"
    elif summary.opposition_points:
        chain_or_trigger_line = f"讨论反对: {summary.opposition_points[0]}"
    else:
        chain_or_trigger_line = ""

    validation_line = ""
    invalidation_line = ""
    validation_summary = (
        summary.cross_market_validation_summary
        or (spotlight.cross_market_validation_summary if spotlight is not None else "")
    ).strip()
    invalidation_summary = (
        summary.cross_market_invalidation_summary
        or (
            spotlight.cross_market_invalidation_summary if spotlight is not None else ""
        )
    ).strip()
    if validation_summary:
        validation_line = f"确认信号: {validation_summary}"
    if invalidation_summary:
        invalidation_line = f"失效信号: {invalidation_summary}"

    return DashboardDebateConclusion(
        decision_line=decision_line,
        consensus_line=f"结论共识: {summary.consensus}" if summary.consensus else "",
        cross_market_line=cross_market_line,
        chain_or_trigger_line=chain_or_trigger_line,
        validation_line=validation_line,
        invalidation_line=invalidation_line,
        active_roles_line=debate_summary_active_roles_line(summary),
        history_line=(
            f"历史校验: {summary.historical_context_note}"
            if summary.historical_context_note
            else ""
        ),
        reliability_line=(
            f"角色可信度: {summary.role_reliability_lines[0]}"
            if summary.role_reliability_lines
            else ""
        ),
        support_line=(
            f"讨论支持: {summary.support_points[0]}" if summary.support_points else ""
        ),
        opposition_line=(
            f"讨论反对: {summary.opposition_points[0]}"
            if summary.opposition_points
            else ""
        ),
        watch_line=(
            f"讨论待确认: {summary.watch_items[0]}" if summary.watch_items else ""
        ),
        evidence_line=debate_summary_evidence_line(summary),
    )


def debate_summary_priority_key(
    summary: DashboardDebateSummary,
) -> tuple[int, ...] | tuple[int, ... | str]:
    verdict = summary.research_verdict.strip()
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
        any(view.role_id == "cross_market" for view in summary.agent_views)
    )
    structure_count = sum(
        1
        for value in (
            summary.primary_risk_gate,
            summary.next_trigger,
            summary.historical_context_note,
            summary.role_reliability_lines,
            summary.support_points,
            summary.opposition_points,
            summary.watch_items,
        )
        if value
    )
    return (
        verdict_rank,
        int(bool(summary.next_trigger)),
        int(bool(summary.primary_risk_gate)),
        int(bool(summary.historical_context_note)),
        int(bool(summary.role_reliability_lines)),
        cross_market_present,
        structure_count,
        int(summary.disagreement_score * 100),
        summary.round_count,
        int(summary.adjusted_score * 100),
        summary.created_at,
    )


def debate_summary_signal_value_tier(
    summary: DashboardDebateSummary,
) -> str:
    verdict = summary.research_verdict.strip()
    has_priority_verdict = bool(
        verdict
        and (
            ("优先" in verdict and ("复核" in verdict or "跟踪" in verdict))
            or any(
                keyword in verdict
                for keyword in ("纸面复核", "纸面跟踪", "重点跟踪", "重点观察")
            )
        )
    )
    has_cross_market = any(
        view.role_id == "cross_market" for view in summary.agent_views
    )
    structure_count = sum(
        1
        for value in (
            summary.primary_risk_gate,
            summary.next_trigger,
            summary.historical_context_note,
            summary.role_reliability_lines,
            summary.support_points,
            summary.opposition_points,
            summary.watch_items,
        )
        if value
    )
    if has_priority_verdict and (
        summary.next_trigger
        or summary.primary_risk_gate
        or summary.historical_context_note
        or has_cross_market
        or structure_count >= 4
    ):
        return "high"
    if (
        has_priority_verdict
        or has_cross_market
        or structure_count >= 3
        or summary.disagreement_score >= 0.35
    ):
        return "medium"
    return "low"


@dataclass(frozen=True)
class DashboardTaskView:
    task_id: str
    task_label: str
    selected_date: str
    latest_date: str
    previous_date: str
    available_dates: tuple[str, ...]
    headline: str
    summary_lines: tuple[str, ...]
    lifecycle_lines: tuple[str, ...]
    unlock_lines: tuple[str, ...]
    report_summary_lines: tuple[str, ...]
    runtime_lines: tuple[str, ...]
    delta_lines: tuple[str, ...]
    agenda_lines: tuple[str, ...]
    recommendation_lines: tuple[str, ...]
    watchlist_lines: tuple[str, ...]
    blocker_lines: tuple[str, ...]
    review_lines: tuple[str, ...]
    next_day_focus_lines: tuple[str, ...]
    report_markdown: str
    report_source: str
    report_mtime: str
    source_status: dict[str, str]
    candidate_count: int
    actionable_count: int
    watch_count: int
    blocked_count: int
    detail_cards: tuple["DashboardCandidateCard", ...]
    ranking_lines: tuple[str, ...]
    market_environment: str
    strategy_breakdown_lines: tuple[str, ...]
    lesson_lines: tuple[str, ...]
    improvement_lines: tuple[str, ...]


@dataclass(frozen=True)
class DashboardCandidateCard:
    symbol: str
    name: str
    display_name: str
    rank_label: str
    score: float
    action_label: str
    status_label: str
    decision_note: str
    next_step: str
    blocker: str
    review_meta: str
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    strategies: tuple[str, ...]
    data_source: str
    news_catalyst_summary: str = ""
    cross_market_summary: str = ""
    cross_market_chain_summary: str = ""
    cross_market_validation_summary: str = ""
    cross_market_invalidation_summary: str = ""
    freshness_label: str = ""
    evidence_quality_label: str = ""
    artifact_date: str = ""
    updated_at: str = ""
    candidate_fingerprint: str = ""
    close: float | None = None
    ret5_pct: float | None = None
    ret20_pct: float | None = None
    volume_ratio: float | None = None
    rsi12: float | None = None
    bias20_pct: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None


@dataclass(frozen=True)
class DashboardReportInsights:
    report_summary_lines: tuple[str, ...]
    runtime_lines: tuple[str, ...]
    market_environment: str
    next_day_focus_lines: tuple[str, ...]


@dataclass(frozen=True)
class DashboardReportDocument:
    markdown: str
    source: str
    mtime: str


@dataclass(frozen=True)
class DashboardClosingReviewFacts:
    total_signals: int
    closed_trades: int
    pending_entries: int
    not_executable: int
    open_positions: int
    win_count: int
    loss_count: int
    win_rate: float
    total_return: float
    strategy_breakdown: dict[str, dict[str, Any]]
    lesson_lines: tuple[str, ...]
    improvement_lines: tuple[str, ...]


class DashboardDataProvider:
    """仪表盘数据提供器，只读真实账本、报告和执行日志。"""

    def __init__(
        self,
        ledger_path: str = "",
        paper_ledger_path: str = "",
        logs_path: str = "",
        reports_dir: str = "",
        debate_results_path: str = "",
        bt_logs_dir: str = "",
        intraday_ledger_path: str = "",
        intraday_latest_path: str = "",
    ) -> None:
        resolved_ledger = (
            ledger_path.strip()
            or os.getenv("AQSP_LEDGER", "").strip()
            or "data/predictions.jsonl"
        )
        resolved_paper_ledger = (
            paper_ledger_path.strip()
            or os.getenv("AQSP_PAPER_LEDGER", "").strip()
            or "data/paper_trades.jsonl"
        )
        resolved_logs = logs_path.strip() or "logs/trades"
        resolved_bt_logs = (
            bt_logs_dir.strip()
            or os.getenv("AQSP_BT_LOGS_DIR", "").strip()
            or "logs/bt"
        )
        resolved_reports = reports_dir.strip() or "reports"
        resolved_debate_results = (
            debate_results_path.strip()
            or os.getenv("AQSP_DEBATE_RESULTS", "").strip()
            or "data/debate_results.jsonl"
        )
        resolved_intraday_ledger = (
            intraday_ledger_path.strip()
            or os.getenv("AQSP_INTRADAY_LEDGER", "").strip()
            or "data/intraday_predictions.jsonl"
        )
        resolved_intraday_latest = (
            intraday_latest_path.strip()
            or os.getenv("AQSP_INTRADAY_LATEST_CSV", "").strip()
            or os.getenv("AQSP_INTRADAY_OUTPUT_CSV", "").strip()
            or "reports/intraday_latest.csv"
        )

        self.ledger_path = Path(resolved_ledger)
        self.paper_ledger_path = Path(resolved_paper_ledger)
        self.logs_path = Path(resolved_logs)
        self.bt_logs_path = Path(resolved_bt_logs)
        self.reports_dir = Path(resolved_reports)
        self.debate_results_path = Path(resolved_debate_results)
        self.intraday_ledger_path = Path(resolved_intraday_ledger)
        self.intraday_latest_path = Path(resolved_intraday_latest)
        self.logger = TradeLogger(str(self.logs_path))
        self._runtime_cache: dict[str, dict[object, Any]] = {}
        self._runtime_cache_source_signature = self._source_signature()

    def _source_signature(self) -> tuple[tuple[str, int, int], ...]:
        paths = tuple(
            path
            for path in (
                getattr(self, "ledger_path", None),
                getattr(self, "paper_ledger_path", None),
                getattr(self, "debate_results_path", None),
                getattr(self, "intraday_ledger_path", None),
                getattr(self, "intraday_latest_path", None),
            )
            if isinstance(path, Path)
        )
        signature: list[tuple[str, int, int]] = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                signature.append((str(path), -1, -1))
            else:
                signature.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(signature)

    def _invalidate_cache_when_sources_change(self) -> None:
        signature = self._source_signature()
        if signature == getattr(self, "_runtime_cache_source_signature", None):
            return
        runtime_cache = getattr(self, "_runtime_cache", None)
        if isinstance(runtime_cache, dict):
            runtime_cache.clear()
        self._runtime_cache_source_signature = signature

    def _cache_bucket(self, name: str) -> dict[object, Any]:
        runtime_cache = getattr(self, "_runtime_cache", None)
        if not isinstance(runtime_cache, dict):
            runtime_cache = {}
            setattr(self, "_runtime_cache", runtime_cache)
        bucket = runtime_cache.get(name)
        if not isinstance(bucket, dict):
            bucket = {}
            runtime_cache[name] = bucket
        return bucket

    def _cache_value(
        self,
        bucket_name: str,
        key: object,
        loader: Callable[[], Any],
    ) -> Any:
        self._invalidate_cache_when_sources_change()
        bucket = self._cache_bucket(bucket_name)
        if key not in bucket:
            bucket[key] = loader()
        return bucket[key]

    def load_signal_rows(self) -> list[dict[str, Any]]:
        def _load() -> list[dict[str, Any]]:
            try:
                rows = read_ledger(self.ledger_path)
            except Exception as exc:
                logger.error("加载 signal ledger 失败: %s", exc)
                return []
            normalized: list[dict[str, Any]] = []
            for row in rows:
                if isinstance(row, dict):
                    normalized.append(dict(row))
            normalized.extend(self._load_intraday_rows())
            return normalized

        return self._cache_value("load_signal_rows", "all", _load)

    def _load_intraday_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        csv_loaded = False
        if self.intraday_latest_path.exists():
            try:
                frame = pd.read_csv(self.intraday_latest_path, dtype=str)
            except Exception as exc:
                logger.error("加载 intraday latest csv 失败: %s", exc)
            else:
                csv_loaded = True
                try:
                    mtime = datetime.fromtimestamp(
                        self.intraday_latest_path.stat().st_mtime,
                        tz=SHANGHAI_TZ,
                    ).isoformat()
                except OSError:
                    mtime = ""
                for raw_row in frame.to_dict(orient="records"):
                    symbol = str(raw_row.get("symbol", "") or "").strip()
                    if not symbol:
                        continue
                    normalized = {
                        key: "" if pd.isna(value) else value
                        for key, value in raw_row.items()
                    }
                    normalized["symbol"] = (
                        "__RUN__"
                        if symbol == "__RUN__"
                        else self._canonical_symbol(symbol)
                    )
                    normalized["signal_date"] = str(
                        normalized.get("signal_date") or normalized.get("date") or ""
                    ).strip()
                    normalized["task_id"] = "intraday"
                    normalized["run_task_id"] = "intraday"
                    normalized.setdefault("created_at", mtime)
                    normalized["_artifact_source"] = "intraday_csv"
                    normalized["_artifact_updated_at"] = mtime
                    rows.append(
                        normalized
                        if symbol == "__RUN__"
                        else self._normalize_intraday_runtime_row(normalized)
                    )

        if self.intraday_ledger_path.exists():
            try:
                intraday_rows = read_ledger(self.intraday_ledger_path)
            except Exception as exc:
                logger.error("加载 intraday ledger 失败: %s", exc)
                intraday_rows = []
            for row in intraday_rows:
                if not isinstance(row, dict):
                    continue
                if csv_loaded and not self._is_runtime_event_row(row):
                    continue
                normalized = dict(row)
                normalized.setdefault("task_id", "intraday")
                normalized.setdefault("run_task_id", "intraday")
                rows.append(self._normalize_intraday_runtime_row(normalized))
        return rows

    def live_candidate_view(
        self,
        *,
        signal_date: str = "",
        now: datetime | None = None,
    ) -> LiveCandidateView:
        """Return the latest intraday CSV as a bounded, freshness-aware view."""
        current_time = now or now_shanghai()
        selected_date = signal_date.strip()

        def _build() -> LiveCandidateView:
            csv_rows = [
                row
                for row in self.load_signal_rows()
                if row.get("_artifact_source") == "intraday_csv"
                and not self._is_runtime_event_row(row)
            ]
            try:
                updated_at = datetime.fromtimestamp(
                    self.intraday_latest_path.stat().st_mtime,
                    tz=SHANGHAI_TZ,
                ).isoformat()
            except OSError:
                updated_at = ""
            artifact_dates = {
                str(row.get("signal_date") or row.get("date") or "").strip()
                for row in csv_rows
                if str(row.get("signal_date") or row.get("date") or "").strip()
            }
            artifact_date = max(artifact_dates, default="")
            metadata = LiveArtifactMetadata(
                artifact_date=artifact_date,
                updated_at=updated_at,
                freshness_status=self._intraday_artifact_freshness_status(),
                source_reason=self._intraday_artifact_status_reason(),
            )
            return build_live_candidate_view(
                csv_rows,
                metadata=metadata,
                now=current_time,
                requested_date=selected_date,
            )

        cache_key = (selected_date, current_time.isoformat(timespec="minutes"))
        return self._cache_value("live_candidate_view", cache_key, _build)

    def _intraday_artifact_state(self) -> dict[str, Any]:
        return self._read_runtime_json_state(
            env_name="AQSP_INTRADAY_STATUS",
            filename="intraday_refresh_status.json",
            bucket_name="intraday_refresh_status",
        )

    def _intraday_artifact_freshness_status(self) -> str:
        state = self._intraday_artifact_state()
        status = str(state.get("status") or "").strip()
        if status in {"failed", "error"}:
            return "failed"
        freshness = state.get("freshness")
        if isinstance(freshness, dict):
            nested_status = str(freshness.get("status") or "").strip()
            if nested_status:
                return nested_status
        quality_gate = state.get("quality_gate")
        if isinstance(quality_gate, dict):
            gate_status = str(quality_gate.get("freshness_status") or "").strip()
            if gate_status:
                return gate_status
        if not status or status == "completed":
            return ""
        return "unknown"

    def _intraday_artifact_status_reason(self) -> str:
        state = self._intraday_artifact_state()
        return str(state.get("reason") or state.get("detail") or "").strip()

    def live_candidate_spotlights(
        self,
        *,
        signal_date: str = "",
        now: datetime | None = None,
    ) -> tuple[DashboardCandidateSpotlight, ...]:
        """Convert the bounded live view into the existing home-card contract."""
        live_view = self.live_candidate_view(signal_date=signal_date, now=now)
        spotlights: list[DashboardCandidateSpotlight] = []
        for candidate in live_view.candidates:
            merged_row = self._same_day_merged_row(dict(candidate.row), ())
            blocker = candidate.blocker or self._candidate_blocker_text(merged_row)
            if live_view.status == "stale":
                stale_blocker = live_view.stale_reason or "盘中产物已过期"
                blocker = f"{blocker}；{stale_blocker}" if blocker else stale_blocker
                action_label = "数据过期"
                status_label = "数据已过期"
            elif live_view.status != "fresh":
                blocker = blocker or "盘中产物不可用"
                action_label = "数据不可用"
                status_label = "数据不可用"
            else:
                action_label, status_label = {
                    "actionable": ("纸面复核", "纸面复核"),
                    "watch": ("继续观察", "继续观察"),
                    "blocked": ("阻塞观察", "阻塞观察"),
                }.get(candidate.status, ("继续观察", "继续观察"))
            review_meta = self._review_meta(merged_row)
            live_meta = (
                f"新鲜度: {candidate.freshness_label}（更新 {candidate.updated_at}）"
                f" / 证据质量: {candidate.evidence_quality_label}"
            )
            review_meta = " / ".join(part for part in (review_meta, live_meta) if part)
            spotlights.append(
                DashboardCandidateSpotlight(
                    symbol=candidate.symbol,
                    display_name=self._symbol_name(merged_row),
                    score=candidate.score,
                    action_label=action_label,
                    status_label=status_label,
                    blocker=blocker,
                    next_step=candidate.next_step or self._next_step_text(merged_row),
                    review_meta=review_meta,
                    task_labels=("盘中观察",),
                    reasons=self._as_text_tuple(merged_row.get("reasons"))
                    or candidate.reasons,
                    risks=self._as_text_tuple(merged_row.get("risks"))
                    or candidate.risks,
                    strategies=self._strategy_tuple(merged_row.get("strategies")),
                    cross_market_summary=self._spotlight_cross_market_summary(
                        merged_row
                    ),
                    news_catalyst_summary=self._spotlight_news_catalyst_summary(
                        merged_row
                    ),
                    cross_market_chain_summary=self._spotlight_cross_market_chain_summary(
                        merged_row
                    ),
                    cross_market_validation_summary=(
                        self._spotlight_cross_market_validation_summary(merged_row)
                    ),
                    cross_market_invalidation_summary=(
                        self._spotlight_cross_market_invalidation_summary(merged_row)
                    ),
                    support_points=self._as_text_tuple(
                        merged_row.get("support_points")
                    ),
                    opposition_points=self._as_text_tuple(
                        merged_row.get("opposition_points")
                    ),
                    watch_items=self._as_text_tuple(merged_row.get("watch_items")),
                    freshness_label=candidate.freshness_label,
                    evidence_quality_label=candidate.evidence_quality_label,
                    artifact_date=candidate.artifact_date,
                    updated_at=candidate.updated_at,
                    candidate_fingerprint=self._candidate_fingerprint_for_row(
                        merged_row
                    ),
                    close=_technical_metric_value(merged_row, "close", "signal_close"),
                    ret5_pct=_technical_metric_value(merged_row, "ret5_pct"),
                    ret20_pct=_technical_metric_value(merged_row, "ret20_pct"),
                    volume_ratio=_technical_metric_value(merged_row, "volume_ratio"),
                    rsi12=_technical_metric_value(merged_row, "rsi12"),
                    bias20_pct=_technical_metric_value(merged_row, "bias20_pct"),
                    stop_loss=_technical_metric_value(merged_row, "stop_loss"),
                    take_profit=_technical_metric_value(merged_row, "take_profit"),
                )
            )
        return tuple(spotlights)

    def _normalize_intraday_runtime_row(self, row: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(row)
        normalized["symbol"] = self._canonical_symbol(
            str(normalized.get("symbol", "") or "")
        )
        current_rating = str(normalized.get("rating", "") or "").strip()
        try:
            score = float(normalized.get("score") or 0.0)
            score_rating = rating_for_score(score, load_thresholds().scoring)
        except Exception:
            return normalized
        if _rating_rank(score_rating) > _rating_rank(current_rating):
            normalized["rating"] = score_rating
            normalized["display_rating_corrected_from_score"] = True
        return normalized

    def load_paper_rows(self) -> list[dict[str, Any]]:
        def _load() -> list[dict[str, Any]]:
            try:
                rows = read_paper_trades(self.paper_ledger_path)
            except Exception as exc:
                logger.error("加载 paper ledger 失败: %s", exc)
                return []
            normalized: list[dict[str, Any]] = []
            for row in rows:
                if isinstance(row, dict):
                    normalized.append(dict(row))
            return normalized

        return self._cache_value("load_paper_rows", "all", _load)

    def debate_summary(
        self,
        *,
        signal_date: str,
        symbol: str,
        candidate_fingerprint: str = "",
        task_id: str = "",
    ) -> DashboardDebateSummary | None:
        """按 signal_date + symbol 返回结构化辩论摘要；缺失时返回 None。"""
        selected_date = signal_date.strip()
        selected_symbol = symbol.strip()
        if not selected_date or not selected_symbol:
            return None

        def _build() -> DashboardDebateSummary | None:
            debate_rows = self._load_debate_rows()
            if not candidate_fingerprint:
                debate_rows = self._dedupe_debate_rows(debate_rows)
            matches = [
                row
                for row in debate_rows
                if self._debate_signal_date(row) == selected_date
                and str(row.get("symbol", "") or "").strip() == selected_symbol
                and self._debate_matches_context(
                    row,
                    candidate_fingerprint=candidate_fingerprint,
                    task_id=task_id,
                )
                and self._has_debate_evidence(row)
            ]
            if not matches:
                return None
            return self._build_debate_summary(
                max(matches, key=self._debate_quality_key)
            )

        return self._cache_value(
            "debate_summary",
            (selected_date, selected_symbol, candidate_fingerprint, task_id),
            _build,
        )

    def debate_summaries(
        self,
        signal_date: str,
        *,
        limit: int = 8,
    ) -> tuple[DashboardDebateSummary, ...]:
        """返回某日所有结构化辩论摘要，按调整后评分与分歧度排序。"""
        selected_date = signal_date.strip()
        if not selected_date:
            return ()

        def _build() -> tuple[DashboardDebateSummary, ...]:
            summaries = [
                self._build_debate_summary(row)
                for row in self._dedupe_debate_rows(self._load_debate_rows())
                if self._debate_signal_date(row) == selected_date
                and self._has_debate_evidence(row)
            ]
            summaries.sort(
                key=lambda item: (
                    item.adjusted_score,
                    item.disagreement_score,
                    item.display_name,
                ),
                reverse=True,
            )
            return tuple(summaries[:limit])

        return self._cache_value(
            "debate_summaries",
            (selected_date, limit),
            _build,
        )

    def prioritized_debate_summaries(
        self,
        signal_date: str,
        *,
        limit: int = 8,
        salient_only: bool = False,
        task_id: str = "",
        symbols: tuple[str, ...] = (),
    ) -> tuple[DashboardDebateSummary, ...]:
        """返回某日所有结构化辩论摘要，按研究优先级而非单纯分数排序。"""
        selected_date = signal_date.strip()
        if not selected_date:
            return ()

        def _build() -> tuple[DashboardDebateSummary, ...]:
            raw_rows = self._load_debate_rows()
            normalized_task = task_id.strip()
            if normalized_task:
                task_rows = [
                    row
                    for row in raw_rows
                    if str(row.get("task_id", "") or "").strip() == normalized_task
                ]
                raw_rows = task_rows or [
                    row
                    for row in raw_rows
                    if not str(row.get("task_id", "") or "").strip()
                ]
            summaries = [
                self._build_debate_summary(row)
                for row in self._dedupe_debate_rows(raw_rows)
                if self._debate_signal_date(row) == selected_date
                and self._has_debate_evidence(row)
            ]
            symbol_set = {symbol.strip() for symbol in symbols if symbol.strip()}
            if symbol_set:
                summaries = [item for item in summaries if item.symbol in symbol_set]
            summaries.sort(key=debate_summary_priority_key, reverse=True)
            if salient_only:
                salient = tuple(
                    item
                    for item in summaries
                    if debate_summary_signal_value_tier(item) != "low"
                )
                return salient[:limit]
            return tuple(summaries[:limit])

        return self._cache_value(
            "prioritized_debate_summaries",
            (selected_date, limit, salient_only, task_id.strip(), symbols),
            _build,
        )

    def candidate_research_context(
        self,
        *,
        signal_date: str,
        symbol: str,
        preferred_task_id: str = "main_chain",
    ) -> dict[str, Any] | None:
        """返回某标的在当日最合适的研究上下文；缺失时返回 None。"""
        selected_date = signal_date.strip()
        selected_symbol = symbol.strip()
        if not selected_date or not selected_symbol:
            return None

        candidate_task_ids: list[str] = []
        if preferred_task_id in _SIGNAL_TASK_IDS:
            candidate_task_ids.append(preferred_task_id)
            for task_id in _SIGNAL_TASK_IDS:
                if task_id not in candidate_task_ids:
                    candidate_task_ids.append(task_id)
        else:
            candidate_task_ids.extend(reversed(_SIGNAL_TASK_IDS))

        matched_rows: list[tuple[int, int, dict[str, Any]]] = []
        for task_rank, task_id in enumerate(candidate_task_ids):
            rows = self._dedupe_rows(
                [
                    row
                    for row in self._task_signal_rows(task_id)
                    if str(row.get("signal_date", "") or "").strip() == selected_date
                    and str(row.get("symbol", "") or "").strip() == selected_symbol
                ]
            )
            if not rows:
                continue
            row = max(rows, key=self._row_meta_key)
            matched_rows.append(
                (
                    task_rank,
                    self._task_phase_order(task_id),
                    {
                        "task_id": task_id,
                        "task_label": self._task_label(task_id),
                        "phase_label": self._task_phase_label(task_id),
                        "row": row,
                    },
                )
            )
        if not matched_rows:
            return None
        matched_rows.sort(
            key=lambda item: (
                item[0],
                -float(item[2]["row"].get("score") or 0.0),
                item[1],
            )
        )
        selected = dict(matched_rows[0][2])
        selected["row"] = self._same_day_merged_row(
            selected["row"],
            [
                {"row": item[2]["row"], "task_id": item[2]["task_id"]}
                for item in matched_rows
            ],
        )
        return selected

    def _load_debate_rows(self) -> list[dict[str, Any]]:
        def _load() -> list[dict[str, Any]]:
            if not self.debate_results_path.exists():
                return []
            try:
                raw_text = self.debate_results_path.read_text(encoding="utf-8")
            except Exception as exc:
                logger.error("加载 debate results 失败: %s", exc)
                return []

            normalized: list[dict[str, Any]] = []
            for line in raw_text.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    logger.warning("解析 debate results 失败: %s", exc)
                    continue
                if isinstance(payload, dict):
                    normalized.append(payload)
            return normalized

        return self._cache_value("load_debate_rows", "all", _load)

    def summarize(self) -> DashboardSummary:
        signal_rows = self.load_signal_rows()
        paper_rows = self.load_paper_rows()
        execution_logs = len(self.get_recent_execution_logs(days=7))
        latest_signal_date = self._max_signal_date(signal_rows)
        return DashboardSummary(
            signal_count=len(signal_rows),
            latest_signal_date=latest_signal_date,
            open_positions=sum(1 for row in paper_rows if row.get("status") == "open"),
            pending_entries=sum(
                1 for row in paper_rows if row.get("status") == "pending_entry"
            ),
            not_executable=sum(
                1 for row in paper_rows if row.get("status") == "not_executable"
            ),
            closed_trades=sum(1 for row in paper_rows if row.get("status") == "closed"),
            execution_logs=execution_logs,
        )

    def task_options(self) -> tuple[DashboardTaskOption, ...]:
        return tuple(
            DashboardTaskOption(task_id=task_id, label=label)
            for task_id, label in _TASK_LABELS.items()
        )

    def runtime_task_runs(
        self,
        log_date: str = "",
        limit: int | None = None,
    ) -> tuple[DashboardRuntimeTaskRun, ...]:
        selected_date = log_date.strip()
        normalized_limit = limit if isinstance(limit, int) and limit > 0 else None

        def _build() -> tuple[DashboardRuntimeTaskRun, ...]:
            runs: list[DashboardRuntimeTaskRun] = []
            for action, path in self._runtime_task_log_candidates(
                selected_date,
                limit=normalized_limit,
            ):
                run = self._parse_runtime_task_log(action, path)
                if run is not None:
                    runs.append(run)
            runs.sort(key=lambda item: item.log_mtime, reverse=True)
            return tuple(runs)

        return self._cache_value(
            "runtime_task_runs",
            (selected_date, normalized_limit),
            _build,
        )

    def runtime_overview(self, signal_date: str = "") -> DashboardRuntimeOverview:
        selected_date = signal_date.strip()

        def _build() -> DashboardRuntimeOverview:
            run = self._latest_run_event(selected_date) or {}
            runs = self.runtime_task_runs(selected_date, limit=4)
            coldstart_run = next(
                (item for item in runs if item.action == "coldstart"),
                None,
            )
            coldstart_handoff = self._read_coldstart_handoff_state()
            handoff_progress = self._coldstart_handoff_progress(coldstart_handoff)
            handoff_line = self._coldstart_handoff_line_from_state(coldstart_handoff)
            risk_state = self._read_runtime_risk_state()
            gate_blocker_line = self._runtime_gate_blocker_line(selected_date)
            progress = ""
            if coldstart_run is not None:
                progress = self._coldstart_progress_from_lines(
                    coldstart_run.detail_lines
                )
            if not progress:
                progress = handoff_progress
            if not progress and (
                gate_blocker_line or str(risk_state.get("cooldown_until") or "").strip()
            ):
                progress = self._coldstart_progress_from_ledger()
            coldstart_ready = self._coldstart_progress_ready(progress)

            status = str(run.get("status") or "").strip()
            triggered = bool(run.get("run_circuit_breaker_triggered"))
            final_count = _runtime_float(run.get("run_final_count"))
            if (status == "blocked_by_circuit_breaker" or triggered) and (
                coldstart_ready and str(risk_state.get("cooldown_until") or "").strip()
            ):
                conclusion = "冷启动样本已达标，等待组合保护冷却"
            elif status == "blocked_by_circuit_breaker" or triggered:
                conclusion = "组合保护生效，暂停新增纸面复核"
            elif (
                coldstart_ready and str(risk_state.get("cooldown_until") or "").strip()
            ):
                conclusion = "冷启动样本已达标，等待组合保护冷却"
            elif coldstart_ready:
                conclusion = "冷启动样本已达标，等待生产 walk-forward gate"
            elif final_count == 0:
                conclusion = "最近运行无新增候选，先看阻塞与数据状态"
            elif run:
                conclusion = "最近运行已落盘，等待完整收盘摘要"
            elif coldstart_run is not None:
                conclusion = f"{coldstart_run.task_label}: {coldstart_run.status_label}"
            else:
                conclusion = ""

            task_id = str(run.get("run_task_id") or run.get("task_id") or "").strip()
            task_label = _RUNTIME_TASK_LABELS.get(task_id, task_id)
            if not task_id and coldstart_run is not None:
                task_id = coldstart_run.action
                task_label = coldstart_run.task_label

            requested_source = str(run.get("run_requested_source") or "").strip()
            effective_source = str(run.get("run_actual_source") or "").strip()
            source_for_reason = effective_source or requested_source
            source_reason = str(run.get("run_source_health_message") or "").strip()
            if source_for_reason and not source_supports_workload(
                source_for_reason,
                "live_short",
            ):
                boundary_reason = _runtime_live_source_boundary_label(source_for_reason)
                source_reason = " / ".join(
                    item for item in (boundary_reason, source_reason) if item
                )

            risk_reason = str(
                run.get("run_circuit_breaker_reason") or run.get("reason") or ""
            ).strip()
            return DashboardRuntimeOverview(
                signal_date=str(
                    run.get("signal_date")
                    or run.get("signal_day_group")
                    or selected_date
                )[:10],
                conclusion=conclusion,
                task_id=task_id,
                task_label=task_label,
                run_status=status
                or (coldstart_run.status_label if coldstart_run else ""),
                requested_source=requested_source,
                effective_source=effective_source,
                source_reason=source_reason,
                data_latest_trade_date=str(
                    run.get("run_data_latest_trade_date") or ""
                ).strip(),
                lag_days=(
                    ""
                    if run.get("run_data_lag_days") in ("", None)
                    else str(run.get("run_data_lag_days"))
                ),
                risk_reason=normalize_research_tone(risk_reason),
                cooldown_until=str(risk_state.get("cooldown_until") or "").strip(),
                coldstart_progress=progress,
                gate_blocker_line=gate_blocker_line,
                coldstart_handoff_line=handoff_line,
                market_context_runtime_line=_market_context_runtime_line(),
                walkforward_runtime_line=self._walkforward_runtime_line(),
                intraday_runtime_line=self._intraday_runtime_line(),
            )

        return self._cache_value("runtime_overview", selected_date, _build)

    def runtime_fallback_digest_lines(
        self,
        signal_date: str = "",
    ) -> tuple[str, ...]:
        selected_date = signal_date.strip()

        def _build() -> tuple[str, ...]:
            run = self._latest_run_event(selected_date)
            if run is None:
                return ()

            status = str(run.get("status") or "").strip()
            reason = str(
                run.get("run_circuit_breaker_reason") or run.get("reason") or ""
            ).strip()
            triggered = bool(run.get("run_circuit_breaker_triggered"))
            final_count = _runtime_float(run.get("run_final_count"))
            screened_count = _runtime_float(run.get("run_screened_count"))
            fetched_count = _runtime_float(run.get("run_fetched_frame_count"))

            if status == "blocked_by_circuit_breaker" or triggered:
                conclusion = "组合保护生效，暂停新增纸面复核"
            elif final_count == 0:
                conclusion = "最近运行无新增候选，先看阻塞与数据状态"
            else:
                conclusion = "最近运行已落盘，等待完整收盘摘要"

            lines = [f"结论: {conclusion}"]
            task_id = str(run.get("run_task_id") or run.get("task_id") or "").strip()
            run_date = str(
                run.get("signal_date") or run.get("signal_day_group") or selected_date
            ).strip()
            status_parts = []
            if task_id:
                status_parts.append(f"任务 {task_id}")
            if run_date:
                status_parts.append(f"日期 {run_date[:10]}")
            if status_parts:
                lines.append("运行状态: " + " / ".join(status_parts))

            source = str(
                run.get("run_actual_source") or run.get("run_requested_source") or ""
            ).strip()
            data_date = str(run.get("run_data_latest_trade_date") or "").strip()
            lag_value = run.get("run_data_lag_days")
            lag = "" if lag_value in ("", None) else str(lag_value)
            data_parts = []
            if source:
                data_parts.append(_runtime_live_source_boundary_label(source))
            if data_date:
                data_parts.append(f"数据日 {data_date}")
            if lag:
                data_parts.append(f"延迟 {lag} 天")
            if data_parts:
                lines.append("数据: " + " / ".join(data_parts))

            if reason:
                lines.append(f"风险/阻塞: {normalize_research_tone(reason)}")
            if any(
                run.get(key) is not None for key in ("daily_pnl_pct", "monthly_pnl_pct")
            ):
                lines.append(
                    "风控读数: "
                    f"日 {_runtime_pct(run.get('daily_pnl_pct'))} / "
                    f"月 {_runtime_pct(run.get('monthly_pnl_pct'))}"
                )
            elif any(
                value is not None
                for value in (fetched_count, screened_count, final_count)
            ):
                count_parts = []
                if fetched_count is not None:
                    count_parts.append(f"获取 {int(fetched_count)}")
                if screened_count is not None:
                    count_parts.append(f"筛选 {int(screened_count)}")
                if final_count is not None:
                    count_parts.append(f"候选 {int(final_count)}")
                if count_parts:
                    lines.append("流程: " + " / ".join(count_parts))
            return tuple(lines[:5])

        return self._cache_value("runtime_fallback_digest_lines", selected_date, _build)

    def _latest_run_event(self, signal_date: str = "") -> dict[str, Any] | None:
        rows = [
            row for row in self.load_signal_rows() if self._is_runtime_event_row(row)
        ]
        selected_date = signal_date.strip()
        if selected_date:
            dated = [
                row
                for row in rows
                if str(row.get("signal_date") or row.get("signal_day_group") or "")[:10]
                == selected_date[:10]
            ]
            if dated:
                rows = dated
        if not rows:
            return None
        return rows[-1]

    def _read_runtime_risk_state(self) -> dict[str, Any]:
        def _load() -> dict[str, Any]:
            candidates = (
                self.ledger_path.parent / "risk_state.json",
                Path("data/risk_state.json"),
            )
            for path in candidates:
                if not path.exists():
                    continue
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict):
                    return payload
            return {}

        return self._cache_value("runtime_risk_state", "all", _load)

    def _read_coldstart_handoff_state(self) -> dict[str, Any]:
        return self._read_runtime_json_state(
            env_name="AQSP_COLDSTART_HANDOFF_STATUS_PATH",
            filename="coldstart_handoff_status.json",
            bucket_name="coldstart_handoff_status",
        )

    def _read_runtime_json_state(
        self,
        *,
        env_name: str,
        filename: str,
        bucket_name: str,
    ) -> dict[str, Any]:
        def _load() -> dict[str, Any]:
            candidates: list[Path] = []
            env_path = os.getenv(env_name, "").strip()
            if env_path:
                candidates.append(Path(env_path))
            candidates.extend(
                (self.ledger_path.parent / filename, Path("data") / filename)
            )
            for path in candidates:
                if not path.exists():
                    continue
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict):
                    return payload
            return {}

        return self._cache_value(bucket_name, "all", _load)

    def _walkforward_runtime_line(self) -> str:
        status = self._read_runtime_json_state(
            env_name="AQSP_WALKFORWARD_PRODUCTION_STATUS",
            filename="walkforward_production_status.json",
            bucket_name="walkforward_production_status",
        )
        gate = self._read_runtime_json_state(
            env_name="AQSP_WALKFORWARD_GATE_PATH",
            filename="walkforward_gate.json",
            bucket_name="walkforward_gate",
        )
        return self._walkforward_runtime_line_from_state(status, gate)

    def _intraday_runtime_line(self) -> str:
        state = self._read_runtime_json_state(
            env_name="AQSP_INTRADAY_STATUS",
            filename="intraday_refresh_status.json",
            bucket_name="intraday_refresh_status",
        )
        return self._intraday_runtime_line_from_state(state)

    @staticmethod
    def _intraday_runtime_line_from_state(state: dict[str, Any]) -> str:
        status = str(state.get("status") or "").strip()
        if not status:
            return ""
        status_label = {
            "completed": "已刷新",
            "failed": "失败保留上一版",
            "skipped": "已跳过",
        }.get(status, status)
        source = str(state.get("source") or "").strip()
        updated_at = str(state.get("updated_at") or "").strip()[:16]
        max_universe = state.get("max_universe")
        reason = normalize_research_tone(str(state.get("reason") or "").strip())
        parts = [f"盘中刷新: {status_label}"]
        if source:
            parts.append(f"源 {source}")
        if max_universe not in ("", None):
            parts.append(f"候选池 {max_universe}")
        candidate_count = state.get("candidate_count")
        actionable_count = state.get(
            "paper_review_count", state.get("actionable_count")
        )
        focus_count = state.get("focus_count")
        watch_count = state.get("watch_count")
        blocked_count = state.get("blocked_count")
        protection_blocked = bool(state.get("protection_blocked"))
        if candidate_count not in ("", None):
            count_parts = [f"输出 {candidate_count}"]
            if focus_count not in ("", None):
                count_parts.append(f"强候选 {focus_count}")
            if actionable_count not in ("", None):
                count_parts.append(f"纸面复核 {actionable_count}")
            if watch_count not in ("", None):
                count_parts.append(f"观察 {watch_count}")
            if blocked_count not in ("", None):
                count_parts.append(f"阻塞 {blocked_count}")
            if protection_blocked:
                count_parts.append("组合保护")
            parts.append(" / ".join(count_parts))
        if updated_at:
            parts.append(f"更新 {updated_at}")
        if reason:
            parts.append(reason)
        return " / ".join(parts)

    @staticmethod
    def _walkforward_runtime_line_from_state(
        status: dict[str, Any],
        gate: dict[str, Any],
    ) -> str:
        status_value = str(status.get("status") or "").strip()
        updated_at = str(status.get("updated_at") or "").strip()[:10]
        effective_symbols = status.get("effective_symbols")
        gate_data_end = str(gate.get("data_end") or "").strip()
        both_pass = gate.get("both_pass")
        has_gate_sidecar = bool(gate)
        gate_label = (
            "双门已过"
            if both_pass is True
            else "DSR/PBO 未过门"
            if both_pass is False
            else "gate 未确认"
        )
        if not status_value and not gate:
            return ""
        status_label = {
            "blocked_resources": "资源不足阻塞",
            "timeout": "超时",
            "failed": "失败",
            "error": "错误",
            "running": "运行中",
            "blocked_running": "已有生产回测运行中",
            "dry_run": "预检完成",
            "blocked_db": "历史库缺失阻塞",
            "completed": "完成",
            "inspecting_coverage": "检查覆盖中",
            "preparing_child": "准备生产子进程",
        }.get(status_value, status_value)
        status_uses_existing_sidecar = status_value not in {"", "completed"}
        parts: list[str] = []
        if status_label:
            parts.append(f"生产回测 {status_label}")
        if updated_at:
            parts.append(f"更新 {updated_at}")
        if isinstance(effective_symbols, int):
            parts.append(f"覆盖 {effective_symbols} 标的")
        if status_uses_existing_sidecar and has_gate_sidecar:
            parts.append(f"沿用既有 gate sidecar: {gate_label}")
        else:
            parts.append(gate_label)
        if gate_data_end:
            parts.append(f"gate 数据至 {gate_data_end}")
        if status_value == "blocked_resources":
            parts.append("后续换更大机器或显式放行后再跑")
        elif status_value in {"timeout", "failed", "error", "blocked_running"}:
            parts.append("后续需重跑生产 walk-forward")
        elif both_pass is False:
            parts.append("后续看质量门修复")
        return "生产 gate: " + " / ".join(parts)

    def _runtime_gate_blocker_line(self, signal_date: str = "") -> str:
        selected_date = signal_date.strip()[:10]

        def _load() -> str:
            candidates: list[Path] = []
            env_path = os.getenv("AQSP_GATE_NOTIFY_STATE_PATH", "").strip()
            if env_path:
                candidates.append(Path(env_path))
            candidates.extend(
                (
                    self.ledger_path.parent / "gate_notify_state.json",
                    Path("data/gate_notify_state.json"),
                )
            )
            for path in candidates:
                if not path.exists():
                    continue
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                run_date = str(payload.get("run_date") or "").strip()[:10]
                if selected_date and run_date and run_date != selected_date:
                    continue
                line = self._gate_blocker_line_from_state(
                    payload,
                    signal_date=selected_date,
                )
                if line:
                    return line
            return ""

        return self._cache_value("runtime_gate_blocker_line", selected_date, _load)

    @staticmethod
    def _gate_blocker_line_from_state(
        payload: dict[str, Any],
        *,
        signal_date: str,
    ) -> str:
        fingerprint = ""
        sent_by_date = payload.get("sent_by_date")
        if isinstance(sent_by_date, dict) and sent_by_date:
            entry: object | None = None
            if signal_date:
                entry = sent_by_date.get(signal_date)
                if entry is None:
                    return ""
            if entry is None:
                latest_key = max(str(key) for key in sent_by_date)
                entry = sent_by_date.get(latest_key)
            if isinstance(entry, dict):
                fingerprint = str(entry.get("fingerprint") or "").strip()
            elif isinstance(entry, str):
                fingerprint = entry.strip()
        if not fingerprint:
            fingerprint = str(payload.get("fingerprint") or "").strip()
        labels = DashboardDataProvider._gate_fingerprint_labels(fingerprint)
        if not labels and isinstance(sent_by_date, dict):
            for key in sorted((str(item) for item in sent_by_date), reverse=True):
                entry = sent_by_date.get(key)
                candidate = ""
                if isinstance(entry, dict):
                    candidate = str(entry.get("fingerprint") or "").strip()
                elif isinstance(entry, str):
                    candidate = entry.strip()
                labels = DashboardDataProvider._gate_fingerprint_labels(candidate)
                if labels:
                    break
        if not labels:
            return ""
        return "双门 gate: " + " / ".join(dict.fromkeys(labels))

    @staticmethod
    def _gate_fingerprint_labels(fingerprint: str) -> tuple[str, ...]:
        return tuple(
            label
            for label in (
                DashboardDataProvider._gate_fingerprint_label(token)
                for token in fingerprint.split("|")
            )
            if label
        )

    @staticmethod
    def _gate_fingerprint_label(token: str) -> str:
        labels = {
            "cold_start": "冷启动样本未满",
            "dsr": "DSR 未过门",
            "pbo": "PBO 未过门",
            "sidecar_missing": "双门 sidecar 缺失",
            "sidecar_parse_failed": "双门 sidecar 解析失败",
            "run_date_invalid": "双门日期异常",
            "gate_stale": "双门结果过期",
            "n_periods_invalid": "有效回测周期不足",
            "data_end_invalid": "双门 data_end 异常",
            "heldout_contaminated": "held-out 边界污染",
            "market_coverage_missing": "全市场覆盖缺失",
            "market_coverage_insufficient": "全市场覆盖不足",
            "dsr_flag_invalid": "DSR 标志异常",
            "pbo_flag_invalid": "PBO 标志异常",
            "pbo_valid_flag_invalid": "PBO 有效性异常",
            "both_pass_flag_invalid": "双门总标志异常",
            "blocked_unknown": "质量门未放行",
        }
        return labels.get(str(token or "").strip(), "")

    def _coldstart_progress_from_ledger(self) -> str:
        signal_days = len(
            self._signal_dates(
                [
                    row
                    for row in self.load_signal_rows()
                    if not self._is_runtime_event_row(row)
                ]
            )
        )
        if signal_days <= 0:
            return ""
        return f"{signal_days}/{cold_start_min_days()}"

    @staticmethod
    def _coldstart_progress_from_lines(lines: tuple[str, ...]) -> str:
        for line in lines:
            match = re.search(r"冷启动[:：]\s*(\d+\s*/\s*\d+)", str(line))
            if match:
                return match.group(1).replace(" ", "")
        return ""

    @staticmethod
    def _coldstart_handoff_progress(state: dict[str, Any]) -> str:
        progress = str(state.get("progress") or "").strip().replace(" ", "")
        return progress if re.fullmatch(r"\d+/\d+", progress) else ""

    @staticmethod
    def _coldstart_handoff_line_from_state(state: dict[str, Any]) -> str:
        if str(state.get("status") or "").strip() != "ready":
            return ""
        progress = DashboardDataProvider._coldstart_handoff_progress(state)
        next_step = str(state.get("next_step") or "").strip()
        next_command = str(state.get("next_command") or "").strip()
        blocker = str(state.get("blocker") or "").strip()
        updated_at = str(state.get("updated_at") or "").strip()[:10]
        parts = ["冷启动交接: 样本门已达标"]
        if progress:
            parts.append(progress)
        if next_step:
            parts.append(f"下一步 {next_step}")
        if next_command:
            parts.append(f"入口 {next_command}")
        if blocker:
            parts.append(blocker)
        if updated_at:
            parts.append(f"更新 {updated_at}")
        return " / ".join(parts)

    @staticmethod
    def _coldstart_progress_ready(progress: str) -> bool:
        match = re.fullmatch(r"(\d+)/(\d+)", str(progress or "").strip())
        if not match:
            return False
        current, target = (int(match.group(1)), int(match.group(2)))
        return target > 0 and current >= target

    @staticmethod
    def _is_runtime_event_row(row: dict[str, Any]) -> bool:
        return (
            str(row.get("symbol", "") or "") == "__RUN__"
            or str(row.get("name", "") or "") == "run_event"
            or bool(row.get("event_type"))
        )

    def task_snapshots(
        self, signal_date: str = ""
    ) -> tuple[DashboardTaskSnapshot, ...]:
        selected_date = signal_date.strip()

        def _build() -> tuple[DashboardTaskSnapshot, ...]:
            snapshots: list[DashboardTaskSnapshot] = []
            for task_id, label in _TASK_LABELS.items():
                available_dates = self.task_dates(task_id)
                latest_date = available_dates[0] if available_dates else ""
                if selected_date:
                    if selected_date in available_dates:
                        snapshots.append(
                            self._lightweight_task_snapshot(
                                task_id=task_id,
                                task_label=label,
                                snapshot_date=selected_date,
                                latest_date=selected_date,
                            )
                        )
                        continue
                    snapshots.append(
                        DashboardTaskSnapshot(
                            task_id=task_id,
                            task_label=label,
                            latest_date=selected_date,
                            status_label="该日未产出",
                            headline=f"{label} {selected_date}: 该日没有独立落盘结果",
                            actionable_count=0,
                            watch_count=0,
                            blocked_count=0,
                        )
                    )
                    continue
                if latest_date:
                    snapshots.append(
                        self._lightweight_task_snapshot(
                            task_id=task_id,
                            task_label=label,
                            snapshot_date=latest_date,
                            latest_date=latest_date,
                        )
                    )
                    continue
                snapshots.append(
                    DashboardTaskSnapshot(
                        task_id=task_id,
                        task_label=label,
                        latest_date="",
                        status_label="未产出",
                        headline=f"{label}: 还没有真实落盘结果",
                        actionable_count=0,
                        watch_count=0,
                        blocked_count=0,
                    )
                )
            return tuple(snapshots)

        return self._cache_value("task_snapshots", selected_date, _build)

    def _lightweight_task_snapshot(
        self,
        *,
        task_id: str,
        task_label: str,
        snapshot_date: str,
        latest_date: str,
    ) -> DashboardTaskSnapshot:
        summary = self._lightweight_task_summary(task_id, snapshot_date)
        return DashboardTaskSnapshot(
            task_id=task_id,
            task_label=task_label,
            latest_date=latest_date,
            status_label=summary.status_label,
            headline=summary.headline,
            actionable_count=summary.actionable_count,
            watch_count=summary.watch_count,
            blocked_count=summary.blocked_count,
        )

    def _lightweight_task_summary(
        self,
        task_id: str,
        signal_date: str,
        *,
        include_report_insights: bool = True,
    ) -> DashboardTaskLiteSummary:
        selected_date = signal_date.strip()

        def _build() -> DashboardTaskLiteSummary:
            if task_id == "briefing":
                return self._lightweight_briefing_summary(
                    selected_date,
                    include_report_insights=include_report_insights,
                )
            if task_id == "closing_review":
                return self._lightweight_closing_review_summary(
                    selected_date,
                    include_report_insights=include_report_insights,
                )
            return self._lightweight_signal_task_summary(task_id, selected_date)

        return self._cache_value(
            "lightweight_task_summary",
            (task_id, selected_date, include_report_insights),
            _build,
        )

    def _lightweight_signal_task_summary(
        self,
        task_id: str,
        signal_date: str,
    ) -> DashboardTaskLiteSummary:
        rows = self._signal_task_rows_for_date(task_id, signal_date)
        actionable_rows = [
            row for row in rows if self._is_actionable(row, task_id=task_id)
        ]
        blocked_rows = [row for row in rows if self._is_blocked(row)]
        watch_rows = [row for row in rows if self._is_watch_only(row, task_id=task_id)]
        candidate_count = len(rows)
        actionable_count = len(actionable_rows)
        watch_count = len(watch_rows)
        blocked_count = len(blocked_rows)
        status_label = self._snapshot_status_label_from_counts(
            task_id=task_id,
            candidate_count=candidate_count,
            actionable_count=actionable_count,
            watch_count=watch_count,
            blocked_count=blocked_count,
        )
        headline = self._headline_for_signal_task(
            task_id=task_id,
            signal_date=signal_date,
            actionable_rows=actionable_rows,
            watch_rows=watch_rows,
            blocked_rows=blocked_rows,
        )
        return DashboardTaskLiteSummary(
            status_label=status_label,
            headline=normalize_research_tone(headline),
            phase_summary=self._task_phase_summary_from_counts(
                task_id=task_id,
                candidate_count=candidate_count,
                actionable_count=actionable_count,
                watch_count=watch_count,
                blocked_count=blocked_count,
            ),
            candidate_count=candidate_count,
            actionable_count=actionable_count,
            watch_count=watch_count,
            blocked_count=blocked_count,
            created_at=self._latest_created_at(rows),
        )

    def _intraday_live_ready(self, signal_date: str) -> bool:
        """Only a fresh intraday artifact may populate the live task lane."""
        selected_date = str(signal_date or "").strip()[:10]
        if selected_date and selected_date != today_shanghai().isoformat():
            # Explicit historical dates remain readable as archive records; the
            # homepage live lane is still gated separately below.
            return True
        live_view = self.live_candidate_view(signal_date=signal_date)
        return live_view.status == "fresh" and bool(live_view.candidates)

    def _lightweight_briefing_summary(
        self,
        signal_date: str,
        *,
        include_report_insights: bool = True,
    ) -> DashboardTaskLiteSummary:
        base = self._lightweight_signal_task_summary("main_chain", signal_date)
        if not include_report_insights:
            path = self.reports_dir / f"briefing-{signal_date}.md"
            mtime = self._report_file_mtime(path)
            status_label = "已产出" if mtime else "未产出"
            headline = (
                f"{_TASK_LABELS['briefing']} {signal_date}: 已归档"
                if mtime
                else f"{_TASK_LABELS['briefing']} {signal_date}: 还没有真实落盘结果"
            )
            return DashboardTaskLiteSummary(
                status_label=status_label,
                headline=normalize_research_tone(headline),
                phase_summary=self._task_phase_summary_from_counts(
                    task_id="briefing",
                    candidate_count=base.candidate_count,
                    actionable_count=base.actionable_count,
                    watch_count=base.watch_count,
                    blocked_count=base.blocked_count,
                ),
                candidate_count=base.candidate_count,
                actionable_count=base.actionable_count,
                watch_count=base.watch_count,
                blocked_count=base.blocked_count,
                created_at=mtime or base.created_at,
            )

        document = self._read_briefing_document(signal_date)
        insights = self._extract_report_insights(document.markdown)
        if insights.next_day_focus_lines:
            status_label = "待跟踪"
        elif document.markdown.strip():
            status_label = "已产出"
        else:
            status_label = "未产出"
        headline = f"{_TASK_LABELS['briefing']} {signal_date}".strip()
        if insights.next_day_focus_lines:
            headline = insights.next_day_focus_lines[0]
        return DashboardTaskLiteSummary(
            status_label=status_label,
            headline=normalize_research_tone(headline),
            phase_summary=self._task_phase_summary_from_counts(
                task_id="briefing",
                candidate_count=base.candidate_count,
                actionable_count=base.actionable_count,
                watch_count=base.watch_count,
                blocked_count=base.blocked_count,
            ),
            candidate_count=base.candidate_count,
            actionable_count=base.actionable_count,
            watch_count=base.watch_count,
            blocked_count=base.blocked_count,
            created_at=document.mtime or base.created_at,
        )

    def _lightweight_closing_review_summary(
        self,
        signal_date: str,
        *,
        include_report_insights: bool = True,
    ) -> DashboardTaskLiteSummary:
        document = (
            self._read_closing_review_document(signal_date)
            if include_report_insights
            else self._empty_report_document()
        )
        signal_rows = self._same_day_unique_rows(signal_date)
        paper_rows = [
            row
            for row in self.load_paper_rows()
            if str(row.get("signal_date", "") or "").strip() == signal_date
        ]
        closed_count = sum(1 for row in paper_rows if row.get("status") == "closed")
        blocked_count = sum(
            1 for row in paper_rows if row.get("status") == "not_executable"
        )
        candidate_count = len(signal_rows)
        watch_count = max(candidate_count - closed_count - blocked_count, 0)
        fast_report_mtime = ""
        if not include_report_insights:
            fast_report_mtime = self._closing_review_dated_report_mtime(signal_date)
        if document.markdown.strip() or fast_report_mtime:
            status_label = "已复盘"
            headline = f"{_TASK_LABELS['closing_review']} {signal_date}: 已归档"
        elif closed_count > 0 or blocked_count > 0:
            status_label = "已验证未归档"
            headline = f"{_TASK_LABELS['closing_review']} {signal_date}: 已验证未归档"
        elif candidate_count > 0:
            status_label = "待复盘"
            headline = f"{_TASK_LABELS['closing_review']} {signal_date}: 待复盘"
        else:
            status_label = "未产出"
            headline = (
                f"{_TASK_LABELS['closing_review']} {signal_date}: 还没有真实落盘结果"
            )
        return DashboardTaskLiteSummary(
            status_label=status_label,
            headline=normalize_research_tone(headline),
            phase_summary=self._task_phase_summary_from_counts(
                task_id="closing_review",
                candidate_count=candidate_count,
                actionable_count=closed_count,
                watch_count=watch_count,
                blocked_count=blocked_count,
            ),
            candidate_count=candidate_count,
            actionable_count=closed_count,
            watch_count=watch_count,
            blocked_count=blocked_count,
            created_at=document.mtime
            or fast_report_mtime
            or self._latest_created_at([dict(row) for row in signal_rows]),
        )

    def _signal_task_rows_for_date(
        self,
        task_id: str,
        signal_date: str,
    ) -> list[dict[str, Any]]:
        if task_id == "intraday" and not self._intraday_live_ready(signal_date):
            return []
        return self._dedupe_rows(
            [
                row
                for row in self._task_signal_rows(task_id)
                if str(row.get("signal_date", "") or "").strip() == signal_date
            ]
        )

    def _runtime_task_log_path(self, action: str, log_date: str) -> Path | None:
        if log_date:
            path = self.bt_logs_path / f"bt-{action}-{log_date}.log"
            return path if path.exists() else None
        matches = sorted(self.bt_logs_path.glob(f"bt-{action}-*.log"))
        if not matches:
            return None
        return max(matches, key=lambda path: (path.stat().st_mtime, path.name))

    def _runtime_task_log_candidates(
        self,
        log_date: str,
        *,
        limit: int | None,
    ) -> tuple[tuple[str, Path], ...]:
        candidates: list[tuple[str, Path, float]] = []
        for action in _RUNTIME_TASK_ORDER:
            path = self._runtime_task_log_path(action, log_date)
            if path is None:
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError as exc:
                logger.warning("读取 BT 任务日志状态失败: %s", exc)
                continue
            candidates.append((action, path, mtime))
        candidates.sort(key=lambda item: (item[2], item[1].name), reverse=True)
        if limit is not None:
            candidates = candidates[:limit]
        return tuple((action, path) for action, path, _mtime in candidates)

    def _parse_runtime_task_log(
        self,
        action: str,
        path: Path,
    ) -> DashboardRuntimeTaskRun | None:
        raw_lines = self._read_runtime_task_log_tail(path)
        if not raw_lines:
            return None
        lines = self._latest_runtime_run_segment(raw_lines)
        status_label = self._runtime_run_status_label(lines)
        headline = self._runtime_run_headline(lines, status_label=status_label)
        detail_lines = self._runtime_run_detail_lines(lines, headline=headline)
        return DashboardRuntimeTaskRun(
            action=action,
            task_label=_RUNTIME_TASK_LABELS.get(action, action),
            log_date=self._runtime_log_date_from_path(path),
            log_mtime=to_iso8601(
                datetime.fromtimestamp(path.stat().st_mtime, tz=SHANGHAI_TZ)
            ),
            status_label=status_label,
            headline=headline,
            detail_lines=detail_lines,
        )

    @staticmethod
    def _read_runtime_task_log_tail(
        path: Path,
        *,
        max_bytes: int = _RUNTIME_LOG_TAIL_BYTES,
    ) -> list[str]:
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                if size > max_bytes:
                    handle.seek(-max_bytes, os.SEEK_END)
                    handle.readline()
                raw = handle.read()
        except Exception as exc:
            logger.warning("读取 BT 任务日志失败: %s", exc)
            return []
        return raw.decode("utf-8", errors="replace").splitlines()

    @staticmethod
    def _latest_runtime_run_segment(lines: list[str]) -> tuple[str, ...]:
        start_index = 0
        markers = ("开始同步代码", "开始运行:", "冷启动日跑开始", "开始消息面雷达")
        for index, line in enumerate(lines):
            if any(marker in line for marker in markers):
                start_index = index
        return tuple(lines[start_index:])

    @staticmethod
    def _runtime_log_date_from_path(path: Path) -> str:
        match = re.search(r"bt-[^-]+-(\d{4}-\d{2}-\d{2})\.log$", path.name)
        return match.group(1) if match else ""

    def _runtime_run_status_label(self, lines: tuple[str, ...]) -> str:
        text = "\n".join(lines)
        if "组合保护" in text or "熔断器触发" in text:
            return "风控阻塞"
        if any(
            marker in text
            for marker in (
                "正常跳过",
                "周末跳过",
                "今日非交易日",
                "当前仍在收盘前",
                "未真实执行",
            )
        ):
            return "正常跳过"
        if any(
            marker in text for marker in ("[ERROR]", "数据错误:", "失败", "异常终止")
        ):
            return "失败"
        if any(
            marker in text
            for marker in (
                "冷启动日跑完成",
                "同步与跑批完成",
                "消息面雷达完成",
                "服务器监控结束",
                "✓ 跑批成功完成",
                "跑批成功完成",
            )
        ):
            return "完成"
        return "运行中或无结论"

    def _runtime_run_headline(
        self,
        lines: tuple[str, ...],
        *,
        status_label: str,
    ) -> str:
        preferred_keywords = {
            "风控阻塞": ("组合保护", "正常阻塞", "熔断器触发"),
            "正常跳过": ("正常跳过", "跳过"),
            "失败": ("[ERROR]", "数据错误:", "失败", "异常终止"),
            "完成": ("完成", "结束", "冷启动:", "同步与跑批完成"),
        }.get(status_label, ())
        cleaned = [
            self._clean_runtime_log_line(line)
            for line in lines
            if self._clean_runtime_log_line(line)
        ]
        if preferred_keywords:
            for line in reversed(cleaned):
                if any(keyword in line for keyword in preferred_keywords):
                    return line
        return cleaned[-1] if cleaned else "日志存在，但没有可读结论"

    def _runtime_run_detail_lines(
        self,
        lines: tuple[str, ...],
        *,
        headline: str,
        limit: int = 3,
    ) -> tuple[str, ...]:
        keywords = (
            "冷启动:",
            "target_day_symbols=",
            "跳过重复历史库更新",
            "组合保护",
            "正常跳过",
            "完成",
            "[ERROR]",
            "数据错误:",
        )
        details: list[str] = []
        for raw_line in reversed(lines):
            line = self._clean_runtime_log_line(raw_line)
            if not line or line == headline:
                continue
            if any(keyword in line for keyword in keywords) and line not in details:
                details.append(line)
            if len(details) >= limit:
                break
        details.reverse()
        return tuple(details)

    @staticmethod
    def _clean_runtime_log_line(line: str) -> str:
        text = re.sub(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]\s*", "", line)
        text = text.strip()
        if not text or set(text) <= {"=", "-", " "}:
            return ""
        return text

    def dashboard_dates(self) -> tuple[str, ...]:
        return self._all_dashboard_dates()

    def home_status(
        self,
        signal_date: str,
        *,
        overview: DashboardDateOverview | None = None,
    ) -> DashboardHomeStatus:
        """Return the compact status shown in the homepage left rail.

        This is presentation metadata only. It reads the existing overview and
        runtime freshness state; it never changes candidate scores or ratings.
        """
        selected_date = signal_date.strip()
        date_overview = overview or self.date_overview(selected_date)
        runtime = self.runtime_overview(selected_date)
        source = (
            str(runtime.effective_source or runtime.requested_source or "").strip()
            or "未记录"
        )
        source_missing = source == "未记录"
        source_label = (
            "实时源未记录"
            if source_missing
            else _runtime_live_source_boundary_label(source)
        )
        stale_source = not source_missing and not source_supports_workload(
            source, "live_short"
        )
        blocked = bool(
            date_overview.blocked_total
            or runtime.cooldown_until
            or runtime.gate_blocker_line
            or stale_source
        )
        if blocked:
            label = "阻塞"
            tone = "blocked"
        elif date_overview.actionable_total:
            label = "实时推荐"
            tone = "focus"
        elif date_overview.watch_total:
            label = "观察"
            tone = "watch"
        else:
            label = "等待刷新"
            tone = "waiting"
        data_day = runtime.data_latest_trade_date.strip() or selected_date or "未记录"
        lag = runtime.lag_days.strip()
        freshness = f"数据日 {data_day}"
        if lag:
            freshness += f" · 滞后 {lag} 天"
        detail = (
            f"{freshness} · 推荐 {date_overview.actionable_total} / "
            f"观察 {date_overview.watch_total} / 阻塞 {date_overview.blocked_total}"
        )
        return DashboardHomeStatus(
            label=label,
            detail=detail,
            tone=tone,
            actionable_count=date_overview.actionable_total,
            watch_count=date_overview.watch_total,
            blocked_count=date_overview.blocked_total,
            source_label=source_label,
        )

    def task_history_frame(self, task_id: str, limit: int = 8) -> pd.DataFrame:
        rows = self.task_history_rows(task_id, limit=limit)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "日期": row.signal_date,
                    "候选": row.candidate_count,
                    "待复核": row.actionable_count,
                    "观察": row.watch_count,
                    "阻塞": row.blocked_count,
                    "摘要": row.headline,
                }
                for row in rows
            ]
        )

    def task_history_rows(
        self,
        task_id: str,
        *,
        limit: int = 8,
    ) -> tuple[DashboardTaskHistoryRow, ...]:
        available_dates = self.task_dates(task_id)
        history: list[DashboardTaskHistoryRow] = []
        for signal_date in available_dates[:limit]:
            view = self._build_task_view_core(
                task_id,
                signal_date=signal_date,
                include_deltas=False,
            )
            history.append(
                DashboardTaskHistoryRow(
                    signal_date=signal_date,
                    candidate_count=view.candidate_count,
                    actionable_count=view.actionable_count,
                    watch_count=view.watch_count,
                    blocked_count=view.blocked_count,
                    headline=view.headline,
                )
            )
        return tuple(history)

    def timeline_rows(self, limit: int = 12) -> tuple[DashboardTimelineRow, ...]:
        def _build() -> tuple[DashboardTimelineRow, ...]:
            all_dates = self._all_dashboard_dates()
            rows: list[DashboardTimelineRow] = []
            for signal_date in all_dates:
                same_day_rows = self.same_day_task_rows(signal_date)
                if not same_day_rows:
                    continue
                task_labels = tuple(row.task_label for row in same_day_rows)
                headline = "；".join(
                    f"{row.task_label}: {row.status_label}" for row in same_day_rows[:3]
                )
                actionable_total, watch_total, blocked_total = (
                    self._same_day_unique_counts(signal_date)
                )
                rows.append(
                    DashboardTimelineRow(
                        signal_date=signal_date,
                        task_labels=task_labels,
                        actionable_total=actionable_total,
                        watch_total=watch_total,
                        blocked_total=blocked_total,
                        headline=headline,
                    )
                )
                if len(rows) >= limit:
                    break
            return tuple(rows)

        return self._cache_value("timeline_rows", limit, _build)

    def timeline_frame(self, limit: int = 12) -> pd.DataFrame:
        rows = self.timeline_rows(limit=limit)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "日期": row.signal_date,
                    "任务覆盖": "、".join(row.task_labels),
                    "待复核": row.actionable_total,
                    "观察": row.watch_total,
                    "阻塞": row.blocked_total,
                    "摘要": row.headline,
                }
                for row in rows
            ]
        )

    def same_day_task_rows(
        self,
        signal_date: str,
        *,
        include_report_insights: bool = True,
    ) -> tuple[DashboardSameDayTaskRow, ...]:
        selected_date = signal_date.strip()
        if not selected_date:
            return ()

        def _build() -> tuple[DashboardSameDayTaskRow, ...]:
            rows: list[DashboardSameDayTaskRow] = []
            for task_id, task_label in _TASK_LABELS.items():
                if selected_date not in self.task_dates(task_id):
                    continue
                summary = self._lightweight_task_summary(
                    task_id,
                    selected_date,
                    include_report_insights=include_report_insights,
                )
                rows.append(
                    DashboardSameDayTaskRow(
                        signal_date=selected_date,
                        task_id=task_id,
                        task_label=task_label,
                        phase_order=self._task_phase_order(task_id),
                        phase_label=self._task_phase_label(task_id),
                        phase_summary=summary.phase_summary,
                        status_label=summary.status_label,
                        headline=summary.headline,
                        candidate_count=summary.candidate_count,
                        actionable_count=summary.actionable_count,
                        watch_count=summary.watch_count,
                        blocked_count=summary.blocked_count,
                        created_at=summary.created_at,
                    )
                )
            rows.sort(key=lambda row: (row.phase_order, row.task_label))
            return tuple(rows)

        return self._cache_value(
            "same_day_task_rows",
            (selected_date, include_report_insights),
            _build,
        )

    def same_day_task_frame(self, signal_date: str) -> pd.DataFrame:
        rows = self.same_day_task_rows(signal_date)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "任务": row.task_label,
                    "状态": row.status_label,
                    "候选": row.candidate_count,
                    "待复核": row.actionable_count,
                    "观察": row.watch_count,
                    "阻塞": row.blocked_count,
                    "摘要": row.headline,
                }
                for row in rows
            ]
        )

    def same_day_candidate_spotlights(
        self,
        signal_date: str,
        *,
        limit: int = 8,
    ) -> tuple[DashboardCandidateSpotlight, ...]:
        selected_date = signal_date.strip()
        if not selected_date:
            return ()

        def _build() -> tuple[DashboardCandidateSpotlight, ...]:
            grouped: dict[str, dict[str, Any]] = {}
            for task_id in (*_SIGNAL_TASK_IDS, *_OBSERVATION_TASK_IDS):
                rows = [
                    row
                    for row in self._dedupe_rows(self._task_signal_rows(task_id))
                    if str(row.get("signal_date", "") or "") == selected_date
                ]
                for row in rows:
                    symbol = str(row.get("symbol", "") or "").strip()
                    if not symbol:
                        continue
                    existing = grouped.get(symbol)
                    if existing is None:
                        grouped[symbol] = {
                            "row": row,
                            "task_id": task_id,
                            "task_labels": [self._task_label(task_id)],
                            "entries": [{"row": row, "task_id": task_id}],
                        }
                        continue
                    existing["entries"].append({"row": row, "task_id": task_id})
                    if self._same_day_spotlight_key(
                        row,
                        task_id,
                    ) > self._same_day_spotlight_key(
                        existing["row"],
                        str(existing.get("task_id", "") or ""),
                    ):
                        existing["row"] = row
                        existing["task_id"] = task_id
                    task_label = self._task_label(task_id)
                    if task_label not in existing["task_labels"]:
                        existing["task_labels"].append(task_label)

            spotlight_payloads = [
                {
                    "spotlight": self._build_same_day_spotlight(symbol, payload),
                    "merged_row": self._same_day_merged_row(
                        payload["row"],
                        payload.get("entries", ()),
                    ),
                }
                for symbol, payload in grouped.items()
            ]
            spotlight_payloads.sort(key=lambda item: item["spotlight"].display_name)
            spotlight_payloads.sort(
                key=lambda item: (
                    self._candidate_sort_key(item["merged_row"]),
                    len(item["spotlight"].task_labels),
                ),
                reverse=True,
            )
            return tuple(item["spotlight"] for item in spotlight_payloads[:limit])

        return self._cache_value(
            "same_day_candidate_spotlights",
            (selected_date, limit),
            _build,
        )

    def candidate_review_cards(
        self,
        signal_date: str,
    ) -> tuple[DashboardCandidateCard, ...]:
        selected_date = signal_date.strip()
        if not selected_date:
            return ()
        return self._build_detail_cards(
            list(self._same_day_unique_rows(selected_date)),
            limit=None,
        )

    def same_day_candidate_journey(
        self,
        signal_date: str,
        symbol: str,
    ) -> tuple[DashboardCandidateJourneyStep, ...]:
        selected_date = signal_date.strip()
        selected_symbol = symbol.strip()
        if not selected_date or not selected_symbol:
            return ()

        def _build() -> tuple[DashboardCandidateJourneyStep, ...]:
            steps: list[DashboardCandidateJourneyStep] = []
            for task_id in ("main_chain", "morning_breakout", "closing_premium"):
                rows = [
                    row
                    for row in self._dedupe_rows(self._task_signal_rows(task_id))
                    if str(row.get("signal_date", "") or "") == selected_date
                    and str(row.get("symbol", "") or "").strip() == selected_symbol
                ]
                if not rows:
                    continue
                row = max(rows, key=self._row_meta_key)
                steps.append(
                    DashboardCandidateJourneyStep(
                        task_id=task_id,
                        task_label=self._task_label(task_id),
                        phase_label=self._task_phase_label(task_id),
                        score=float(row.get("score") or 0.0),
                        action_label=self._action_label(row),
                        status_label=self._candidate_status(row),
                        blocker=self._candidate_blocker_text(row),
                        next_step=str(row.get("candidate_next_step", "") or "").strip(),
                        review_meta=self._review_meta(row),
                        reasons=self._as_text_tuple(row.get("reasons")),
                        risks=self._as_text_tuple(row.get("risks")),
                    )
                )
            return tuple(steps)

        return self._cache_value(
            "same_day_candidate_journey",
            (selected_date, selected_symbol),
            _build,
        )

    def date_overview(
        self,
        signal_date: str,
        *,
        rows: tuple[DashboardSameDayTaskRow, ...] | None = None,
        spotlights: tuple[DashboardCandidateSpotlight, ...] | None = None,
        debates: tuple[DashboardDebateSummary, ...] | None = None,
    ) -> DashboardDateOverview:
        selected_date = signal_date.strip()

        def _build() -> DashboardDateOverview:
            same_day_rows = (
                rows if rows is not None else self.same_day_task_rows(selected_date)
            )
            if not same_day_rows:
                return DashboardDateOverview(
                    signal_date=selected_date,
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

            focus_debates = (
                debates[:1]
                if debates is not None
                else self.prioritized_debate_summaries(
                    selected_date,
                    limit=1,
                    salient_only=True,
                )
            )
            focus_spotlights = (
                spotlights[:1]
                if spotlights is not None
                else self.same_day_candidate_spotlights(selected_date, limit=1)
            )
            ordered_rows = sorted(
                same_day_rows,
                key=lambda row: (
                    row.actionable_count > 0,
                    row.blocked_count > 0,
                    row.watch_count > 0,
                    row.actionable_count,
                    row.blocked_count,
                    row.watch_count,
                    -row.phase_order,
                ),
                reverse=True,
            )
            top_row = ordered_rows[0]
            blocker_row = next(
                (row for row in same_day_rows if row.blocked_count > 0), None
            )
            signal_rows = tuple(
                row for row in same_day_rows if row.task_id in _SIGNAL_TASK_IDS
            )
            focus_candidates = signal_rows or same_day_rows
            focus_row = next(
                (row for row in focus_candidates if row.actionable_count > 0),
                next(
                    (row for row in focus_candidates if row.blocked_count > 0),
                    next(
                        (row for row in focus_candidates if row.watch_count > 0),
                        top_row,
                    ),
                ),
            )
            actionable_total, watch_total, blocked_total = self._same_day_unique_counts(
                selected_date
            )
            focus_headline = focus_row.headline
            if focus_debates:
                lead_debate = focus_debates[0]
                debate_lead = (
                    lead_debate.research_verdict.strip()
                    or lead_debate.consensus.strip()
                    or lead_debate.primary_risk_gate.strip()
                    or lead_debate.next_trigger.strip()
                    or lead_debate.recommended_adjustment_label.strip()
                )
                debate_followup = (
                    lead_debate.next_trigger.strip()
                    or lead_debate.primary_risk_gate.strip()
                )
                focus_headline = " | ".join(
                    part
                    for part in (
                        lead_debate.display_name,
                        normalize_research_tone(debate_lead),
                        (
                            normalize_research_tone(debate_followup)
                            if debate_followup and debate_followup != debate_lead
                            else ""
                        ),
                    )
                    if part
                )
            elif focus_spotlights:
                lead_spotlight = focus_spotlights[0]
                spotlight_lead = (
                    lead_spotlight.cross_market_summary.strip()
                    or " / ".join(
                        part
                        for part in (
                            lead_spotlight.action_label.strip(),
                            lead_spotlight.status_label.strip(),
                        )
                        if part
                    )
                    or lead_spotlight.blocker.strip()
                    or lead_spotlight.next_step.strip()
                )
                spotlight_followup = (
                    lead_spotlight.blocker.strip() or lead_spotlight.next_step.strip()
                )
                focus_headline = " | ".join(
                    part
                    for part in (
                        lead_spotlight.display_name,
                        normalize_research_tone(spotlight_lead),
                        (
                            normalize_research_tone(spotlight_followup)
                            if spotlight_followup
                            and spotlight_followup != spotlight_lead
                            else ""
                        ),
                    )
                    if part
                )
            return DashboardDateOverview(
                signal_date=selected_date,
                task_count=len(same_day_rows),
                actionable_total=actionable_total,
                watch_total=watch_total,
                blocked_total=blocked_total,
                top_task_label=top_row.task_label,
                top_headline=top_row.headline,
                blocker_headline=blocker_row.headline
                if blocker_row is not None
                else "",
                focus_headline=focus_headline,
                workflow_summary=self._workflow_summary(same_day_rows),
                archive_summary=self._archive_summary(
                    same_day_rows, focus_row, blocker_row
                ),
            )

        if rows is None and spotlights is None and debates is None:
            return self._cache_value("date_overview", selected_date, _build)
        return _build()

    def preferred_task_for_date(self, signal_date: str) -> str:
        same_day_rows = self.same_day_task_rows(signal_date)
        if not same_day_rows:
            return self.default_task_id()
        for preferred_task_id in (
            "intraday",
            "main_chain",
            "morning_breakout",
            "closing_premium",
            "closing_review",
            "briefing",
        ):
            for row in same_day_rows:
                if row.task_id == preferred_task_id:
                    return preferred_task_id
        return same_day_rows[0].task_id

    def paper_summary(self, signal_date: str = "") -> DashboardPaperSummary:
        rows = self.load_paper_rows()
        selected_date = signal_date.strip()
        open_rows = [row for row in rows if row.get("status") == "open"]
        scoped_rows = rows
        if selected_date:
            scoped_rows = [
                row
                for row in rows
                if str(row.get("signal_date", "") or "").strip() == selected_date
            ]
        pending_rows = [
            row for row in scoped_rows if row.get("status") == "pending_entry"
        ]
        blocked_rows = [
            row for row in scoped_rows if row.get("status") == "not_executable"
        ]
        closed_rows = [row for row in scoped_rows if row.get("status") == "closed"]
        execution_rows = (
            self.execution_logs_for_date(selected_date)
            if selected_date
            else self.get_recent_execution_logs(days=7)
        )

        open_position_lines = tuple(
            (
                f"{self._symbol_name(row)} | 入场 {row.get('entry_date', '-')}"
                f" | 止损 {row.get('stop_loss', '-')}"
                f" | 止盈 {row.get('take_profit', '-')}"
            )
            for row in open_rows[:5]
        )

        event_lines: list[str] = []
        if pending_rows:
            event_lines.append(
                f"纸面入场假设 {len(pending_rows)} 笔，等待下一交易日开盘价验证。"
            )
        if blocked_rows:
            event_lines.append(
                f"不可成交 {len(blocked_rows)} 笔，最新阻塞: "
                f"{self._symbol_name(blocked_rows[-1])} | "
                f"{blocked_rows[-1].get('not_executable_reason', '未知原因')}"
            )
        if closed_rows:
            latest_closed = closed_rows[-1]
            event_lines.append(
                f"最近纸面关闭: {self._symbol_name(latest_closed)} | "
                f"纸面收益 {latest_closed.get('return_pct', '-')}"
            )

        action_summary_lines: list[str] = []
        if execution_rows:
            latest_execution = execution_rows[-1]
            action_summary_lines.append(
                f"最近纸面回写: {self._display_name_for_symbol(latest_execution)} | "
                f"{latest_execution.get('action', '-')} "
                f"{latest_execution.get('shares', '-')}"
                f" @ {latest_execution.get('price', '-')}"
            )
        if pending_rows:
            action_summary_lines.append(
                f"纸面入场待核对 {len(pending_rows)} 笔，开盘优先检查下一交易日开盘价是否可成交。"
            )
        if blocked_rows:
            action_summary_lines.append(
                f"阻塞队列 {len(blocked_rows)} 笔，先处理涨跌停/停牌导致的不可成交样本。"
            )
        if not action_summary_lines and open_rows:
            action_summary_lines.append(
                f"当前以纸面持有假设跟踪为主，共 {len(open_rows)} 笔。"
            )
        if selected_date and not any(
            [pending_rows, blocked_rows, closed_rows, execution_rows]
        ):
            action_summary_lines = (
                f"{selected_date} 暂无虚拟盘纸面事件，当前以研究判断与纸面持有跟踪为主。",
            )
            event_lines = [f"{selected_date} 暂无纸面入场、阻塞或关闭事件。"]

        return DashboardPaperSummary(
            signal_date=selected_date,
            open_positions=len(open_rows),
            pending_entries=len(pending_rows),
            not_executable=len(blocked_rows),
            closed_trades=len(closed_rows),
            open_position_lines=open_position_lines,
            event_lines=tuple(event_lines),
            action_summary_lines=tuple(action_summary_lines),
        )

    def execution_focus(
        self,
        *,
        signal_date: str,
        symbol: str,
        task_id: str = "main_chain",
    ) -> DashboardExecutionFocus:
        selected_date = signal_date.strip()
        selected_symbol = symbol.strip()
        preferred_task_id = task_id
        research_context = self.candidate_research_context(
            signal_date=selected_date,
            symbol=selected_symbol,
            preferred_task_id=preferred_task_id,
        )
        signal_row = (
            research_context["row"]
            if research_context is not None
            and isinstance(research_context.get("row"), dict)
            else None
        )

        all_symbol_rows = [
            row
            for row in self.load_paper_rows()
            if str(row.get("symbol", "") or "").strip() == selected_symbol
        ]
        paper_rows = all_symbol_rows
        if selected_date:
            paper_rows = [
                row
                for row in all_symbol_rows
                if str(row.get("signal_date", "") or "").strip() == selected_date
            ]
        current_open_rows = [
            row for row in all_symbol_rows if row.get("status") == "open"
        ]
        if selected_date:
            open_rows = [
                row
                for row in current_open_rows
                if str(row.get("signal_date", "") or "").strip() == selected_date
            ]
        else:
            open_rows = current_open_rows
        pending_rows = [
            row for row in paper_rows if row.get("status") == "pending_entry"
        ]
        blocked_rows = [
            row for row in paper_rows if row.get("status") == "not_executable"
        ]
        closed_rows = [row for row in paper_rows if row.get("status") == "closed"]
        execution_rows = self._execution_logs_for_signal_context(
            signal_date=selected_date,
            symbol=selected_symbol,
            paper_rows=paper_rows,
        )

        display_name = (
            self._symbol_name(signal_row)
            if signal_row is not None
            else self._display_name_for_symbol({"symbol": selected_symbol})
        )
        paper_context_row = next(
            (
                row
                for row in [*paper_rows, *all_symbol_rows]
                if any(
                    str(row.get(field, "") or "").strip()
                    for field in (
                        "portfolio_action",
                        "candidate_status",
                        "candidate_blocker",
                        "candidate_next_step",
                        "candidate_review_window",
                        "candidate_review_priority",
                    )
                )
            ),
            None,
        )
        context_row = signal_row or paper_context_row
        review_meta = self._review_meta(signal_row) if signal_row is not None else ""
        next_step = (
            str(signal_row.get("candidate_next_step", "") or "").strip()
            if signal_row is not None
            else ""
        )
        blocker = (
            self._candidate_blocker_text(signal_row) if signal_row is not None else ""
        )
        if context_row is not None and signal_row is None:
            review_meta = self._review_meta(context_row)
            next_step = str(context_row.get("candidate_next_step", "") or "").strip()
            blocker = self._candidate_blocker_text(context_row)

        research_lines: list[str] = []
        if context_row is not None:
            if (
                signal_row is not None
                and research_context is not None
                and research_context["task_id"] != preferred_task_id
            ):
                research_lines.append(
                    "研究来源: "
                    f"{research_context['task_label']} / {research_context['phase_label']}"
                )
            elif signal_row is None:
                research_lines.append("研究来源: paper ledger 继承上下文")
            research_lines.extend(
                [
                    f"研究动作: {self._action_status_text(context_row)}",
                    f"评分 {float(context_row.get('score') or 0.0):.1f}",
                ]
            )
            if review_meta:
                research_lines.append(f"再看时间: {review_meta}")
            if next_step:
                research_lines.append(f"研究下一步: {next_step}")
            elif blocker:
                research_lines.append(normalize_research_tone(f"当前限制: {blocker}"))
            research_lines.extend(self._execution_focus_cross_market_lines(context_row))
            debate_active_role_summary = str(
                context_row.get("debate_active_role_summary", "") or ""
            ).strip()
            support_points = self._as_text_tuple(context_row.get("support_points"))
            opposition_points = self._as_text_tuple(
                context_row.get("opposition_points")
            )
            watch_items = self._as_text_tuple(context_row.get("watch_items"))
            role_reliability_lines = self._as_text_tuple(
                context_row.get("role_reliability_lines")
            )
            if debate_active_role_summary:
                research_lines.append(f"讨论视角: {debate_active_role_summary}")
            if support_points:
                research_lines.append(f"支持观点: {support_points[0]}")
            if opposition_points:
                research_lines.append(f"反对观点: {opposition_points[0]}")
            if watch_items:
                research_lines.append(f"待确认: {watch_items[0]}")
            if role_reliability_lines:
                research_lines.append(f"角色可信度: {role_reliability_lines[0]}")
        else:
            research_lines.append("该标的当前不在研究候选中，主要从纸面记录回看。")

        readiness_lines: list[str] = []
        if pending_rows:
            readiness_lines.append(
                f"纸面入场假设 {len(pending_rows)} 笔，开盘先核对下一交易日开盘价是否可成交。"
            )
            if blocker:
                readiness_lines.append(normalize_research_tone(f"当前限制: {blocker}"))
        if blocked_rows:
            latest_blocked = blocked_rows[-1]
            blocked_reason = str(
                latest_blocked.get("candidate_blocker")
                or latest_blocked.get("not_executable_reason")
                or "未知原因"
            ).strip()
            readiness_lines.append(
                f"阻塞 {len(blocked_rows)} 笔，最新原因: {blocked_reason}"
            )
        if not readiness_lines:
            if context_row is not None and not open_rows and not execution_rows:
                if blocker:
                    readiness_lines.append(
                        f"研究已产出，但当前被{blocker}拦住，暂不进入纸面入场验证链路。"
                    )
                else:
                    readiness_lines.append("研究已产出，但尚未进入纸面入场或阻塞队列。")
            else:
                readiness_lines.append("当前没有新的纸面入场或不可成交事件。")

        execution_lines: list[str] = []
        if execution_rows:
            latest_execution = execution_rows[-1]
            latest_timestamp = str(latest_execution.get("timestamp", "") or "").strip()
            same_day_execution_count = sum(
                1
                for row in execution_rows
                if selected_date
                and str(row.get("timestamp", "") or "").startswith(selected_date)
            )
            execution_log_label = (
                "同日纸面验证日志"
                if same_day_execution_count == len(execution_rows)
                else "关联纸面验证日志"
            )
            execution_lines.append(
                f"最近纸面回写: {latest_execution.get('action', '-')} "
                f"{latest_execution.get('shares', '-')} @ {latest_execution.get('price', '-')}"
                f"{f' / {latest_timestamp}' if latest_timestamp else ''}"
            )
            execution_lines.append(f"{execution_log_label} {len(execution_rows)} 条。")
        elif pending_rows or blocked_rows:
            execution_lines.append(
                "当前仍停留在纸面入场/阻塞阶段，尚未形成纸面验证日志。"
            )
        else:
            execution_lines.append("当前暂无纸面日志，仍以研究判断为主。")

        holding_lines: list[str] = []
        if open_rows:
            latest_open = open_rows[-1]
            holding_lines.append(
                f"本日绑定纸面持有 {len(open_rows)} 笔，最近入场: "
                f"{latest_open.get('entry_date', '-')}"
            )
            holding_lines.append(
                f"止损 {latest_open.get('stop_loss', '-')} / 止盈 {latest_open.get('take_profit', '-')}"
            )
        if closed_rows:
            latest_closed = closed_rows[-1]
            holding_lines.append(
                f"最近纸面关闭: {latest_closed.get('exit_date', '-')}"
                f" / 纸面收益 {latest_closed.get('return_pct', '-')}"
            )
        if selected_date and not open_rows and current_open_rows and not closed_rows:
            holding_lines.append(
                f"纸面 ledger 有未绑定 {selected_date} 信号日的持有假设，未作为当日验证证据。"
            )
        if not holding_lines:
            holding_lines.append("当前没有纸面持有假设，也没有当日关闭回写。")

        if open_rows:
            holding_status = "纸面持有跟踪中"
        elif closed_rows:
            holding_status = "已完成一轮纸面关闭"
        elif selected_date and current_open_rows:
            holding_status = "纸面持有未绑定本日"
        else:
            holding_status = "尚未形成纸面持有"

        if execution_rows:
            execution_status = "已有纸面验证"
        elif pending_rows:
            execution_status = "等待开盘验证"
        elif blocked_rows:
            execution_status = "可成交性受阻"
        else:
            execution_status = "尚未进入执行"

        if context_row is None:
            research_status = "缺少结论"
        elif blocker:
            research_status = "存在阻塞"
        elif next_step:
            research_status = "待确认"
        else:
            research_status = "已落盘"

        return DashboardExecutionFocus(
            symbol=selected_symbol,
            display_name=display_name,
            research_status=research_status,
            execution_status=execution_status,
            holding_status=holding_status,
            research_lines=tuple(research_lines),
            readiness_lines=tuple(readiness_lines),
            execution_lines=tuple(execution_lines),
            holding_lines=tuple(holding_lines),
        )

    def default_task_id(self) -> str:
        return "main_chain"

    def task_dates(self, task_id: str) -> tuple[str, ...]:
        def _build() -> tuple[str, ...]:
            if task_id == "briefing":
                return self._briefing_dates()
            if task_id == "closing_review":
                return self._closing_review_dates()
            return self._signal_dates(self._task_signal_rows(task_id))

        return self._cache_value("task_dates", task_id, _build)

    def _closing_review_dates(self) -> tuple[str, ...]:
        dates = set(
            self._signal_dates(
                [
                    row
                    for row in self.load_signal_rows()
                    if not self._is_runtime_event_row(row)
                ]
            )
        )
        if self.reports_dir.exists():
            patterns = (
                "closing_review-*.md",
                "closing-review-*.md",
                "daily-review-*.md",
            )
            for pattern in patterns:
                for path in self.reports_dir.glob(pattern):
                    matched = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
                    if matched:
                        dates.add(matched.group(1))
        return tuple(sorted((item for item in dates if item), reverse=True))

    def build_task_view(self, task_id: str, signal_date: str = "") -> DashboardTaskView:
        return self._build_task_view_core(
            task_id,
            signal_date=signal_date,
            include_deltas=True,
        )

    def home_digest_payload(
        self,
        task_id: str,
        signal_date: str = "",
    ) -> DashboardHomeDigestPayload:
        """Build the homepage payload once so first paint does not fan out reads."""
        normalized_task = task_id if task_id in _TASK_LABELS else self.default_task_id()
        normalized_signal_date = signal_date.strip()

        def _build() -> DashboardHomeDigestPayload:
            task_view = self.build_task_digest_view(
                normalized_task,
                signal_date=normalized_signal_date,
            )
            review_date = (
                task_view.selected_date
                or task_view.latest_date
                or normalized_signal_date
            )
            rows = self.same_day_task_rows(
                review_date,
                include_report_insights=False,
            )
            live_view = self.live_candidate_view(signal_date=review_date)
            if normalized_task == "intraday":
                # A missing/expired live artifact is an observation/block state;
                # never substitute a historical main-chain candidate here.
                if getattr(live_view, "status", "unknown") != "fresh":
                    rows = tuple(
                        row
                        for row in rows
                        if str(getattr(row, "task_id", "") or "") == "intraday"
                    )
                spotlights = (
                    self.live_candidate_spotlights(signal_date=review_date)
                    if live_view.candidates
                    else ()
                )
            else:
                use_live_view = bool(
                    live_view.candidates
                    and not normalized_signal_date
                    and live_view.artifact_date == review_date
                )
                spotlights = (
                    self.live_candidate_spotlights(signal_date=review_date)
                    if use_live_view
                    else self.same_day_candidate_spotlights(review_date, limit=3)
                )
            candidate_symbols = tuple(
                str(getattr(card, "symbol", "") or "").strip()
                for card in getattr(task_view, "detail_cards", ())
            ) + tuple(
                str(getattr(spotlight, "symbol", "") or "").strip()
                for spotlight in spotlights
            )
            debates = (
                ()
                if normalized_task == "intraday"
                and getattr(live_view, "status", "unknown") != "fresh"
                else self.prioritized_debate_summaries(
                    review_date,
                    limit=3,
                    salient_only=False,
                    task_id=normalized_task,
                    symbols=candidate_symbols,
                )
            )
            debates = self._prioritize_debates_for_candidate_symbols(
                debates,
                candidate_symbols=candidate_symbols,
                limit=3,
            )
            if rows:
                overview = self.date_overview(
                    review_date,
                    rows=rows,
                    spotlights=spotlights,
                    debates=debates,
                )
                paper_summary = self.paper_summary(review_date)
            else:
                spotlights = ()
                overview = DashboardDateOverview(
                    signal_date=review_date,
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
                paper_summary = DashboardPaperSummary(
                    signal_date=review_date,
                    open_positions=0,
                    pending_entries=0,
                    not_executable=0,
                    closed_trades=0,
                    open_position_lines=(),
                    event_lines=(),
                    action_summary_lines=(),
                )
            return DashboardHomeDigestPayload(
                task_view=task_view,
                same_day_rows=rows,
                spotlights=spotlights,
                debates=debates,
                overview=overview,
                paper_summary=paper_summary,
            )

        return self._cache_value(
            "home_digest_payload",
            (normalized_task, normalized_signal_date),
            _build,
        )

    @staticmethod
    def _prioritize_debates_for_candidate_symbols(
        debates: tuple[DashboardDebateSummary, ...],
        *,
        candidate_symbols: tuple[str, ...],
        limit: int,
    ) -> tuple[DashboardDebateSummary, ...]:
        candidate_order = {
            symbol: index for index, symbol in enumerate(candidate_symbols) if symbol
        }
        ordered = sorted(
            enumerate(debates),
            key=lambda item: (
                candidate_order.get(item[1].symbol, len(candidate_order)),
                item[0],
            ),
        )
        return tuple(item for _, item in ordered[:limit])

    def build_task_digest_view(
        self,
        task_id: str,
        signal_date: str = "",
    ) -> DashboardTaskView:
        normalized_task = task_id if task_id in _TASK_LABELS else self.default_task_id()
        normalized_signal_date = signal_date.strip()

        def _build() -> DashboardTaskView:
            available_dates = self.task_dates(normalized_task)
            selected_date = normalized_signal_date or (
                available_dates[0] if available_dates else ""
            )
            previous_date = self._previous_date(
                available_dates=available_dates,
                selected_date=selected_date,
            )
            summary = self._lightweight_task_summary(
                normalized_task,
                selected_date,
                include_report_insights=False,
            )
            rows = (
                self._signal_task_rows_for_date(normalized_task, selected_date)
                if normalized_task in (*_SIGNAL_TASK_IDS, *_OBSERVATION_TASK_IDS)
                else []
            )
            actionable_rows = [
                row for row in rows if self._is_actionable(row, task_id=normalized_task)
            ]
            blocked_rows = [row for row in rows if self._is_blocked(row)]
            watch_rows = [
                row for row in rows if self._is_watch_only(row, task_id=normalized_task)
            ]
            recommendation_lines = tuple(
                self._recommendation_line(row) for row in actionable_rows[:3]
            )
            watchlist_lines = tuple(self._watch_line(row) for row in watch_rows[:3])
            blocker_lines = tuple(self._blocker_line(row) for row in blocked_rows[:3])
            review_lines = tuple(
                self._review_line(row) for row in rows[:3] if self._review_line(row)
            )
            agenda_lines = self._build_agenda_lines(
                recommendation_lines=recommendation_lines,
                blocker_lines=blocker_lines,
                review_lines=review_lines,
                focus_lines=(),
            )
            runtime_lines = (
                self._ledger_market_context_runtime_lines(
                    task_id=normalized_task,
                    signal_date=selected_date,
                )
                if normalized_task in (*_SIGNAL_TASK_IDS, *_OBSERVATION_TASK_IDS)
                else ()
            )
            return DashboardTaskView(
                task_id=normalized_task,
                task_label=_TASK_LABELS[normalized_task],
                selected_date=selected_date,
                latest_date=available_dates[0] if available_dates else "",
                previous_date=previous_date,
                available_dates=available_dates,
                headline=normalize_research_tone(summary.headline),
                summary_lines=(
                    (normalize_research_tone(summary.phase_summary),)
                    if summary.phase_summary
                    else ()
                ),
                lifecycle_lines=(),
                unlock_lines=(),
                report_summary_lines=(),
                runtime_lines=tuple(
                    normalize_research_tone(line) for line in runtime_lines
                ),
                delta_lines=(),
                agenda_lines=tuple(
                    normalize_research_tone(line) for line in agenda_lines
                ),
                recommendation_lines=tuple(
                    normalize_research_tone(line) for line in recommendation_lines
                ),
                watchlist_lines=tuple(
                    normalize_research_tone(line) for line in watchlist_lines
                ),
                blocker_lines=tuple(
                    normalize_research_tone(line) for line in blocker_lines
                ),
                review_lines=tuple(
                    normalize_research_tone(line) for line in review_lines
                ),
                next_day_focus_lines=(),
                report_markdown="",
                report_source="",
                report_mtime="",
                source_status=(
                    self.latest_source_status(
                        task_id=normalized_task,
                        signal_date=selected_date,
                    )
                    if normalized_task in (*_SIGNAL_TASK_IDS, *_OBSERVATION_TASK_IDS)
                    else {}
                ),
                candidate_count=summary.candidate_count,
                actionable_count=summary.actionable_count,
                watch_count=summary.watch_count,
                blocked_count=summary.blocked_count,
                detail_cards=(),
                ranking_lines=(),
                market_environment="",
                strategy_breakdown_lines=(),
                lesson_lines=(),
                improvement_lines=(),
            )

        return self._cache_value(
            "build_task_digest_view",
            (normalized_task, normalized_signal_date),
            _build,
        )

    def _build_task_view_core(
        self,
        task_id: str,
        *,
        signal_date: str = "",
        include_deltas: bool,
    ) -> DashboardTaskView:
        normalized_task = task_id if task_id in _TASK_LABELS else self.default_task_id()
        normalized_signal_date = signal_date.strip()

        def _build() -> DashboardTaskView:
            available_dates = self.task_dates(normalized_task)
            selected_date = normalized_signal_date or (
                available_dates[0] if available_dates else ""
            )

            if normalized_task == "closing_review":
                return self._build_closing_review_view(
                    selected_date=selected_date,
                    available_dates=available_dates,
                    include_deltas=include_deltas,
                )
            if normalized_task == "briefing":
                return self._build_briefing_view(
                    selected_date=selected_date,
                    available_dates=available_dates,
                    include_deltas=include_deltas,
                )
            return self._build_signal_task_view(
                task_id=normalized_task,
                selected_date=selected_date,
                available_dates=available_dates,
                include_deltas=include_deltas,
            )

        return self._cache_value(
            "build_task_view_core",
            (normalized_task, normalized_signal_date, include_deltas),
            _build,
        )

    def latest_signal_frame(
        self,
        limit: int = 20,
        *,
        task_id: str = "main_chain",
        signal_date: str = "",
    ) -> pd.DataFrame:
        rows = self._task_signal_rows(task_id)
        if signal_date.strip():
            rows = [
                row
                for row in rows
                if str(row.get("signal_date", "") or "") == signal_date.strip()
            ]
        else:
            latest_signal_date = self._max_signal_date(rows)
            rows = [
                row
                for row in rows
                if str(row.get("signal_date", "") or "") == latest_signal_date
            ]
        rows = self._dedupe_rows(rows)
        rows.sort(key=self._sort_key, reverse=True)
        table = [
            {
                "日期": row.get("signal_date", ""),
                "代码": row.get("symbol", ""),
                "名称": self._symbol_name(row),
                "评分": row.get("score", ""),
                "主链复核": self._action_label(row),
                "候选状态": self._candidate_status(row),
                "阻塞原因": self._candidate_blocker_text(row),
                "下一步": str(row.get("candidate_next_step", "") or ""),
                "数据源": row.get("run_actual_source", ""),
                "健康度": row.get("run_source_health_label", ""),
            }
            for row in rows[:limit]
        ]
        return pd.DataFrame(table)

    def open_positions_frame(self, *, signal_date: str = "") -> pd.DataFrame:
        selected_date = signal_date.strip()
        rows = [row for row in self.load_paper_rows() if row.get("status") == "open"]
        if selected_date:
            rows = [
                row
                for row in rows
                if str(row.get("signal_date", "") or "").strip() == selected_date
            ]
        if not rows:
            return pd.DataFrame()
        table = [
            {
                "代码": row.get("symbol", ""),
                "名称": self._symbol_name(row),
                "纸面入场日": row.get("entry_date", ""),
                "纸面入场价": row.get("entry_price", ""),
                "止损": row.get("stop_loss", ""),
                "止盈": row.get("take_profit", ""),
                "持有周期": row.get("horizon_days", ""),
            }
            for row in rows
        ]
        return pd.DataFrame(table)

    def paper_events_frame(
        self,
        limit: int = 20,
        *,
        signal_date: str = "",
    ) -> pd.DataFrame:
        rows = self.load_paper_rows()
        if signal_date.strip():
            rows = [
                row
                for row in rows
                if str(row.get("signal_date", "") or "") == signal_date.strip()
            ]
        if not rows:
            return pd.DataFrame()
        table = [
            {
                "代码": row.get("symbol", ""),
                "名称": self._symbol_name(row),
                "状态": row.get("status", ""),
                "信号日": row.get("signal_date", ""),
                "纸面入场日": row.get("entry_date", ""),
                "纸面关闭日": row.get("exit_date", ""),
                "关闭原因": row.get(
                    "exit_reason", row.get("not_executable_reason", "")
                ),
                "纸面收益%": row.get("return_pct", ""),
            }
            for row in rows[-limit:]
        ]
        return pd.DataFrame(table[::-1])

    def get_recent_execution_logs(self, days: int = 7) -> list[dict[str, Any]]:
        normalized_days = max(0, int(days))

        def _load() -> list[dict[str, Any]]:
            end_date = today_shanghai()
            start_date = end_date - timedelta(days=normalized_days)
            try:
                rows = self.logger.query_logs(
                    start_date=start_date,
                    end_date=end_date,
                )
            except Exception as exc:
                logger.error("加载执行日志失败: %s", exc)
                return []
            return [
                self._normalize_execution_log_row(row)
                for row in rows
                if row.get("type") in ("paper_execution", "execution")
            ]

        return self._cache_value(
            "get_recent_execution_logs",
            normalized_days,
            _load,
        )

    def execution_logs_for_date(self, signal_date: str) -> list[dict[str, Any]]:
        selected_date = signal_date.strip()
        if not selected_date:
            return []
        try:
            target_date = date.fromisoformat(selected_date)
        except ValueError:
            logger.warning("执行日志日期格式无效: %s", signal_date)
            return []

        def _load() -> list[dict[str, Any]]:
            try:
                rows = self.logger.query_logs(
                    start_date=target_date,
                    end_date=target_date,
                )
            except Exception as exc:
                logger.error("加载 %s 执行日志失败: %s", selected_date, exc)
                return []
            return [
                self._normalize_execution_log_row(row)
                for row in rows
                if row.get("type") in ("paper_execution", "execution")
                and str(row.get("timestamp", "") or "").startswith(selected_date)
            ]

        return self._cache_value(
            "execution_logs_for_date",
            selected_date,
            _load,
        )

    def _execution_logs_for_signal_context(
        self,
        *,
        signal_date: str,
        symbol: str,
        paper_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        selected_date = signal_date.strip()
        selected_symbol = symbol.strip()
        if not selected_symbol:
            return []

        execution_dates = {selected_date} if selected_date else set()
        for row in paper_rows:
            for field in ("entry_date", "exit_date", "executed_at", "closed_at"):
                value = str(row.get(field, "") or "").strip()
                if not value:
                    continue
                execution_dates.add(value[:10])

        if not execution_dates:
            rows = self.get_recent_execution_logs(days=7)
        else:
            rows = []
            for execution_date in sorted(execution_dates):
                rows.extend(self.execution_logs_for_date(execution_date))

        matched_rows: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str, str, str]] = set()
        for row in rows:
            if str(row.get("symbol", "") or "").strip() != selected_symbol:
                continue
            key = (
                str(row.get("timestamp", "") or "").strip(),
                str(row.get("symbol", "") or "").strip(),
                str(row.get("action", "") or "").strip(),
                str(row.get("price", "") or "").strip(),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            matched_rows.append(dict(row))
        matched_rows.sort(key=lambda row: str(row.get("timestamp", "") or ""))
        return matched_rows

    def recent_execution_frame(
        self,
        limit: int = 20,
        *,
        signal_date: str = "",
    ) -> pd.DataFrame:
        selected_date = signal_date.strip()
        rows = (
            self.execution_logs_for_date(selected_date)
            if selected_date
            else self.get_recent_execution_logs(days=7)
        )
        if not rows:
            return pd.DataFrame()
        table = [
            {
                "时间": row.get("timestamp", ""),
                "代码": row.get("symbol", ""),
                "动作": row.get("action", ""),
                "数量": row.get("shares", ""),
                "价格": row.get("price", ""),
                "成本": row.get("cost", ""),
            }
            for row in rows[-limit:]
        ]
        return pd.DataFrame(table[::-1])

    @staticmethod
    def _normalize_execution_log_row(row: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(row)
        normalized["action"] = DashboardDataProvider._paper_action_label(
            normalized.get("action", "")
        )
        return normalized

    @staticmethod
    def _paper_action_label(action: object) -> str:
        value = str(action or "").strip()
        return {
            "BUY": "纸面入场",
            "SELL": "纸面离场",
            "PAPER_ENTRY": "纸面入场",
            "PAPER_EXIT": "纸面离场",
        }.get(value, value)

    def latest_source_status(
        self,
        *,
        task_id: str = "main_chain",
        signal_date: str = "",
    ) -> dict[str, str]:
        rows = self._task_signal_rows(task_id)
        if signal_date.strip():
            rows = [
                row
                for row in rows
                if str(row.get("signal_date", "") or "") == signal_date.strip()
            ]
        if not rows:
            run_row = self._latest_source_run_event_row(
                task_id=task_id,
                signal_date=signal_date,
            )
            if run_row is not None:
                return self._source_status_from_row(run_row)
            return {}
        latest_row = max(
            rows,
            key=lambda row: (
                self._source_meta_score(row),
                *self._row_meta_key(row),
            ),
        )
        if self._source_meta_score(latest_row) == 0:
            run_row = self._latest_source_run_event_row(
                task_id=task_id,
                signal_date=signal_date,
            )
            if run_row is not None:
                return self._source_status_from_row(run_row)
            return {
                "requested_source": "未记录",
                "actual_source": "未记录",
                "health_label": "历史记录缺字段",
                "health_message": (
                    "该日历史记录未写入数据源元信息，无法还原当时的数据源健康度。"
                ),
                "data_latest_trade_date": "未记录",
                "lag_days": "未记录",
                "updated_at": now_shanghai().isoformat(timespec="seconds"),
            }
        return self._source_status_from_row(latest_row)

    def _build_signal_task_view(
        self,
        *,
        task_id: str,
        selected_date: str,
        available_dates: tuple[str, ...],
        include_deltas: bool,
    ) -> DashboardTaskView:
        report_document = self._report_document_for_signal_task(task_id, selected_date)
        report_markdown = report_document.markdown
        report_insights = self._extract_report_insights(report_markdown)
        previous_date = self._previous_date(
            available_dates=available_dates,
            selected_date=selected_date,
        )
        deduped = self._signal_task_rows_for_date(task_id, selected_date)
        actionable_rows = [
            row for row in deduped if self._is_actionable(row, task_id=task_id)
        ]
        blocked_rows = [row for row in deduped if self._is_blocked(row)]
        watch_rows = [
            row for row in deduped if self._is_watch_only(row, task_id=task_id)
        ]

        headline = self._headline_for_signal_task(
            task_id=task_id,
            signal_date=selected_date,
            actionable_rows=actionable_rows,
            watch_rows=watch_rows,
            blocked_rows=blocked_rows,
        )
        summary_lines = self._summary_lines_for_signal_task(
            task_id=task_id,
            rows=deduped,
            actionable_rows=actionable_rows,
            watch_rows=watch_rows,
            blocked_rows=blocked_rows,
        )
        recommendation_lines = tuple(
            self._recommendation_line(row) for row in actionable_rows[:5]
        )
        watchlist_lines = tuple(self._watch_line(row) for row in watch_rows[:5])
        blocker_lines = tuple(self._blocker_line(row) for row in blocked_rows[:5])
        review_lines = tuple(
            self._review_line(row) for row in deduped[:5] if self._review_line(row)
        )
        delta_lines = (
            self._build_delta_lines(
                task_id=task_id,
                selected_date=selected_date,
                previous_date=previous_date,
            )
            if include_deltas
            else ()
        )
        agenda_lines = self._build_agenda_lines(
            recommendation_lines=recommendation_lines,
            blocker_lines=blocker_lines,
            review_lines=review_lines,
            focus_lines=report_insights.next_day_focus_lines,
        )
        runtime_lines = self._merge_runtime_lines(
            report_insights.runtime_lines,
            self._ledger_market_context_runtime_lines(
                task_id=task_id,
                signal_date=selected_date,
            ),
        )
        return DashboardTaskView(
            task_id=task_id,
            task_label=_TASK_LABELS[task_id],
            selected_date=selected_date,
            latest_date=available_dates[0] if available_dates else "",
            previous_date=previous_date,
            available_dates=available_dates,
            headline=normalize_research_tone(headline),
            summary_lines=tuple(
                normalize_research_tone(line) for line in summary_lines
            ),
            lifecycle_lines=tuple(
                normalize_research_tone(line)
                for line in self._build_lifecycle_lines(deduped)
            ),
            unlock_lines=tuple(
                normalize_research_tone(line)
                for line in self._build_unlock_lines(
                    watch_rows=watch_rows,
                    blocked_rows=blocked_rows,
                )
            ),
            report_summary_lines=report_insights.report_summary_lines,
            runtime_lines=runtime_lines,
            delta_lines=tuple(normalize_research_tone(line) for line in delta_lines),
            agenda_lines=tuple(normalize_research_tone(line) for line in agenda_lines),
            recommendation_lines=tuple(
                normalize_research_tone(line) for line in recommendation_lines
            ),
            watchlist_lines=tuple(
                normalize_research_tone(line) for line in watchlist_lines
            ),
            blocker_lines=tuple(
                normalize_research_tone(line) for line in blocker_lines
            ),
            review_lines=tuple(normalize_research_tone(line) for line in review_lines),
            next_day_focus_lines=report_insights.next_day_focus_lines,
            report_markdown=report_markdown,
            report_source=report_document.source,
            report_mtime=report_document.mtime,
            source_status=self.latest_source_status(
                task_id=task_id, signal_date=selected_date
            ),
            candidate_count=len(deduped),
            actionable_count=len(actionable_rows),
            watch_count=len(watch_rows),
            blocked_count=len(blocked_rows),
            detail_cards=self._build_detail_cards(deduped, task_id=task_id),
            ranking_lines=tuple(
                normalize_research_tone(line)
                for line in self._build_ranking_lines(deduped)
            ),
            market_environment=report_insights.market_environment,
            strategy_breakdown_lines=(),
            lesson_lines=(),
            improvement_lines=(),
        )

    def _build_briefing_view(
        self,
        *,
        selected_date: str,
        available_dates: tuple[str, ...],
        include_deltas: bool,
    ) -> DashboardTaskView:
        report_document = self._read_briefing_document(selected_date)
        report_markdown = report_document.markdown
        report_insights = self._extract_report_insights(report_markdown)
        base_view = self._build_task_view_core(
            "main_chain",
            signal_date=selected_date,
            include_deltas=include_deltas,
        )
        runtime_lines = self._merge_runtime_lines(
            report_insights.runtime_lines,
            self._ledger_market_context_runtime_lines(
                task_id="main_chain",
                signal_date=selected_date,
            ),
        )
        return DashboardTaskView(
            task_id="briefing",
            task_label=_TASK_LABELS["briefing"],
            selected_date=selected_date,
            latest_date=available_dates[0] if available_dates else "",
            previous_date=base_view.previous_date,
            available_dates=available_dates,
            headline=normalize_research_tone(
                f"{_TASK_LABELS['briefing']} {selected_date or ''}".strip()
            ),
            summary_lines=base_view.summary_lines,
            lifecycle_lines=base_view.lifecycle_lines,
            unlock_lines=base_view.unlock_lines,
            report_summary_lines=report_insights.report_summary_lines,
            runtime_lines=runtime_lines,
            delta_lines=base_view.delta_lines if include_deltas else (),
            agenda_lines=self._build_agenda_lines(
                recommendation_lines=base_view.recommendation_lines,
                blocker_lines=base_view.blocker_lines,
                review_lines=base_view.review_lines,
                focus_lines=report_insights.next_day_focus_lines,
            ),
            recommendation_lines=base_view.recommendation_lines,
            watchlist_lines=base_view.watchlist_lines,
            blocker_lines=base_view.blocker_lines,
            review_lines=base_view.review_lines,
            next_day_focus_lines=report_insights.next_day_focus_lines,
            report_markdown=report_markdown,
            report_source=report_document.source,
            report_mtime=report_document.mtime,
            source_status=base_view.source_status,
            candidate_count=base_view.candidate_count,
            actionable_count=base_view.actionable_count,
            watch_count=base_view.watch_count,
            blocked_count=base_view.blocked_count,
            detail_cards=base_view.detail_cards,
            ranking_lines=base_view.ranking_lines,
            market_environment=report_insights.market_environment,
            strategy_breakdown_lines=base_view.strategy_breakdown_lines,
            lesson_lines=base_view.lesson_lines,
            improvement_lines=base_view.improvement_lines,
        )

    def _build_closing_review_view(
        self,
        *,
        selected_date: str,
        available_dates: tuple[str, ...],
        include_deltas: bool,
    ) -> DashboardTaskView:
        reviewer = ClosingReviewer(
            ledger_path=str(self.ledger_path),
            paper_ledger_path=str(self.paper_ledger_path),
        )
        review = reviewer.review_today(selected_date or None)
        fact_review = self._closing_review_facts(
            selected_date=selected_date,
            reviewer=reviewer,
        )
        summary_lines = tuple(review.main_chain_summary)
        recommendation_lines = tuple(
            normalize_research_tone(line)
            for line in summary_lines
            if line.startswith(("今日重点名单:", "纸面复核主链:", "可执行主链:"))
        )
        watchlist_lines = tuple(
            normalize_research_tone(line)
            for line in summary_lines
            if line.startswith(("备选观察名单:", "候选观察池:", "继续观察名单:"))
        )
        blocker_lines = tuple(
            normalize_research_tone(line)
            for line in summary_lines
            if line.startswith(("当前卡点:", "纸面阻塞:", "执行阻塞:", "当前限制:"))
        )
        review_lines = tuple(
            normalize_research_tone(line)
            for line in summary_lines
            if line.startswith(("观察复核:", "后续关注:"))
        )
        strategy_breakdown_lines = self._format_strategy_breakdown_lines(
            fact_review.strategy_breakdown
        )
        report_document = self._read_closing_review_document(selected_date)
        report_markdown = report_document.markdown
        lesson_lines = fact_review.lesson_lines
        improvement_lines = fact_review.improvement_lines
        headline = (
            f"{_TASK_LABELS['closing_review']} {selected_date}: "
            f"{fact_review.closed_trades} 笔已验证，胜率 {fact_review.win_rate:.0%}，"
            f"总收益 {fact_review.total_return:.2f}%"
        )
        previous_date = self._previous_date(
            available_dates=available_dates,
            selected_date=selected_date,
        )
        return DashboardTaskView(
            task_id="closing_review",
            task_label=_TASK_LABELS["closing_review"],
            selected_date=selected_date,
            latest_date=available_dates[0] if available_dates else "",
            previous_date=previous_date,
            available_dates=available_dates,
            headline=normalize_research_tone(headline),
            summary_lines=tuple(
                normalize_research_tone(line)
                for line in (
                    f"市场环境: {review.market_environment}",
                    f"总信号 {fact_review.total_signals} / 已验证 {fact_review.closed_trades}",
                    (
                        "事实来源: paper ledger "
                        f"closed={fact_review.closed_trades} / "
                        f"not_executable={fact_review.not_executable} / "
                        f"pending={fact_review.pending_entries}"
                    ),
                    f"胜率 {fact_review.win_rate:.0%} / 总收益 {fact_review.total_return:.2f}%",
                    *summary_lines,
                )
            ),
            lifecycle_lines=tuple(
                normalize_research_tone(line)
                for line in self._build_lifecycle_lines(
                    self._dedupe_rows(
                        [
                            row
                            for row in self._task_signal_rows("main_chain")
                            if str(row.get("signal_date", "") or "") == selected_date
                        ]
                    )
                )
            ),
            unlock_lines=tuple(
                normalize_research_tone(line)
                for line in self._build_unlock_lines(
                    watch_rows=[
                        row
                        for row in self._dedupe_rows(
                            [
                                row
                                for row in self._task_signal_rows("main_chain")
                                if str(row.get("signal_date", "") or "")
                                == selected_date
                            ]
                        )
                        if self._is_watch_candidate(row)
                    ],
                    blocked_rows=[
                        row
                        for row in self._dedupe_rows(
                            [
                                row
                                for row in self._task_signal_rows("main_chain")
                                if str(row.get("signal_date", "") or "")
                                == selected_date
                            ]
                        )
                        if self._is_blocked(row)
                    ],
                )
            ),
            report_summary_lines=(),
            runtime_lines=(),
            delta_lines=tuple(
                normalize_research_tone(line)
                for line in (
                    self._build_delta_lines(
                        task_id="closing_review",
                        selected_date=selected_date,
                        previous_date=previous_date,
                    )
                    if include_deltas
                    else ()
                )
            ),
            agenda_lines=tuple(
                normalize_research_tone(line)
                for line in self._build_agenda_lines(
                    recommendation_lines=recommendation_lines,
                    blocker_lines=blocker_lines,
                    review_lines=tuple(improvement_lines)
                    + review_lines
                    + tuple(lesson_lines),
                    focus_lines=(),
                )
            ),
            recommendation_lines=recommendation_lines,
            watchlist_lines=watchlist_lines,
            blocker_lines=blocker_lines,
            review_lines=review_lines,
            next_day_focus_lines=(),
            report_markdown=report_markdown,
            report_source=report_document.source,
            report_mtime=report_document.mtime,
            source_status=self.latest_source_status(
                task_id="main_chain",
                signal_date=selected_date,
            ),
            candidate_count=fact_review.total_signals,
            actionable_count=fact_review.closed_trades,
            watch_count=max(
                fact_review.total_signals
                - fact_review.closed_trades
                - fact_review.not_executable,
                0,
            ),
            blocked_count=fact_review.not_executable,
            detail_cards=self._build_detail_cards(
                self._dedupe_rows(
                    [
                        row
                        for row in self._task_signal_rows("main_chain")
                        if str(row.get("signal_date", "") or "") == selected_date
                    ]
                )
            ),
            ranking_lines=tuple(
                normalize_research_tone(line)
                for line in self._build_ranking_lines(
                    self._dedupe_rows(
                        [
                            row
                            for row in self._task_signal_rows("main_chain")
                            if str(row.get("signal_date", "") or "") == selected_date
                        ]
                    )
                )
            ),
            market_environment=review.market_environment,
            strategy_breakdown_lines=tuple(
                normalize_research_tone(line) for line in strategy_breakdown_lines
            ),
            lesson_lines=tuple(normalize_research_tone(line) for line in lesson_lines),
            improvement_lines=tuple(
                normalize_research_tone(line) for line in improvement_lines
            ),
        )

    def _closing_review_facts(
        self,
        *,
        selected_date: str,
        reviewer: ClosingReviewer,
    ) -> DashboardClosingReviewFacts:
        signal_rows = list(self._same_day_unique_rows(selected_date))
        signal_by_id = {
            str(row.get("id", "") or "").strip(): row
            for row in signal_rows
            if str(row.get("id", "") or "").strip()
        }
        signal_by_symbol = {
            str(row.get("symbol", "") or "").strip(): row
            for row in signal_rows
            if str(row.get("symbol", "") or "").strip()
        }
        paper_rows = [
            row
            for row in self.load_paper_rows()
            if str(row.get("signal_date", "") or "").strip() == selected_date
        ]
        closed_rows = [row for row in paper_rows if row.get("status") == "closed"]
        pending_rows = [
            row for row in paper_rows if row.get("status") == "pending_entry"
        ]
        blocked_rows = [
            row for row in paper_rows if row.get("status") == "not_executable"
        ]
        open_rows = [row for row in paper_rows if row.get("status") == "open"]

        returns = [self._float_value(row.get("return_pct")) for row in closed_rows]
        win_count = sum(1 for value in returns if value > 0)
        loss_count = max(len(closed_rows) - win_count, 0)
        total_return = round(sum(returns), 4)
        win_rate = win_count / len(closed_rows) if closed_rows else 0.0
        strategy_breakdown: dict[str, dict[str, Any]] = {}
        for row, return_pct in zip(closed_rows, returns):
            signal_row = self._matching_signal_row(
                paper_row=row,
                signal_by_id=signal_by_id,
                signal_by_symbol=signal_by_symbol,
            )
            merged_row = {**signal_row, **row}
            strategy = reviewer._resolve_strategy_type(merged_row)
            if strategy not in strategy_breakdown:
                strategy_breakdown[strategy] = {
                    "total": 0,
                    "wins": 0,
                    "losses": 0,
                    "total_return": 0.0,
                    "win_rate": 0.0,
                }
            strategy_breakdown[strategy]["total"] += 1
            strategy_breakdown[strategy]["total_return"] += return_pct
            if return_pct > 0:
                strategy_breakdown[strategy]["wins"] += 1
            else:
                strategy_breakdown[strategy]["losses"] += 1

        for stats in strategy_breakdown.values():
            total = int(stats.get("total") or 0)
            wins = int(stats.get("wins") or 0)
            stats["win_rate"] = wins / total if total else 0.0
            stats["total_return"] = round(float(stats.get("total_return") or 0.0), 4)

        lesson_lines = self._closing_fact_lessons(
            closed_rows=closed_rows,
            blocked_rows=blocked_rows,
            returns=returns,
        )
        improvement_lines = self._closing_fact_improvements(
            closed_count=len(closed_rows),
            pending_count=len(pending_rows),
            blocked_count=len(blocked_rows),
            win_rate=win_rate,
            returns=returns,
        )

        return DashboardClosingReviewFacts(
            total_signals=len(signal_rows),
            closed_trades=len(closed_rows),
            pending_entries=len(pending_rows),
            not_executable=len(blocked_rows),
            open_positions=len(open_rows),
            win_count=win_count,
            loss_count=loss_count,
            win_rate=win_rate,
            total_return=total_return,
            strategy_breakdown=strategy_breakdown,
            lesson_lines=lesson_lines,
            improvement_lines=improvement_lines,
        )

    def _matching_signal_row(
        self,
        *,
        paper_row: dict[str, Any],
        signal_by_id: dict[str, dict[str, Any]],
        signal_by_symbol: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        signal_id = str(paper_row.get("signal_id", "") or "").strip()
        if signal_id and signal_id in signal_by_id:
            return signal_by_id[signal_id]
        symbol = str(paper_row.get("symbol", "") or "").strip()
        return signal_by_symbol.get(symbol, {})

    def _closing_fact_lessons(
        self,
        *,
        closed_rows: list[dict[str, Any]],
        blocked_rows: list[dict[str, Any]],
        returns: list[float],
    ) -> tuple[str, ...]:
        lines: list[str] = []
        if not closed_rows:
            lines.append("本日还没有 closed 虚拟盘记录，胜率/收益不展示为实绩。")
        loss_count = sum(1 for value in returns if value <= 0)
        win_count = sum(1 for value in returns if value > 0)
        if closed_rows and loss_count > win_count:
            lines.append("事实平仓亏损较多，需复核入场纪律和退出条件。")
        if any(value <= -3 for value in returns):
            lines.append("存在大亏平仓记录，需检查止损是否按计划触发。")
        if blocked_rows:
            lines.append("不可成交样本仅计入阻塞，不进入胜率与收益统计。")
        return tuple(lines)

    def _closing_fact_improvements(
        self,
        *,
        closed_count: int,
        pending_count: int,
        blocked_count: int,
        win_rate: float,
        returns: list[float],
    ) -> tuple[str, ...]:
        lines: list[str] = []
        if closed_count > 0 and win_rate < 0.5:
            lines.append("事实胜率偏低，先复核候选进入虚拟盘后的执行质量。")
        if any(value <= -5 for value in returns):
            lines.append("出现大幅亏损平仓，下一轮优先检查止损距离和仓位假设。")
        if pending_count > 0:
            lines.append("仍有 pending_entry，次日开盘先补齐可成交性验证。")
        if blocked_count > 0:
            lines.append("把不可成交原因回写到候选复盘，避免把买不到的样本当失败交易。")
        return tuple(lines[:4])

    def _float_value(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _task_signal_rows(self, task_id: str) -> list[dict[str, Any]]:
        def _build() -> list[dict[str, Any]]:
            rows = [
                row
                for row in self.load_signal_rows()
                if not self._is_runtime_event_row(row)
            ]
            if task_id == "intraday":
                return [row for row in rows if self._row_task_id(row) == "intraday"]
            if task_id == "morning_breakout":
                return [
                    row for row in rows if self._row_task_id(row) == "morning_breakout"
                ]
            if task_id == "closing_premium":
                return [
                    row for row in rows if self._row_task_id(row) == "closing_premium"
                ]
            if task_id == "main_chain":
                return [row for row in rows if self._row_task_id(row) == "main_chain"]
            return rows

        return self._cache_value("task_signal_rows", task_id, _build)

    def _row_task_id(self, row: dict[str, Any]) -> str:
        for key in ("task_id", "run_task_id", "source_task_id"):
            explicit = str(row.get(key, "") or "").strip()
            if explicit in (*_SIGNAL_TASK_IDS, *_OBSERVATION_TASK_IDS):
                return explicit
        strategies = row.get("strategies") or []
        if isinstance(strategies, str):
            strategy_values = [strategies]
        else:
            strategy_values = [str(item) for item in strategies]
        haystack = " ".join(strategy_values).lower()
        matched_task_ids = []
        if "morning_breakout" in haystack or "morning-breakout" in haystack:
            matched_task_ids.append("morning_breakout")
        if "closing_premium" in haystack or "closing-premium" in haystack:
            matched_task_ids.append("closing_premium")
        if len(matched_task_ids) == 1:
            return matched_task_ids[0]
        return "main_chain"

    def _signal_dates(self, rows: list[dict[str, Any]]) -> tuple[str, ...]:
        dates = {
            str(row.get("signal_date", "") or "").strip()
            for row in rows
            if str(row.get("signal_date", "") or "").strip()
        }
        return tuple(sorted(dates, reverse=True))

    def _briefing_dates(self) -> tuple[str, ...]:
        if not self.reports_dir.exists():
            return ()
        dates = []
        for path in self.reports_dir.glob("briefing-*.md"):
            stem = path.stem
            if stem.startswith("briefing-"):
                dates.append(stem.removeprefix("briefing-"))
        return tuple(sorted(set(dates), reverse=True))

    def _empty_report_document(self) -> DashboardReportDocument:
        return DashboardReportDocument(markdown="", source="", mtime="")

    def _report_file_mtime(self, path: Path) -> str:
        if not path.exists():
            return ""
        try:
            return to_iso8601(
                datetime.fromtimestamp(path.stat().st_mtime, tz=SHANGHAI_TZ)
            )
        except OSError as exc:
            logger.warning("读取报告 mtime 失败 %s: %s", path, exc)
            return ""

    def _closing_review_dated_report_mtime(self, signal_date: str) -> str:
        normalized_date = signal_date.strip()
        if not normalized_date:
            return ""
        candidate_paths = [
            self.reports_dir / f"closing_review-{normalized_date}.md",
            self.reports_dir / f"closing-review-{normalized_date}.md",
            self.reports_dir / f"daily-review-{normalized_date}.md",
        ]
        if normalized_date == self._max_signal_date(self.load_signal_rows()):
            candidate_paths.append(self.reports_dir / "closing_review.md")
        for path in candidate_paths:
            mtime = self._report_file_mtime(path)
            if mtime:
                return mtime
        return ""

    def _read_report_document(
        self,
        path: Path,
        *,
        expected_date: str = "",
    ) -> DashboardReportDocument:
        if not path.exists():
            return self._empty_report_document()
        try:
            markdown_text = path.read_text(encoding="utf-8")
            mtime = to_iso8601(
                datetime.fromtimestamp(path.stat().st_mtime, tz=SHANGHAI_TZ)
            )
        except OSError as exc:
            logger.error("读取报告失败 %s: %s", path, exc)
            return self._empty_report_document()
        if expected_date and not self._report_body_matches_signal_date(
            markdown_text,
            expected_date,
        ):
            logger.debug("报告日期不匹配，已忽略: %s", path)
            return self._empty_report_document()
        return DashboardReportDocument(
            markdown=markdown_text,
            source=str(path),
            mtime=mtime,
        )

    def _read_closing_review_document(
        self, selected_date: str
    ) -> DashboardReportDocument:
        if not self.reports_dir.exists():
            return self._empty_report_document()
        normalized_date = selected_date.strip()
        candidate_paths: list[Path] = []
        if normalized_date:
            candidate_paths.extend(
                [
                    self.reports_dir / f"closing_review-{normalized_date}.md",
                    self.reports_dir / f"closing-review-{normalized_date}.md",
                    self.reports_dir / f"daily-review-{normalized_date}.md",
                ]
            )
        latest_signal_date = self._max_signal_date(self.load_signal_rows())
        if not normalized_date or normalized_date == latest_signal_date:
            candidate_paths.append(self.reports_dir / "closing_review.md")
        for path in candidate_paths:
            document = self._read_report_document(
                path,
                expected_date=normalized_date,
            )
            if document.markdown:
                return document
        return self._empty_report_document()

    def _read_closing_review_markdown(self, selected_date: str) -> str:
        return self._read_closing_review_document(selected_date).markdown

    def _max_signal_date(self, rows: list[dict[str, Any]]) -> str:
        dates = self._signal_dates(rows)
        return dates[0] if dates else ""

    def _all_dashboard_dates(self) -> tuple[str, ...]:
        def _build() -> tuple[str, ...]:
            dates: set[str] = set()
            for task_id in _TASK_LABELS:
                dates.update(self.task_dates(task_id))
            dates.update(
                self._debate_signal_date(row)
                for row in self._load_debate_rows()
                if self._has_debate_evidence(row)
            )
            return tuple(sorted((date for date in dates if date), reverse=True))

        return self._cache_value("all_dashboard_dates", "all", _build)

    def _dedupe_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            key = (
                str(row.get("signal_date", "") or ""),
                self._row_task_id(row),
                self._symbol_key(row),
            )
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = row
                continue
            if self._row_meta_key(row) > self._row_meta_key(existing):
                grouped[key] = row
                continue
            if self._row_meta_key(row) == self._row_meta_key(existing):
                if float(row.get("score") or 0.0) > float(existing.get("score") or 0.0):
                    grouped[key] = row
        return list(grouped.values())

    @staticmethod
    def _canonical_symbol(symbol: str) -> str:
        value = str(symbol or "").strip()
        if value.isdigit() and len(value) < 6:
            return value.zfill(6)
        return value

    def _symbol_key(self, row: dict[str, Any]) -> str:
        return self._canonical_symbol(str(row.get("symbol", "") or ""))

    def _row_meta_key(self, row: dict[str, Any]) -> tuple[str, str, float]:
        return (
            str(row.get("signal_date", "") or ""),
            str(row.get("created_at", "") or ""),
            float(row.get("score") or 0.0),
        )

    def _sort_key(self, row: dict[str, Any]) -> tuple[float, str]:
        return (float(row.get("score") or 0.0), str(row.get("created_at", "") or ""))

    def _latest_created_at(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return ""
        return max(str(row.get("created_at", "") or "").strip() for row in rows)

    def _task_row_created_at(
        self,
        *,
        task_id: str,
        signal_date: str,
        view: DashboardTaskView,
    ) -> str:
        if task_id in {"briefing", "closing_review"} and view.report_mtime:
            return view.report_mtime
        if task_id == "briefing":
            source_task_id = "main_chain"
        elif task_id == "closing_review":
            source_task_id = "main_chain"
        else:
            source_task_id = task_id
        rows = [
            row
            for row in self._dedupe_rows(self._task_signal_rows(source_task_id))
            if str(row.get("signal_date", "") or "").strip() == signal_date
        ]
        return self._latest_created_at(rows)

    def _task_label(self, task_id: str) -> str:
        return normalize_research_tone(_TASK_LABELS.get(task_id, task_id))

    def _task_phase_order(self, task_id: str) -> int:
        return _TASK_PHASE_META.get(task_id, (99, task_id, ""))[0]

    def _task_phase_label(self, task_id: str) -> str:
        return normalize_research_tone(
            _TASK_PHASE_META.get(task_id, (99, task_id, ""))[1]
        )

    def _task_phase_note(self, task_id: str) -> str:
        return normalize_research_tone(
            _TASK_PHASE_META.get(task_id, (99, task_id, ""))[2]
        )

    def _task_metric_labels(self, task_id: str) -> tuple[str, str, str]:
        return _TASK_METRIC_LABELS.get(task_id, ("待复核", "观察", "阻塞"))

    def _task_phase_summary(self, task_id: str, view: DashboardTaskView) -> str:
        return self._task_phase_summary_from_counts(
            task_id=task_id,
            candidate_count=view.candidate_count,
            actionable_count=view.actionable_count,
            watch_count=view.watch_count,
            blocked_count=view.blocked_count,
        )

    def _task_phase_summary_from_counts(
        self,
        *,
        task_id: str,
        candidate_count: int,
        actionable_count: int,
        watch_count: int,
        blocked_count: int,
    ) -> str:
        note = self._task_phase_note(task_id)
        action_label, watch_label, blocked_label = self._task_metric_labels(task_id)
        if actionable_count > 0:
            return f"{note}；当前{action_label} {actionable_count} 只。"
        if blocked_count > 0:
            return f"{note}；当前{blocked_label} {blocked_count} 只。"
        if watch_count > 0:
            return f"{note}；当前{watch_label} {watch_count} 只。"
        if candidate_count > 0:
            return f"{note}；当前已有结果待回看。"
        return f"{note}；当前还没有有效结果，先确认对应任务是否已跑完。"

    def _workflow_summary(
        self,
        rows: tuple[DashboardSameDayTaskRow, ...],
    ) -> str:
        phase_text = " -> ".join(
            f"{row.phase_label}({row.status_label})" for row in rows[:5]
        )
        if not phase_text:
            return ""
        return f"当日流程: {phase_text}"

    def _archive_summary(
        self,
        rows: tuple[DashboardSameDayTaskRow, ...],
        focus_row: DashboardSameDayTaskRow,
        blocker_row: DashboardSameDayTaskRow | None,
    ) -> str:
        blocker_text = (
            f"主要卡在 {blocker_row.task_label}"
            if blocker_row is not None
            else "全链路无明显阻塞"
        )
        return (
            f"本日共覆盖 {len(rows)} 个阶段；"
            f"主焦点在 {focus_row.task_label}；{blocker_text}。"
        )

    def _same_day_spotlight_key(
        self,
        row: dict[str, Any],
        task_id: str = "",
    ) -> tuple[int, str, float]:
        normalized_task_id = task_id or self._row_task_id(row)
        return (
            self._task_phase_order(normalized_task_id),
            str(row.get("created_at", "") or ""),
            float(row.get("score") or 0.0),
        )

    def _build_same_day_spotlight(
        self,
        symbol: str,
        payload: dict[str, Any],
    ) -> DashboardCandidateSpotlight:
        merged_row = self._same_day_merged_row(
            payload["row"],
            payload.get("entries", ()),
        )
        return DashboardCandidateSpotlight(
            symbol=symbol,
            display_name=self._symbol_name(merged_row),
            score=float(merged_row.get("score") or 0.0),
            action_label=self._action_label(merged_row),
            status_label=self._candidate_status(merged_row),
            blocker=self._candidate_blocker_text(merged_row),
            next_step=self._next_step_text(merged_row),
            review_meta=self._review_meta(merged_row),
            task_labels=tuple(payload["task_labels"]),
            reasons=self._as_text_tuple(merged_row.get("reasons")),
            risks=self._as_text_tuple(merged_row.get("risks")),
            strategies=self._strategy_tuple(merged_row.get("strategies")),
            cross_market_summary=self._spotlight_cross_market_summary(merged_row),
            news_catalyst_summary=self._spotlight_news_catalyst_summary(merged_row),
            cross_market_chain_summary=self._spotlight_cross_market_chain_summary(
                merged_row
            ),
            cross_market_validation_summary=(
                self._spotlight_cross_market_validation_summary(merged_row)
            ),
            cross_market_invalidation_summary=(
                self._spotlight_cross_market_invalidation_summary(merged_row)
            ),
            support_points=self._as_text_tuple(merged_row.get("support_points")),
            opposition_points=self._as_text_tuple(merged_row.get("opposition_points")),
            watch_items=self._as_text_tuple(merged_row.get("watch_items")),
            candidate_fingerprint=self._candidate_fingerprint_for_row(merged_row),
            close=_technical_metric_value(merged_row, "close", "signal_close"),
            ret5_pct=_technical_metric_value(merged_row, "ret5_pct"),
            ret20_pct=_technical_metric_value(merged_row, "ret20_pct"),
            volume_ratio=_technical_metric_value(merged_row, "volume_ratio"),
            rsi12=_technical_metric_value(merged_row, "rsi12"),
            bias20_pct=_technical_metric_value(merged_row, "bias20_pct"),
            stop_loss=_technical_metric_value(merged_row, "stop_loss"),
            take_profit=_technical_metric_value(merged_row, "take_profit"),
        )

    def _same_day_merged_row(
        self,
        final_row: dict[str, Any],
        entries: Any,
    ) -> dict[str, Any]:
        merged = dict(final_row)
        ordered_entries = self._same_day_evidence_entries(final_row, entries)
        for field in (
            "reasons",
            "risks",
            "candidate_next_step",
            "candidate_review_priority",
            "candidate_review_window",
            "news_catalyst_judgement",
            "news_catalyst_priority_score",
            "news_catalyst_support_count",
            "news_catalyst_oppose_count",
            "news_catalyst_review_count",
            "news_catalyst_supports",
            "news_catalyst_opposes",
            "news_catalyst_needs_review",
            "news_catalyst_lead",
            "news_catalyst_source",
            "cross_market_primary_theme",
            "cross_market_linkage_basis",
            "cross_market_action",
            "cross_market_strength",
            "cross_market_lead_window",
            "cross_market_observation_window",
            "cross_market_priority_score",
            "cross_market_themes",
            "cross_market_rule_ids",
            "cross_market_transmission_path",
            "cross_market_validation_signals",
            "cross_market_invalidation_signals",
            "cross_market_chain_summary",
            "cross_market_support_event_count",
            "cross_market_conflict_event_count",
            "cross_market_evidence_stack_summary",
            "cross_market_summaries",
            "debate_research_verdict",
            "debate_primary_risk_gate",
            "debate_next_trigger",
            "debate_active_role_summary",
            "support_points",
            "opposition_points",
            "watch_items",
            "role_reliability_lines",
            "debate_historical_context_note",
            "debate_historical_context_bucket",
            "debate_historical_context_sample_count",
            "debate_historical_context_accuracy",
        ):
            if self._row_field_has_value(merged.get(field)):
                continue
            value = self._first_non_empty_field(
                ordered_entries,
                field,
                final_row=final_row,
            )
            if value is not None:
                merged[field] = value

        if not self._row_field_has_value(merged.get("debate_active_role_summary")):
            debate_active_role_summary = self._debate_active_role_summary_for_row(
                final_row
            )
            if debate_active_role_summary:
                merged["debate_active_role_summary"] = debate_active_role_summary

        if self._is_blocked(final_row) and not self._row_field_has_value(
            merged.get("candidate_blocker")
        ):
            blocker = self._first_non_empty_field(
                ordered_entries,
                "candidate_blocker",
                final_row=final_row,
            )
            if blocker is not None:
                merged["candidate_blocker"] = blocker
        return self._merge_debate_evidence(merged)

    def _merge_debate_evidence(self, row: dict[str, Any]) -> dict[str, Any]:
        signal_date = str(
            row.get("signal_date", "") or row.get("date", "") or ""
        ).strip()
        symbol = str(row.get("symbol", "") or "").strip()
        if not signal_date or not symbol or symbol == "__RUN__":
            return dict(row)
        matches = [
            debate_row
            for debate_row in self._load_debate_rows()
            if self._debate_signal_date(debate_row) == signal_date
            and str(debate_row.get("symbol", "") or "").strip() == symbol
            and self._debate_matches_candidate_row(debate_row, row)
            and self._has_debate_evidence(debate_row)
        ]
        if not self._candidate_fingerprint_for_row(row):
            debate_fingerprints = {
                str(item.get("candidate_fingerprint", "") or "").strip()
                for item in matches
                if str(item.get("candidate_fingerprint", "") or "").strip()
            }
            if len(debate_fingerprints) > 1:
                return dict(row)
        if not matches:
            return dict(row)

        debate_row = max(matches, key=self._debate_quality_key)
        merged = dict(row)
        field_sources = {
            "debate_research_verdict": "research_verdict",
            "debate_primary_risk_gate": "primary_risk_gate",
            "debate_next_trigger": "next_trigger",
            "debate_context_quality": "debate_context_quality",
            "market_context_lines": "market_context_lines",
            "cross_market_chain_summary": "cross_market_chain_summary",
            "cross_market_validation_signals": "cross_market_validation_summary",
            "cross_market_invalidation_signals": "cross_market_invalidation_summary",
            "support_points": "support_points",
            "opposition_points": "opposition_points",
            "watch_items": "watch_items",
        }
        for target, source in field_sources.items():
            if self._row_field_has_value(merged.get(target)):
                continue
            value = debate_row.get(source)
            if self._row_field_has_value(value):
                merged[target] = value

        if not self._row_field_has_value(merged.get("cross_market_primary_theme")):
            cross_market_summary = debate_row.get("cross_market_summary")
            if self._row_field_has_value(cross_market_summary):
                merged["cross_market_primary_theme"] = cross_market_summary
        if not self._row_field_has_value(merged.get("support_points")):
            merged["support_points"] = debate_row.get("opportunity_highlights") or ()
        if not self._row_field_has_value(merged.get("opposition_points")):
            merged["opposition_points"] = debate_row.get("risk_warnings") or ()
        return merged

    def _same_day_evidence_entries(
        self,
        final_row: dict[str, Any],
        entries: Any,
    ) -> tuple[dict[str, Any], ...]:
        evidence_entries: list[tuple[tuple[int, str, float], dict[str, Any]]] = []
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                row = entry.get("row")
                if not isinstance(row, dict):
                    continue
                task_id = str(entry.get("task_id", "") or "")
                evidence_entries.append(
                    (self._same_day_spotlight_key(row, task_id), entry)
                )
        if not evidence_entries:
            task_id = self._row_task_id(final_row)
            evidence_entries.append(
                (
                    self._same_day_spotlight_key(final_row, task_id),
                    {"row": final_row, "task_id": task_id},
                )
            )
        evidence_entries.sort(key=lambda item: item[0], reverse=True)
        return tuple(entry for _, entry in evidence_entries)

    def _row_field_has_value(self, value: Any) -> bool:
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple)):
            return any(str(item).strip() for item in value)
        return value is not None

    def _first_non_empty_field(
        self,
        entries: tuple[dict[str, Any], ...],
        field: str,
        *,
        final_row: dict[str, Any],
    ) -> Any | None:
        for entry in entries:
            row = entry.get("row")
            if not isinstance(row, dict):
                continue
            value = row.get(field)
            if self._row_field_has_value(value):
                if not self._can_backfill_field_from_entry(
                    row,
                    field,
                    final_row=final_row,
                ):
                    continue
                if row is final_row:
                    return value
                task_id = str(entry.get("task_id", "") or "")
                return self._label_backfilled_evidence(value, task_id)
        return None

    def _can_backfill_field_from_entry(
        self,
        source_row: dict[str, Any],
        field: str,
        *,
        final_row: dict[str, Any],
    ) -> bool:
        if source_row is final_row or field not in _DEBATE_BACKFILL_FIELDS:
            return True
        source_fingerprint = self._candidate_fingerprint_for_row(source_row)
        final_fingerprint = self._candidate_fingerprint_for_row(final_row)
        if source_fingerprint or final_fingerprint:
            return bool(source_fingerprint and source_fingerprint == final_fingerprint)
        source_created_at = self._row_created_at(source_row)
        final_created_at = self._row_created_at(final_row)
        return bool(
            source_created_at
            and final_created_at
            and source_created_at >= final_created_at
        )

    def _candidate_fingerprint_for_row(self, row: dict[str, Any]) -> str:
        metrics = row.get("metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        return str(
            row.get("candidate_fingerprint")
            or row.get("debate_candidate_fingerprint")
            or metrics.get("candidate_fingerprint")
            or metrics.get("debate_candidate_fingerprint")
            or ""
        ).strip()

    def _debate_matches_context(
        self,
        row: dict[str, Any],
        *,
        candidate_fingerprint: str = "",
        task_id: str = "",
    ) -> bool:
        expected_fingerprint = str(candidate_fingerprint or "").strip()
        row_fingerprint = self._candidate_fingerprint_for_row(row)
        if expected_fingerprint:
            return bool(row_fingerprint) and expected_fingerprint == row_fingerprint
        expected_task = str(task_id or "").strip()
        row_task = str(row.get("task_id", "") or "").strip()
        return not (expected_task and row_task and expected_task != row_task)

    def _debate_matches_candidate_row(
        self,
        debate_row: dict[str, Any],
        candidate_row: dict[str, Any],
    ) -> bool:
        candidate_fingerprint = self._candidate_fingerprint_for_row(candidate_row)
        debate_fingerprint = str(
            self._candidate_fingerprint_for_row(debate_row) or ""
        ).strip()
        if candidate_fingerprint and debate_fingerprint:
            return candidate_fingerprint == debate_fingerprint
        if candidate_fingerprint and not debate_fingerprint:
            return False

        candidate_task = self._row_task_id(candidate_row)
        debate_task = str(debate_row.get("task_id", "") or "").strip()
        if candidate_task and debate_task and candidate_task == debate_task:
            return True
        return not debate_task

    def _row_created_at(self, row: dict[str, Any]) -> str:
        return str(
            row.get("candidate_created_at") or row.get("created_at") or ""
        ).strip()

    def _label_backfilled_evidence(self, value: Any, task_id: str) -> Any:
        source = self._task_phase_label(task_id) if task_id else "同日阶段"
        if isinstance(value, str):
            return f"{source}: {value.strip()}"
        if isinstance(value, (list, tuple)):
            return [
                f"{source}: {str(item).strip()}" for item in value if str(item).strip()
            ]
        return value

    def _same_day_unique_rows(self, signal_date: str) -> tuple[dict[str, Any], ...]:
        selected_date = signal_date.strip()
        if not selected_date:
            return ()

        def _build() -> tuple[dict[str, Any], ...]:
            grouped: dict[str, dict[str, Any]] = {}
            for task_id in (*_SIGNAL_TASK_IDS, *_OBSERVATION_TASK_IDS):
                rows = [
                    row
                    for row in self._dedupe_rows(self._task_signal_rows(task_id))
                    if str(row.get("signal_date", "") or "") == selected_date
                ]
                for row in rows:
                    symbol = str(row.get("symbol", "") or "").strip()
                    if not symbol:
                        continue
                    payload = grouped.get(symbol)
                    if payload is None:
                        grouped[symbol] = {
                            "row": row,
                            "task_id": task_id,
                            "entries": [{"row": row, "task_id": task_id}],
                        }
                        continue
                    payload["entries"].append({"row": row, "task_id": task_id})
                    if self._same_day_spotlight_key(
                        row,
                        task_id,
                    ) > self._same_day_spotlight_key(
                        payload["row"],
                        str(payload.get("task_id", "") or ""),
                    ):
                        payload["row"] = row
                        payload["task_id"] = task_id
            return tuple(
                self._same_day_merged_row(payload["row"], payload.get("entries", ()))
                for payload in grouped.values()
            )

        return self._cache_value("same_day_unique_rows", selected_date, _build)

    def _same_day_unique_counts(self, signal_date: str) -> tuple[int, int, int]:
        unique_rows = self._same_day_unique_rows(signal_date)
        actionable_total = sum(1 for row in unique_rows if self._is_actionable(row))
        watch_total = sum(1 for row in unique_rows if self._is_watch_only(row))
        blocked_total = sum(1 for row in unique_rows if self._is_blocked(row))
        return actionable_total, watch_total, blocked_total

    def _spotlight_priority_rank(self, item: DashboardCandidateSpotlight) -> int:
        if item.blocker:
            return 3
        if item.action_label in {"上调优先级", "优先级上调"}:
            return 0
        if item.action_label in {"重点关注", "重点跟踪", "继续观察", "观察候选"}:
            return 1
        return 2

    def _priority_bucket(self, row: dict[str, Any]) -> int:
        action = str(row.get("portfolio_action", "") or "").strip()
        rating = str(row.get("rating", "") or "").strip()
        blocker = str(row.get("candidate_blocker", "") or "").strip()
        if action == "downgrade" or blocker:
            return 3
        if action == "promote":
            return 0
        if not blocker and is_tradable_rating(rating):
            return 1
        if action == "keep":
            return 2
        if rating in {"watch", "avoid"}:
            return 4
        return 5

    def _candidate_sort_key(
        self, row: dict[str, Any]
    ) -> tuple[int, tuple[int, ...], float, str]:
        return (
            -self._priority_bucket(row),
            self._discussion_priority_key(row),
            float(row.get("score") or 0.0),
            str(row.get("created_at", "") or ""),
        )

    def _discussion_priority_key(self, row: dict[str, Any]) -> tuple[int, ...]:
        verdict_source, verdict = self._split_backfilled_evidence_text(
            str(row.get("debate_research_verdict", "") or "")
        )
        risk_source, risk_gate = self._split_backfilled_evidence_text(
            str(row.get("debate_primary_risk_gate", "") or "")
        )
        trigger_source, next_trigger = self._split_backfilled_evidence_text(
            str(row.get("debate_next_trigger", "") or "")
        )
        history_source, history_note = self._split_backfilled_evidence_text(
            str(row.get("debate_historical_context_note", "") or "")
        )
        chain_source, chain_summary = self._split_backfilled_evidence_text(
            str(row.get("cross_market_chain_summary", "") or "")
        )
        linkage_source, linkage_basis = self._split_backfilled_evidence_text(
            str(row.get("cross_market_linkage_basis", "") or "")
        )
        lead_window_source, lead_window = self._split_backfilled_evidence_text(
            str(row.get("cross_market_lead_window", "") or "")
        )
        transmission_source, transmission_path = self._split_backfilled_evidence_text(
            str(row.get("cross_market_transmission_path", "") or "")
        )
        validation_signals = self._as_text_tuple(
            row.get("cross_market_validation_signals")
        )
        invalidation_signals = self._as_text_tuple(
            row.get("cross_market_invalidation_signals")
        )
        cross_market_theme = str(
            row.get("cross_market_primary_theme", "") or ""
        ).strip()
        cross_market_action = str(row.get("cross_market_action", "") or "").strip()
        discussion_sources = {
            source
            for source in (
                verdict_source,
                risk_source,
                trigger_source,
                history_source,
                chain_source,
                linkage_source,
                lead_window_source,
                transmission_source,
            )
            if source
        }
        structure_count = (
            sum(
                1
                for value in (
                    risk_gate,
                    next_trigger,
                    history_note,
                    chain_summary,
                    linkage_basis,
                    lead_window,
                    transmission_path,
                    cross_market_theme,
                    cross_market_action,
                )
                if value
            )
            + int(bool(validation_signals))
            + int(bool(invalidation_signals))
        )
        return (
            self._discussion_verdict_rank(verdict),
            structure_count,
            int(bool(next_trigger)),
            int(bool(risk_gate)),
            int(bool(validation_signals)),
            int(bool(invalidation_signals)),
            int(bool(cross_market_theme or chain_summary or linkage_basis)),
            int(bool(cross_market_action)),
            len(discussion_sources),
            int(float(row.get("cross_market_priority_score") or 0.0) * 100),
            self._discussion_history_bucket_rank(row),
            self._discussion_history_accuracy_score(row),
            int(bool(history_note)),
        )

    def _discussion_verdict_rank(self, verdict: str) -> int:
        text = verdict.strip()
        if not text:
            return 0
        if "优先" in text and ("复核" in text or "跟踪" in text):
            return 3
        if any(
            keyword in text
            for keyword in ("纸面复核", "纸面跟踪", "重点跟踪", "重点观察")
        ):
            return 2
        return 1

    def _action_label(self, row: dict[str, Any]) -> str:
        action = str(row.get("portfolio_action", "") or "").strip()
        rating = str(row.get("rating", "") or "").strip()
        if action and action != "keep":
            return normalize_research_tone(portfolio_action_label(action))
        if self._is_intraday_row(row) and self._is_actionable(row):
            return "纸面复核"
        if rating:
            return normalize_research_tone(rating_label(rating))
        if action:
            return normalize_research_tone(portfolio_action_label(action))
        return normalize_research_tone(rating_label(rating))

    def _action_status_text(self, row: dict[str, Any]) -> str:
        action = self._action_label(row).strip()
        status = self._candidate_status(row).strip()
        if action and status:
            if action == status:
                return action
            if action in {"继续观察", "继续观察名单"} and status == "结果不变":
                return status
            return f"{action} / {status}"
        return action or status or "-"

    def _candidate_status(self, row: dict[str, Any]) -> str:
        explicit = str(row.get("candidate_status", "") or "").strip()
        if explicit:
            return normalize_research_tone(explicit)
        return self._action_label(row)

    def _symbol_name(self, row: dict[str, Any]) -> str:
        return format_symbol_name(
            str(row.get("symbol", "") or ""),
            str(row.get("name", "") or ""),
        )

    def _display_name_for_symbol(self, row: dict[str, Any]) -> str:
        display_name = self._symbol_name(row)
        if " " in display_name:
            return display_name

        symbol = str(row.get("symbol", "") or "").strip()
        if not symbol:
            return display_name

        for source_row in [*self.load_signal_rows(), *self.load_paper_rows()]:
            if str(source_row.get("symbol", "") or "").strip() != symbol:
                continue
            resolved = self._symbol_name(source_row)
            if resolved.strip():
                return resolved
        return display_name

    def _is_intraday_row(self, row: dict[str, Any], task_id: str = "") -> bool:
        return task_id == "intraday" or self._row_task_id(row) == "intraday"

    def _is_actionable(self, row: dict[str, Any], task_id: str = "") -> bool:
        if self._is_blocked(row):
            return False
        action = str(row.get("portfolio_action", "") or "").strip()
        if action == "promote":
            return True
        if action == "downgrade":
            return False
        return is_tradable_rating(row.get("rating"))

    def _is_watch_candidate(self, row: dict[str, Any], task_id: str = "") -> bool:
        rating = str(row.get("rating", "") or "").strip()
        action = str(row.get("portfolio_action", "") or "").strip()
        return rating in {"watch", "avoid"} or action in {"downgrade", "keep"}

    def _is_watch_only(self, row: dict[str, Any], task_id: str = "") -> bool:
        return (
            not self._is_actionable(row, task_id=task_id)
            and not self._is_blocked(row)
            and self._is_watch_candidate(row, task_id=task_id)
        )

    def _is_blocked(self, row: dict[str, Any]) -> bool:
        blocker = str(row.get("candidate_blocker", "") or "").strip()
        status = str(row.get("candidate_status", "") or "").strip()
        action = str(row.get("portfolio_action", "") or "").strip()
        return bool(blocker or "阻塞" in status or action == "downgrade")

    def _candidate_blocker_text(self, row: dict[str, Any]) -> str:
        blocker = str(row.get("candidate_blocker", "") or "").strip()
        if blocker:
            return normalize_research_tone(blocker)
        if not self._is_blocked(row):
            return ""
        risks = self._as_text_tuple(row.get("risks"))
        if risks:
            return risks[0]
        return MISSING_BLOCKER_TEXT

    def _next_step_text(self, row: dict[str, Any]) -> str:
        next_step = str(row.get("candidate_next_step", "") or "").strip()
        if next_step:
            return normalize_research_tone(next_step)
        debate_next_trigger = str(row.get("debate_next_trigger", "") or "").strip()
        if debate_next_trigger:
            return normalize_research_tone(debate_next_trigger)
        return ""

    def _split_backfilled_evidence_text(self, text: str) -> tuple[str, str]:
        value = text.strip()
        for task_id in (*_SIGNAL_TASK_IDS, *_OBSERVATION_TASK_IDS):
            source = self._task_phase_label(task_id)
            prefix = f"{source}: "
            if value.startswith(prefix):
                return source, value[len(prefix) :].strip()
        prefix = "同日阶段: "
        if value.startswith(prefix):
            return "同日阶段", value[len(prefix) :].strip()
        return "", value

    def _review_meta(self, row: dict[str, Any]) -> str:
        return normalize_research_tone(
            format_review_meta(
                str(row.get("candidate_review_priority", "") or ""),
                str(row.get("candidate_review_window", "") or ""),
            )
        )

    def _as_text_tuple(self, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            parts = [item.strip() for item in value.split("；")]
            return tuple(normalize_research_tone(item) for item in parts if item)
        if isinstance(value, (list, tuple)):
            return tuple(
                normalize_research_tone(str(item).strip())
                for item in value
                if str(item).strip()
            )
        return ()

    def _strategy_tuple(self, value: Any) -> tuple[str, ...]:
        """Normalize strategy names from CSV strings and structured rows."""
        if isinstance(value, str):
            values: tuple[Any, ...] = tuple(
                part.strip()
                for part in value.replace("；", ",").replace(";", ",").split(",")
            )
        elif isinstance(value, (list, tuple)):
            values = tuple(value)
        else:
            return ()
        return tuple(
            normalize_research_tone(str(item).strip())
            for item in values
            if str(item).strip()
        )

    def _dedupe_debate_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep one newest same-day conclusion per symbol for display consumers.

        A symbol can have intraday and midday debate rows.  Those are runtime
        attempts, not separate candidates; displaying both made one candidate
        occupy multiple committee cards and pushed other candidates out.
        """
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            key = (
                self._debate_signal_date(row),
                str(row.get("symbol", "") or "").strip(),
            )
            if not key[0] or not key[1]:
                continue
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = row
                continue
            row_has_evidence = self._has_debate_evidence(row)
            existing_has_evidence = self._has_debate_evidence(existing)
            if row_has_evidence != existing_has_evidence:
                if row_has_evidence:
                    grouped[key] = row
                continue
            if self._debate_recency_key(row) > self._debate_recency_key(existing):
                grouped[key] = row
                continue
            if self._debate_recency_key(row) == self._debate_recency_key(
                existing
            ) and self._debate_quality_key(row) > self._debate_quality_key(existing):
                grouped[key] = row
        return list(grouped.values())

    def _debate_has_cross_market_evidence(self, row: dict[str, Any]) -> bool:
        """Return whether a stored debate has attributable cross-market evidence."""
        context_lines = self._as_text_tuple(row.get("market_context_lines"))
        if any(
            marker in line
            for line in context_lines
            for marker in ("消息结果: 无可用新闻记录", "无可用新闻记录")
        ):
            return False
        if any(
            self._as_text_tuple(row.get(field))
            for field in ("real_message_evidence", "rule_transmission_evidence")
        ):
            return True
        if int(row.get("cross_market_support_event_count") or 0) > 0:
            return True
        if int(row.get("cross_market_conflict_event_count") or 0) > 0:
            return True
        if str(row.get("cross_market_evidence_stack_summary", "") or "").strip():
            return True
        return any(
            str(row.get(field, "") or "").strip()
            for field in (
                "cross_market_primary_theme",
                "cross_market_linkage_basis",
                "cross_market_transmission_hypothesis",
                "cross_market_chain_summary",
            )
        )

    @staticmethod
    def _sanitize_unsupported_cross_market_text(
        value: str,
        *,
        has_evidence: bool,
    ) -> str:
        """Remove legacy overseas claims when the row carries no evidence."""
        text = str(value or "").strip()
        if has_evidence or not text:
            return text
        for phrase in (
            "跨市传导: ⚠️ 海外叙事未必立刻传到A股，需确认板块共振",
            "跨市传导: 海外叙事未必立刻传到A股，需确认板块共振",
            "跨市传导: 跨市场线索存在，但仍需确认是否形成A股主线接力",
            "核对海外风险线索是否延续，避免隔夜外盘噪音误导。",
        ):
            text = text.replace("，但卡点是 " + phrase, "")
            text = text.replace("，卡点是 " + phrase, "")
            text = text.replace(phrase, "")
        return text.strip(" ，；")

    def _debate_signal_date(self, row: dict[str, Any]) -> str:
        return (
            str(row.get("related_signal_date", "") or "").strip()
            or str(row.get("signal_date", "") or "").strip()
        )

    def _has_debate_evidence(self, row: dict[str, Any]) -> bool:
        if not self._debate_signal_date(row):
            return False
        if not str(row.get("symbol", "") or "").strip():
            return False
        if self._explicit_false(row.get("process_recorded")):
            return False
        if self._explicit_false(row.get("conclusion_recorded")):
            return False
        if self._explicit_false(row.get("advisory_boundary_ok")):
            return False
        if self._explicit_false(row.get("evidence_sufficient")):
            return False
        quality_issues = self._as_text_tuple(row.get("debate_quality_issues"))
        if quality_issues:
            return False
        task_id = str(row.get("task_id", "") or "").strip()
        if not task_id:
            # Legacy records predate task metadata; keep their established display
            # contract while new task-scoped records use the stricter gate below.
            return self._debate_evidence_score(row) > 0

        rounds = self._debate_round_dicts(row)
        has_opinions = any(
            isinstance(round_data.get("opinions"), list)
            and any(isinstance(item, dict) for item in round_data["opinions"])
            for round_data in rounds
        )
        has_round_summary = any(
            str(round_data.get("summary", "") or "").strip() for round_data in rounds
        )
        has_conclusion = any(
            str(row.get(field, "") or "").strip()
            for field in (
                "research_verdict",
                "final_consensus",
                "primary_risk_gate",
                "next_trigger",
            )
        )
        return bool(
            self._debate_vote_map(row)
            and rounds
            and (has_opinions or has_round_summary)
            and has_conclusion
        )

    @staticmethod
    def _explicit_false(value: Any) -> bool:
        return value is False or str(value or "").strip().lower() in {
            "0",
            "false",
            "no",
        }

    def _debate_evidence_score(self, row: dict[str, Any]) -> int:
        return sum(
            (
                4 if self._debate_vote_map(row) else 0,
                3 if self._debate_round_dicts(row) else 0,
                2 if str(row.get("final_consensus", "") or "").strip() else 0,
                2 if str(row.get("adjustment_reason", "") or "").strip() else 0,
                1 if self._as_text_tuple(row.get("risk_warnings")) else 0,
                1 if self._as_text_tuple(row.get("opportunity_highlights")) else 0,
            )
        )

    def _debate_quality_key(
        self, row: dict[str, Any]
    ) -> tuple[int, int, str, str, float]:
        context_quality = str(row.get("debate_context_quality", "") or "").strip()
        context_rank = {
            "structured_context": 2,
            "": 1,
            "thin_context": 0,
        }.get(context_quality, 1)
        return (
            context_rank,
            self._debate_evidence_score(row),
            self._debate_signal_date(row),
            str(row.get("created_at", "") or "").strip(),
            float(row.get("adjusted_score") or row.get("original_score") or 0.0),
        )

    @staticmethod
    def _debate_recency_key(row: dict[str, Any]) -> tuple[str, str]:
        return (
            str(row.get("created_at", "") or "").strip(),
            str(row.get("debate_id", "") or "").strip(),
        )

    def _debate_row_key(self, row: dict[str, Any]) -> tuple[str, str, float]:
        return (
            self._debate_signal_date(row),
            str(row.get("created_at", "") or "").strip(),
            float(row.get("adjusted_score") or row.get("original_score") or 0.0),
        )

    def _build_debate_summary(
        self,
        row: dict[str, Any],
    ) -> DashboardDebateSummary:
        has_cross_market_evidence = self._debate_has_cross_market_evidence(row)
        vote_map = self._debate_vote_map(row)
        bull_count = sum(1 for stance in vote_map.values() if stance == "bullish")
        bear_count = sum(1 for stance in vote_map.values() if stance == "bearish")
        neutral_count = sum(1 for stance in vote_map.values() if stance == "neutral")
        round_summaries = self._debate_round_summaries(row)
        risk_warnings = self._as_text_tuple(row.get("risk_warnings"))
        opportunity_highlights = self._as_text_tuple(row.get("opportunity_highlights"))
        agent_views = self._debate_agent_views(
            row,
            vote_map=vote_map,
            has_cross_market_evidence=has_cross_market_evidence,
        )
        support_points = tuple(
            self._sanitize_unsupported_cross_market_text(
                item,
                has_evidence=has_cross_market_evidence,
            )
            for item in self._as_text_tuple(row.get("support_points"))
            if self._sanitize_unsupported_cross_market_text(
                item,
                has_evidence=has_cross_market_evidence,
            )
        )
        opposition_points = tuple(
            self._sanitize_unsupported_cross_market_text(
                item,
                has_evidence=has_cross_market_evidence,
            )
            for item in self._as_text_tuple(row.get("opposition_points"))
            if self._sanitize_unsupported_cross_market_text(
                item,
                has_evidence=has_cross_market_evidence,
            )
        )
        watch_items = self._as_text_tuple(row.get("watch_items"))
        recommended_adjustment = str(
            row.get("recommended_adjustment", "") or ""
        ).strip()
        display_name = format_symbol_name(
            str(row.get("symbol", "") or ""),
            str(row.get("name", "") or ""),
        )
        summary_lines = self._debate_summary_lines(
            row=row,
            bull_count=bull_count,
            bear_count=bear_count,
            neutral_count=neutral_count,
            risk_warnings=risk_warnings,
            opportunity_highlights=opportunity_highlights,
            agent_views=agent_views,
            has_cross_market_evidence=has_cross_market_evidence,
        )
        return DashboardDebateSummary(
            signal_date=self._debate_signal_date(row),
            symbol=str(row.get("symbol", "") or "").strip(),
            display_name=display_name,
            debate_id=str(row.get("debate_id", "") or "").strip(),
            rating=str(row.get("rating", "") or "").strip(),
            original_score=float(row.get("original_score") or 0.0),
            adjusted_score=float(
                row.get("adjusted_score") or row.get("original_score") or 0.0
            ),
            adjustment_weight=float(row.get("adjustment_weight") or 0.0),
            recommended_adjustment=recommended_adjustment,
            recommended_adjustment_label=self._debate_adjustment_label(
                recommended_adjustment
            ),
            disagreement_score=float(row.get("disagreement_score") or 0.0),
            consensus=str(row.get("final_consensus", "") or "").strip(),
            adjustment_reason=str(row.get("adjustment_reason", "") or "").strip(),
            bull_count=bull_count,
            bear_count=bear_count,
            neutral_count=neutral_count,
            round_count=len(self._debate_round_dicts(row)),
            regime=str(row.get("regime", "") or "").strip(),
            data_source=str(row.get("data_source", "") or "").strip(),
            thresholds_version=str(row.get("thresholds_version", "") or "").strip(),
            summary_lines=summary_lines,
            round_summaries=round_summaries,
            risk_warnings=risk_warnings,
            opportunity_highlights=opportunity_highlights,
            agent_views=agent_views,
            cross_market_summary=(
                self._spotlight_cross_market_summary(row)
                if has_cross_market_evidence
                else ""
            ),
            cross_market_chain_summary=(
                self._spotlight_cross_market_chain_summary(row)
                if has_cross_market_evidence
                else ""
            ),
            cross_market_validation_summary=(
                self._spotlight_cross_market_validation_summary(row)
                if has_cross_market_evidence
                else ""
            ),
            cross_market_invalidation_summary=(
                self._spotlight_cross_market_invalidation_summary(row)
                if has_cross_market_evidence
                else ""
            ),
            research_verdict=self._sanitize_unsupported_cross_market_text(
                str(row.get("research_verdict", "") or ""),
                has_evidence=has_cross_market_evidence,
            ),
            primary_risk_gate=(
                self._sanitize_unsupported_cross_market_text(
                    str(row.get("primary_risk_gate", "") or ""),
                    has_evidence=has_cross_market_evidence,
                )
                or (
                    "消息或规则传导证据缺失，跨市视角不形成结论。"
                    if not has_cross_market_evidence
                    else ""
                )
            ),
            next_trigger=(
                self._sanitize_unsupported_cross_market_text(
                    str(row.get("next_trigger", "") or ""),
                    has_evidence=has_cross_market_evidence,
                )
                or "等待实时量价与消息证据确认。"
            ),
            historical_context_note=str(
                row.get("historical_context_note", "") or ""
            ).strip(),
            role_reliability_lines=self._as_text_tuple(
                row.get("role_reliability_lines")
            ),
            role_selection_summary=str(
                row.get("role_selection_summary", "") or ""
            ).strip(),
            role_selection_plan=str(row.get("role_selection_plan", "") or "").strip(),
            support_points=support_points,
            opposition_points=opposition_points,
            watch_items=watch_items,
            created_at=str(row.get("created_at", "") or "").strip(),
            candidate_fingerprint=str(
                self._candidate_fingerprint_for_row(row) or ""
            ).strip(),
        )

    def _debate_vote_map(self, row: dict[str, Any]) -> dict[str, str]:
        vote_map: dict[str, str] = {}
        raw_vote = row.get("final_vote")
        if isinstance(raw_vote, dict):
            for role, stance in raw_vote.items():
                role_id = str(role).strip()
                stance_value = str(stance).strip()
                if role_id and stance_value:
                    vote_map[role_id] = stance_value
        if vote_map:
            return vote_map

        for opinion in self._debate_final_round_opinions(row):
            role_id = str(opinion.get("role", "") or "").strip()
            stance_value = str(
                opinion.get("final_position") or opinion.get("stance") or ""
            ).strip()
            if role_id and stance_value:
                vote_map[role_id] = stance_value
        return vote_map

    def _debate_round_dicts(self, row: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        rounds = row.get("rounds")
        if not isinstance(rounds, list):
            return ()
        return tuple(item for item in rounds if isinstance(item, dict))

    def _debate_final_round_opinions(
        self,
        row: dict[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        rounds = self._debate_round_dicts(row)
        if not rounds:
            return ()
        final_round = max(rounds, key=self._debate_round_sort_key)
        opinions = final_round.get("opinions")
        if not isinstance(opinions, list):
            return ()
        return tuple(item for item in opinions if isinstance(item, dict))

    def _debate_round_sort_key(self, row: dict[str, Any]) -> tuple[int, str]:
        try:
            round_number = int(row.get("round_num") or row.get("round") or 0)
        except (TypeError, ValueError):
            round_number = 0
        return (round_number, str(row.get("created_at", "") or ""))

    def _debate_round_summaries(self, row: dict[str, Any]) -> tuple[str, ...]:
        return tuple(
            summary
            for summary in (
                str(round_data.get("summary", "") or "").strip()
                for round_data in self._debate_round_dicts(row)
            )
            if summary
        )

    def _debate_agent_views(
        self,
        row: dict[str, Any],
        *,
        vote_map: dict[str, str],
        has_cross_market_evidence: bool = True,
    ) -> tuple[DashboardDebateAgentView, ...]:
        opinion_map: dict[str, dict[str, Any]] = {}
        for opinion in self._debate_final_round_opinions(row):
            role_id = str(opinion.get("role", "") or "").strip()
            if role_id:
                opinion_map[role_id] = opinion

        role_ids = set(vote_map) | set(opinion_map)
        ordered_role_ids = sorted(
            role_ids,
            key=lambda role_id: (
                self._debate_role_sort_index(role_id),
                role_id,
            ),
        )
        agent_views: list[DashboardDebateAgentView] = []
        for role_id in ordered_role_ids:
            opinion = opinion_map.get(role_id, {})
            vote_stance = str(vote_map.get(role_id, "") or "").strip()
            opinion_stance = str(
                opinion.get("final_position") or opinion.get("stance") or ""
            ).strip()
            stance = vote_stance or opinion_stance or "neutral"
            arguments = self._as_text_tuple(opinion.get("arguments"))
            risks = self._as_text_tuple(opinion.get("risk_factors"))
            opportunities = self._as_text_tuple(opinion.get("opportunity_factors"))
            stance_label = self._debate_stance_label(stance)
            key_argument = arguments[0] if arguments else ""
            key_risk = risks[0] if risks else ""
            key_opportunity = opportunities[0] if opportunities else ""
            if vote_stance and opinion_stance and vote_stance != opinion_stance:
                stance_label = f"{stance_label}（发言冲突）"
                key_argument = (
                    "最终投票与发言不一致: "
                    f"投票{self._debate_stance_label(vote_stance)}，"
                    f"发言{self._debate_stance_label(opinion_stance)}，需人工复核"
                )
            if role_id == "cross_market" and not has_cross_market_evidence:
                key_argument = ""
                key_risk = ""
                key_opportunity = ""
            agent_views.append(
                DashboardDebateAgentView(
                    role_id=role_id,
                    role_label=self._debate_role_label(role_id),
                    stance=stance,
                    stance_label=stance_label,
                    confidence=float(opinion.get("confidence") or 0.0),
                    key_argument=key_argument,
                    key_risk=key_risk,
                    key_opportunity=key_opportunity,
                )
            )
        return tuple(agent_views)

    def _debate_summary_lines(
        self,
        *,
        row: dict[str, Any],
        bull_count: int,
        bear_count: int,
        neutral_count: int,
        risk_warnings: tuple[str, ...],
        opportunity_highlights: tuple[str, ...],
        agent_views: tuple[DashboardDebateAgentView, ...],
        has_cross_market_evidence: bool = True,
    ) -> tuple[str, ...]:
        recommended_adjustment = str(
            row.get("recommended_adjustment", "") or ""
        ).strip()
        lines: list[str] = []
        if (
            row.get("original_score") is not None
            or row.get("adjusted_score") is not None
        ):
            adjustment = recommended_adjustment or "unknown"
            summary_label = self._debate_summary_adjustment_label(adjustment)
            lines.append(
                normalize_research_tone(
                    f"{summary_label}: "
                    f"runtime原始分 {float(row.get('original_score') or 0.0):.1f}；"
                    f"附件参考分 {float(row.get('adjusted_score') or row.get('original_score') or 0.0):.1f}；"
                    "不覆盖runtime打分"
                )
            )
        elif recommended_adjustment:
            lines.append(
                normalize_research_tone(
                    f"辩论倾向: {self._debate_adjustment_label(recommended_adjustment)}；不覆盖runtime打分"
                )
            )
        market_context = self._as_text_tuple(row.get("market_context_lines"))
        if market_context:
            lines.append(normalize_research_tone(f"市场上下文: {market_context[0]}"))
        active_role_summary = self._debate_active_role_summary(agent_views)
        if active_role_summary:
            lines.append(f"讨论视角: {active_role_summary}")
        if bull_count + bear_count + neutral_count:
            lines.append(
                f"投票分布: 看多 {bull_count} / 看空 {bear_count} / 中性 {neutral_count}"
            )
        consensus = self._sanitize_unsupported_cross_market_text(
            str(row.get("final_consensus", "") or ""),
            has_evidence=has_cross_market_evidence,
        )
        adjustment_reason = str(row.get("adjustment_reason", "") or "").strip()
        research_verdict = self._sanitize_unsupported_cross_market_text(
            str(row.get("research_verdict", "") or ""),
            has_evidence=has_cross_market_evidence,
        )
        primary_risk_gate = self._sanitize_unsupported_cross_market_text(
            str(row.get("primary_risk_gate", "") or ""),
            has_evidence=has_cross_market_evidence,
        )
        next_trigger = self._sanitize_unsupported_cross_market_text(
            str(row.get("next_trigger", "") or ""),
            has_evidence=has_cross_market_evidence,
        )
        role_reliability_lines = self._as_text_tuple(row.get("role_reliability_lines"))
        if research_verdict:
            lines.append(f"研究口径: {research_verdict}")
        if primary_risk_gate:
            lines.append(f"核心卡点: {primary_risk_gate}")
        elif not has_cross_market_evidence:
            lines.append("核心卡点: 消息或规则传导证据缺失，跨市视角不形成结论。")
        if next_trigger:
            lines.append(f"下一触发: {next_trigger}")
        if role_reliability_lines:
            lines.append(f"角色可信度: {role_reliability_lines[0]}")
        if consensus:
            lines.append(f"委员会共识: {consensus}")
        if adjustment_reason and adjustment_reason != consensus:
            lines.append(f"复核原因: {adjustment_reason}")
        support_points = self._as_text_tuple(row.get("support_points"))
        opposition_points = self._as_text_tuple(row.get("opposition_points"))
        watch_items = self._as_text_tuple(row.get("watch_items"))
        if support_points:
            lines.append(f"支持观点: {support_points[0]}")
        if opposition_points:
            lines.append(f"反对观点: {opposition_points[0]}")
        if watch_items:
            lines.append(f"待确认: {watch_items[0]}")
        if risk_warnings:
            lines.append(f"核心风险: {risk_warnings[0]}")
        if opportunity_highlights:
            lines.append(f"机会线索: {opportunity_highlights[0]}")
        return tuple(lines[:5])

    def _debate_active_role_summary(
        self,
        agent_views: tuple[DashboardDebateAgentView, ...],
    ) -> str:
        labels: list[str] = []
        for view in agent_views:
            label = str(view.role_label or "").strip()
            if label and label not in labels:
                labels.append(label)
        if not labels:
            return ""
        if len(labels) <= 5:
            return "、".join(labels)
        return "、".join(labels[:5]) + f" 等 {len(labels)} 个角色"

    def _debate_role_sort_index(self, role_id: str) -> int:
        try:
            return _DEBATE_ROLE_ORDER.index(role_id)
        except ValueError:
            return len(_DEBATE_ROLE_ORDER)

    def _debate_role_label(self, role_id: str) -> str:
        return _DEBATE_ROLE_LABELS.get(role_id, role_id)

    def _debate_stance_label(self, stance: str) -> str:
        return _DEBATE_STANCE_LABELS.get(stance, stance or "未表态")

    def _debate_adjustment_label(self, adjustment: str) -> str:
        return _DEBATE_ADJUSTMENT_LABELS.get(adjustment, adjustment or "辩论证据待补全")

    def _debate_summary_adjustment_label(self, adjustment: str) -> str:
        label = self._debate_adjustment_label(adjustment)
        return label.replace("辩论倾向", "讨论倾向", 1)

    def _build_detail_cards(
        self,
        rows: list[dict[str, Any]],
        *,
        limit: int | None = 10,
        task_id: str = "",
    ) -> tuple[DashboardCandidateCard, ...]:
        ordered = sorted(rows, key=self._candidate_sort_key, reverse=True)
        if limit is not None:
            ordered = ordered[:limit]
        cards: list[DashboardCandidateCard] = []
        for index, row in enumerate(ordered, start=1):
            row = self._merge_debate_evidence(row)
            strategies = row.get("strategies") or []
            if isinstance(strategies, str):
                strategies_tuple = tuple(
                    item.strip() for item in strategies.split(",") if item.strip()
                )
            else:
                strategies_tuple = tuple(
                    str(item).strip() for item in strategies if str(item).strip()
                )
            cards.append(
                DashboardCandidateCard(
                    symbol=str(row.get("symbol", "") or ""),
                    name=str(row.get("name", "") or ""),
                    display_name=self._symbol_name(row),
                    rank_label=self._rank_label(index, row, task_id=task_id),
                    score=float(row.get("score") or 0.0),
                    action_label=self._action_label(row),
                    status_label=self._candidate_status(row),
                    decision_note=self._decision_note(row),
                    next_step=self._next_step_text(row),
                    blocker=self._candidate_blocker_text(row),
                    review_meta=self._review_meta(row),
                    reasons=self._as_text_tuple(row.get("reasons")),
                    risks=self._as_text_tuple(row.get("risks")),
                    strategies=strategies_tuple,
                    data_source=str(row.get("run_actual_source", "") or ""),
                    candidate_fingerprint=self._candidate_fingerprint_for_row(row),
                    news_catalyst_summary=self._spotlight_news_catalyst_summary(row),
                    cross_market_summary=self._spotlight_cross_market_summary(row),
                    cross_market_chain_summary=(
                        self._spotlight_cross_market_chain_summary(row)
                    ),
                    cross_market_validation_summary=(
                        self._spotlight_cross_market_validation_summary(row)
                    ),
                    cross_market_invalidation_summary=(
                        self._spotlight_cross_market_invalidation_summary(row)
                    ),
                    close=_technical_metric_value(row, "close", "signal_close"),
                    ret5_pct=_technical_metric_value(row, "ret5_pct"),
                    ret20_pct=_technical_metric_value(row, "ret20_pct"),
                    volume_ratio=_technical_metric_value(row, "volume_ratio"),
                    rsi12=_technical_metric_value(row, "rsi12"),
                    bias20_pct=_technical_metric_value(row, "bias20_pct"),
                    stop_loss=_technical_metric_value(row, "stop_loss"),
                    take_profit=_technical_metric_value(row, "take_profit"),
                )
            )
        return tuple(cards)

    def _rank_label(self, index: int, row: dict[str, Any], task_id: str = "") -> str:
        if index == 1 and self._is_actionable(row, task_id=task_id):
            return "第一顺位"
        if index == 2 and self._is_actionable(row, task_id=task_id):
            return "第二顺位"
        if self._is_actionable(row, task_id=task_id):
            return "后续顺位"
        if self._is_blocked(row):
            return "阻塞观察"
        return "观察"

    def _decision_note(self, row: dict[str, Any]) -> str:
        blocker = self._candidate_blocker_text(row)
        next_step = self._next_step_text(row)
        action = str(row.get("portfolio_action", "") or "").strip()
        cross_market = self._spotlight_cross_market_summary(row)
        cross_market_chain = self._spotlight_cross_market_chain_summary(row)
        cross_market_validation = self._spotlight_cross_market_validation_summary(row)
        cross_market_invalidation = self._spotlight_cross_market_invalidation_summary(
            row
        )
        history_note = self._discussion_history_note(row)
        active_role_summary = str(
            row.get("debate_active_role_summary", "") or ""
        ).strip()
        verdict_source, debate_verdict = self._split_backfilled_evidence_text(
            str(row.get("debate_research_verdict", "") or "")
        )
        risk_source, debate_risk_gate = self._split_backfilled_evidence_text(
            str(row.get("debate_primary_risk_gate", "") or "")
        )
        debate_source = verdict_source or risk_source
        context_warning = (
            "讨论上下文较薄，结论仅作低置信度观察"
            if str(row.get("debate_context_quality", "") or "").strip()
            == "thin_context"
            else ""
        )
        mapping_parts = tuple(
            part
            for part in (
                (f"跨市线索 {cross_market}" if cross_market else ""),
                (f"传导链 {cross_market_chain}" if cross_market_chain else ""),
                (
                    f"确认信号 {cross_market_validation}"
                    if cross_market_validation
                    else ""
                ),
                (
                    f"失效信号 {cross_market_invalidation}"
                    if cross_market_invalidation
                    else ""
                ),
                context_warning,
            )
            if part
        )
        if debate_verdict and debate_risk_gate:
            note = f"{debate_verdict}，但先卡住 {debate_risk_gate}"
            if debate_source:
                note = f"{debate_source}: {note}"
            if mapping_parts:
                note += "；" + "；".join(mapping_parts)
            if active_role_summary:
                note += f"；讨论视角 {active_role_summary}"
            if history_note:
                note += f"；{history_note}"
            return normalize_research_tone(note)
        if blocker:
            return blocker
        if debate_risk_gate:
            note = (
                f"{debate_source}: {debate_risk_gate}"
                if debate_source
                else debate_risk_gate
            )
            return normalize_research_tone(note)
        if debate_verdict:
            note = debate_verdict
            if debate_source:
                note = f"{debate_source}: {note}"
            if mapping_parts:
                note += "；" + "；".join(mapping_parts)
            if active_role_summary:
                note += f"；讨论视角 {active_role_summary}"
            if history_note:
                note += f"；{history_note}"
            return normalize_research_tone(note)
        if action == "promote":
            note = "PM 已上调优先级，进入优先跟踪序列"
            if mapping_parts:
                note += "；" + "；".join(mapping_parts)
            if history_note:
                note += f"；{history_note}"
            return normalize_research_tone(note)
        if action == "keep":
            note = "维持顺位，等待更强确认"
            if mapping_parts:
                note += "；" + "；".join(mapping_parts)
            if history_note:
                note += f"；{history_note}"
            return normalize_research_tone(note)
        if next_step:
            if mapping_parts:
                note = "；".join((*mapping_parts, next_step))
                if history_note:
                    note += f"；{history_note}"
                return normalize_research_tone(note)
            if history_note:
                return normalize_research_tone(f"{next_step}；{history_note}")
            return normalize_research_tone(next_step)
        if mapping_parts:
            note = "；".join(mapping_parts)
            if history_note:
                note += f"；{history_note}"
            return normalize_research_tone(note)
        if history_note:
            return normalize_research_tone(history_note)
        return normalize_research_tone("按当前顺位继续跟踪")

    def _cross_market_summary(self, row: dict[str, Any]) -> str:
        theme = str(row.get("cross_market_primary_theme", "") or "").strip()
        action = str(row.get("cross_market_action", "") or "").strip()
        evidence_stack = str(
            row.get("cross_market_evidence_stack_summary", "") or ""
        ).strip()
        if not theme:
            return ""
        stack_suffix = f"｜{evidence_stack}" if evidence_stack else ""
        if action:
            return normalize_research_tone(f"{theme}({action}){stack_suffix}")
        return normalize_research_tone(f"{theme}{stack_suffix}")

    def _spotlight_cross_market_summary(self, row: dict[str, Any]) -> str:
        _, theme = self._split_backfilled_evidence_text(
            str(row.get("cross_market_primary_theme", "") or "")
        )
        _, action = self._split_backfilled_evidence_text(
            str(row.get("cross_market_action", "") or "")
        )
        _, evidence_stack = self._split_backfilled_evidence_text(
            str(row.get("cross_market_evidence_stack_summary", "") or "")
        )
        if not theme:
            return ""
        stack_suffix = f"｜{evidence_stack}" if evidence_stack else ""
        if action:
            return normalize_research_tone(f"{theme}({action}){stack_suffix}")
        return normalize_research_tone(f"{theme}{stack_suffix}")

    def _spotlight_news_catalyst_summary(self, row: dict[str, Any]) -> str:
        judgement = str(row.get("news_catalyst_judgement", "") or "").strip()
        if not judgement:
            return ""
        label = {
            "supports": "消息支持",
            "opposes": "消息反对",
            "mixed": "消息分歧",
            "needs_review": "消息待复核",
        }.get(judgement, "消息观察")
        lead = str(row.get("news_catalyst_lead", "") or "").strip()
        if not lead:
            for field in (
                "news_catalyst_opposes",
                "news_catalyst_supports",
                "news_catalyst_needs_review",
            ):
                values = self._as_text_tuple(row.get(field))
                if values:
                    lead = values[0]
                    break
        source = str(row.get("news_catalyst_source", "") or "").strip()
        source_suffix = f"｜{source}" if source and source not in lead else ""
        summary = f"{label}: {lead}{source_suffix}" if lead else label
        return normalize_research_tone(summary)

    def _spotlight_cross_market_chain_summary(self, row: dict[str, Any]) -> str:
        _, chain_summary = self._split_backfilled_evidence_text(
            str(row.get("cross_market_chain_summary", "") or "")
        )
        return normalize_research_tone(chain_summary) if chain_summary else ""

    def _cross_market_reason_summary(self, row: dict[str, Any]) -> str:
        chain_summary = self._spotlight_cross_market_chain_summary(row)
        if chain_summary:
            kept_segments = tuple(
                segment.strip()
                for segment in chain_summary.split("｜")
                if segment.strip()
                and not segment.strip().startswith(
                    ("确认 ", "失效 ", "同向 ", "反向 ", "触发 ")
                )
            )
            if kept_segments:
                return normalize_research_tone("｜".join(kept_segments[:3]))
        _, linkage_basis = self._split_backfilled_evidence_text(
            str(row.get("cross_market_linkage_basis", "") or "")
        )
        if linkage_basis:
            return normalize_research_tone(linkage_basis)
        transmission_path = self._as_text_tuple(
            row.get("cross_market_transmission_path")
        )
        if transmission_path:
            return normalize_research_tone(transmission_path[0])
        return ""

    def _execution_focus_cross_market_lines(
        self,
        row: dict[str, Any],
    ) -> tuple[str, ...]:
        summary = self._spotlight_cross_market_summary(
            row
        ) or self._cross_market_summary(row)
        reason_summary = self._cross_market_reason_summary(row)
        validation_summary = self._spotlight_cross_market_validation_summary(row)
        invalidation_summary = self._spotlight_cross_market_invalidation_summary(row)
        logic_parts = tuple(
            part
            for part in (
                summary,
                f"映射 {reason_summary}" if reason_summary else "",
            )
            if part
        )
        return tuple(
            line
            for line in (
                ("跨市逻辑: " + " | ".join(logic_parts) if logic_parts else ""),
                (f"确认信号: {validation_summary}" if validation_summary else ""),
                (f"失效信号: {invalidation_summary}" if invalidation_summary else ""),
            )
            if line
        )

    def _extract_chain_marker_summary(self, chain_summary: str, marker: str) -> str:
        normalized_chain = normalize_research_tone(chain_summary).strip()
        if not normalized_chain:
            return ""
        prefix = f"{marker} "
        for segment in normalized_chain.split("｜"):
            clean_segment = segment.strip()
            if clean_segment.startswith(prefix):
                return clean_segment[len(prefix) :].strip()
        return ""

    def _spotlight_cross_market_validation_summary(self, row: dict[str, Any]) -> str:
        signals = self._as_text_tuple(row.get("cross_market_validation_signals"))
        if not signals:
            return self._extract_chain_marker_summary(
                self._spotlight_cross_market_chain_summary(row),
                "确认",
            )
        _, summary = self._split_backfilled_evidence_text(str(signals[0]))
        return normalize_research_tone(summary) if summary else ""

    def _spotlight_cross_market_invalidation_summary(self, row: dict[str, Any]) -> str:
        signals = self._as_text_tuple(row.get("cross_market_invalidation_signals"))
        if not signals:
            return self._extract_chain_marker_summary(
                self._spotlight_cross_market_chain_summary(row),
                "失效",
            )
        _, summary = self._split_backfilled_evidence_text(str(signals[0]))
        return normalize_research_tone(summary) if summary else ""

    def _discussion_history_note(self, row: dict[str, Any]) -> str:
        _, note = self._split_backfilled_evidence_text(
            str(row.get("debate_historical_context_note", "") or "")
        )
        sample_count = int(row.get("debate_historical_context_sample_count", 0) or 0)
        if not note or sample_count <= 0:
            return ""
        return normalize_research_tone(note)

    def _discussion_history_bucket_rank(self, row: dict[str, Any]) -> int:
        bucket = str(row.get("debate_historical_context_bucket", "") or "").strip()
        sample_count = int(row.get("debate_historical_context_sample_count", 0) or 0)
        if sample_count <= 0:
            return 0
        return {
            "strong_supportive": 5,
            "supportive": 4,
            "conflicted": 3,
            "unknown": 2,
            "conflicts_dominate": 1,
        }.get(bucket, 1)

    def _discussion_history_accuracy_score(self, row: dict[str, Any]) -> int:
        sample_count = int(row.get("debate_historical_context_sample_count", 0) or 0)
        accuracy = float(row.get("debate_historical_context_accuracy", 0.0) or 0.0)
        if sample_count < 3:
            return 0
        return int(accuracy * 1000)

    def _debate_active_role_summary_for_row(self, row: dict[str, Any]) -> str:
        signal_date = str(row.get("signal_date", "") or "").strip()
        symbol = str(row.get("symbol", "") or "").strip()
        if not signal_date or not symbol:
            return ""
        fingerprint = self._candidate_fingerprint_for_row(row)
        if fingerprint:
            matches = [
                debate_row
                for debate_row in self._load_debate_rows()
                if self._debate_signal_date(debate_row) == signal_date
                and str(debate_row.get("symbol", "") or "").strip() == symbol
                and self._candidate_fingerprint_for_row(debate_row) == fingerprint
                and self._has_debate_evidence(debate_row)
            ]
            debate_summary = (
                self._build_debate_summary(max(matches, key=self._debate_quality_key))
                if matches
                else None
            )
        else:
            debate_summary = self.debate_summary(signal_date=signal_date, symbol=symbol)
        if debate_summary is None:
            return ""
        return self._debate_active_role_summary(debate_summary.agent_views)

    def _build_ranking_lines(
        self,
        rows: list[dict[str, Any]],
        *,
        limit: int = 3,
    ) -> tuple[str, ...]:
        ordered = sorted(rows, key=self._candidate_sort_key, reverse=True)[:limit]
        lines: list[str] = []
        for index, row in enumerate(ordered, start=1):
            rank_label = self._rank_label(index, row)
            lines.append(
                f"{rank_label}: {self._symbol_name(row)} | {self._action_label(row)}"
                f" | 评分 {float(row.get('score') or 0.0):.1f}"
                f" | {self._decision_note(row)}"
            )
        return tuple(lines)

    def _build_lifecycle_lines(
        self,
        rows: list[dict[str, Any]],
        *,
        limit: int = 5,
    ) -> tuple[str, ...]:
        ordered = sorted(rows, key=self._candidate_sort_key, reverse=True)[:limit]
        lines: list[str] = []
        for row in ordered:
            parts = [
                self._symbol_name(row),
                self._action_label(row),
                self._candidate_status(row),
            ]
            meta = self._review_meta(row)
            if meta:
                parts.append(meta)
            decision_note = self._decision_note(row)
            if decision_note:
                parts.append(decision_note)
            lines.append(" | ".join(part for part in parts if part))
        return tuple(lines)

    def _build_unlock_lines(
        self,
        *,
        watch_rows: list[dict[str, Any]],
        blocked_rows: list[dict[str, Any]],
        limit: int = 5,
    ) -> tuple[str, ...]:
        lines: list[str] = []
        for row in blocked_rows[:limit]:
            next_step = self._next_step_text(row)
            blocker = self._candidate_blocker_text(row) or "等待条件解除"
            line = normalize_research_tone(
                f"{self._symbol_name(row)} | 当前限制: {blocker}"
            )
            if next_step:
                line += f" | 再看动作: {next_step}"
            lines.append(line)
        remaining_slots = max(limit - len(lines), 0)
        if remaining_slots <= 0:
            return tuple(lines[:limit])
        for row in watch_rows[:remaining_slots]:
            if row in blocked_rows:
                continue
            next_step = self._next_step_text(row)
            meta = self._review_meta(row)
            line = f"{self._symbol_name(row)} | 继续观察"
            if next_step:
                line += f" | 触发条件: {next_step}"
            if meta:
                line += f" | {meta}"
            lines.append(line)
            if len(lines) >= limit:
                break
        return tuple(lines[:limit])

    def _format_strategy_breakdown_lines(
        self,
        strategy_breakdown: dict[str, dict[str, Any]],
    ) -> tuple[str, ...]:
        ordered = sorted(
            strategy_breakdown.items(),
            key=lambda item: (
                float(item[1].get("total_return", 0.0) or 0.0),
                int(item[1].get("total", 0) or 0),
            ),
            reverse=True,
        )
        return tuple(
            (
                f"{strategy} | {int(stats.get('total', 0) or 0)}笔"
                f" / 胜率 {float(stats.get('win_rate', 0.0) or 0.0):.0%}"
                f" / 收益 {float(stats.get('total_return', 0.0) or 0.0):.2f}%"
            )
            for strategy, stats in ordered
        )

    def _recommendation_line(self, row: dict[str, Any]) -> str:
        line = (
            f"{self._symbol_name(row)} | {self._candidate_status(row)}"
            f" | 评分 {float(row.get('score') or 0.0):.1f}"
        )
        next_step = self._next_step_text(row)
        if next_step:
            line += f" | {next_step}"
        return line

    def _watch_line(self, row: dict[str, Any]) -> str:
        line = f"{self._symbol_name(row)} | {self._candidate_status(row)}"
        meta = self._review_meta(row)
        if meta:
            line += f" | {meta}"
        return line

    def _blocker_line(self, row: dict[str, Any]) -> str:
        blocker = self._candidate_blocker_text(row) or "等待条件解除"
        return f"{self._symbol_name(row)} | {blocker}"

    def _review_line(self, row: dict[str, Any]) -> str:
        next_step = self._next_step_text(row)
        meta = self._review_meta(row)
        if not next_step and not meta:
            return ""
        line = self._symbol_name(row)
        if meta:
            line += f" | {meta}"
        if next_step:
            line += f" | {next_step}"
        return line

    def _headline_for_signal_task(
        self,
        *,
        task_id: str,
        signal_date: str,
        actionable_rows: list[dict[str, Any]],
        watch_rows: list[dict[str, Any]],
        blocked_rows: list[dict[str, Any]],
    ) -> str:
        label = _TASK_LABELS[task_id]
        if task_id == "intraday":
            if actionable_rows:
                names = "、".join(self._symbol_name(row) for row in actionable_rows[:3])
                return (
                    f"{label} {signal_date}: 未收盘快照，纸面复核 {len(actionable_rows)} 只，"
                    f"先看 {names}"
                )
            if watch_rows:
                names = "、".join(self._symbol_name(row) for row in watch_rows[:3])
                return (
                    f"{label} {signal_date}: 未收盘快照，观察 {len(watch_rows)} 只，"
                    f"先看 {names}"
                )
            if blocked_rows:
                names = "、".join(self._symbol_name(row) for row in blocked_rows[:3])
                return (
                    f"{label} {signal_date}: 未收盘快照，盘中阻塞 "
                    f"{len(blocked_rows)} 只，先核对 {names}"
                )
            return (
                f"{label} {signal_date}: 还没有盘中观察快照，先确认盘中任务是否已运行"
            )
        if actionable_rows:
            names = "、".join(self._symbol_name(row) for row in actionable_rows[:3])
            return (
                f"{label} {signal_date}: 待复核 {len(actionable_rows)} 只，先看 {names}"
            )
        if watch_rows:
            names = "、".join(self._symbol_name(row) for row in watch_rows[:3])
            return f"{label} {signal_date}: 无待复核候选，转入继续观察名单 {names}"
        if blocked_rows:
            names = "、".join(self._symbol_name(row) for row in blocked_rows[:3])
            return f"{label} {signal_date}: 无待复核候选，先核对卡点 {names}"
        return f"{label} {signal_date}: 还没有真实落盘结果，先确认任务是否已运行"

    def _summary_lines_for_signal_task(
        self,
        *,
        task_id: str,
        rows: list[dict[str, Any]],
        actionable_rows: list[dict[str, Any]],
        watch_rows: list[dict[str, Any]],
        blocked_rows: list[dict[str, Any]],
    ) -> tuple[str, ...]:
        action_label, watch_label, _ = self._task_metric_labels(task_id)
        lines = [
            f"任务: {_TASK_LABELS[task_id]} / 候选 {len(rows)} / {action_label} {len(actionable_rows)} / {watch_label} {len(watch_rows)}"
        ]
        if task_id == "intraday":
            lines.append("盘中快照未收盘，只做纸面复核与观察，不进入正式 ledger。")
        if blocked_rows:
            lines.append(f"阻塞 {len(blocked_rows)} 只，优先核对卡点与复核条件。")
        source_status = (
            self._source_status_from_row(max(rows, key=self._row_meta_key))
            if rows
            else {}
        )
        if source_status:
            source_line = self._source_status_summary_line(source_status)
            if source_line:
                lines.append(source_line)
        return tuple(lines)

    def _source_status_summary_line(self, source_status: dict[str, str]) -> str:
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
                return (
                    f"数据链路: 实时源 {actual} 已降级（live_short={fit}）{lag_suffix}"
                )
            return f"数据链路: 实时源 {actual}（live_short={fit}）{lag_suffix}"
        return (
            f"数据链路: 当前实际源 {actual} 只适合历史验证，盘中短线不可用（live_short={fit}）"
            f"{lag_suffix}"
        )

    def _previous_date(
        self,
        *,
        available_dates: tuple[str, ...],
        selected_date: str,
    ) -> str:
        if not selected_date:
            return available_dates[1] if len(available_dates) > 1 else ""
        try:
            current_index = available_dates.index(selected_date)
        except ValueError:
            return ""
        next_index = current_index + 1
        if next_index >= len(available_dates):
            return ""
        return available_dates[next_index]

    def _build_delta_lines(
        self,
        *,
        task_id: str,
        selected_date: str,
        previous_date: str,
    ) -> tuple[str, ...]:
        if not selected_date or not previous_date:
            return ()
        current_view = self._build_task_view_core(
            task_id,
            signal_date=selected_date,
            include_deltas=False,
        )
        previous_view = self._build_task_view_core(
            task_id,
            signal_date=previous_date,
            include_deltas=False,
        )
        return (
            f"较 {previous_date} 候选 {self._signed_delta(current_view.candidate_count - previous_view.candidate_count)}",
            f"较 {previous_date} 待复核 {self._signed_delta(current_view.actionable_count - previous_view.actionable_count)}",
            f"较 {previous_date} 观察 {self._signed_delta(current_view.watch_count - previous_view.watch_count)}",
            f"较 {previous_date} 阻塞 {self._signed_delta(current_view.blocked_count - previous_view.blocked_count)}",
        )

    def _signed_delta(self, value: int) -> str:
        if value >= 0:
            return f"+{value}"
        return str(value)

    def _build_agenda_lines(
        self,
        *,
        recommendation_lines: tuple[str, ...],
        blocker_lines: tuple[str, ...],
        review_lines: tuple[str, ...],
        focus_lines: tuple[str, ...],
    ) -> tuple[str, ...]:
        agenda: list[str] = []
        if recommendation_lines:
            agenda.append(f"先看推荐: {recommendation_lines[0]}")
        if blocker_lines:
            agenda.append(f"先核对卡点: {blocker_lines[0]}")
        if review_lines:
            agenda.append(f"安排复核: {review_lines[0]}")
        if focus_lines:
            agenda.append(f"明日重点: {focus_lines[0]}")
        return tuple(agenda[:4])

    def _source_status_from_row(self, row: dict[str, Any]) -> dict[str, str]:
        lag_days = row.get("run_data_lag_days", "")
        return {
            "requested_source": str(row.get("run_requested_source", "") or ""),
            "actual_source": str(row.get("run_actual_source", "") or ""),
            "health_label": str(row.get("run_source_health_label", "") or ""),
            "health_message": str(row.get("run_source_health_message", "") or ""),
            "data_latest_trade_date": str(
                row.get("run_data_latest_trade_date", "") or ""
            ),
            "lag_days": "" if lag_days in ("", None) else str(lag_days),
            "updated_at": now_shanghai().isoformat(timespec="seconds"),
        }

    def _latest_source_run_event_row(
        self,
        *,
        task_id: str,
        signal_date: str,
    ) -> dict[str, Any] | None:
        if task_id != "main_chain":
            return None
        selected_date = signal_date.strip()
        rows = [
            row
            for row in self.load_signal_rows()
            if self._is_runtime_event_row(row)
            and (
                not selected_date
                or str(row.get("signal_date", "") or "") == selected_date
            )
            and self._source_meta_score(row) > 0
        ]
        if not rows:
            return None
        return max(rows, key=self._row_meta_key)

    def _ledger_market_context_runtime_lines(
        self,
        *,
        task_id: str,
        signal_date: str,
    ) -> tuple[str, ...]:
        selected_date = signal_date.strip()
        if not selected_date:
            return ()
        rows = [
            row
            for row in self._task_signal_rows(task_id)
            if str(row.get("signal_date", "") or "").strip() == selected_date
            and self._as_text_tuple(row.get("run_market_context_lines"))
        ]
        if not rows:
            return ()
        latest_row = max(rows, key=self._row_meta_key)
        return tuple(
            f"市场上下文: {line}"
            for line in self._as_text_tuple(latest_row.get("run_market_context_lines"))[
                :3
            ]
        )

    def _merge_runtime_lines(
        self,
        *line_groups: tuple[str, ...],
    ) -> tuple[str, ...]:
        merged: list[str] = []
        seen: set[str] = set()
        for lines in line_groups:
            for line in lines:
                normalized = normalize_research_tone(str(line).strip())
                if not normalized or normalized in seen:
                    continue
                merged.append(normalized)
                seen.add(normalized)
        return tuple(merged[:8])

    def _source_meta_score(self, row: dict[str, Any]) -> int:
        fields = (
            "run_requested_source",
            "run_actual_source",
            "run_source_health_label",
            "run_source_health_message",
            "run_data_latest_trade_date",
            "run_data_lag_days",
        )
        return sum(
            1
            for field in fields
            if row.get(field, "") not in ("", None) and str(row.get(field, "")).strip()
        )

    def _snapshot_status_label(
        self,
        task_id: str,
        view: DashboardTaskView,
    ) -> str:
        if task_id in {"briefing", "closing_review"}:
            return self._snapshot_status_label_from_report_view(task_id, view)
        return self._snapshot_status_label_from_counts(
            task_id=task_id,
            candidate_count=view.candidate_count,
            actionable_count=view.actionable_count,
            watch_count=view.watch_count,
            blocked_count=view.blocked_count,
        )

    def _snapshot_status_label_from_report_view(
        self,
        task_id: str,
        view: DashboardTaskView,
    ) -> str:
        if task_id == "briefing":
            if view.next_day_focus_lines:
                return "待跟踪"
            if view.report_markdown.strip():
                return "已产出"
            return "未产出"
        if task_id == "closing_review":
            if view.report_markdown.strip():
                return "已复盘"
            if view.actionable_count > 0 or view.blocked_count > 0:
                return "已验证未归档"
            if view.candidate_count > 0:
                return "待复盘"
            return "未产出"
        return self._snapshot_status_label_from_counts(
            task_id=task_id,
            candidate_count=view.candidate_count,
            actionable_count=view.actionable_count,
            watch_count=view.watch_count,
            blocked_count=view.blocked_count,
        )

    def _snapshot_status_label_from_counts(
        self,
        *,
        task_id: str,
        candidate_count: int,
        actionable_count: int,
        watch_count: int,
        blocked_count: int,
    ) -> str:
        if actionable_count > 0:
            return "有推荐"
        if blocked_count > 0:
            return "待核对"
        if watch_count > 0:
            return "观察中"
        if candidate_count > 0:
            return "已产出"
        return "未产出"

    def _extract_report_insights(self, markdown_text: str) -> DashboardReportInsights:
        if not markdown_text.strip():
            return DashboardReportInsights(
                report_summary_lines=(),
                runtime_lines=(),
                market_environment="",
                next_day_focus_lines=(),
            )
        execution_lines = sanitize_research_lines(
            self._section_lines(markdown_text, "执行摘要", "📌 执行摘要")
        )
        runtime_lines = self._runtime_snapshot_lines(
            self._section_lines(markdown_text, "运行参数", "数据与规则")
        )
        market_environment = normalize_research_tone(
            self._market_environment_line(markdown_text)
        )
        next_day_focus_lines = sanitize_research_lines(
            self._focus_lines(
                self._section_lines(markdown_text, "明日重点", "明日先看")
            )
        )
        return DashboardReportInsights(
            report_summary_lines=tuple(
                normalize_research_tone(line)
                .replace("主链候选", "今日重点名单")
                .replace("高分主链候选", "高分今日重点名单")
                for line in execution_lines
            ),
            runtime_lines=runtime_lines,
            market_environment=market_environment,
            next_day_focus_lines=tuple(
                normalize_research_tone(line) for line in next_day_focus_lines
            ),
        )

    def _section_lines(self, markdown_text: str, *headings: str) -> tuple[str, ...]:
        target_headings = {
            self._normalize_heading(heading) for heading in headings if heading.strip()
        }
        inside_section = False
        collected: list[str] = []
        for raw_line in markdown_text.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("## "):
                normalized = self._normalize_heading(stripped[3:])
                if inside_section:
                    break
                inside_section = normalized in target_headings
                continue
            if not inside_section or not stripped or stripped == "---":
                continue
            if stripped.startswith(">"):
                continue
            collected.append(self._strip_list_marker(stripped))
        return tuple(line for line in collected if line)

    def _normalize_heading(self, heading: str) -> str:
        return heading.replace("📌", "").strip()

    def _strip_list_marker(self, line: str) -> str:
        for prefix in ("- ", "* "):
            if line.startswith(prefix):
                return line[len(prefix) :].strip()
        return line.strip()

    def _runtime_snapshot_lines(self, lines: tuple[str, ...]) -> tuple[str, ...]:
        prefixes = (
            "数据源:",
            "数据来源:",
            "数据层级:",
            "数据完整度:",
            "数据时效:",
            "数据健康:",
            "数据状态:",
            "候选池:",
            "扫描范围:",
            "thresholds.version:",
            "规则版本:",
            "regime:",
            "市场标签:",
        )
        selected = [
            line
            for line in lines
            if any(line.startswith(prefix) for prefix in prefixes)
        ]
        return tuple(humanize_runtime_snapshot_line(line) for line in selected[:6])

    def _market_environment_line(self, markdown_text: str) -> str:
        lines = self._section_lines(markdown_text, "市场态势", "市场环境")
        if not lines:
            return ""
        first_line = lines[0]
        if "当前市场态势:" in first_line:
            first_line = first_line.split("当前市场态势:", 1)[1].strip()
        return first_line.replace("**", "").strip()

    def _focus_lines(self, lines: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(line for line in lines[:5] if line)

    def _report_document_for_signal_task(
        self,
        task_id: str,
        signal_date: str,
    ) -> DashboardReportDocument:
        if task_id == "main_chain":
            latest_signal_date = self._max_signal_date(self._task_signal_rows(task_id))
            if signal_date and signal_date != latest_signal_date:
                return self._read_briefing_document(signal_date)
            path = self.reports_dir / "latest.md"
            document = self._read_report_document(path, expected_date=signal_date)
            if document.markdown:
                return document
        return self._empty_report_document()

    def _report_markdown_for_signal_task(self, task_id: str, signal_date: str) -> str:
        return self._report_document_for_signal_task(task_id, signal_date).markdown

    def _read_briefing_document(self, signal_date: str) -> DashboardReportDocument:
        if not signal_date:
            return self._empty_report_document()
        path = self.reports_dir / f"briefing-{signal_date}.md"
        return self._read_report_document(path, expected_date=signal_date)

    def _read_briefing_markdown(self, signal_date: str) -> str:
        return self._read_briefing_document(signal_date).markdown

    def _report_matches_signal_date(self, markdown_text: str, signal_date: str) -> bool:
        selected_date = signal_date.strip()
        if not markdown_text.strip() or not selected_date:
            return False
        matched = re.search(r"数据日期\s*(\d{4}-\d{2}-\d{2})", markdown_text)
        if matched is None:
            return False
        return matched.group(1) == selected_date

    def _report_body_matches_signal_date(
        self,
        markdown_text: str,
        signal_date: str,
    ) -> bool:
        selected_date = signal_date.strip()
        if not markdown_text.strip() or not selected_date:
            return False
        if self._report_matches_signal_date(markdown_text, selected_date):
            return True
        header_dates = re.findall(r"\d{4}-\d{2}-\d{2}", markdown_text[:1200])
        return bool(header_dates and header_dates[0] == selected_date)
