"""PM debate 阻断门测试：debate 结论首次获得真实裁决权（仅标签层，不改 score）。

阻断信号一律用结构化字段（debate_risk_veto_applied / disagreement_score / consensus），
且**仅在 debate 由 LLM 实际驱动（debate_llm_enabled）时生效**——规则化 debate 的
「失效检验…」是每票皆有的样板句，关键词匹配会把整条管道打成 observation_only
（2026-09-25 test_run_scheduled_persists_decision_audit_log 事故实证）。
"""

from __future__ import annotations

from aqsp.core.types import PickResult
from aqsp.portfolio.manager import apply_portfolio_manager


def _pick(
    symbol: str,
    score: float,
    *,
    debate_consensus: str = "",
    llm: bool = True,
    metrics: dict[str, object] | None = None,
) -> PickResult:
    merged = {"debate_llm_enabled": llm}
    merged.update(metrics or {})
    return PickResult(
        symbol=symbol,
        name=symbol,
        date="2026-09-25",
        close=10.0,
        score=score,
        rating="buy_candidate",
        entry_type="close",
        ideal_buy=10.0,
        stop_loss=9.5,
        take_profit=11.0,
        position="10%-30%",
        debate_consensus=debate_consensus,
        metrics=merged,
    )


def test_high_disagreement_blocks_candidate(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_PM_DEBATE_HIGH_DISAGREEMENT", "0.75")
    picks = [
        _pick(
            "300750",
            82,
            debate_consensus="bullish",
            metrics={"debate_disagreement_score": 0.8},
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.picks[0].score == 82  # 红线：score 不被改写
    assert bundle.picks[0].metrics["portfolio_action"] == "observation_only"
    assert bundle.picks[0].metrics["debate_action_influence"] == "blocked"
    assert bundle.decisions[0].action == "observation_only"
    assert any("0.80" in reason for reason in bundle.decisions[0].reasons)


def test_structured_risk_veto_blocks_candidate() -> None:
    """风控角色结构化否决（risk_veto_applied）→ observation_only。"""
    picks = [
        _pick(
            "600036",
            70,
            debate_consensus="bullish",
            metrics={
                "debate_disagreement_score": 0.2,
                "debate_risk_veto_applied": True,
                "debate_risk_veto_reason": "风控角色明确看空",
            },
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "observation_only"
    assert bundle.picks[0].metrics["debate_action_influence"] == "blocked"
    assert any("风控角色结构化否决" in reason for reason in bundle.decisions[0].reasons)


def test_prose_block_words_no_longer_trigger(monkeypatch) -> None:
    """回归：规则化 debate 的「失效检验…」样板句不得触发阻断（09-25 事故）。

    关键词匹配已移除；LLM 未启用时规则 debate 仅记 rules_only，不动 action。
    """
    picks = [
        _pick(
            "600036",
            70,
            debate_consensus="bullish",
            llm=False,
            metrics={
                "debate_disagreement_score": 0.2,
                "debate_research_verdict": "技术形态完好，但午后出现失效检验信号",
                "debate_primary_risk_gate": "跌破止损位，假设失效",
            },
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "keep"
    assert bundle.picks[0].metrics["debate_action_influence"] == "rules_only"


def test_bearish_with_mid_disagreement_downgrades() -> None:
    picks = [
        _pick(
            "000001",
            65,
            debate_consensus="bearish",
            metrics={"debate_disagreement_score": 0.6},
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "downgrade"
    assert bundle.picks[0].metrics["debate_action_influence"] == "downgraded"
    assert bundle.picks[0].score == 65


def test_mid_disagreement_alone_downgrades() -> None:
    picks = [
        _pick(
            "000001",
            65,
            debate_consensus="neutral",
            metrics={"debate_disagreement_score": 0.5},
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "downgrade"
    assert bundle.picks[0].metrics["debate_action_influence"] == "downgraded"


def test_bullish_low_disagreement_keeps() -> None:
    picks = [
        _pick(
            "600519",
            80,
            debate_consensus="bullish",
            metrics={"debate_disagreement_score": 0.1},
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "keep"
    assert bundle.picks[0].metrics["debate_action_influence"] == "none"


def test_missing_debate_fields_marked_no_debate() -> None:
    picks = [_pick("600519", 80, metrics={})]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "keep"
    assert bundle.picks[0].metrics["debate_action_influence"] == "no_debate"


def test_quality_gate_observation_only_is_not_overridden() -> None:
    """已被质量门 observation_only 的候选不被 debate 门改写。"""
    picks = [
        _pick(
            "600036",
            70,
            debate_consensus="bullish",
            metrics={
                "observation_only": True,
                "debate_disagreement_score": 0.9,
            },
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "observation_only"
    assert bundle.picks[0].metrics["debate_action_influence"] == "none"
    assert not any(
        "debate 阻断门" in reason for reason in bundle.decisions[0].reasons
    )


def test_gate_does_not_escalate_when_action_already_constrained() -> None:
    """阻断门只把 keep 提级；对已有 observation_only 的候选不叠加理由。"""
    picks = [
        _pick(
            "600036",
            70,
            debate_consensus="bearish",
            metrics={
                "observation_only": True,
                "debate_disagreement_score": 0.9,
            },
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "observation_only"
    assert not any("debate 阻断门" in reason for reason in bundle.decisions[0].reasons)


def test_env_thresholds_override(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_PM_DEBATE_HIGH_DISAGREEMENT", "0.9")
    monkeypatch.setenv("AQSP_PM_DEBATE_MID_DISAGREEMENT", "0.3")
    picks = [
        _pick(
            "000001",
            65,
            debate_consensus="neutral",
            metrics={"debate_disagreement_score": 0.4},
        )
    ]

    bundle = apply_portfolio_manager(picks)

    # 0.4 ≥ 自定义 mid 0.3 → 降级；未达自定义 high 0.9 → 不阻断
    assert bundle.decisions[0].action == "downgrade"
    assert bundle.picks[0].metrics["debate_action_influence"] == "downgraded"


def test_gate_switch_off_disables_debate_gate(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_PM_DEBATE_GATE", "0")
    picks = [
        _pick(
            "300750",
            82,
            debate_consensus="bullish",
            metrics={"debate_disagreement_score": 0.9},
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "keep"
    assert bundle.picks[0].metrics["debate_action_influence"] == "none"


def test_ledger_row_carries_debate_action_influence(tmp_path) -> None:
    """influence / veto / llm 标记必须随账本行落盘，供复盘对账。"""
    import json

    from aqsp.ledger.base import append_predictions

    picks = [
        _pick(
            "600519",
            80,
            debate_consensus="bullish",
            metrics={
                "debate_disagreement_score": 0.8,
                "debate_action_influence": "blocked",
                "debate_risk_veto_applied": True,
                "debate_llm_enabled": True,
            },
        )
    ]
    ledger = tmp_path / "predictions.jsonl"
    append_predictions(str(ledger), picks)

    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert row["debate_action_influence"] == "blocked"
    assert row["debate_risk_veto_applied"] is True
    assert row["debate_llm_enabled"] is True


def test_parse_debate_disagreement_is_none_safe() -> None:
    from aqsp.portfolio.manager import _parse_debate_disagreement

    raw_expected = [
        ({}, None),
        ({"debate_disagreement_score": ""}, None),
        ({"debate_disagreement_score": "abc"}, None),
        ({"debate_disagreement_score": "0.55"}, 0.55),
        ({"debate_disagreement_score": 0.4}, 0.4),
    ]
    for raw, expected in raw_expected:
        assert _parse_debate_disagreement(raw) == expected
