"""Validated first-round opinions remain useful when peer replies fail."""
# ruff: noqa: F811 -- reuse an isolated API fixture
from __future__ import annotations

from copy import deepcopy
import json

import candidate_discussion as discussion
import position_discussion as transport
from test_candidate_observation import adoption, opinion, summary, env  # noqa: F401 -- fixture


def run_with(env, monkeypatch, *, first_failures=None, reply_failures=None):
    first_failures, reply_failures = first_failures or {}, reply_failures or {}
    calls = []
    async def model(endpoint, payload, timeout, limits, *, system):
        calls.append(deepcopy(payload))
        assert 'quote_updated只表示' in system
        if payload['output_schema']['title'] == 'CandidateSummary':
            return json.dumps(summary())
        role = next(key for key, (_, description) in discussion.ROLES.items() if description == payload['role'])
        peers = payload.get('peer_opinions') or {}
        failure = (reply_failures if peers else first_failures).get(role)
        if failure == 'timeout':
            raise TimeoutError('synthetic timeout')
        value = opinion(peers)
        if failure == 'invalid':
            value['summary'] = '拒绝原文标记：无来源价格8.765元'
        else:
            value['summary'] = ('回应有效' if peers else '初轮有效') + role
        return json.dumps(value)
    monkeypatch.setattr(transport, 'call_model', model)
    client = env[0]
    value = client.get('/api/candidate-observations/600000').json()['data']
    response = client.post('/api/candidate-observations/600000/discussion', json={
        'expected_version': value['version'], 'expected_evidence_version': value['evidence_version']})
    assert response.status_code == 200, response.text
    return response.json()['data'], calls


def test_candidate_partial_discussion_keeps_full_challenge_mode_when_all_roles_finish(env, monkeypatch):
    result, calls = run_with(env, monkeypatch)
    assert result['status'] == 'complete' and result['summary']
    assert result['synthesis_basis'] == {'mode': 'challenge', 'participating_roles': ['trend', 'entry', 'risk'],
                                        'missing_roles': [], 'unreviewed_roles': []}
    assert len(calls) == 7
    assert 'synthesis_opinions' not in calls[-1]
    assert [item['stage'] for item in calls[-1]['rounds'][0]['opinions']] == ['challenge'] * 3
    assert 'evidence_snapshot' not in calls[0]['review_context']['current']
    assert 'price_refs' not in calls[0]['review_context']['current']


def test_candidate_partial_discussion_uses_valid_initial_after_one_peer_timeout(env, monkeypatch):
    result, calls = run_with(env, monkeypatch, first_failures={'risk': 'invalid'}, reply_failures={'trend': 'timeout'})
    assert result['status'] == 'partial' and result['summary'] and result['summary_issue'] == ''
    assert result['synthesis_basis'] == {'mode': 'partial_initial', 'participating_roles': ['trend', 'entry'],
                                        'missing_roles': ['risk'], 'unreviewed_roles': ['trend']}
    assert len(calls) == 6  # three initial, two replies, one summary; no retry
    selected = {item['role']: item for item in calls[-1]['rounds'][0]['opinions']}
    assert selected['trend']['stage'] == 'independent' and selected['trend']['opinion']['summary'] == '初轮有效trend'
    assert selected['entry']['stage'] == 'challenge' and selected['entry']['opinion']['summary'] == '回应有效entry'
    assert '拒绝原文标记' not in json.dumps(calls[-1], ensure_ascii=False)
    current = env[0].get('/api/candidate-observations/600000').json()['data']
    rejected = env[0].post('/api/candidate-observations/600000/adopt', json=adoption(current, result))
    assert rejected.status_code == 409  # reading a partial summary cannot change intent
    assert current['watch'] is None


def test_candidate_partial_discussion_can_summarize_two_initials_when_all_replies_fail(env, monkeypatch):
    result, calls = run_with(env, monkeypatch, first_failures={'risk': 'timeout'},
                             reply_failures={'trend': 'timeout', 'entry': 'invalid'})
    assert result['status'] == 'partial' and result['summary']
    assert result['synthesis_basis']['unreviewed_roles'] == ['trend', 'entry']
    assert all(item['stage'] == 'independent' for item in calls[-1]['rounds'][0]['opinions'])
    assert '拒绝原文标记' not in json.dumps(calls[-1], ensure_ascii=False)


def test_candidate_partial_discussion_explains_skip_when_only_one_distinct_role_is_valid(env, monkeypatch):
    result, calls = run_with(env, monkeypatch, first_failures={'risk': 'invalid', 'entry': 'timeout'})
    assert result['status'] == 'partial' and result['summary'] is None
    assert '不足两名不同角色' in result['summary_issue']
    assert len(calls) == 3
    assert result['synthesis_basis']['participating_roles'] == ['trend']
    assert result['synthesis_basis']['missing_roles'] == ['entry', 'risk']


def test_candidate_partial_discussion_explains_skip_when_all_roles_fail(env, monkeypatch):
    result, calls = run_with(env, monkeypatch, first_failures={role: 'timeout' for role in discussion.ROLES})
    assert result['status'] == 'failed' and result['summary'] is None
    assert '不足两名不同角色' in result['summary_issue'] and len(calls) == 3
    assert result['synthesis_basis']['participating_roles'] == []


def test_candidate_partial_discussion_does_not_count_two_rounds_of_one_role_as_two_participants():
    initial = [{'role': 'trend', 'label': '趋势', 'status': 'ok', 'opinion': {'summary': '初轮有效'}}]
    reply = [{'role': 'trend', 'label': '趋势', 'status': 'ok', 'opinion': {'summary': '回应有效'}}]
    selected, basis = transport._synthesis_inputs(initial, reply, discussion.ROLES)
    assert len(selected) == 1 and selected[0]['opinion']['summary'] == '回应有效'
    assert basis['participating_roles'] == ['trend'] and basis['missing_roles'] == ['entry', 'risk']


def test_candidate_partial_discussion_preserves_existing_two_replies_path_as_partial(env, monkeypatch):
    result, calls = run_with(env, monkeypatch, first_failures={'risk': 'invalid'})
    assert result['summary'] and result['status'] == 'partial'
    assert result['synthesis_basis']['mode'] == 'challenge'
    assert result['synthesis_basis']['unreviewed_roles'] == []
    assert result['synthesis_basis']['missing_roles'] == ['risk']
    assert len(calls[-1]['rounds'][0]['opinions']) == 2


def test_candidate_partial_discussion_keeps_initial_when_single_reply_times_out(env, monkeypatch):
    result, calls = run_with(env, monkeypatch, reply_failures={'trend': 'timeout'})
    assert result['status'] == 'partial' and result['summary']
    assert result['synthesis_basis'] == {'mode': 'challenge', 'participating_roles': ['trend', 'entry', 'risk'],
                                        'missing_roles': [], 'unreviewed_roles': ['trend']}
    assert len(calls) == 7
    selected = calls[-1]['rounds'][0]['opinions']
    assert len(selected) == 3 and selected[0]['stage'] == 'independent'
    assert all(row['status'] == 'ok' for row in calls[-1]['rounds'][0]['opinions'])
