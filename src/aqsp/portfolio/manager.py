from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass

from aqsp.core.types import PickResult
from aqsp.market_context import format_pick_market_context_summary
from aqsp.presentation import format_symbol_name
from aqsp.portfolio.correlation import CorrelationResult
from aqsp.portfolio.optimizer import (
    PortfolioAllocation,
    optimize_portfolio_allocations_from_risk,
)
from aqsp.portfolio.risk_summary import summarize_portfolio_risk
from aqsp.regime import build_runtime_strategy_mix
from aqsp.portfolio.sector_check import ConcentrationResult, get_sector
from aqsp.ratings import is_tradable_rating
from aqsp.strategies.thresholds import Thresholds, load_thresholds

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PortfolioDecision:
    symbol: str
    action: str
    score_delta: float
    reasons: tuple[str, ...]
    priority_delta: float = 0.0


@dataclass(frozen=True)
class WatchlistReviewItem:
    symbol: str
    name: str
    blocker: str
    next_step: str
    review_window: str
    priority: str


@dataclass(frozen=True)
class PortfolioDecisionSummary:
    promote_count: int
    downgrade_count: int
    keep_count: int
    top_focus: tuple[str, ...]
    watchlist: tuple[str, ...]
    allocations: tuple[PortfolioAllocation, ...]
    cash_reserve: float
    allocation_note: str
    regime_label: str = ""
    strategy_mix_name: str = ""
    strategy_mix_description: str = ""
    strategy_focus: tuple[str, ...] = ()
    strategy_weights: tuple[tuple[str, float], ...] = ()
    cross_market_overview: str = ""
    cross_market_focus: tuple[str, ...] = ()
    debate_focus: tuple[str, ...] = ()
    debate_support_points: tuple[str, ...] = ()
    debate_opposition_points: tuple[str, ...] = ()
    debate_watch_items: tuple[str, ...] = ()
    debate_risk_gates: tuple[str, ...] = ()
    debate_next_triggers: tuple[str, ...] = ()
    debate_priority_queue: tuple[str, ...] = ()
    action_hotspots: tuple[str, ...] = ()
    execution_blockers: tuple[str, ...] = ()
    watch_reviews: tuple[WatchlistReviewItem, ...] = ()
    portfolio_risk_lines: tuple[str, ...] = ()

    @property
    def headline(self) -> str:
        return (
            f"上调 {self.promote_count} / 降级 {self.downgrade_count} / "
            f"维持 {self.keep_count}"
        )

    @property
    def has_actionable_focus(self) -> bool:
        return bool(self.top_focus)

    @property
    def has_allocations(self) -> bool:
        return bool(self.allocations)

    @property
    def has_strategy_mix(self) -> bool:
        return bool(self.strategy_mix_name)


@dataclass(frozen=True)
class PortfolioDecisionBundle:
    picks: list[PickResult]
    decisions: tuple[PortfolioDecision, ...]
    summary: PortfolioDecisionSummary


def _display_name(pick: PickResult) -> str:
    return format_symbol_name(pick.symbol, pick.name)


def _build_strategy_mix_summary(
    regime: str, *, thresholds: Thresholds
) -> tuple[str, str, str, tuple[str, ...], tuple[tuple[str, float], ...]]:
    mix = build_runtime_strategy_mix(regime, thresholds=thresholds)
    return (
        mix.regime_label,
        mix.mix_name,
        mix.mix_description,
        mix.strategy_focus,
        mix.strategy_weights,
    )


def _summarize_action_hotspots(
    decisions: list[PortfolioDecision],
) -> tuple[str, ...]:
    counter: Counter[str] = Counter()
    for decision in decisions:
        for reason in decision.reasons:
            if reason and reason != "保持原排序":
                counter[reason] += 1
    return tuple(reason for reason, _ in counter.most_common(3))


def _build_execution_blockers(
    picks: list[PickResult],
    decision_by_symbol: dict[str, PortfolioDecision],
) -> tuple[str, ...]:
    blockers: list[str] = []
    for pick in picks:
        decision = decision_by_symbol.get(pick.symbol)
        if decision is None or decision.action != "downgrade":
            continue
        reason = next(
            (item for item in decision.reasons if item and item != "保持原排序"),
            "",
        )
        if not reason:
            continue
        blockers.append(f"{_display_name(pick)}: {reason}")
        if len(blockers) >= 3:
            break
    return tuple(blockers)


def _build_cross_market_focus(
    picks: list[PickResult],
    decision_by_symbol: dict[str, PortfolioDecision],
) -> tuple[str, ...]:
    focused: list[str] = []
    for pick in picks:
        summary = format_pick_market_context_summary(pick, compact=True)
        if not summary:
            continue
        decision = decision_by_symbol.get(pick.symbol)
        action = getattr(decision, "action", "keep") if decision is not None else "keep"
        if action == "downgrade":
            continue
        line = f"{_display_name(pick)} | {summary}"
        evidence_stack = _cross_market_evidence_stack_summary(pick)
        if evidence_stack:
            line += f" | {evidence_stack}"
        if line not in focused:
            focused.append(line)
        if len(focused) >= 3:
            break
    return tuple(focused)


def _build_debate_focus(
    picks: list[PickResult],
    decision_by_symbol: dict[str, PortfolioDecision],
) -> tuple[str, ...]:
    focused: list[str] = []
    for pick in picks:
        decision = decision_by_symbol.get(pick.symbol)
        if getattr(decision, "action", "keep") == "downgrade":
            continue
        verdict = str(pick.metrics.get("debate_research_verdict", "") or "").strip()
        if not verdict:
            continue
        line = f"{_display_name(pick)} | {verdict}"
        evidence_stack = _cross_market_evidence_stack_summary(pick)
        if evidence_stack:
            line += f" | 跨市证据 {evidence_stack}"
        history_note = _debate_historical_context_note(pick)
        if history_note:
            line += f" | {history_note}"
        if line not in focused:
            focused.append(line)
        if len(focused) >= 3:
            break
    return tuple(focused)


def _build_debate_risk_gates(
    picks: list[PickResult],
) -> tuple[str, ...]:
    items: list[str] = []
    for pick in picks:
        risk_gate = str(pick.metrics.get("debate_primary_risk_gate", "") or "").strip()
        if not risk_gate:
            continue
        line = f"{_display_name(pick)} | {risk_gate}"
        if line not in items:
            items.append(line)
        if len(items) >= 3:
            break
    return tuple(items)


def _build_debate_support_focus(
    picks: list[PickResult],
    decision_by_symbol: dict[str, PortfolioDecision],
) -> tuple[str, ...]:
    return _build_debate_point_focus(
        picks,
        decision_by_symbol,
        point_resolver=_debate_support_points,
    )


def _build_debate_opposition_focus(
    picks: list[PickResult],
    decision_by_symbol: dict[str, PortfolioDecision],
) -> tuple[str, ...]:
    return _build_debate_point_focus(
        picks,
        decision_by_symbol,
        point_resolver=_debate_opposition_points,
    )


def _build_debate_watch_focus(
    picks: list[PickResult],
    decision_by_symbol: dict[str, PortfolioDecision],
) -> tuple[str, ...]:
    return _build_debate_point_focus(
        picks,
        decision_by_symbol,
        point_resolver=_debate_watch_items,
    )


def _build_debate_point_focus(
    picks: list[PickResult],
    decision_by_symbol: dict[str, PortfolioDecision],
    *,
    point_resolver,
) -> tuple[str, ...]:
    items: list[str] = []
    for pick in picks:
        decision = decision_by_symbol.get(pick.symbol)
        if getattr(decision, "action", "keep") == "downgrade":
            continue
        points = point_resolver(pick)
        if not points:
            continue
        line = f"{_display_name(pick)} | {points[0]}"
        if line not in items:
            items.append(line)
        if len(items) >= 3:
            break
    return tuple(items)


def _build_debate_next_triggers(
    picks: list[PickResult],
    decision_by_symbol: dict[str, PortfolioDecision],
) -> tuple[str, ...]:
    items: list[str] = []
    for pick in picks:
        decision = decision_by_symbol.get(pick.symbol)
        if getattr(decision, "action", "keep") == "downgrade":
            continue
        trigger = str(pick.metrics.get("debate_next_trigger", "") or "").strip()
        if not trigger:
            continue
        line = f"{_display_name(pick)} | {trigger}"
        if line not in items:
            items.append(line)
        if len(items) >= 3:
            break
    return tuple(items)


def _normalize_debate_trigger(trigger: str) -> str:
    clean = str(trigger or "").strip()
    if not clean:
        return ""
    return clean if clean.startswith("先") else f"先看 {clean}"


def _normalize_debate_risk_gate(risk_gate: str) -> str:
    clean = str(risk_gate or "").strip()
    if not clean:
        return ""
    if clean.startswith("失效条件:"):
        return clean
    if clean.startswith("若出现"):
        return clean
    return f"卡点 {clean}"


def _build_debate_priority_queue(
    picks: list[PickResult],
    decision_by_symbol: dict[str, PortfolioDecision],
) -> tuple[str, ...]:
    notes: list[str] = []
    for pick in picks:
        decision = decision_by_symbol.get(pick.symbol)
        action = getattr(decision, "action", "keep")
        if action == "downgrade":
            continue
        verdict = str(pick.metrics.get("debate_research_verdict", "") or "").strip()
        trigger = _normalize_debate_trigger(
            str(pick.metrics.get("debate_next_trigger", "") or "").strip()
        )
        risk_gate = _normalize_debate_risk_gate(
            str(pick.metrics.get("debate_primary_risk_gate", "") or "").strip()
        )
        history_note = _debate_historical_context_note(pick)
        role_reliability = _debate_role_reliability_lines(pick)
        watch_items = _debate_watch_items(pick)
        if not any(
            (verdict, trigger, risk_gate, history_note, role_reliability, watch_items)
        ):
            continue
        parts = [_display_name(pick)]
        if verdict:
            parts.append(verdict)
        if trigger:
            parts.append(trigger)
        if risk_gate:
            parts.append(risk_gate)
        if history_note:
            parts.append(history_note)
        if role_reliability:
            parts.append(f"角色可信度 {role_reliability[0]}")
        if watch_items:
            parts.append(f"待确认 {watch_items[0]}")
        evidence_stack = _cross_market_evidence_stack_summary(pick)
        if evidence_stack:
            parts.append(f"跨市证据 {evidence_stack}")
        notes.append(" | ".join(parts))
    return tuple(notes[:3])


def _cross_market_support_event_count(pick: PickResult) -> int:
    return int(pick.metrics.get("cross_market_support_event_count", 0) or 0)


def _cross_market_conflict_event_count(pick: PickResult) -> int:
    return int(pick.metrics.get("cross_market_conflict_event_count", 0) or 0)


def _cross_market_evidence_stack_summary(pick: PickResult) -> str:
    return str(
        pick.metrics.get("cross_market_evidence_stack_summary", "") or ""
    ).strip()


def _debate_historical_context_note(pick: PickResult) -> str:
    note = str(pick.metrics.get("debate_historical_context_note", "") or "").strip()
    sample_count = int(
        pick.metrics.get("debate_historical_context_sample_count", 0) or 0
    )
    if not note or sample_count <= 0:
        return ""
    return note


def _debate_historical_context_accuracy_score(pick: PickResult) -> int:
    sample_count = int(
        pick.metrics.get("debate_historical_context_sample_count", 0) or 0
    )
    if sample_count < 3:
        return 0
    accuracy = float(pick.metrics.get("debate_historical_context_accuracy", 0.0) or 0.0)
    return int(accuracy * 1000)


def _debate_role_reliability_lines(pick: PickResult) -> tuple[str, ...]:
    return _metric_text_tuple(
        pick,
        "debate_role_reliability_lines",
        "role_reliability_lines",
    )


def _debate_support_points(pick: PickResult) -> tuple[str, ...]:
    return _metric_text_tuple(
        pick,
        "debate_support_points",
        "support_points",
    )


def _debate_opposition_points(pick: PickResult) -> tuple[str, ...]:
    return _metric_text_tuple(
        pick,
        "debate_opposition_points",
        "opposition_points",
    )


def _debate_watch_items(pick: PickResult) -> tuple[str, ...]:
    return _metric_text_tuple(
        pick,
        "debate_watch_items",
        "watch_items",
    )


def _metric_text_tuple(
    pick: PickResult,
    *keys: str,
) -> tuple[str, ...]:
    for key in keys:
        raw_value = pick.metrics.get(key)
        if isinstance(raw_value, str):
            clean = raw_value.strip()
            if clean:
                return (clean,)
            continue
        if isinstance(raw_value, (list, tuple)):
            values = tuple(
                clean for clean in (str(item).strip() for item in raw_value) if clean
            )
            if values:
                return values
    return ()


def _build_cross_market_overview(
    picks: list[PickResult],
    decision_by_symbol: dict[str, PortfolioDecision],
) -> str:
    grouped: dict[str, list[PickResult]] = {}
    for pick in picks:
        metrics = pick.metrics or {}
        theme = str(metrics.get("cross_market_primary_theme", "") or "").strip()
        if not theme:
            continue
        decision = decision_by_symbol.get(pick.symbol)
        if getattr(decision, "action", "keep") == "downgrade":
            continue
        grouped.setdefault(theme, []).append(pick)
    if not grouped:
        return ""

    theme, members = next(iter(grouped.items()))
    names = "、".join(_display_name(member_pick) for member_pick in members[:2])
    return f"{theme}关联证据：{names}"


def _portfolio_sort_key(pick: PickResult) -> tuple[float, float, str]:
    return (-float(pick.score), 0.0, pick.symbol)


def _watchlist_review_details(reason: str) -> tuple[str, str, str]:
    if "T+1" in reason:
        return (
            "明日解除 T+1 后，优先复核开盘承接与流动性",
            "明日开盘前后",
            "high",
        )
    if "板块集中度" in reason or "集中度" in reason:
        return (
            "等待板块暴露回落或出现更强领涨，再考虑转入纸面复核名单",
            "板块分化时",
            "medium",
        )
    if "相关性" in reason or "高相关" in reason:
        return (
            "等待高相关标的分化后，再重新评估纸面复核优先级",
            "分化确认后",
            "medium",
        )
    return (
        "待阻塞条件解除后，再考虑转入纸面复核名单",
        "下一轮信号出现时",
        "low",
    )


def _select_watch_review_reason(reasons: tuple[str, ...]) -> str:
    actionable_keywords = ("T+1", "板块集中度", "集中度", "相关性", "高相关")
    for keyword in actionable_keywords:
        for reason in reasons:
            if keyword in reason:
                return reason
    return next((item for item in reasons if item and item != "保持原排序"), "")


def _build_watch_reviews(
    picks: list[PickResult],
    decision_by_symbol: dict[str, PortfolioDecision],
) -> tuple[WatchlistReviewItem, ...]:
    priority_order = {"high": 0, "medium": 1, "low": 2}
    indexed: list[tuple[int, WatchlistReviewItem]] = []
    for index, pick in enumerate(picks):
        decision = decision_by_symbol.get(pick.symbol)
        if decision is None or decision.action != "downgrade":
            continue
        reason = _select_watch_review_reason(tuple(decision.reasons))
        if not reason:
            continue
        next_step, review_window, priority = _watchlist_review_details(reason)
        indexed.append(
            (
                index,
                WatchlistReviewItem(
                    symbol=pick.symbol,
                    name=pick.name,
                    blocker=reason,
                    next_step=next_step,
                    review_window=review_window,
                    priority=priority,
                ),
            )
        )
    indexed.sort(
        key=lambda item: (
            priority_order.get(item[1].priority, 99),
            item[0],
        )
    )
    return tuple(item for _, item in indexed[:3])


def summarize_portfolio_decisions(
    picks: list[PickResult],
    decisions: list[PortfolioDecision],
    *,
    regime: str = "",
    concentration: ConcentrationResult | None = None,
    correlation_result: CorrelationResult | None = None,
    thresholds: Thresholds | None = None,
) -> PortfolioDecisionSummary:
    current_thresholds = thresholds or load_thresholds()
    promote_count = sum(1 for item in decisions if item.action == "promote")
    downgrade_count = sum(1 for item in decisions if item.action == "downgrade")
    keep_count = sum(1 for item in decisions if item.action == "keep")
    top_focus = tuple(
        _display_name(pick) for pick in picks if pick.rating == "strong_buy_candidate"
    )[:3]
    if not top_focus:
        top_focus = tuple(
            _display_name(pick) for pick in picks if is_tradable_rating(pick.rating)
        )[:3]
    watchlist = tuple(
        _display_name(pick)
        for pick in picks
        if pick.metrics.get("portfolio_action") == "downgrade" or pick.rating == "watch"
    )[:5]
    decision_by_symbol = {item.symbol: item for item in decisions}
    optimization = optimize_portfolio_allocations_from_risk(
        picks,
        decision_by_symbol,
        risk=current_thresholds.risk,
        concentration=concentration,
        correlation_result=correlation_result,
        regime=regime,
    )
    portfolio_risk = summarize_portfolio_risk(
        optimization.allocations,
        cash_reserve=optimization.cash_reserve,
        concentration=concentration,
        correlation_result=correlation_result,
    )
    (
        regime_label,
        strategy_mix_name,
        strategy_mix_description,
        strategy_focus,
        strategy_weights,
    ) = _build_strategy_mix_summary(regime, thresholds=current_thresholds)
    return PortfolioDecisionSummary(
        promote_count=promote_count,
        downgrade_count=downgrade_count,
        keep_count=keep_count,
        top_focus=top_focus,
        watchlist=watchlist,
        cross_market_overview=_build_cross_market_overview(picks, decision_by_symbol),
        cross_market_focus=_build_cross_market_focus(picks, decision_by_symbol),
        debate_focus=_build_debate_focus(picks, decision_by_symbol),
        debate_support_points=_build_debate_support_focus(picks, decision_by_symbol),
        debate_opposition_points=_build_debate_opposition_focus(
            picks, decision_by_symbol
        ),
        debate_watch_items=_build_debate_watch_focus(picks, decision_by_symbol),
        debate_risk_gates=_build_debate_risk_gates(picks),
        debate_next_triggers=_build_debate_next_triggers(picks, decision_by_symbol),
        debate_priority_queue=_build_debate_priority_queue(picks, decision_by_symbol),
        action_hotspots=_summarize_action_hotspots(decisions),
        execution_blockers=_build_execution_blockers(picks, decision_by_symbol),
        watch_reviews=_build_watch_reviews(picks, decision_by_symbol),
        portfolio_risk_lines=portfolio_risk.lines,
        allocations=optimization.allocations,
        cash_reserve=optimization.cash_reserve,
        allocation_note=optimization.note,
        regime_label=regime_label,
        strategy_mix_name=strategy_mix_name,
        strategy_mix_description=strategy_mix_description,
        strategy_focus=strategy_focus,
        strategy_weights=strategy_weights,
    )


def apply_portfolio_manager(
    picks: list[PickResult],
    *,
    regime: str = "",
    concentration: ConcentrationResult | None = None,
    correlation_result: CorrelationResult | None = None,
    sector_map: dict[str, str] | None = None,
    industry_map: dict[str, str] | None = None,
    thresholds: Thresholds | None = None,
) -> PortfolioDecisionBundle:
    current_thresholds = thresholds or load_thresholds()
    sector_map = sector_map or {}
    industry_map = industry_map or {}
    decisions: list[PortfolioDecision] = []
    updated: list[PickResult] = []
    # 当前主链没有持仓快照输入，止损/T+1 只能由上游显式过滤后再进入 PM。
    # 在没有真实持仓上下文前，不在这里做假集成，避免输出看似已接管但实际未生效的裁决。

    concentrated_sector = ""
    if concentration and concentration.is_concentrated and concentration.sectors:
        concentrated_sector = concentration.sectors[0].sector

    high_corr_symbols: set[str] = set()
    if correlation_result:
        # 高相关对里降级「评分较低」的那只，保留较优的。
        # 注意：correlation.high_corr_pairs 的 (left,right) 是按股票代码字典序排的，
        # 与评分无关。若直接降 right 会变成「按代码大小降级」，可能误降高分票。
        score_by_symbol = {p.symbol: p.score for p in picks}
        for left, right, corr in correlation_result.high_corr_pairs:
            if corr >= current_thresholds.risk.max_correlation:
                left_score = score_by_symbol.get(left, 0.0)
                right_score = score_by_symbol.get(right, 0.0)
                # 降级评分较低的一只（相等时降 right，保持确定性）
                weaker = left if left_score < right_score else right
                high_corr_symbols.add(weaker)

    for pick in picks:
        reasons: list[str] = []
        action = "keep"
        observation_only = bool(pick.metrics.get("observation_only", False))
        paper_review_eligible = pick.metrics.get("paper_review_eligible", True)
        if observation_only or paper_review_eligible is False:
            action = "observation_only"
            reasons.append("质量门/组合保护仅允许观察，不进入纸面复核")

        if pick.recommended_adjustment == "lower":
            reasons.append("多 Agent 委员会偏谨慎，仅作为复核提示")
        elif pick.recommended_adjustment == "raise":
            reasons.append("多 Agent 委员会支持，仅作为复核提示")

        if (
            concentrated_sector
            and get_sector(
                pick.symbol,
                sector_hint=sector_map.get(pick.symbol, ""),
                industry_hint=industry_map.get(pick.symbol, ""),
            )
            == concentrated_sector
        ):
            reasons.append(f"板块集中度过高，限制{concentrated_sector}暴露")
            if not observation_only:
                action = "downgrade"

        if pick.symbol in high_corr_symbols:
            reasons.append("与前序候选高相关，限制组合拥挤风险")
            if not observation_only:
                action = "downgrade"

        if str(pick.metrics.get("cross_market_primary_theme", "") or "").strip():
            reasons.append("跨市证据仅进入复核，不改写候选评分")

        # The runtime scorer owns score, rating, position, and candidate order.
        # Portfolio management only labels constraints for later allocation/review.
        context_priority_score = round(float(pick.score), 2)

        updated.append(
            PickResult(
                symbol=pick.symbol,
                name=pick.name,
                date=pick.date,
                close=pick.close,
                score=round(float(pick.score), 2),
                rating=pick.rating,
                entry_type=pick.entry_type,
                ideal_buy=pick.ideal_buy,
                stop_loss=pick.stop_loss,
                take_profit=pick.take_profit,
                position=pick.position,
                strategies=pick.strategies,
                reasons=pick.reasons,
                risks=pick.risks,
                metrics={
                    **pick.metrics,
                    "portfolio_action": action,
                    "context_priority_score": context_priority_score,
                    "context_priority_delta": 0.0,
                    "context_priority_reason": "",
                },
                adjusted_score=pick.adjusted_score,
                recommended_adjustment=pick.recommended_adjustment,
                debate_consensus=pick.debate_consensus,
                confidence=pick.confidence,
                regime_score=pick.regime_score,
            )
        )
        decisions.append(
            PortfolioDecision(
                symbol=pick.symbol,
                action=action,
                score_delta=0.0,
                priority_delta=0.0,
                reasons=tuple(reasons) if reasons else ("保持原排序",),
            )
        )
    updated.sort(key=_portfolio_sort_key)
    return PortfolioDecisionBundle(
        picks=updated,
        decisions=tuple(decisions),
        summary=summarize_portfolio_decisions(
            updated,
            decisions,
            regime=regime,
            concentration=concentration,
            correlation_result=correlation_result,
            thresholds=current_thresholds,
        ),
    )
