"""Human-adopted qualitative waits never invent executable prices or trades."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

import portfolio as pf
import position_discussion as discussion
import position_followup as followup
import position_plan_service as plans
import position_strategy_evidence as sources
import strategy_conditions as conditions
from strategy_market_source import BREADTH_SOURCE
from test_position_action_plan import NOW, plan_client  # noqa: F401
from test_position_discussion import discussion_client  # noqa: F401
from test_position_strategy_watch import strategy  # noqa: F401

# ruff: noqa: F811 -- reusable isolated fixtures


@pytest.fixture
def directional(request, discussion_client, monkeypatch):
    facts = {'market': {'status': 'neutral', 'source': BREADTH_SOURCE, 'as_of': NOW.isoformat()},
             'sector': {'status': 'falling', 'source': '合成行业源', 'industry': '合成行业', 'as_of': NOW.isoformat()}}
    monkeypatch.setattr(sources, 'load_strategy_context', lambda *args, **kwargs: deepcopy(facts))
    original = discussion.call_model
    async def qualitative(*args, **kwargs):
        result = json.loads(await original(*args, **kwargs))
        for action in result['actions']:
            action.update(action='observe', price_ref=None, condition='等所属行业当前方向不再下跌后人工复核',
                          invalidation='若原依据失效则重新核对', prerequisite_ids=['sector:not_falling:v1'], invalidation_ids=[])
        return json.dumps(result, ensure_ascii=False)
    monkeypatch.setattr(discussion, 'call_model', qualitative)
    client, position_id, body, result, calls = request.getfixturevalue('strategy')
    assert result['summary']['actions'][0]['price_ref'] is None
    return client, position_id, body | {'comparator': None}, facts, calls


def adopted(directional):
    client, position_id, body, facts, calls = directional
    response = client.post(f'/api/positions/{position_id}/strategy-watch', json=body)
    assert response.status_code == 200, response.text
    return response.json()['data'], pf._load()['position_reviews'][position_id], facts


def test_direction_wait_freezes_human_intent_without_price_or_trade_when_adopted(directional) -> None:
    before = deepcopy(pf._load())
    value, root, _ = adopted(directional)
    watch = value['review_plan']['watch']
    assert all(watch[key] is None for key in ('price', 'price_ref', 'comparator', 'source_price_ref'))
    contract = watch['condition_contract']
    assert contract['schema_version'] == 3 and contract['price_predicate'] is None
    assert contract['origin']['action'] == 'observe' and contract['origin']['price_ref'] is None
    assert followup.watch_contract(watch, value['position_id']) == contract
    assert value['strategy_direction_progress']
    assert not any(row['role'] == 'price' for row in value['strategy_condition_checks']['items'])
    assert {key: row for key, row in pf._load().items() if key != 'position_reviews'} == {
        key: row for key, row in before.items() if key != 'position_reviews'}
    assert len(root['watch_audit']) == 1


def test_direction_wait_tracks_expected_progress_and_notifies_once_when_registered_premise_matches(directional) -> None:
    value, root, facts = adopted(directional)
    first = followup.observe_plan(value, root, current=NOW).to_dict()
    assert first['state'] == 'waiting' and first['reason'] == 'strategy_prerequisite'
    assert followup.notification_key(root | {'followup_mode': 'changes'}, first, current=NOW) is None
    facts['sector']['status'] = 'rising'
    current = plans.read_position_plan(value['position_id'])
    assert current['strategy_review_context']['material_changed'] and current['strategy_direction_progress']
    changed = followup.observe_plan(current, root, current=NOW).to_dict()
    assert changed['state'] == 'needs_review' and changed['reason'] == 'strategy_direction_review'
    assert '不指定买卖价位' in changed['detail'] and '不表示可以买卖' in changed['detail']
    key = followup.notification_key(root | {'followup_mode': 'changes'}, changed, current=NOW)
    assert key
    facts['sector']['summary'] = '报价和文字刷新不改变方向条件状态'
    repeated = followup.observe_plan(plans.read_position_plan(value['position_id']), root, current=NOW).to_dict()
    assert followup.notification_key(root | {'followup_mode': 'changes'}, repeated, current=NOW) == key


@pytest.mark.parametrize('patch', [
    {'industry': '另一个行业'}, {'source': '另一个来源'},
])
def test_direction_wait_requires_reanalysis_when_source_or_industry_changes(directional, patch) -> None:
    value, root, facts = adopted(directional)
    facts['sector'].update(status='rising', **patch)
    current = plans.read_position_plan(value['position_id'])
    assert not current['strategy_direction_progress']
    assert followup.observe_plan(current, root, current=NOW).reason == 'evidence_changed'


def test_direction_wait_retains_unregistered_market_identity_when_sector_improves(directional) -> None:
    value, root, facts = adopted(directional)
    facts['sector']['status'] = 'rising'
    facts['market']['status'] = 'weak'
    current = plans.read_position_plan(value['position_id'])
    assert not current['strategy_direction_progress']
    assert followup.observe_plan(current, root, current=NOW).reason == 'evidence_changed'


def test_direction_wait_marks_unknown_then_recovers_when_original_source_returns(directional) -> None:
    value, root, facts = adopted(directional)
    facts['sector']['as_of'] = '2026-10-22T10:00:00+08:00'
    current = plans.read_position_plan(value['position_id'])
    observed = followup.observe_plan(current, root, current=NOW)
    assert observed.state == 'evidence_unavailable' and observed.reason == 'strategy_condition_unknown'
    facts['sector'].update(as_of=NOW.isoformat(), status='flat')
    assert followup.observe_plan(plans.read_position_plan(value['position_id']), root, current=NOW).reason == 'strategy_direction_review'


@pytest.mark.parametrize('patch', [
    {'comparator': 'at_or_above'}, {'action': 'sell_review'}, {'action': 'buy_review'}, {'action': 'pause'},
    {'prerequisite_ids': []}, {'prerequisite_ids': ['forged']},
    {'prerequisite_ids': ['sector:not_falling:v1', 'sector:falling:v1']},
])
def test_direction_wait_rejects_invalid_qualitative_adoption_without_writes(directional, patch) -> None:
    client, position_id, body, _, _ = directional
    if 'comparator' in patch:
        body = body | patch
    else:
        data = pf._load()
        data['position_reviews'][position_id]['comparison']['summary']['actions'][0].update(patch)
        pf._save(data)
    before = deepcopy(pf._load())
    response = client.post(f'/api/positions/{position_id}/strategy-watch', json=body)
    assert response.status_code == 400, response.text
    assert pf._load() == before


def test_direction_wait_cannot_hide_sale_hold_risk_or_t_pause_when_conditions_match(directional) -> None:
    value, root, facts = adopted(directional)
    facts['sector']['status'] = 'rising'
    current = plans.read_position_plan(value['position_id'])
    current['sale_requires_review'] = True
    assert followup.observe_plan(current, root, current=NOW).reason == 'sale'
    current['sale_requires_review'] = False
    current['review_plan']['choice'] = 'hold'
    current['current_risk'] = [{'level': 'L1', 'status': 'triggered'}]
    assert followup.observe_plan(current, root, current=NOW).reason == 'risk'
    current['review_plan']['choice'] = 't_watch'
    current['basis']['personal_pause_t'] = True
    assert followup.observe_plan(current, root, current=NOW).reason == 'personal'


def test_direction_wait_detects_tampered_price_and_origin_when_contract_reused(directional) -> None:
    value, _, _ = adopted(directional)
    watch = value['review_plan']['watch']
    for key, tamper in [('price', 9.5), ('comparator', 'at_or_above'), ('source_price_ref', 'price_0')]:
        with pytest.raises(ValueError):
            followup.watch_contract(watch | {key: tamper}, value['position_id'])
    with pytest.raises(ValueError):
        followup.watch_contract(watch, 'foreign-position')
    frozen = deepcopy(watch['condition_contract'])
    frozen['origin']['action'] = 'buy_review'
    frozen['digest'] = conditions.digest({key: row for key, row in frozen.items() if key != 'digest'})
    with pytest.raises(ValueError):
        conditions.validate_contract(frozen)


def test_direction_wait_invalidation_requires_review_without_price_when_selected(directional) -> None:
    client, position_id, body, facts, _ = directional
    data = pf._load()
    action = data['position_reviews'][position_id]['comparison']['summary']['actions'][0]
    action.update(prerequisite_ids=[], invalidation_ids=['sector:falling:v1'])
    pf._save(data)
    response = client.post(f'/api/positions/{position_id}/strategy-watch', json=body)
    assert response.status_code == 200, response.text
    root = pf._load()['position_reviews'][position_id]
    observed = followup.observe_plan(response.json()['data'], root, current=NOW)
    assert observed.reason == 'strategy_invalidation' and observed.state == 'needs_review'


def test_direction_wait_stops_and_foreign_tenant_cannot_adopt_when_requested(directional) -> None:
    client, position_id, body, _, _ = directional
    assert client.post(f'/api/positions/{position_id}/strategy-watch', headers={'X-User-Id': 'foreign'}, json=body).status_code == 409
    value, _, _ = adopted(directional)
    response = client.post(f'/api/positions/{position_id}/strategy-watch/stop', json={
        'expected_position': value['position_snapshot'], 'expected_revision': value['revision']})
    assert response.status_code == 200
    stopped = response.json()['data']
    assert not stopped['strategy_direction_progress'] and stopped['strategy_condition_checks'] is None
    assert followup.observe_plan(stopped, pf._load()['position_reviews'][position_id], current=NOW).reason == 'watch_stopped'


def test_direction_wait_allows_registered_market_breadth_but_not_index_changes(directional) -> None:
    client, position_id, body, facts, _ = directional
    data = pf._load()
    data['position_reviews'][position_id]['comparison']['summary']['actions'][0].update(
        prerequisite_ids=['market:not_weak:v1'])
    pf._save(data)
    assert client.post(f'/api/positions/{position_id}/strategy-watch', json=body).status_code == 200
    root = pf._load()['position_reviews'][position_id]
    facts['market'].update(status='strong', breadth='偏强')
    current = plans.read_position_plan(position_id)
    assert current['strategy_direction_progress']
    assert followup.observe_plan(current, root, current=NOW).reason == 'strategy_direction_review'
    facts['market']['indices'] = [{'name': '合成新指数', 'change_pct': -1, 'source': '合成指数源'}]
    current = plans.read_position_plan(position_id)
    assert not current['strategy_direction_progress']
    assert followup.observe_plan(current, root, current=NOW).reason == 'evidence_changed'


def test_direction_wait_cannot_adopt_t_route_when_personal_attention_is_unavailable(directional) -> None:
    client, position_id, body, _, _ = directional
    current = plans.read_position_plan(position_id)
    response = client.post(f'/api/positions/{position_id}/constraints', json={
        'attention': 'unavailable', 'extra_cash': 0, 'loss_limit': None,
        'expected_position': current['position_snapshot'], 'expected_revision': current['revision']})
    assert response.status_code == 200, response.text
    current = response.json()['data']
    assert current['basis']['personal_pause_t']
    response = client.post(f'/api/positions/{position_id}/discussion', json={
        'choice': None, 'expected_position': current['position_snapshot'], 'expected_revision': current['revision']})
    assert response.status_code == 200 and response.json()['data']['status'] == 'complete', response.text
    result = response.json()['data']
    body.update(generated_at=result['generated_at'], expected_revision=current['revision'],
                expected_analysis_basis_id=result['analysis_basis_id'])
    before = deepcopy(pf._load())
    response = client.post(f'/api/positions/{position_id}/strategy-watch', json=body | {'choice': 't_watch'})
    assert response.status_code == 400 and '暂停做T' in response.text
    assert pf._load() == before
