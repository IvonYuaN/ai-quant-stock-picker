"""Personal boundaries must not fabricate risk capacity or reset accrued loss."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from position_action_plan import calculate_scenario
from position_constraints import (
    apply_route_constraints, default_constraints, describe_constraints,
    parse_constraints, validate_personal_scenario,
)

NOW = datetime(2026, 10, 27, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
HOLDING = {"code": "600001", "position_id": "holding-one", "cost": 12,
           "shares": 1000, "sellable_shares": 800}
BASIS = {"price": 10.0, "quote_as_of": NOW.isoformat(), "quote_issue": "",
         "checked_at": NOW.isoformat(), "references": [{"label": "本交易日已见低点", "price": 9}]}
RAW = {"attention": "available", "extra_cash": 0, "loss_limit": 500}
PARAMETERS = {"quantity": 200, "sell_price": 10, "buy_price": 9.5,
              "commission_rate": 0.0003, "min_commission": 5,
              "stamp_tax_rate": 0.0005, "slippage_rate": 0, "fees_confirmed": True}
REVIEW = {"title": "比较", "missing_evidence": ["个人风险与资金约束", "板块当前证据"],
          "routes": [{"choice": "hold", "status": "review", "reason": "保留敞口", "needs": ["你可承受的后续损失"]},
                     {"choice": "reduce", "status": "review", "reason": "可复核", "needs": []},
                     {"choice": "t_watch", "status": "unknown", "reason": "仍需量价证据", "needs": ["能否盯盘", "两腿费用"]}]}


def confirm(raw: dict | None = None, previous: dict | None = None, **kwargs) -> dict:
    return parse_constraints(raw or RAW, previous, kwargs.get("holding", HOLDING),
                             kwargs.get("basis", BASIS), current=kwargs.get("current", NOW))


def test_constraints_default_preserves_unknown_and_returns_fresh_dict() -> None:
    first = default_constraints()
    first["attention"] = "available"
    assert default_constraints() == {"attention": "unknown", "extra_cash": None,
                                     "loss_limit": None, "risk_baseline": None}
    unknown = confirm({"attention": "unknown"})
    assert unknown["extra_cash"] is None and unknown["loss_limit"] is None


@pytest.mark.parametrize("field", ["extra_cash", "loss_limit"])
@pytest.mark.parametrize("value", [True, "100", -1, float("nan"), float("inf"), [], {}])
def test_constraints_rejects_invalid_money(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        confirm({**RAW, field: value})


@pytest.mark.parametrize("attention", [None, "sometimes", "", True, [], {}])
def test_constraints_rejects_unknown_attention_enum(attention: object) -> None:
    with pytest.raises(ValueError, match="盯盘"):
        confirm({**RAW, "attention": attention})


@pytest.mark.parametrize("patch", [{"reset_risk_baseline": "yes"}, {"unexpected": 1}])
def test_constraints_rejects_invalid_reset_or_extra_fields(patch: dict) -> None:
    with pytest.raises(ValueError):
        confirm({**RAW, **patch})


def test_constraints_rejects_confirmation_when_current_has_no_timezone() -> None:
    with pytest.raises(ValueError, match="时区"):
        confirm(current=NOW.replace(tzinfo=None))


def test_constraints_clears_baseline_when_loss_allowance_becomes_unknown() -> None:
    original = confirm()
    cleared = confirm({**RAW, "loss_limit": None}, original)
    assert cleared["risk_baseline"] is None
    assert original["risk_baseline"]["price"] == 10
    zero = confirm({**RAW, "loss_limit": 0})
    assert zero["risk_baseline"]["price"] == 10
    assert describe_constraints(zero, HOLDING, BASIS)["risk_remaining"] == 0


def test_constraints_refuses_consumption_when_quote_precedes_confirmation_basis() -> None:
    original = confirm()
    earlier = {**BASIS, "quote_as_of": (NOW - timedelta(minutes=1)).isoformat()}
    description = describe_constraints(original, HOLDING, earlier)
    assert description["risk_remaining"] is None
    assert "早于预算确认基准" in description["constraints_issue"]


def test_constraints_preserves_confirmed_baseline_when_quote_and_attention_change() -> None:
    first = confirm()
    later = NOW + timedelta(minutes=10)
    lower = {**BASIS, "price": 9.8, "quote_as_of": later.isoformat(), "checked_at": later.isoformat()}
    before = deepcopy(first)
    again = confirm({**RAW, "attention": "occasional"}, first, basis=lower, current=later)
    assert again["risk_baseline"] == first["risk_baseline"]
    assert first == before
    description = describe_constraints(again, HOLDING, lower)
    assert description["risk_remaining"] == pytest.approx(300)
    assert "风险数量尚未验证" in description["quantity_bounds"]["note"]
    # Reads also cannot consume the budget a second time or roll its anchor.
    assert describe_constraints(again, HOLDING, lower) == description
    assert first["risk_baseline"]["market_value"] == 10000


@pytest.mark.parametrize("change", ["loss", "reset", "position"])
def test_constraints_resets_only_on_explicit_budget_confirmation(change: str) -> None:
    first = confirm()
    raw = dict(RAW)
    holding = dict(HOLDING)
    if change == "loss":
        raw["loss_limit"] = 700
    elif change == "reset":
        raw["reset_risk_baseline"] = True
    else:
        holding["shares"] = 900
    later = NOW + timedelta(minutes=10)
    basis = {**BASIS, "price": 9, "quote_as_of": later.isoformat(), "checked_at": later.isoformat()}
    second = confirm(raw, first, holding=holding, basis=basis, current=later)
    assert second["risk_baseline"]["price"] == 9
    assert second["risk_baseline"]["shares"] == holding["shares"]
    assert second["risk_baseline"]["confirmed_at"] == later.isoformat()


@pytest.mark.parametrize("changed", [{"shares": 900}, {"cost": 11}, {"sellable_shares": 1000}, {"position_id": "new"}])
def test_constraints_marks_old_budget_invalid_when_position_changes(changed: dict) -> None:
    confirmed = confirm()
    result = describe_constraints(confirmed, {**HOLDING, **changed}, BASIS)
    assert "旧风险预算不作为当前约束" in result["constraints_issue"]
    assert result["risk_remaining"] is None
    assert confirmed["risk_baseline"]["price"] == 10


@pytest.mark.parametrize("patch", [{"price": None}, {"price": 0}, {"quote_as_of": None}, {"quote_as_of": "2026-10-27T10:00:00"}, {"quote_issue": "过期"}, {"quote_as_of": (NOW + timedelta(minutes=1)).isoformat()}])
def test_constraints_rejects_budget_confirmation_when_quote_invalid(patch: dict) -> None:
    with pytest.raises(ValueError, match="有效现价"):
        confirm(basis={**BASIS, **patch})
    # Unknown budget still permits voluntary attention and cash recording.
    assert confirm({**RAW, "loss_limit": None}, basis={**BASIS, **patch})["risk_baseline"] is None


def test_constraints_does_not_renew_budget_when_quote_is_below_baseline() -> None:
    c = confirm()
    description = describe_constraints(c, HOLDING, {**BASIS, "price": 9})
    assert description["risk_remaining"] == 0
    assert "整个余仓风险" in description["constraints_issue"]
    assert describe_constraints(c, HOLDING, {**BASIS, "price": 11})["risk_remaining"] == 500
    assert describe_constraints(c, HOLDING, {**BASIS, "quote_issue": "过期"})["risk_remaining"] is None


@pytest.mark.parametrize("code,lot", [("600001", 100), ("688001", 200), ("920001", 100)])
def test_constraints_quantity_bounds_only_use_share_and_board_rules(code: str, lot: int) -> None:
    bounds = describe_constraints(default_constraints(), {**HOLDING, "code": code}, BASIS)["quantity_bounds"]
    assert bounds["sellable_shares"] == 800 and bounds["buy_lot"] == lot
    assert bounds["extra_cash_limit"] is None
    assert "建议数量" in bounds["note"] and "余仓" in bounds["note"]
    assert "风险数量尚未验证" in bounds["note"]
    assert not any(key in bounds for key in ("risk_quantity", "recommended_quantity", "stop_price"))


def test_constraints_route_changes_attention_and_replaces_only_personal_gaps() -> None:
    before = deepcopy(REVIEW)
    unknown = apply_route_constraints(REVIEW, default_constraints(), HOLDING, BASIS)
    assert "个人风险与资金约束" not in unknown["missing_evidence"]
    assert "能否盯盘尚未确认" in unknown["missing_evidence"]
    assert "新增资金上限未知" in unknown["missing_evidence"]
    assert "板块当前证据" in unknown["missing_evidence"]
    available = apply_route_constraints(REVIEW, confirm(), HOLDING, BASIS)
    assert not any("未知" in item for item in available["missing_evidence"])
    assert "实际卖出净现金" in available["routes"][2]["reason"]
    paused = apply_route_constraints(REVIEW, confirm({**RAW, "attention": "unavailable"}), HOLDING, BASIS)
    assert paused["routes"][2]["status"] == "paused"
    assert "不能盯盘" in paused["routes"][2]["reason"]
    occasional = apply_route_constraints(REVIEW, confirm({**RAW, "attention": "occasional"}), HOLDING, BASIS)
    assert "只能偶尔盯盘" in occasional["routes"][2]["reason"]
    assert REVIEW == before


def test_constraints_zero_extra_cash_allows_net_sale_cash_but_rejects_cash_deficit() -> None:
    c = confirm()
    profitable = calculate_scenario("t_watch", PARAMETERS, HOLDING)
    assert profitable["net_cash"] > 0
    validate_personal_scenario("t_watch", PARAMETERS, profitable, c)
    expensive = {**PARAMETERS, "buy_price": 10.1}
    negative = calculate_scenario("t_watch", expensive, HOLDING)
    assert negative["net_cash"] < 0
    with pytest.raises(ValueError, match="超过新增资金上限"):
        validate_personal_scenario("t_watch", expensive, negative, c)
    validate_personal_scenario("t_watch", expensive, negative, {**c, "extra_cash": -negative["net_cash"]})
    with pytest.raises(ValueError):
        validate_personal_scenario("t_watch", expensive, negative, {**c, "extra_cash": -negative["net_cash"] - 0.01})
    validate_personal_scenario("t_watch", expensive, negative, {**c, "extra_cash": None})


def test_constraints_refuses_t_calculation_when_cannot_monitor_or_cash_unverified() -> None:
    c = confirm({**RAW, "attention": "unavailable"})
    scenario = calculate_scenario("t_watch", PARAMETERS, HOLDING)
    with pytest.raises(ValueError, match="不能盯盘"):
        validate_personal_scenario("t_watch", PARAMETERS, scenario, c)
    validate_personal_scenario("hold", {}, {}, c)
    validate_personal_scenario("reduce", {**PARAMETERS, "buy_price": None}, {}, c)
    with pytest.raises(ValueError, match="净现金未核验"):
        validate_personal_scenario("t_watch", PARAMETERS, {"net_cash": None}, confirm())
