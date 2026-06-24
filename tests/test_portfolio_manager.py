from __future__ import annotations

from aqsp.core.types import PickResult
from aqsp.portfolio.correlation import CorrelationResult
from aqsp.portfolio.manager import apply_portfolio_manager
from aqsp.portfolio.optimizer import (
    _cap_weights,
    optimize_portfolio_allocations_from_risk,
)
from aqsp.portfolio.sector_check import ConcentrationResult, SectorConcentration
from aqsp.strategies.thresholds import RiskThresholds, Thresholds


def _pick(
    symbol: str, score: float, *, recommended_adjustment: str = "keep"
) -> PickResult:
    return PickResult(
        symbol=symbol,
        name=symbol,
        date="2026-06-02",
        close=10.0,
        score=score,
        rating="buy_candidate",
        entry_type="close",
        ideal_buy=10.0,
        stop_loss=9.5,
        take_profit=11.0,
        position="10%-30%",
        recommended_adjustment=recommended_adjustment,
        adjusted_score=score - 3 if recommended_adjustment == "lower" else score + 3,
    )


def test_apply_portfolio_manager_downgrades_when_debate_and_correlation_stack() -> None:
    picks = [
        _pick("600036", 42, recommended_adjustment="lower"),
        _pick("000001", 38),
    ]
    concentration = ConcentrationResult(
        total_candidates=2,
        sector_count=1,
        max_concentration=1.0,
        warnings=("too concentrated",),
        sectors=(
            SectorConcentration(
                sector="银行",
                count=2,
                total=2,
                ratio=1.0,
                symbols=("600036", "000001"),
            ),
        ),
    )
    correlation = CorrelationResult(
        matrix={
            "600036": {"600036": 1.0, "000001": 0.82},
            "000001": {"600036": 0.82, "000001": 1.0},
        },
        high_corr_pairs=[("000001", "600036", 0.82)],
        avg_correlation=0.82,
    )

    bundle = apply_portfolio_manager(
        picks,
        concentration=concentration,
        correlation_result=correlation,
    )

    by_symbol = {item.symbol: item for item in bundle.picks}
    decisions = {item.symbol: item for item in bundle.decisions}

    assert by_symbol["600036"].rating == "avoid"
    assert decisions["600036"].action == "downgrade"
    assert any("多Agent辩论偏谨慎" in reason for reason in decisions["600036"].reasons)
    assert bundle.summary.downgrade_count == 2
    assert bundle.summary.promote_count == 0
    assert bundle.summary.headline == "上调 0 / 降级 2 / 维持 0"
    assert any("600036" in item for item in bundle.summary.watchlist)
    assert any("板块集中度过高" in item for item in bundle.summary.action_hotspots)
    assert any("600036" in item for item in bundle.summary.execution_blockers)
    assert bundle.summary.allocations == ()
    assert bundle.summary.cash_reserve == 1.0
    assert "保留现金" in bundle.summary.allocation_note


def test_apply_portfolio_manager_promotes_when_debate_supports_buy_candidate() -> None:
    picks = [_pick("300750", 66, recommended_adjustment="raise")]

    bundle = apply_portfolio_manager(picks, regime="stable_bull")

    assert bundle.picks[0].rating == "strong_buy_candidate"
    assert bundle.decisions[0].action == "promote"
    assert bundle.summary.promote_count == 1
    assert bundle.summary.top_focus == ("300750",)
    assert bundle.summary.allocations[0].symbol == "300750"
    assert 0 < bundle.summary.allocations[0].weight <= 0.30
    assert bundle.summary.regime_label == "稳定上涨"
    assert bundle.summary.strategy_mix_name == "配置化策略权重"
    assert "动量趋势" in bundle.summary.strategy_focus
    assert any(
        strategy_id == "momentum" and weight == 1.2
        for strategy_id, weight in bundle.summary.strategy_weights
    )


def test_apply_portfolio_manager_uses_configured_correlation_threshold() -> None:
    picks = [_pick("600036", 70), _pick("000001", 60)]
    correlation = CorrelationResult(
        matrix={
            "600036": {"600036": 1.0, "000001": 0.82},
            "000001": {"600036": 0.82, "000001": 1.0},
        },
        high_corr_pairs=[("000001", "600036", 0.82)],
        avg_correlation=0.82,
    )

    loose = apply_portfolio_manager(
        picks,
        correlation_result=correlation,
        thresholds=Thresholds(risk=RiskThresholds(max_correlation=0.9)),
    )
    strict = apply_portfolio_manager(
        picks,
        correlation_result=correlation,
        thresholds=Thresholds(risk=RiskThresholds(max_correlation=0.7)),
    )

    assert {item.action for item in loose.decisions} == {"keep"}
    assert any(item.action == "downgrade" for item in strict.decisions)


def test_apply_portfolio_manager_uses_configured_allocation_limits() -> None:
    picks = [_pick("300750", 85, recommended_adjustment="raise")]

    bundle = apply_portfolio_manager(
        picks,
        thresholds=Thresholds(
            risk=RiskThresholds(
                max_positions=1,
                max_single_position_pct=0.12,
                min_cash_reserve=0.50,
            )
        ),
    )

    assert bundle.summary.allocations[0].weight <= 0.12
    assert bundle.summary.cash_reserve >= 0.50


def test_portfolio_optimizer_uses_configured_target_curve() -> None:
    picks = [_pick("300750", 85)]

    conservative = optimize_portfolio_allocations_from_risk(
        picks,
        {},
        risk=RiskThresholds(
            max_positions=1,
            max_single_position_pct=1.0,
            min_cash_reserve=0.0,
            allocation_score_strong=80.0,
            allocation_invested_strong=0.40,
            allocation_adjustment_step=0.0,
            allocation_floor_pct=0.10,
        ),
    )
    assert conservative.invested_ratio == 0.40


def test_portfolio_optimizer_uses_configured_weight_multipliers() -> None:
    strong = _pick("300750", 80)
    strong = PickResult(**{**strong.__dict__, "rating": "strong_buy_candidate"})
    normal = _pick("000001", 80)

    boosted = optimize_portfolio_allocations_from_risk(
        [strong, normal],
        {},
        risk=RiskThresholds(
            max_positions=2,
            max_single_position_pct=1.0,
            min_cash_reserve=0.0,
            allocation_invested_strong=0.80,
            allocation_adjustment_step=0.0,
            allocation_floor_pct=0.10,
            allocation_strong_multiplier=2.0,
        ),
    )

    weights = {item.symbol: item.weight for item in boosted.allocations}
    assert weights["300750"] > weights["000001"]


def test_apply_portfolio_manager_excludes_downgraded_tradable_from_allocations() -> (
    None
):
    picks = [_pick("000001", 85)]
    concentration = ConcentrationResult(
        total_candidates=1,
        sector_count=1,
        max_concentration=1.0,
        warnings=("too concentrated",),
        sectors=(
            SectorConcentration(
                sector="银行",
                count=1,
                total=1,
                ratio=1.0,
                symbols=("000001",),
            ),
        ),
    )

    bundle = apply_portfolio_manager(picks, concentration=concentration)

    assert bundle.picks[0].rating == "buy_candidate"
    assert bundle.decisions[0].action == "downgrade"
    assert bundle.summary.allocations == ()
    assert bundle.summary.cash_reserve == 1.0
    assert "无可执行主链" in bundle.summary.allocation_note


def test_apply_portfolio_manager_keeps_action_when_no_incremental_override() -> None:
    picks = [_pick("300750", 18, recommended_adjustment="keep")]

    bundle = apply_portfolio_manager(picks)

    assert bundle.picks[0].score == 18
    assert bundle.picks[0].rating == "buy_candidate"
    assert bundle.decisions[0].action == "keep"
    assert bundle.decisions[0].reasons == ("保持原排序",)
    assert bundle.summary.keep_count == 1
    assert bundle.summary.top_focus == ("300750",)
    assert bundle.summary.cash_reserve >= 0.15


def test_apply_portfolio_manager_uses_runtime_sector_map_for_concentration() -> None:
    picks = [
        _pick("600036", 42),
        _pick("000021", 40),
    ]
    concentration = ConcentrationResult(
        total_candidates=2,
        sector_count=1,
        max_concentration=1.0,
        warnings=("too concentrated",),
        sectors=(
            SectorConcentration(
                sector="银行",
                count=2,
                total=2,
                ratio=1.0,
                symbols=("600036", "000021"),
            ),
        ),
    )

    bundle = apply_portfolio_manager(
        picks,
        concentration=concentration,
        sector_map={"000021": "银行"},
    )

    decisions = {item.symbol: item for item in bundle.decisions}
    assert decisions["000021"].action == "downgrade"
    assert any("银行暴露" in reason for reason in decisions["000021"].reasons)


def test_apply_portfolio_manager_builds_watch_reviews_with_priority_and_window() -> (
    None
):
    picks = [
        _pick("600036", 42, recommended_adjustment="lower"),
        _pick("000001", 38),
    ]
    concentration = ConcentrationResult(
        total_candidates=2,
        sector_count=1,
        max_concentration=1.0,
        warnings=("too concentrated",),
        sectors=(
            SectorConcentration(
                sector="银行",
                count=2,
                total=2,
                ratio=1.0,
                symbols=("600036", "000001"),
            ),
        ),
    )
    correlation = CorrelationResult(
        matrix={
            "600036": {"600036": 1.0, "000001": 0.82},
            "000001": {"600036": 0.82, "000001": 1.0},
        },
        high_corr_pairs=[("000001", "600036", 0.82)],
        avg_correlation=0.82,
    )

    bundle = apply_portfolio_manager(
        picks,
        concentration=concentration,
        correlation_result=correlation,
    )

    assert bundle.summary.watch_reviews
    lead_review = bundle.summary.watch_reviews[0]
    assert lead_review.symbol == "600036"
    assert lead_review.priority == "medium"
    assert lead_review.review_window == "板块分化时"
    assert "等待板块暴露回落" in lead_review.next_step


def test_cap_weights_respects_total_target_when_under_name_cap() -> None:
    weights = _cap_weights(
        {"300750": 1.2, "600900": 1.0, "000651": 0.8},
        total_target=0.62,
        max_single_weight=0.30,
    )

    assert round(sum(weights.values()), 6) == 0.62
    assert all(weight <= 0.30 for weight in weights.values())
