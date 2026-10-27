"""Exact source times are facts; partial prefixes and numeric units are not."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

import candidate_discussion as candidate
import position_discussion as position
from model_source_time import without_source_times
from strategy_review_context import validate_position_action_numbers
from test_position_discussion import OPINION

STAMP = '2026-10-27T10:00:00+08:00'
OLD = '2026-10-26T09:00:00+08:00'
REFS = {'current': {'price': 9.1, 'label': '当前参考价', 'source': '合成源', 'as_of': STAMP}}
EVIDENCE = {'quote': {'source': '合成源', 'as_of': STAMP}, 'adopted_plan': {
    'price_ref': {'price': 9.8, 'source': '原合成源', 'as_of': OLD}}}


@pytest.mark.parametrize('token', [
    '2026-10-27', '2026-10-27T10:00', '2026-10-27 10:00',
    '2026-10-27T10:00:00', '2026-10-27 10:00:00', STAMP,
    '2026-10-27T10:00+08:00', '2026-10-27 10:00:00+08:00', '10:00', '10:00:00',
])
def test_model_time_accepts_exact_source_spellings_when_unambiguous(token: str) -> None:
    assert without_source_times(f'来源{token}；核对', [STAMP]) == '来源；核对'
    row = deepcopy(OPINION)
    row['evidence_ids'] = ['quote']
    row['actions'][0]['condition'] = f'依据{token}有源价9.1元再核对'
    validate_position_action_numbers(row, EVIDENCE, REFS)


@pytest.mark.parametrize('token', [
    '2026-10-28', '2026-10-27T10:01', '2026-10-27T10:00:01', '11:00',
    '2026-10-27T10:00:009', '2026-10-27T10:00:00+08:001',
    '2026-10-27T10:00Z', '2026-10-27T02:00:00+00:00',
    '2026-10-27T10:00unknown', '2026-10-27unknown', '10:00:bad', '10:00.5',
    '2026-10-2', '2026-1-27', '1:00', '10:0',
])
def test_model_time_rejects_wrong_or_partial_token_when_unknown(token: str) -> None:
    with pytest.raises(ValueError):
        without_source_times(token, [STAMP])


@pytest.mark.parametrize('unit', list('元块角股手%％分家点倍'))
@pytest.mark.parametrize('token', ['2026-10-27', '2026-10-27T10:00', '10:00'])
def test_model_time_rejects_units_when_source_time_looks_like_price(token: str, unit: str) -> None:
    with pytest.raises(ValueError):
        without_source_times(f'{token} {unit}', [STAMP])


def test_model_time_rejects_bare_clock_when_multiple_source_dates_match() -> None:
    with pytest.raises(ValueError):
        without_source_times('10:00', [STAMP, '2026-10-26T10:00:00+08:00'])
    assert without_source_times('09:00', [STAMP, OLD]) == ''


def test_model_time_keeps_unknown_numbers_when_no_date_or_clock_present() -> None:
    assert without_source_times('报价123.45元', [STAMP]) == '报价123.45元'


def test_model_time_defines_zero_offset_normalization_without_other_zone_inference() -> None:
    assert without_source_times('2026-10-27T10:00:00+00:00', ['2026-10-27T10:00:00Z']) == ''
    assert without_source_times('2026-10-27 10:00', ['2026-10-27T10:00+08:00']) == ''
    with pytest.raises(ValueError):
        without_source_times('2026-10-27T02:00:00+00:00', [STAMP])


@pytest.mark.parametrize('token', ['2026-10-27T10:00 GMT', '2026-10-27T10:00 UTC+8',
                                  '2026-10-27T10:00 CST', '2026-10-27T10:00 +08:00'])
def test_model_time_rejects_unrecognized_timezone_suffix(token: str) -> None:
    with pytest.raises(ValueError):
        without_source_times(token, [STAMP])


def test_position_time_allows_original_in_explanation_but_not_current_action() -> None:
    row = deepcopy(OPINION)
    row['evidence_ids'] = ['quote']
    row['summary'] = '原依据2026-10-26T09:00；现在重新核对'
    row['actions'][0].update(price_ref='current', condition='依据2026-10-27T10:00当前价9.1再核对')
    position._parse(json.dumps(row), position.RoleOpinion, EVIDENCE, REFS, set(), {'pause_t': False, 'sellable_shares': 1000})
    row['actions'][0]['condition'] = '依据2026-10-26T09:00再行动'
    with pytest.raises(ValueError):
        validate_position_action_numbers(row, EVIDENCE, REFS)


def _capture() -> dict:
    return json.loads((Path(__file__).parent / 'fixtures/model_source_time_capture.json').read_text())


def test_candidate_time_replays_captured_real_role_when_minute_iso_is_sourced() -> None:
    capture = _capture()
    parsed = candidate.parse(capture['raw_role'], candidate.CandidateOpinion, capture['value'], set())
    assert '2026-10-27T10:00' in parsed['summary']
    assert parsed['actions'][0]['choice'] == 'defer'


@pytest.mark.parametrize('text', ['2026-10-28T10:00', '2026-10-27T10:01', '2026-10-27T10:00元', '2026-10-27T10:00abc'])
def test_candidate_time_rejects_unknown_even_when_component_numbers_equal_prices(text: str) -> None:
    capture = _capture()
    value = capture['value']
    row = json.loads(capture['raw_role'])
    row['summary'] = text
    # All date/time components happen to be price facts; time validation still rejects.
    for number in (2026, 10, 27, 28, 0, 1):
        value['price_refs'][f'collision{number}'] = {**REFS['current'], 'price': number}
    with pytest.raises(ValueError):
        candidate.parse(json.dumps(row), candidate.CandidateOpinion, value, set())


def test_candidate_time_preserves_original_explanation_and_current_action_boundary() -> None:
    capture = _capture()
    value = capture['value']
    value['review_context']['original']['price_ref'] = {**REFS['current'], 'as_of': OLD}
    row = json.loads(capture['raw_role'])
    row['summary'] = '原依据2026-10-26T09:00，现在需核对'
    candidate.parse(json.dumps(row), candidate.CandidateOpinion, value, set())
    row['actions'][0]['condition'] = '依据2026-10-26T09:00再行动'
    with pytest.raises(ValueError):
        candidate.parse(json.dumps(row), candidate.CandidateOpinion, value, set())


@pytest.mark.parametrize('token', ['2026-10-28T10:00', '2026-10-27T10:01', '2026-10-27T10:00元',
                                  '2026-10-27T10:00xyz', '2026-10-26T09:00'])
def test_position_time_cannot_use_price_collisions_to_authorize_unknown_action_time(token: str) -> None:
    row = deepcopy(OPINION)
    row['actions'][0]['condition'] = token
    refs = {str(number): {**REFS['current'], 'price': number} for number in (2026, 10, 27, 28, 0, 1, 26, 9)}
    with pytest.raises(ValueError):
        validate_position_action_numbers(row, EVIDENCE, refs)


def test_candidate_time_rejects_ambiguous_history_clock_in_explanation() -> None:
    capture = _capture()
    value = capture['value']
    value['review_context']['original']['price_ref'] = {**REFS['current'], 'as_of': '2026-10-26T10:00:00+08:00'}
    row = json.loads(capture['raw_role'])
    row['summary'] = '来源10:00，仍需复核'
    with pytest.raises(ValueError):
        candidate.parse(json.dumps(row), candidate.CandidateOpinion, value, set())


@pytest.mark.parametrize('kind,stamp', [('stock', '2026-10-27T10:00:08+08:00'),
                                      ('index', '2026-10-27T10:00:05+08:00')])
def test_model_time_accepts_typed_subsource_clock_when_independent_of_parent(kind: str, stamp: str) -> None:
    evidence = deepcopy(EVIDENCE)
    evidence['stock'] = {'source': '个股源', 'as_of': '2026-10-27T10:00:08+08:00'}
    evidence['market'] = {'source': '指数源', 'as_of': STAMP,
                          'indices': [{'name': '上证指数', 'as_of': '2026-10-27T10:00:05+08:00'}]}
    row = deepcopy(OPINION)
    row['actions'][0]['condition'] = f'依据{stamp}核对当前参考价9.1元'
    validate_position_action_numbers(row, evidence, REFS)
    # A subsource clock requires its typed source owner; arbitrary prose cannot
    # authorize it, and original evidence still cannot authorize a current action.
    evidence['stock' if kind == 'stock' else 'market']['source'] = ''
    with pytest.raises(ValueError):
        validate_position_action_numbers(row, evidence, REFS)
