"""PM debate 阻断门测试：debate 结论首次获得真实裁决权（仅标签层，不改 score）。"""

from __future__ import annotations

import pytest

from aqsp.core.types import PickResult
from aqsp.portfolio.manager import apply_portfolio_manager


def _pick(
    symbol: str,
    score: float,
    *,
    debate_consensus: str = "",
    metrics: dict[str, object] | None = None,
) -> PickResult:
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
        metrics=metrics or {},
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


def test_block_word_in_verdict_blocks_candidate() -> None:
    picks = [
        _pick(
            "600036",
            70,
            debate_consensus="bullish",
            metrics={
                "debate_disagreement_score": 0.2,
                "debate_research_verdict": "技术形态完好，但午后出现失效检验信号",
            },
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "observation_only"
    assert bundle.picks[0].metrics["debate_action_influence"] == "blocked"
    assert any("失效检验" in reason for reason in bundle.decisions[0].reasons)


def test_block_word_in_risk_gate_blocks_candidate() -> None:
    picks = [
        _pick(
            "600036",
            70,
            metrics={
                "debate_disagreement_score": 0.1,
                "debate_primary_risk_gate": "跌破止损位，假设失效",
            },
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "observation_only"
    assert bundle.picks[0].metrics["debate_action_influence"] == "blocked"


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

    assert bundle.picks[0].score == 65
    assert bundle.picks[0].metrics["portfolio_action"] == "downgrade"
    assert bundle.picks[0].metrics["debate_action_influence"] == "downgraded"
    assert bundle.decisions[0].action == "downgrade"
    assert any("bearish" in reason for reason in bundle.decisions[0].reasons)


def test_mid_disagreement_alone_downgrades() -> None:
    picks = [
        _pick(
            "000001",
            65,
            debate_consensus="neutral",
            metrics={"debate_disagreement_score": 0.55},
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "downgrade"
    assert bundle.picks[0].metrics["debate_action_influence"] == "downgraded"
    assert any("0.55" in reason for reason in bundle.decisions[0].reasons)


def test_bullish_low_disagreement_keeps() -> None:
    picks = [
        _pick(
            "300750",
            80,
            debate_consensus="bullish",
            metrics={"debate_disagreement_score": 0.2},
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "keep"
    assert bundle.picks[0].metrics["debate_action_influence"] == "none"


def test_missing_debate_fields_marked_no_debate() -> None:
    picks = [_pick("600036", 70)]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "keep"
    assert bundle.picks[0].metrics["debate_action_influence"] == "no_debate"


def test_quality_gate_observation_only_is_not_overridden() -> None:
    picks = [
        _pick(
            "600036",
            70,
            metrics={
                "observation_only": True,
                "debate_disagreement_score": 0.9,
                "debate_research_verdict": "失效检验已出现",
            },
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "observation_only"
    assert bundle.picks[0].metrics["debate_action_influence"] == "none"
    assert not any(
        "debate 阻断门" in reason for reason in bundle.decisions[0].reasons
    )


def test_existing_downgrade_is_not_overridden_by_debate_gate() -> None:
    picks = [
        _pick(
            "600036",
            70,
            debate_consensus="bearish",
            metrics={"debate_disagreement_score": 0.9},
        )
    ]

    bundle = apply_portfolio_manager(picks, thresholds=None)  # 默认阈值

    # 无集中度/相关性输入时不会 downgrade，此处高分歧应阻断为 observation_only
    assert bundle.decisions[0].action == "observation_only"
    assert bundle.picks[0].metrics["debate_action_influence"] == "blocked"


def test_env_block_words_override_default_words(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_PM_DEBATE_BLOCK_WORDS", "自定义雷点")
    picks = [
        _pick(
            "600036",
            70,
            metrics={
                "debate_disagreement_score": 0.1,
                "debate_research_verdict": "出现失效检验，默认阻断语应被覆盖",
            },
        ),
        _pick(
            "000001",
            68,
            metrics={
                "debate_disagreement_score": 0.1,
                "debate_primary_risk_gate": "盘中触及自定义雷点",
            },
        ),
    ]

    bundle = apply_portfolio_manager(picks)
    decisions = {item.symbol: item for item in bundle.decisions}
    influences = {
        item.symbol: item.metrics["debate_action_influence"] for item in bundle.picks
    }

    # 默认阻断语被覆盖后不再命中
    assert decisions["600036"].action == "keep"
    assert influences["600036"] == "none"
    # 自定义阻断语生效
    assert decisions["000001"].action == "observation_only"
    assert influences["000001"] == "blocked"


def test_env_thresholds_override(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_PM_DEBATE_HIGH_DISAGREEMENT", "0.9")
    monkeypatch.setenv("AQSP_PM_DEBATE_MID_DISAGREEMENT", "0.3")
    picks = [
        _pick("600036", 70, metrics={"debate_disagreement_score": 0.8}),
    ]

    bundle = apply_portfolio_manager(picks)

    # 0.8 < 0.9 不阻断；0.8 >= 0.3 降级
    assert bundle.decisions[0].action == "downgrade"
    assert bundle.picks[0].metrics["debate_action_influence"] == "downgraded"


def test_gate_switch_off_disables_debate_gate(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_PM_DEBATE_GATE", "0")
    picks = [
        _pick(
            "600036",
            70,
            debate_consensus="bearish",
            metrics={"debate_disagreement_score": 0.9},
        )
    ]

    bundle = apply_portfolio_manager(picks)

    assert bundle.decisions[0].action == "keep"
    assert bundle.picks[0].metrics["debate_action_influence"] == "none"


def test_ledger_row_carries_debate_action_influence(tmp_path) -> None:
    from aqsp.ledger.base import append_predictions, read_ledger

    picks = apply_portfolio_manager(
        [_pick("600036", 70, metrics={"debate_disagreement_score": 0.8})]
    ).picks
    ledger = tmp_path / "predictions.jsonl"

    append_predictions(ledger, picks)

    row = read_ledger(ledger)[0]
    assert row["debate_action_influence"] == "blocked"
    assert row["portfolio_action"] == "observation_only"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, None), ("", None), ("0.8", 0.8), ("not-a-number", None), (0.5, 0.5)],
)
def test_parse_debate_disagreement_is_none_safe(raw, expected) -> None:
    from aqsp.portfolio.manager import _parse_debate_disagreement

    assert _parse_debate_disagreement({"debate_disagreement_score": raw}) == expected
