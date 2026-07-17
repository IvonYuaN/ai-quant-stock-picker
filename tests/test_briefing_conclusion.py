from __future__ import annotations

from aqsp.briefing.agent_roles import AgentRole
from aqsp.briefing.conclusion import (
    build_debate_conclusion_view,
    debate_consensus_point,
    debate_summary_segments,
)
from aqsp.briefing.debate import AgentOpinion, DebateResult, DebateRound
from aqsp.briefing.schema import CommitteeConclusion


def _real_rounds() -> list[DebateRound]:
    return [
        DebateRound(
            round_num=1,
            opinions=[
                AgentOpinion(
                    agent_id="bull-1",
                    role=AgentRole.BULL,
                    stance="bullish",
                    confidence=0.7,
                    arguments=["量价共振"],
                ),
                AgentOpinion(
                    agent_id="risk-1",
                    role=AgentRole.RISK_CONTROL,
                    stance="neutral",
                    confidence=0.6,
                    arguments=["需要确认流动性"],
                    peer_reviewed_roles=["bull", "cross_market"],
                    risk_factors=["止损执行仍需复核"],
                ),
                AgentOpinion(
                    agent_id="cross-1",
                    role=AgentRole.CROSS_MARKET,
                    stance="bullish",
                    confidence=0.6,
                    arguments=["海外主题需要A股板块接力"],
                    peer_reviewed_roles=["bull", "risk_control"],
                ),
            ],
        ),
        DebateRound(
            round_num=2,
            opinions=[
                AgentOpinion(
                    agent_id="bull-1",
                    role=AgentRole.BULL,
                    stance="bullish",
                    confidence=0.7,
                    arguments=["量价共振"],
                    counterarguments=["已复核风险与跨市观点"],
                    peer_reviewed_roles=["risk_control", "cross_market"],
                ),
                AgentOpinion(
                    agent_id="risk-1",
                    role=AgentRole.RISK_CONTROL,
                    stance="neutral",
                    confidence=0.6,
                    arguments=["需要确认流动性"],
                    counterarguments=["已复核多头与跨市观点"],
                    peer_reviewed_roles=["bull", "cross_market"],
                    risk_factors=["止损执行仍需复核"],
                ),
                AgentOpinion(
                    agent_id="cross-1",
                    role=AgentRole.CROSS_MARKET,
                    stance="bullish",
                    confidence=0.6,
                    arguments=["海外主题需要A股板块接力"],
                    counterarguments=["已复核多头与风控观点"],
                    peer_reviewed_roles=["bull", "risk_control"],
                ),
            ],
        ),
    ]


def test_briefing_conclusion_view_builds_structured_lines_from_debate_result() -> None:
    result = DebateResult(
        debate_id="d1",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        deterministic_score=72.0,
        adjusted_score=78.0,
        rating="buy_candidate",
        rounds=_real_rounds(),
        recommended_adjustment="raise",
        disagreement_score=0.42,
        final_consensus="趋势强但仍需确认开盘承接",
        final_vote={
            AgentRole.BULL: "bullish",
            AgentRole.RISK_CONTROL: "neutral",
            AgentRole.CROSS_MARKET: "bullish",
        },
        market_context_lines=(
            "确认信号: 机器人龙头放量上攻且核心零部件同步走强",
            "失效条件: 只有海外叙事但A股机器人板块不共振",
        ),
        research_verdict="倾向继续观察，等待开盘承接确认",
        primary_risk_gate="高开过猛则回撤风险放大",
        next_trigger="先确认开盘承接是否继续增强。",
        historical_context_note="历史校验: 强证据 2/3 (67%)",
        role_reliability_lines=("技术多头: 近21天 7/10 (70%)",),
        role_selection_summary="因海外传导、分歧校验，本轮先看 技术多头、风险控制、跨市传导。",
        role_selection_plan="技术多头看趋势延续和量价共振；风险控制看流动性、止损和不可成交；跨市传导看海外催化到A股映射。",
        support_points=("量价共振且跨市主线仍在扩散",),
        opposition_points=("若高开过猛则追高回撤风险放大",),
        watch_items=("先确认开盘承接与量价延续",),
        real_message_evidence=("海外物理AI主题公开披露",),
        cross_market_evidence=("海外机器人订单公开披露",),
        rule_transmission_evidence=("传导假设: 海外主题 -> A股机器人",),
        pending_confirmations=("确认: A股板块同步放量",),
    )

    view = build_debate_conclusion_view(result)
    segments = debate_summary_segments(result)
    projection = CommitteeConclusion.from_debate_result(result)

    assert view.headline == "倾向继续观察，等待开盘承接确认"
    assert view.active_roles_line == "讨论视角: 技术多头、风险控制、跨市传导"
    assert (
        view.role_selection_line
        == "选角理由: 因海外传导、分歧校验，本轮先看 技术多头、风险控制、跨市传导。"
    )
    assert (
        view.role_plan_line
        == "角色分工: 技术多头看趋势延续和量价共振；风险控制看流动性、止损和不可成交；跨市传导看海外催化到A股映射。"
    )
    assert view.risk_gate_line == "核心卡点: 高开过猛则回撤风险放大"
    assert view.validation_line == "确认信号: 机器人龙头放量上攻且核心零部件同步走强"
    assert view.invalidation_line == "失效信号: 只有海外叙事但A股机器人板块不共振"
    assert view.trigger_line == "下一触发: 先确认开盘承接是否继续增强。"
    assert view.historical_context_line == "历史校验: 强证据 2/3 (67%)"
    assert view.quality_audit is not None
    assert view.quality_audit.historical_evaluation_only
    assert view.reliability_line == "角色可信度: 技术多头: 近21天 7/10 (70%)"
    assert projection.event_evidence == ("海外物理AI主题公开披露",)
    assert projection.cross_market_evidence == ("海外机器人订单公开披露",)
    assert projection.transmission_points == ("传导假设: 海外主题 -> A股机器人",)
    assert projection.pending_confirmations == ("确认: A股板块同步放量",)
    assert segments == (
        "倾向继续观察，等待开盘承接确认",
        "视角: 技术多头、风险控制、跨市传导",
        "选角: 因海外传导、分歧校验，本轮先看 技术多头、风险控制、跨市传导。",
        "分工: 技术多头看趋势延续和量价共振；风险控制看流动性、止损和不可成交；跨市传导看海外催化到A股映射。",
        "卡点: 高开过猛则回撤风险放大",
        "确认: 机器人龙头放量上攻且核心零部件同步走强",
        "失效: 只有海外叙事但A股机器人板块不共振",
        "触发: 先确认开盘承接是否继续增强。",
    )


def test_debate_consensus_point_reuses_structured_lines_for_raise_result() -> None:
    result = DebateResult(
        debate_id="d2",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        deterministic_score=72.0,
        adjusted_score=78.0,
        rating="buy_candidate",
        rounds=_real_rounds(),
        recommended_adjustment="raise",
        disagreement_score=0.42,
        final_consensus="bullish",
        final_vote={
            AgentRole.BULL: "bullish",
            AgentRole.RISK_CONTROL: "neutral",
            AgentRole.CROSS_MARKET: "bullish",
        },
        market_context_lines=(
            "确认信号: 机器人龙头放量上攻且核心零部件同步走强",
            "失效条件: 只有海外叙事但A股机器人板块不共振",
        ),
        research_verdict="倾向继续观察，等待开盘承接确认",
        primary_risk_gate="高开过猛则回撤风险放大",
        next_trigger="先确认开盘承接是否继续增强。",
        support_points=("量价共振",),
        opposition_points=("高开过猛则回撤风险放大",),
        risk_warnings=["高位波动"],
    )

    assert debate_consensus_point(result) == (
        "委员会结论: 宁德时代(300750) 倾向继续观察，等待开盘承接确认；"
        "附件参考分78.0，不改写系统评分｜"
        "支持 量价共振｜"
        "反对 高开过猛则回撤风险放大｜"
        "触发 先确认开盘承接是否继续增强。"
    )


def test_briefing_conclusion_blocks_unmapped_candidate() -> None:
    result = DebateResult(
        debate_id="unmapped",
        symbol="",
        name="",
        original_score=72.0,
        rating="watch",
        final_consensus="bullish",
        final_vote={AgentRole.BULL: "bullish"},
        next_trigger="确认量价延续",
    )

    view = build_debate_conclusion_view(result)

    assert not view.candidate_mapped
    assert view.headline == "结论已阻断：无法映射候选"
    assert debate_summary_segments(result) == ("结论已阻断：无法映射候选",)
    assert debate_consensus_point(result) == ""


def test_briefing_conclusion_blocks_candidate_symbol_mismatch() -> None:
    result = DebateResult(
        debate_id="mismatch",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        rating="watch",
        final_consensus="bullish",
        final_vote={AgentRole.BULL: "bullish"},
        next_trigger="确认量价延续",
    )

    view = build_debate_conclusion_view(result, candidate={"symbol": "600519"})

    assert not view.candidate_mapped
    assert view.quality_audit is not None
    assert "candidate_unmapped" in view.quality_audit.issues


def test_briefing_conclusion_blocks_empty_data_even_for_legacy_payload() -> None:
    result = DebateResult(
        debate_id="empty-legacy",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        adjusted_score=80.0,
        rating="buy_candidate",
        data_status="empty",
        data_note="行情数据为空",
        final_consensus="bullish",
        final_vote={AgentRole.BULL: "bullish", AgentRole.RISK_CONTROL: "neutral"},
        support_points=("量价共振",),
        opposition_points=("高位波动",),
        risk_warnings=["高位波动"],
        next_trigger="确认开盘承接",
    )

    view = build_debate_conclusion_view(result)

    assert view.quality_audit is not None
    assert not view.quality_audit.passed
    assert "empty_market_data" in view.quality_audit.issues
    assert view.headline == "结论已阻断：行情数据为空"


def test_briefing_conclusion_blocks_evidence_free_result_instead_of_rendering_consensus() -> (
    None
):
    result = DebateResult(
        debate_id="evidence-free",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        adjusted_score=80.0,
        rating="buy_candidate",
        recommended_adjustment="raise",
        final_consensus="bullish",
        final_vote={
            AgentRole.BULL: "bullish",
            AgentRole.RISK_CONTROL: "neutral",
            AgentRole.CROSS_MARKET: "bullish",
        },
        support_points=("尚未形成明确支持证据，先保持观察。",),
        opposition_points=("尚未形成明确反对证据，仍需风险复核。",),
        risk_warnings=["输入未提供可核验风险证据"],
        next_trigger="等待更多确认",
    )

    view = build_debate_conclusion_view(result)

    assert view.headline == "结论已阻断：缺少可核验证据"
    assert view.quality_audit is not None
    assert "no_substantive_evidence" in view.quality_audit.issues
    assert debate_consensus_point(result).startswith("委员会阻塞:")


def test_briefing_conclusion_does_not_legacy_bypass_empty_market_data() -> None:
    result = DebateResult(
        debate_id="legacy-empty",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        rating="watch",
        data_status="empty",
        final_consensus="bullish",
        final_vote={AgentRole.BULL: "bullish", AgentRole.RISK_CONTROL: "neutral"},
        support_points=("量价结构",),
        opposition_points=("等待风险确认",),
        risk_warnings=["行情数据为空"],
        next_trigger="补齐行情后复核",
    )

    view = build_debate_conclusion_view(result)

    assert view.headline == "结论已阻断：行情数据为空"
    assert view.quality_audit is not None
    assert "empty_market_data" in view.quality_audit.issues


def test_briefing_conclusion_does_not_legacy_bypass_explicit_incomplete_dict() -> None:
    payload = {
        "symbol": "300750",
        "name": "宁德时代",
        "final_consensus": "bullish",
        "final_vote": {"bull": "bullish"},
        "process_recorded": False,
        "conclusion_recorded": True,
    }

    view = build_debate_conclusion_view(payload)

    assert view.quality_audit is not None
    assert not view.quality_audit.passed
    assert "empty_discussion" in view.quality_audit.issues
