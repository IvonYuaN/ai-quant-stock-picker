from __future__ import annotations

import json
import time
from datetime import timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from aqsp.briefing.agent_roles import AgentRole
from aqsp.briefing.conclusion import debate_evidence_provenance
from aqsp.briefing.schema import CommitteeConclusion
from aqsp.briefing.debate import (
    AShareDebateCoordinator,
    AShareDebateAgent,
    AgentPerformanceMetrics,
    AgentOpinion,
    DebateResult,
    DebateRound,
    RebuttalRecord,
    format_debate_result,
)
from aqsp.briefing.debate_tracker import (
    AgentResponsibility,
    AgentReliabilitySummary,
    CrossMarketContextHistorySummary,
    audit_debate_quality,
    DebatePerformanceTracker,
)
from aqsp.core.types import PickResult
from aqsp.core.time import now_shanghai
from aqsp.utils.llm_safe import LlmResult


def _make_pick(**overrides) -> PickResult:
    defaults = dict(
        symbol="300750",
        name="宁德时代",
        date="2026-06-30",
        close=100.0,
        score=72.0,
        rating="buy_candidate",
        entry_type="relative_strength",
        ideal_buy=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        position="watch",
        strategies=("momentum",),
        reasons=("放量突破",),
        risks=("追高波动",),
        metrics={},
    )
    defaults.update(overrides)
    return PickResult(**defaults)


def test_debate_performance_tracker_calculate_adjustment_weight_when_cross_market_evidence_varies(
    tmp_path,
) -> None:
    tracker = DebatePerformanceTracker(
        storage_path=str(tmp_path / "debate_performance.jsonl")
    )
    agent_id = "cross_market_demo"
    metrics = tracker.get_agent_metrics(AgentRole.CROSS_MARKET, agent_id)
    metrics.total_predictions = 10
    metrics.correct_predictions = 8

    unknown_weight = tracker.calculate_adjustment_weight(
        AgentRole.CROSS_MARKET,
        agent_id,
        regime="stable_bull",
        debate_context={
            "cross_market_support_event_count": 0,
            "cross_market_conflict_event_count": 0,
        },
    )
    strong_weight = tracker.calculate_adjustment_weight(
        AgentRole.CROSS_MARKET,
        agent_id,
        regime="stable_bull",
        debate_context={
            "cross_market_support_event_count": 2,
            "cross_market_conflict_event_count": 0,
        },
    )
    conflicted_weight = tracker.calculate_adjustment_weight(
        AgentRole.CROSS_MARKET,
        agent_id,
        regime="stable_bull",
        debate_context={
            "cross_market_support_event_count": 1,
            "cross_market_conflict_event_count": 2,
        },
    )
    bull_weight = tracker.calculate_adjustment_weight(
        AgentRole.BULL,
        "bull_demo",
        regime="stable_bull",
        debate_context={
            "cross_market_support_event_count": 2,
            "cross_market_conflict_event_count": 0,
        },
    )

    assert strong_weight > unknown_weight > conflicted_weight
    assert bull_weight >= 0.0


def test_debate_agent_uses_stable_agent_id_when_runtime_signature_matches() -> None:
    first = AShareDebateAgent(
        role=AgentRole.CROSS_MARKET,
        enable_llm=True,
        language="zh-CN",
        llm_provider="agnes",
        llm_model="agnes-2.0-flash",
    )
    second = AShareDebateAgent(
        role=AgentRole.CROSS_MARKET,
        enable_llm=True,
        language="zh-CN",
        llm_provider="agnes",
        llm_model="agnes-2.0-flash",
    )
    changed = AShareDebateAgent(
        role=AgentRole.CROSS_MARKET,
        enable_llm=False,
        language="zh-CN",
    )

    assert first.agent_id == second.agent_id
    assert first.agent_id != changed.agent_id


def test_debate_adjustment_marks_bull_bear_tie_as_keep(tmp_path) -> None:
    tracker = DebatePerformanceTracker(
        storage_path=str(tmp_path / "debate-performance.jsonl")
    )

    adjustment, disagreement, recommendation = tracker.calculate_debate_adjustment(
        {AgentRole.BULL: "bullish", AgentRole.BEAR: "bearish"},
        {AgentRole.BULL: 0.1, AgentRole.BEAR: 0.1},
    )

    assert adjustment == 0.0
    assert disagreement == 0.75
    assert recommendation == "keep"


def test_debate_adjustment_normalizes_contrarian_weights_by_magnitude(tmp_path) -> None:
    tracker = DebatePerformanceTracker(
        storage_path=str(tmp_path / "debate-performance.jsonl")
    )

    adjustment, _, recommendation = tracker.calculate_debate_adjustment(
        {AgentRole.BULL: "bullish", AgentRole.BEAR: "bearish"},
        {AgentRole.BULL: 0.2, AgentRole.BEAR: -0.1},
    )

    assert adjustment == pytest.approx(0.3)
    assert recommendation == "raise"


def test_debate_adjustment_uses_current_vote_margin_before_history_unlocks(
    tmp_path,
) -> None:
    tracker = DebatePerformanceTracker(
        storage_path=str(tmp_path / "debate-performance.jsonl")
    )

    adjustment, _, recommendation = tracker.calculate_debate_adjustment(
        {
            AgentRole.BULL: "bullish",
            AgentRole.BEAR: "bullish",
            AgentRole.RISK_CONTROL: "bearish",
        },
        {
            role: 0.0
            for role in (AgentRole.BULL, AgentRole.BEAR, AgentRole.RISK_CONTROL)
        },
    )

    assert adjustment > 0
    assert adjustment < 0.3
    assert recommendation == "raise"


def test_debate_performance_tracker_reuses_stable_agent_history_when_reloaded(
    tmp_path,
) -> None:
    storage_path = str(tmp_path / "debate_performance.jsonl")
    agent = AShareDebateAgent(
        role=AgentRole.CROSS_MARKET,
        enable_llm=False,
        language="zh-CN",
    )
    tracker = DebatePerformanceTracker(storage_path=storage_path)
    tracker.record_prediction(
        AgentRole.CROSS_MARKET,
        agent.agent_id,
        "bullish",
        was_correct=True,
    )
    tracker.record_prediction(
        AgentRole.CROSS_MARKET,
        agent.agent_id,
        "bullish",
        was_correct=False,
    )

    reloaded = DebatePerformanceTracker(storage_path=storage_path)
    metrics = reloaded.get_agent_metrics(AgentRole.CROSS_MARKET, agent.agent_id)

    assert metrics.agent_id == agent.agent_id
    assert metrics.total_predictions == 2
    assert metrics.correct_predictions == 1


def test_debate_performance_tracker_deduplicates_one_role_outcome_per_debate(
    tmp_path,
) -> None:
    tracker = DebatePerformanceTracker(
        storage_path=str(tmp_path / "debate_performance.jsonl")
    )
    identity = {
        "debate_id": "debate-1",
        "signal_date": "2026-07-13",
        "candidate_fingerprint": "candidate-1",
    }
    for _ in range(2):
        tracker.record_prediction(
            AgentRole.BULL,
            "bull-agent",
            "bullish",
            was_correct=True,
            **identity,
        )

    metrics = tracker.get_agent_metrics(AgentRole.BULL, "bull-agent")
    assert metrics.total_predictions == 1
    assert metrics.correct_predictions == 1


def test_debate_performance_tracker_builds_agent_reliability_summary(
    tmp_path,
) -> None:
    tracker = DebatePerformanceTracker(
        storage_path=str(tmp_path / "debate_performance.jsonl")
    )
    agent_id = "bull_demo"
    metrics = tracker.get_agent_metrics(AgentRole.BULL, agent_id)
    metrics.total_predictions = 10
    metrics.correct_predictions = 7
    metrics.bias_toward = "bullish"

    summary = tracker.get_agent_reliability_summary(
        AgentRole.BULL,
        agent_id,
        regime="stable_bull",
    )

    assert isinstance(summary, AgentReliabilitySummary)
    assert summary.sample_count == 10
    assert summary.correct_count == 7
    assert summary.accuracy == 0.7
    assert summary.adjustment_weight > 0
    assert "技术多头: 近21天 7/10 (70%)" in summary.summary_line


def test_debate_performance_tracker_reports_role_responsibility_without_scoring(
    tmp_path,
) -> None:
    tracker = DebatePerformanceTracker(
        storage_path=str(tmp_path / "debate_performance.jsonl")
    )

    responsibilities = tracker.get_agent_responsibilities(
        {AgentRole.BULL: "bull-fixed", AgentRole.RISK_CONTROL: "risk-fixed"}
    )

    assert all(isinstance(item, AgentResponsibility) for item in responsibilities)
    assert [item.role for item in responsibilities] == [
        AgentRole.BULL,
        AgentRole.RISK_CONTROL,
    ]
    assert responsibilities[0].agent_id == "bull-fixed"
    assert "趋势延续" in responsibilities[0].responsibility


def test_debate_coordinator_keeps_deterministic_score_as_advisory_boundary(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    pick = _make_pick(score=72.0)
    coordinator = AShareDebateCoordinator(
        enable_llm=False,
        max_rounds=1,
        roles=(AgentRole.BULL,),
    )

    result = coordinator.run_debate(
        pick,
        pd.DataFrame({"close": [10 + i for i in range(30)]}),
    )

    assert result.deterministic_score == pick.score
    assert result.original_score == pick.score
    assert result.deterministic_score_unchanged is True
    assert result.advisory_only is True


def test_debate_audit_rejects_missing_deterministic_score_when_original_score_is_nonzero(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    pick = _make_pick(score=72.0)
    result = AShareDebateCoordinator(
        max_rounds=2,
        roles=(AgentRole.BULL, AgentRole.RISK_CONTROL),
    ).run_debate(pick, pd.DataFrame({"close": [100.0, 101.0]}))
    result.deterministic_score = 0.0

    audit = audit_debate_quality(
        result,
        candidate=pick,
        expected_roles=(AgentRole.BULL, AgentRole.RISK_CONTROL),
    )

    assert audit.advisory_boundary_ok is False
    assert "advisory_boundary_violation" in audit.issues


def test_debate_coordinator_blocks_placeholder_only_rounds_without_fake_interaction(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    pick = _make_pick(
        score=50.0,
        strategies=(),
        reasons=(),
        risks=(),
    )
    result = AShareDebateCoordinator(
        enable_llm=False,
        max_rounds=2,
        roles=(AgentRole.BULL, AgentRole.RISK_CONTROL),
    ).run_debate(pick, pd.DataFrame())

    assert result.failure.startswith("讨论链路未通过审计")
    assert "empty_market_data" in result.failure
    assert result.research_verdict == "结论阻断：行情数据为空，仅记录待补行情。"
    assert result.rounds == []
    assert result.deterministic_score == pick.score
    assert result.deterministic_score_unchanged is True


def test_debate_evidence_provenance_drops_unavailable_news_text() -> None:
    pick = _make_pick(
        metrics={"news_catalyst_lead": "无可用新闻记录"},
    )

    messages, rules, pending = AShareDebateCoordinator._extract_evidence_provenance(
        pick,
        ("候选消息: 无可用新闻记录",),
    )

    assert messages == ()
    assert rules == ()
    assert pending == ()


def test_debate_evidence_provenance_filters_placeholder_fallback_lines() -> None:
    result = DebateResult(
        debate_id="fallback-evidence",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        rating="watch",
        market_context_lines=(
            "候选消息: 无可用新闻记录",
            "跨市证据: 等待更多确认",
            "传导链: 海外主题 -> A股板块",
        ),
    )

    provenance = debate_evidence_provenance(result)

    assert provenance.real_messages == ()
    assert provenance.cross_market_evidence == ()
    assert provenance.rule_transmissions == ("传导链: 海外主题 -> A股板块",)


def test_debate_evidence_provenance_keeps_scalar_event_and_cross_market_evidence() -> (
    None
):
    pick = _make_pick(
        metrics={
            "news_catalyst_supporting_evidence": "Reuters: 订单公告",
            "cross_market_supporting_evidence": "海外同业同步上调指引",
            "cross_market_transmission_path": "海外物理AI -> A股设备链",
            "cross_market_validation_signals": "A股龙头同步放量",
        }
    )

    messages, rules, pending = AShareDebateCoordinator._extract_evidence_provenance(
        pick, ()
    )
    cross_market = AShareDebateCoordinator._extract_cross_market_evidence(pick, ())

    assert messages == ("Reuters: 订单公告",)
    assert cross_market == ("海外同业同步上调指引",)
    assert rules == ("传导路径: 海外物理AI -> A股设备链",)
    assert pending == ("确认: A股龙头同步放量",)


def test_debate_coordinator_blocks_empty_market_before_any_round_is_generated(
    monkeypatch,
) -> None:
    coordinator = AShareDebateCoordinator(
        enable_llm=True,
        max_rounds=2,
        roles=(AgentRole.BULL, AgentRole.RISK_CONTROL, AgentRole.CROSS_MARKET),
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("empty market data must not start a debate")

    monkeypatch.setattr(coordinator, "_run_debate_rounds", fail_if_called)
    pick = _make_pick(score=72.0)
    result = coordinator.run_debate(pick, pd.DataFrame())

    assert result.rounds == []
    assert result.final_vote == {}
    assert result.data_status == "empty"
    assert "empty_market_data" in result.failure
    assert result.adjusted_score == pick.score
    assert result.deterministic_score == pick.score
    assert result.deterministic_score_unchanged is True


def test_debate_llm_payload_cannot_rewrite_deterministic_score(monkeypatch) -> None:
    monkeypatch.setattr(
        "aqsp.briefing.debate.llm_call_or_fallback",
        lambda **kwargs: LlmResult(
            text='{"score": 0, "arguments": ["模型建议改分"]}',
            degraded=False,
        ),
    )
    pick = _make_pick(score=72.0)
    result = AShareDebateCoordinator(
        enable_llm=True,
        max_rounds=2,
        roles=(AgentRole.BULL, AgentRole.RISK_CONTROL),
    ).run_debate(pick, pd.DataFrame({"close": [100.0, 101.0, 102.0]}))

    assert result.original_score == 72.0
    assert result.deterministic_score == 72.0
    assert result.deterministic_score_unchanged is True
    assert all(opinion.llm_advisory_points for opinion in result.rounds[0].opinions)


def test_debate_audit_uses_configured_roles_instead_of_only_recorded_votes() -> None:
    result = DebateResult(
        debate_id="partial-roles",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        rating="watch",
        expected_roles=("bull", "risk_control"),
        rounds=[
            DebateRound(
                round_num=1,
                opinions=[
                    AgentOpinion(
                        agent_id="bull-1",
                        role=AgentRole.BULL,
                        stance="bullish",
                        confidence=0.7,
                        arguments=["放量突破"],
                    )
                ],
            )
        ],
        final_consensus="bullish",
        final_vote={AgentRole.BULL: "bullish"},
        support_points=("放量突破",),
        opposition_points=("缺少风控角色复核",),
        risk_warnings=["缺少风控角色复核"],
        next_trigger="补充风控角色复核",
    )

    audit = audit_debate_quality(result, expected_roles=result.expected_roles)

    assert not audit.passed
    assert "missing_role" in audit.issues


def test_debate_coordinator_records_real_second_round_when_no_opposing_stance() -> None:
    roles = (
        AgentRole.BULL,
        AgentRole.RISK_CONTROL,
        AgentRole.CROSS_MARKET,
    )
    pick = _make_pick(score=72.0, metrics={})
    coordinator = AShareDebateCoordinator(
        enable_llm=False,
        max_rounds=2,
        roles=roles,
    )

    result = coordinator.run_debate(
        pick,
        pd.DataFrame({"close": [100.0, 101.0]}),
    )

    assert len(result.rounds) == 2
    assert all(round_data.opinions for round_data in result.rounds)
    assert all(
        any(opinion.counterarguments for opinion in round_data.opinions)
        for round_data in result.rounds[1:]
    )
    reviewed_opinions = [
        opinion for opinion in result.rounds[1].opinions if opinion.rebuttal_records
    ]
    assert reviewed_opinions
    assert all(opinion.peer_reviewed_roles for opinion in reviewed_opinions)
    assert all(
        record.challenged_claim and record.rebuttal_reason
        for opinion in result.rounds[1].opinions
        for record in opinion.rebuttal_records
    )
    audit = audit_debate_quality(
        result,
        candidate=pick,
        expected_roles=roles,
    )
    assert audit.passed


def test_debate_agents_do_not_claim_unprovided_flow_or_policy_evidence() -> None:
    pick = _make_pick(score=72.0, metrics={})
    frame = pd.DataFrame({"close": [100.0, 101.0]})

    for role in (
        AgentRole.POLICY_SENSITIVE,
        AgentRole.MARGIN_TRADING,
        AgentRole.NORTHBOUND,
        AgentRole.RETAIL_MOOD,
    ):
        opinion = AShareDebateAgent(role).generate_initial_opinion(pick, frame)
        text = " ".join(
            opinion.arguments + opinion.risk_factors + opinion.opportunity_factors
        )
        assert "持续净流入" not in text
        assert "政策支持行业具有超额收益" not in text
        assert "市场情绪高涨利于多头" not in text


def test_debate_result_to_dict_persists_process_and_advisory_boundary() -> None:
    pick = _make_pick(score=72.0)
    result = AShareDebateCoordinator(
        enable_llm=False,
        max_rounds=2,
        roles=(AgentRole.BULL, AgentRole.RISK_CONTROL, AgentRole.CROSS_MARKET),
    ).run_debate(pick, pd.DataFrame({"close": [100.0, 101.0]}))

    payload = result.to_dict()

    assert payload["process_recorded"] is True
    assert payload["debate_rounds_completed"] == 2
    assert payload["conclusion_recorded"] is True
    assert payload["adjusted_score_is_advisory"] is True
    assert payload["deterministic_score"] == pick.score
    assert payload["viewpoint_coverage"]["cross_market"] is True
    assert payload["discussion_agent_count"] == 3
    assert payload["rebuttal_count"] >= 1
    assert payload["real_opposition_count"] >= 1


def test_debate_result_keeps_independent_viewpoint_buckets_and_final_round_counts(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    pick = _make_pick(
        metrics={
            "cross_market_primary_theme": "海外物理AI叙事升温",
            "cross_market_support_event_count": 2,
            "cross_market_supporting_evidence": "Reuters: 海外设备商上调指引",
            "cross_market_validation_signals": ("A股设备链同步放量",),
        }
    )
    result = AShareDebateCoordinator(
        max_rounds=2,
        roles=(
            AgentRole.BULL,
            AgentRole.BEAR,
            AgentRole.RISK_CONTROL,
            AgentRole.CROSS_MARKET,
        ),
        task_id="viewpoint-test",
    ).run_debate(pick, pd.DataFrame({"close": [100.0, 101.0, 102.0]}))

    payload = result.to_dict()
    buckets = payload["viewpoint_buckets"]
    assert set(buckets) == {
        "bullish",
        "bearish",
        "event_fundamental",
        "technical",
        "risk_counterevidence",
        "uncertainty",
    }
    assert buckets["bullish"]
    assert buckets["technical"]
    assert buckets["bearish"]
    assert buckets["event_fundamental"]
    assert buckets["risk_counterevidence"]
    assert sum(payload["stance_counts"].values()) == len(result.final_vote)
    assert payload["deterministic_score"] == pick.score
    assert payload["deterministic_score_unchanged"] is True

    conclusion = CommitteeConclusion.from_debate_result(result)
    assert conclusion.viewpoint_buckets["technical"]
    assert conclusion.viewpoint_buckets["event_fundamental"]
    assert conclusion.viewpoint_buckets["risk_counterevidence"]
    assert conclusion.disagreement_points


def test_debate_audit_keeps_neutral_bear_role_out_of_real_opposition_count() -> None:
    pick = _make_pick(score=72.0, risks=())
    result = AShareDebateCoordinator(
        enable_llm=False,
        max_rounds=2,
        roles=(AgentRole.BULL, AgentRole.BEAR),
    ).run_debate(
        pick,
        pd.DataFrame({"close": [100.0, 101.0]}),
        signal_date=pick.date,
    )

    bear_round = next(
        opinion
        for opinion in result.rounds[-1].opinions
        if opinion.role == AgentRole.BEAR
    )
    assert bear_round.stance == "neutral"
    assert bear_round.rebuttal_records
    assert any(
        "风险条件" in record.rebuttal_reason for record in bear_round.rebuttal_records
    )

    audit = audit_debate_quality(
        result, candidate=pick, expected_roles=("bull", "bear")
    )

    assert not audit.passed
    assert audit.rebuttal_count >= 1
    assert audit.real_opposition_recorded is False
    assert audit.real_opposition_count == 0
    assert "missing_real_opposition" in audit.issues


def test_debate_coordinator_passes_cross_market_evidence_context_to_tracker_when_adjusting() -> (
    None
):
    captured: dict[str, object] = {}

    class DummyTracker:
        def get_all_weights(self, agent_ids, regime="unknown", debate_context=None):
            captured["regime"] = regime
            captured["debate_context"] = dict(debate_context or {})
            return {role: 0.1 for role in agent_ids}

        def calculate_debate_adjustment(self, votes, agent_weights):
            return (0.0, 0.0, "keep")

        def get_agent_metrics(self, role, agent_id):
            return AgentPerformanceMetrics(agent_id=agent_id, role=role)

        def get_cross_market_context_history(self, debate_context=None):
            return CrossMarketContextHistorySummary(
                current_bucket="conflicted",
                current_label="支持但有分歧",
                current_sample_count=4,
                current_accuracy=0.5,
                total_sample_count=9,
                governance_note="历史校验: 支持但有分歧 2/4 (50%)；强证据 4/5",
                bucket_summaries=(),
            )

        def get_all_reliability_summaries(
            self, agent_ids, *, regime="unknown", debate_context=None
        ):
            return tuple(
                AgentReliabilitySummary(
                    role=role,
                    role_label=role.value,
                    agent_id=agent_id,
                    sample_count=5,
                    correct_count=3,
                    accuracy=0.6,
                    adjustment_weight=0.1,
                    bias_toward="neutral",
                )
                for role, agent_id in agent_ids.items()
            )

    coordinator = AShareDebateCoordinator(
        enable_llm=False,
        max_rounds=1,
        roles=(AgentRole.CROSS_MARKET,),
        regime="stable_bull",
    )
    coordinator.tracker = DummyTracker()

    result = coordinator.run_debate(
        _make_pick(
            metrics={
                "cross_market_primary_theme": "海外物理AI叙事升温",
                "cross_market_action": "优先复核",
                "cross_market_priority_score": 3,
            }
        ),
        pd.DataFrame({"close": [10 + i for i in range(30)]}),
        market_context_lines=(
            "传导推演[强]: 海外物理AI叙事升温 -> A股机器人；动作 优先复核；观察窗 2-5日；同向 2 条｜反向 1 条；优先看有订单、放量和产业催化验证的环节。",
            "证据堆栈: 同向 2 条｜反向 1 条",
        ),
    )

    assert result.cross_market_support_event_count == 2
    assert result.cross_market_conflict_event_count == 1
    assert result.cross_market_evidence_stack_summary == "同向 2 条｜反向 1 条"
    assert (
        result.historical_context_note == "历史校验: 支持但有分歧 2/4 (50%)；强证据 4/5"
    )
    assert result.role_reliability_lines == (
        "cross_market: 近21天 3/5 (60%)｜当前权重 0.10",
    )
    assert captured["regime"] == "stable_bull"
    assert captured["debate_context"] == {
        "cross_market_support_event_count": 2,
        "cross_market_conflict_event_count": 1,
        "cross_market_evidence_stack_summary": "同向 2 条｜反向 1 条",
    }


def test_debate_risk_control_veto_blocks_advisory_raise() -> None:
    coordinator = AShareDebateCoordinator(
        enable_llm=False,
        max_rounds=2,
        roles=(AgentRole.BULL, AgentRole.RISK_CONTROL),
    )
    coordinator.tracker = SimpleNamespace(
        get_all_weights=lambda agent_ids, regime="unknown", debate_context=None: {
            role: 1.0 for role in agent_ids
        },
        calculate_debate_adjustment=lambda votes, agent_weights: (0.2, 0.5, "raise"),
        get_cross_market_context_history=lambda debate_context=None: SimpleNamespace(
            governance_note="",
            current_bucket="",
            current_sample_count=0,
            current_accuracy=0.0,
        ),
        get_all_reliability_summaries=lambda agent_ids, regime="unknown", debate_context=None: (),
    )
    result = DebateResult(
        debate_id="risk-veto",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        rating="watch",
        final_vote={
            AgentRole.BULL: "bullish",
            AgentRole.RISK_CONTROL: "bearish",
        },
    )

    coordinator._calculate_adjustment(result, {})

    assert result.adjustment_weight == 0.0
    assert result.recommended_adjustment == "keep"
    assert result.risk_veto_applied is True
    assert "禁止讨论层上调" in result.risk_veto_reason


def test_debate_bear_and_risk_roles_emit_falsifiable_checks_for_high_score() -> None:
    pick = _make_pick(score=72.0, risks=())
    frame = pd.DataFrame({"close": [100.0, 101.0, 102.0]})

    bear = AShareDebateAgent(AgentRole.BEAR).generate_initial_opinion(pick, frame)
    risk = AShareDebateAgent(AgentRole.RISK_CONTROL).generate_initial_opinion(
        pick, frame
    )

    assert any("反方检验" in item for item in bear.risk_factors)
    assert any("失效检验" in item for item in risk.risk_factors)


def test_debate_performance_tracker_summarizes_cross_market_context_history_when_records_exist(
    tmp_path,
) -> None:
    tracker = DebatePerformanceTracker(
        storage_path=str(tmp_path / "debate_performance.jsonl")
    )

    for index in range(3):
        tracker.record_prediction(
            AgentRole.CROSS_MARKET,
            f"cross_market_strong_{index}",
            "bullish",
            was_correct=index < 2,
            context={
                "cross_market_support_event_count": 2,
                "cross_market_conflict_event_count": 0,
            },
        )
    for index in range(3):
        tracker.record_prediction(
            AgentRole.CROSS_MARKET,
            f"cross_market_conflict_{index}",
            "bearish",
            was_correct=index == 0,
            context={
                "cross_market_support_event_count": 1,
                "cross_market_conflict_event_count": 2,
            },
        )

    history = tracker.get_cross_market_context_history(
        debate_context={
            "cross_market_support_event_count": 2,
            "cross_market_conflict_event_count": 0,
        }
    )

    assert history.current_bucket == "strong_supportive"
    assert history.current_sample_count == 3
    assert history.current_accuracy == 2 / 3
    assert history.total_sample_count == 6
    assert history.governance_note == "历史校验: 强证据 2/3 (67%)；冲突主导 1/3"
    assert history.bucket_summaries[0].bucket == "strong_supportive"
    assert history.bucket_summaries[0].accuracy == 2 / 3
    assert history.bucket_summaries[1].bucket == "conflicts_dominate"
    assert history.bucket_summaries[1].accuracy == 1 / 3


def test_debate_performance_tracker_filters_context_history_by_task_id(
    tmp_path,
) -> None:
    tracker = DebatePerformanceTracker(
        storage_path=str(tmp_path / "debate_performance.jsonl")
    )

    tracker.record_prediction(
        AgentRole.CROSS_MARKET,
        "cross_market_shared",
        "bullish",
        was_correct=True,
        task_id="intraday",
        context={
            "cross_market_support_event_count": 2,
            "cross_market_conflict_event_count": 0,
        },
    )
    tracker.record_prediction(
        AgentRole.CROSS_MARKET,
        "cross_market_shared",
        "bearish",
        was_correct=False,
        task_id="closing_review",
        context={
            "cross_market_support_event_count": 0,
            "cross_market_conflict_event_count": 2,
        },
    )

    intraday = tracker.get_cross_market_context_history(
        task_id="intraday",
        debate_context={
            "cross_market_support_event_count": 2,
            "cross_market_conflict_event_count": 0,
        },
    )
    closing = tracker.get_cross_market_context_history(
        task_id="closing_review",
        debate_context={
            "cross_market_support_event_count": 0,
            "cross_market_conflict_event_count": 2,
        },
    )

    assert intraday.total_sample_count == 1
    assert intraday.current_bucket == "strong_supportive"
    assert closing.total_sample_count == 1
    assert closing.current_bucket == "conflicts_dominate"


@pytest.mark.parametrize("round_count", [2, 3])
def test_audit_debate_quality_accepts_real_nine_role_round_history(
    round_count: int,
) -> None:
    roles = tuple(AgentRole)
    rounds: list[DebateRound] = []
    for round_num in range(1, round_count + 1):
        opinions = [
            AgentOpinion(
                agent_id=f"{role.value}-agent",
                role=role,
                stance=(
                    "bullish" if index == 0 else "bearish" if index == 1 else "neutral"
                ),
                confidence=0.5,
                arguments=[f"第{round_num}轮{role.value}证据"],
                counterarguments=(
                    [] if round_num == 1 else [f"第{round_num}轮回应其他角色"]
                ),
                peer_reviewed_roles=(
                    [] if round_num == 1 else [roles[(index + 1) % len(roles)].value]
                ),
                rebuttal_records=(
                    []
                    if round_num == 1 or index > 1
                    else [
                        RebuttalRecord(
                            challenged_role=roles[1 - index].value,
                            challenged_claim=f"第{round_num}轮被反驳主张{roles[1 - index].value}",
                            rebuttal_reason="当前证据与该主张方向相反，若该主张成立则当前假设失效",
                            challenged_stance=("bearish" if index == 0 else "bullish"),
                            opposing_stance=("bullish" if index == 0 else "bearish"),
                        )
                    ]
                ),
            )
            for index, role in enumerate(roles)
        ]
        rounds.append(
            DebateRound(
                round_num=round_num,
                opinions=opinions,
                summary=f"第{round_num}轮已记录",
            )
        )

    result = DebateResult(
        debate_id="quality-check",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        deterministic_score=72.0,
        rating="watch",
        related_signal_date="2026-06-30",
        task_id="closing_review",
        rounds=rounds,
        final_consensus="neutral",
        final_vote={role: "neutral" for role in roles},
        support_points=("已记录支持观点",),
        opposition_points=("已记录反对观点",),
        risk_warnings=["已记录风控观点"],
        primary_risk_gate="等待风险确认",
        next_trigger="确认下一轮量价与流动性",
        falsifiable_conditions=("失效条件: 跌破关键支撑则取消当前假设",),
    )

    audit = audit_debate_quality(result, candidate=_make_pick())

    assert audit.passed
    assert audit.process_recorded
    assert audit.conclusion_recorded
    assert audit.next_trigger_recorded
    assert audit.recorded_role_count == 9
    assert audit.historical_evaluation_only


def test_audit_debate_quality_rejects_empty_discussion() -> None:
    result = DebateResult(
        debate_id="empty",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        rating="watch",
    )

    audit = audit_debate_quality(result, candidate=_make_pick())

    assert not audit.passed
    assert not audit.process_recorded
    assert "empty_discussion" in audit.issues
    assert "missing_conclusion" in audit.issues
    assert "missing_next_trigger" in audit.issues


def test_audit_debate_quality_keeps_explicit_empty_cross_market_viewpoint() -> None:
    result = DebateResult(
        debate_id="cross-market-neutral",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        rating="watch",
        rounds=[
            DebateRound(
                round_num=1,
                opinions=[
                    AgentOpinion(
                        agent_id="cross-1",
                        role=AgentRole.CROSS_MARKET,
                        stance="neutral",
                        confidence=0.5,
                        arguments=["无可用跨市消息或规则传导，不据此形成判断"],
                        risk_factors=["保持中性，等待可核验的外部证据"],
                    )
                ],
            ),
            DebateRound(
                round_num=2,
                opinions=[
                    AgentOpinion(
                        agent_id="cross-1",
                        role=AgentRole.CROSS_MARKET,
                        stance="neutral",
                        confidence=0.5,
                        arguments=["无可用跨市消息或规则传导，不据此形成判断"],
                        counterarguments=["当前没有新增跨市证据"],
                        peer_reviewed_roles=[AgentRole.CROSS_MARKET],
                        rebuttal_records=[
                            RebuttalRecord(
                                challenged_role=AgentRole.CROSS_MARKET.value,
                                challenged_claim="当前没有新增跨市证据",
                                rebuttal_reason="同行仍为中性观点，记录为复核而非方向性反驳",
                            )
                        ],
                        risk_factors=["保持中性，等待可核验的外部证据"],
                    )
                ],
            ),
        ],
        final_consensus="跨市证据不足，保持中性",
        final_vote={AgentRole.CROSS_MARKET: "neutral"},
        next_trigger="出现可核验的跨市传导证据",
    )

    audit = audit_debate_quality(
        result,
        expected_roles=(AgentRole.CROSS_MARKET,),
    )

    assert audit.cross_market_recorded
    assert "missing_cross_market_viewpoint" not in audit.issues


def test_audit_debate_quality_rejects_same_symbol_when_date_or_fingerprint_mismatch() -> (
    None
):
    result = DebateResult(
        debate_id="wrong-candidate",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        related_signal_date="2026-07-14",
        candidate_fingerprint="candidate-b",
        rating="watch",
    )
    candidate = _make_pick(
        symbol="300750",
        date="2026-07-15",
        metrics={"candidate_fingerprint": "candidate-a"},
    )

    audit = audit_debate_quality(result, candidate=candidate)

    assert not audit.candidate_mapped
    assert "candidate_unmapped" in audit.issues


def test_audit_debate_quality_rejects_generic_second_round_without_peer_reference() -> (
    None
):
    roles = (AgentRole.BULL, AgentRole.CROSS_MARKET)
    result = DebateResult(
        debate_id="fake-round",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        rating="watch",
        rounds=[
            DebateRound(
                round_num=1,
                opinions=[
                    AgentOpinion(
                        agent_id="bull-1",
                        role=AgentRole.BULL,
                        stance="bullish",
                        confidence=0.6,
                        arguments=["放量突破"],
                    ),
                    AgentOpinion(
                        agent_id="cross-1",
                        role=AgentRole.CROSS_MARKET,
                        stance="neutral",
                        confidence=0.5,
                        arguments=["等待海外映射证据"],
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
                        confidence=0.6,
                        arguments=["放量突破"],
                        counterarguments=["未出现相反观点"],
                    ),
                    AgentOpinion(
                        agent_id="cross-1",
                        role=AgentRole.CROSS_MARKET,
                        stance="neutral",
                        confidence=0.5,
                        arguments=["等待海外映射证据"],
                        counterarguments=["未出现相反观点"],
                    ),
                ],
            ),
        ],
        final_consensus="split",
        final_vote={AgentRole.BULL: "bullish", AgentRole.CROSS_MARKET: "neutral"},
        support_points=("放量突破",),
        opposition_points=("等待海外映射证据",),
        risk_warnings=["等待风险确认"],
        next_trigger="确认A股映射",
    )

    audit = audit_debate_quality(result, expected_roles=roles)

    assert not audit.passed
    assert audit.non_interactive_rounds == (2,)
    assert "non_interactive_round" in audit.issues


def test_audit_debate_quality_rejects_neutral_preserve_original_view_as_rebuttal() -> (
    None
):
    roles = (AgentRole.BULL, AgentRole.CROSS_MARKET)
    result = DebateResult(
        debate_id="neutral-review",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        deterministic_score=72.0,
        related_signal_date="2026-06-30",
        task_id="intraday",
        rating="watch",
        rounds=[
            DebateRound(
                round_num=1,
                opinions=[
                    AgentOpinion(
                        agent_id="bull-1",
                        role=AgentRole.BULL,
                        stance="bullish",
                        confidence=0.7,
                        arguments=["放量突破"],
                    ),
                    AgentOpinion(
                        agent_id="cross-1",
                        role=AgentRole.CROSS_MARKET,
                        stance="neutral",
                        confidence=0.5,
                        arguments=["暂无跨市证据"],
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
                        arguments=["放量突破"],
                        counterarguments=["保留原观点，等待复核"],
                        peer_reviewed_roles=["cross_market"],
                        rebuttal_records=[
                            RebuttalRecord(
                                challenged_role="cross_market",
                                challenged_claim="暂无跨市证据",
                                rebuttal_reason="保留该主张并记录为待复核依据",
                            )
                        ],
                    ),
                    AgentOpinion(
                        agent_id="cross-1",
                        role=AgentRole.CROSS_MARKET,
                        stance="neutral",
                        confidence=0.5,
                        arguments=["暂无跨市证据"],
                        counterarguments=["保留原观点"],
                        counterargument_roles=["bull"],
                    ),
                ],
            ),
        ],
        final_consensus="neutral",
        final_vote={AgentRole.BULL: "bullish", AgentRole.CROSS_MARKET: "neutral"},
        support_points=("放量突破",),
        opposition_points=("暂无跨市证据",),
        risk_warnings=["失效条件: A股板块不共振"],
        next_trigger="确认A股映射",
        falsifiable_conditions=("失效条件: A股板块不共振",),
    )

    audit = audit_debate_quality(result, expected_roles=roles)

    assert not audit.passed
    assert audit.non_interactive_rounds == (2,)
    assert "missing_real_opposition" in audit.issues


def test_debate_result_serializes_falsifiable_conditions_and_rebuttal_stances() -> None:
    pick = _make_pick(
        metrics={
            "candidate_fingerprint": "fp-1",
            "cross_market_invalidation_signals": ("跌破关键支撑",),
        }
    )
    result = AShareDebateCoordinator(
        task_id="intraday",
        max_rounds=2,
        roles=(AgentRole.BULL, AgentRole.BEAR),
    ).run_debate(pick, pd.DataFrame({"close": [100.0, 101.0]}), signal_date=pick.date)

    payload = result.to_dict()

    assert "falsifiable_conditions" in payload
    assert payload["deterministic_score"] == pick.score
    records = [
        record
        for round_data in payload["rounds"]
        for opinion in round_data["opinions"]
        for record in opinion["rebuttal_records"]
    ]
    assert records
    assert all(
        record["challenged_stance"] != record["opposing_stance"] for record in records
    )


def test_audit_debate_quality_rejects_task_mismatch_even_when_symbol_and_date_match() -> (
    None
):
    result = DebateResult(
        debate_id="task-mismatch",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        deterministic_score=72.0,
        related_signal_date="2026-06-30",
        task_id="closing_review",
        rating="watch",
    )
    candidate = _make_pick(
        metrics={"candidate_fingerprint": "fp-1", "task_id": "intraday"}
    )

    audit = audit_debate_quality(result, candidate=candidate)

    assert not audit.candidate_mapped
    assert "candidate_unmapped" in audit.issues


def test_audit_debate_quality_accepts_legacy_json_role_references_without_rebuttals() -> (
    None
):
    result = {
        "symbol": "300750",
        "rounds": [
            {
                "round_num": 1,
                "opinions": [
                    {
                        "agent_id": "bull-1",
                        "role": "bull",
                        "stance": "bullish",
                        "arguments": ["放量突破"],
                    },
                    {
                        "agent_id": "cross-1",
                        "role": "cross_market",
                        "stance": "neutral",
                        "arguments": ["等待海外映射证据"],
                    },
                ],
            },
            {
                "round_num": 2,
                "opinions": [
                    {
                        "agent_id": "bull-1",
                        "role": "bull",
                        "stance": "bullish",
                        "arguments": ["放量突破"],
                        "counterarguments": ["复核跨市证据"],
                        "peer_reviewed_roles": ["cross_market"],
                    },
                    {
                        "agent_id": "cross-1",
                        "role": "cross_market",
                        "stance": "neutral",
                        "arguments": ["等待海外映射证据"],
                        "counterarguments": ["复核多头观点"],
                        "counterargument_roles": ["bull"],
                    },
                ],
            },
        ],
        "final_consensus": "neutral",
        "final_vote": {"bull": "bullish", "cross_market": "neutral"},
        "support_points": ["放量突破"],
        "opposition_points": ["等待海外映射证据"],
        "risk_warnings": ["等待风险确认"],
        "next_trigger": "确认A股映射",
    }

    audit = audit_debate_quality(
        result,
        expected_roles=(AgentRole.BULL, AgentRole.CROSS_MARKET),
    )

    assert audit.non_interactive_rounds == (2,)
    assert "missing_real_opposition" in audit.issues


def test_audit_debate_quality_rejects_non_advisory_result() -> None:
    pick = _make_pick()
    result = AShareDebateCoordinator(
        max_rounds=2,
        roles=(AgentRole.BULL, AgentRole.RISK_CONTROL),
    ).run_debate(pick, pd.DataFrame({"close": [100.0, 101.0]}))
    result.advisory_only = False

    audit = audit_debate_quality(
        result,
        expected_roles=(AgentRole.BULL, AgentRole.RISK_CONTROL),
    )

    assert not audit.passed
    assert audit.advisory_boundary_ok is False
    assert "advisory_boundary_violation" in audit.issues


def test_debate_coordinator_marks_empty_market_data_without_changing_score() -> None:
    pick = _make_pick(score=72.0)
    result = AShareDebateCoordinator(
        max_rounds=2,
        roles=(
            AgentRole.BULL,
            AgentRole.BEAR,
            AgentRole.RISK_CONTROL,
            AgentRole.SECTOR_LEADER,
            AgentRole.CROSS_MARKET,
            AgentRole.POLICY_SENSITIVE,
        ),
    ).run_debate(pick, pd.DataFrame())

    assert result.data_status == "empty"
    assert "行情数据为空" in result.data_note
    assert "empty_market_data" in result.failure
    assert result.research_verdict == "结论阻断：行情数据为空，仅记录待补行情。"
    assert result.to_dict()["process_recorded"] is False
    assert result.deterministic_score == pick.score
    assert result.deterministic_score_unchanged is True
    assert result.advisory_only is True


def test_debate_result_serializes_role_interactions_without_model_script() -> None:
    result = AShareDebateCoordinator(
        max_rounds=2,
        roles=(
            AgentRole.BULL,
            AgentRole.BEAR,
            AgentRole.RISK_CONTROL,
            AgentRole.SECTOR_LEADER,
            AgentRole.CROSS_MARKET,
            AgentRole.POLICY_SENSITIVE,
        ),
    ).run_debate(
        _make_pick(score=72.0),
        pd.DataFrame({"close": [100.0, 101.0, 102.0]}),
    )

    payload = result.to_dict()
    second_round = payload["rounds"][1]

    assert second_round["interaction_pairs"]
    assert ["risk_control", "bull"] in second_round["interaction_pairs"]
    assert payload["score_boundary"] == {
        "deterministic_score": 72.0,
        "original_score": 72.0,
        "unchanged": True,
        "advisory_only": True,
    }
    rendered = format_debate_result(result)
    assert "质询/复核对象:" in rendered
    assert "反驳意见" not in rendered
    assert "质询对象=" not in rendered


def test_debate_performance_tracker_ignores_empty_predictions_and_old_tasks(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "current")
    storage_path = str(tmp_path / "debate_performance.jsonl")
    tracker = DebatePerformanceTracker(storage_path=storage_path)

    tracker.record_prediction(
        AgentRole.BULL,
        "bull-current",
        "bullish",
        was_correct=True,
    )
    tracker.record_prediction(
        AgentRole.BULL,
        "bull-old",
        "bullish",
        was_correct=False,
        task_id="old",
    )
    tracker.record_prediction(
        AgentRole.BULL,
        "bull-empty",
        "",
        was_correct=True,
    )

    current = DebatePerformanceTracker(storage_path=storage_path)
    metrics = current.get_agent_metrics(AgentRole.BULL, "bull-current")

    assert metrics.total_predictions == 1
    assert current.get_agent_metrics(AgentRole.BULL, "bull-old").total_predictions == 0
    breakdown = current.get_context_breakdown(AgentRole.BULL)
    assert breakdown[0].bucket == "unknown"
    assert breakdown[0].sample_count == 1


def test_debate_performance_tracker_isolates_explicit_task_history(tmp_path) -> None:
    storage_path = tmp_path / "debate_performance.jsonl"
    rows = []
    signal_date = now_shanghai().date().isoformat()
    created_at = f"{signal_date}T09:00:00+08:00"
    for task_id, was_correct in (("intraday", True), ("closing_review", False)):
        rows.append(
            {
                "agent_id": "bull-shared",
                "role": AgentRole.BULL.value,
                "predicted_stance": "bullish",
                "was_correct": was_correct,
                "task_id": task_id,
                "debate_id": f"debate-{task_id}",
                "signal_date": signal_date,
                "candidate_fingerprint": f"candidate-{task_id}",
                "created_at": created_at,
            }
        )
    storage_path.write_text(
        "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
    )

    intraday = DebatePerformanceTracker(
        storage_path=str(storage_path), task_id="intraday"
    )
    closing = DebatePerformanceTracker(
        storage_path=str(storage_path), task_id="closing_review"
    )
    unscoped = DebatePerformanceTracker(storage_path=str(storage_path), task_id=None)

    assert (
        intraday.get_agent_metrics(AgentRole.BULL, "bull-shared").correct_predictions
        == 1
    )
    assert (
        closing.get_agent_metrics(AgentRole.BULL, "bull-shared").correct_predictions
        == 0
    )
    assert (
        unscoped.get_agent_metrics(AgentRole.BULL, "bull-shared").total_predictions == 0
    )


def test_debate_performance_tracker_requires_independent_days_and_cooldown(
    tmp_path,
) -> None:
    storage_path = tmp_path / "debate_performance.jsonl"
    rows = []
    base_date = now_shanghai().date()
    signal_dates = tuple(
        (base_date - timedelta(days=offset)).isoformat() for offset in (15, 14, 13)
    )
    for index, signal_date in enumerate(signal_dates):
        for duplicate in range(2):
            rows.append(
                {
                    "agent_id": "bull-qualified",
                    "role": AgentRole.BULL.value,
                    "predicted_stance": "bullish",
                    "was_correct": True,
                    "task_id": "intraday",
                    "debate_id": f"debate-{index}-{duplicate}",
                    "signal_date": signal_date,
                    "candidate_fingerprint": f"candidate-{index}-{duplicate}",
                    "created_at": f"{signal_date}T09:00:00+08:00",
                }
            )
    storage_path.write_text(
        "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
    )
    tracker = DebatePerformanceTracker(
        storage_path=str(storage_path), task_id="intraday"
    )
    qualified = tracker.calculate_adjustment_weight(AgentRole.BULL, "bull-qualified")
    assert qualified > 0

    tracker._agent_latest_record_at["bull_bull-qualified"] = now_shanghai()
    assert tracker.calculate_adjustment_weight(AgentRole.BULL, "bull-qualified") == 0.0


def test_debate_coordinator_deadline_reports_block_without_changing_score(
    monkeypatch,
) -> None:
    original = AShareDebateAgent.generate_initial_opinion

    def slow_initial(self, pick, df, *, market_context_lines=()):
        time.sleep(0.01)
        return original(self, pick, df, market_context_lines=market_context_lines)

    monkeypatch.setattr(AShareDebateAgent, "generate_initial_opinion", slow_initial)
    heartbeats: list[int] = []
    pick = _make_pick(score=72.0)
    result = AShareDebateCoordinator(
        max_rounds=2,
        roles=(AgentRole.BULL, AgentRole.RISK_CONTROL),
        task_id="intraday",
        debate_deadline_seconds=0.001,
        heartbeat_callback=lambda: heartbeats.append(1),
    ).run_debate(pick, pd.DataFrame({"close": [100.0, 101.0]}))

    assert result.deadline_exceeded is True
    assert result.runtime_status == "blocked"
    assert result.deterministic_score == 72.0
    assert result.deterministic_score_unchanged is True
    assert result.advisory_only is True
    assert result.task_id == "intraday"
    assert result.heartbeat_count >= 1
    assert heartbeats
