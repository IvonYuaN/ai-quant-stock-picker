"""Persist bounded plan checks from existing LIVE evidence, no extra quote I/O."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import alert_events as ae
import portfolio as pf
from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock
from position_followup import ATTENTION_STATES, FOLLOWUP_MODES, FollowupDefaults, followup_view, notification_key, observe_plan, plan_key, risk_rule_key, saved_observation
from position_plan_service import (PlanConflictError, _checked_root, _revision, _root, _snapshot,
                                   build_position_worksheet, read_position_plan)


def save_followup_mode(position_id: str, mode: str, expected_mode: str,
                       expected_position: dict[str, Any] | None, expected_revision: str | None) -> dict[str, Any] | None:
    """Explicit outbound preference has its own CAS; it does not rewrite a plan."""
    if mode not in FOLLOWUP_MODES or expected_mode not in FOLLOWUP_MODES:
        raise ValueError('计划通知模式必须是 off、changes 或 daily')
    if not Path(pf._pf_file()).is_file():
        return None
    with ae._LOCK, advisory_lock(ae._file()), pf._LOCK, advisory_lock(pf._pf_file()):
        if not Path(pf._pf_file()).is_file():
            return None
        data, events = pf._load(), ae._load()
        root, _ = _checked_root(position_id, expected_position, expected_revision, data, events)
        if root.get('followup_mode', 'off') != expected_mode:
            raise PlanConflictError('通知设置已在其他页面变化，请刷新后重新核对')
        root['followup_mode'] = mode
        # Notification preferences do not expire a human draft or model result.
        data.setdefault('position_reviews', {})[position_id] = root
        pf._save(data)
    return read_position_plan(position_id)


def list_followups() -> dict[str, Any]:
    """Return durable relevant observations without fetching or claiming liveness."""
    import os
    if not os.path.isfile(pf._pf_file()):
        return {'items': []}
    with ae._LOCK, pf._LOCK:
        data, events = pf._load(), ae._load()
    items = []
    for holding in data.get('holdings', []):
        position_id = holding.get('position_id')
        root = data.get('position_reviews', {}).get(position_id)
        if not root or not root.get('followup'):
            continue
        snapshot = _snapshot(holding)
        view = followup_view(root, _revision(root, data, events, holding), snapshot, current=now_shanghai())
        observation = view['last_check']
        if observation and observation['state'] in ATTENTION_STATES:
            items.append({**observation, 'requires_refresh': view['requires_refresh'], 'issue': view['issue']})
    return {'items': sorted(items, key=lambda row: row['observed_at'], reverse=True)}


def _validate_candidate(candidate: dict[str, Any], *, source_portfolio: dict[str, Any] | None = None,
                        source_contexts: dict[str, dict[str, Any]] | None = None) -> bool:
    with ae._LOCK, pf._LOCK:
        data, events = pf._load(), ae._load()
    position_id = candidate['position_id']
    holding = next((row for row in data.get('holdings', []) if row.get('position_id') == position_id), None)
    root = data.get('position_reviews', {}).get(position_id)
    if not holding or not root or root.get('followup_mode', 'off') != candidate['mode']:
        return False
    try:
        observation = saved_observation(root) or {}
    except DataError:
        return False
    if observation.get('revision') != _revision(root, data, events, holding):
        return False
    if any(candidate.get(key) != observation.get(key) for key in ('revision', 'position_snapshot', 'plan_key', 'quote_as_of', 'detail')):
        return False
    current = now_shanghai()
    try:
        stamp = datetime.fromisoformat(candidate['observed_at'])
        age = (current - stamp).total_seconds()
        if stamp.utcoffset() is None or age < 0 or age > FollowupDefaults().max_check_age_seconds:
            return False
    except (KeyError, TypeError, ValueError):
        return False
    if candidate['state'] in {'price_watch', 'below_watch', 'paused'}:
        from decision_context import position_quote_issue
        if position_quote_issue({'as_of': candidate.get('quote_as_of')}, checked_at=current):
            return False
    frozen_checks = candidate.get('condition_checks')
    watch = (root.get('review_plan') or {}).get('watch') or {}
    if frozen_checks is not None or 'condition_contract' in watch:
        from position_followup import watch_contract
        import strategy_conditions as conditions
        try:
            contract = watch_contract(watch, position_id)
            if not isinstance(frozen_checks, dict) or observation.get('condition_checks') != frozen_checks:
                return False
            if frozen_checks.get('digest') != conditions.semantic_state(frozen_checks.get('items', []),
                    contract_digest=contract['digest'] if contract else None, mode=frozen_checks.get('mode', 'legacy')):
                return False
            # Reuse this round's quote batch; only public caches/history are
            # read again. Outbound checks never fetch another quote per position.
            if source_portfolio is None:
                return False
            source_holding = next((row for row in source_portfolio.get('holdings', [])
                                   if row.get('position_id') == position_id), None)
            if source_holding is None:
                return False
            from position_strategy_evidence import load_strategy_context
            context = (source_contexts or {}).get(source_holding['code'])
            if context is None:
                context = load_strategy_context(source_holding, [row['code'] for row in source_portfolio['holdings']],
                    Path(pf._tenant_dir()) / 'live-risk-history.db', checked_at=current)
            worksheet = build_position_worksheet(position_id, source_portfolio, data, events, current=current, context=context)
            if (worksheet is None or worksheet.get('strategy_condition_issue')
                    or worksheet['revision'] != candidate['revision'] or worksheet['position_snapshot'] != candidate['position_snapshot']):
                return False
            latest = observe_plan(worksheet, root, current=current).to_dict()
            current_checks = latest.get('condition_checks')
            if (latest['state'] != candidate['state'] or latest['reason'] != candidate.get('reason')
                    or not current_checks or current_checks['digest'] != frozen_checks['digest']):
                return False
            sources = {(item['id'], item['role']): item.get('source') for item in current_checks['items']}
            from decision_context import position_quote_issue
            for item in frozen_checks['items']:
                if item.get('source') != sources.get((item['id'], item['role'])):
                    return False
                if item['role'] != 'manual' and item['status'] in {'met', 'unmet'}:
                    if not item.get('source') or position_quote_issue({'as_of': item.get('as_of')}, checked_at=current):
                        return False
        except (ValueError, KeyError, TypeError, DataError):
            return False
    return observation.get('state') == candidate['state'] and notification_key(root, observation, current=current) == candidate['key']


def check_plan_followups(portfolio: dict[str, Any], contexts: dict[str, dict[str, Any]], *, current: datetime,
                         notify: bool = False, risk_notified_positions: set[str] | None = None,
                         expected_plan_keys: dict[str, str] | None = None,
                         expected_rule_keys: dict[str, str] | None = None,
                         changed_symbols: set[str] | None = None) -> dict[str, Any]:
    """Reuse one source batch, CAS at commit, then validate frozen outbound work.

    A quote remains a point observation. A mutation after the final validation
    can race with channel I/O; notification text always identifies its as-of time.
    """
    with ae._LOCK, pf._LOCK:
        data, events = deepcopy(pf._load()), ae._load()
    roots = data.get('position_reviews', {})
    candidates, observations, skipped = [], [], []
    for position_id, root in roots.items():
        if not root.get('review_plan', {}).get('choice'):
            continue
        symbol = root.get('symbol', '')
        try:
            previous = saved_observation(root)
            if previous and previous['state'] == 'exited':
                continue
            if expected_plan_keys is not None and expected_plan_keys.get(position_id) != plan_key(root, position_id):
                raise PlanConflictError('取价期间人工计划或个人条件已变化，等待下一轮完整检查')
            if symbol in (changed_symbols or set()) or (expected_rule_keys is not None
                    and expected_rule_keys.get(position_id) != risk_rule_key(data.get('risk_rules', []), position_id)):
                raise PlanConflictError('本轮风险规则或持仓已变化，等待下一轮完整检查')
            saved_at = datetime.fromisoformat(root.get('updated_at') or root['review_plan']['updated_at'])
            if saved_at.tzinfo is None or saved_at > current:
                raise PlanConflictError('本轮观察早于计划保存时间，等待下一轮检查')
            worksheet = build_position_worksheet(position_id, portfolio, data, events, current=current, context=contexts.get(symbol))
            if worksheet is None:
                continue
            observation = observe_plan(worksheet, root, current=current).to_dict()
            with ae._LOCK, advisory_lock(ae._file()), pf._LOCK, advisory_lock(pf._pf_file()):
                latest, latest_events = pf._load(), ae._load()
                latest_root = latest.get('position_reviews', {}).get(position_id)
                if latest_root is None:
                    raise PlanConflictError('当前计划已变化')
                if not worksheet['archived']:
                    _checked_root(position_id, worksheet['position_snapshot'], worksheet['revision'], latest, latest_events)
                elif (any(row.get('position_id') == position_id for row in latest.get('holdings', []))
                      or _root(latest, latest_events, position_id) != root):
                    raise PlanConflictError('清仓历史已变化')
                previous = saved_observation(latest_root)
                if previous and previous['observed_at'] > observation['observed_at']:
                    raise PlanConflictError('已有更新的检查记录')
                # Only this field changes: do not version-bump user intent.
                latest_root['followup'] = observation
                pf._save(latest)
                root = deepcopy(latest_root)
            observations.append(observation)
            key = notification_key(root, observation, current=current)
            # Existing same-position risk notices already cover these risk facts.
            covered = position_id in (risk_notified_positions or set()) and observation.get('reason') in {'risk', 'joint_weakness'}
            if key and not covered:
                candidates.append({**observation, 'key': key, 'mode': root.get('followup_mode', 'off'),
                                   'plan_href': '/my/holdings/' + position_id + '/plan'})
        except (PlanConflictError, ValueError, DataError) as exc:
            skipped.append({'symbol': symbol, 'level': 'PLAN', 'reason': str(exc)})
    messages: tuple[str, ...] = ()
    suppressed = False
    if notify and candidates:
        from position_followup_delivery import deliver_followup_notifications
        delivery = deliver_followup_notifications(candidates, validate=lambda candidate: _validate_candidate(candidate, source_portfolio=portfolio, source_contexts=contexts))
        messages, suppressed = delivery.notify, delivery.suppressed
    return {'observations': observations, 'skipped': skipped, 'notify': list(messages), 'notify_suppressed': suppressed}
