from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from aqsp.briefing.closing_review import DailyReview, WeeklySummary
from aqsp.briefing.generator import Briefing
from aqsp.briefing.debate import DebateResult
from aqsp.monitor.checker import MonitorResult
from aqsp.models import PickResult
from aqsp.portfolio.manager import PortfolioDecisionSummary
from aqsp.notifier import prepend_source_status_banner
from aqsp.presentation import format_symbol_name
from aqsp.strategies.closing_premium import PremiumSignal, format_closing_signals
from aqsp.strategies.morning_breakout import BreakoutSignal, format_morning_signals

NotifyMode = Literal["summary", "full"]


def _safe_mode(mode: str) -> NotifyMode:
    return "full" if mode == "full" else "summary"


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
    portfolio_summary: PortfolioDecisionSummary | None = None,
    actual_source: str,
    source_health_label: str,
    source_health_message: str,
    requested_source: str = "",
    cold_start_days: int = 0,
    cold_start_min_days: int = 0,
    is_cold_start: bool = False,
    circuit_breaker_reason: str = "",
    mode: str = "summary",
) -> str:
    lines = [
        "# AI选股日报",
        "",
        "## 核心结论",
        "",
        f"- 数据日期: {run_date}",
    ]
    if circuit_breaker_reason:
        lines.append(f"- 组合保护: {circuit_breaker_reason}")
    elif tradable:
        lines.append(f"- 可执行标的: {len(tradable)}")
        lines.append(f"- 首选标的: {_format_daily_pick(tradable[0])}")
    else:
        lines.append("- 可执行标的: 0")
        lines.append("- 主链状态: 今日无可执行标的，仅观察")
    if is_cold_start and cold_start_min_days > 0:
        lines.append(f"- 冷启动进度: {cold_start_days}/{cold_start_min_days}")
    if portfolio_summary is not None and portfolio_summary.allocations:
        top_alloc = "、".join(
            f"{item.symbol} {item.weight:.0%}"
            for item in portfolio_summary.allocations[:3]
        )
        lines.append(f"- 配仓建议: {top_alloc}")
    if portfolio_summary is not None and portfolio_summary.cash_reserve > 0:
        lines.append(f"- 现金留存: {portfolio_summary.cash_reserve:.0%}")

    lines.extend(["", "## Top 候选", ""])
    if tradable:
        top_n = 5 if _safe_mode(mode) == "full" else 3
        for index, pick in enumerate(tradable[:top_n], start=1):
            lines.append(f"{index}. {_format_daily_pick(pick)}")
    else:
        lines.append("- 今日无可执行候选，等待下一轮主链信号。")

    lines.extend(
        [
            "",
            "## 行动建议",
            "",
            (
                "1. 优先核对首选标的的开盘强弱与流动性，再决定是否执行。"
                if tradable
                else "1. 本次以观察为主，不建议为了凑单强行开仓。"
            ),
        ]
    )
    if circuit_breaker_reason:
        lines.append("2. 熔断保护中，暂停新开仓，只保留复盘和观察。")
    elif is_cold_start:
        lines.append("2. 冷启动未完成，结果仅供跟踪，不要放大仓位。")

    return prepend_source_status_banner(
        "\n".join(lines),
        source_status={
            "requested_source": requested_source,
            "actual_source": actual_source,
            "health_label": source_health_label,
            "health_message": source_health_message,
        },
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
    if review.key_lessons:
        lines.append(f"- 关键复盘: {review.key_lessons[0]}")
    if review.improvement_suggestions:
        lines.extend(["", "## 明日动作", "", f"1. {review.improvement_suggestions[0]}"])
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
        lines.append(
            f"- {symbol_name} | 裁决 {result.recommended_adjustment.upper()} | "
            f"分歧度 {result.disagreement_score:.0%} | {consensus}"
        )
    return "\n".join(lines)


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
    return (
        f"{symbol_name} | {pick.score:.0f}分 | "
        f"买 {pick.ideal_buy:g} / 损 {pick.stop_loss:g} / 盈 {pick.take_profit:g}"
    )
