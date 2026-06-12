"""仪表盘数据工具 - 基于真实落盘数据构建任务导航视图。"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from aqsp.audit.trade_logger import TradeLogger
from aqsp.briefing.closing_review import ClosingReviewer
from aqsp.core.time import SHANGHAI_TZ, now_shanghai, today_shanghai, to_iso8601
from aqsp.ledger.base import read_ledger
from aqsp.paper import read_paper_trades
from aqsp.presentation import (
    format_review_meta,
    format_symbol_name,
    humanize_runtime_snapshot_line,
    normalize_research_tone,
)
from aqsp.ratings import is_tradable_rating, portfolio_action_label, rating_label
from aqsp.web.archive_safety import sanitize_research_lines

logger = logging.getLogger(__name__)

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
    "intraday": (2, "盘中观察", "未收盘快照，只作观察，不进入正式待复核"),
    "morning_breakout": (3, "早盘观察", "开盘后核对强势突破是否成立"),
    "closing_premium": (4, "尾盘确认", "收盘前评估溢价承接与隔夜价值"),
    "closing_review": (5, "收盘复盘", "核对执行结果与失效样本"),
    "briefing": (6, "次日预案", "整理明日重点与待跟踪事项"),
}
_TASK_METRIC_LABELS: dict[str, tuple[str, str, str]] = {
    "intraday": ("正式待复核", "盘中观察", "盘中阻塞"),
    "closing_review": ("已验证", "待复盘", "复盘阻塞"),
    "briefing": ("已落盘", "待跟踪", "待补档"),
}
_DEBATE_ROLE_ORDER = (
    "bull",
    "bear",
    "risk_control",
    "sector_leader",
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
        resolved_reports = reports_dir.strip() or "reports"
        resolved_debate_results = (
            debate_results_path.strip()
            or os.getenv("AQSP_DEBATE_RESULTS", "").strip()
            or "data/debate_results.jsonl"
        )

        self.ledger_path = Path(resolved_ledger)
        self.paper_ledger_path = Path(resolved_paper_ledger)
        self.logs_path = Path(resolved_logs)
        self.reports_dir = Path(resolved_reports)
        self.debate_results_path = Path(resolved_debate_results)
        self.logger = TradeLogger(str(self.logs_path))

    def load_signal_rows(self) -> list[dict[str, Any]]:
        try:
            rows = read_ledger(self.ledger_path)
        except Exception as exc:
            logger.error("加载 signal ledger 失败: %s", exc)
            return []
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                normalized.append(dict(row))
        return normalized

    def load_paper_rows(self) -> list[dict[str, Any]]:
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

    def debate_summary(
        self,
        *,
        signal_date: str,
        symbol: str,
    ) -> DashboardDebateSummary | None:
        """按 signal_date + symbol 返回结构化辩论摘要；缺失时返回 None。"""
        selected_date = signal_date.strip()
        selected_symbol = symbol.strip()
        if not selected_date or not selected_symbol:
            return None

        matches = [
            row
            for row in self._dedupe_debate_rows(self._load_debate_rows())
            if self._debate_signal_date(row) == selected_date
            and str(row.get("symbol", "") or "").strip() == selected_symbol
            and self._has_debate_evidence(row)
        ]
        if not matches:
            return None
        return self._build_debate_summary(max(matches, key=self._debate_row_key))

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
        return matched_rows[0][2]

    def _load_debate_rows(self) -> list[dict[str, Any]]:
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

    def task_snapshots(
        self, signal_date: str = ""
    ) -> tuple[DashboardTaskSnapshot, ...]:
        selected_date = signal_date.strip()
        snapshots: list[DashboardTaskSnapshot] = []
        for task_id, label in _TASK_LABELS.items():
            available_dates = self.task_dates(task_id)
            latest_date = available_dates[0] if available_dates else ""
            if selected_date:
                if selected_date in available_dates:
                    view = self.build_task_view(task_id, signal_date=selected_date)
                    status_label = self._snapshot_status_label(task_id, view)
                    snapshots.append(
                        DashboardTaskSnapshot(
                            task_id=task_id,
                            task_label=label,
                            latest_date=selected_date,
                            status_label=status_label,
                            headline=view.headline,
                            actionable_count=view.actionable_count,
                            watch_count=view.watch_count,
                            blocked_count=view.blocked_count,
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
                view = self.build_task_view(task_id, signal_date=latest_date)
                status_label = self._snapshot_status_label(task_id, view)
                snapshots.append(
                    DashboardTaskSnapshot(
                        task_id=task_id,
                        task_label=label,
                        latest_date=latest_date,
                        status_label=status_label,
                        headline=view.headline,
                        actionable_count=view.actionable_count,
                        watch_count=view.watch_count,
                        blocked_count=view.blocked_count,
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

    def dashboard_dates(self) -> tuple[str, ...]:
        return self._all_dashboard_dates()

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
        all_dates = self._all_dashboard_dates()
        rows: list[DashboardTimelineRow] = []
        for signal_date in all_dates[:limit]:
            same_day_rows = self.same_day_task_rows(signal_date)
            if not same_day_rows:
                continue
            task_labels = tuple(row.task_label for row in same_day_rows)
            headline = "；".join(
                f"{row.task_label}: {row.status_label}" for row in same_day_rows[:3]
            )
            actionable_total, watch_total, blocked_total = self._same_day_unique_counts(
                signal_date
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
        return tuple(rows)

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
    ) -> tuple[DashboardSameDayTaskRow, ...]:
        selected_date = signal_date.strip()
        if not selected_date:
            return ()
        rows: list[DashboardSameDayTaskRow] = []
        for task_id, task_label in _TASK_LABELS.items():
            if selected_date not in self.task_dates(task_id):
                continue
            view = self._build_task_view_core(
                task_id,
                signal_date=selected_date,
                include_deltas=False,
            )
            rows.append(
                DashboardSameDayTaskRow(
                    signal_date=selected_date,
                    task_id=task_id,
                    task_label=task_label,
                    phase_order=self._task_phase_order(task_id),
                    phase_label=self._task_phase_label(task_id),
                    phase_summary=self._task_phase_summary(task_id, view),
                    status_label=self._snapshot_status_label(task_id, view),
                    headline=view.headline,
                    candidate_count=view.candidate_count,
                    actionable_count=view.actionable_count,
                    watch_count=view.watch_count,
                    blocked_count=view.blocked_count,
                )
            )
        rows.sort(key=lambda row: (row.phase_order, row.task_label))
        return tuple(rows)

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

        grouped: dict[str, dict[str, Any]] = {}
        for task_id in ("main_chain", "morning_breakout", "closing_premium"):
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

        spotlights = [
            self._build_same_day_spotlight(symbol, payload)
            for symbol, payload in grouped.items()
        ]
        spotlights.sort(
            key=lambda item: (
                self._spotlight_priority_rank(item),
                -item.score,
                -len(item.task_labels),
                item.display_name,
            )
        )
        return tuple(spotlights[:limit])

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

    def date_overview(self, signal_date: str) -> DashboardDateOverview:
        rows = self.same_day_task_rows(signal_date)
        if not rows:
            return DashboardDateOverview(
                signal_date=signal_date.strip(),
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

        ordered_rows = sorted(
            rows,
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
        blocker_row = next((row for row in rows if row.blocked_count > 0), None)
        signal_rows = tuple(row for row in rows if row.task_id in _SIGNAL_TASK_IDS)
        focus_candidates = signal_rows or rows
        focus_row = next(
            (row for row in focus_candidates if row.actionable_count > 0),
            next(
                (row for row in focus_candidates if row.blocked_count > 0),
                next((row for row in focus_candidates if row.watch_count > 0), top_row),
            ),
        )
        actionable_total, watch_total, blocked_total = self._same_day_unique_counts(
            signal_date.strip()
        )
        return DashboardDateOverview(
            signal_date=signal_date.strip(),
            task_count=len(rows),
            actionable_total=actionable_total,
            watch_total=watch_total,
            blocked_total=blocked_total,
            top_task_label=top_row.task_label,
            top_headline=top_row.headline,
            blocker_headline=blocker_row.headline if blocker_row is not None else "",
            focus_headline=focus_row.headline,
            workflow_summary=self._workflow_summary(rows),
            archive_summary=self._archive_summary(rows, focus_row, blocker_row),
        )

    def preferred_task_for_date(self, signal_date: str) -> str:
        same_day_rows = self.same_day_task_rows(signal_date)
        if not same_day_rows:
            return self.default_task_id()
        for preferred_task_id in (
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
                research_lines.append(f"现在卡在哪: {blocker}")
        else:
            research_lines.append("该标的当前不在研究候选中，主要从纸面记录回看。")

        readiness_lines: list[str] = []
        if pending_rows:
            readiness_lines.append(
                f"纸面入场假设 {len(pending_rows)} 笔，开盘先核对下一交易日开盘价是否可成交。"
            )
            if blocker:
                readiness_lines.append(f"现在卡在哪: {blocker}")
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
            research_status = "缺少研究结论"
        elif blocker:
            research_status = "研究侧存在阻塞"
        elif next_step:
            research_status = "研究侧待确认"
        else:
            research_status = "研究结论已落盘"

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
        if task_id == "briefing":
            return self._briefing_dates()
        if task_id == "closing_review":
            return self._closing_review_dates()
        return self._signal_dates(self._task_signal_rows(task_id))

    def _closing_review_dates(self) -> tuple[str, ...]:
        dates = set(self._signal_dates(self.load_signal_rows()))
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

    def _build_task_view_core(
        self,
        task_id: str,
        *,
        signal_date: str = "",
        include_deltas: bool,
    ) -> DashboardTaskView:
        normalized_task = task_id if task_id in _TASK_LABELS else self.default_task_id()
        available_dates = self.task_dates(normalized_task)
        selected_date = signal_date.strip() or (
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
        start_date = today_shanghai() - timedelta(days=days)
        try:
            rows = self.logger.query_logs(
                start_date=start_date,
                end_date=today_shanghai(),
            )
        except Exception as exc:
            logger.error("加载执行日志失败: %s", exc)
            return []
        return [dict(row) for row in rows if row.get("type") == "execution"]

    def execution_logs_for_date(self, signal_date: str) -> list[dict[str, Any]]:
        selected_date = signal_date.strip()
        if not selected_date:
            return []
        try:
            target_date = date.fromisoformat(selected_date)
        except ValueError:
            logger.warning("执行日志日期格式无效: %s", signal_date)
            return []
        try:
            rows = self.logger.query_logs(
                start_date=target_date,
                end_date=target_date,
            )
        except Exception as exc:
            logger.error("加载 %s 执行日志失败: %s", selected_date, exc)
            return []
        return [
            dict(row)
            for row in rows
            if row.get("type") == "execution"
            and str(row.get("timestamp", "") or "").startswith(selected_date)
        ]

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
            return {}
        latest_row = max(
            rows,
            key=lambda row: (
                self._source_meta_score(row),
                *self._row_meta_key(row),
            ),
        )
        if self._source_meta_score(latest_row) == 0:
            fallback_date = signal_date.strip() or str(
                latest_row.get("signal_date", "") or ""
            )
            return {
                "requested_source": "未记录",
                "actual_source": "未记录",
                "health_label": "历史记录缺字段",
                "health_message": (
                    "该日历史记录未写入数据源元信息，无法还原当时的数据源健康度。"
                ),
                "data_latest_trade_date": fallback_date or "未记录",
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
        rows = self._task_signal_rows(task_id)
        if selected_date:
            rows = [
                row
                for row in rows
                if str(row.get("signal_date", "") or "") == selected_date
            ]
        deduped = self._dedupe_rows(rows)
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
            runtime_lines=report_insights.runtime_lines,
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
            runtime_lines=report_insights.runtime_lines,
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
            if line.startswith(("当前卡点:", "纸面阻塞:", "执行阻塞:", "现在卡在哪:"))
        )
        review_lines = tuple(
            normalize_research_tone(line)
            for line in summary_lines
            if line.startswith(("观察复核:", "观察名单接下来:"))
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
        rows = self.load_signal_rows()
        if task_id == "intraday":
            return [row for row in rows if self._row_task_id(row) == "intraday"]
        if task_id == "morning_breakout":
            return [row for row in rows if self._row_task_id(row) == "morning_breakout"]
        if task_id == "closing_premium":
            return [row for row in rows if self._row_task_id(row) == "closing_premium"]
        if task_id == "main_chain":
            return [row for row in rows if self._row_task_id(row) == "main_chain"]
        return rows

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
        dates: set[str] = set()
        for task_id in _TASK_LABELS:
            dates.update(self.task_dates(task_id))
        return tuple(sorted((date for date in dates if date), reverse=True))

    def _dedupe_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            key = (
                str(row.get("signal_date", "") or ""),
                str(row.get("symbol", "") or ""),
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

    def _row_meta_key(self, row: dict[str, Any]) -> tuple[str, str, float]:
        return (
            str(row.get("signal_date", "") or ""),
            str(row.get("created_at", "") or ""),
            float(row.get("score") or 0.0),
        )

    def _sort_key(self, row: dict[str, Any]) -> tuple[float, str]:
        return (float(row.get("score") or 0.0), str(row.get("created_at", "") or ""))

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
        note = self._task_phase_note(task_id)
        action_label, watch_label, blocked_label = self._task_metric_labels(task_id)
        if view.actionable_count > 0:
            return f"{note}；当前{action_label} {view.actionable_count} 只。"
        if view.blocked_count > 0:
            return f"{note}；当前{blocked_label} {view.blocked_count} 只。"
        if view.watch_count > 0:
            return f"{note}；当前{watch_label} {view.watch_count} 只。"
        if view.candidate_count > 0:
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
            next_step=str(merged_row.get("candidate_next_step", "") or "").strip(),
            review_meta=self._review_meta(merged_row),
            task_labels=tuple(payload["task_labels"]),
            reasons=self._as_text_tuple(merged_row.get("reasons")),
            risks=self._as_text_tuple(merged_row.get("risks")),
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
                if row is final_row:
                    return value
                task_id = str(entry.get("task_id", "") or "")
                return self._label_backfilled_evidence(value, task_id)
        return None

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
        grouped: dict[str, dict[str, Any]] = {}
        for task_id in _SIGNAL_TASK_IDS:
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

    def _same_day_unique_counts(self, signal_date: str) -> tuple[int, int, int]:
        unique_rows = self._same_day_unique_rows(signal_date)
        actionable_total = sum(1 for row in unique_rows if self._is_actionable(row))
        watch_total = sum(1 for row in unique_rows if self._is_watch_only(row))
        blocked_total = sum(1 for row in unique_rows if self._is_blocked(row))
        return actionable_total, watch_total, blocked_total

    def _spotlight_priority_rank(self, item: DashboardCandidateSpotlight) -> int:
        if item.blocker:
            return 3
        if item.action_label == "上调优先级":
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

    def _action_label(self, row: dict[str, Any]) -> str:
        action = str(row.get("portfolio_action", "") or "").strip()
        if action:
            return normalize_research_tone(portfolio_action_label(action))
        rating = str(row.get("rating", "") or "").strip()
        return normalize_research_tone(rating_label(rating))

    def _action_status_text(self, row: dict[str, Any]) -> str:
        action = self._action_label(row).strip()
        status = self._candidate_status(row).strip()
        if action and status:
            if action == status:
                return action
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
        if self._is_intraday_row(row, task_id=task_id):
            return False
        if self._is_blocked(row):
            return False
        action = str(row.get("portfolio_action", "") or "").strip()
        if action == "promote":
            return True
        if action == "downgrade":
            return False
        return is_tradable_rating(row.get("rating"))

    def _is_watch_candidate(self, row: dict[str, Any], task_id: str = "") -> bool:
        if self._is_intraday_row(row, task_id=task_id):
            return True
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

    def _dedupe_debate_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            key = (
                self._debate_signal_date(row),
                str(row.get("symbol", "") or "").strip(),
            )
            if not all(key):
                continue
            existing = grouped.get(key)
            if existing is None or self._debate_quality_key(
                row
            ) > self._debate_quality_key(existing):
                grouped[key] = row
        return list(grouped.values())

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
        return self._debate_evidence_score(row) > 0

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

    def _debate_quality_key(self, row: dict[str, Any]) -> tuple[int, str, str, float]:
        return (
            self._debate_evidence_score(row),
            self._debate_signal_date(row),
            str(row.get("created_at", "") or "").strip(),
            float(row.get("adjusted_score") or row.get("original_score") or 0.0),
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
        vote_map = self._debate_vote_map(row)
        bull_count = sum(1 for stance in vote_map.values() if stance == "bullish")
        bear_count = sum(1 for stance in vote_map.values() if stance == "bearish")
        neutral_count = sum(1 for stance in vote_map.values() if stance == "neutral")
        round_summaries = self._debate_round_summaries(row)
        risk_warnings = self._as_text_tuple(row.get("risk_warnings"))
        opportunity_highlights = self._as_text_tuple(row.get("opportunity_highlights"))
        agent_views = self._debate_agent_views(row, vote_map=vote_map)
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
            if vote_stance and opinion_stance and vote_stance != opinion_stance:
                stance_label = f"{stance_label}（发言冲突）"
                key_argument = (
                    "最终投票与发言不一致: "
                    f"投票{self._debate_stance_label(vote_stance)}，"
                    f"发言{self._debate_stance_label(opinion_stance)}，需人工复核"
                )
            agent_views.append(
                DashboardDebateAgentView(
                    role_id=role_id,
                    role_label=self._debate_role_label(role_id),
                    stance=stance,
                    stance_label=stance_label,
                    confidence=float(opinion.get("confidence") or 0.0),
                    key_argument=key_argument,
                    key_risk=risks[0] if risks else "",
                    key_opportunity=opportunities[0] if opportunities else "",
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
            lines.append(
                normalize_research_tone(
                    f"{self._debate_adjustment_label(adjustment)}: "
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
        if bull_count + bear_count + neutral_count:
            lines.append(
                f"投票分布: 看多 {bull_count} / 看空 {bear_count} / 中性 {neutral_count}"
            )
        consensus = str(row.get("final_consensus", "") or "").strip()
        adjustment_reason = str(row.get("adjustment_reason", "") or "").strip()
        if consensus:
            lines.append(f"辩论共识: {consensus}")
        if adjustment_reason and adjustment_reason != consensus:
            lines.append(f"复核依据: {adjustment_reason}")
        if risk_warnings:
            lines.append(f"核心风险: {risk_warnings[0]}")
        if opportunity_highlights:
            lines.append(f"机会线索: {opportunity_highlights[0]}")
        return tuple(lines[:5])

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

    def _build_detail_cards(
        self,
        rows: list[dict[str, Any]],
        *,
        limit: int | None = 6,
        task_id: str = "",
    ) -> tuple[DashboardCandidateCard, ...]:
        ordered = sorted(
            rows,
            key=lambda row: (
                -self._priority_bucket(row),
                float(row.get("score") or 0.0),
                str(row.get("created_at", "") or ""),
            ),
            reverse=True,
        )
        if limit is not None:
            ordered = ordered[:limit]
        cards: list[DashboardCandidateCard] = []
        for index, row in enumerate(ordered, start=1):
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
                    next_step=str(row.get("candidate_next_step", "") or "").strip(),
                    blocker=self._candidate_blocker_text(row),
                    review_meta=self._review_meta(row),
                    reasons=self._as_text_tuple(row.get("reasons")),
                    risks=self._as_text_tuple(row.get("risks")),
                    strategies=strategies_tuple,
                    data_source=str(row.get("run_actual_source", "") or ""),
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
        next_step = str(row.get("candidate_next_step", "") or "").strip()
        action = str(row.get("portfolio_action", "") or "").strip()
        if blocker:
            return blocker
        if action == "promote":
            return normalize_research_tone("PM 已上调优先级，进入优先跟踪序列")
        if action == "keep":
            return normalize_research_tone("维持顺位，等待更强确认")
        if next_step:
            return normalize_research_tone(next_step)
        return normalize_research_tone("按当前顺位继续跟踪")

    def _build_ranking_lines(
        self,
        rows: list[dict[str, Any]],
        *,
        limit: int = 3,
    ) -> tuple[str, ...]:
        ordered = sorted(
            rows,
            key=lambda row: (
                -self._priority_bucket(row),
                float(row.get("score") or 0.0),
                str(row.get("created_at", "") or ""),
            ),
            reverse=True,
        )[:limit]
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
        ordered = sorted(
            rows,
            key=lambda row: (
                -self._priority_bucket(row),
                float(row.get("score") or 0.0),
                str(row.get("created_at", "") or ""),
            ),
            reverse=True,
        )[:limit]
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
            next_step = str(row.get("candidate_next_step", "") or "").strip()
            blocker = self._candidate_blocker_text(row) or "等待条件解除"
            line = f"{self._symbol_name(row)} | 现在卡在哪: {blocker}"
            if next_step:
                line += f" | 再看动作: {next_step}"
            lines.append(line)
        remaining_slots = max(limit - len(lines), 0)
        if remaining_slots <= 0:
            return tuple(lines[:limit])
        for row in watch_rows[:remaining_slots]:
            if row in blocked_rows:
                continue
            next_step = str(row.get("candidate_next_step", "") or "").strip()
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
        next_step = str(row.get("candidate_next_step", "") or "").strip()
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
        next_step = str(row.get("candidate_next_step", "") or "").strip()
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
            lines.append("盘中快照未收盘，只作观察，不进入正式主链待复核。")
        if blocked_rows:
            lines.append(f"阻塞 {len(blocked_rows)} 只，优先核对卡点与复核条件。")
        source_status = (
            self._source_status_from_row(max(rows, key=self._row_meta_key))
            if rows
            else {}
        )
        if source_status:
            lines.append(
                f"数据源: {source_status.get('requested_source', '-')}"
                f" -> {source_status.get('actual_source', '-')}"
                f" / {source_status.get('health_label', '-')}"
            )
        return tuple(lines)

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
        return {
            "requested_source": str(row.get("run_requested_source", "") or ""),
            "actual_source": str(row.get("run_actual_source", "") or ""),
            "health_label": str(row.get("run_source_health_label", "") or ""),
            "health_message": str(row.get("run_source_health_message", "") or ""),
            "data_latest_trade_date": str(
                row.get("run_data_latest_trade_date", "") or ""
            ),
            "lag_days": str(row.get("run_data_lag_days", "") or ""),
            "updated_at": now_shanghai().isoformat(timespec="seconds"),
        }

    def _source_meta_score(self, row: dict[str, Any]) -> int:
        fields = (
            "run_requested_source",
            "run_actual_source",
            "run_source_health_label",
            "run_source_health_message",
            "run_data_latest_trade_date",
            "run_data_lag_days",
        )
        return sum(1 for field in fields if str(row.get(field, "") or "").strip())

    def _snapshot_status_label(
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
        if view.actionable_count > 0:
            return "有推荐"
        if view.blocked_count > 0:
            return "待核对"
        if view.watch_count > 0:
            return "观察中"
        if view.candidate_count > 0:
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
                normalize_research_tone(line) for line in execution_lines
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
