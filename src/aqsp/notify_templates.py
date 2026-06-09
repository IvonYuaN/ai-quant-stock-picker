from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from aqsp.briefing.closing_review import DailyReview, WeeklySummary
from aqsp.briefing.generator import Briefing
from aqsp.briefing.debate import DebateResult
from aqsp.monitor.checker import MonitorResult
from aqsp.models import PickResult
from aqsp.portfolio.manager import PortfolioDecisionSummary
from aqsp.portfolio.snapshot import SnapshotDiff, snapshot_diff_highlights, summarize_snapshot_diff
from aqsp.notifier import prepend_source_status_banner
from aqsp.presentation import (
    format_symbol_name,
    format_watch_review_action,
    format_watch_review_line,
    review_priority_label,
)
from aqsp.strategies.closing_premium import PremiumSignal, format_closing_signals
from aqsp.strategies.morning_breakout import BreakoutSignal, format_morning_signals

NotifyMode = Literal["summary", "full"]


def _safe_mode(mode: str) -> NotifyMode:
    return "full" if mode == "full" else "summary"


def _blocked_watchlist_status(
    portfolio_summary: PortfolioDecisionSummary | None,
) -> str:
    if portfolio_summary is None:
        return "今日无可执行标的，仅观察"
    watchlist = tuple(portfolio_summary.watchlist[:2])
    if watchlist:
        return "今日无可执行标的，转入观察池：" + "、".join(watchlist)
    if portfolio_summary.execution_blockers:
        return "今日无可执行标的，受执行约束暂仅观察"
    return "今日无可执行标的，仅观察"


def build_briefing_notification(
    briefing: Briefing,
    source_status: dict[str, str | bool] | None = None,
    mode: str = "summary",
) -> str:
    if _safe_mode(mode) == "full":
        return prepend_source_status_banner(
            briefing.to_markdown(),
            source_status=source_status,
        )

    summary = briefing.generate_smart_summary().strip()
    sections = {section.title: section.content.strip() for section in briefing.sections}
    body_parts: list[str] = []
    if summary:
        body_parts.append("## 主链摘要\n\n" + summary)
    main_chain = sections.get("主链总览", "")
    if main_chain:
        body_parts.append("## 主链总览\n\n" + main_chain)
    allocation_execution = _format_allocation_execution(briefing.portfolio_summary)
    if allocation_execution:
        body_parts.append("## 配仓执行\n\n" + allocation_execution)
    debate = _format_debate_summary(briefing.debate_results)
    if debate:
        body_parts.append("## 多Agent辩论\n\n" + debate)
    next_day = sections.get("明日重点", "")
    if next_day:
        body_parts.append("## 明日动作\n\n" + next_day)
    body = "\n\n".join(part for part in body_parts if part).strip() or briefing.to_markdown()
    return prepend_source_status_banner(body, source_status=source_status)


def build_daily_run_notification(
    *,
    run_date: str,
    tradable: Sequence[PickResult],
    candidates: Sequence[PickResult] = (),
    portfolio_summary: PortfolioDecisionSummary | None = None,
    debate_results: Sequence[DebateResult] = (),
    actual_source: str,
    source_health_label: str,
    source_health_message: str,
    requested_source: str = "",
    cold_start_days: int = 0,
    cold_start_min_days: int = 0,
    is_cold_start: bool = False,
    circuit_breaker_reason: str = "",
    snapshot_diff: SnapshotDiff | None = None,
    mode: str = "summary",
) -> str:
    lead_conclusion = _daily_lead_conclusion(
        tradable=tradable,
        candidates=candidates,
        portfolio_summary=portfolio_summary,
        circuit_breaker_reason=circuit_breaker_reason,
    )
    lines = [
        "# AQSP 研究日报",
        "",
        "## 核心结论",
        "",
        f"- 数据日期: {run_date}",
        f"- **一眼结论**: {lead_conclusion}",
    ]
    if circuit_breaker_reason:
        lines.append(f"- 组合保护: {circuit_breaker_reason}")
    elif tradable:
        lines.append(f"- 可执行标的: {len(tradable)}")
        lines.append(f"- 首选标的: {_format_daily_pick(tradable[0])}")
    else:
        lines.append("- 可执行标的: 0")
        lines.append("- 主链状态: " + _blocked_watchlist_status(portfolio_summary))
    if is_cold_start and cold_start_min_days > 0:
        lines.append(f"- 冷启动进度: {cold_start_days}/{cold_start_min_days}")
    if portfolio_summary is not None and portfolio_summary.regime_label:
        lines.append(f"- 当前市况: {portfolio_summary.regime_label}")
    if portfolio_summary is not None and portfolio_summary.strategy_mix_name:
        lines.append(
            "- 策略主配比: "
            f"{portfolio_summary.strategy_mix_name}"
            + (
                f" | {portfolio_summary.strategy_mix_description}"
                if portfolio_summary.strategy_mix_description
                else ""
            )
        )
    if portfolio_summary is not None and portfolio_summary.strategy_focus:
        lines.append(
            "- 优先策略: "
            + "、".join(portfolio_summary.strategy_focus[:3])
        )
    if portfolio_summary is not None and portfolio_summary.action_hotspots:
        lines.append(
            "- 裁决热点: "
            + "；".join(portfolio_summary.action_hotspots[:2])
        )
    if portfolio_summary is not None and portfolio_summary.allocations:
        top_alloc = "、".join(
            f"{item.symbol} {item.weight:.0%}"
            for item in portfolio_summary.allocations[:3]
        )
        lines.append(f"- 配仓建议: {top_alloc}")
    elif portfolio_summary is not None and portfolio_summary.watchlist:
        lines.append(
            "- 观察池: " + "、".join(portfolio_summary.watchlist[:3])
        )
    if portfolio_summary is not None and portfolio_summary.cash_reserve > 0:
        lines.append(f"- 现金留存: {portfolio_summary.cash_reserve:.0%}")
    if portfolio_summary is not None and portfolio_summary.execution_blockers:
        lines.append(
            "- 执行阻塞: "
            + "；".join(portfolio_summary.execution_blockers[:2])
        )
    if snapshot_diff is not None and snapshot_diff.has_changes:
        lines.append(f"- 候选变化: {summarize_snapshot_diff(snapshot_diff)}")
    if debate_results:
        lead = debate_results[0]
        consensus = lead.final_consensus or lead.adjustment_reason or "暂无共识摘要"
        lines.append(
            f"- 重点辩论: {lead.symbol} {lead.name} | {lead.recommended_adjustment.upper()} | {consensus}"
        )

    lines.extend(["", "## 📋 候选简表", ""])
    lines.extend(_daily_candidate_table(tradable, candidates, mode=mode))
    allocation_execution = _format_allocation_execution(portfolio_summary)
    if allocation_execution:
        lines.extend(["", "## 配仓执行", "", allocation_execution])
    debate = _format_debate_summary(debate_results[:2])
    if debate:
        lines.extend(["", "## 多Agent辩论", "", debate])
    if snapshot_diff is not None and snapshot_diff.has_changes:
        lines.extend(["", "## 候选变化", ""])
        lines.extend(snapshot_diff_highlights(snapshot_diff, max_items=3))

    lines.extend(
        [
            "",
            "## 行动建议",
            "",
            _daily_watch_action_line(candidates, portfolio_summary)
            or _daily_action_line_one(tradable, portfolio_summary),
        ]
    )
    if circuit_breaker_reason:
        lines.append("2. 熔断保护中，暂停新开仓，只保留复盘和观察。")
    elif is_cold_start:
        lines.append("2. 冷启动未完成，结果仅供跟踪，不要放大仓位。")
    elif portfolio_summary is not None and portfolio_summary.allocation_note:
        lines.append(f"2. {portfolio_summary.allocation_note}。")
    if debate_results:
        lines.append(f"3. {_daily_debate_action_line(debate_results[0])}")

    return prepend_source_status_banner(
        "\n".join(lines),
        source_status={
            "requested_source": requested_source,
            "actual_source": actual_source,
            "health_label": source_health_label,
            "health_message": source_health_message,
        },
    )


def _daily_lead_conclusion(
    *,
    tradable: Sequence[PickResult],
    candidates: Sequence[PickResult],
    portfolio_summary: PortfolioDecisionSummary | None,
    circuit_breaker_reason: str,
) -> str:
    if circuit_breaker_reason:
        return f"🛡️ 组合保护触发，先暂停新增纸面动作；原因：{circuit_breaker_reason}"
    if tradable:
        lead = tradable[0]
        return f"🎯 有 {len(tradable)} 个纸面复核对象，先看 {format_symbol_name(lead.symbol, lead.name)}"
    if portfolio_summary is not None and portfolio_summary.watchlist:
        names = "、".join(portfolio_summary.watchlist[:2])
        return f"👀 今日无主仓对象，先盯观察池：{names}"
    if candidates:
        lead = candidates[0]
        return f"👀 仅观察，先看 {format_symbol_name(lead.symbol, lead.name)} 的确认条件"
    return "⏸️ 今日没有足够清晰的候选，保持观察"


def _table_cell(value: object) -> str:
    text = str(value or "").strip()
    return text.replace("|", "/") or "-"


def _daily_candidate_table(
    tradable: Sequence[PickResult],
    candidates: Sequence[PickResult],
    *,
    mode: str,
) -> list[str]:
    rows: list[str] = [
        "| # | 标的 | 状态 | 分数 | 处理 | 关键点 |",
        "|---:|---|---|---:|---|---|",
    ]
    picks = tuple(tradable) if tradable else tuple(candidates)
    if not picks:
        rows.append("| - | - | - | - | 观察 | 今日无清晰候选 |")
        return rows

    top_n = 5 if _safe_mode(mode) == "full" else 3
    for index, pick in enumerate(picks[:top_n], start=1):
        rows.append(_daily_candidate_table_row(index, pick, tradable=bool(tradable)))
    return rows


def _daily_candidate_table_row(
    index: int,
    pick: PickResult,
    *,
    tradable: bool,
) -> str:
    symbol_name = format_symbol_name(pick.symbol, pick.name)
    status = str(pick.metrics.get("candidate_status", "") or "")
    blocker = _candidate_blocker(pick)
    if blocker:
        status = status or "观察阻塞"
        action = "⛔ 等阻塞解除"
        key = blocker
    elif tradable:
        status = status or "纸面复核"
        action = "🎯 纸面复核"
        key = pick.reasons[0] if pick.reasons else "先看开盘承接"
    else:
        status = status or "观察"
        action = "👀 观察"
        key = (
            _candidate_next_step(pick)
            or (pick.reasons[0] if pick.reasons else "等待更强确认")
        )
    review_window = _candidate_review_window(pick)
    review_priority = _review_priority_label(_candidate_review_priority(pick))
    review_meta = " / ".join(part for part in (review_priority, review_window) if part)
    if review_meta:
        key = f"{key}；复核 {review_meta}"
    return (
        f"| {index} | {_table_cell(symbol_name)} | {_table_cell(status)} | "
        f"{pick.score:.0f} | {_table_cell(action)} | {_table_cell(key)} |"
    )


def build_monitor_notification(
    results: Sequence[MonitorResult],
    mode: str = "summary",
) -> str:
    triggered = [result for result in results if result.triggered]
    critical = [result for result in triggered if result.severity == "critical"]
    warnings = [result for result in triggered if result.severity == "warning"]

    lines = [
        "# 系统监控告警",
        "",
        "## 核心结论",
        "",
        f"- 严重告警: {len(critical)}",
        f"- 一般告警: {len(warnings)}",
    ]
    if critical:
        lines.append(f"- 最高优先级: {critical[0].name} | {critical[0].message}")
    elif warnings:
        lines.append(f"- 首条告警: {warnings[0].name} | {warnings[0].message}")
    else:
        lines.append("- 总体状态: 正常")

    if _safe_mode(mode) == "full":
        lines.extend(["", "## 详细告警", ""])
        lines.extend(_format_monitor_results(triggered or list(results)))
        return "\n".join(lines)

    if critical or warnings:
        lines.extend(["", "## 行动建议", ""])
        lines.extend(_monitor_actions(critical, warnings))
        lines.extend(["", "## 告警回放", ""])
        lines.extend(_format_monitor_results((critical + warnings)[:5]))
    return "\n".join(lines)


def build_morning_breakout_notification(
    signals: Sequence[BreakoutSignal],
    mode: str = "summary",
    top_n: int = 3,
) -> str:
    if _safe_mode(mode) == "full":
        return format_morning_signals(list(signals), top_n=max(top_n, 5))

    if not signals:
        return "\n".join(
            [
                "# 早盘打板策略",
                "",
                "## 核心结论",
                "",
                "- 总体状态: 今日未发现符合条件的早盘打板标的",
                "- 行动建议: 保持观望，等待更强的量价共振信号",
            ]
        )

    lines = [
        "# 早盘打板策略",
        "",
        "## 核心结论",
        "",
        f"- 候选数量: {len(signals)}",
        f"- 首选标的: {_format_breakout_signal(signals[0])}",
        "- 风险提示: 打板策略波动大，默认轻仓+严格止损",
        "",
        "## Top 候选",
        "",
    ]
    for index, signal in enumerate(signals[:top_n], start=1):
        lines.append(f"{index}. {_format_breakout_signal(signal)}")
    return "\n".join(lines)


def build_closing_premium_notification(
    signals: Sequence[PremiumSignal],
    mode: str = "summary",
    top_n: int = 3,
) -> str:
    if _safe_mode(mode) == "full":
        return format_closing_signals(list(signals), top_n=max(top_n, 5))

    if not signals:
        return "\n".join(
            [
                "# 尾盘溢价策略",
                "",
                "## 核心结论",
                "",
                "- 总体状态: 今日未发现符合条件的尾盘溢价标的",
                "- 行动建议: 继续观察尾盘量价异动，避免勉强开仓",
            ]
        )

    lines = [
        "# 尾盘溢价策略",
        "",
        "## 核心结论",
        "",
        f"- 候选数量: {len(signals)}",
        f"- 首选标的: {_format_premium_signal(signals[0])}",
        "- 行动建议: 优先跟踪量价突破型标的，次日开盘只做强势延续",
        "",
        "## Top 候选",
        "",
    ]
    for index, signal in enumerate(signals[:top_n], start=1):
        lines.append(f"{index}. {_format_premium_signal(signal)}")
    return "\n".join(lines)


def build_closing_review_notification(
    *,
    review: DailyReview | None = None,
    weekly_summary: WeeklySummary | None = None,
    mode: str = "summary",
) -> str:
    if weekly_summary is not None:
        return _build_weekly_review_notification(weekly_summary, mode=mode)
    if review is None:
        raise ValueError("review or weekly_summary is required")
    if _safe_mode(mode) == "full":
        from aqsp.briefing.closing_review import format_daily_review

        return format_daily_review(review)

    lines = [
        "# 收盘复盘",
        "",
        "## 核心结论",
        "",
        f"- 日期: {review.date}",
        f"- 胜率/收益: {review.win_rate:.1%} / {review.total_return:.2f}%",
        f"- 执行情况: {review.executed_signals}/{review.total_signals}",
    ]
    if review.main_chain_summary:
        lines.append(f"- 主链裁决: {review.main_chain_summary[0]}")
        for item in review.main_chain_summary[1:4]:
            lines.append(f"- {item}")
    if review.key_lessons:
        lines.append(f"- 关键复盘: {review.key_lessons[0]}")
    if review.improvement_suggestions:
        lines.extend(["", "## 明日动作", "", f"1. {review.improvement_suggestions[0]}"])
        review_action = next(
            (
                item.split(": ", 1)[1]
                for item in review.main_chain_summary
                if item.startswith("观察复核: ")
            ),
            "",
        )
        blocker_action = next(
            (
                item.split(": ", 1)[1]
                for item in review.main_chain_summary
                if item.startswith("执行阻塞: ")
            ),
            "",
        )
        if review_action:
            lines.append(f"2. 优先复核 {review_action}。")
        elif blocker_action:
            lines.append(f"2. 先处理 {blocker_action}。")
    if review.strategy_breakdown:
        lines.extend(["", "## 策略表现", ""])
        for strategy, stats in list(review.strategy_breakdown.items())[:3]:
            lines.append(
                f"- {strategy}: {stats['wins']}/{stats['total']} | "
                f"胜率 {stats['win_rate']:.1%} | 收益 {stats['total_return']:.2f}%"
            )
    return "\n".join(lines)


def _build_weekly_review_notification(
    summary: WeeklySummary,
    mode: str = "summary",
) -> str:
    if _safe_mode(mode) == "full":
        from aqsp.briefing.closing_review import format_weekly_summary

        return format_weekly_summary(summary)

    return "\n".join(
        [
            "# 周度复盘",
            "",
            "## 核心结论",
            "",
            f"- 周期: {summary.week_start} ~ {summary.week_end}",
            f"- 总收益/胜率: {summary.total_return:.2f}% / {summary.win_rate:.1%}",
            f"- 夏普/回撤: {summary.sharpe_ratio:.2f} / {summary.max_drawdown:.2f}%",
            f"- 最佳策略: {summary.best_strategy}",
            f"- 最差策略: {summary.worst_strategy}",
            "",
            "## 下周动作",
            "",
            f"1. {summary.next_week_outlook}",
        ]
    )


def _format_debate_summary(results: Sequence[DebateResult]) -> str:
    if not results:
        return ""
    lines: list[str] = []
    for result in results[:3]:
        symbol_name = format_symbol_name(result.symbol, result.name)
        consensus = result.final_consensus or result.adjustment_reason or "暂无共识摘要"
        line = (
            f"- {symbol_name} | 裁决 {result.recommended_adjustment.upper()} | "
            f"分歧度 {result.disagreement_score:.0%} | {consensus}"
        )
        if result.risk_warnings:
            line += f" | 风险: {result.risk_warnings[0]}"
        elif result.opportunity_highlights:
            line += f" | 机会: {result.opportunity_highlights[0]}"
        lines.append(line)
    return "\n".join(lines)


def _format_allocation_execution(
    portfolio_summary: PortfolioDecisionSummary | None,
) -> str:
    if portfolio_summary is None:
        return ""
    lines: list[str] = []
    if portfolio_summary.allocations:
        for item in portfolio_summary.allocations[:3]:
            display = format_symbol_name(item.symbol, item.name)
            rationale = "；".join(item.rationale[:3])
            line = f"- {display} {item.weight:.0%}"
            if rationale:
                line += f" | {rationale}"
            lines.append(line)
    elif portfolio_summary.watchlist:
        lines.append("- 暂无可执行主仓，先盯观察池:")
        for item in portfolio_summary.watchlist[:3]:
            lines.append(f"  - {item}")
    if portfolio_summary.watch_reviews:
        lines.append("- 观察复核:")
        for item in portfolio_summary.watch_reviews[:2]:
            lines.append(
                "  - "
                + format_watch_review_line(
                    format_symbol_name(item.symbol, item.name),
                    priority=item.priority,
                    review_window=item.review_window,
                    next_step=item.next_step,
                )
            )
    if portfolio_summary.cash_reserve > 0:
        lines.append(f"- 现金留存 {portfolio_summary.cash_reserve:.0%}")
    if portfolio_summary.allocation_note:
        lines.append(f"- 执行约束: {portfolio_summary.allocation_note}")
    if portfolio_summary.execution_blockers:
        lines.append(
            "- 当前阻塞: " + "；".join(portfolio_summary.execution_blockers[:2])
        )
    return "\n".join(lines)


def _daily_action_line_one(
    tradable: Sequence[PickResult],
    portfolio_summary: PortfolioDecisionSummary | None,
) -> str:
    if portfolio_summary is not None and portfolio_summary.allocations:
        lead = portfolio_summary.allocations[0]
        return (
            f"1. 先看 {lead.symbol} {lead.name} 的开盘强弱与流动性，"
            f"若确认延续，再按 {lead.weight:.0%} 参考仓位执行。"
        )
    if portfolio_summary is not None and portfolio_summary.watchlist:
        if tradable:
            return "1. 优先核对首选标的的开盘强弱与流动性，再决定是否执行。"
        return (
            "1. 本次先盯观察池里最强票的开盘承接，"
            "只有阻塞条件解除后再考虑转入执行名单。"
        )
    if tradable:
        return "1. 优先核对首选标的的开盘强弱与流动性，再决定是否执行。"
    return "1. 本次以观察为主，不建议为了凑单强行开仓。"


def _candidate_blocker(pick: PickResult) -> str:
    return str(pick.metrics.get("candidate_blocker", "") or "")


def _candidate_next_step(pick: PickResult) -> str:
    return str(pick.metrics.get("candidate_next_step", "") or "")


def _candidate_review_window(pick: PickResult) -> str:
    return str(pick.metrics.get("candidate_review_window", "") or "")


def _candidate_review_priority(pick: PickResult) -> str:
    return str(pick.metrics.get("candidate_review_priority", "") or "")


def _review_priority_label(priority: str) -> str:
    return review_priority_label(priority)


def _daily_watch_action_line(
    candidates: Sequence[PickResult],
    portfolio_summary: PortfolioDecisionSummary | None = None,
) -> str:
    if portfolio_summary is not None and portfolio_summary.watch_reviews:
        lead = portfolio_summary.watch_reviews[0]
        return "1. " + format_watch_review_action(
            f"{lead.symbol} {lead.name}",
            priority=lead.priority,
            review_window=lead.review_window,
            next_step=lead.next_step,
        )
    if not candidates:
        return ""
    lead = candidates[0]
    next_step = _candidate_next_step(lead)
    review_window = _candidate_review_window(lead)
    review_priority = _review_priority_label(_candidate_review_priority(lead))
    if next_step:
        return "1. " + format_watch_review_action(
            f"{lead.symbol} {lead.name}",
            priority=review_priority,
            review_window=review_window,
            next_step=next_step,
        )
    return ""


def _daily_debate_action_line(result: DebateResult) -> str:
    if result.recommended_adjustment == "lower":
        return (
            f"{result.symbol} {result.name} 的辩论偏谨慎，若开盘不及预期，优先降低仓位或延后执行"
        )
    if result.recommended_adjustment == "raise":
        return (
            f"{result.symbol} {result.name} 获辩论加分，可作为优先确认对象，但仍需尊重开盘流动性"
        )
    if result.disagreement_score >= 0.5:
        return (
            f"{result.symbol} {result.name} 多空分歧较大，除非开盘明显走强，否则以观察为主"
        )
    return f"{result.symbol} {result.name} 辩论分歧可控，可按主链节奏正常跟踪"


def _format_monitor_results(results: Sequence[MonitorResult]) -> list[str]:
    lines: list[str] = []
    for result in results:
        lines.append(f"- {result.name}: {result.message}")
        for key, value in list(result.details.items())[:3]:
            lines.append(f"  {key}: {value}")
    return lines


def _monitor_actions(
    critical: Sequence[MonitorResult],
    warnings: Sequence[MonitorResult],
) -> list[str]:
    if critical:
        first = critical[0]
        return [
            f"1. 先处理 `{first.name}`，避免后续任务继续误报或停摆。",
            "2. 暂停依赖该检查项的自动链路，问题排除后再恢复通知。",
        ]
    if warnings:
        return [
            f"1. 优先复核 `{warnings[0].name}` 的输入源或配置。",
            "2. 本次结果可观察，但不要直接放大仓位。",
        ]
    return ["1. 当前无需动作。"]


def _format_breakout_signal(signal: BreakoutSignal) -> str:
    symbol_name = format_symbol_name(signal.symbol, signal.name)
    lead_reason = signal.reasons[0] if signal.reasons else signal.signal_type
    return (
        f"{symbol_name} | {signal.signal_type} | {signal.score:.1f}分 | "
        f"现价 {signal.current_price:.2f} / 目标 {signal.target_price:.2f} / "
        f"止损 {signal.stop_loss:.2f} | {lead_reason}"
    )


def _format_premium_signal(signal: PremiumSignal) -> str:
    symbol_name = format_symbol_name(signal.symbol, signal.name)
    lead_reason = signal.reasons[0] if signal.reasons else signal.signal_type
    return (
        f"{symbol_name} | {signal.signal_type} | {signal.score:.1f}分 | "
        f"入场 {signal.entry_price:.2f} / 止损 {signal.stop_loss:.2f} / "
        f"预期 {signal.expected_return:.2f}% | {lead_reason}"
    )


def _format_daily_pick(pick: PickResult) -> str:
    symbol_name = format_symbol_name(pick.symbol, pick.name)
    status = str(pick.metrics.get("candidate_status", "") or "")
    parts = [symbol_name]
    if status:
        parts.append(status)
    parts.append(f"{pick.score:.0f}分")
    parts.append(f"买 {pick.ideal_buy:g} / 损 {pick.stop_loss:g} / 盈 {pick.take_profit:g}")
    return " | ".join(parts)


def _format_watch_pick(pick: PickResult) -> str:
    symbol_name = format_symbol_name(pick.symbol, pick.name)
    status = str(pick.metrics.get("candidate_status", "") or "")
    lead_reason = pick.reasons[0] if pick.reasons else "等待更强确认"
    parts = [symbol_name]
    if status:
        parts.append(status)
    parts.extend((f"{pick.score:.0f}分", "观察", lead_reason))
    blocker = _candidate_blocker(pick)
    if blocker:
        parts.append(f"阻塞: {blocker}")
    review_window = _candidate_review_window(pick)
    review_priority = _review_priority_label(_candidate_review_priority(pick))
    review_meta = " / ".join(part for part in (review_priority, review_window) if part)
    if review_meta:
        parts.append(f"复核: {review_meta}")
    return " | ".join(parts)
