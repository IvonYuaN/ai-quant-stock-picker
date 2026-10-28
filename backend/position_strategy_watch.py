"""Human adoption of sourced model observations, separate from trade accounting.

The model proposes conditions. Only a human-selected quote comparison is machine
observable; prose such as 'stabilizes' remains a manual check after reaching it.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
import math
from typing import Any

import alert_events as ae
import portfolio as pf
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock
from decision_context import position_quote_issue
from position_followup import FollowupDefaults
from position_plan_service import PlanConflictError, _checked_root, _store_root, read_position_plan
from strategy_direction import direction_identity


@dataclass(frozen=True)
class StrategyWatchInput:
    choice: str
    source_discussion: str
    generated_at: str
    action_index: int
    comparator: str
    expected_position: dict[str, Any]
    expected_revision: str
    expected_analysis_basis_id: str


def _discussion(worksheet: dict[str, Any], source: str) -> dict[str, Any] | None:
    if source == 'comparison':
        return worksheet.get('comparison')
    if source == 'plan':
        return worksheet['review_plan'].get('discussion')
    raise ValueError('请选择当前比较或已选方案的分析')


def _validate_result(worksheet: dict[str, Any], result: dict[str, Any] | None,
                     value: StrategyWatchInput, current: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    if not result or not result.get('summary') or result.get('status') not in {'complete', 'partial'}:
        raise ValueError('尚无有效汇总观察条件，请先完成AI分析')
    if (result.get('generated_at') != value.generated_at
            or result.get('analysis_basis_id') != value.expected_analysis_basis_id
            or result.get('review_revision') != value.expected_revision
            or result.get('position_snapshot') != value.expected_position):
        raise PlanConflictError('采用的分析或持仓版本已变化，请更新后重新核对')
    try:
        stamp = datetime.fromisoformat(result['generated_at'])
        age = (current - stamp).total_seconds()
    except (KeyError, TypeError, ValueError) as exc:
        raise PlanConflictError('分析时点不可核验，请重新分析') from exc
    if not 0 <= age <= FollowupDefaults().max_check_age_seconds:
        raise PlanConflictError('这份分析已旧，请用当前依据重新分析后采用')
    original = result.get('evidence_snapshot') or {}
    if position_quote_issue(original.get('quote'), checked_at=current):
        raise PlanConflictError('分析使用的行情已旧，请刷新当前依据并重新分析')
    if worksheet['basis'].get('quote_issue'):
        raise PlanConflictError('当前行情不可核验，尚未采用观察条件')
    # Normal quote ticks do not invalidate a discussion while it is running.
    # A changed semantic premise does: do not inherit old strength after a turn.
    before = {key: original.get(key) or original.get('route_constraints', {}).get('evidence', {}).get(key, {})
              for key in ('market', 'sector', 'stock')}
    if direction_identity(before) != direction_identity(worksheet['route_review']['evidence']):
        raise PlanConflictError('大盘、板块或个股依据已变化，请先重新分析；旧条件未采用')
    actions = result['summary'].get('actions') or []
    if type(value.action_index) is not int or not 0 <= value.action_index < len(actions):
        raise ValueError('请选择这份分析中的有效观察条件')
    action = actions[value.action_index]
    reference = result.get('price_refs', {}).get(action.get('price_ref'))
    if action.get('action') not in {'observe', 'sell_review', 'buy_review'} or not isinstance(reference, dict):
        raise ValueError('这条意见没有可跟踪的来源价位，请先补齐依据')
    price = reference.get('price')
    if (isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0
            or any(not isinstance(reference.get(key), str) or not reference[key].strip() for key in ('label', 'source', 'as_of'))):
        raise ValueError('观察价的来源或时点不完整，尚未采用')
    if action['action'] == 'buy_review' and value.choice != 't_watch':
        raise ValueError('回补观察只能在明确选择做T路线后采用')
    if value.choice == 't_watch' and (worksheet['basis'].get('joint_weakness') or worksheet['basis'].get('personal_pause_t')
                                    or not worksheet['basis'].get('sellable_shares')):
        raise ValueError('当前条件暂停做T，先核对风险或选择其他路线')
    if action['action'] == 'sell_review' and not worksheet['basis'].get('sellable_shares'):
        raise ValueError('当前没有可卖股份，不能采用卖出观察条件')
    if value.source_discussion == 'plan' and result.get('choice') != value.choice:
        raise PlanConflictError('这份分析属于另一条路线，请重新比较后采用')
    # Recheck older persisted model results too: a current reference ID cannot
    # launder a different historical number hidden in the action prose.
    from strategy_review_context import validate_position_action_numbers
    validate_position_action_numbers({'actions': [action]}, original, result.get('price_refs') or {})
    return action, reference


def adopt_strategy_watch(position_id: str, value: StrategyWatchInput) -> dict[str, Any]:
    """Adopt one model proposal with explicit human route/direction and CAS.

    No shares, costs, risk rules, scores, fees or notification mode are changed.
    Quantity and fee estimates are optional, separate follow-on calculations.
    """
    if value.choice not in {'hold', 'reduce', 't_watch'} or value.comparator not in {'at_or_above', 'at_or_below'}:
        raise ValueError('请明确选择路线与报价达到或跌至的方向')
    worksheet = read_position_plan(position_id)
    if not worksheet or worksheet['archived']:
        raise PlanConflictError('原持仓已退出或不存在，请刷新')
    if worksheet['revision'] != value.expected_revision or worksheet['position_snapshot'] != value.expected_position:
        raise PlanConflictError('持仓或计划已变化，请刷新后重新采用')
    result = _discussion(worksheet, value.source_discussion)
    current = now_shanghai()
    action, reference = _validate_result(worksheet, result, value, current)
    watch = {'direction_schema': 'direction-v1', 'choice': value.choice, 'action': action['action'], 'price': reference['price'], 'comparator': value.comparator,
             'condition': action['condition'], 'invalidation': action['invalidation'], 'price_ref': deepcopy(reference),
             'source_generated_at': value.generated_at, 'source_discussion': value.source_discussion,
             'source_analysis_basis_id': value.expected_analysis_basis_id, 'source_action_index': value.action_index,
             'saved_at': current.isoformat(), 'evidence_snapshot': {
                 **deepcopy(worksheet['route_review']['evidence']), 'holding': deepcopy(value.expected_position),
                 'quote': {'price': worksheet['basis']['price'], 'as_of': worksheet['basis']['quote_as_of'], 'source': worksheet['basis']['quote_source']},
                 'risk_trigger': deepcopy(worksheet['risk_trigger'])},
             'price_refs': deepcopy(result['price_refs']),
             'evidence_identity': {'symbol': worksheet['symbol'], 'position_id': position_id},
             'position_snapshot': deepcopy(value.expected_position), 'trade_revision': worksheet['trade_revision'],
             'constraints': deepcopy(worksheet['constraints'])}
    with ae._LOCK, advisory_lock(ae._file()), pf._LOCK, advisory_lock(pf._pf_file()):
        data, events = pf._load(), ae._load()
        root, _ = _checked_root(position_id, value.expected_position, value.expected_revision, data, events)
        persisted = root.get('comparison') if value.source_discussion == 'comparison' else root['review_plan'].get('discussion')
        if persisted != result:
            raise PlanConflictError('讨论结果已更新，请核对当前分析后重新采用')
        previous = root['review_plan']
        plan = deepcopy(previous) if previous.get('choice') == value.choice else {'choice': value.choice}
        plan.update(watch=watch, position_snapshot=deepcopy(value.expected_position),
                    constraints_version=root.get('constraints_version', 0), updated_at=current.isoformat())
        plan.pop('watch_stopped', None)
        # Adoption is an observation decision, never an acknowledgement of a sale.
        root['review_plan'] = plan
        root.setdefault('watch_audit', []).append({'request': asdict(value), 'previous': deepcopy(previous.get('watch')),
                                                 'saved_at': current.isoformat()})
        _store_root(data, position_id, root)
    updated = read_position_plan(position_id)
    if updated is None:
        raise PlanConflictError('观察条件已保存，但持仓随后变化；请刷新核对当前状态')
    return updated


def stop_strategy_watch(position_id: str, expected_position: dict[str, Any], expected_revision: str) -> dict[str, Any]:
    """Stop monitoring explicitly, without reverting to old calculator prices."""
    with ae._LOCK, advisory_lock(ae._file()), pf._LOCK, advisory_lock(pf._pf_file()):
        data, events = pf._load(), ae._load()
        root, _ = _checked_root(position_id, expected_position, expected_revision, data, events)
        plan = root['review_plan']
        previous = plan.pop('watch', None)
        if previous is not None or not plan.get('watch_stopped'):
            plan['watch_stopped'] = True
            plan['updated_at'] = now_shanghai().isoformat()
            root.setdefault('watch_audit', []).append({'action': 'stop', 'previous': previous,
                                                     'saved_at': plan['updated_at']})
            _store_root(data, position_id, root)
    updated = read_position_plan(position_id)
    if updated is None:
        raise PlanConflictError('跟踪已停止，但持仓随后变化；请刷新核对')
    return updated
