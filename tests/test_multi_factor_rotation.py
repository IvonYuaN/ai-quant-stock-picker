from __future__ import annotations

from aqsp.strategies.multi_factor_rotation import MultiFactorRotationStrategy


def test_factor_weight_update_is_proposal_only_when_evidence_passes() -> None:
    strategy = MultiFactorRotationStrategy()
    before = dict(strategy._factor_weights)

    proposal = strategy.update_factor_weights(
        {"rsi_14": 1.0},
        independent_signal_days=30,
        cooldown_active=False,
        walkforward_evidence={"status": "pass"},
    )

    assert proposal is not None
    assert proposal.proposal_only is True
    assert proposal.walkforward_status == "pass"
    assert dict(strategy._factor_weights) == before


def test_factor_weight_update_is_blocked_without_samples_or_gate() -> None:
    strategy = MultiFactorRotationStrategy()

    assert (
        strategy.update_factor_weights(
            {"rsi_14": 1.0},
            independent_signal_days=29,
            cooldown_active=False,
            walkforward_evidence={"status": "pass"},
        )
        is None
    )
    assert (
        strategy.update_factor_weights(
            {"rsi_14": 1.0},
            independent_signal_days=30,
            cooldown_active=False,
            walkforward_evidence=None,
        )
        is None
    )
    assert (
        strategy.update_factor_weights(
            {"rsi_14": 1.0},
            independent_signal_days=30,
            cooldown_active=True,
            walkforward_evidence={"status": "pass"},
        )
        is None
    )
