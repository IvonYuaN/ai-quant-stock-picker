"""Action reference contracts do not depend on incidental numeric price values."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from model_contract import ModelContractError
from strategy_review_context import validate_position_action_numbers


STAMP = '2026-10-29T15:00:00+08:00'
EVIDENCE = {'quote': {'price': 1.0, 'as_of': STAMP, 'source': 'synthetic'},
            'holding': {'shares': 1000}}
PRICES = {'price_1': {'price': 1.0, 'label': '本交易日已见高点',
                      'source': 'synthetic', 'as_of': STAMP}}


def opinion(text: str, field: str = 'condition', reference: str = 'price_1') -> dict[str, Any]:
    action = {'action': 'observe', 'price_ref': reference,
              'condition': '观察所引用观察价，仍需人工复核',
              'invalidation': '依据改变时重新分析',
              'prerequisite_ids': [], 'invalidation_ids': []}
    action[field] = text
    return {'summary': '仅使用当前有源事实', 'actions': [action]}


@pytest.mark.parametrize('field', ['condition', 'invalidation'])
@pytest.mark.parametrize('text', [
    '等待price_1后人工复核',
    '达到price_0001后人工复核',
    '达到price_9999后人工复核',
    '达到PRICE_1后人工复核',
    '达到price_1_suffix后人工复核',
    '达到prefix_price_1后人工复核',
    '行业条件sector:falling:v1满足后复核',
    '市场条件market:not_weak:v1满足后复核',
    '行业条件sector:unknown:v1满足后复核',
    '行业条件sector:falling:v999满足后复核',
    '行业条件sector:falling:v1_suffix满足后复核',
    '市场条件MARKET:weak:v1满足后复核',
])
def test_model_action_contract_rejects_embedded_id_when_suffix_matches_real_price(field: str, text: str) -> None:
    row = opinion(text, field)
    before = deepcopy(row)
    with pytest.raises(ModelContractError) as caught:
        validate_position_action_numbers(row, EVIDENCE, PRICES)
    assert caught.value.code == 'embedded_reference'
    assert str(caught.value) == str(ModelContractError('embedded_reference'))
    assert text not in str(caught.value)
    assert row == before


@pytest.mark.parametrize('field', ['condition', 'invalidation'])
def test_model_action_contract_rejects_exact_custom_reference_when_key_has_no_numeric_suffix(field: str) -> None:
    row = opinion('等待current_low后核对', field, 'current_low')
    with pytest.raises(ModelContractError) as caught:
        validate_position_action_numbers(row, EVIDENCE, {'current_low': PRICES['price_1']})
    assert caught.value.code == 'embedded_reference'


@pytest.mark.parametrize('text', [
    '等到所引用观察价后核对，不能把到价当成成交',
    '观察当前有源高点1.0元，仍须人工复核',
    '遵守T+1，不把可卖底仓误当作操作数量',
    '板块转弱时重新评估；大盘方向仍需核对',
    '2026-10-29T15:00:00+08:00的观察依据需要复核',
    'price只是普通词，引用使用对应字段',
])
def test_model_action_contract_preserves_natural_language_when_reference_stays_structured(text: str) -> None:
    row = opinion(text)
    before = deepcopy(row)
    validate_position_action_numbers(row, EVIDENCE, PRICES)
    assert row == before


def test_model_action_contract_uses_ascii_token_boundaries_when_custom_id_is_inside_ordinary_word() -> None:
    row = opinion('slowly review the observation', reference='low')
    validate_position_action_numbers(row, EVIDENCE, {'low': PRICES['price_1']})


def test_model_action_contract_keeps_explanation_and_structured_ids_when_action_has_no_embedded_reference() -> None:
    row = opinion('所引用观察价附近仍需人工复核')
    row['summary'] = '原观察记录使用price_1和sector:falling:v1，仅作为历史说明'
    row['actions'][0]['prerequisite_ids'] = ['sector:falling:v1']
    before = deepcopy(row)
    validate_position_action_numbers(row, EVIDENCE, PRICES)
    assert row == before


@pytest.mark.parametrize('text', [
    '复核是否保留全部1000股敞口',
    '观察999元是否止跌',
    '跌破九点八元则取消',
    '2026-10-28的观察依据需要复核',
    '跌破2026-10-29元时取消',
])
def test_model_action_contract_reports_safe_number_error_when_action_uses_forbidden_number(text: str) -> None:
    row = opinion(text)
    before = deepcopy(row)
    with pytest.raises(ModelContractError, match='当前有源') as caught:
        validate_position_action_numbers(row, EVIDENCE, PRICES)
    assert caught.value.code == 'action_numbers'
    assert str(caught.value) == str(ModelContractError('action_numbers'))
    assert row == before


@pytest.mark.parametrize('text', [
    '5根完成日线末日量比1倍后买回',
    '本交易日已见低点附近先复核',
])
def test_model_action_contract_reports_safe_evidence_error_when_window_or_price_identity_is_invalid(text: str) -> None:
    row = opinion(text)
    before = deepcopy(row)
    with pytest.raises(ModelContractError) as caught:
        validate_position_action_numbers(row, EVIDENCE, PRICES)
    assert caught.value.code == 'action_evidence'
    assert str(caught.value) == str(ModelContractError('action_evidence'))
    assert row == before
