"""Historical explanations cannot widen the current action price namespace."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

import position_discussion as discussion
from strategy_review_context import validate_position_action_numbers
from test_position_discussion import OPINION

PRICES = {'current_low': {'price': 9.1, 'label': '本交易日低点', 'source': '合成源', 'as_of': '2026-10-27T10:00:00+08:00'}}
EVIDENCE = {'sector': {'change_pct': 1, 'source': '合成源', 'as_of': '2026-10-27T10:00:00+08:00'},
            'research': {'candidate': {'score': 75, 'data_source': 'synthetic'}},
            'adopted_plan': {'price_refs': {'old_low': {'price': 9.8, 'source': '原合成源', 'as_of': '2026-10-26T10:00:00+08:00'}}}}


@pytest.mark.parametrize('text', ['跌破9.8元则取消', '高于123.45再核对', '跌破九点八元再核对'])
def test_position_review_rejects_old_or_unsourced_action_number_when_reference_is_current(text: str) -> None:
    row = deepcopy(OPINION)
    row['summary'] = '原来9.8元只是历史对比'
    row['actions'][0].update(price_ref='current_low', condition=text)
    with pytest.raises(ValueError, match='当前有源'):
        discussion._parse(json.dumps(row), discussion.RoleOpinion, EVIDENCE, PRICES, set(), {'pause_t': False, 'sellable_shares': 1000})


@pytest.mark.parametrize('text', [
    '板块上涨1%，仍须人工复核T+1规则',
    '有源评分75分不能作为价位，等待当前参考价后核对',
    '评分75仅为事实；2026-10-27T10:00:00+08:00的行情仍需复核',
    '2026-10-27 10:00:00的有源低点9.1元是观察参考',
])
def test_position_review_accepts_sourced_units_dates_and_t_plus_one_when_explaining_action(text: str) -> None:
    row = deepcopy(OPINION)
    row['actions'][0].update(price_ref='current_low', condition=text)
    validate_position_action_numbers(row, EVIDENCE, PRICES)


@pytest.mark.parametrize('text', ['回落至75元后核对', '板块上涨5%说明可回补', '买入100股',
                                  '评分75倍说明可回补', '当前参考价9.1分', '放量9.1倍', '报价9.1点'])
def test_position_review_rejects_wrong_unit_or_invented_quantity_when_number_has_other_source(text: str) -> None:
    row = deepcopy(OPINION)
    row['actions'][0]['condition'] = text
    with pytest.raises(ValueError):
        validate_position_action_numbers(row, EVIDENCE, PRICES)
