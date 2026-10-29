"""Position predicates retain human intent, source clocks and hard constraints."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import json

import pytest

import portfolio as pf
import position_discussion as discussion
import position_followup as followup
import position_followup_service as service
import position_plan_service as plans
import position_strategy_evidence as sources
import strategy_conditions as conditions
from breadth_fixtures import complete_breadth
from test_position_strategy_watch import strategy  # noqa: F401
from test_position_discussion import discussion_client, OPINION  # noqa: F401
from test_position_action_plan import plan_client, NOW  # noqa: F401

# ruff: noqa: F811 -- imported fixtures intentionally injected


def adopt_registered(strategy, *, choice='reduce', prerequisites=None, invalidations=None):
    client, position_id, body, _, _ = strategy
    data = pf._load()
    result = data['position_reviews'][position_id]['comparison']
    action = result['summary']['actions'][0]
    action.update(prerequisite_ids=prerequisites or [], invalidation_ids=invalidations or [])
    pf._save(data)
    response = client.post(f'/api/positions/{position_id}/strategy-watch', json=body | {'choice': choice})
    assert response.status_code == 200, response.text
    return response.json()['data'], pf._load()['position_reviews'][position_id]


def current_evidence(value, root):
    from strategy_market_source import BREADTH_SOURCE
    facts = {'market': {'status': 'strong', 'source': BREADTH_SOURCE, 'as_of': NOW.isoformat()},
             'sector': {'status': 'rising', 'source': '合成行业源', 'as_of': NOW.isoformat(), 'industry': '合成银行'}}
    value['route_review']['evidence'].update(deepcopy(facts))
    value['review_plan']['watch']['evidence_snapshot'].update(deepcopy(facts))
    root['review_plan']['watch']['evidence_snapshot'].update(deepcopy(facts))
    value['current_risk'] = []
    return facts


@pytest.mark.parametrize('choice', ['hold', 'reduce', 't_watch'])
def test_position_conditions_freeze_position_identity_when_route_adopted(strategy, choice):
    value, root = adopt_registered(strategy, choice=choice, prerequisites=['sector:not_falling:v1'])
    frozen = value['review_plan']['watch']['condition_contract']
    assert frozen['schema_version'] == 2
    assert frozen['origin']['kind'] == 'position' and frozen['origin']['choice'] == choice
    assert frozen['origin']['position_id'] == value['position_id']
    assert frozen['prerequisite_ids'] == ['sector:not_falling:v1']
    assert value['strategy_condition_checks']['mode'] == 'registered'
    assert value['strategy_condition_checks']['schema_version'] == 1
    assert followup.watch_contract(root['review_plan']['watch'], value['position_id']) == frozen


def test_position_conditions_wait_for_prerequisite_even_when_price_reached(strategy):
    value, root = adopt_registered(strategy, prerequisites=['market:not_weak:v1'])
    current_evidence(value, root)
    value['basis']['price'] = value['review_plan']['watch']['price']
    value['route_review']['evidence']['market']['status'] = 'weak'
    # Keep the same premise identity to isolate predicate precedence.
    value['review_plan']['watch']['evidence_snapshot']['market']['status'] = 'weak'
    observed = followup.observe_plan(value, root, current=NOW)
    assert observed.state == 'waiting' and observed.reason == 'strategy_prerequisite'
    assert '市场宽度不再偏弱' in observed.detail
    assert next(row for row in observed.condition_checks['items'] if row['role'] == 'price')['status'] == 'met'


@pytest.mark.parametrize('constraint', ['risk', 'personal', 'sale', 'direction'])
def test_position_conditions_remain_visible_behind_existing_hard_constraints(strategy, constraint):
    choice = 't_watch' if constraint == 'personal' else 'hold'
    value, root = adopt_registered(strategy, choice=choice, invalidations=['sector:falling:v1'])
    current_evidence(value, root)
    if constraint == 'risk':
        value['current_risk'] = [{'status': 'triggered', 'level': 'L1'}]
    elif constraint == 'personal':
        value['basis']['personal_pause_t'] = True
    elif constraint == 'sale':
        value['sale_requires_review'] = True
    else:
        value['route_review']['evidence']['sector']['status'] = 'falling'
    observed = followup.observe_plan(value, root, current=NOW)
    assert observed.reason == {'risk': 'risk', 'personal': 'personal', 'sale': 'sale', 'direction': 'evidence_changed'}[constraint]
    assert observed.condition_checks and observed.condition_checks['mode'] == 'registered'
    if constraint == 'direction':
        assert observed.condition_checks['summary'] == 'review'


def test_position_conditions_read_clock_does_not_renew_expired_market_or_sector(strategy):
    value, root = adopt_registered(strategy, prerequisites=['market:not_weak:v1', 'sector:not_falling:v1'])
    current_evidence(value, root)
    later = NOW + timedelta(minutes=31)
    value['basis']['quote_as_of'] = later.isoformat()
    checks = followup.strategy_condition_checks(value, current=later)
    assert all(row['status'] == 'unknown' for row in checks['items'] if row['role'] == 'prerequisite')
    assert next(row for row in checks['items'] if row['role'] == 'price')['status'] in {'met', 'unmet'}


@pytest.mark.parametrize('mutation', ['choice', 'source', 'price', 'origin', 'text'])
def test_position_conditions_reject_tampering_without_mutating_saved_watch(strategy, mutation):
    value, root = adopt_registered(strategy, prerequisites=['sector:not_falling:v1'])
    watch = value['review_plan']['watch']
    if mutation == 'choice':
        watch['choice'] = 'hold'
    elif mutation == 'source':
        watch['price_ref']['source'] = 'forged'
    elif mutation == 'price':
        watch['price'] += 1
    elif mutation == 'origin':
        watch['condition_contract']['origin']['position_id'] = 'other-position'
    else:
        watch['condition'] = 'changed condition'
    with pytest.raises(ValueError):
        followup.watch_contract(watch, value['position_id'])
    observation = followup.observe_plan(value, root, current=NOW)
    assert observation.reason == 'watch_invalid' and observation.condition_checks is None


def test_position_conditions_old_watch_keeps_manual_text_without_inferred_ids(strategy):
    value, root = adopt_registered(strategy)
    value['review_plan']['watch'].pop('condition_contract')
    checks = followup.strategy_condition_checks(value, current=NOW)
    assert checks['mode'] == 'legacy'
    assert [row['role'] for row in checks['items']] == ['price', 'manual']
    assert '未逐项登记' in checks['items'][-1]['detail']


def test_position_conditions_candidate_v1_remains_valid_and_identity_separate(strategy):
    from test_strategy_conditions import contract
    candidate = contract(['market:not_weak:v1'])
    original = deepcopy(candidate)
    assert conditions.validate_contract(candidate) == original
    value, _ = adopt_registered(strategy, prerequisites=['market:not_weak:v1'])
    with pytest.raises(ValueError):
        watch = {**value['review_plan']['watch'], 'condition_contract': candidate}
        followup.watch_contract(watch, value['position_id'])
    assert candidate == original


@pytest.mark.parametrize('ids', [['invented:v1'], ['market:weak:v1', 'market:not_weak:v1'], ['sector:falling:v1', 'sector:falling:v1']])
def test_position_conditions_parser_rejects_invalid_ids_when_model_cites_directory(ids):
    payload = deepcopy(OPINION)
    payload['actions'][0]['prerequisite_ids'] = ids
    with pytest.raises(ValueError):
        discussion._parse(json.dumps(payload), discussion.RoleOpinion, {'quote': {}, 'holding': {}}, {'price_0': {}}, set(),
                          {'pause_t': False, 'sellable_shares': 100, 'condition_ids': [item.id for item in conditions.DEFINITIONS]})


def test_position_conditions_model_receives_optional_catalog_without_forced_thesis(strategy):
    _, _, _, result, calls = strategy
    assert len(result['condition_catalog']['items']) == 4
    assert all(len(call['condition_catalog']['items']) == 4 for call in calls)
    schema = calls[0]['output_schema']['$defs']['Observation']['properties']
    assert 'prerequisite_ids' not in calls[0]['output_schema']['$defs']['Observation']['required']
    assert result['summary']['actions'][0]['prerequisite_ids'] == []
    assert len(schema['prerequisite_ids']['items']['enum']) == 4


def test_position_conditions_real_context_projection_retains_industry_identity(strategy, monkeypatch):
    import decision_context as dc
    value, _ = adopt_registered(strategy)
    monkeypatch.setattr(dc, 'load_cached_market_overview', lambda **kwargs: {'sentiment': complete_breadth(NOW, up=3000, down=1000)})
    monkeypatch.setattr(sources, 'load_cached_strategy_sectors', lambda codes, **kwargs: {code: {
        'industry': '合成银行', 'name': '合成银行', 'source': '合成行业源', 'as_of': NOW.isoformat(),
        'membership_as_of': NOW.isoformat(), 'change_pct': 1} for code in codes})
    monkeypatch.setattr(sources, '_indices', lambda current: None)
    current = plans.read_position_plan(value['position_id'])
    assert current['route_review']['evidence']['sector']['industry'] == '合成银行'
    facts = followup.condition_facts(current, current=NOW)
    assert facts['sector'].valid and facts['sector'].value == 'rising'


def test_position_conditions_notification_identity_changes_only_for_semantic_conditions(strategy):
    value, root = adopt_registered(strategy, prerequisites=['sector:not_falling:v1'])
    current_evidence(value, root)
    root['followup_mode'] = 'changes'
    value['basis']['price'] = value['review_plan']['watch']['price']
    first = followup.observe_plan(value, root, current=NOW).to_dict()
    value['basis']['price'] += 0.1
    second = followup.observe_plan(value, root, current=NOW).to_dict()
    assert followup.notification_key(root, first, current=NOW) == followup.notification_key(root, second, current=NOW)
    changed = deepcopy(first)
    changed['condition_checks']['digest'] = 'different-condition-status'
    assert followup.notification_key(root, first, current=NOW) != followup.notification_key(root, changed, current=NOW)


def test_position_conditions_outbound_reuses_batch_and_rejects_expired_frozen_sources(strategy, monkeypatch):
    value, root = adopt_registered(strategy, prerequisites=['sector:not_falling:v1'])
    facts = current_evidence(value, root)
    root['followup_mode'] = 'changes'
    value['basis']['price'] = value['review_plan']['watch']['price']
    data = pf._load()
    data['position_reviews'][value['position_id']] = root
    pf._save(data)
    portfolio = pf.get_portfolio()
    portfolio['holdings'][0]['quote']['price'] = value['basis']['price']
    observed = service.check_plan_followups(portfolio, {value['symbol']: facts}, current=NOW)['observations'][0]
    root = pf._load()['position_reviews'][value['position_id']]
    candidate = {**observed, 'mode': 'changes', 'key': followup.notification_key(root, observed, current=NOW)}
    monkeypatch.setattr(sources, 'load_strategy_context', lambda *args, **kwargs: facts)
    monkeypatch.setattr(service, 'now_shanghai', lambda: NOW)
    monkeypatch.setattr(pf, 'get_portfolio', lambda: (_ for _ in ()).throw(AssertionError('extra quote I/O')))
    assert service._validate_candidate(candidate, source_portfolio=portfolio)
    monkeypatch.setattr(sources, 'load_strategy_context', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('lost source batch')))
    assert service._validate_candidate(candidate, source_portfolio=portfolio, source_contexts={value['symbol']: facts})
    monkeypatch.setattr(sources, 'load_strategy_context', lambda *args, **kwargs: facts)
    assert not service._validate_candidate(candidate)
    forged = deepcopy(candidate)
    forged['condition_checks']['items'][0]['status'] = 'unknown'
    assert not service._validate_candidate(forged, source_portfolio=portfolio)
    # Even a fresh observation cannot renew a stale original source in a frozen message.
    old = deepcopy(candidate)
    old['condition_checks']['items'][0]['as_of'] = (NOW - timedelta(minutes=31)).isoformat()
    data = pf._load()
    data['position_reviews'][value['position_id']]['followup']['condition_checks'] = old['condition_checks']
    pf._save(data)
    assert not service._validate_candidate(old, source_portfolio=portfolio)


@pytest.mark.parametrize('reference', [['damaged'], 'damaged', 1, None])
def test_position_conditions_preserve_page_and_followup_when_price_reference_corrupt(strategy, reference):
    value, root = adopt_registered(strategy, prerequisites=['sector:not_falling:v1'])
    data = pf._load()
    data['position_reviews'][value['position_id']]['review_plan']['watch']['price_ref'] = reference
    pf._save(data)
    current = plans.read_position_plan(value['position_id'])
    assert current['strategy_condition_checks'] is None
    assert current['strategy_condition_issue']
    observed = followup.observe_plan(current, data['position_reviews'][value['position_id']], current=NOW)
    assert observed.state == 'needs_review' and observed.reason == 'watch_invalid'
    assert observed.condition_checks is None


def test_position_conditions_notification_explains_changed_items_without_changing_old_messages(strategy):
    import position_followup_delivery as delivery
    value, root = adopt_registered(strategy, invalidations=['sector:falling:v1'])
    current_evidence(value, root)
    value['route_review']['evidence']['sector']['status'] = 'falling'
    observation = followup.observe_plan(value, root, current=NOW).to_dict()
    message = delivery._render(observation)
    assert '条件核对' in message and '已触发' in message
    old = {key: row for key, row in observation.items() if key != 'condition_checks'}
    assert '条件核对' not in delivery._render(old)


def test_position_conditions_buyback_watch_does_not_skip_actual_sale_when_price_matches(strategy):
    value, root = adopt_registered(strategy, choice='t_watch')
    current_evidence(value, root)
    watch = value['review_plan']['watch']
    watch['action'] = 'buy_review'
    frozen = watch['condition_contract']
    frozen['origin']['action'] = 'buy_review'
    frozen['digest'] = conditions.digest({key: row for key, row in frozen.items() if key != 'digest'})
    value['basis']['price'] = watch['price']
    observation = followup.observe_plan(value, root, current=NOW)
    assert observation.reason == 't_sale_required'
    assert observation.condition_checks and observation.condition_checks['summary'] == 'manual_required'
