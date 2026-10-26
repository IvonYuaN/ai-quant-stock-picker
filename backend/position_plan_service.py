"""Tenant-bound worksheet reads and choice persistence; never place orders."""
from __future__ import annotations

from typing import Any

import alert_events as ae
import portfolio as pf
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock
from position_action_plan import build_action_basis, calculate_scenario


def read_action_plan(event_id: str) -> dict[str, Any] | None:
    """Refresh quote evidence, reject stale position identity, and reprice scenarios."""
    from decision_context import _session

    event = next((row for row in ae.list_events() if row["id"] == event_id and row["status"] == "OPEN"), None)
    if event is None:
        return None
    holding = next((row for row in pf.get_portfolio()["holdings"] if row["code"] == event["symbol"]
                    and row.get("position_id") == event.get("position_id")), None)
    if holding is None:
        return None
    current = now_shanghai()
    old_context = event.get("position_context") or {}
    old_basis = old_context.get("action_basis") or {}
    from decision_context import _source_date
    from decision_context import _timely_quote
    market_stamp = _source_date((old_context.get("market") or {}).get("as_of"), current)
    sector_stamp = _source_date((old_context.get("sector") or {}).get("as_of"), current)
    # A saved assessment is not a permanent veto. Re-evaluate the current stock
    # quote and require the board evidence still to be timely.
    quote = holding.get("quote") or {}
    change = quote.get("change_pct")
    joint = bool((old_context.get("market") or {}).get("status") == "weak"
                 and (old_context.get("sector") or {}).get("status") == "falling"
                 and market_stamp and sector_stamp
                 and market_stamp[0] == _session(current) and _timely_quote(sector_stamp, current)
                 and isinstance(change, (int, float)) and not isinstance(change, bool) and change < 0)
    basis = build_action_basis(holding, holding.get("quote"), None, checked_at=current, joint_weakness=joint).to_dict()
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
    return {"symbol": event["symbol"], "basis": basis, "position_snapshot": snapshot, "review_plan": plan,
            "scenario": scenario, "scenario_issue": scenario_issue,
            "simulation_only": True}


def save_action_plan(
    event_id: str, choice: str, parameters: dict[str, Any] | None = None,
    expected_position: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Atomically bind explicit scenario inputs to an unchanged live position.

    Legacy choice-only clients continue to work. Quotes/network are read before
    locks. Lock ordering matches sales: alerts then portfolio.
    """
    if parameters is None:
        return ae.record_review_plan(event_id, choice)
    worksheet = read_action_plan(event_id)
    if worksheet is None:
        return None
    basis = worksheet["basis"]
    if basis["quote_issue"]:
        raise ValueError(f"请先刷新有效行情：{basis['quote_issue']}")
    if expected_position != worksheet["position_snapshot"]:
        raise ValueError("持仓已变化，请刷新操作计划后重新测算")
    if choice == "t_watch" and basis["joint_weakness"]:
        raise ValueError("同日大盘、板块和个股均走弱，暂停新增做 T 测算，先复核风险")
    # Calculate using trusted refreshed portfolio fields, not a client cost basis.
    holding = {**worksheet["position_snapshot"], "code": worksheet["symbol"]}
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
                "parameters": parameters.copy(), "position_snapshot": worksheet["position_snapshot"]}
        for row in events:
            if row.get("status") == "OPEN" and row.get("symbol") == target["symbol"] and row.get("position_id") == target.get("position_id"):
                row["review_plan"] = plan.copy()
        ae._save(events)
        return plan
