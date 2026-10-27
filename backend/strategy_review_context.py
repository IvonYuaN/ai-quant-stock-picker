"""Pure adopted-plan comparisons. Historical evidence never supplies action refs."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
import re
from typing import Any
from model_source_time import trusted_source_stamps, without_source_times


CANDIDATE_CHANGES = {
    'research_date': ('研究日期变化', ('research', 'date')),
    'parameters': ('策略参数版本变化', ('research', 'parameter_snapshot')),
    'candidate': ('个股评分或研究证据变化', ('research', 'candidate')),
    'risk_gate': ('研究风险门禁变化', ('research', 'recommendation_gate')),
    'research_source': ('研究来源变化', ('research', 'source')),
    'debate': ('研究讨论结论变化', ('research', 'debate')),
    'ownership': ('持仓归属变化', ()),
    'quote_source': ('行情来源或有效性变化', ('quote',)),
    'directions': ('大盘、板块或个股方向变化', ()),
}
REVIEW_NOTE = ('原证据只用于采用时与现在的历史对比，不是当前行动依据；'
               '新增观察条件只能引用本次有效 price_refs。比较维持原路、调整或暂缓的依据，'
               '缺原证据时明确未知，不从现在倒推；本次分析不修改人工计划，也不要求重新选择。')


def _at(value: dict[str, Any], path: tuple[str, ...]) -> Any:
    result: Any = value
    for key in path:
        result = result.get(key) if isinstance(result, dict) else None
    return deepcopy(result)


def _direction(evidence: dict[str, Any]) -> dict[str, Any]:
    return {key: {field: (evidence.get(key) or {}).get(field) for field in ('status', 'source', 'industry')}
            for key in ('market', 'sector', 'stock')}


def _quote_facts(evidence: dict[str, Any]) -> dict[str, Any]:
    quote = evidence.get('quote') or {}
    return {key: quote.get(key) for key in ('price', 'change_pct', 'as_of', 'source')}


def _change(category: str, label: str, before: Any, after: Any) -> dict[str, Any]:
    return {'category': category, 'label': label, 'before': deepcopy(before), 'after': deepcopy(after)}


def _base(kind: str, current: dict[str, Any]) -> dict[str, Any]:
    return {'kind': kind, 'status': 'not_adopted', 'original': None, 'current': deepcopy(current),
            'changes': [], 'missing_evidence': [], 'material_changed': False, 'quote_updated': False,
            'notice': REVIEW_NOTE}


def _identity(context: dict[str, Any], watch: dict[str, Any], symbol: str,
              position_id: str | None = None) -> bool:
    identity = watch.get('evidence_identity')
    if identity is not None and identity != {'symbol': symbol,
                                             **({'position_id': position_id} if position_id else {})}:
        context.update(status='identity_mismatch', missing_evidence=['原方案身份与当前租户或标的不一致，未使用原证据'])
        return False
    if position_id and (watch.get('position_snapshot') or {}).get('position_id') != position_id:
        context.update(status='identity_mismatch', missing_evidence=['原方案属于另一笔持仓，未使用原证据'])
        return False
    return True


def candidate_review_context(value: dict[str, Any]) -> dict[str, Any]:
    """Compare a same-tenant candidate's frozen adoption to current server facts."""
    current = {key: deepcopy(value.get(key)) for key in ('symbol', 'evidence_version', 'evidence_basis', 'ownership', 'entry_blocked')}
    current.update(evidence_snapshot=deepcopy(value['evidence']), price_refs=deepcopy(value['price_refs']))
    context = _base('candidate', current)
    watch = value.get('watch')
    if not watch or watch.get('status') != 'active':
        return context
    if not _identity(context, watch, value['symbol']):
        return context
    # Snapshots added by this version carry explicit scope. Legacy conditions
    # remain readable, but cannot mint a historical quote from last_workspace.
    saved = deepcopy(watch.get('evidence_snapshot') or {}) if watch.get('evidence_identity') else {}
    original = {key: deepcopy(watch.get(key)) for key in ('choice', 'condition', 'invalidation', 'comparator', 'adopted_at', 'evidence_version')}
    original.update(saved_at=watch.get('adopted_at'), price_ref=deepcopy(watch.get('source_ref')),
                    evidence_snapshot=saved, price_refs=deepcopy(watch.get('price_refs') or {}) if saved else {},
                    snapshot_complete=bool(saved and all(key in saved for key in ('research', 'quote', 'market', 'sector', 'stock'))))
    context.update(status='adopted', original=original)
    if not original['snapshot_complete']:
        context['missing_evidence'].append('原记录未保存采用时完整证据，只能核对已保存的路线、条件与身份，不能还原当时行情或评分')
    previous, latest = watch.get('evidence_basis') or {}, value.get('evidence_basis') or {}
    if previous.get('schema') != latest.get('schema'):
        context['missing_evidence'].append('原依据版本缺失或不兼容，实质变化无法完整核验')
    else:
        for key, (label, path) in CANDIDATE_CHANGES.items():
            if previous.get(key) == latest.get(key):
                continue
            if key == 'directions':
                before, after = _direction(saved) if saved else None, _direction(value['evidence'])
            elif key == 'ownership':
                before, after = watch.get('ownership'), value.get('ownership')
            else:
                before, after = _at(saved, path), _at(value['evidence'], path)
            context['changes'].append(_change(key, label, before, after))
    context['material_changed'] = bool(context['changes'])
    context['quote_updated'] = bool(saved and _quote_facts(saved) != _quote_facts(value['evidence']))
    return context


def position_review_context(worksheet: dict[str, Any], evidence: dict[str, Any], prices: dict[str, Any]) -> dict[str, Any]:
    """Reuse the position watch's original facts without crossing position lives."""
    current = {key: deepcopy(worksheet.get(key)) for key in ('symbol', 'position_snapshot', 'constraints', 'trade_revision')}
    current.update(evidence_snapshot=deepcopy(evidence), price_refs=deepcopy(prices), choice=worksheet['review_plan'].get('choice'))
    context = _base('position', current)
    watch = worksheet['review_plan'].get('watch')
    if not watch:
        return context
    if not _identity(context, watch, worksheet['symbol'], worksheet['position_id']):
        return context
    saved = deepcopy(watch.get('evidence_snapshot') or {})
    original = {key: deepcopy(watch.get(key)) for key in ('condition', 'invalidation', 'comparator', 'saved_at', 'price_ref', 'position_snapshot', 'constraints', 'trade_revision')}
    original.update(choice=watch.get('choice'), evidence_snapshot=saved,
                    price_refs=deepcopy(watch.get('price_refs') or {}),
                    snapshot_complete=bool(saved and all(key in saved for key in ('holding', 'quote', 'market', 'sector', 'stock', 'risk_trigger'))))
    context.update(status='adopted', original=original)
    if not original['snapshot_complete']:
        context['missing_evidence'].append('原记录未保存采用时完整证据，仅比较已留存的条件和来源，不从当前行情倒推')
    if original['choice'] is None:
        context['missing_evidence'].append('原采用路线未单独保存，当前路线不能冒充原路线')
    for key, label in (('market', '大盘依据变化'), ('sector', '板块依据变化'), ('stock', '个股方向变化')):
        if key not in saved:
            continue
        before, after = _direction(saved)[key], _direction(evidence)[key]
        if before != after:
            context['changes'].append(_change(key, label, before, after))
    for key, label in (('position_snapshot', '持仓数量、成本或可卖股份变化'), ('constraints', '个人条件变化'), ('trade_revision', '实际成交、费用或买回关联变化')):
        if key in watch and watch[key] != current.get(key):
            context['changes'].append(_change(key, label, watch[key], current.get(key)))
    if watch.get('choice') is not None and watch['choice'] != current['choice']:
        context['changes'].append(_change('choice', '当前路线与原采用路线不同', watch['choice'], current['choice']))
    if 'quote' in saved:
        before = {'source': saved['quote'].get('source'), 'available': saved['quote'].get('price') is not None}
        after = {'source': (evidence.get('quote') or {}).get('source'), 'available': (evidence.get('quote') or {}).get('price') is not None}
        if before != after:
            context['changes'].append(_change('quote_source', '行情来源或有效性变化', before, after))
    if 'risk_trigger' in saved:
        # Checked timestamps are not new risk premises. Keep rule definitions
        # and evaluated states/thresholds; do not call a quote tick a new plan.
        def risk_parts(snapshot: dict[str, Any]) -> dict[str, Any]:
            risk = snapshot.get('risk_trigger') or {}
            fields = ('id', 'rule_id', 'level', 'status', 'threshold', 'enabled', 'revision', 'param_value', 'trigger_price')
            return {key: [{field: row.get(field) for field in fields if field in row} for row in risk.get(key, [])]
                    for key in ('rules', 'current_facts')}
        before, after = risk_parts(saved), risk_parts(evidence)
        if before != after:
            context['changes'].append(_change('risk', '当前风险规则或触线事实变化', before, after))
    context['material_changed'] = bool(context['changes'])
    context['quote_updated'] = bool(saved.get('quote') and _quote_facts(saved) != _quote_facts(evidence))
    return context


def model_review_evidence(evidence: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Expose historical references separately from the current price namespace."""
    if context['status'] == 'not_adopted':
        return deepcopy(evidence)
    return {**deepcopy(evidence), 'adopted_plan': deepcopy(context['original']),
            'review_changes': {key: deepcopy(context[key]) for key in ('changes', 'missing_evidence', 'material_changed', 'quote_updated')}}


@dataclass(frozen=True)
class SourceNumbers:
    prices: frozenset[float]
    percentages: frozenset[float]
    scores: frozenset[float]
    counts: frozenset[float]
    stamps: frozenset[str]


def source_numbers(evidence: dict[str, Any], refs: dict[str, Any]) -> SourceNumbers:
    """Read typed source fields, never numbers inside model/user prose."""
    def numbers(values: list[Any]) -> frozenset[float]:
        return frozenset(float(item) for item in values if type(item) in (int, float) and math.isfinite(item))
    rows = [row for row in refs.values() if isinstance(row, dict)]
    rows += [evidence.get('quote') or {}]
    rows = [row for row in rows if row.get('source') and row.get('as_of')]
    market, sector = evidence.get('market') or {}, evidence.get('sector') or {}
    research = evidence.get('research') or {}
    candidate = research.get('candidate') or {}
    percentages = [row.get('change_pct') for row in rows]
    if sector.get('source') and sector.get('as_of'):
        percentages.append(sector.get('change_pct'))
    stock = evidence.get('stock') or {}
    if stock.get('source') and stock.get('as_of'):
        percentages.append(stock.get('change_pct'))
    if market.get('source') and market.get('as_of'):
        percentages.extend(row.get('change_pct') for row in market.get('indices', []))
    stamps = [row.get('as_of') for row in rows] + [market.get('as_of'), sector.get('as_of'), research.get('date'),
             research.get('generated_at'), research.get('stale_after'), candidate.get('data_fetched_at')]
    return SourceNumbers(numbers([row.get('price') for row in rows]), numbers(percentages),
                         numbers([candidate.get('score')]) if candidate.get('data_source') else frozenset(),
                         numbers(list((market.get('breadth_counts') or {}).values())) if market.get('source') else frozenset(),
                         trusted_source_stamps(stamps))


def historical_numbers(context: dict[str, Any] | None) -> SourceNumbers:
    """Historical prose allowance does not modify the current-action namespace."""
    original = (context or {}).get('original') or {}
    refs = dict(original.get('price_refs') or {})
    if isinstance(original.get('price_ref'), dict):
        refs['adopted_reference'] = original['price_ref']
    return source_numbers(original.get('evidence_snapshot') or {}, refs)


def validate_position_action_numbers(parsed: dict[str, Any], evidence: dict[str, Any], prices: dict[str, Any]) -> None:
    """Reject old/invented numeric action conditions even if supplied in history."""
    facts = source_numbers(evidence, prices)
    history = historical_numbers({'original': evidence.get('adopted_plan')})
    def validate_explanation_times(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key not in {'actions', 'price_ref', 'evidence_ids', 'reply_to'}:
                    validate_explanation_times(child)
        elif isinstance(value, list):
            for child in value:
                validate_explanation_times(child)
        elif isinstance(value, str):
            without_source_times(value.replace('T+1', ''), facts.stamps | history.stamps)
    validate_explanation_times(parsed)
    allowed = facts.prices
    stamps = facts.stamps
    for action in parsed.get('actions', []):
        for key in ('condition', 'invalidation'):
            prose = without_source_times(action[key].replace('T+1', ''), stamps)
            # Inspect currency before removing source dates: otherwise a model
            # can turn an allowed date into a fabricated price by adding 元.
            currency = re.findall(r'([-+]?\d+(?:\.\d+)?)\s*(?:元|块|角)', prose)
            if any(float(number) not in allowed for number in currency):
                raise ValueError('模型动作的价格单位与当前有源价位不符')
            for suffix, values in ((r'[%％]', facts.percentages), (r'分', facts.scores), (r'家', facts.counts)):
                prose = re.sub(rf'([-+]?\d+(?:\.\d+)?)\s*{suffix}',
                               lambda match: '' if float(match[1]) in values else match[0], prose)
            prose = re.sub(r'评分\s*([-+]?\d+(?:\.\d+)?)(?=\s*(?:[，,、；;。!?！？：:]|$|仅为事实))',
                           lambda match: '' if float(match[1]) in facts.scores else match[0], prose)
            chinese_numbers = '零〇一二两三四五六七八九十百千万'
            chinese_price = (rf'[{chinese_numbers}]+点[{chinese_numbers}]+|[{chinese_numbers}]+\s*(?:元|块|角|股|%|％)|'
                             rf'(?:跌破|回落到|价位|价格|到达|高于|低于|止损(?:在)?|目标价|突破|支撑|阻力)\s*[{chinese_numbers}]+')
            if re.search(chinese_price, prose) or re.search(r'\d+(?:\.\d+)?\s*(?:股|手|[%％]|分|家|点|倍)', prose) or any(
                    float(number) not in allowed for number in re.findall(r'[-+]?\d+(?:\.\d+)?', prose)):
                raise ValueError('模型动作的数字条件只能引用当前有源价位，历史价不能作为新行动条件')
