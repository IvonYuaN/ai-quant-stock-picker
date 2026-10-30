"""Read-side plan changes remain available without a successful model call."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

import portfolio as pf
import position_discussion as discussion
import position_strategy_evidence as sources
from strategy_review_context import position_review_facts
from test_position_action_plan import NOW, QUOTE, plan_client  # noqa: F401
from test_position_discussion import discussion_client  # noqa: F401
from test_position_strategy_watch import strategy  # noqa: F401

# ruff: noqa: F811 -- pytest fixtures are shared across contract suites


@pytest.fixture
def adopted(request, discussion_client, monkeypatch):
    facts = {'market': {'status': 'strong', 'source': '合成大盘源', 'as_of': NOW.isoformat()},
             'sector': {'status': 'rising', 'source': '合成板块源', 'industry': '合成行业',
                        'as_of': NOW.isoformat()}}
    monkeypatch.setattr(sources, 'load_strategy_context', lambda *args, **kwargs: deepcopy(facts))
    client, position_id, body, _, calls = request.getfixturevalue('strategy')
    response = client.post(f'/api/positions/{position_id}/strategy-watch', json=body)
    assert response.status_code == 200, response.text
    return client, position_id, facts, calls


def read(client: Any, position_id: str) -> dict[str, Any]:
    response = client.get(f'/api/positions/{position_id}/action-plan')
    assert response.status_code == 200, response.text
    return response.json()['data']


def test_position_changes_explain_sector_turn_without_model_or_intent_writes_when_read(adopted, monkeypatch) -> None:
    client, position_id, facts, calls = adopted
    before, count = deepcopy(pf._load()), len(calls)
    facts['sector']['status'] = 'falling'
    def forbidden(*args, **kwargs):
        raise AssertionError('page reads must not call models')
    monkeypatch.setattr(discussion, 'call_model', forbidden)
    value = read(client, position_id)
    context = value['strategy_review_context']
    change = next(row for row in context['changes'] if row['category'] == 'sector')
    assert change['before']['status'] == 'rising' and change['after']['status'] == 'falling'
    assert context['material_changed'] and not context['quote_updated']
    assert context['original']['condition'] == value['review_plan']['watch']['condition']
    assert pf._load() == before and len(calls) == count


def test_position_changes_quote_ticks_are_only_quote_updates_when_direction_and_risk_stay_same(adopted, monkeypatch) -> None:
    client, position_id, _, _ = adopted
    monkeypatch.setattr(pf.astock, 'tencent_quote', lambda codes: {
        code: {**QUOTE, 'price': 9.3} for code in codes})
    context = read(client, position_id)['strategy_review_context']
    assert context['quote_updated'] and not context['material_changed']
    assert context['changes'] == []


def test_position_changes_expired_sector_becomes_unknown_without_claiming_recovery(adopted) -> None:
    client, position_id, facts, _ = adopted
    facts['sector']['as_of'] = '2026-10-22T10:00:00+08:00'
    value = read(client, position_id)
    context = value['strategy_review_context']
    current = context['current']['evidence_snapshot']['sector']
    assert current['status'] == 'unknown' and current['source'] == '' and current['as_of'] is None
    assert any(row['category'] == 'sector' and row['after']['status'] == 'unknown'
               for row in context['changes'])
    assert value['review_plan']['watch']['evidence_snapshot']['sector']['status'] == 'rising'


@pytest.mark.parametrize('missing', ['evidence_snapshot', 'direction_schema', 'choice'])
def test_position_changes_keep_legacy_missing_facts_explicit_when_history_incomplete(adopted, missing) -> None:
    client, position_id, _, _ = adopted
    data = pf._load()
    data['position_reviews'][position_id]['review_plan']['watch'].pop(missing)
    pf._save(data)
    context = read(client, position_id)['strategy_review_context']
    assert context['missing_evidence']
    if missing == 'evidence_snapshot':
        assert context['original']['evidence_snapshot'] == {} and not context['original']['snapshot_complete']
    elif missing == 'choice':
        assert context['original']['choice'] is None


def test_position_changes_discard_other_position_history_when_identity_mismatches(adopted) -> None:
    client, position_id, _, _ = adopted
    data = pf._load()
    data['position_reviews'][position_id]['review_plan']['watch']['evidence_identity']['position_id'] = 'other-position'
    pf._save(data)
    context = read(client, position_id)['strategy_review_context']
    assert context['status'] == 'identity_mismatch' and context['original'] is None
    assert context['changes'] == [] and context['missing_evidence']
    response = client.get(f'/api/positions/{position_id}/action-plan', headers={'X-User-Id': 'other'})
    assert response.status_code == 409 and 'strategy_review_context' not in response.text


def test_position_changes_are_absent_when_watch_stopped_or_position_archived(adopted) -> None:
    client, position_id, _, _ = adopted
    value = read(client, position_id)
    response = client.post(f'/api/positions/{position_id}/strategy-watch/stop', json={
        'expected_position': value['position_snapshot'], 'expected_revision': value['revision']})
    assert response.status_code == 200, response.text
    assert read(client, position_id)['strategy_review_context'] is None
    data = pf._load()
    # Preserve a historical active watch to exercise the archived guard itself.
    data['position_reviews'][position_id]['review_plan'] = value['review_plan']
    pf._save(data)
    pf.close_position('002129', '2026-10-23', 9.4, 1000, 12, operation_id='change-review-exit')
    archived = read(client, position_id)
    assert archived['archived'] and archived['strategy_review_context'] is None


def test_position_changes_projection_matches_model_input_without_aliasing_when_evidence_mutated(adopted) -> None:
    client, position_id, _, calls = adopted
    value = read(client, position_id)
    evidence, prices = position_review_facts(value)
    original = deepcopy(value)
    response = client.post(f'/api/positions/{position_id}/discussion', json={
        'choice': 'reduce', 'expected_position': value['position_snapshot'], 'expected_revision': value['revision']})
    assert response.status_code == 200, response.text
    context = response.json()['data']['review_context']
    assert context == value['strategy_review_context']
    assert calls[-1]['evidence']['quote'] == evidence['quote']
    assert calls[-1]['price_refs'] == prices
    evidence['holding']['shares'] = 999999
    evidence['sector']['status'] = 'unknown'
    prices['price_0']['price'] = 0
    assert value == original


def test_position_changes_survive_failed_analysis_without_mutating_adopted_watch(adopted, monkeypatch) -> None:
    client, position_id, facts, _ = adopted
    watch = deepcopy(read(client, position_id)['review_plan']['watch'])
    facts['sector']['status'] = 'falling'
    async def unavailable(*args, **kwargs):
        raise TimeoutError('synthetic model unavailable')
    monkeypatch.setattr(discussion, 'call_model', unavailable)
    value = read(client, position_id)
    response = client.post(f'/api/positions/{position_id}/discussion', json={
        'choice': 'reduce', 'expected_position': value['position_snapshot'], 'expected_revision': value['revision']})
    assert response.status_code == 200, response.text
    assert response.json()['data']['status'] == 'failed'
    current = read(client, position_id)
    assert current['strategy_review_context']['material_changed']
    assert current['review_plan']['watch'] == watch


def test_position_changes_are_absent_when_no_watch_has_been_adopted(plan_client) -> None:
    client, _ = plan_client
    value = read(client, pf._load()['holdings'][0]['position_id'])
    assert value['strategy_review_context'] is None
