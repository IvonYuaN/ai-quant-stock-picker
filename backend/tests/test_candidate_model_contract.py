"""Candidate schema and server guards share safe, bounded model contracts."""
# ruff: noqa: F811 -- reuse the isolated candidate API fixture
from __future__ import annotations

from copy import deepcopy
import json

import pytest

import candidate_discussion as discussion
import candidate_observation as resources
from model_contract import ModelContractError
import position_discussion as transport
from test_candidate_observation import env, opinion, summary  # noqa: F401 -- fixture


@pytest.mark.parametrize('blocked', [False, True])
@pytest.mark.parametrize('schema', [discussion.CandidateAction, discussion.CandidateOpinion, discussion.CandidateSummary])
def test_candidate_model_contract_schema_limits_only_actions_when_entry_blocked(env, schema, blocked):
    value = {**resources.workspace('600000'), 'entry_blocked': blocked}
    result = discussion.schema_for(schema, value, set())
    action = result if schema is discussion.CandidateAction else result['$defs']['CandidateAction']
    expected = ['defer'] if blocked else ['pullback', 'confirmation', 'defer']
    assert action['properties']['choice']['enum'] == expected
    if schema is discussion.CandidateSummary:
        assert result['$defs']['CandidateRoute']['properties']['choice']['enum'] == ['pullback', 'confirmation', 'defer']
    assert discussion.CandidateAction.model_json_schema()['properties']['choice']['enum'] == ['pullback', 'confirmation', 'defer']
    deferred = action['allOf'][0]
    assert deferred['if']['properties']['choice']['const'] == 'defer'
    assert deferred['then']['properties'] == {'price_ref': {'type': 'null'},
        'prerequisite_ids': {'maxItems': 0}, 'invalidation_ids': {'maxItems': 0}}
    if schema is discussion.CandidateSummary and blocked:
        assert result['properties']['focus_choice']['anyOf'] == [{'const': 'defer'}, {'type': 'null'}]
        assert result['$defs']['CandidateRoute']['allOf'][0]['then']['properties']['judgment']['enum'] == ['not_now', 'insufficient']


@pytest.mark.parametrize('schema', [discussion.CandidateOpinion, discussion.CandidateSummary])
def test_candidate_model_contract_keeps_existing_no_price_action_when_entry_open(env, schema):
    value = {**resources.workspace('600000'), 'entry_blocked': False, 'price_refs': {}}
    row = opinion() if schema is discussion.CandidateOpinion else summary()
    row['actions'][0]['price_ref'] = None
    result = discussion.schema_for(schema, value, set())
    assert result['$defs']['CandidateAction']['properties']['price_ref']['type'] == 'null'
    assert result['$defs']['CandidateAction']['properties']['choice']['enum'] == ['pullback', 'confirmation', 'defer']
    assert discussion.parse(json.dumps(row), schema, value, set())['actions'][0]['choice'] == 'pullback'


@pytest.mark.parametrize(('change', 'code'), [
    ({'evidence_ids': ['missing']}, 'evidence_reference'),
    ({'reply_to': ['invented']}, 'peer_reference'),
    ({'actions': [{'price_ref': 'missing'}]}, 'price_reference'),
    ({'actions': [{'prerequisite_ids': ['missing']}]}, 'condition_reference'),
    ({'actions': [{'prerequisite_ids': ['market:not_weak:v1', 'market:not_weak:v1']}]}, 'condition_reference'),
    ({'actions': [{'prerequisite_ids': ['market:not_weak:v1'], 'invalidation_ids': ['market:not_weak:v1']}]}, 'condition_reference'),
    ({'actions': [{'prerequisite_ids': ['market:not_weak:v1', 'market:weak:v1']}]}, 'condition_reference'),
    ({'actions': [{'choice': 'defer'}]}, 'hard_constraint'),
    ({'actions': [{'choice': 'defer', 'price_ref': None, 'prerequisite_ids': ['market:not_weak:v1']}]}, 'condition_reference'),
])
def test_candidate_model_contract_rejects_existing_invalid_references_with_fixed_code(env, change, code):
    value = resources.workspace('600000')
    row = opinion()
    patch = deepcopy(change)
    if 'actions' in patch:
        row['actions'][0].update(patch.pop('actions')[0])
    row.update(patch)
    with pytest.raises(ModelContractError) as exc:
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    assert exc.value.code == code
    failure = transport._model_failure(exc.value)
    assert failure == {'failure_kind': 'invalid_output', 'contract_issue_code': code, 'issue': str(exc.value)}
    assert 'missing' not in failure['issue'] and 'invented' not in failure['issue']


@pytest.mark.parametrize('change', [
    {'reply_to': []}, {'reply_to': ['invented']}, {'challenge': ' '},
])
def test_candidate_model_contract_rejects_missing_peer_response_when_peers_supplied(env, change):
    row = opinion({'trend': {}})
    row.update(change)
    with pytest.raises(ModelContractError) as exc:
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, resources.workspace('600000'), {'trend'})
    assert exc.value.code == 'peer_reference'


def test_candidate_model_contract_keeps_valid_independent_and_peer_opinions(env):
    value = resources.workspace('600000')
    first = discussion.parse(json.dumps(opinion()), discussion.CandidateOpinion, value, set())
    reply = discussion.parse(json.dumps(opinion({'trend': {}})), discussion.CandidateOpinion, value, {'trend'})
    assert first['reply_to'] == [] and reply['reply_to'] == ['trend']


@pytest.mark.parametrize(('change', 'code'), [
    ('duplicate_route', 'route_contract'), ('route_evidence', 'evidence_reference'),
    ('blocked_focus', 'hard_constraint'), ('blocked_route', 'hard_constraint'),
])
def test_candidate_model_contract_rejects_invalid_summary_with_fixed_code(env, change, code):
    blocked = change.startswith('blocked')
    value = {**resources.workspace('600000'), 'entry_blocked': blocked}
    row = summary(blocked=blocked)
    if change == 'duplicate_route':
        row['routes'][0]['choice'] = 'defer'
    elif change == 'route_evidence':
        row['routes'][0]['evidence_ids'] = ['untrusted-raw-marker']
    elif change == 'blocked_focus':
        row['focus_choice'] = 'pullback'
    else:
        row['routes'][0]['judgment'] = 'investigate'
    with pytest.raises(ModelContractError) as exc:
        discussion.parse(json.dumps(row), discussion.CandidateSummary, value, set())
    assert exc.value.code == code
    assert 'untrusted-raw-marker' not in str(exc.value)


@pytest.mark.parametrize('choice', ['pullback', 'confirmation'])
def test_candidate_model_contract_server_still_rejects_actions_when_entry_blocked(env, choice):
    value = {**resources.workspace('600000'), 'entry_blocked': True}
    row = opinion()
    row['actions'][0]['choice'] = choice
    with pytest.raises(ModelContractError) as exc:
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    assert exc.value.code == 'hard_constraint'


def test_candidate_model_contract_preserves_three_routes_when_entry_blocked(env):
    value = {**resources.workspace('600000'), 'entry_blocked': True}
    parsed = discussion.parse(json.dumps(summary(blocked=True)), discussion.CandidateSummary, value, set())
    assert {row['choice'] for row in parsed['routes']} == {'pullback', 'confirmation', 'defer'}
    assert parsed['focus_choice'] == 'defer'


@pytest.mark.parametrize('field', ['condition', 'invalidation'])
@pytest.mark.parametrize('text', ['等待price_1后人工复核', '市场条件market:not_weak:v1满足后复核', 'sector:invented:v1满足后复核'])
def test_candidate_model_contract_rejects_embedded_id_when_its_number_equals_sourced_price(env, field, text):
    value = {**resources.workspace('600000'), 'price_refs': {'price_1': {'price': 1.0}}}
    row = opinion()
    row['actions'][0].update(price_ref='price_1', **{field: text})
    with pytest.raises(ModelContractError) as exc:
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    assert exc.value.code == 'embedded_reference'


def test_candidate_model_contract_accepts_natural_action_and_structured_ids_when_price_equals_one(env):
    value = {**resources.workspace('600000'), 'price_refs': {'price_1': {'price': 1.0}}}
    row = opinion()
    row['actions'][0].update(price_ref='price_1', condition='观察所引用价位，核对市场与板块后人工复核',
                             prerequisite_ids=['market:not_weak:v1'], invalidation_ids=[])
    assert discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())['actions'] == row['actions']


@pytest.mark.parametrize(('failure', 'kind', 'code'), [
    ('none', '', ''), ('route', 'invalid_output', 'route_contract'),
    ('unknown', 'invalid_output', ''), ('timeout', 'timeout', ''),
    ('roles', 'insufficient_roles', ''), ('peers', 'insufficient_roles', ''),
])
def test_candidate_model_contract_persists_safe_summary_failure_metadata(env, monkeypatch, failure, kind, code):
    async def model(endpoint, payload, timeout, limits, *, system):
        if payload['output_schema']['title'] == 'CandidateSummary':
            if failure == 'timeout':
                raise TimeoutError('private-raw-marker')
            if failure == 'unknown':
                raise RuntimeError('private-raw-marker')
            row = summary()
            if failure == 'route':
                row['routes'][0]['choice'] = 'defer'
            return json.dumps(row)
        if failure == 'roles':
            raise TimeoutError('private-raw-marker')
        row = opinion(payload.get('peer_opinions'))
        if failure == 'peers':
            row['reply_to'] = ['private-raw-marker']
        return json.dumps(row)

    monkeypatch.setattr(transport, 'call_model', model)
    client = env[0]
    value = client.get('/api/candidate-observations/600000').json()['data']
    response = client.post('/api/candidate-observations/600000/discussion', json={
        'expected_version': value['version'], 'expected_evidence_version': value['evidence_version']})
    assert response.status_code == 200, response.text
    result = response.json()['data']
    assert result['summary_failure_kind'] == kind
    assert result['summary_contract_issue_code'] == code
    assert bool(result['summary_issue']) == (failure != 'none')
    assert 'private-raw-marker' not in json.dumps(result, ensure_ascii=False)
    stored = resources.load_store()['resources']['600000']['discussion']
    assert stored['summary_failure_kind'] == kind and stored['summary_contract_issue_code'] == code
    if failure == 'peers':
        assert all(row['contract_issue_code'] == 'peer_reference' for row in result['rounds'][0]['opinions'])
