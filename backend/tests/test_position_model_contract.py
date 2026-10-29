"""Generation options and independent acceptance share the existing hard guards."""
from __future__ import annotations

# ruff: noqa: F811 -- shared TestClient fixtures
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from model_contract import ModelContractError
import position_discussion as discussion
from test_position_discussion import OPINION, discussion_client  # noqa: F401
from test_position_action_plan import plan_client  # noqa: F401
from test_position_discussion import COMPARISON

PRICES = {'price_0': {'price': 1.0, 'label': '观察参考', 'source': 'synthetic', 'as_of': '2026-10-29T10:00:00+08:00'}}
EVIDENCE = {'quote': {'price': 1.0}, 'holding': {'shares': 1000}}


@pytest.mark.parametrize('shares,issue,pause,has_prices,allowed', [
    (1000, '', False, True, {'observe', 'sell_review', 'buy_review', 'pause'}),
    (0, '', True, True, {'observe', 'pause'}),
    (1000, '行情已旧', True, True, {'observe', 'pause'}),
    (1000, '', True, True, {'observe', 'sell_review', 'pause'}),
    (0, '', False, True, {'observe', 'buy_review', 'pause'}),
    (1000, '', False, False, {'observe', 'pause'}),
])
def test_position_model_schema_offers_only_existing_guard_allowed_actions_when_sources_change(
    shares: int, issue: str, pause: bool, has_prices: bool, allowed: set[str],
) -> None:
    guards = {'sellable_shares': shares, 'quote_issue': issue, 'pause_t': pause}
    prices = PRICES if has_prices else {}
    for model in (discussion.RoleOpinion, discussion.DiscussionSummary, discussion.ComparisonSummary):
        schema = discussion.output_schema(model, EVIDENCE, prices, set(), guards=guards)
        assert set(schema['$defs']['Observation']['properties']['action']['enum']) == allowed
        if model is discussion.ComparisonSummary:
            assert set(schema['$defs']['RouteOpinion']['properties']['choice']['enum']) == {'hold', 'reduce', 't_watch'}


@pytest.mark.parametrize('patch,peers', [({'reply_to': ['invented']}, set()),
    ({'reply_to': ['risk'], 'challenge': '回应依据'}, {'trend'}),
    ({'reply_to': [], 'challenge': '回应依据'}, {'trend'}),
    ({'reply_to': ['trend'], 'challenge': ''}, {'trend'})])
def test_position_model_rejects_fabricated_or_missing_peer_when_round_has_exact_peers(patch: dict[str, Any], peers: set[str]) -> None:
    with pytest.raises(ModelContractError) as error:
        discussion._parse(json.dumps(OPINION | patch), discussion.RoleOpinion, EVIDENCE, PRICES, peers,
                          {'sellable_shares': 1000, 'pause_t': False})
    assert error.value.code == 'peer_reference'


@pytest.mark.parametrize('peers,patch', [(set(), {}), ({'trend'}, {'reply_to': ['trend'], 'challenge': '核对同行的当前证据'})])
def test_position_model_accepts_actual_peer_when_independent_or_challenge_contract_matches(peers: set[str], patch: dict[str, Any]) -> None:
    result = discussion._parse(json.dumps(OPINION | patch), discussion.RoleOpinion, EVIDENCE, PRICES, peers,
                               {'sellable_shares': 1000, 'pause_t': False})
    assert result['reply_to'] == patch.get('reply_to', [])


@pytest.mark.parametrize('action,guards,code', [
    ('sell_review', {'sellable_shares': 0, 'pause_t': True}, 'hard_constraint'),
    ('sell_review', {'sellable_shares': 1000, 'pause_t': True, 'quote_issue': '旧行情'}, 'hard_constraint'),
    ('buy_review', {'sellable_shares': 1000, 'pause_t': True}, 'hard_constraint'),
])
def test_position_model_parser_rejects_forged_action_even_when_generation_schema_is_bypassed(action: str, guards: dict[str, Any], code: str) -> None:
    row = deepcopy(OPINION)
    row['actions'][0]['action'] = action
    with pytest.raises(ModelContractError) as error:
        discussion._parse(json.dumps(row), discussion.RoleOpinion, EVIDENCE, PRICES, set(), guards)
    assert error.value.code == code


def test_position_model_failure_returns_fixed_reason_when_error_contains_private_material() -> None:
    safe = discussion._model_failure(ModelContractError('peer_reference'))
    assert safe['failure_kind'] == 'invalid_output' and safe['contract_issue_code'] == 'peer_reference'
    unsafe = discussion._model_failure(ValueError('private-key provider raw model output'))
    assert 'private-key' not in json.dumps(unsafe) and 'provider raw' not in json.dumps(unsafe)
    assert 'contract_issue_code' not in unsafe


def test_position_model_schema_receives_current_guards_in_all_real_orchestration_requests(discussion_client: tuple[Any, ...]) -> None:
    client, event, worksheet, calls = discussion_client
    response = client.post(f'/api/alerts/{event}/discussion', json={
        'choice': None, 'expected_position': worksheet['position_snapshot'], 'expected_revision': worksheet['revision']})
    assert response.status_code == 200 and len(calls) == 7
    for payload in calls:
        actions = set(payload['output_schema']['$defs']['Observation']['properties']['action']['enum'])
        guards = payload['guards']
        assert ('buy_review' in actions) == (bool(payload['price_refs']) and not guards['pause_t'])
        assert ('sell_review' in actions) == (bool(payload['price_refs']) and bool(guards['sellable_shares']) and not guards['quote_issue'])


def test_position_model_invalid_peer_is_excluded_from_replies_and_summary_when_other_roles_succeed(
    discussion_client: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, event, worksheet, calls = discussion_client
    real = discussion.call_model
    async def model(*args: Any, **kwargs: Any) -> str:
        text = await real(*args, **kwargs)
        payload = args[1]
        if not payload.get('peer_opinions') and payload.get('role') == discussion.ROLES['risk'][1]:
            return json.dumps(json.loads(text) | {'reply_to': ['invented-peer']})
        return text
    monkeypatch.setattr(discussion, 'call_model', model)
    result = client.post(f'/api/alerts/{event}/discussion', json={
        'choice': None, 'expected_position': worksheet['position_snapshot'], 'expected_revision': worksheet['revision']}).json()['data']
    failed = next(row for row in result['rounds'][0]['opinions'] if row['role'] == 'risk')
    assert failed['contract_issue_code'] == 'peer_reference' and not failed.get('opinion')
    assert all('risk' not in payload.get('peer_opinions', {}) for payload in calls)
    assert all(row['role'] != 'risk' for row in calls[-1]['rounds'][0]['opinions'])
    assert result['status'] == 'partial'


def test_position_model_example_does_not_reintroduce_retired_default_when_environment_is_new() -> None:
    example = (Path(__file__).resolve().parents[2] / '.env.example').read_text()
    assert 'AGNES_MODEL=agnes-3.0-flash' in example and 'AGNES_MODEL=agnes-2.0-flash' not in example


@pytest.mark.parametrize('shares,issue,pause,expected', [
    (1000, '', False, ['hold', 'reduce', 't_watch']),
    (0, '', True, ['hold']),
    (1000, '旧行情', True, ['hold']),
    (1000, '', True, ['hold', 'reduce']),
])
def test_position_model_comparison_schema_keeps_all_routes_but_only_guard_allowed_focus(
    shares: int, issue: str, pause: bool, expected: list[str],
) -> None:
    guards = {'sellable_shares': shares, 'quote_issue': issue, 'pause_t': pause}
    schema = discussion.output_schema(discussion.ComparisonSummary, EVIDENCE, PRICES, set(), guards=guards)
    assert schema['properties']['focus_choice']['anyOf'][0]['enum'] == expected
    for choice in expected:
        row = deepcopy(COMPARISON)
        row['focus_choice'] = choice
        assert discussion._parse(json.dumps(row), discussion.ComparisonSummary, EVIDENCE, PRICES, set(), guards)['focus_choice'] == choice
    if pause:
        rule = schema['$defs']['RouteOpinion']['allOf'][0]
        assert rule['if']['properties']['choice']['const'] == 't_watch'
        assert 'investigate' not in rule['then']['properties']['judgment']['enum']


def test_position_model_action_schema_requires_price_only_for_buy_or_sell() -> None:
    schema = discussion.output_schema(discussion.RoleOpinion, EVIDENCE, PRICES, set())
    rule = schema['$defs']['Observation']['allOf'][0]
    assert rule['if']['properties']['action']['enum'] == ['sell_review', 'buy_review']
    assert rule['then']['required'] == ['price_ref']
    assert rule['then']['properties']['price_ref'] == {'type': 'string', 'enum': ['price_0']}


@pytest.mark.parametrize('reference,condition,invalidation,issue', [
    (None, '观察大盘与行业方向是否延续', '方向变化后重新复核', None),
    ('price_0', '核对所引用观察价附近的价格行为', '所引用观察价不再支持原判断时复核', None),
    ('price_0', '观察本交易日已见低点附近的价格行为', '本交易日已见低点被有效跌破后复核', None),
    (None, '观察本交易日已见低点附近的价格行为', '方向变化后重新复核', 'action_evidence'),
    ('price_0', '观察本交易日已见低点附近的价格行为', '行情源前收盘价不再支持时复核', 'action_evidence'),
    (None, '继续持有1000股，观察大盘与行业方向', '方向变化后重新复核', 'action_numbers'),
])
def test_position_model_keeps_explanation_facts_separate_when_one_observation_has_one_price_identity(
    reference: str | None, condition: str, invalidation: str, issue: str | None,
) -> None:
    prices = {'price_0': {**PRICES['price_0'], 'label': '本交易日已见低点'},
              'price_1': {**PRICES['price_0'], 'label': '行情源前收盘价'}}
    row = deepcopy(OPINION)
    row['summary'] = '已有1000股持仓，现有股数只是判断背景，不代表新的操作数量。'
    row['actions'] = [{'action': 'observe', 'price_ref': reference, 'condition': condition, 'invalidation': invalidation}]
    if issue:
        with pytest.raises(ModelContractError) as error:
            discussion._parse(json.dumps(row), discussion.RoleOpinion, EVIDENCE, prices, set(),
                              {'sellable_shares': 1000, 'pause_t': False})
        assert error.value.code == issue
    else:
        parsed = discussion._parse(json.dumps(row), discussion.RoleOpinion, EVIDENCE, prices, set(),
                                   {'sellable_shares': 1000, 'pause_t': False})
        assert parsed['summary'] == row['summary']
        assert parsed['actions'][0]['condition'] == condition


def test_position_model_explains_field_responsibilities_in_every_discussion_phase(discussion_client: tuple[Any, ...]) -> None:
    client, event, worksheet, calls = discussion_client
    result = client.post(f'/api/alerts/{event}/discussion', json={
        'choice': None, 'expected_position': worksheet['position_snapshot'], 'expected_revision': worksheet['revision']})
    assert result.status_code == 200 and result.json()['data']['status'] == 'complete'
    assert len(calls) == 7
    for payload in calls:
        schema = payload['output_schema']
        explanation = schema['properties']['conclusion' if 'rounds' in payload else 'summary']['description']
        assert '解释' in explanation and '历史量价' in explanation
        fields = schema['$defs']['Observation']['properties']
        assert '共用的唯一价位身份' in fields['price_ref']['description']
        assert all('null时不点名任何价位' in fields[key]['description'] for key in ('condition', 'invalidation'))
        assert payload['guards']['no_orders'] and set(fields['price_ref']['anyOf'][0]['enum']) == set(payload['price_refs'])


def test_position_model_preserves_full_audit_when_all_generation_phases_use_references(discussion_client: tuple[Any, ...]) -> None:
    client, event, worksheet, calls = discussion_client
    result = client.post(f'/api/alerts/{event}/discussion', json={
        'choice': None, 'expected_position': worksheet['position_snapshot'], 'expected_revision': worksheet['revision']}).json()['data']
    full = result['evidence_snapshot']
    assert result['status'] == 'complete' and len(calls) == 7
    for payload in calls:
        generated = json.loads(json.dumps(payload['evidence']))
        assert set(generated) == set(full)
        assert generated['holding'] == full['holding'] == worksheet['position_snapshot']
        assert generated['stock'] == full['stock']
        for key in ('market', 'sector', 'stock'):
            assert generated['route_constraints']['evidence'][key] == {'evidence_ref': key}
            assert full['route_constraints']['evidence'][key] == full[key]
        for key, price in result['price_refs'].items():
            assert generated[key] == {'price_ref': key}
            assert full[key] == payload['price_refs'][key] == price
    stored = client.get(f'/api/alerts/{event}/action-plan').json()['data']['comparison']
    assert stored['evidence_snapshot'] == full
