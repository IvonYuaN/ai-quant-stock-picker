"""Semantic direction changes pause plans; quote refreshes do not."""
from copy import deepcopy

import pytest

from strategy_direction import direction_identity


def facts() -> dict:
    return {'market': {'status': 'strong', 'source': '市场源', 'breadth': '偏强',
                       'indices': [{'name': '上证指数', 'source': '指数源', 'change_pct': 1.2, 'as_of': 'old'}]},
            'sector': {'status': 'rising', 'source': '行业源', 'industry': '银行', 'change_pct': 2},
            'stock': {'status': 'rising', 'source': '个股源'}}


@pytest.mark.parametrize(('dimension', 'field', 'value'), [
    ('sector', 'industry', '半导体'), ('sector', 'source', '另一行业源'),
    ('market', 'breadth', '普涨'), ('stock', 'status', 'unknown'),
])
def test_direction_identity_changes_when_semantic_premise_changes(dimension, field, value) -> None:
    original = facts()
    changed = deepcopy(original)
    changed[dimension][field] = value
    assert direction_identity(original) != direction_identity(changed)


def test_direction_identity_changes_when_index_turns_or_source_disappears() -> None:
    original = facts()
    changed = deepcopy(original)
    changed['market']['indices'][0]['change_pct'] = -0.1
    assert direction_identity(original) != direction_identity(changed)
    changed['market']['indices'] = []
    assert direction_identity(original) != direction_identity(changed)


def test_direction_identity_stays_when_only_numbers_and_source_times_refresh() -> None:
    original = facts()
    changed = deepcopy(original)
    changed['market'].update(as_of='new', breadth_counts={'up': 3500, 'down': 1500})
    changed['market']['indices'][0].update(change_pct=1.4, as_of='new')
    changed['sector'].update(change_pct=2.7, as_of='new', summary='新展示文字')
    assert direction_identity(original) == direction_identity(changed)


def test_direction_identity_is_stable_when_index_order_changes() -> None:
    original = facts()
    original['market']['indices'].append({'name': '深证成指', 'source': '指数源', 'change_pct': -1})
    changed = deepcopy(original)
    changed['market']['indices'].reverse()
    assert direction_identity(original) == direction_identity(changed)
