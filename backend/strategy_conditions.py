"""Pure, versioned observation predicates shared by candidate/position adapters.

Adapters own source/session checks. This module performs no I/O, scores, orders,
price discovery or inference from prose. A machine match always leaves manual
price/volume confirmation outstanding.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Literal

ConditionStatus = Literal['met', 'unmet', 'unknown', 'manual_required']


@dataclass(frozen=True)
class ConditionDefinition:
    id: str
    label: str
    kind: str
    evidence_key: str
    accepted_statuses: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {'id': self.id, 'label': self.label, 'kind': self.kind,
                'evidence_key': self.evidence_key, 'accepted_statuses': list(self.accepted_statuses)}


# Existing direction vocabulary, not new strategy thresholds. IDs/definitions
# remain immutable within v1; future semantics need a separate version.
DEFINITIONS = (
    ConditionDefinition('market:not_weak:v1', '市场宽度不再偏弱', 'market_status', 'market', ('neutral', 'strong')),
    ConditionDefinition('market:weak:v1', '市场宽度偏弱', 'market_status', 'market', ('weak',)),
    ConditionDefinition('sector:not_falling:v1', '所属行业当前方向不再下跌', 'sector_status', 'sector', ('flat', 'rising')),
    ConditionDefinition('sector:falling:v1', '所属行业当前方向下跌', 'sector_status', 'sector', ('falling',)),
)


@dataclass(frozen=True)
class ConditionFact:
    """An adapter's explicitly validated current fact; absent proof stays unknown."""
    value: str | float | None = None
    source: str = ''
    as_of: str | None = None
    valid: bool = False


def digest(value: Any) -> str:
    """Canonical identity; callers choose frozen contract or semantic state."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _definition(identifier: str) -> ConditionDefinition:
    result = next((item for item in DEFINITIONS if item.id == identifier), None)
    if result is None:
        raise ValueError('观察条件引用不存在的目录ID')
    return result


def validate_selection(prerequisite_ids: Any, invalidation_ids: Any) -> None:
    """Reject unknown, duplicate or contradictory exact catalog references."""
    for identifiers in (prerequisite_ids, invalidation_ids):
        if not isinstance(identifiers, list) or any(not isinstance(item, str) for item in identifiers):
            raise ValueError('观察条件ID必须是目录列表')
        if len(identifiers) != len(set(identifiers)):
            raise ValueError('观察条件ID重复')
        definitions = [_definition(item) for item in identifiers]
        for index, first in enumerate(definitions):
            if any(first.evidence_key == second.evidence_key and not set(first.accepted_statuses).intersection(second.accepted_statuses)
                   for second in definitions[index + 1:]):
                raise ValueError('同一前提或失效条件包含相互矛盾的方向')
    if set(prerequisite_ids).intersection(invalidation_ids):
        raise ValueError('同一条件不能同时作为前提与失效条件')


def _direction_check(definition: ConditionDefinition, fact: ConditionFact, role: str) -> dict[str, Any]:
    known = fact.valid and bool(fact.source and fact.as_of) and isinstance(fact.value, str)
    allowed = {'market': {'weak', 'neutral', 'strong'}, 'sector': {'falling', 'flat', 'rising'}}
    known = known and fact.value in allowed[definition.evidence_key]
    status: ConditionStatus = ('met' if fact.value in definition.accepted_statuses else 'unmet') if known else 'unknown'
    detail = ('当前有源方向与该条件相符' if status == 'met' else '当前有源方向尚未符合该条件') if known else '缺少当前有效来源，不能判断该条件'
    return {'id': definition.id, 'label': definition.label, 'role': role, 'status': status,
            'value': fact.value if known else None, 'detail': detail,
            'source': fact.source if known else '', 'as_of': fact.as_of if known else None}


def condition_catalog(facts: Mapping[str, ConditionFact]) -> dict[str, Any]:
    """Offer all supported predicates; never prescribe which a model must pick."""
    items = []
    for definition in DEFINITIONS:
        check = _direction_check(definition, facts.get(definition.evidence_key, ConditionFact()), 'prerequisite')
        items.append({**{key: value for key, value in definition.as_dict().items() if key != 'accepted_statuses'},
                      **{key: check[key] for key in ('status', 'value', 'detail', 'source', 'as_of')}})
    return {'schema_version': 1, 'items': items}


def _price_predicate(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    keys = {'price_ref', 'comparator', 'price', 'source', 'as_of'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('冻结报价条件结构无效')
    price = value['price']
    if (type(price) not in (int, float) or not math.isfinite(price) or price <= 0
            or value['comparator'] not in {'at_or_above', 'at_or_below'}
            or any(not isinstance(value[key], str) or not value[key] for key in ('price_ref', 'source', 'as_of'))):
        raise ValueError('冻结报价条件没有有效来源或比较方向')
    return deepcopy(value)


def _origin(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'discussion_id', 'action_index', 'choice'}
            or not isinstance(value['discussion_id'], str) or not value['discussion_id']
            or value['choice'] not in {'pullback', 'confirmation', 'defer'}
            or (value['choice'] == 'defer' and value['action_index'] is not None)
            or (value['choice'] != 'defer' and (type(value['action_index']) is not int or value['action_index'] < 0))):
        raise ValueError('冻结条件缺少原讨论动作身份')
    return deepcopy(value)


def freeze_contract(prerequisite_ids: list[str], invalidation_ids: list[str], *,
                    price_predicate: dict[str, Any] | None, condition: str, invalidation: str,
                    origin: dict[str, Any] | None = None) -> dict[str, Any]:
    """Freeze exact definitions and reference operands at human adoption."""
    validate_selection(prerequisite_ids, invalidation_ids)
    if not isinstance(condition, str) or not isinstance(invalidation, str):
        raise ValueError('人工核验说明无效')
    contract = {'schema_version': 1, 'origin': _origin(origin), 'prerequisite_ids': sorted(prerequisite_ids),
                'invalidation_ids': sorted(invalidation_ids),
                'definitions': [_definition(identifier).as_dict() for identifier in sorted(set(prerequisite_ids + invalidation_ids))],
                'price_predicate': _price_predicate(price_predicate),
                'manual_checks': [{'id': 'manual:price_volume:v1' if price_predicate is not None else 'manual:premises:v1',
                                   'label': '人工确认价格与量价前提' if price_predicate is not None else '人工复核原前提与失效说明',
                                   'detail': (f'原观察条件：{condition}；原失效说明：{invalidation}。'
                                              + ('到价及方向匹配仍不能确认止跌、放量、突破有效或可以买入。' if price_predicate is not None
                                                 else '保持暂缓；仅在依据变化后人工复核，不产生参与或成交任务。'))}]}
    return {**contract, 'digest': digest(contract)}


def _position_origin(value: Any) -> dict[str, Any]:
    keys = {'kind', 'position_id', 'analysis_basis_id', 'source_discussion', 'generated_at',
            'action_index', 'choice', 'action', 'price_ref'}
    if (not isinstance(value, dict) or set(value) != keys or value['kind'] != 'position'
            or value['source_discussion'] not in {'comparison', 'plan'}
            or value['choice'] not in {'hold', 'reduce', 't_watch'}
            or value['action'] not in {'observe', 'sell_review', 'buy_review'}
            or type(value['action_index']) is not int or value['action_index'] < 0
            or any(not isinstance(value[key], str) or not value[key].strip()
                   for key in ('position_id', 'analysis_basis_id', 'generated_at', 'price_ref'))):
        raise ValueError('持仓冻结条件缺少有效动作身份')
    return deepcopy(value)


def freeze_position_contract(prerequisite_ids: list[str], invalidation_ids: list[str], *,
                             price_predicate: dict[str, Any], condition: str, invalidation: str,
                             origin: dict[str, Any]) -> dict[str, Any]:
    """Position v2 has a distinct origin; existing candidate v1 stays byte-stable."""
    body = freeze_contract(prerequisite_ids, invalidation_ids, price_predicate=price_predicate,
                           condition=condition, invalidation=invalidation)
    body.pop('digest')
    body.update(schema_version=2, origin=_position_origin(origin))
    body['manual_checks'] = [{'id': 'manual:price_volume:v1', 'label': '人工核对买卖前提与原路线限制',
                             'detail': f'原观察条件：{condition}；原失效说明：{invalidation}。到价或方向匹配不能证明转弱、止跌、放量、突破有效或已经成交；做T须保留卖后买与实际持仓限制。'}]
    return {**body, 'digest': digest(body)}


def validate_contract(value: Any) -> dict[str, Any]:
    """Reject changed operands/definitions; old records without a contract are separate."""
    keys = {'schema_version', 'digest', 'prerequisite_ids', 'invalidation_ids', 'definitions', 'price_predicate', 'manual_checks', 'origin'}
    if not isinstance(value, dict) or set(value) != keys or type(value['schema_version']) is not int or value['schema_version'] not in {1, 2}:
        raise ValueError('观察条件合同版本或结构无效')
    validate_selection(value['prerequisite_ids'], value['invalidation_ids'])
    if any(value[key] != sorted(value[key]) for key in ('prerequisite_ids', 'invalidation_ids')):
        raise ValueError('冻结条件ID顺序不符合规范')
    definitions = [_definition(identifier).as_dict() for identifier in sorted(set(value['prerequisite_ids'] + value['invalidation_ids']))]
    if value['definitions'] != definitions:
        raise ValueError('冻结条件定义已被更改')
    _price_predicate(value['price_predicate'])
    if value['schema_version'] == 1:
        _origin(value['origin'])
    else:
        _position_origin(value['origin'])
        if value['price_predicate'] is None:
            raise ValueError('持仓冻结条件缺少报价比较')
    manual = value['manual_checks']
    if (not isinstance(manual, list) or len(manual) != 1 or not isinstance(manual[0], dict)
            or set(manual[0]) != {'id', 'label', 'detail'}
            or manual[0]['id'] != ('manual:price_volume:v1' if value['price_predicate'] is not None else 'manual:premises:v1')
            or any(not isinstance(manual[0][key], str) or not manual[0][key] for key in ('label', 'detail'))):
        raise ValueError('冻结条件缺少人工核验说明')
    body = {key: item for key, item in value.items() if key != 'digest'}
    if not isinstance(value['digest'], str) or value['digest'] != digest(body):
        raise ValueError('观察条件合同摘要不一致')
    return deepcopy(value)


def _price_check(predicate: dict[str, Any], fact: ConditionFact) -> dict[str, Any]:
    known = (fact.valid and bool(fact.source and fact.as_of) and type(fact.value) in (int, float)
             and math.isfinite(fact.value) and fact.value > 0)
    reached = (fact.value >= predicate['price'] if predicate['comparator'] == 'at_or_above' else fact.value <= predicate['price']) if known else False
    status = ('met' if reached else 'unmet') if known else 'unknown'
    direction = '达到或高于' if predicate['comparator'] == 'at_or_above' else '达到或低于'
    return {'id': 'quote:observed_price:v1', 'label': f"报价{direction} {predicate['price']:g} 元", 'role': 'price',
            'status': status, 'value': fact.value if known else None,
            'detail': '只核对已采用的报价比较，不代表止跌、突破确认或买入信号' if known else '报价缺少当前有效来源，不能核对到价',
            'source': fact.source if known else '', 'as_of': fact.as_of if known else None}


def semantic_state(items: Sequence[Mapping[str, Any]], *, contract_digest: str | None, mode: str) -> str:
    """Notification identity excludes changing values, times and display prose."""
    return digest({'mode': mode, 'contract': contract_digest,
                   'conditions': sorted(({'id': item['id'], 'role': item['role'], 'status': item['status']} for item in items),
                                        key=lambda item: (item['id'], item['role']))})


def evaluate_contract(contract: dict[str, Any] | None, facts: Mapping[str, ConditionFact], *,
                      legacy_price: dict[str, Any] | None = None, legacy_condition: str = '',
                      legacy_invalidation: str = '') -> dict[str, Any]:
    """Evaluate current validated facts without inferring predicates from text."""
    frozen = validate_contract(contract) if contract is not None else None
    mode = 'registered' if frozen is not None else 'legacy'
    items = []
    if frozen is not None:
        for role, key in (('prerequisite', 'prerequisite_ids'), ('invalidation', 'invalidation_ids')):
            items.extend(_direction_check(_definition(identifier), facts.get(_definition(identifier).evidence_key, ConditionFact()), role)
                         for identifier in frozen[key])
    predicate = frozen['price_predicate'] if frozen is not None else legacy_price
    if predicate is not None:
        items.append(_price_check(_price_predicate(predicate), facts.get('quote', ConditionFact())))
    manual = frozen['manual_checks'] if frozen is not None else [{'id': 'manual:legacy_text:v1', 'label': '旧版文字条件，需人工核验',
        'detail': f'旧观察条件：{legacy_condition}；旧失效说明：{legacy_invalidation}。未逐项登记，不从原文推断自动条件。'}]
    items.extend({**item, 'role': 'manual', 'status': 'manual_required', 'value': None, 'source': '', 'as_of': None} for item in manual)
    automatic = [item for item in items if item['role'] != 'manual']
    summary = ('review' if any(item['role'] == 'invalidation' and item['status'] == 'met' for item in automatic)
               else 'unknown' if any(item['status'] == 'unknown' for item in automatic)
               else 'waiting' if any(item['role'] in {'prerequisite', 'price'} and item['status'] == 'unmet' for item in automatic)
               else 'manual_required')
    return {'schema_version': 1, 'mode': mode, 'summary': summary, 'items': items,
            'digest': semantic_state(items, contract_digest=frozen['digest'] if frozen else None, mode=mode)}
