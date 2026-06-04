from __future__ import annotations

from dataclasses import dataclass

from aqsp.core.types import PickResult
from aqsp.presentation import format_symbol_name
from aqsp.portfolio.correlation import CorrelationResult
from aqsp.portfolio.optimizer import (
    PortfolioAllocation,
    optimize_portfolio_allocations,
)
from aqsp.portfolio.sector_check import ConcentrationResult, get_sector
from aqsp.ratings import is_tradable_rating
from aqsp.strategies.adaptive_evolution import StrategyMixAdaptor


@dataclass(frozen=True)
class PortfolioDecision:
    symbol: str
    action: str
    score_delta: float
    reasons: tuple[str, ...]


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


_REGIME_LABELS = {
    "stable_bull": "稳定上涨",
    "volatile_bull": "波动上涨",
    "stable_bear": "稳定下跌",
    "volatile_bear": "波动下跌",
    "stable_sideways": "稳定震荡",
    "volatile_sideways": "波动震荡",
}

_STRATEGY_LABELS = {
    "momentum": "动量趋势",
    "limit_up_ladder": "涨停接力",
    "morning_breakout": "早盘突破",
    "sector_rotation": "板块轮动",
    "triple_rise": "三连阳",
    "intraday_trade": "日内快反",
    "quality": "质量稳健",
    "value": "价值低估",
    "mean_reversion": "均值回归",
}


def _resolve_regime_label(regime: str) -> str:
    return _REGIME_LABELS.get(regime, regime or "")


def _resolve_strategy_label(strategy_id: str) -> str:
    return _STRATEGY_LABELS.get(strategy_id, strategy_id)


def _build_strategy_mix_summary(
    regime: str,
) -> tuple[str, str, str, tuple[str, ...], tuple[tuple[str, float], ...]]:
    if not regime:
        return "", "", "", (), ()
    mix = StrategyMixAdaptor().select_mix(regime)
    weights = tuple(
        (strategy_id, float(weight))
        for strategy_id, weight in mix.weights.items()
    )
    focus = tuple(_resolve_strategy_label(item) for item in mix.enabled_strategies)
    return (
        _resolve_regime_label(regime),
        mix.name,
        mix.description,
        focus,
        weights,
    )


def summarize_portfolio_decisions(
    picks: list[PickResult],
    decisions: list[PortfolioDecision],
    *,
    regime: str = "",
    concentration: ConcentrationResult | None = None,
    correlation_result: CorrelationResult | None = None,
) -> PortfolioDecisionSummary:
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
    optimization = optimize_portfolio_allocations(
        picks,
        decision_by_symbol,
        concentration=concentration,
        correlation_result=correlation_result,
    )
    (
        regime_label,
        strategy_mix_name,
        strategy_mix_description,
        strategy_focus,
        strategy_weights,
    ) = _build_strategy_mix_summary(regime)
    return PortfolioDecisionSummary(
        promote_count=promote_count,
        downgrade_count=downgrade_count,
        keep_count=keep_count,
        top_focus=top_focus,
        watchlist=watchlist,
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
) -> PortfolioDecisionBundle:
    decisions: list[PortfolioDecision] = []
    updated: list[PickResult] = []

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
            if corr >= 0.7:
                left_score = score_by_symbol.get(left, 0.0)
                right_score = score_by_symbol.get(right, 0.0)
                # 降级评分较低的一只（相等时降 right，保持确定性）
                weaker = left if left_score < right_score else right
                high_corr_symbols.add(weaker)

    for pick in picks:
        delta = 0.0
        reasons: list[str] = []
        negative_adjustment = False
        positive_adjustment = False

        if pick.recommended_adjustment == "lower":
            delta -= max(abs(pick.score - pick.adjusted_score), 2.0)
            reasons.append("多Agent辩论偏谨慎，降低优先级")
            negative_adjustment = True
        elif pick.recommended_adjustment == "raise":
            delta += max(abs(pick.adjusted_score - pick.score), 2.0)
            reasons.append("多Agent辩论支持上调优先级")
            positive_adjustment = True

        if concentrated_sector and get_sector(pick.symbol) == concentrated_sector:
            delta -= 4.0
            reasons.append(f"板块集中度过高，压低{concentrated_sector}暴露")
            negative_adjustment = True

        if pick.symbol in high_corr_symbols:
            delta -= 3.0
            reasons.append("与前序候选高相关，降低组合拥挤风险")
            negative_adjustment = True

        final_score = pick.score + delta
        final_rating = pick.rating
        final_position = pick.position
        action = "keep"

        if negative_adjustment:
            action = "downgrade"
        elif positive_adjustment and delta > 0:
            action = "promote"

        if negative_adjustment and (final_score < 20 or delta <= -6):
            final_rating = "avoid"
            final_position = "watch"
        elif positive_adjustment and delta >= 3 and pick.rating == "buy_candidate":
            final_rating = "strong_buy_candidate"

        updated.append(
            PickResult(
                symbol=pick.symbol,
                name=pick.name,
                date=pick.date,
                close=pick.close,
                score=round(final_score, 2),
                rating=final_rating,
                entry_type=pick.entry_type,
                ideal_buy=pick.ideal_buy,
                stop_loss=pick.stop_loss,
                take_profit=pick.take_profit,
                position=final_position,
                strategies=pick.strategies,
                reasons=pick.reasons,
                risks=pick.risks,
                metrics={**pick.metrics, "portfolio_action": action},
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
                score_delta=round(delta, 2),
                reasons=tuple(reasons) if reasons else ("保持原排序",),
            )
        )

    updated.sort(key=lambda p: p.score, reverse=True)
    return PortfolioDecisionBundle(
        picks=updated,
        decisions=tuple(decisions),
        summary=summarize_portfolio_decisions(
            updated,
            decisions,
            regime=regime,
            concentration=concentration,
            correlation_result=correlation_result,
        ),
    )
