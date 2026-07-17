from __future__ import annotations

from argparse import Namespace
import json
from types import SimpleNamespace

from aqsp import cli
from aqsp.runtime_snapshot import (
    RuntimeSnapshotDebate,
    _snapshot_candidates,
    build_runtime_research_snapshot,
)


def test_runtime_snapshot_maps_same_symbol_debates_by_candidate_fingerprint() -> None:
    cards = tuple(
        SimpleNamespace(
            symbol="300750",
            candidate_fingerprint=fingerprint,
            display_name="300750 宁德时代",
            score=score,
            rank_label="观察",
            action_label="继续观察",
            status_label="",
            next_step="",
            blocker="",
            reasons=(),
            risks=(),
            news_catalyst_summary="",
            cross_market_summary="",
            decision_note=fingerprint,
        )
        for fingerprint, score in (("candidate-a", 80.0), ("candidate-b", 60.0))
    )
    debates = tuple(
        RuntimeSnapshotDebate(
            symbol="300750",
            display_name="300750 宁德时代",
            conclusion=fingerprint,
            recommended_adjustment="keep",
            disagreement_score=0.0,
            primary_risk_gate="",
            next_trigger="",
            active_roles=(),
            support_points=(),
            opposition_points=(),
            watch_items=(),
            process_recorded=True,
            conclusion_recorded=True,
            candidate_fingerprint=fingerprint,
        )
        for fingerprint in ("candidate-b", "candidate-a")
    )

    candidates = _snapshot_candidates(
        SimpleNamespace(
            task_view=SimpleNamespace(detail_cards=cards),
            spotlights=(),
        ),
        debates,
    )

    assert [item.decision_note for item in candidates] == [
        "candidate-a",
        "candidate-b",
    ]
    assert [item.debate_status for item in candidates] == ["recorded", "recorded"]


def test_runtime_snapshot_builds_shared_agent_payload_from_one_home_digest() -> None:
    candidate = SimpleNamespace(
        symbol="603019",
        display_name="603019 中科曙光",
        score=72.5,
        rank_label="第一顺位",
        action_label="纸面复核",
        status_label="新晋",
        next_step="确认量能承接",
        blocker="",
        reasons=("放量突破",),
        risks=("高位波动",),
        news_catalyst_summary="算力催化",
        cross_market_summary="海外算力映射",
        decision_note="等待确认",
    )
    spotlight = SimpleNamespace(
        symbol="600879",
        display_name="600879 航天电子",
        score=65.0,
        rank_label="同日联动",
        action_label="继续观察",
        status_label="等待确认",
        next_step="确认板块共振",
        blocker="",
        reasons=(),
        risks=(),
        news_catalyst_summary="商业航天催化",
        cross_market_summary="",
        decision_note="",
    )
    debate = SimpleNamespace(
        symbol="603019",
        display_name="603019 中科曙光",
        research_verdict="维持纸面复核",
        consensus="",
        recommended_adjustment="keep",
        disagreement_score=0.2,
        primary_risk_gate="量能承接",
        next_trigger="放量确认",
        agent_views=(SimpleNamespace(role_id="cross_market"),),
        support_points=("海外映射仍在",),
        opposition_points=("高位分歧",),
        watch_items=("确认板块共振",),
    )

    class _Provider:
        def default_task_id(self) -> str:
            return "main_chain"

        def home_digest_payload(self, task_id: str, signal_date: str = ""):
            assert task_id == "main_chain"
            return SimpleNamespace(
                task_view=SimpleNamespace(
                    task_id="main_chain",
                    task_label="主链推荐",
                    selected_date="2026-07-10",
                    latest_date="",
                    detail_cards=(candidate,),
                ),
                spotlights=(spotlight,),
                debates=(debate,),
                overview=SimpleNamespace(
                    actionable_total=1,
                    watch_total=1,
                    blocked_total=0,
                ),
            )

        def runtime_overview(self, signal_date: str = ""):
            assert signal_date == "2026-07-10"
            return SimpleNamespace(
                conclusion="最近运行已落盘",
                requested_source="online_first",
                effective_source="sina",
                source_reason="fallback 到 sina",
                data_latest_trade_date="2026-07-10",
                lag_days="0",
                market_context_runtime_line="海外算力主线",
                risk_reason="等待量能确认",
            )

    snapshot = build_runtime_research_snapshot(_Provider())
    payload = json.loads(snapshot.to_json())

    assert payload["schema_version"] == "v1"
    assert payload["signal_date"] == "2026-07-10"
    assert payload["source"]["effective"] == "sina"
    assert payload["candidate_counts"] == {
        "actionable": 1,
        "watch": 1,
        "blocked": 0,
    }
    assert [item["symbol"] for item in payload["candidates"]] == [
        "603019",
        "600879",
    ]
    assert payload["debates"][0]["active_roles"] == ["cross_market"]
    assert payload["debates"][0]["conclusion"] == "结论已阻断：讨论链路不完整"
    assert len(payload["debate_failures"]) == 1
    assert payload["debate_failures"][0].startswith("603019(讨论链路未通过审计:")
    assert "empty_discussion" in payload["debate_failures"][0]
    assert "委员会结论不改写确定性评分" in payload["guardrails"][0]


def test_runtime_snapshot_dedupes_spotlight_already_in_task_cards() -> None:
    candidate = SimpleNamespace(
        symbol="603019",
        display_name="603019 中科曙光",
        score=72.5,
        rank_label="第一顺位",
        action_label="纸面复核",
        status_label="",
        next_step="",
        blocker="",
        reasons=(),
        risks=(),
        news_catalyst_summary="",
        cross_market_summary="",
        decision_note="",
    )

    class _Provider:
        def default_task_id(self) -> str:
            return "main_chain"

        def home_digest_payload(self, task_id: str, signal_date: str = ""):
            return SimpleNamespace(
                task_view=SimpleNamespace(
                    task_id="main_chain",
                    task_label="主链推荐",
                    selected_date="2026-07-10",
                    latest_date="",
                    detail_cards=(candidate,),
                ),
                spotlights=(candidate,),
                debates=(),
                overview=SimpleNamespace(
                    actionable_total=1,
                    watch_total=0,
                    blocked_total=0,
                ),
            )

        def runtime_overview(self, signal_date: str = ""):
            return SimpleNamespace()

    snapshot = build_runtime_research_snapshot(_Provider())

    assert len(snapshot.candidates) == 1


def test_runtime_snapshot_keeps_round_process_and_candidate_role_coverage() -> None:
    candidate = SimpleNamespace(
        symbol="300750",
        display_name="300750 宁德时代",
        score=72.0,
        rank_label="第一顺位",
        action_label="纸面复核",
        status_label="",
        next_step="确认量能",
        blocker="",
        reasons=("放量突破",),
        risks=("追高波动",),
        news_catalyst_summary="",
        cross_market_summary="海外物理AI映射",
        decision_note="",
    )
    debates = [
        {
            "symbol": "300750",
            "name": "宁德时代",
            "research_verdict": "倾向继续观察",
            "consensus": "neutral",
            "final_vote": {"bull": "neutral", "cross_market": "neutral"},
            "agent_views": [],
            "rounds": [
                {
                    "round_num": 1,
                    "summary": "首轮形成观点",
                    "opinions": [
                        {
                            "agent_id": "bull-1",
                            "role": "bull",
                            "stance": "neutral",
                            "confidence": 0.5,
                            "arguments": ["放量突破"],
                            "counterarguments": [],
                            "peer_reviewed_roles": [],
                            "risk_factors": [],
                            "opportunity_factors": [],
                        },
                        {
                            "agent_id": "cross-1",
                            "role": "cross_market",
                            "stance": "neutral",
                            "confidence": 0.5,
                            "arguments": ["海外映射待确认"],
                            "counterarguments": [],
                            "peer_reviewed_roles": [],
                            "risk_factors": ["海外叙事可能不传导"],
                            "opportunity_factors": [],
                        },
                    ],
                },
                {
                    "round_num": 2,
                    "summary": "二轮回应",
                    "opinions": [
                        {
                            "agent_id": "bull-1",
                            "role": "bull",
                            "stance": "neutral",
                            "confidence": 0.5,
                            "arguments": ["放量突破仍需承接"],
                            "counterarguments": ["已复核跨市观点"],
                            "counterargument_roles": ["cross_market"],
                            "peer_reviewed_roles": ["cross_market"],
                            "risk_factors": ["高开回撤"],
                            "opportunity_factors": [],
                        },
                        {
                            "agent_id": "cross-1",
                            "role": "cross_market",
                            "stance": "neutral",
                            "confidence": 0.5,
                            "arguments": ["等待A股映射确认"],
                            "counterarguments": ["已复核技术观点"],
                            "counterargument_roles": ["bull"],
                            "peer_reviewed_roles": ["bull"],
                            "risk_factors": ["海外叙事可能不传导"],
                            "opportunity_factors": [],
                        },
                    ],
                },
            ],
            # Deliberately lie in the input flags; snapshot construction must
            # recompute them from the recorded rounds and vote.
                "process_recorded": False,
                "conclusion_recorded": False,
                "advisory_only": True,
                "original_score": 72.0,
                "deterministic_score": 72.0,
            "deterministic_score_unchanged": True,
            "support_points": ["放量突破"],
            "opposition_points": ["等待确认"],
            "watch_items": ["确认量能"],
            "next_trigger": "确认量能",
        }
    ]

    class _Provider:
        def default_task_id(self) -> str:
            return "intraday"

        def home_digest_payload(self, task_id: str, signal_date: str = ""):
            return SimpleNamespace(
                task_view=SimpleNamespace(
                    task_id=task_id,
                    task_label="盘中",
                    selected_date="2026-07-14",
                    detail_cards=(candidate,),
                ),
                spotlights=(),
                debates=debates,
                overview=SimpleNamespace(
                    actionable_total=1,
                    watch_total=0,
                    blocked_total=0,
                ),
            )

        def runtime_overview(self, signal_date: str = ""):
            return SimpleNamespace()

    snapshot = build_runtime_research_snapshot(_Provider())

    assert snapshot.debates[0].active_roles == ("bull", "cross_market")
    assert len(snapshot.debates[0].rounds) == 2
    assert snapshot.debates[0].process_recorded is True
    assert snapshot.debates[0].conclusion_recorded is True
    assert snapshot.debates[0].advisory_only is True
    assert snapshot.candidates[0].debate_round_count == 2
    assert snapshot.candidates[0].debate_roles == ("bull", "cross_market")
    assert snapshot.candidates[0].debate_status == "recorded"


def test_runtime_snapshot_accepts_missing_debate_collection_as_empty() -> None:
    class _Provider:
        def default_task_id(self) -> str:
            return "main_chain"

        def home_digest_payload(self, task_id: str, signal_date: str = ""):
            return {
                "task_view": {
                    "task_id": task_id,
                    "task_label": "主链",
                    "selected_date": "2026-07-14",
                    "detail_cards": (),
                },
                "overview": {
                    "actionable_total": 0,
                    "watch_total": 0,
                    "blocked_total": 0,
                },
            }

        def runtime_overview(self, signal_date: str = ""):
            return {}

    snapshot = build_runtime_research_snapshot(_Provider())

    assert snapshot.debates == ()


def test_cli_runtime_snapshot_writes_machine_readable_payload(
    monkeypatch, tmp_path
) -> None:
    captured: dict[str, object] = {}
    snapshot = SimpleNamespace(to_json=lambda: '{"schema_version":"v1"}')

    def _build(provider, *, signal_date: str, task_id: str):
        captured["provider"] = provider
        captured["signal_date"] = signal_date
        captured["task_id"] = task_id
        return snapshot

    monkeypatch.setattr(cli, "build_runtime_research_snapshot", _build)
    output = tmp_path / "runtime-snapshot.json"

    result = cli.run_runtime_snapshot(
        Namespace(date="2026-07-10", task_id="intraday", output=str(output))
    )

    assert result == 0
    assert captured["signal_date"] == "2026-07-10"
    assert captured["task_id"] == "intraday"
    assert output.read_text(encoding="utf-8") == '{"schema_version":"v1"}\n'
