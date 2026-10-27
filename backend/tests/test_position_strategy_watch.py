"""Advisory strategy adoption and off-page monitoring, no actual trades needed."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

import pytest

import portfolio as pf
import position_discussion as discussion
import position_plan_service as plans
import position_strategy_watch as watches
from position_followup import notification_key, observe_plan
from test_position_action_plan import NOW, QUOTE, plan_client  # noqa: F401
from test_position_discussion import discussion_client  # noqa: F401

# ruff: noqa: F811 -- imported pytest fixtures are intentionally injected


@pytest.fixture
def strategy(discussion_client, monkeypatch):
    client, event_id, _, calls = discussion_client
    monkeypatch.setattr(discussion, 'now_shanghai', lambda: NOW)
    monkeypatch.setattr(watches, 'now_shanghai', lambda: NOW)
    position_id = pf._load()['holdings'][0]['position_id']
    value = plans.read_position_plan(position_id)
    response = client.post(f'/api/positions/{position_id}/discussion', json={
        'choice': None, 'expected_position': value['position_snapshot'], 'expected_revision': value['revision']})
    assert response.status_code == 200, response.text
    result = response.json()['data']
    assert result['status'] == 'complete'
    body = {'choice': 'reduce', 'source_discussion': 'comparison', 'generated_at': result['generated_at'],
            'action_index': 0, 'comparator': 'at_or_above', 'expected_position': value['position_snapshot'],
            'expected_revision': value['revision'], 'expected_analysis_basis_id': result['analysis_basis_id']}
    return client, position_id, body, result, calls


def test_strategy_watch_adopts_ai_observation_without_fees_or_trade_when_confirmed(strategy):
    client, position_id, body, result, _ = strategy
    before = deepcopy(pf._load())
    response = client.post(f'/api/positions/{position_id}/strategy-watch', json=body)
    assert response.status_code == 200, response.text
    value = response.json()['data']
    watch = value['review_plan']['watch']
    assert watch['condition'] == result['summary']['actions'][0]['condition']
    assert watch['price_ref'] in result['price_refs'].values()
    assert value['review_plan']['choice'] == 'reduce'
    assert not value['review_plan'].get('parameters') and value['scenario'] is None
    assert {key: row for key, row in pf._load().items() if key != 'position_reviews'} == {
        key: row for key, row in before.items() if key != 'position_reviews'}
    assert len(pf._load()['position_reviews'][position_id]['watch_audit']) == 1


def test_strategy_watch_normal_quote_ticks_do_not_permanently_block_adoption(strategy, monkeypatch):
    client, position_id, body, _, _ = strategy
    monkeypatch.setattr(pf.astock, 'tencent_quote', lambda codes: {
        code: {**QUOTE, 'price': 9.3, 'quote_time': '20261023100200'} for code in codes})
    monkeypatch.setattr(plans, 'now_shanghai', lambda: NOW + timedelta(minutes=2))
    monkeypatch.setattr(watches, 'now_shanghai', lambda: NOW + timedelta(minutes=2))
    changed = plans.read_position_plan(position_id)
    assert changed['analysis_basis_id'] != body['expected_analysis_basis_id']
    assert changed['revision'] == body['expected_revision']
    response = client.post(f'/api/positions/{position_id}/strategy-watch', json=body)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize('patch,status', [({'comparator': 'automatic'}, 400), ({'action_index': 999}, 400),
                                        ({'action_index': True}, 422), ({'price': 0.01}, 422),
                                        ({'expected_analysis_basis_id': 'forged'}, 409),
                                        ({'generated_at': 'forged'}, 409), ({'expected_revision': 'old'}, 409)])
def test_strategy_watch_rejects_forged_or_invalid_adoption_without_writes(strategy, patch, status):
    client, position_id, body, _, _ = strategy
    before = deepcopy(pf._load())
    response = client.post(f'/api/positions/{position_id}/strategy-watch', json=body | patch)
    assert response.status_code == status, response.text
    assert pf._load() == before


def test_strategy_watch_expired_analysis_requires_current_reanalysis(strategy, monkeypatch):
    client, position_id, body, _, _ = strategy
    monkeypatch.setattr(watches, 'now_shanghai', lambda: NOW + timedelta(minutes=16))
    before = deepcopy(pf._load())
    response = client.post(f'/api/positions/{position_id}/strategy-watch', json=body)
    assert response.status_code == 409 and '已旧' in response.text
    assert pf._load() == before


def test_strategy_watch_adopted_condition_drives_monitor_without_trade_or_calculator(strategy):
    client, position_id, body, _, _ = strategy
    value = client.post(f'/api/positions/{position_id}/strategy-watch', json=body).json()['data']
    root = pf._load()['position_reviews'][position_id]
    value['basis']['price'] = value['review_plan']['watch']['price'] - 0.1
    waiting = observe_plan(value, root, current=NOW)
    assert waiting.state == 'waiting' and '正在跟踪' in waiting.title
    assert notification_key(root | {'followup_mode': 'changes'}, waiting.to_dict(), current=NOW) is None
    value['basis']['price'] = value['review_plan']['watch']['price']
    reached = observe_plan(value, root, current=NOW)
    assert reached.state == 'price_watch' and '其他条件尚未自动确认' in reached.detail
    assert value['review_plan']['watch']['invalidation'] in reached.detail
    assert reached.reason == 'strategy_price'
    key = notification_key(root | {'followup_mode': 'changes'}, reached.to_dict(), current=NOW)
    value['basis']['price'] += 0.1
    assert key == notification_key(root | {'followup_mode': 'changes'}, observe_plan(value, root, current=NOW).to_dict(), current=NOW)


def test_strategy_watch_monitor_explains_changed_premise_or_personal_limits(strategy):
    client, position_id, body, _, _ = strategy
    value = client.post(f'/api/positions/{position_id}/strategy-watch', json=body).json()['data']
    root = pf._load()['position_reviews'][position_id]
    value['route_review']['evidence']['market']['status'] = 'weak'
    assert observe_plan(value, root, current=NOW).reason == 'evidence_changed'
    value['constraints'] = {**value['constraints'], 'attention': 'unavailable'}
    assert observe_plan(value, root, current=NOW).reason == 'watch_changed'


def test_strategy_watch_survives_optional_fee_calculation_when_same_route(strategy):
    from test_position_action_plan import PARAMS
    client, position_id, body, _, _ = strategy
    value = client.post(f'/api/positions/{position_id}/strategy-watch', json=body).json()['data']
    watch = deepcopy(value['review_plan']['watch'])
    response = client.post(f'/api/positions/{position_id}/plan', json={
        'choice': 'reduce', 'parameters': PARAMS | {'buy_price': None},
        'expected_position': value['position_snapshot'], 'expected_revision': value['revision']})
    assert response.status_code == 200, response.text
    value = plans.read_position_plan(position_id)
    assert value['review_plan']['watch'] == watch
    root = pf._load()['position_reviews'][position_id]
    value['basis']['price'] = watch['price']
    assert observe_plan(value, root, current=NOW).reason == 'strategy_price'


def test_strategy_watch_stop_preserves_audit_and_never_reverts_to_old_prices(strategy):
    client, position_id, body, _, _ = strategy
    value = client.post(f'/api/positions/{position_id}/strategy-watch', json=body).json()['data']
    before = deepcopy(pf._load())
    response = client.post(f'/api/positions/{position_id}/strategy-watch/stop', json={
        'expected_position': value['position_snapshot'], 'expected_revision': value['revision']})
    assert response.status_code == 200, response.text
    value = response.json()['data']
    assert 'watch' not in value['review_plan'] and value['review_plan']['watch_stopped']
    root = pf._load()['position_reviews'][position_id]
    assert root['watch_audit'][-1]['action'] == 'stop'
    assert root['watch_audit'][-1]['previous']['condition']
    assert pf._load()['holdings'] == before['holdings']
    observed = observe_plan(value, root, current=NOW).to_dict()
    assert observed['reason'] == 'watch_stopped'
    assert notification_key(root | {'followup_mode': 'changes'}, observed, current=NOW) is None


def test_strategy_watch_rejects_market_turn_during_model_analysis(strategy, monkeypatch):
    client, position_id, body, _, _ = strategy
    value = plans.read_position_plan(position_id)
    value['route_review']['evidence']['market']['status'] = 'weak'
    monkeypatch.setattr(watches, 'read_position_plan', lambda key: value)
    before = deepcopy(pf._load())
    response = client.post(f'/api/positions/{position_id}/strategy-watch', json=body)
    assert response.status_code == 409 and '依据已变化' in response.text
    assert pf._load() == before


def test_strategy_discussion_can_explain_unsaved_route_when_fee_scenario_invalid(strategy):
    client, position_id, _, _, calls = strategy
    data = pf._load()
    root = data['position_reviews'][position_id]
    root['review_plan']['parameters'] = {'quantity': -1}
    pf._save(data)
    value = plans.read_position_plan(position_id)
    assert value['scenario_issue']
    response = client.post(f'/api/positions/{position_id}/discussion', json={
        'choice': value['review_plan']['choice'], 'expected_position': value['position_snapshot'],
        'expected_revision': value['revision']})
    assert response.status_code == 200, response.text
    assert response.json()['data']['summary']
    assert calls[-1]['saved_parameters'] is None and calls[-1]['scenario'] is None
    assert calls[-1]['scenario_issue']
