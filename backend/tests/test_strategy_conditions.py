"""Pure observation contracts never infer a trading predicate from prose."""
from __future__ import annotations

from copy import deepcopy
import pytest

import strategy_conditions as conditions

STAMP = '2026-10-27T10:00:00+08:00'
PRICE = {'price_ref': 'low', 'comparator': 'at_or_below', 'price': 9.8, 'source': '合成报价源', 'as_of': STAMP}


def contract(prerequisite_ids=None, invalidation_ids=None):
    return conditions.freeze_contract(prerequisite_ids or [], invalidation_ids or [],
        price_predicate=PRICE, condition='回落后人工核对是否止跌', invalidation='风险变化后复核')


def facts(**patch):
    return {'market': conditions.ConditionFact('strong', '合成市场源', STAMP, True),
            'sector': conditions.ConditionFact('rising', '合成行业源', STAMP, True),
            'quote': conditions.ConditionFact(9.8, '合成报价源', STAMP, True), **patch}


def test_condition_catalog_reports_current_status_without_requiring_any_predicate():
    value = conditions.condition_catalog(facts())
    assert value['schema_version'] == 1 and len(value['items']) == 4
    assert value['items'][0]['status'] == 'met' and value['items'][0]['value'] == 'strong'
    empty = contract()
    assert empty['prerequisite_ids'] == empty['invalidation_ids'] == empty['definitions'] == []
    result = conditions.evaluate_contract(empty, facts())
    assert result['summary'] == 'manual_required'
    assert any(item['role'] == 'manual' and item['status'] == 'manual_required' for item in result['items'])


@pytest.mark.parametrize('prerequisites,invalidations', [
    (['missing'], []), (['market:not_weak:v1', 'market:not_weak:v1'], []),
    (['market:weak:v1', 'market:not_weak:v1'], []),
    (['sector:falling:v1'], ['sector:falling:v1']),
    ([], ['sector:falling:v1', 'sector:not_falling:v1']),
    ('market:weak:v1', []), ([True], []),
])
def test_condition_contract_rejects_unknown_duplicate_or_conflicting_ids(prerequisites, invalidations):
    with pytest.raises(ValueError):
        conditions.freeze_contract(prerequisites, invalidations, price_predicate=None, condition='说明', invalidation='失效说明')


@pytest.mark.parametrize('mutation', ['digest', 'definition', 'role', 'order', 'duplicate', 'operand', 'version', 'manual', 'extra'])
def test_condition_contract_rejects_changed_frozen_structure(mutation):
    value = contract(['market:not_weak:v1', 'sector:not_falling:v1'])
    if mutation == 'digest':
        value['digest'] = 'changed'
    elif mutation == 'definition':
        value['definitions'][0]['accepted_statuses'] = ['weak']
    elif mutation == 'role':
        value['invalidation_ids'] = value['prerequisite_ids']
        value['prerequisite_ids'] = []
    elif mutation == 'order':
        value['prerequisite_ids'].reverse()
        value['digest'] = conditions.digest({key: item for key, item in value.items() if key != 'digest'})
    elif mutation == 'duplicate':
        value['prerequisite_ids'].append(value['prerequisite_ids'][0])
    elif mutation == 'operand':
        value['price_predicate']['price'] = 1
    elif mutation == 'version':
        value['schema_version'] = True
    elif mutation == 'manual':
        value['manual_checks'] = []
    else:
        value['formula'] = 'volume > invented'
    with pytest.raises(ValueError):
        conditions.validate_contract(value)


def test_condition_match_is_separate_from_invalidation_and_manual_confirmation():
    frozen = contract(['market:not_weak:v1'], ['sector:falling:v1'])
    original = deepcopy(frozen)
    matched = conditions.evaluate_contract(frozen, facts())
    assert matched['summary'] == 'manual_required'
    failed = conditions.evaluate_contract(frozen, facts(sector=conditions.ConditionFact('falling', '合成行业源', STAMP, True)))
    assert failed['summary'] == 'review'
    assert next(item for item in failed['items'] if item['role'] == 'invalidation')['status'] == 'met'
    assert frozen == original


@pytest.mark.parametrize('fact', [conditions.ConditionFact(), conditions.ConditionFact('strong', '', STAMP, True),
    conditions.ConditionFact('strong', '源', None, True), conditions.ConditionFact('strong', '源', STAMP, False),
    conditions.ConditionFact('unknown', '源', STAMP, True)])
def test_condition_missing_or_unvalidated_source_is_unknown_instead_of_unmet(fact):
    value = conditions.evaluate_contract(contract(['market:not_weak:v1']), facts(market=fact))
    assert value['summary'] == 'unknown'
    assert value['items'][0]['status'] == 'unknown' and value['items'][0]['value'] is None


def test_condition_digest_changes_only_with_semantic_status_or_frozen_contract():
    frozen = contract(['market:not_weak:v1'])
    first = conditions.evaluate_contract(frozen, facts())
    refreshed = conditions.evaluate_contract(frozen, facts(quote=conditions.ConditionFact(9.7, '合成报价源', '2026-10-27T10:01:00+08:00', True)))
    assert first['digest'] == refreshed['digest']
    changed = conditions.evaluate_contract(frozen, facts(market=conditions.ConditionFact('weak', '合成市场源', STAMP, True)))
    assert changed['digest'] != first['digest']
    assert first['digest'] != frozen['digest']


def test_condition_legacy_text_never_becomes_a_direction_or_volume_predicate():
    result = conditions.evaluate_contract(None, facts(), legacy_price=PRICE,
        legacy_condition='放量止跌后买入', legacy_invalidation='行业转弱')
    assert result['mode'] == 'legacy'
    assert [item['role'] for item in result['items']] == ['price', 'manual']
    assert result['items'][-1]['status'] == 'manual_required'
    assert '放量止跌后买入' in result['items'][-1]['detail']


def test_condition_opposite_roles_can_express_waiting_for_weakness_without_forcing_a_thesis():
    frozen = contract(['market:weak:v1'], ['market:not_weak:v1'])
    assert frozen['prerequisite_ids'] == ['market:weak:v1']
    assert conditions.evaluate_contract(frozen, facts())['summary'] == 'review'
