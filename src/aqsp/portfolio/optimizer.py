from __future__ import annotations

from dataclasses import dataclass

from aqsp.core.types import PickResult
from aqsp.portfolio.correlation import CorrelationResult
from aqsp.portfolio.sector_check import ConcentrationResult
from aqsp.ratings import is_tradable_rating


@dataclass(frozen=True)
class PortfolioAllocation:
    symbol: str
    name: str
    weight: float
    rationale: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioOptimizationResult:
    allocations: tuple[PortfolioAllocation, ...]
    cash_reserve: float
    note: str

    @property
    def invested_ratio(self) -> float:
        return round(1.0 - self.cash_reserve, 4)


def optimize_portfolio_allocations(
    picks: list[PickResult],
    decision_by_symbol: dict[str, object],
    *,
    concentration: ConcentrationResult | None = None,
    correlation_result: CorrelationResult | None = None,
    max_names: int = 5,
    max_single_weight: float = 0.20,
) -> PortfolioOptimizationResult:
    tradable = [
        pick
        for pick in picks
        if is_tradable_rating(pick.rating)
        and getattr(decision_by_symbol.get(pick.symbol), "action", "keep")
        != "downgrade"
    ][:max_names]
    if not tradable:
        return PortfolioOptimizationResult(
            allocations=(),
            cash_reserve=1.0,
            note="今日无可执行主链，建议保留现金等待下一轮信号。",
        )

    target_invested = _target_invested_ratio(
        tradable,
        decision_by_symbol=decision_by_symbol,
        concentration=concentration,
        correlation_result=correlation_result,
    )

    raw_scores: dict[str, float] = {}
    rationales: dict[str, tuple[str, ...]] = {}
    for pick in tradable:
        raw_scores[pick.symbol] = _raw_weight_score(pick, decision_by_symbol)
        rationales[pick.symbol] = _build_rationale(pick, decision_by_symbol)

    capped_weights = _cap_weights(
        raw_scores,
        total_target=target_invested,
        max_single_weight=max_single_weight,
    )
    allocations = tuple(
        PortfolioAllocation(
            symbol=pick.symbol,
            name=pick.name,
            weight=round(capped_weights.get(pick.symbol, 0.0), 4),
            rationale=rationales[pick.symbol],
        )
        for pick in tradable
        if capped_weights.get(pick.symbol, 0.0) > 0
    )
    invested_ratio = sum(item.weight for item in allocations)
    cash_reserve = round(max(0.0, 1.0 - invested_ratio), 4)
    note = _build_note(
        allocations=allocations,
        cash_reserve=cash_reserve,
        concentration=concentration,
        correlation_result=correlation_result,
        max_single_weight=max_single_weight,
    )
    return PortfolioOptimizationResult(
        allocations=allocations,
        cash_reserve=cash_reserve,
        note=note,
    )


def _target_invested_ratio(
    tradable: list[PickResult],
    *,
    decision_by_symbol: dict[str, object],
    concentration: ConcentrationResult | None,
    correlation_result: CorrelationResult | None,
) -> float:
    top_score = tradable[0].score if tradable else 0.0
    strong_count = sum(1 for pick in tradable if pick.rating == "strong_buy_candidate")
    promote_count = sum(
        1
        for pick in tradable
        if getattr(decision_by_symbol.get(pick.symbol), "action", "keep") == "promote"
    )
    downgrade_count = sum(
        1
        for pick in tradable
        if getattr(decision_by_symbol.get(pick.symbol), "action", "keep")
        == "downgrade"
    )

    if top_score >= 75:
        ratio = 0.80
    elif top_score >= 65:
        ratio = 0.72
    elif top_score >= 55:
        ratio = 0.62
    else:
        ratio = 0.50

    if strong_count == 0:
        ratio -= 0.05
    if promote_count >= 2 and strong_count >= 1:
        ratio += 0.05
    if downgrade_count > promote_count:
        ratio -= 0.05
    if concentration is not None and concentration.is_concentrated:
        ratio -= 0.05
    if correlation_result is not None and correlation_result.avg_correlation >= 0.55:
        ratio -= 0.05

    return max(0.35, min(0.85, round(ratio, 4)))


def _raw_weight_score(pick: PickResult, decision_by_symbol: dict[str, object]) -> float:
    decision = decision_by_symbol.get(pick.symbol)
    action = getattr(decision, "action", "keep") if decision is not None else "keep"
    reasons = tuple(getattr(decision, "reasons", ()) or ())

    multiplier = 1.0
    if pick.rating == "strong_buy_candidate":
        multiplier *= 1.15
    if action == "promote":
        multiplier *= 1.10
    elif action == "downgrade":
        multiplier *= 0.75
    if any("高相关" in reason for reason in reasons):
        multiplier *= 0.88
    if any("板块集中度" in reason for reason in reasons):
        multiplier *= 0.92

    return max(1.0, pick.score + 1.0) * multiplier


def _build_rationale(
    pick: PickResult,
    decision_by_symbol: dict[str, object],
) -> tuple[str, ...]:
    decision = decision_by_symbol.get(pick.symbol)
    reasons: list[str] = [f"主链评分 {pick.score:.1f}"]
    if pick.rating == "strong_buy_candidate":
        reasons.append("强信号优先分配")
    if decision is not None:
        action = getattr(decision, "action", "keep")
        if action == "promote":
            reasons.append("PM 上调优先级")
        elif action == "downgrade":
            reasons.append("PM 降级后仅保留跟踪仓")
        for reason in tuple(getattr(decision, "reasons", ()) or ()):
            if "高相关" in reason:
                reasons.append("相关性约束压缩权重")
                break
        for reason in tuple(getattr(decision, "reasons", ()) or ()):
            if "板块集中度" in reason:
                reasons.append("板块暴露约束压缩权重")
                break
    return tuple(reasons)


def _cap_weights(
    raw_scores: dict[str, float],
    *,
    total_target: float,
    max_single_weight: float,
) -> dict[str, float]:
    if not raw_scores:
        return {}

    effective_target = min(total_target, max_single_weight * len(raw_scores))
    remaining_target = effective_target
    remaining = dict(raw_scores)
    fixed: dict[str, float] = {}

    while remaining and remaining_target > 0:
        total_raw = sum(remaining.values())
        if total_raw <= 0:
            break
        provisional = {
            symbol: remaining_target * score / total_raw
            for symbol, score in remaining.items()
        }
        over_cap = {
            symbol: weight
            for symbol, weight in provisional.items()
            if weight > max_single_weight + 1e-9
        }
        if not over_cap:
            fixed.update(provisional)
            break
        for symbol in over_cap:
            fixed[symbol] = max_single_weight
            remaining_target -= max_single_weight
            remaining.pop(symbol, None)

    return {symbol: round(weight, 6) for symbol, weight in fixed.items()}


def _build_note(
    *,
    allocations: tuple[PortfolioAllocation, ...],
    cash_reserve: float,
    concentration: ConcentrationResult | None,
    correlation_result: CorrelationResult | None,
    max_single_weight: float,
) -> str:
    reasons = [f"单票上限 {max_single_weight:.0%}"]
    if concentration is not None and concentration.is_concentrated:
        reasons.append("板块集中度偏高")
    if correlation_result is not None and correlation_result.high_corr_pairs:
        reasons.append("候选相关性需要压缩")
    if cash_reserve >= 0.40:
        reasons.append("信号强度不足时提高现金留存")
    if not allocations:
        reasons.append("今日不建议建立主仓")
    return "；".join(reasons)
