from __future__ import annotations

from dataclasses import dataclass

from aqsp.core.types import PickResult
from aqsp.portfolio.correlation import CorrelationResult
from aqsp.portfolio.sector_check import ConcentrationResult, get_sector


@dataclass(frozen=True)
class PortfolioDecision:
    symbol: str
    action: str
    score_delta: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioDecisionBundle:
    picks: list[PickResult]
    decisions: tuple[PortfolioDecision, ...]


def apply_portfolio_manager(
    picks: list[PickResult],
    *,
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
        for left, right, corr in correlation_result.high_corr_pairs:
            if corr >= 0.7:
                high_corr_symbols.add(right)

    for pick in picks:
        delta = 0.0
        reasons: list[str] = []

        if pick.recommended_adjustment == "lower":
            delta -= max(abs(pick.score - pick.adjusted_score), 2.0)
            reasons.append("多Agent辩论偏谨慎，降低优先级")
        elif pick.recommended_adjustment == "raise":
            delta += max(abs(pick.adjusted_score - pick.score), 2.0)
            reasons.append("多Agent辩论支持上调优先级")

        if concentrated_sector and get_sector(pick.symbol) == concentrated_sector:
            delta -= 4.0
            reasons.append(f"板块集中度过高，压低{concentrated_sector}暴露")

        if pick.symbol in high_corr_symbols:
            delta -= 3.0
            reasons.append("与前序候选高相关，降低组合拥挤风险")

        final_score = pick.score + delta
        final_rating = pick.rating
        final_position = pick.position
        action = "keep"

        if final_score < 20 or delta <= -6:
            final_rating = "avoid"
            final_position = "watch"
            action = "downgrade"
        elif delta >= 3 and pick.rating == "buy_candidate":
            final_rating = "strong_buy_candidate"
            action = "promote"

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
    return PortfolioDecisionBundle(picks=updated, decisions=tuple(decisions))
