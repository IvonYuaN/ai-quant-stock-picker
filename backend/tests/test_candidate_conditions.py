"""Candidate model predicates survive adoption and off-page checks without orders."""
# ruff: noqa: F811 -- reuse the isolated candidate API fixture
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import json

import pytest
from model_contract import ModelContractError

import candidate_discussion as discussion
import candidate_monitor as monitor
import candidate_observation as resources
import decision_context as dc
import position_discussion as transport
import position_strategy_evidence as sources
from aqsp.core.errors import DataError
from breadth_fixtures import complete_breadth
from test_candidate_observation import NOW, analyze, adoption, opinion, env  # noqa: F401 -- pytest fixture


@pytest.fixture
def direction_sources(env, monkeypatch):
    state = {'market': complete_breadth(NOW, up=800, down=3000), 'sector_change': -1, 'stamp': NOW.isoformat()}
    monkeypatch.setattr(dc, 'load_cached_market_overview', lambda **kwargs: {'sentiment': deepcopy(state['market'])})
    monkeypatch.setattr(sources, 'load_cached_strategy_sectors', lambda codes, **kwargs: {
        code: {'industry': '银行', 'name': '银行', 'membership_as_of': state['stamp'], 'as_of': state['stamp'],
               'change_pct': state['sector_change'], 'source': '合成行业源'} for code in codes})
    return state


def register(env, monkeypatch, *, prerequisites=None, invalidations=None):
    previous = transport.call_model
    async def selected(*args, **kwargs):
        value = json.loads(await previous(*args, **kwargs))
        for action in value['actions']:
            action.update(prerequisite_ids=prerequisites or [], invalidation_ids=invalidations or [])
        return json.dumps(value, ensure_ascii=False)
    monkeypatch.setattr(transport, 'call_model', selected)
    value, result = analyze(env)
    response = env[0].post('/api/candidate-observations/600000/adopt', json=adoption(value, result))
    assert response.status_code == 200, response.text
    return response.json()['data'], result


def item(value, identifier):
    return next(row for row in value['observation']['condition_checks']['items'] if row['id'] == identifier)


def test_candidate_conditions_follow_adopted_selection_even_when_basis_change_requires_review(env, direction_sources, monkeypatch):
    value, result = register(env, monkeypatch, prerequisites=['market:not_weak:v1', 'sector:not_falling:v1'])
    assert result['condition_catalog']['items'][0]['status'] == 'unmet'
    assert value['watch']['condition_contract']['prerequisite_ids'] == ['market:not_weak:v1', 'sector:not_falling:v1']
    frozen = deepcopy(resources.load_store()['resources']['600000']['watch'])
    env[2]['price'] = 9.8
    monitor.check_candidates()
    reached = resources.workspace('600000')
    assert reached['observation']['state'] == 'price_watch'
    assert reached['observation']['condition_checks']['summary'] == 'waiting'
    assert item(reached, 'quote:observed_price:v1')['status'] == 'met'
    assert item(reached, 'sector:not_falling:v1')['status'] == 'unmet'
    direction_sources['market'] = complete_breadth(NOW, up=3000, down=1000)
    direction_sources['sector_change'] = 1
    monitor.check_candidates()
    changed = resources.workspace('600000')
    assert changed['observation']['state'] == 'needs_review'
    assert item(changed, 'market:not_weak:v1')['status'] == 'met'
    assert item(changed, 'sector:not_falling:v1')['status'] == 'met'
    assert changed['observation']['condition_checks']['summary'] == 'manual_required'
    assert resources.load_store()['resources']['600000']['watch'] == frozen
    assert not (resources.root() / 'portfolio.json').exists()


def test_candidate_conditions_source_expiry_is_unknown_even_when_cache_is_new(env, direction_sources, monkeypatch):
    register(env, monkeypatch, prerequisites=['market:not_weak:v1', 'sector:not_falling:v1'])
    direction_sources['market'] = complete_breadth(NOW - timedelta(days=1), up=3000, down=1000)
    direction_sources['stamp'] = (NOW - timedelta(days=1)).isoformat()
    value = resources.workspace('600000')
    assert value['observation']['state'] == 'needs_review'
    for identifier in ('market:not_weak:v1', 'sector:not_falling:v1'):
        assert item(value, identifier)['status'] == 'unknown'
        assert item(value, identifier)['value'] is None
    assert value['observation']['condition_checks']['summary'] == 'unknown'


def test_candidate_conditions_explicit_invalidation_is_visible_without_changing_intent(env, direction_sources, monkeypatch):
    direction_sources['sector_change'] = 1
    value, _ = register(env, monkeypatch, invalidations=['sector:falling:v1'])
    original = deepcopy(value['watch'])
    direction_sources['sector_change'] = -1
    value = resources.workspace('600000')
    assert value['observation']['state'] == 'needs_review'
    assert value['observation']['condition_checks']['summary'] == 'review'
    assert item(value, 'sector:falling:v1')['status'] == 'met'
    assert value['watch'] == original


def test_candidate_conditions_omitted_model_ids_do_not_gain_preset_market_requirements(env):
    value, result = analyze(env)
    assert result['summary']['actions'][0]['prerequisite_ids'] == []
    assert result['summary']['actions'][0]['invalidation_ids'] == []
    adopted = env[0].post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).json()['data']
    assert adopted['watch']['condition_contract']['definitions'] == []
    assert {row['role'] for row in adopted['observation']['condition_checks']['items']} == {'price', 'manual'}


@pytest.mark.parametrize('change', [
    {'prerequisite_ids': ['unlisted:volume:v1']},
    {'prerequisite_ids': ['market:not_weak:v1', 'market:not_weak:v1']},
    {'prerequisite_ids': ['market:not_weak:v1'], 'invalidation_ids': ['market:not_weak:v1']},
])
def test_candidate_conditions_parser_rejects_unknown_duplicate_or_role_conflicts(env, change):
    value = resources.workspace('600000')
    row = opinion()
    row['actions'][0].update(change)
    with pytest.raises(ValueError):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())


def test_candidate_conditions_references_are_not_numeric_trading_claims(env):
    value = resources.workspace('600000')
    row = opinion()
    row['actions'][0].update(prerequisite_ids=['market:not_weak:v1'], invalidation_ids=['sector:falling:v1'])
    parsed = discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    assert parsed['actions'][0]['prerequisite_ids'] == ['market:not_weak:v1']
    schema = discussion.schema_for(discussion.CandidateOpinion, value, set())
    assert set(schema['$defs']['CandidateAction']['properties']['prerequisite_ids']['items']['enum']) == {
        item['id'] for item in value['condition_catalog']['items']}


def test_candidate_conditions_old_discussion_and_watch_remain_legacy_and_unmodified_on_read(env):
    value, result = analyze(env)
    store = resources.load_store()
    old = store['resources']['600000']['discussion']
    old.pop('condition_catalog')
    for action in old['summary']['actions']:
        action.pop('prerequisite_ids')
        action.pop('invalidation_ids')
    resources.save_store(store)
    response = env[0].post('/api/candidate-observations/600000/adopt', json=adoption(value, result))
    assert response.status_code == 200
    assert response.json()['data']['observation']['condition_checks']['mode'] == 'legacy'
    before = resources.store_path().read_bytes()
    current = resources.workspace('600000')
    assert current['watch']['condition'] == result['summary']['actions'][0]['condition']
    assert 'condition_contract' not in current['watch']
    assert resources.store_path().read_bytes() == before
    # The old schema permitted missing reference metadata; preserve the record
    # and expose an unregistered manual check instead of inventing provenance.
    store = resources.load_store()
    store['resources']['600000']['watch']['source_ref'] = {}
    store['resources']['600000']['watch'].pop('price_ref')
    resources.save_store(store)
    current = resources.workspace('600000')
    assert current['observation']['condition_checks']['mode'] == 'legacy'
    assert [row['role'] for row in current['observation']['condition_checks']['items']] == ['manual']


@pytest.mark.parametrize('field', ['digest', 'definitions', 'price_predicate', 'prerequisite_ids'])
def test_candidate_conditions_corrupt_frozen_contract_fails_closed_without_overwriting(env, monkeypatch, field):
    register(env, monkeypatch, prerequisites=['market:not_weak:v1'])
    store = resources.load_store()
    frozen = store['resources']['600000']['watch']['condition_contract']
    if field == 'digest':
        frozen[field] = 'changed'
    elif field == 'price_predicate':
        frozen[field]['price'] = 1
    elif field == 'definitions':
        frozen[field][0]['label'] = '被更改'
    else:
        frozen[field] = ['invented']
    resources.save_store(store)
    before = resources.store_path().read_bytes()
    with pytest.raises(DataError):
        resources.workspace('600000')
    assert resources.store_path().read_bytes() == before


def test_candidate_conditions_monitor_distinguishes_conditions_under_same_review_reason(env, direction_sources, monkeypatch):
    register(env, monkeypatch, prerequisites=['market:not_weak:v1', 'sector:not_falling:v1'])
    batches = []
    monkeypatch.setattr(monitor, 'deliver', lambda batch: batches.append(deepcopy(batch)) or True)
    direction_sources['market'] = complete_breadth(NOW, up=3000, down=1000)
    monitor.check_candidates(notify=True)
    direction_sources['sector_change'] = 1
    monitor.check_candidates(notify=True)
    assert batches[0]['observation']['state'] == batches[1]['observation']['state'] == 'needs_review'
    assert batches[0]['observation']['reason'] == batches[1]['observation']['reason'] == 'research_changed'
    assert batches[0]['key'] != batches[1]['key']
    env[2]['price'] = 10.3
    monitor.check_candidates(notify=True)
    assert batches[-1]['key'] == batches[-2]['key']
    assert '所属行业当前方向不再下跌：已满足' in monitor._render(batches[-1])


def test_candidate_conditions_outbound_revalidates_contract_semantic_state_and_frozen_source(env, direction_sources, monkeypatch):
    register(env, monkeypatch, prerequisites=['market:not_weak:v1', 'sector:not_falling:v1'])
    batches = []
    monkeypatch.setattr(monitor, 'deliver', lambda batch: batches.append(deepcopy(batch)) or True)
    direction_sources['market'] = complete_breadth(NOW, up=3000, down=1000)
    monitor.check_candidates(notify=True)
    batch = batches[-1]
    assert monitor._validate(batch)
    changed = deepcopy(batch)
    changed['observation']['condition_checks']['digest'] = 'changed'
    assert not monitor._validate(changed)
    changed = deepcopy(batch)
    changed['observation']['condition_checks']['items'][0]['as_of'] = (NOW - timedelta(days=1)).isoformat()
    assert not monitor._validate(changed)
    direction_sources['sector_change'] = 1
    assert not monitor._validate(batch)


def test_candidate_conditions_same_status_time_and_quote_refresh_do_not_change_state_digest(env, direction_sources, monkeypatch):
    value, _ = register(env, monkeypatch, prerequisites=['market:not_weak:v1'])
    first = value['observation']['condition_checks']['digest']
    later = NOW + timedelta(minutes=1)
    for module in (resources, discussion, monitor):
        monkeypatch.setattr(module, 'now_shanghai', lambda: later)
    env[2].update(price=10.4, quote_time='20261027100100')
    direction_sources['market'] = complete_breadth(later, up=900, down=3000)
    direction_sources['stamp'] = later.isoformat()
    current = resources.workspace('600000')
    assert current['observation']['condition_checks']['digest'] == first


@pytest.mark.parametrize('field,value', [('price', 1), ('comparator', 'at_or_above'),
    ('condition', '改成另一前提'), ('invalidation', '改成另一失效'), ('discussion_id', 'another-discussion'),
    ('action_index', 1)])
def test_candidate_conditions_top_level_intent_cannot_disagree_with_frozen_contract(env, monkeypatch, field, value):
    register(env, monkeypatch, prerequisites=['market:not_weak:v1'])
    store = resources.load_store()
    store['resources']['600000']['watch'][field] = value
    resources.save_store(store)
    before = resources.store_path().read_bytes()
    with pytest.raises(DataError):
        resources.workspace('600000')
    assert resources.store_path().read_bytes() == before


def test_candidate_conditions_defer_has_only_original_premise_review_without_price_task(env):
    value, result = analyze(env)
    body = adoption(value, result, choice='defer', action_index=None, comparator=None)
    response = env[0].post('/api/candidate-observations/600000/adopt', json=body)
    assert response.status_code == 200, response.text
    value = response.json()['data']
    assert value['observation']['state'] == 'deferred'
    checks = value['observation']['condition_checks']
    assert [row['role'] for row in checks['items']] == ['manual']
    assert checks['items'][0]['id'] == 'manual:premises:v1'
    assert '保持暂缓' in checks['items'][0]['detail'] and '可以买入' not in checks['items'][0]['detail']


def test_candidate_conditions_legacy_notifications_keep_existing_semantic_key(env, monkeypatch):
    from position_route_review import evidence_digest
    value, result = analyze(env)
    store = resources.load_store()
    store['resources']['600000']['discussion'].pop('condition_catalog')
    resources.save_store(store)
    env[0].post('/api/candidate-observations/600000/adopt', json=adoption(value, result))
    env[2]['price'] = 9.8
    batches = []
    monkeypatch.setattr(monitor, 'deliver', lambda batch: batches.append(deepcopy(batch)) or True)
    monitor.check_candidates(notify=True)
    watch = resources.load_store()['resources']['600000']['watch']
    expected = 'candidate-observation|' + evidence_digest({'symbol': '600000', 'watch_id': watch['id'], 'state': 'price_watch', 'reason': 'price'})
    assert batches[0]['key'] == expected


@pytest.mark.parametrize('field,value', [('price', 1), ('source', '其他来源'), ('as_of', '2026-10-26T10:00:00+08:00')])
def test_candidate_conditions_frozen_source_reference_cannot_disagree_with_contract(env, monkeypatch, field, value):
    register(env, monkeypatch, prerequisites=['market:not_weak:v1'])
    store = resources.load_store()
    store['resources']['600000']['watch']['source_ref'][field] = value
    resources.save_store(store)
    with pytest.raises(DataError):
        resources.workspace('600000')


@pytest.mark.parametrize('status,label', [('met', '失效条件已触发，需复核'), ('unmet', '失效条件未触发')])
def test_candidate_conditions_notification_names_invalidation_as_trigger_not_support(env, direction_sources, monkeypatch, status, label):
    value, _ = register(env, monkeypatch, invalidations=['sector:falling:v1'])
    observed = deepcopy(value['observation'])
    check = next(row for row in observed['condition_checks']['items'] if row['role'] == 'invalidation')
    check['status'] = status
    rendered = monitor._render({'observation': observed, 'watch': value['watch'], 'name': value['name'], 'symbol': '600000'})
    assert '所属行业当前方向下跌：' + label in rendered
    assert '所属行业当前方向下跌：已满足' not in rendered


def test_candidate_conditions_defer_notification_preserves_old_frozen_rendering(env):
    value, result = analyze(env)
    body = adoption(value, result, choice='defer', action_index=None, comparator=None)
    value = env[0].post('/api/candidate-observations/600000/adopt', json=body).json()['data']
    batch = {'observation': value['observation'], 'watch': value['watch'], 'name': value['name'], 'symbol': '600000'}
    assert '保持暂缓，人工复核' in monitor._render(batch)
    assert '报价只证明到达观察价' not in monitor._render(batch)
    old = deepcopy(batch)
    old['observation'].pop('condition_checks')
    assert '报价只证明到达观察价' in monitor._render(old)


@pytest.mark.parametrize('field', ['prerequisite_ids', 'invalidation_ids'])
def test_candidate_conditions_defer_cannot_promise_ignored_action_predicates(env, field):
    value = resources.workspace('600000')
    row = opinion()
    row['actions'][0].update(choice='defer', price_ref=None)
    row['actions'][0][field] = ['sector:falling:v1']
    with pytest.raises(ModelContractError, match='可监控条件'):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
