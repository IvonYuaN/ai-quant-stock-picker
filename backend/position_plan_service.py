"""One tenant/position worksheet and CAS-bound plan; never place orders.

portfolio.position_reviews is canonical. Alert IDs only resolve a position; no
synthetic alert is created. Quote I/O happens before the alerts -> portfolio lock.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import alert_events as ae
import portfolio as pf
import trade_journal as journal
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock
from position_action_plan import build_action_basis, calculate_scenario
from position_constraints import (apply_route_constraints, default_constraints,
                                  describe_constraints, parse_constraints,
                                  validate_personal_scenario)
from position_route_review import build_route_review, current_position_evidence, current_rule_facts, evidence_digest, risk_evidence_revision


class PlanConflictError(ValueError):
    """The caller must refresh; no stale mutation has been committed."""


def _snapshot(holding: dict[str, Any]) -> dict[str, Any]:
    today = now_shanghai().date().isoformat()
    sellable = sum(float(lot['shares']) for lot in (holding.get('lots') or [holding])
                   if not lot.get('opened_at') or lot['opened_at'] < today)
    return {key: holding.get(key) for key in ('position_id', 'cost', 'shares')} | {'sellable_shares': sellable}


def _events_for(events: list[dict[str, Any]], position_id: str) -> list[dict[str, Any]]:
    return [row for row in events if row.get('position_id') == position_id]


def _receipt(data: dict[str, Any], position_id: str) -> dict[str, Any] | None:
    # List order is durable operation order, including several sales on one day.
    live = next((row for row in data.get('holdings', []) if row.get('position_id') == position_id), {})
    symbol = live.get('code') or data.get('position_reviews', {}).get(position_id, {}).get('symbol')
    return next((row for row in reversed(data.get('closed', []))
                 if row.get('position_id') == position_id and (symbol is None or row.get('code') == symbol)), None)


def _receipt_id(receipt: dict[str, Any] | None) -> str | None:
    return str(receipt.get('operation_id') or evidence_digest(receipt)) if receipt else None


def _root(data: dict[str, Any], events: list[dict[str, Any]], position_id: str) -> dict[str, Any]:
    saved = data.get('position_reviews', {}).get(position_id)
    if saved is not None:
        return deepcopy(saved)
    root: dict[str, Any] = {'version': 0, 'constraints': default_constraints(), 'review_plan': {}, 'comparison': None}
    candidates = [row for row in _events_for(events, position_id)
                  if (row.get('review_plan') or {}).get('choice') in {'hold', 'reduce', 't_watch'}
                  and (row['review_plan'].get('position_snapshot') or {}).get('position_id', position_id) == position_id]
    candidates.sort(key=lambda row: str(row['review_plan'].get('updated_at', '')), reverse=True)
    if candidates:
        latest = candidates[0]['review_plan']
        tied = [row for row in candidates if row['review_plan'].get('updated_at') == latest.get('updated_at')]
        if any(row['review_plan'] != latest for row in tied):
            root['migration_issue'] = '旧提醒中存在冲突方案，请核对后重新选择；未自动继承'
        else:
            root['review_plan'] = deepcopy(latest)
            root['migrated_from'] = [row['id'] for row in tied]
    analyses = [row for row in _events_for(events, position_id) if row.get('position_analysis')]
    if analyses:
        root['comparison'] = deepcopy(max(analyses, key=lambda row: str(row['position_analysis'].get('generated_at', '')))['position_analysis'])
    # A virtual migration is committed with the first explicit write. Reads do
    # not modify files; after that write event data is never imported again.
    return root


def _risk_revision(data: dict[str, Any], events: list[dict[str, Any]], position_id: str) -> str:
    fields = ('id', 'position_id', 'enabled', 'revision', 'param_value', 'trigger_price', 'effective_from', 'high_water_price')
    rules = [{key: row.get(key) for key in fields} for row in data.get('risk_rules', []) if row.get('position_id') == position_id]
    opened = [row for row in _events_for(events, position_id) if row.get('status') == 'OPEN']
    return evidence_digest({'rules': sorted(rules, key=lambda row: str(row['id'])), 'events': risk_evidence_revision(opened)})


def _revision(root: dict[str, Any], data: dict[str, Any], events: list[dict[str, Any]], holding: dict[str, Any]) -> str:
    position_id = holding['position_id']
    return evidence_digest({'version': root['version'], 'constraints': root['constraints'],
                            'plan': {key: value for key, value in root['review_plan'].items() if key != 'discussion'},
                            'position': _snapshot(holding), 'risk': _risk_revision(data, events, position_id),
                            'sale': _receipt_id(_receipt(data, position_id)),
                            'trades': journal.position_revision(data, holding['code'])})


def resolve_position_id(event_id: str) -> str | None:
    """Resolve a real same-tenant event, including a reviewed/ignored event."""
    event = next((row for row in ae.list_events() if row.get('id') == event_id), None)
    return str(event['position_id']) if event and event.get('position_id') else None


def read_action_plan(event_id: str) -> dict[str, Any] | None:
    """Compatibility adapter; the plan belongs to the position, not the event."""
    position_id = resolve_position_id(event_id)
    return read_position_plan(position_id) if position_id else None


def read_position_plan(position_id: str) -> dict[str, Any] | None:
    """Read current quote evidence, personal constraints and one durable plan."""
    # Do not invoke the legacy owner migration for an unknown tenant merely
    # because it guessed another tenant's position identity.
    if not Path(pf._pf_file()).is_file():
        return None
    # This established API lazily attaches IDs/rules for legacy holdings.
    pf.get_risk_rules()
    portfolio = pf.get_portfolio()
    with ae._LOCK, pf._LOCK:
        events, data = ae._load(), pf._load()
    return build_position_worksheet(position_id, portfolio, data, events, current=now_shanghai())


def build_position_worksheet(position_id: str, portfolio: dict[str, Any], data: dict[str, Any], events: list[dict[str, Any]],
                             *, current: datetime, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Build from one already observed portfolio; no disk or network reads."""
    from decision_context import _session

    holding = next((row for row in portfolio['holdings'] if row.get('position_id') == position_id), None)
    root = _root(data, events, position_id)
    receipt = _receipt(data, position_id)
    archived = holding is None
    if archived:
        if position_id not in data.get('position_reviews', {}) or receipt is None:
            return None
        holding = {'position_id': position_id, 'code': receipt['code'], 'name': receipt.get('name') or receipt['code'],
                   'shares': 0, 'sellable_shares': 0, 'cost': receipt.get('cost') or 0, 'quote': {}}
    # Guard reads across a concurrent close/reopen while quote I/O was pending.
    live = next((row for row in data.get('holdings', []) if row.get('position_id') == position_id), None)
    if not archived and (live is None or any(live.get(key) != holding.get(key) for key in ('cost', 'shares'))):
        raise PlanConflictError('读取期间持仓已变化，请刷新')
    holding = {**holding, **(_snapshot(live) if live else {}), 'risk_rules': [row for row in data.get('risk_rules', []) if row.get('position_id') == position_id]}
    same = _events_for(events, position_id)
    opened = [row for row in same if row.get('status') == 'OPEN']
    context_event = max(same, key=lambda row: str(row.get('updated_at', '')), default={})
    old_context = context if context is not None else context_event.get('position_context') or {}
    quote = holding.get('quote') or {}
    evidence = current_position_evidence(old_context, quote, current=current)
    basis = build_action_basis(holding, quote, None, checked_at=current, joint_weakness=False).to_dict()
    basis['joint_weakness'] = bool(not basis['quote_issue'] and evidence['market']['status'] == 'weak'
                                   and evidence['sector']['status'] == 'falling' and evidence['stock']['status'] == 'falling')
    completed = _session(current, completed=True)
    basis['references'] = list(basis['references']) + [ref for ref in (old_context.get('action_basis') or {}).get('references', ())
        if isinstance(ref, dict) and ref.get('label', '').startswith('最近完成日') and completed and ref.get('as_of') == completed.isoformat()]
    snapshot = {key: holding.get(key) for key in ('position_id', 'cost', 'shares', 'sellable_shares')}
    # Use raw lots for the same sellability calculation as the lock-time guard.
    if live is not None:
        snapshot = _snapshot(live)
    constraints = root['constraints']
    details = describe_constraints(constraints, holding, basis)
    basis['personal_pause_t'] = bool(constraints.get('attention') == 'unavailable' or details['constraints_issue'])
    revision = _revision(root, data, events, live or holding)
    plan = deepcopy(root['review_plan'])
    scenario, scenario_issue = None, ''
    trade_revision = journal.position_revision(data, holding['code'])
    trades_changed = plan.get('trade_revision') is not None and plan['trade_revision'] != trade_revision
    sale_requires_review = bool(receipt and plan.get('acknowledged_sale_id') != _receipt_id(receipt))
    if plan.get('parameters'):
        if archived or plan.get('position_snapshot') != snapshot:
            scenario_issue = '持仓数量、成本或可卖底仓已变化，旧测算失效，请核对后重新保存'
        elif plan.get('constraints_version', 0) != root.get('constraints_version', 0) or details['constraints_issue']:
            scenario_issue = '个人条件已变化，旧测算失效，请核对后重新保存'
        elif sale_requires_review:
            scenario_issue = '已回录新的卖出，先核对剩余仓位并重新保存试算'
        elif trades_changed:
            scenario_issue = '实际成交、费用或买回关联已变化，请核对后重新保存试算'
        else:
            try:
                scenario = calculate_scenario(plan['choice'], plan['parameters'], {**holding, **snapshot})
                validate_personal_scenario(plan['choice'], plan['parameters'], scenario, constraints)
            except ValueError as exc:
                scenario, scenario_issue = None, str(exc)
    discussion = plan.get('discussion')
    if discussion and not archived and (discussion.get('position_snapshot') != snapshot or sale_requires_review or trades_changed):
        plan['discussion'] = None
    facts = current_rule_facts(holding.get('risk_rules', []), basis, current=current)
    review = build_route_review(holding, quote, basis, old_context, opened, current=current, risk_facts=facts).to_dict()
    review = apply_route_constraints(review, constraints, holding, basis)
    risk_revision = _risk_revision(data, events, position_id)
    basis_id = evidence_digest({'risk_revision': risk_revision, 'review_revision': revision, 'evidence': review['evidence'],
                                'basis': {key: value for key, value in basis.items() if key != 'checked_at'}})
    comparison = root.get('comparison')
    if comparison and not archived and (comparison.get('position_snapshot') != snapshot or comparison.get('review_revision') != revision):
        comparison = None
    from position_followup import followup_view
    return {'followup': followup_view(root, revision, snapshot, current=current), 'position_id': position_id, 'name': holding.get('name') or holding['code'], 'symbol': holding['code'],
            'revision': revision, 'archived': archived, 'constraints': constraints, **details,
            'migration_issue': root.get('migration_issue', ''), 'basis': basis, 'position_snapshot': snapshot, 'review_plan': plan,
            'route_review': review, 'comparison': comparison, 'analysis_basis_id': basis_id, 'risk_revision': risk_revision,
            'current_risk': facts, 'risk_trigger': {'checked_at': context_event.get('updated_at'), 'basis': context_event.get('basis_json', {}), 'current_facts': facts,
                             'rules': holding.get('risk_rules', []), 'open_alert_count': len(opened)},
            'scenario': scenario, 'scenario_issue': scenario_issue,
            'recorded_sale': {key: receipt[key] for key in ('date', 'price', 'shares')} if receipt else None,
            'sale_requires_review': sale_requires_review, 'simulation_only': True}


def _checked_root(position_id: str, expected_position: dict[str, Any] | None, expected_revision: str | None,
                  data: dict[str, Any], events: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    holding = next((row for row in data.get('holdings', []) if row.get('position_id') == position_id), None)
    if holding is None:
        raise PlanConflictError('原持仓已退出或身份变化，请刷新；未保存旧结果')
    root = _root(data, events, position_id)
    if not expected_revision or expected_position != _snapshot(holding) or expected_revision != _revision(root, data, events, holding):
        raise PlanConflictError('持仓、个人条件、方案或风险依据已变化，请刷新后重新核对；旧内容未保存')
    return root, holding


def _store_root(data: dict[str, Any], position_id: str, root: dict[str, Any]) -> None:
    root['symbol'] = next(row['code'] for row in data['holdings'] if row.get('position_id') == position_id)
    root['version'] += 1
    root['updated_at'] = now_shanghai().isoformat()
    data.setdefault('position_reviews', {})[position_id] = root
    pf._save(data)


def save_action_plan(event_id: str, choice: str, parameters: dict[str, Any] | None = None,
                     expected_position: dict[str, Any] | None = None, expected_revision: str | None = None) -> dict[str, Any] | None:
    """Adapt a real event to canonical position storage, with the same guards."""
    if choice not in {"hold", "reduce", "t_watch"}:
        raise ValueError("方案必须是 hold、reduce 或 t_watch")
    position_id = resolve_position_id(event_id)
    return save_position_plan(position_id, choice, parameters, expected_position, expected_revision) if position_id else None


def save_position_plan(position_id: str, choice: str, parameters: dict[str, Any] | None = None,
                       expected_position: dict[str, Any] | None = None, expected_revision: str | None = None) -> dict[str, Any] | None:
    """CAS protects intent-only saves as well as explicit fee calculations."""
    if choice not in {'hold', 'reduce', 't_watch'}:
        raise ValueError('方案必须是 hold、reduce 或 t_watch')
    worksheet = read_position_plan(position_id)
    if worksheet is None:
        return None
    if expected_revision != worksheet['revision'] or expected_position != worksheet['position_snapshot'] or worksheet['archived']:
        raise PlanConflictError('请刷新当前持仓计划后重新核对，旧页面未保存')
    if parameters is not None:
        if worksheet['basis']['quote_issue']:
            raise ValueError(f"请先刷新有效行情：{worksheet['basis']['quote_issue']}")
        if choice == 't_watch' and (worksheet['basis']['joint_weakness'] or worksheet['constraints_issue']):
            raise ValueError('暂停新增做 T 测算，请先核对三维风险及个人条件')
        scenario = calculate_scenario(choice, parameters, {**worksheet['position_snapshot'], 'code': worksheet['symbol']})
        validate_personal_scenario(choice, parameters, scenario, worksheet['constraints'])
    with ae._LOCK, advisory_lock(ae._file()), pf._LOCK, advisory_lock(pf._pf_file()):
        data, events = pf._load(), ae._load()
        root, _ = _checked_root(position_id, expected_position, expected_revision, data, events)
        previous = root['review_plan']
        plan = deepcopy(previous) if previous.get('choice') == choice and parameters is None else {'choice': choice}
        plan['updated_at'] = now_shanghai().isoformat()
        if parameters is not None or not plan.get('parameters'):
            plan.update({'position_snapshot': worksheet['position_snapshot'],
                         'constraints_version': root.get('constraints_version', 0),
                         'trade_revision': journal.position_revision(data, worksheet['symbol'])})
        # Intent changes do not silently acknowledge a new sale. Explicit hold
        # confirmation, or a refreshed fee calculation, acknowledges this receipt.
        if parameters is not None or choice == 'hold' and previous.get('choice') == 'hold':
            plan['acknowledged_sale_id'] = _receipt_id(_receipt(data, position_id))
        if parameters is not None:
            plan['parameters'] = deepcopy(parameters)
            plan.pop('discussion', None)
        root['review_plan'] = plan
        root.pop('migration_issue', None)
        _store_root(data, position_id, root)
        return plan


def save_position_constraints(position_id: str, raw: dict[str, Any], expected_position: dict[str, Any] | None,
                              expected_revision: str | None, expected_quote: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Confirm optional constraints; loss limits retain their confirmed baseline."""
    worksheet = read_position_plan(position_id)
    if worksheet is None:
        return None
    constraints = parse_constraints(raw, worksheet['constraints'], worksheet['position_snapshot'], worksheet['basis'], current=now_shanghai())
    if constraints.get('risk_baseline') and constraints['risk_baseline'] != worksheet['constraints'].get('risk_baseline'):
        actual_quote = {'price': worksheet['basis']['price'], 'as_of': worksheet['basis']['quote_as_of']}
        if expected_quote != actual_quote:
            raise PlanConflictError('确认风险金额期间行情已变化，请更新并重新确认起算价格和时点；旧预算未重置')
    with ae._LOCK, advisory_lock(ae._file()), pf._LOCK, advisory_lock(pf._pf_file()):
        data, events = pf._load(), ae._load()
        root, _ = _checked_root(position_id, expected_position, expected_revision, data, events)
        if constraints != root['constraints']:
            root['constraints'] = constraints
            root['constraints_version'] = root.get('constraints_version', 0) + 1
            _store_root(data, position_id, root)
    return read_position_plan(position_id)


def save_position_discussion(position_id: str, snapshot: dict[str, Any], result: dict[str, Any]) -> None:
    """Commit only the same root, risk, constraints and holding used by the model."""
    with ae._LOCK, advisory_lock(ae._file()), pf._LOCK, advisory_lock(pf._pf_file()):
        data, events = pf._load(), ae._load()
        root, _ = _checked_root(position_id, snapshot, result.get('review_revision'), data, events)
        if result['choice'] is None:
            root['comparison'] = result
        else:
            if root['review_plan'].get('choice') != result['choice']:
                raise PlanConflictError('讨论期间方案已变化，旧结论未保存')
            root['review_plan']['discussion'] = result
        # Model output must not change its own revision or any human choice.
        data.setdefault('position_reviews', {})[position_id] = root
        pf._save(data)
