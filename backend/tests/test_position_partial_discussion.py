"""A failed reply cannot erase another round's validated research."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

import portfolio as pf
import position_discussion as discussion
from test_position_action_plan import plan_client  # noqa: F401
from test_position_discussion import OPINION, SUMMARY, discussion_client  # noqa: F401
from test_position_strategy_watch import strategy  # noqa: F401

# ruff: noqa: F811 -- imported fixtures


def test_position_partial_summary_uses_distinct_latest_valid_roles_when_reply_fails(discussion_client, monkeypatch):
    client, event_id, worksheet, _ = discussion_client
    payloads = []

    async def model(endpoint, payload, timeout, limits):
        if 'rounds' in payload:
            payloads.append(payload)
            return json.dumps(SUMMARY)
        role = next(key for key, value in discussion.ROLES.items() if value[1] == payload['role'])
        peers = payload['peer_opinions']
        if role == 'risk':
            return json.dumps({**OPINION, 'evidence_ids': ['rejected-private-content']})
        if role == 'trend' and peers:
            raise TimeoutError()
        return json.dumps({**OPINION, 'summary': f'有效{role}', 'reply_to': list(peers)[:1], 'challenge': '当前证据仍有缺口' if peers else ''})

    monkeypatch.setattr(discussion, 'call_model', model)
    before = deepcopy(pf._load())
    result = client.post(f'/api/alerts/{event_id}/discussion', json={
        'choice': 't_watch', 'expected_position': worksheet['position_snapshot'], 'expected_revision': worksheet['revision']}).json()['data']
    assert result['status'] == 'partial' and result['summary']
    assert result['synthesis_basis'] == {'mode': 'partial_initial', 'participating_roles': ['trend', 't_review'],
                                        'missing_roles': ['risk'], 'unreviewed_roles': ['trend']}
    selected = payloads[0]['synthesis_opinions']
    assert [(row['role'], row['stage']) for row in selected] == [('trend', 'independent'), ('t_review', 'challenge')]
    assert all(row['status'] == 'ok' for row in payloads[0]['rounds'][0]['opinions'])
    assert 'rejected-private-content' not in json.dumps(payloads)
    assert {key: value for key, value in pf._load().items() if key != 'position_reviews'} == {
        key: value for key, value in before.items() if key != 'position_reviews'}


def test_position_partial_summary_explains_skip_when_one_role_is_valid(discussion_client, monkeypatch):
    client, event_id, worksheet, _ = discussion_client
    calls = []

    async def model(endpoint, payload, timeout, limits):
        calls.append(payload)
        if payload.get('role') != discussion.ROLES['trend'][1]:
            raise TimeoutError()
        return json.dumps(OPINION)

    monkeypatch.setattr(discussion, 'call_model', model)
    result = client.post(f'/api/alerts/{event_id}/discussion', json={
        'choice': 't_watch', 'expected_position': worksheet['position_snapshot'], 'expected_revision': worksheet['revision']}).json()['data']
    assert result['status'] == 'partial' and result['summary'] is None
    assert result['summary_failure_kind'] == 'insufficient_roles' and '不同角色' in result['summary_issue']
    assert len(calls) == 3 and result['synthesis_basis']['participating_roles'] == ['trend']


@pytest.mark.parametrize('mode,expected', [('partial_initial', 400), ('challenge', 200)])
def test_position_partial_summary_blocks_new_watch_only_when_cross_review_is_incomplete(strategy, mode, expected):
    client, position_id, body, _, _ = strategy
    data = pf._load()
    result = data['position_reviews'][position_id]['comparison']
    result.update(status='partial', synthesis_basis={'mode': mode, 'participating_roles': ['trend', 't_review'],
                                                    'missing_roles': ['risk'], 'unreviewed_roles': ['trend'] if mode == 'partial_initial' else []})
    pf._save(data)
    before = deepcopy(pf._load())
    response = client.post(f'/api/positions/{position_id}/strategy-watch', json=body)
    assert response.status_code == expected, response.text
    if expected == 400:
        assert '仅供阅读' in response.text and pf._load() == before


def test_position_partial_summary_does_not_double_count_same_role_or_mutate_rows():
    initial = [{'role': 'trend', 'status': 'ok', 'opinion': {'summary': '初轮'}}]
    replies = [{'role': 'trend', 'status': 'ok', 'opinion': {'summary': '有效回应'}},
               {'role': 'risk', 'status': 'failed', 'opinion': {'summary': '无效内容'}}]
    before = deepcopy((initial, replies))
    selected, basis = discussion._synthesis_inputs(initial, replies, discussion.ROLES)
    assert len(selected) == 1 and selected[0]['opinion']['summary'] == '有效回应'
    assert basis['participating_roles'] == ['trend'] and basis['mode'] == 'partial_initial'
    assert (initial, replies) == before
