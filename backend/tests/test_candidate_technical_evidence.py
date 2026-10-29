"""Frozen metrics can explain research, never mint action prices/thresholds."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from zoneinfo import ZoneInfo

import pytest

import candidate_discussion as discussion
from candidate_technical_evidence import technical_evidence, technical_evidence_from_dict, model_technical_context, model_technical_evidence
from test_candidate_observation import opinion

NOW = datetime(2026, 10, 27, 10, tzinfo=ZoneInfo('Asia/Shanghai'))


def basis() -> dict:
    return {'source': 'synthetic-bars', 'as_of': '2026-10-27T09:40:00+08:00',
            'timestamp_source': 'bar_time', 'research_date': '2026-10-27', 'precision': 'timestamp',
            'parameter_snapshot': {'version': 'v1', 'effective_from': '2026-10-01', 'fingerprint': 'a' * 64},
            'facts': [{'key': key, 'value': value, 'unit': unit, 'period': period} for key, value, unit, period in (
                ('ret5_pct', 3.5, 'percent', '5_trading_days'),
                ('ret20_pct', -1.2, 'percent', '20_trading_days'),
                ('volume_ratio', 1.42, 'ratio', 'source_defined'),
                ('rsi12', 55.8, 'index', '12_trading_days'),
                ('macd_hist', -0.123, 'index', 'source_defined'),
                ('kdj_j', 65.4, 'index', 'source_defined'),
                ('bias20_pct', 2.1, 'percent', '20_trading_days'))]}


def research() -> dict:
    return {'date': '2026-10-27', 'generated_at': '2026-10-27T09:55:00+08:00',
            'candidate': {'technical_basis': basis(), 'technical_metrics': [{'key': 'stop_loss', 'value': '88.88'}]}}


@pytest.fixture
def value(monkeypatch) -> dict:
    monkeypatch.setattr(discussion, 'now_shanghai', lambda: NOW)
    return {'symbol': '600000', 'entry_blocked': False, 'quote': {'change_pct': 2},
            'evidence': {'research': research(), 'market': {}, 'quote': {}},
            'price_refs': {'low': {'price': 9.8, 'source': 'synthetic', 'as_of': NOW.isoformat()}}}


@pytest.mark.parametrize('text', [
    '冻结研究量比1.42倍，支持研究量能，但不代表当前放量。',
    '冻结研究量比为1.42x；还需核对当前量能。',
    '冻结研究5日动能+3.50%，20日动能-1.20%，短长窗口分歧需核验。',
    '冻结研究RSI12为55.8，MACD柱-0.123，KDJ-J 65.4，不能据此保证趋势。',
    '冻结研究MA20偏离+2.10%，仍需当前确认。',
    '冻结研究ret5_pct 3.50%、volume_ratio 1.42倍、rsi12 55.8，均仅为原研究事实。',
    '冻结研究量比1.42倍，来源时点2026-10-27T09:40:00+08:00。',
    '冻结研究RSI12数值未提供，MA20依据不足，不能据此判断。',
])
def test_candidate_technical_accepts_exact_typed_facts_when_frozen_scope_is_explicit(value, text):
    row = opinion()
    row['summary'] = text
    value['evidence']['stock'] = {'change_pct': 2}
    assert discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())['summary'] == text


@pytest.mark.parametrize('text', [
    '冻结研究量比1.42元', '冻结研究量比1.42%', '冻结研究量比1.42倍元',
    '冻结研究5日动能3.5元', '冻结研究20日动能3.5%', '冻结研究RSI14为55.8',
    '冻结研究MA10偏离2.1%', '冻结研究量比9.8倍', '冻结研究量比1.43倍',
    '量比1.42倍', '当前冻结研究量比1.42倍', '最新量比1.42倍',
    '冻结研究5日动能高于3.5%', '冻结研究放量1.42倍', '现价1.42元',
    '冻结研究量比1.42倍已经是当前量比。',
    '冻结研究volume_ratio 9.8元，仅供研究。',
    '冻结研究ret5_pct 9.8元，仅供研究。',
])
def test_candidate_technical_rejects_wrong_unit_period_value_or_current_claim_when_sources_exist(value, text):
    row = opinion()
    row['summary'] = text
    with pytest.raises(ValueError):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())

@pytest.mark.parametrize('text', ['冻结研究量比1.42倍后复核', '量比高于1.42倍再看',
                                  '冻结研究RSI12为55.8则进入观察', '突破3.5元后复核',
                                  '量比，9.8倍以上时复核', '量比超过两倍后复核', '放量二倍后复核',
                                  '冻结研究指标，9.8倍时复核', '冻结研究RSI，9.8点时复核',
                                  '冻结研究volume_ratio 9.8后复核', '冻结研究ret5_pct 9.8元后复核'])
def test_candidate_technical_rejects_old_metric_thresholds_when_used_in_new_actions(value, text):
    row = opinion()
    row['actions'][0]['condition'] = text
    with pytest.raises(ValueError):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())


@pytest.mark.parametrize('field,raw', [('as_of', '2026-10-28T09:40:00+08:00'),
                                    ('as_of', '2026-10-26T09:40:00+08:00'),
                                    ('as_of', '2026-10-27T09:56:00+08:00'),
                                    ('as_of', '2026-10-27T09:40:00'),
                                    ('research_date', '2026-10-26'),
                                    ('parameter_snapshot', None), ('facts', [])])
def test_candidate_technical_marks_unavailable_when_original_provenance_is_invalid(field, raw):
    report = research()
    report['candidate']['technical_basis'][field] = raw
    result = technical_evidence(report, current=NOW)
    assert result.status == 'unavailable' and not result.facts and result.issues


@pytest.mark.parametrize('raw', [True, '1.42', float('nan'), float('inf'), None])
def test_candidate_technical_rejects_non_numeric_or_nonfinite_fact_when_display_looks_valid(raw):
    report = research()
    report['candidate']['technical_basis']['facts'][2]['value'] = raw
    assert technical_evidence(report, current=NOW).status == 'unavailable'


def test_candidate_technical_preserves_original_time_when_quote_refreshes_and_removes_display_only_facts():
    report = research()
    report['candidate'].update(data_fetched_at=NOW.isoformat(), data_source='new-quote')
    result = technical_evidence(report, current=NOW)
    assert result.status == 'available' and result.as_of == basis()['as_of'] and result.source == 'synthetic-bars'
    report['technical_evidence'] = result.to_dict()
    model = model_technical_evidence({'research': report})
    assert model['research']['technical_evidence']['facts'] == result.to_dict()['facts']
    assert 'technical_basis' not in model['research']['candidate'] and 'technical_metrics' not in model['research']['candidate']
    assert report['candidate']['technical_metrics']  # input remains intact
    del report['candidate']['technical_basis']
    assert technical_evidence(report, current=NOW).status == 'unavailable'


def test_candidate_technical_revalidates_sanitized_model_contract_after_raw_basis_is_removed(value):
    report = research()
    raw = technical_evidence(report, current=NOW).to_dict()
    report['technical_evidence'] = raw
    del report['candidate']['technical_basis']
    parsed = technical_evidence_from_dict(report['technical_evidence'], current=NOW)
    assert parsed.status == 'available' and parsed.facts == technical_evidence(research(), current=NOW).facts
    row = opinion()
    row['summary'] = '冻结研究量比1.42倍，支持研究但不代表当前放量。'
    value['evidence']['research'] = report
    assert discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    raw['scope'] = 'current'
    assert technical_evidence_from_dict(raw, current=NOW).status == 'unavailable'


def test_candidate_technical_keeps_historical_facts_out_of_current_namespace_when_adopted(value):
    original = deepcopy(value['evidence'])
    original['research']['candidate']['technical_basis']['facts'][2]['value'] = 1.21
    value['review_context'] = {'status': 'adopted', 'original': {
        'saved_at': NOW.isoformat(), 'evidence_snapshot': original, 'price_refs': {}}}
    row = opinion()
    row['summary'] = '采用时原研究量比1.21倍，仅用于历史比较。冻结研究量比1.42倍，需复核。'
    assert discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    row['summary'] = '冻结研究量比1.21倍，需复核。'
    with pytest.raises(ValueError):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    row['summary'] = '采用时原研究量比1.21元'
    with pytest.raises(ValueError):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())


def test_candidate_technical_reads_sanitized_original_evidence_when_raw_basis_is_absent(value):
    from candidate_technical_evidence import historical_technical_evidence
    report = research()
    saved = technical_evidence(report, current=NOW)
    report['technical_evidence'] = saved.to_dict()
    del report['candidate']['technical_basis']
    context = {'status': 'adopted', 'original': {
        'saved_at': NOW.isoformat(), 'evidence_snapshot': {'research': report}}}
    assert historical_technical_evidence(context).facts == saved.facts
    context['original']['evidence_snapshot']['research']['technical_evidence']['scope'] = 'current'
    assert historical_technical_evidence(context).status == 'unavailable'


def test_candidate_technical_strips_unverified_values_from_all_change_copies_when_model_payload_is_built():
    candidate = research()['candidate']
    context = {'changes': [{'category': 'candidate', 'before': candidate, 'after': deepcopy(candidate)}],
               'original': {'evidence_snapshot': {'research': {'candidate': candidate}}}}
    cleaned = model_technical_context(context)
    cleaned_evidence = model_technical_evidence({'review_changes': context})
    assert '88.88' not in json.dumps(cleaned) and '88.88' not in json.dumps(cleaned_evidence)
    assert 'technical_basis' not in json.dumps(cleaned)
    assert 'technical_metrics' in json.dumps(context)


def test_candidate_technical_accepts_original_bar_day_without_inventing_midnight():
    report = research()
    report['candidate']['technical_basis'].update(as_of='2026-10-27', precision='day', timestamp_source='bar_date')
    result = technical_evidence(report, current=NOW)
    assert result.status == 'available' and result.as_of == '2026-10-27' and result.precision == 'day'


def test_candidate_technical_accepts_bridge_dataclass_shape_before_http_serialization():
    from dataclasses import asdict
    from aqsp.web.home_snapshot import technical_basis_from_dict
    from strategy_review_context import candidate_research_identity
    report = research()
    report['candidate']['technical_basis'] = asdict(technical_basis_from_dict(basis()))
    report['candidate']['technical_metrics'] = ({'key': 'close', 'value': '10.20'}, {'key': 'volume_ratio', 'value': '1.42x'})
    assert technical_evidence(report, current=NOW).status == 'available'
    candidate = candidate_research_identity(report['candidate'])
    assert candidate['technical_metrics'] == [{'key': 'volume_ratio', 'value': '1.42x'}]
