"""Route comparison is current evidence, not a loss-driven recommendation."""
from __future__ import annotations

from datetime import timedelta

import pytest

from position_action_plan import build_action_basis
from position_route_review import build_route_review
from test_position_action_plan import HOLDING, NOW, QUOTE, plan_client  # noqa: F401


def review(*, quote=None, holding=None, context=None, joint=False):
    quote = QUOTE if quote is None else quote
    holding = HOLDING if holding is None else holding
    basis = build_action_basis(holding, quote, None, checked_at=NOW, joint_weakness=joint).to_dict()
    return build_route_review(holding, quote, basis, context or {}, [{"level": "L1"}], current=NOW)


def test_route_review_explains_current_holding_when_no_plan_selected() -> None:
    result = review()
    assert result.routes[0].status == "risk_review"
    assert "1000" in result.routes[1].reason
    assert result.routes[2].status == "unknown"
    assert result.unrealized_pnl_pct == pytest.approx(-23.3333, abs=0.001)
    assert result.recovery_to_cost_pct == pytest.approx(30.4348, abs=0.001)
    assert "个人风险与资金约束" in result.missing_evidence


def test_route_review_does_not_force_sale_or_t_when_loss_is_large() -> None:
    result = review(holding={**HOLDING, "cost": 100})
    assert result.routes[0].status == "risk_review"
    assert result.routes[1].status == "review"
    assert result.routes[2].status == "unknown"
    assert result.evidence["market"]["status"] == "unknown"


@pytest.mark.parametrize("patch,joint", [({"sellable_shares": 0}, False), ({}, True)])
def test_route_review_pauses_t_when_actual_constraints_prevent_it(patch: dict, joint: bool) -> None:
    result = review(holding={**HOLDING, **patch}, joint=joint)
    assert result.routes[2].status == "paused"
    if not joint:
        assert result.routes[1].status == "paused"


def test_route_review_drops_old_background_when_source_time_expires() -> None:
    context = {"market": {"status": "weak", "source": "合成源", "as_of": (NOW-timedelta(days=1)).isoformat()},
               "sector": {"status": "falling", "source": "合成源", "as_of": (NOW-timedelta(hours=1)).isoformat()}}
    result = review(context=context, quote={**QUOTE, "change_pct": 1.1})
    assert result.evidence["market"]["status"] == result.evidence["sector"]["status"] == "unknown"
    assert result.evidence["stock"]["status"] == "rising"


def test_route_review_pauses_price_decisions_when_quote_is_stale() -> None:
    result = review(quote={**QUOTE, "quote_time": "20261022100000"})
    assert result.routes[1].status == result.routes[2].status == "paused"
    assert result.unrealized_pnl_pct is None
    assert result.recovery_to_cost_pct is None


def test_route_review_read_keeps_choice_and_trades_unchanged_when_first_opened(plan_client) -> None:  # noqa: F811
    import portfolio as pf
    import alert_events as ae
    client, event = plan_client
    before = (pf._load(), ae.list_events())
    data = client.get(f"/api/alerts/{event}/action-plan").json()["data"]
    assert len(data["route_review"]["routes"]) == 3
    assert data["review_plan"] == {}
    assert (pf._load(), ae.list_events()) == before
