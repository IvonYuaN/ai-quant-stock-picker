"""Typed frozen research facts; never a current price or a trading threshold."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date, datetime
import re
from typing import Any

from aqsp.web.home_snapshot import technical_basis_from_dict


@dataclass(frozen=True)
class TechnicalEvidence:
    status: str = 'unavailable'
    scope: str = 'frozen_research'
    source: str = ''
    as_of: str = ''
    precision: str = ''
    timestamp_source: str = ''
    research_date: str = ''
    parameter_snapshot: dict[str, Any] | None = None
    facts: tuple[dict[str, Any], ...] = ()
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Expose the same validated facts to UI and model transport."""
        return asdict(self)


def technical_evidence_from_dict(payload: object, *, current: datetime) -> TechnicalEvidence:
    """Re-validate the sanitized, model-facing evidence contract."""
    if not isinstance(payload, dict) or payload.get('status') != 'available':
        return TechnicalEvidence(issues=('经核验的冻结技术依据不可用',))
    required = {'status', 'scope', 'source', 'as_of', 'precision', 'timestamp_source',
                'research_date', 'parameter_snapshot', 'facts', 'issues'}
    if set(payload) != required:
        return TechnicalEvidence(issues=('冻结技术依据字段不完整，不能作为模型事实',))
    try:
        basis = {key: payload[key] for key in ('source', 'as_of', 'precision', 'timestamp_source',
                                                'research_date', 'parameter_snapshot')}
        basis['facts'] = list(payload['facts']) if isinstance(payload['facts'], (list, tuple)) else payload['facts']
        result = technical_evidence({'candidate': {'technical_basis': basis},
                                     'date': payload['research_date'],
                                     'generated_at': current.isoformat()}, current=current)
        if result.status != 'available' or payload['scope'] != 'frozen_research' or payload['issues'] not in ([], ()):
            return TechnicalEvidence(issues=('冻结技术依据重新校验失败',))
        return result
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
        return TechnicalEvidence(issues=('冻结技术依据字段类型无效，不能作为模型事实',))


def technical_evidence(research: dict[str, Any], *, current: datetime) -> TechnicalEvidence:
    """Validate the original basis, not the latest candidate quote timestamp.

    The enclosing research service owns trading-calendar freshness. Here the
    source must belong to that research day and precede its publication/current
    check. No calculation timestamp is invented when upstream did not record it.
    """
    raw = (research.get('candidate') or {}).get('technical_basis')
    if not raw:
        return TechnicalEvidence(issues=('原技术指标来源未记录，不能用最新报价时点代替',))
    try:
        # Bridge dataclasses use tuples before the HTTP JSON boundary. The
        # strict snapshot reader has already validated them; normalize only
        # this container without parsing display strings or altering numbers.
        if isinstance(raw, dict) and isinstance(raw.get('facts'), tuple):
            raw = {**raw, 'facts': list(raw['facts'])}
        basis = technical_basis_from_dict(raw)
        if basis is None:
            raise ValueError('missing basis')
        generated = datetime.fromisoformat(research['generated_at'])
        if (current.tzinfo is None or current.utcoffset() is None
                or generated.tzinfo is None or generated.utcoffset() is None
                or generated > current or basis.research_date != research['date']):
            raise ValueError('invalid original source time')
        if basis.precision == 'day':
            source_day = date.fromisoformat(basis.as_of)
            if source_day.isoformat() != research['date'] or source_day > generated.astimezone(current.tzinfo).date():
                raise ValueError('invalid original bar day')
        else:
            stamp = datetime.fromisoformat(basis.as_of)
            if (stamp.tzinfo is None or stamp.utcoffset() is None or stamp > generated
                    or stamp.astimezone(current.tzinfo).date().isoformat() != research['date']):
                raise ValueError('invalid original bar time')
        payload = asdict(basis)
        if not payload['facts']:
            raise ValueError('missing numeric facts')
        return TechnicalEvidence(status='available', **payload)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return TechnicalEvidence(issues=('冻结技术依据的来源、研究日期、参数身份或数值无效，需重新生成研究',))


def historical_technical_evidence(context: dict[str, Any] | None) -> TechnicalEvidence:
    """Only an identity-checked original snapshot can supply historical facts."""
    if (context or {}).get('status') != 'adopted':
        return TechnicalEvidence()
    original = (context or {}).get('original') or {}
    try:
        saved_at = datetime.fromisoformat(original.get('saved_at') or original.get('adopted_at') or '')
    except (ValueError, TypeError):
        return TechnicalEvidence()
    research = (original.get('evidence_snapshot') or {}).get('research') or {}
    if 'technical_evidence' in research:
        return technical_evidence_from_dict(research['technical_evidence'], current=saved_at)
    return technical_evidence(research, current=saved_at)


def model_technical_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Do not expose display-only or unverified numeric technical claims."""
    def cleaned(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: cleaned(item) for key, item in value.items()
                    if key not in {'technical_metrics', 'technical_basis'}}
        if isinstance(value, list):
            return [cleaned(item) for item in value]
        if isinstance(value, tuple):
            return tuple(cleaned(item) for item in value)
        return deepcopy(value)
    return cleaned(evidence)


def model_technical_context(context: dict[str, Any]) -> dict[str, Any]:
    """Keep adoption comparisons while removing display-only number sources."""
    return model_technical_evidence(context)


# Names and periods are schema identities, not configurable strategy cutoffs.
_NAMES = {
    'ret5_pct': r'(?:ret5_pct|5\s*(?:个交易日|交易日|日)(?:动能|涨跌幅))',
    'ret20_pct': r'(?:ret20_pct|20\s*(?:个交易日|交易日|日)(?:动能|涨跌幅))',
    'volume_ratio': r'(?:volume_ratio|量比)',
    'rsi12': r'(?:rsi12|RSI\s*(?:12|[（(]\s*12\s*[）)]))',
    'macd_hist': r'(?:macd_hist|MACD\s*(?:柱|柱值|hist))',
    'kdj_j': r'(?:kdj_j|KDJ\s*[-－]?\s*J(?:值)?)',
    'bias20_pct': r'(?:bias20_pct|MA\s*20\s*偏离|20\s*日(?:均线偏离|乖离率))',
}
_NUMBER = r'[-+]?\d+(?:\.\d+)?'
_FROZEN = r'(?:冻结(?:研究)?|原研究|研究时|研究快照|研究依据|采用时)'


def mask_technical_facts(text: str, facts: TechnicalEvidence, *, historical: bool = False) -> str:
    """Consume correctly named/typed facts, leaving all other numbers to guards.

    Technical values never join the generic price/percent namespace. A frozen
    marker is required in the same sentence; current/realtime labels and action
    comparators cannot borrow these old observations as current thresholds.
    """
    if facts.status != 'available':
        return text
    for fact in facts.facts:
        name = _NAMES.get(fact['key'])
        if not name:
            continue
        unit = {'percent': r'[%％]', 'ratio': r'(?:倍|[xX×])', 'index': r'(?:点)?'}[fact['unit']]
        pattern = rf'(?<![A-Za-z0-9_.])(?:{name})\s*(?:为|是|:|：|=)?\s*({_NUMBER})\s*{unit}(?![\d.%％元块角股手倍xX×点])'

        def consume(match: re.Match[str]) -> str:
            start = max(text.rfind(sep, 0, match.start()) for sep in ('。', '；', ';', '\n')) + 1
            end_points = [pos for sep in ('。', '；', ';', '\n') if (pos := text.find(sep, match.end())) >= 0]
            clause = text[start:min(end_points) if end_points else len(text)]
            prefix = text[start:match.start()]
            # Keep negative limitations such as “不代表当前放量” readable;
            # an explicit postposed current assertion cannot relabel history.
            assertion = re.sub(r'(?:不|未|不能)(?:代表|证明|说明|等同于|视为|当作|认定为)[^，,。；;]*', '', clause)
            if (not re.search(_FROZEN, clause) or re.search(r'(?:当前|实时|最新|现在|今日实时)', prefix)
                    or re.search(r'(?:当前|实时|最新)(?:的)?\s*(?:量比|指标|RSI|MACD|KDJ|动能|偏离|数值)', assertion, re.IGNORECASE)):
                raise ValueError('冻结技术事实必须说明原研究身份，不能冒充当前指标')
            if historical and not re.search(r'(?:采用时|原研究|原冻结|历史)', clause):
                raise ValueError('历史技术事实只能说明原研究，不能作为当前依据')
            if float(match[1]) != fact['value']:
                raise ValueError('模型技术事实无来源或单位周期不符')
            return '已核验的冻结技术事实'

        text = re.sub(pattern, consume, text, flags=re.IGNORECASE)
    return text


def mask_indicator_names(text: str) -> str:
    """Known identifier digits are names, not an invented numeric observation."""
    text = re.sub(r'RSI\s*(?:12|[（(]\s*12\s*[）)])(?![\d.%％元块角股手倍xX×点])', 'RSI', text, flags=re.IGNORECASE)
    text = re.sub(r'MA\s*20(?![\d.%％元块角股手倍xX×点])', '均线', text, flags=re.IGNORECASE)
    text = re.sub(r'(?:ret5_pct|ret20_pct|volume_ratio|rsi12|macd_hist|kdj_j|bias20_pct)', '指标', text, flags=re.IGNORECASE)
    return re.sub(r'(?:5|20)\s*(?:个交易日|交易日|日)(?=动能|涨跌幅|均线偏离|乖离率)', '', text)


def reject_unmatched_technical_numbers(text: str) -> None:
    """Catch wrong units/periods even when the same number is a valid price."""
    names = r'(?:指标|量比|RSI|MACD|KDJ|MA\s*\d+|动能|涨跌幅|均线偏离|乖离率)'
    if (re.search(rf'{names}[^，,。；;!?！？\n]*{_NUMBER}', text, flags=re.IGNORECASE)
            or re.search(rf'(?:{_NUMBER}|[零〇一二两三四五六七八九十百千万]+)\s*(?:倍|[xX×]|点)', text)):
        raise ValueError('模型技术事实无来源或单位周期不符；数字交易条件不能借用冻结指标')
