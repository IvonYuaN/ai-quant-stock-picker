from __future__ import annotations

from aqsp.cli import _apply_protection_observation_boundary
from aqsp.core.types import PickResult
from aqsp.portfolio.optimizer import optimize_portfolio_allocations


def _pick() -> PickResult:
    return PickResult(
        symbol="000001",
        name="测试标的",
        date="2026-07-17",
        close=10.0,
        score=80.0,
        rating="strong_buy_candidate",
        entry_type="relative_strength",
        ideal_buy=10.0,
        stop_loss=9.0,
        take_profit=12.0,
        position="10%-20%",
        metrics={"paper_review_eligible": True},
    )


def test_circuit_breaker_observation_cannot_be_promoted() -> None:
    observed = _apply_protection_observation_boundary(
        [_pick()], reason="单日组合亏损触发"
    )[0]

    assert observed.metrics["observation_only"] is True
    assert observed.metrics["paper_review_eligible"] is False
    assert observed.metrics["portfolio_action"] == "observation_only"
    assert observed.metrics["candidate_status"] == "组合保护观察"
    assert "单日组合亏损触发" in observed.risks[-1]


def test_observation_candidate_is_excluded_from_allocations() -> None:
    pick = _apply_protection_observation_boundary(
        [_pick()], reason="冷却期"
    )[0]
    decision = type("Decision", (), {"action": "observation_only"})()

    result = optimize_portfolio_allocations(
        [pick], {pick.symbol: decision}
    )

    assert result.allocations == ()
    assert result.cash_reserve == 1.0
