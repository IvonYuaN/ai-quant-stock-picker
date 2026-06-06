"""仪表盘数据工具 - 基于真实落盘数据构建任务导航视图。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from aqsp.audit.trade_logger import TradeLogger
from aqsp.briefing.closing_review import ClosingReviewer, format_daily_review
from aqsp.core.time import now_shanghai, today_shanghai
from aqsp.ledger.base import read_ledger
from aqsp.paper import read_paper_trades
from aqsp.presentation import format_review_meta, format_symbol_name
from aqsp.ratings import is_tradable_rating, portfolio_action_label, rating_label

logger = logging.getLogger(__name__)

_TASK_LABELS = {
    "main_chain": "主链推荐",
    "morning_breakout": "早盘策略",
    "closing_premium": "尾盘策略",
    "closing_review": "收盘复盘",
    "briefing": "简报回看",
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
class DashboardPaperSummary:
    open_positions: int
    pending_entries: int
    not_executable: int
    closed_trades: int
    open_position_lines: tuple[str, ...]
    event_lines: tuple[str, ...]
    action_summary_lines: tuple[str, ...]


@dataclass(frozen=True)
class DashboardTaskView:
    task_id: str
    task_label: str
    selected_date: str
    latest_date: str
    available_dates: tuple[str, ...]
    headline: str
    summary_lines: tuple[str, ...]
    report_summary_lines: tuple[str, ...]
    runtime_lines: tuple[str, ...]
    agenda_lines: tuple[str, ...]
    recommendation_lines: tuple[str, ...]
    watchlist_lines: tuple[str, ...]
    blocker_lines: tuple[str, ...]
    review_lines: tuple[str, ...]
    next_day_focus_lines: tuple[str, ...]
    report_markdown: str
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


class DashboardDataProvider:
    """仪表盘数据提供器，只读真实账本、报告和执行日志。"""

    def __init__(
        self,
        ledger_path: str = "",
        paper_ledger_path: str = "",
        logs_path: str = "",
        reports_dir: str = "",
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

        self.ledger_path = Path(resolved_ledger)
        self.paper_ledger_path = Path(resolved_paper_ledger)
        self.logs_path = Path(resolved_logs)
        self.reports_dir = Path(resolved_reports)
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

    def task_snapshots(self) -> tuple[DashboardTaskSnapshot, ...]:
        snapshots: list[DashboardTaskSnapshot] = []
        for task_id, label in _TASK_LABELS.items():
            available_dates = self.task_dates(task_id)
            latest_date = available_dates[0] if available_dates else ""
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
                    status_label="暂无结果",
                    headline=f"{label}: 暂无真实落盘结果",
                    actionable_count=0,
                    watch_count=0,
                    blocked_count=0,
                )
            )
        return tuple(snapshots)

    def paper_summary(self) -> DashboardPaperSummary:
        rows = self.load_paper_rows()
        open_rows = [row for row in rows if row.get("status") == "open"]
        pending_rows = [row for row in rows if row.get("status") == "pending_entry"]
        blocked_rows = [row for row in rows if row.get("status") == "not_executable"]
        closed_rows = [row for row in rows if row.get("status") == "closed"]
        execution_rows = self.get_recent_execution_logs(days=7)

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
            event_lines.append(f"待开仓 {len(pending_rows)} 笔，等待 next open 验证。")
        if blocked_rows:
            event_lines.append(
                f"不可成交 {len(blocked_rows)} 笔，最新阻塞: "
                f"{self._symbol_name(blocked_rows[-1])} | "
                f"{blocked_rows[-1].get('not_executable_reason', '未知原因')}"
            )
        if closed_rows:
            latest_closed = closed_rows[-1]
            event_lines.append(
                f"最近平仓: {self._symbol_name(latest_closed)} | "
                f"收益 {latest_closed.get('return_pct', '-')}"
            )

        action_summary_lines: list[str] = []
        if execution_rows:
            latest_execution = execution_rows[-1]
            action_summary_lines.append(
                f"最近执行: {self._display_name_for_symbol(latest_execution)} | "
                f"{latest_execution.get('action', '-')} "
                f"{latest_execution.get('shares', '-')}"
                f" @ {latest_execution.get('price', '-')}"
            )
        if pending_rows:
            action_summary_lines.append(
                f"待执行队列 {len(pending_rows)} 笔，开盘优先检查 next open 是否可成交。"
            )
        if blocked_rows:
            action_summary_lines.append(
                f"阻塞队列 {len(blocked_rows)} 笔，先处理涨跌停/停牌导致的不可成交样本。"
            )
        if not action_summary_lines and open_rows:
            action_summary_lines.append(
                f"当前以持仓跟踪为主，共 {len(open_rows)} 笔 open position。"
            )

        return DashboardPaperSummary(
            open_positions=len(open_rows),
            pending_entries=len(pending_rows),
            not_executable=len(blocked_rows),
            closed_trades=len(closed_rows),
            open_position_lines=open_position_lines,
            event_lines=tuple(event_lines),
            action_summary_lines=tuple(action_summary_lines),
        )

    def default_task_id(self) -> str:
        return "main_chain"

    def task_dates(self, task_id: str) -> tuple[str, ...]:
        if task_id == "briefing":
            return self._briefing_dates()
        if task_id == "closing_review":
            return self._signal_dates(self.load_signal_rows())
        return self._signal_dates(self._task_signal_rows(task_id))

    def build_task_view(self, task_id: str, signal_date: str = "") -> DashboardTaskView:
        normalized_task = task_id if task_id in _TASK_LABELS else self.default_task_id()
        available_dates = self.task_dates(normalized_task)
        selected_date = signal_date.strip() or (available_dates[0] if available_dates else "")

        if normalized_task == "closing_review":
            return self._build_closing_review_view(
                selected_date=selected_date,
                available_dates=available_dates,
            )
        if normalized_task == "briefing":
            return self._build_briefing_view(
                selected_date=selected_date,
                available_dates=available_dates,
            )
        return self._build_signal_task_view(
            task_id=normalized_task,
            selected_date=selected_date,
            available_dates=available_dates,
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
                "主链动作": self._action_label(row),
                "候选状态": self._candidate_status(row),
                "阻塞原因": str(row.get("candidate_blocker", "") or ""),
                "下一步": str(row.get("candidate_next_step", "") or ""),
                "数据源": row.get("run_actual_source", ""),
                "健康度": row.get("run_source_health_label", ""),
            }
            for row in rows[:limit]
        ]
        return pd.DataFrame(table)

    def open_positions_frame(self) -> pd.DataFrame:
        rows = [row for row in self.load_paper_rows() if row.get("status") == "open"]
        if not rows:
            return pd.DataFrame()
        table = [
            {
                "代码": row.get("symbol", ""),
                "名称": self._symbol_name(row),
                "入场日": row.get("entry_date", ""),
                "入场价": row.get("entry_price", ""),
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
                "入场日": row.get("entry_date", ""),
                "退出日": row.get("exit_date", ""),
                "退出原因": row.get("exit_reason", row.get("not_executable_reason", "")),
                "收益%": row.get("return_pct", ""),
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

    def recent_execution_frame(self, limit: int = 20) -> pd.DataFrame:
        rows = self.get_recent_execution_logs(days=7)
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
        latest_row = max(rows, key=self._row_meta_key)
        return self._source_status_from_row(latest_row)

    def _build_signal_task_view(
        self,
        *,
        task_id: str,
        selected_date: str,
        available_dates: tuple[str, ...],
    ) -> DashboardTaskView:
        report_markdown = self._report_markdown_for_signal_task(task_id, selected_date)
        report_insights = self._extract_report_insights(report_markdown)
        rows = self._task_signal_rows(task_id)
        if selected_date:
            rows = [
                row
                for row in rows
                if str(row.get("signal_date", "") or "") == selected_date
            ]
        deduped = self._dedupe_rows(rows)
        actionable_rows = [row for row in deduped if self._is_actionable(row)]
        blocked_rows = [row for row in deduped if self._is_blocked(row)]
        watch_rows = [
            row
            for row in deduped
            if row not in actionable_rows and (self._is_watch_candidate(row) or row in blocked_rows)
        ]

        headline = self._headline_for_signal_task(
            task_id=task_id,
            signal_date=selected_date,
            actionable_rows=actionable_rows,
            watch_rows=watch_rows,
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
        review_lines = tuple(self._review_line(row) for row in deduped[:5] if self._review_line(row))
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
            available_dates=available_dates,
            headline=headline,
            summary_lines=summary_lines,
            report_summary_lines=report_insights.report_summary_lines,
            runtime_lines=report_insights.runtime_lines,
            agenda_lines=agenda_lines,
            recommendation_lines=recommendation_lines,
            watchlist_lines=watchlist_lines,
            blocker_lines=blocker_lines,
            review_lines=review_lines,
            next_day_focus_lines=report_insights.next_day_focus_lines,
            report_markdown=report_markdown,
            source_status=self.latest_source_status(task_id=task_id, signal_date=selected_date),
            candidate_count=len(deduped),
            actionable_count=len(actionable_rows),
            watch_count=len(watch_rows),
            blocked_count=len(blocked_rows),
            detail_cards=self._build_detail_cards(deduped),
            ranking_lines=self._build_ranking_lines(deduped),
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
    ) -> DashboardTaskView:
        report_markdown = self._read_briefing_markdown(selected_date)
        report_insights = self._extract_report_insights(report_markdown)
        base_view = self._build_signal_task_view(
            task_id="main_chain",
            selected_date=selected_date,
            available_dates=self.task_dates("main_chain"),
        )
        return DashboardTaskView(
            task_id="briefing",
            task_label=_TASK_LABELS["briefing"],
            selected_date=selected_date,
            latest_date=available_dates[0] if available_dates else "",
            available_dates=available_dates,
            headline=f"{_TASK_LABELS['briefing']} {selected_date or ''}".strip(),
            summary_lines=base_view.summary_lines,
            report_summary_lines=report_insights.report_summary_lines,
            runtime_lines=report_insights.runtime_lines,
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
    ) -> DashboardTaskView:
        review = ClosingReviewer(ledger_path=str(self.ledger_path)).review_today(
            selected_date or None
        )
        summary_lines = tuple(review.main_chain_summary)
        recommendation_lines = tuple(
            line for line in summary_lines if line.startswith("可执行主链:")
        )
        watchlist_lines = tuple(
            line for line in summary_lines if line.startswith("候选观察池:")
        )
        blocker_lines = tuple(
            line for line in summary_lines if line.startswith("执行阻塞:")
        )
        review_lines = tuple(
            line for line in summary_lines if line.startswith("观察复核:")
        )
        strategy_breakdown_lines = self._format_strategy_breakdown_lines(
            review.strategy_breakdown
        )
        lesson_lines = tuple(review.key_lessons)
        improvement_lines = tuple(review.improvement_suggestions)
        headline = (
            f"{_TASK_LABELS['closing_review']} {selected_date}: "
            f"{review.executed_signals} 笔已验证，胜率 {review.win_rate:.0%}，"
            f"总收益 {review.total_return:.2f}%"
        )
        return DashboardTaskView(
            task_id="closing_review",
            task_label=_TASK_LABELS["closing_review"],
            selected_date=selected_date,
            latest_date=available_dates[0] if available_dates else "",
            available_dates=available_dates,
            headline=headline,
            summary_lines=(
                f"市场环境: {review.market_environment}",
                f"总信号 {review.total_signals} / 已验证 {review.executed_signals}",
                f"胜率 {review.win_rate:.0%} / 总收益 {review.total_return:.2f}%",
                *summary_lines,
            ),
            report_summary_lines=(),
            runtime_lines=(),
            agenda_lines=self._build_agenda_lines(
                recommendation_lines=recommendation_lines,
                blocker_lines=blocker_lines,
                review_lines=tuple(improvement_lines) + review_lines + tuple(lesson_lines),
                focus_lines=(),
            ),
            recommendation_lines=recommendation_lines,
            watchlist_lines=watchlist_lines,
            blocker_lines=blocker_lines,
            review_lines=review_lines,
            next_day_focus_lines=(),
            report_markdown=format_daily_review(review),
            source_status=self.latest_source_status(
                task_id="main_chain",
                signal_date=selected_date,
            ),
            candidate_count=review.total_signals,
            actionable_count=review.executed_signals,
            watch_count=max(review.total_signals - review.executed_signals, 0),
            blocked_count=len(blocker_lines),
            detail_cards=self._build_detail_cards(
                self._dedupe_rows(
                    [
                        row
                        for row in self._task_signal_rows("main_chain")
                        if str(row.get("signal_date", "") or "") == selected_date
                    ]
                )
            ),
            ranking_lines=self._build_ranking_lines(
                self._dedupe_rows(
                    [
                        row
                        for row in self._task_signal_rows("main_chain")
                        if str(row.get("signal_date", "") or "") == selected_date
                    ]
                )
            ),
            market_environment=review.market_environment,
            strategy_breakdown_lines=strategy_breakdown_lines,
            lesson_lines=lesson_lines,
            improvement_lines=improvement_lines,
        )

    def _task_signal_rows(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.load_signal_rows()
        if task_id == "morning_breakout":
            return [row for row in rows if self._row_task_id(row) == "morning_breakout"]
        if task_id == "closing_premium":
            return [row for row in rows if self._row_task_id(row) == "closing_premium"]
        if task_id == "main_chain":
            return [row for row in rows if self._row_task_id(row) == "main_chain"]
        return rows

    def _row_task_id(self, row: dict[str, Any]) -> str:
        strategies = row.get("strategies") or []
        if isinstance(strategies, str):
            strategy_values = [strategies]
        else:
            strategy_values = [str(item) for item in strategies]
        haystack = " ".join(strategy_values).lower()
        if "morning_breakout" in haystack or "morning-breakout" in haystack:
            return "morning_breakout"
        if "closing_premium" in haystack or "closing-premium" in haystack:
            return "closing_premium"
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

    def _max_signal_date(self, rows: list[dict[str, Any]]) -> str:
        dates = self._signal_dates(rows)
        return dates[0] if dates else ""

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

    def _priority_bucket(self, row: dict[str, Any]) -> int:
        action = str(row.get("portfolio_action", "") or "").strip()
        rating = str(row.get("rating", "") or "").strip()
        blocker = str(row.get("candidate_blocker", "") or "").strip()
        if action == "promote":
            return 0
        if not blocker and is_tradable_rating(rating):
            return 1
        if action == "keep":
            return 2
        if action == "downgrade" or blocker:
            return 3
        if rating in {"watch", "avoid"}:
            return 4
        return 5

    def _action_label(self, row: dict[str, Any]) -> str:
        action = str(row.get("portfolio_action", "") or "").strip()
        if action:
            return portfolio_action_label(action)
        rating = str(row.get("rating", "") or "").strip()
        return rating_label(rating)

    def _candidate_status(self, row: dict[str, Any]) -> str:
        explicit = str(row.get("candidate_status", "") or "").strip()
        if explicit:
            return explicit
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

    def _is_actionable(self, row: dict[str, Any]) -> bool:
        action = str(row.get("portfolio_action", "") or "").strip()
        if action == "promote":
            return True
        if action == "downgrade":
            return False
        return is_tradable_rating(row.get("rating"))

    def _is_watch_candidate(self, row: dict[str, Any]) -> bool:
        rating = str(row.get("rating", "") or "").strip()
        action = str(row.get("portfolio_action", "") or "").strip()
        return rating in {"watch", "avoid"} or action in {"downgrade", "keep"}

    def _is_blocked(self, row: dict[str, Any]) -> bool:
        blocker = str(row.get("candidate_blocker", "") or "").strip()
        status = str(row.get("candidate_status", "") or "").strip()
        action = str(row.get("portfolio_action", "") or "").strip()
        return bool(blocker or "阻塞" in status or action == "downgrade")

    def _review_meta(self, row: dict[str, Any]) -> str:
        return format_review_meta(
            str(row.get("candidate_review_priority", "") or ""),
            str(row.get("candidate_review_window", "") or ""),
        )

    def _as_text_tuple(self, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            parts = [item.strip() for item in value.split("；")]
            return tuple(item for item in parts if item)
        if isinstance(value, (list, tuple)):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return ()

    def _build_detail_cards(
        self,
        rows: list[dict[str, Any]],
        *,
        limit: int = 6,
    ) -> tuple[DashboardCandidateCard, ...]:
        ordered = sorted(
            rows,
            key=lambda row: (
                -self._priority_bucket(row),
                float(row.get("score") or 0.0),
                str(row.get("created_at", "") or ""),
            ),
            reverse=True,
        )[:limit]
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
                    rank_label=self._rank_label(index, row),
                    score=float(row.get("score") or 0.0),
                    action_label=self._action_label(row),
                    status_label=self._candidate_status(row),
                    decision_note=self._decision_note(row),
                    next_step=str(row.get("candidate_next_step", "") or "").strip(),
                    blocker=str(row.get("candidate_blocker", "") or "").strip(),
                    review_meta=self._review_meta(row),
                    reasons=self._as_text_tuple(row.get("reasons")),
                    risks=self._as_text_tuple(row.get("risks")),
                    strategies=strategies_tuple,
                    data_source=str(row.get("run_actual_source", "") or ""),
                )
            )
        return tuple(cards)

    def _rank_label(self, index: int, row: dict[str, Any]) -> str:
        if index == 1 and self._is_actionable(row):
            return "首选"
        if index == 2 and self._is_actionable(row):
            return "次选"
        if self._is_actionable(row):
            return "备选"
        if self._is_blocked(row):
            return "阻塞观察"
        return "观察"

    def _decision_note(self, row: dict[str, Any]) -> str:
        blocker = str(row.get("candidate_blocker", "") or "").strip()
        next_step = str(row.get("candidate_next_step", "") or "").strip()
        action = str(row.get("portfolio_action", "") or "").strip()
        if blocker:
            return blocker
        if action == "promote":
            return "PM 已上调优先级，进入优先跟踪序列"
        if action == "keep":
            return "维持顺位，等待更强确认"
        if next_step:
            return next_step
        return "按当前顺位继续跟踪"

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
        blocker = str(row.get("candidate_blocker", "") or "").strip() or "等待条件解除"
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
    ) -> str:
        label = _TASK_LABELS[task_id]
        if actionable_rows:
            names = "、".join(self._symbol_name(row) for row in actionable_rows[:3])
            return f"{label} {signal_date}: 可执行 {len(actionable_rows)} 只，先看 {names}"
        if watch_rows:
            names = "、".join(self._symbol_name(row) for row in watch_rows[:3])
            return f"{label} {signal_date}: 无可执行标的，转观察池 {names}"
        return f"{label} {signal_date}: 暂无真实落盘结果"

    def _summary_lines_for_signal_task(
        self,
        *,
        task_id: str,
        rows: list[dict[str, Any]],
        actionable_rows: list[dict[str, Any]],
        watch_rows: list[dict[str, Any]],
        blocked_rows: list[dict[str, Any]],
    ) -> tuple[str, ...]:
        lines = [
            f"任务: {_TASK_LABELS[task_id]} / 候选 {len(rows)} / 可执行 {len(actionable_rows)} / 观察 {len(watch_rows)}"
        ]
        if blocked_rows:
            lines.append(f"阻塞 {len(blocked_rows)} 只，优先处理观察复核。")
        source_status = self._source_status_from_row(max(rows, key=self._row_meta_key)) if rows else {}
        if source_status:
            lines.append(
                f"数据源: {source_status.get('requested_source', '-')}"
                f" -> {source_status.get('actual_source', '-')}"
                f" / {source_status.get('health_label', '-')}"
            )
        return tuple(lines)

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
            agenda.append(f"先解阻塞: {blocker_lines[0]}")
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
            return "暂无结果"
        if task_id == "closing_review":
            if view.report_markdown.strip():
                return "已复盘"
            return "暂无结果"
        if view.actionable_count > 0:
            return "有推荐"
        if view.blocked_count > 0:
            return "待解锁"
        if view.watch_count > 0:
            return "观察中"
        if view.candidate_count > 0:
            return "已产出"
        return "暂无结果"

    def _extract_report_insights(self, markdown_text: str) -> DashboardReportInsights:
        if not markdown_text.strip():
            return DashboardReportInsights(
                report_summary_lines=(),
                runtime_lines=(),
                market_environment="",
                next_day_focus_lines=(),
            )
        execution_lines = self._section_lines(markdown_text, "执行摘要", "📌 执行摘要")
        runtime_lines = self._runtime_snapshot_lines(
            self._section_lines(markdown_text, "运行参数")
        )
        market_environment = self._market_environment_line(markdown_text)
        next_day_focus_lines = self._focus_lines(
            self._section_lines(markdown_text, "明日重点")
        )
        return DashboardReportInsights(
            report_summary_lines=execution_lines,
            runtime_lines=runtime_lines,
            market_environment=market_environment,
            next_day_focus_lines=next_day_focus_lines,
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
            "数据时效:",
            "数据健康:",
            "候选池:",
            "thresholds.version:",
            "regime:",
        )
        selected = [
            line for line in lines if any(line.startswith(prefix) for prefix in prefixes)
        ]
        return tuple(selected[:6])

    def _market_environment_line(self, markdown_text: str) -> str:
        lines = self._section_lines(markdown_text, "市场态势")
        if not lines:
            return ""
        first_line = lines[0]
        if "当前市场态势:" in first_line:
            first_line = first_line.split("当前市场态势:", 1)[1].strip()
        return first_line.replace("**", "").strip()

    def _focus_lines(self, lines: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(line for line in lines[:5] if line)

    def _report_markdown_for_signal_task(self, task_id: str, signal_date: str) -> str:
        if task_id == "main_chain":
            latest_signal_date = self._max_signal_date(self._task_signal_rows(task_id))
            if signal_date and signal_date != latest_signal_date:
                return self._read_briefing_markdown(signal_date)
            path = self.reports_dir / "latest.md"
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def _read_briefing_markdown(self, signal_date: str) -> str:
        if not signal_date:
            return ""
        path = self.reports_dir / f"briefing-{signal_date}.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")
