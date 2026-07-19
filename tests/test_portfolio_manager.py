from __future__ import annotations

import pandas as pd

from aqsp.core.types import PickResult
from aqsp.portfolio.correlation import CorrelationResult
from aqsp.portfolio.manager import apply_portfolio_manager
from aqsp.portfolio.optimizer import (
    _cap_weights,
    optimize_portfolio_allocations_from_risk,
)
from aqsp.portfolio.diversification import (
    DiversificationEngine,
    PortfolioConstraint,
)
from aqsp.portfolio.sector_check import ConcentrationResult, SectorConcentration
from aqsp.strategies.thresholds import RiskThresholds, Thresholds


def _pick(
    symbol: str,
    score: float,
    *,
    recommended_adjustment: str = "keep",
    metrics: dict[str, object] | None = None,
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
        metrics=metrics or {},
    )


def test_apply_portfolio_manager_keeps_score_when_debate_override_disabled() -> None:
    picks = [_pick("600036", 42, recommended_adjustment="lower")]

    bundle = apply_portfolio_manager(picks)

    assert bundle.picks[0].score == 42
    assert bundle.picks[0].rating == "buy_candidate"
    assert bundle.decisions[0].action == "keep"
    assert bundle.decisions[0].score_delta == 0.0
    assert any("仅作为复核提示" in reason for reason in bundle.decisions[0].reasons)


def test_apply_portfolio_manager_keeps_deterministic_order_when_debate_raises() -> None:
    picks = [
        _pick("300750", 66, recommended_adjustment="raise"),
        _pick("600036", 68),
    ]

    bundle = apply_portfolio_manager(picks)
    decisions = {item.symbol: item for item in bundle.decisions}

    assert [pick.symbol for pick in bundle.picks] == ["600036", "300750"]
    assert bundle.picks[1].score == 66
    assert bundle.picks[0].rating == "buy_candidate"
    assert decisions["300750"].action == "keep"
    assert decisions["300750"].score_delta == 0.0
    assert decisions["300750"].priority_delta == 0.0
    assert any("仅作为复核提示" in reason for reason in decisions["300750"].reasons)


def test_apply_portfolio_manager_uses_risk_controls_not_debate_override(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AQSP_GOAL_SWITCH_MULTI_AGENT_RUNTIME_OVERRIDE", "true")
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
        sector_map={"600036": "银行", "000001": "银行"},
    )

    by_symbol = {item.symbol: item for item in bundle.picks}
    decisions = {item.symbol: item for item in bundle.decisions}

    assert by_symbol["600036"].rating == "buy_candidate"
    assert decisions["600036"].action == "downgrade"
    assert by_symbol["600036"].score == 42.0
    assert any("板块集中度过高" in reason for reason in decisions["600036"].reasons)
    assert any("仅作为复核提示" in reason for reason in decisions["600036"].reasons)
    assert bundle.summary.downgrade_count == 2
    assert bundle.summary.promote_count == 0
    assert bundle.summary.headline == "上调 0 / 降级 2 / 维持 0"
    assert any("600036" in item for item in bundle.summary.watchlist)
    assert any("板块集中度过高" in item for item in bundle.summary.action_hotspots)
    assert any("600036" in item for item in bundle.summary.execution_blockers)
    assert bundle.summary.allocations == ()
    assert bundle.summary.cash_reserve == 1.0
    assert "保留现金" in bundle.summary.allocation_note
    assert any("组合集中度 HHI" in line for line in bundle.summary.portfolio_risk_lines)
    assert any("高相关候选对" in line for line in bundle.summary.portfolio_risk_lines)


def test_apply_portfolio_manager_does_not_promote_when_debate_supports_buy_candidate(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AQSP_GOAL_SWITCH_MULTI_AGENT_RUNTIME_OVERRIDE", "true")
    picks = [
        _pick(
            "300750",
            66,
            recommended_adjustment="raise",
            metrics={
                "debate_research_verdict": "倾向优先纸面复核，主因 技术面强势",
                "debate_primary_risk_gate": "追高回撤风险",
                "debate_next_trigger": "先确认开盘承接与量价延续",
                "debate_historical_context_note": "历史校验: 强证据 2/3 (67%)；冲突主导 1/3",
                "debate_historical_context_sample_count": 3,
                "debate_historical_context_accuracy": 2 / 3,
                "role_reliability_lines": ("跨市场: 近21天 7/10 (70%)｜当前权重 0.18",),
                "support_points": ("技术面强势且量价共振。",),
                "opposition_points": ("若高开过猛，追高回撤风险会放大。",),
                "watch_items": ("观察次日承接是否继续。",),
                "cross_market_evidence_stack_summary": "同向 2 条｜反向 1 条",
                "cross_market_support_event_count": 2,
                "cross_market_conflict_event_count": 1,
            },
        )
    ]

    bundle = apply_portfolio_manager(picks, regime="stable_bull")

    assert bundle.picks[0].rating == "buy_candidate"
    assert bundle.decisions[0].action == "keep"
    assert bundle.summary.promote_count == 0
    assert bundle.summary.top_focus == ("300750",)
    assert bundle.summary.allocations[0].symbol == "300750"
    assert 0 < bundle.summary.allocations[0].weight <= 0.30
    assert bundle.summary.portfolio_risk_lines
    assert bundle.summary.regime_label == "稳定上涨"
    assert bundle.summary.strategy_mix_name == "进攻牛市"
    assert bundle.summary.strategy_mix_description == "稳定上涨期，重仓动量+涨停板"
    assert "RPS 动量" in bundle.summary.strategy_focus
    assert any(
        strategy_id == "rps_momentum" and round(weight, 2) == 1.06
        for strategy_id, weight in bundle.summary.strategy_weights
    )
    assert bundle.summary.debate_focus == (
        "300750 | 倾向优先纸面复核，主因 技术面强势 | 跨市证据 同向 2 条｜反向 1 条 | 历史校验: 强证据 2/3 (67%)；冲突主导 1/3",
    )
    assert bundle.summary.debate_support_points == ("300750 | 技术面强势且量价共振。",)
    assert bundle.summary.debate_opposition_points == (
        "300750 | 若高开过猛，追高回撤风险会放大。",
    )
    assert bundle.summary.debate_watch_items == ("300750 | 观察次日承接是否继续。",)
    assert bundle.summary.debate_risk_gates == ("300750 | 追高回撤风险",)
    assert bundle.summary.debate_next_triggers == ("300750 | 先确认开盘承接与量价延续",)
    assert bundle.summary.debate_priority_queue == (
        "300750 | 倾向优先纸面复核，主因 技术面强势 | 先确认开盘承接与量价延续 | 卡点 追高回撤风险 | 历史校验: 强证据 2/3 (67%)；冲突主导 1/3 | 角色可信度 跨市场: 近21天 7/10 (70%)｜当前权重 0.18 | 待确认 观察次日承接是否继续。 | 跨市证据 同向 2 条｜反向 1 条",
    )


def test_apply_portfolio_manager_includes_cross_market_evidence_in_focus_lines() -> (
    None
):
    picks = [
        _pick(
            "688981",
            70,
            metrics={
                "cross_market_primary_theme": "海外物理AI叙事升温",
                "cross_market_action": "重点跟踪",
                "cross_market_priority_score": 2,
                "cross_market_evidence_stack_summary": "同向 2 条｜反向 1 条",
                "debate_research_verdict": "倾向优先纸面复核",
            },
        ),
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.summary.cross_market_focus == (
        "688981 | 海外物理AI叙事升温(重点跟踪) | 同向 2 条｜反向 1 条",
    )
    assert bundle.summary.debate_focus == (
        "688981 | 倾向优先纸面复核 | 跨市证据 同向 2 条｜反向 1 条",
    )


def test_apply_portfolio_manager_keeps_structured_debate_notes_in_runtime_order() -> (
    None
):
    picks = [
        _pick(
            "300750",
            72,
            metrics={
                "debate_research_verdict": "倾向优先纸面复核",
                "debate_primary_risk_gate": "高位分歧仍需消化",
                "debate_next_trigger": "先确认量价延续",
                "debate_historical_context_note": "历史校验: 强证据 4/5 (80%)",
                "debate_historical_context_sample_count": 5,
                "debate_historical_context_accuracy": 0.8,
                "role_reliability_lines": ("跨市场: 近21天 7/10 (70%)",),
                "support_points": ("海外主线仍在扩散。",),
                "watch_items": ("观察次日承接。",),
                "cross_market_evidence_stack_summary": "同向 2 条｜反向 0 条",
                "cross_market_support_event_count": 2,
                "cross_market_conflict_event_count": 0,
            },
        ),
        _pick(
            "600036",
            88,
            metrics={
                "debate_research_verdict": "观点仍有分歧",
            },
        ),
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.summary.debate_priority_queue[0] == "600036 | 观点仍有分歧"
    assert bundle.summary.debate_priority_queue[1].startswith(
        "300750 | 倾向优先纸面复核"
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


def test_apply_portfolio_manager_uses_configured_allocation_limits(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_GOAL_SWITCH_MULTI_AGENT_RUNTIME_OVERRIDE", "true")
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


def test_portfolio_optimizer_uses_best_base_score_after_context_reordering() -> None:
    context_first = _pick("688981", 70)
    base_stronger = _pick("600036", 85)

    result = optimize_portfolio_allocations_from_risk(
        [context_first, base_stronger],
        {},
        risk=RiskThresholds(
            max_positions=2,
            max_single_position_pct=1.0,
            min_cash_reserve=0.0,
            allocation_score_strong=80.0,
            allocation_score_mid=75.0,
            allocation_invested_strong=0.60,
            allocation_invested_mid=0.35,
            allocation_adjustment_step=0.0,
            allocation_floor_pct=0.10,
        ),
    )

    assert result.invested_ratio == 0.60


def test_portfolio_optimizer_caps_by_score_not_input_order() -> None:
    low = _pick("000001", 60)
    high = _pick("300750", 85)

    result = optimize_portfolio_allocations_from_risk(
        [low, high],
        {},
        risk=RiskThresholds(
            max_positions=1,
            max_single_position_pct=1.0,
            min_cash_reserve=0.0,
            allocation_invested_strong=0.5,
            allocation_adjustment_step=0.0,
        ),
    )

    assert [item.symbol for item in result.allocations] == ["300750"]


def test_diversification_engine_enforces_correlation_constraint() -> None:
    engine = DiversificationEngine(
        PortfolioConstraint(
            max_weight=0.5,
            max_sector_weight=1.0,
            max_correlation=0.7,
            min_diversification=1,
        )
    )
    correlation = pd.DataFrame(
        [[1.0, 0.9], [0.9, 1.0]],
        index=["300750", "000001"],
        columns=["300750", "000001"],
    )

    result = engine.optimize(
        {"300750": 0.9, "000001": 0.8},
        {"300750": "新能源", "000001": "银行"},
        correlation,
    )

    assert result.symbols == ["300750"]
    assert result.max_correlation == 0.0


def test_diversification_engine_skips_non_positive_scores() -> None:
    engine = DiversificationEngine(
        PortfolioConstraint(
            max_weight=0.5,
            max_sector_weight=1.0,
            min_diversification=1,
        )
    )

    result = engine.optimize({"000001": -1.0}, {"000001": "银行"})

    assert result.symbols == []
    assert result.weights == {}


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


def test_apply_portfolio_manager_keeps_score_order_when_cross_market_medium_matches() -> (
    None
):
    picks = [
        _pick(
            "688981",
            70,
            metrics={
                "cross_market_primary_theme": "海外物理AI叙事升温",
                "cross_market_action": "重点跟踪",
                "cross_market_priority_score": 2,
                "cross_market_rule_ids": ("physical_ai",),
                "cross_market_score_adjustment_allowed": True,
                "cross_market_priority_boost": True,
                "cross_market_context_only": False,
            },
        ),
        _pick("600036", 71),
    ]

    bundle = apply_portfolio_manager(picks)
    decisions = {item.symbol: item for item in bundle.decisions}

    assert [pick.symbol for pick in bundle.picks] == ["600036", "688981"]
    assert bundle.picks[1].score == 70
    assert bundle.picks[1].rating == "buy_candidate"
    assert bundle.picks[1].metrics["context_priority_score"] == 70.0
    assert bundle.picks[1].metrics["context_priority_delta"] == 0.0
    assert decisions["688981"].action == "keep"
    assert decisions["688981"].score_delta == 0.0
    assert decisions["688981"].priority_delta == 0.0
    assert any("跨市证据仅进入复核" in reason for reason in decisions["688981"].reasons)
    assert bundle.summary.cross_market_overview == "海外物理AI叙事升温关联证据：688981"
    assert bundle.summary.cross_market_focus == (
        "688981 | 海外物理AI叙事升温(重点跟踪)",
    )


def test_apply_portfolio_manager_keeps_score_when_cross_market_strong_matches() -> None:
    picks = [
        _pick(
            "300750",
            66,
            metrics={
                "cross_market_primary_theme": "海外物理AI叙事升温",
                "cross_market_action": "优先复核",
                "cross_market_priority_score": 3,
                "cross_market_rule_ids": ("physical_ai",),
                "cross_market_score_adjustment_allowed": True,
                "cross_market_priority_boost": True,
                "cross_market_context_only": False,
            },
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.picks[0].score == 66
    assert bundle.picks[0].rating == "buy_candidate"
    assert bundle.picks[0].metrics["context_priority_score"] == 66.0
    assert bundle.picks[0].metrics["context_priority_delta"] == 0.0
    assert bundle.picks[0].metrics["portfolio_action"] == "keep"
    assert bundle.decisions[0].action == "keep"
    assert bundle.decisions[0].score_delta == 0.0
    assert bundle.decisions[0].priority_delta == 0.0
    assert bundle.summary.promote_count == 0
    assert any("跨市证据仅进入复核" in reason for reason in bundle.decisions[0].reasons)
    assert not any("跨市场催化匹配" in item for item in bundle.summary.action_hotspots)
    assert bundle.summary.cross_market_overview == "海外物理AI叙事升温关联证据：300750"
    assert bundle.summary.cross_market_focus == (
        "300750 | 海外物理AI叙事升温(优先复核)",
    )
    assert not any(
        "跨市场传导匹配提升优先级" in reason
        for reason in bundle.summary.allocations[0].rationale
    )


def test_apply_portfolio_manager_keeps_direct_news_context_from_changing_score() -> (
    None
):
    picks = [
        _pick(
            "300750",
            70,
            metrics={
                "cross_market_primary_theme": "消息面直接催化",
                "cross_market_linkage_basis": "新闻催化",
                "cross_market_action": "优先复核",
                "cross_market_priority_score": 3,
                "cross_market_score_adjustment_allowed": False,
                "cross_market_context_only": True,
                "news_catalyst_judgement": "supports",
            },
        ),
        _pick("600036", 71),
    ]

    bundle = apply_portfolio_manager(picks)
    decisions = {item.symbol: item for item in bundle.decisions}

    assert decisions["300750"].score_delta == 0.0
    assert decisions["300750"].action == "keep"
    assert bundle.picks[0].symbol == "600036"
    assert not any("跨市场催化匹配" in reason for reason in decisions["300750"].reasons)


def test_apply_portfolio_manager_keeps_negative_direct_news_from_changing_score() -> (
    None
):
    picks = [
        _pick(
            "300750",
            70,
            metrics={
                "cross_market_primary_theme": "消息面直接催化",
                "cross_market_linkage_basis": "新闻催化",
                "cross_market_action": "风险复核",
                "cross_market_priority_score": 2,
                "cross_market_score_adjustment_allowed": False,
                "cross_market_context_only": True,
                "news_catalyst_judgement": "opposes",
            },
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.picks[0].score == 70
    assert bundle.decisions[0].score_delta == 0.0
    assert bundle.decisions[0].action == "keep"


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
