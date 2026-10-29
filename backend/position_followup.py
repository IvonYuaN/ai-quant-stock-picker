"""Pure, source-timed observations of a saved human plan, never trade signals."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import math
from typing import Any

from aqsp.core.errors import DataError
from position_route_review import evidence_digest
from strategy_direction import direction_identity
import strategy_conditions as conditions

FOLLOWUP_MODES = frozenset({'off', 'changes', 'daily'})
ATTENTION_STATES = frozenset({'price_watch', 'below_watch', 'paused', 'needs_review', 'evidence_unavailable'})


@dataclass(frozen=True)
class FollowupDefaults:
    # Display liveness budget for a bounded scheduled check, not a price rule.
    max_check_age_seconds: int = 900


@dataclass(frozen=True)
class FollowupObservation:
    position_id: str
    symbol: str
    name: str
    state: str
    title: str
    detail: str
    observed_at: str
    quote_as_of: str | None
    revision: str
    plan_key: str
    position_snapshot: dict[str, Any]
    reason: str
    joint_pause_pending: bool = False
    condition_checks: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize an observation; timestamps remain source/inspection times."""
        return asdict(self)


def saved_observation(root: dict[str, Any]) -> dict[str, Any] | None:
    """Reject corrupt tracking state without resetting it or licensing delivery."""
    value = root.get('followup')
    if value is None:
        return None
    try:
        if not isinstance(value, dict):
            raise ValueError('not an object')
        required = ('position_id', 'symbol', 'name', 'state', 'title', 'detail', 'observed_at', 'revision', 'plan_key', 'reason')
        if any(not isinstance(value.get(key), str) or not value[key] for key in required):
            raise ValueError('invalid observation fields')
        if value['state'] not in ATTENTION_STATES | {'waiting', 'unplanned', 'missing_prices', 'exited'}:
            raise ValueError('unknown state')
        if not isinstance(value.get('position_snapshot'), dict) or value['position_snapshot'].get('position_id') != value['position_id']:
            raise ValueError('invalid position')
        if 'joint_pause_pending' in value and type(value['joint_pause_pending']) is not bool:
            raise ValueError('invalid pause state')
        for key in ('observed_at', 'quote_as_of'):
            if key == 'quote_as_of' and value.get(key) is None:
                continue
            stamp = datetime.fromisoformat(value[key])
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError('missing timezone')
        return value
    except (KeyError, TypeError, ValueError) as exc:
        raise DataError('后台跟进记录损坏，未覆盖原记录或发送计划通知；请核对存储后重新检查') from exc


def plan_key(root: dict[str, Any], position_id: str) -> str:
    """Bind human intent, excluding dynamic prices, model output and risk highs."""
    plan = root.get('review_plan') or {}
    return evidence_digest({'position_id': position_id, 'intent_version': root.get('version', 0), 'choice': plan.get('choice'),
                            'parameters': plan.get('parameters'), 'watch': plan.get('watch'), 'watch_stopped': plan.get('watch_stopped'), 'updated_at': plan.get('updated_at'),
                            'position': plan.get('position_snapshot'), 'acknowledged_sale_id': plan.get('acknowledged_sale_id'),
                            'constraints_version': root.get('constraints_version', 0), 'constraints': root.get('constraints')})


def _price(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def risk_rule_key(rules: list[dict[str, Any]], position_id: str) -> str:
    """Bind the rule batch used for this check, including dynamic risk highs."""
    fields = ('id', 'position_id', 'enabled', 'revision', 'param_value', 'trigger_price', 'effective_from', 'high_water_price')
    rows = [{key: row.get(key) for key in fields} for row in rules if row.get('position_id') == position_id]
    return evidence_digest(sorted(rows, key=lambda row: str(row['id'])))


def condition_facts(worksheet: dict[str, Any], *, current: datetime) -> dict[str, conditions.ConditionFact]:
    """Adapt source-guarded position evidence without disk/network access."""
    from decision_context import position_quote_issue
    from strategy_market_source import BREADTH_SOURCE

    evidence = worksheet.get('route_review', {}).get('evidence', {})
    basis = worksheet.get('basis', {})
    result = {}
    for key in ('market', 'sector', 'quote'):
        fact = ({'price': basis.get('price'), 'source': basis.get('quote_source'), 'as_of': basis.get('quote_as_of')}
                if key == 'quote' else evidence.get(key) or {})
        source, stamp = fact.get('source'), fact.get('as_of')
        valid = bool(isinstance(source, str) and source and not position_quote_issue({'as_of': stamp}, checked_at=current))
        observed = fact.get('price') if key == 'quote' else fact.get('status')
        if key == 'market':
            valid = valid and source == BREADTH_SOURCE and observed in {'weak', 'neutral', 'strong'}
        elif key == 'sector':
            valid = valid and bool(fact.get('industry')) and observed in {'falling', 'flat', 'rising'}
        else:
            valid = valid and not basis.get('quote_issue') and _price(observed) is not None
        result[key] = conditions.ConditionFact(observed, source if isinstance(source, str) else '',
                                                stamp if isinstance(stamp, str) else None, bool(valid))
    return result


def watch_contract(watch: dict[str, Any], position_id: str) -> dict[str, Any] | None:
    """Bind a position-only contract back to the exact adopted watch operands."""
    if 'condition_contract' not in watch:
        return None
    frozen = conditions.validate_contract(watch['condition_contract'])
    reference = watch.get('price_ref') or {}
    if not isinstance(reference, dict):
        raise ValueError('持仓冻结观察价引用结构无效，请重新核对')
    if _price(reference.get('price')) != _price(watch.get('price')):
        raise ValueError('持仓冻结观察价与原引用不一致')
    expected = conditions.freeze_position_contract(frozen['prerequisite_ids'], frozen['invalidation_ids'],
        price_predicate={'price_ref': watch.get('source_price_ref'), 'comparator': watch.get('comparator'),
                         'price': watch.get('price'), 'source': reference.get('source'), 'as_of': reference.get('as_of')},
        condition=watch['condition'], invalidation=watch['invalidation'],
        origin={'kind': 'position', 'position_id': position_id, 'analysis_basis_id': watch.get('source_analysis_basis_id'),
                'source_discussion': watch.get('source_discussion'), 'generated_at': watch.get('source_generated_at'),
                'action_index': watch.get('source_action_index'), 'choice': watch.get('choice'),
                'action': watch.get('action'), 'price_ref': watch.get('source_price_ref')})
    if frozen != expected:
        raise ValueError('冻结条件合同与已采用持仓观察不一致')
    return frozen


def strategy_condition_checks(worksheet: dict[str, Any], *, current: datetime) -> dict[str, Any] | None:
    """Expose checks even behind risk/changed-premise pauses; never relax them."""
    plan = worksheet.get('review_plan') or {}
    watch = plan.get('watch')
    if not watch or plan.get('watch_stopped') or worksheet.get('archived'):
        return None
    frozen = watch_contract(watch, worksheet['position_id'])
    reference = watch.get('price_ref') or {}
    if not isinstance(reference, dict):
        raise ValueError('持仓观察价引用结构无效，请重新核对')
    legacy_price = None
    if (frozen is None and _price(watch.get('price')) is not None
            and watch.get('comparator') in {'at_or_above', 'at_or_below'}
            and all(isinstance(reference.get(key), str) and reference[key] for key in ('source', 'as_of'))):
        legacy_price = {'price_ref': 'legacy_position_price', 'comparator': watch['comparator'], 'price': watch['price'],
                        'source': reference['source'], 'as_of': reference['as_of']}
    return conditions.evaluate_contract(frozen, condition_facts(worksheet, current=current),
        legacy_price=legacy_price, legacy_condition=str(watch.get('condition') or ''),
        legacy_invalidation=str(watch.get('invalidation') or ''))


def observe_plan(worksheet: dict[str, Any], root: dict[str, Any], *, current: datetime) -> FollowupObservation:
    """Describe saved prices only; reaching them proves neither reversal nor fill."""
    plan, basis = worksheet['review_plan'], worksheet['basis']
    choice = plan.get('choice')
    check_issue = ''
    try:
        checks = strategy_condition_checks(worksheet, current=current)
    except (ValueError, KeyError, TypeError) as exc:
        checks, check_issue = None, str(exc)
    previous = saved_observation(root) or {}
    evidence = worksheet.get('route_review', {}).get('evidence', {})
    unresolved_pause = (choice == 't_watch'
                        and previous.get('plan_key') == plan_key(root, worksheet['position_id'])
                        and (previous.get('joint_pause_pending') or previous.get('reason') in {'joint_weakness', 'joint_evidence_unknown'})
                        and any(evidence.get(key, {}).get('status', 'unknown') == 'unknown'
                                for key in ('market', 'sector', 'stock')))
    state, title, detail, reason = 'waiting', '仍在等已保存的观察价', '本次报价未达到保存的观察条件；持续等待不外发通知。', 'waiting'
    if worksheet['archived']:
        state, title, detail, reason = 'exited', '这笔持仓已清仓', '历史计划停止当前跟进；重新买入不沿用原计划。', 'exited'
    elif not choice:
        state, title, detail, reason = 'unplanned', '还没有选择处理方式', '可以先比较三条路；尚未建立后台观察条件。', 'unplanned'
    elif plan.get('watch_stopped'):
        state, title, detail, reason = 'unplanned', '你已停止这项策略跟踪', '原观察条件不再监控，也不自动回退到旧试算价位；可重新分析后采用新条件。持仓风险规则仍按原设置检查。', 'watch_stopped'
    elif worksheet.get('sale_requires_review'):
        state, title, detail, reason = 'needs_review', '成交后需要重核余仓', '已有新的真实卖出回录；请核对剩余股份和用途，不能沿用原试算继续回补。', 'sale'
    elif (plan.get('position_snapshot') != worksheet['position_snapshot']
          or plan.get('constraints_version', 0) != root.get('constraints_version', 0)):
        state, title, detail, reason = 'needs_review', '计划条件已变化，先重新核对', '持仓、可卖股份或个人条件与保存时不同；原价位与试算暂停作为当前操作依据。', 'plan_changed'
    elif basis.get('quote_issue') or _price(basis.get('price')) is None:
        state, title, detail, reason = 'evidence_unavailable', '行情依据不可用，等待重新检查', str(basis.get('quote_issue') or '缺少有效现价；不能判断是否到价，也不能视为风险解除。'), 'quote'
        if unresolved_pause:
            reason = 'joint_evidence_unknown'
    elif choice == 't_watch' and (basis.get('personal_pause_t') or basis.get('joint_weakness')):
        reason = 'personal' if basis.get('personal_pause_t') else 'joint_weakness'
        state, title, detail = 'paused', '原做 T 计划先暂停', str(worksheet.get('constraints_issue') or ('当前个人条件不支持继续做 T；请核对盯盘时间与整个余仓风险。' if reason == 'personal' else '大盘、板块和个股本轮弱势证据一致；请重新核对计划，不沿用原回补条件。'))
    elif choice == 'hold' and any(row.get('status') == 'triggered' and row.get('level') in {'L1', 'L5'} for row in worksheet.get('current_risk', [])):
        state, title, detail, reason = 'needs_review', '当前风险线仍触发，重新核对持有依据', '继续持有仍承担后续下跌风险；当前风险事实不能单独替代卖出分析，请核对三维证据和可承受损失。', 'risk'
    elif unresolved_pause:
        state, title, detail, reason = 'evidence_unavailable', '三维暂停依据尚未核清', '本轮部分大盘、板块或个股证据缺失；不能将缺证视为原暂停已解除，继续保留暂停并核对来源。', 'joint_evidence_unknown'
    elif plan.get('watch'):
        watch = plan['watch']
        target = _price(watch.get('price'))
        comparator = watch.get('comparator')
        if check_issue:
            state, title, detail, reason = 'needs_review', '已保存条件需要重新核对', check_issue, 'watch_invalid'
        elif (watch.get('position_snapshot') != worksheet['position_snapshot']
                or watch.get('constraints') != worksheet.get('constraints')
                or watch.get('trade_revision') != worksheet.get('trade_revision')):
            state, title, detail, reason = 'needs_review', '原策略对应的持仓或条件已变化', '即使重新保存了试算，原AI观察条件也需按最新持仓、费用和个人条件重新分析后采用。', 'watch_changed'
        elif choice == 't_watch' and watch.get('action') == 'buy_review' and not worksheet.get('recorded_sale'):
            state, title, detail, reason = 'needs_review', '先核对实际卖出，再观察买回', '做T须先卖后买；尚未读到这笔持仓的实际卖出记录，不能把到价视为回补条件成立。', 't_sale_required'
        elif watch.get('action') == 'sell_review' and not basis.get('sellable_shares'):
            state, title, detail, reason = 'needs_review', '当前没有可卖股份', '请先核对T+1与可卖底仓；条件匹配不解除当日买入不可卖出的限制。', 't_plus_one'
        elif target is None or comparator not in {'at_or_above', 'at_or_below'}:
            state, title, detail, reason = 'needs_review', '已保存观察条件不可核验', '观察价或比较方向不完整，请重新分析并采用；未沿用损坏条件。', 'watch_invalid'
        elif watch.get('direction_schema') != 'direction-v1':
            state, title, detail, reason = 'needs_review', '原方向依据需要重新核对', '旧方案未保存完整方向身份；请比较当前证据后重新分析并采用，不从现在倒填原依据。', 'evidence_changed'
        elif direction_identity(watch.get('evidence_snapshot') or {}) != direction_identity(evidence):
            state, title, detail, reason = 'needs_review', '原策略依据发生变化，重新分析', '大盘、板块或个股的方向/证据可用性与采用时不同；先核对变化，再决定是否保留原条件。', 'evidence_changed'
        else:
            reached = basis['price'] >= target if comparator == 'at_or_above' else basis['price'] <= target
            direction = '达到或高于' if comparator == 'at_or_above' else '达到或低于'
            condition = str(watch.get('condition') or '核对原分析提出的其他条件')
            invalidation = str(watch.get('invalidation') or '依据变化时重新分析')
            if (checks and checks['mode'] == 'registered' and (checks['summary'] in {'review', 'unknown'}
                    or (checks['summary'] == 'waiting' and any(item['role'] == 'prerequisite' and item['status'] == 'unmet'
                                                              for item in checks['items'])))):
                summary = checks['summary']
                state = 'needs_review' if summary == 'review' else 'evidence_unavailable' if summary == 'unknown' else 'waiting'
                title = '原策略失效条件已出现，重新分析' if summary == 'review' else '条件尚缺有效依据' if summary == 'unknown' else '正在跟踪尚未满足的策略前提'
                reason = 'strategy_invalidation' if summary == 'review' else 'strategy_condition_unknown' if summary == 'unknown' else 'strategy_prerequisite'
                pending = [item['label'] for item in checks['items'] if (item['role'] == 'invalidation' and item['status'] == 'met')
                           or (item['role'] != 'manual' and item['status'] == 'unknown')
                           or (item['role'] == 'prerequisite' and item['status'] == 'unmet')]
                detail = '；'.join(pending) + '。先核对这些条件；到价不表示买卖前提成立。'
            elif reached:
                state, title, reason = 'price_watch', '已到策略观察价，核验买卖前提', 'strategy_price'
                detail = f"本次报价 {basis['price']:.2f} 元，{direction}你采用的 {target:.2f} 元。下一步核验：{condition}；取消条件：{invalidation}。报价只证明到价，其他条件尚未自动确认。"
            else:
                title, reason = '正在跟踪你采用的策略条件', 'strategy_waiting'
                detail = f"当前 {basis['price']:.2f} 元，等待报价{direction} {target:.2f} 元后核验：{condition}；取消条件：{invalidation}。"
    elif worksheet.get('scenario_issue'):
        state, title, detail, reason = 'needs_review', '原试算需要重新核对', worksheet['scenario_issue'], 'scenario'
    else:
        params = plan.get('parameters') or {}
        high, low = _price(params.get('sell_price')), _price(params.get('buy_price'))
        if not high:
            state, title, detail, reason = 'missing_prices', '尚未保存后台观察价', '当前高低点仅供页面查看，尚未确认为后台观察条件；可保存试算后再跟进到价。', 'prices'
        elif low and basis['price'] < low:
            state, title, detail, reason = 'below_watch', '已低于保存的观察低价，先复核风险', f"本次报价 {basis['price']:.2f} 元，低于已保存的 {low:.2f} 元。尚未确认止跌；不要沿用旧回补计划。", 'low'
        elif basis['price'] >= high:
            state, title, detail, reason = 'price_watch', '已到保存的观察价，等你复核', f"本次报价 {basis['price']:.2f} 元，已到保存的 {high:.2f} 元。转弱尚未确认；到价不等于应当买卖或已成交。", 'high'
        else:
            detail = f"本次报价 {basis['price']:.2f} 元，尚未到已保存的 {high:.2f} 元。观察价是人工假设，不是经验证的买卖信号。"
    return FollowupObservation(worksheet['position_id'], worksheet['symbol'], worksheet['name'], state, title, detail,
                               current.isoformat(), basis.get('quote_as_of'), worksheet['revision'],
                               plan_key(root, worksheet['position_id']), worksheet['position_snapshot'], reason,
                               bool(choice == 't_watch' and not worksheet['archived'] and (unresolved_pause or basis.get('joint_weakness'))), checks)


def followup_view(root: dict[str, Any], revision: str, snapshot: dict[str, Any], *, current: datetime) -> dict[str, Any]:
    """Reads never manufacture a successful background check."""
    current_plan_key = plan_key(root, snapshot['position_id'])
    try:
        observation = saved_observation(root)
    except DataError as exc:
        return {'mode': root.get('followup_mode', 'off'), 'last_check': None, 'requires_refresh': True, 'issue': str(exc), 'current_plan_key': current_plan_key}
    issue = ''
    if observation:
        if observation.get('revision') != revision or observation.get('position_snapshot') != snapshot:
            issue = '持仓、个人条件、计划或风险规则已变化；下方是上次检查记录，请等待本轮检查或手动检查持仓。'
        else:
            try:
                stamp = datetime.fromisoformat(observation['observed_at'])
                if stamp.tzinfo is None or stamp.utcoffset() is None:
                    raise ValueError('missing timezone')
                age = (current - stamp).total_seconds()
                if age < 0 or stamp.date() != current.date() or age > FollowupDefaults().max_check_age_seconds:
                    issue = '后台检查记录已旧；当前到价与风险状态尚待新一轮检查，不能当作实时跟踪。'
            except (KeyError, TypeError, ValueError):
                issue = '后台记录时间不可核验，等待重新检查。'
    return {'mode': root.get('followup_mode', 'off'), 'last_check': observation, 'requires_refresh': bool(issue), 'issue': issue, 'current_plan_key': current_plan_key}


def notification_key(root: dict[str, Any], observation: dict[str, Any], *, current: datetime) -> str | None:
    """No quota consumed for quiet waiting or off; oscillation reuses one key."""
    mode = root.get('followup_mode', 'off')
    if mode not in {'changes', 'daily'} or observation['state'] not in ATTENTION_STATES:
        return None
    period = current.date().isoformat() if mode == 'daily' else 'changes'
    digest = evidence_digest({'position_id': observation['position_id'], 'plan': observation['plan_key'],
                              'state': observation['state'], 'reason': observation['reason'], 'period': period,
                              **({'conditions': observation['condition_checks']['digest']}
                                 if (observation.get('condition_checks') or {}).get('mode') == 'registered' else {})})
    return 'position-plan|' + digest
