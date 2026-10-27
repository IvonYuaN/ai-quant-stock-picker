"""Tenant-bound worksheet reads and choice persistence; never place orders."""
from __future__ import annotations

from typing import Any

import alert_events as ae
import portfolio as pf
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock
from position_action_plan import build_action_basis, calculate_scenario
from position_route_review import build_route_review, current_position_evidence, evidence_digest, risk_evidence_revision


def read_action_plan(event_id: str) -> dict[str, Any] | None:
    """Refresh quote evidence, reject stale position identity, and reprice scenarios."""
    from decision_context import _session

    events = ae.list_events()
    event = next((row for row in events if row["id"] == event_id and row["status"] == "OPEN"), None)
    if event is None:
        return None
    portfolio = pf.get_portfolio()
    holding = next((row for row in portfolio["holdings"] if row["code"] == event["symbol"]
                    and row.get("position_id") == event.get("position_id")), None)
    if holding is None:
        return None
    current = now_shanghai()
    old_context = event.get("position_context") or {}
    old_basis = old_context.get("action_basis") or {}
    # A saved assessment is not a permanent veto. Re-evaluate the current stock
    # quote and require the board evidence still to be timely.
    quote = holding.get("quote") or {}
    evidence = current_position_evidence(old_context, quote, current=current)
    basis = build_action_basis(holding, holding.get("quote"), None, checked_at=current, joint_weakness=False).to_dict()
    basis["joint_weakness"] = bool(not basis["quote_issue"] and evidence["market"]["status"] == "weak"
                                   and evidence["sector"]["status"] == "falling" and evidence["stock"]["status"] == "falling")
    # Completed-day bars remain useful when the original check is recent enough;
    # never carry a previous session's daily references into a new completed day.
    completed = _session(current, completed=True)
    basis["references"] = list(basis["references"]) + [
        ref for ref in old_basis.get("references", ())
        if isinstance(ref, dict) and ref.get("label", "").startswith("最近完成日")
        and completed and ref.get("as_of") == completed.isoformat()
    ]
    snapshot = {key: holding.get(key) for key in ("position_id", "cost", "shares", "sellable_shares")}
    plan = event.get("review_plan") or {}
    scenario, scenario_issue = None, ""
    if plan.get("parameters"):
        if plan.get("position_snapshot") != snapshot:
            scenario_issue = "持仓数量、成本或可卖底仓已变化，旧测算失效，请核对后重新保存"
        else:
            try:
                scenario = calculate_scenario(plan["choice"], plan["parameters"], holding)
            except ValueError as exc:
                scenario_issue = str(exc)
    if plan.get("discussion") and plan["discussion"].get("position_snapshot") != snapshot:
        plan = {**plan, "discussion": None}
    # A user-entered sale is a fact, not proof that a T leg has completed.
    # Match the durable portfolio receipt before showing any event-side copy.
    last_sale = event.get("last_sale") or {}
    receipt = next((row for row in portfolio.get("closed", [])
                    if last_sale.get("operation_id") and row.get("operation_id") == last_sale["operation_id"]
                    and row.get("position_id") == holding.get("position_id")
                    and row.get("code") == event["symbol"]
                    and all(row.get(key) == last_sale.get(key) for key in ("date", "price", "shares"))), None)
    recorded_sale = {key: receipt[key] for key in ("date", "price", "shares")} if receipt else None
    sale_requires_review = bool(receipt and plan.get("acknowledged_sale_id") != receipt["operation_id"])
    if sale_requires_review:
        plan = {**plan, "discussion": None}
        if plan.get("parameters"):
            scenario, scenario_issue = None, "已回录新的卖出，先核对剩余仓位并重新保存试算"
    matching = [row for row in events if row.get("status") == "OPEN" and row.get("symbol") == event["symbol"]
                and row.get("position_id") == holding.get("position_id")]
    review = build_route_review(holding, quote, basis, old_context, matching, current=current).to_dict()
    revision = risk_evidence_revision(matching)
    basis_id = evidence_digest({"risk_revision": revision, "evidence": review["evidence"],
                                "basis": {key: value for key, value in basis.items() if key != "checked_at"}})
    comparison = event.get("position_analysis")
    if comparison and comparison.get("position_snapshot") != snapshot:
        comparison = None
    return {"symbol": event["symbol"], "basis": basis, "position_snapshot": snapshot, "review_plan": plan,
            "route_review": review, "comparison": comparison,
            "analysis_basis_id": basis_id, "risk_revision": revision,
            "scenario": scenario, "scenario_issue": scenario_issue,
            "recorded_sale": recorded_sale,
            "sale_requires_review": sale_requires_review,
            "simulation_only": True}


def save_action_plan(
    event_id: str, choice: str, parameters: dict[str, Any] | None = None,
    expected_position: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Atomically bind explicit scenario inputs to an unchanged live position.

    Legacy choice-only clients continue to work. Quotes/network are read before
    locks. Lock ordering matches sales: alerts then portfolio.
    """
    confirm_observation = choice == "hold" and parameters is None and expected_position is not None
    if parameters is None and not confirm_observation:
        return ae.record_review_plan(event_id, choice)
    worksheet = read_action_plan(event_id)
    if worksheet is None:
        return None
    basis = worksheet["basis"]
    if parameters is not None and basis["quote_issue"]:
        raise ValueError(f"请先刷新有效行情：{basis['quote_issue']}")
    if expected_position != worksheet["position_snapshot"]:
        raise ValueError("持仓已变化，请刷新操作计划后重新测算")
    if choice == "t_watch" and basis["joint_weakness"]:
        raise ValueError("同日大盘、板块和个股均走弱，暂停新增做 T 测算，先复核风险")
    # Calculate using trusted refreshed portfolio fields, not a client cost basis.
    holding = {**worksheet["position_snapshot"], "code": worksheet["symbol"]}
    if parameters is not None:
        calculate_scenario(choice, parameters, holding)
    with ae._LOCK, advisory_lock(ae._file()), pf._LOCK, advisory_lock(pf._pf_file()):
        events = ae._load()
        target = next((row for row in events if row["id"] == event_id and row["status"] == "OPEN"), None)
        if target is None:
            return None
        live = next((row for row in pf._load().get("holdings", []) if row.get("code") == target["symbol"] and row.get("position_id") == target.get("position_id")), None)
        if live is None or any(live.get(key) != holding.get(key) for key in ("position_id", "cost", "shares")):
            raise ValueError("保存期间持仓已变化，请刷新后重算")
        today = now_shanghai().date().isoformat()
        sellable = sum(float(lot["shares"]) for lot in (live.get("lots") or [live]) if not lot.get("opened_at") or lot["opened_at"] < today)
        if sellable != holding["sellable_shares"]:
            raise ValueError("可卖底仓已变化，请刷新后重算")
        plan = {"choice": choice, "updated_at": now_shanghai().isoformat(),
                "position_snapshot": worksheet["position_snapshot"],
                "acknowledged_sale_id": (target.get("last_sale") or {}).get("operation_id")}
        if parameters is not None:
            plan["parameters"] = parameters.copy()
        for row in events:
            if row.get("status") == "OPEN" and row.get("symbol") == target["symbol"] and row.get("position_id") == target.get("position_id"):
                row["review_plan"] = plan.copy()
        ae._save(events)
        return plan
