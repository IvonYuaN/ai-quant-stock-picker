"""A quote's daily change is distinct from a frozen multi-day indicator."""
from __future__ import annotations

import json

import pytest

import candidate_discussion as discussion
from test_candidate_observation import env, opinion  # noqa: F401

# ruff: noqa: F811 -- fixture


@pytest.mark.parametrize('text', ['当前报价10.2、当日涨跌幅+2.00%，仍需人工核验。',
                                 '本交易日涨跌幅为2%，不是止跌证明。'])
@pytest.mark.parametrize('field', ['summary', 'condition'])
def test_candidate_quote_claims_accept_exact_daily_fact_without_confusing_technical_period(env, text, field):
    value = env[0].get('/api/candidate-observations/600000').json()['data']
    row = opinion()
    if field == 'summary':
        row[field] = text
    else:
        row['actions'][0][field] = text
    parsed = discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    assert (parsed[field] if field == 'summary' else parsed['actions'][0][field]) == text


@pytest.mark.parametrize('text', ['当日涨跌幅3.5%', '当日涨跌幅2元', '当日涨跌幅二%',
                                 '5日涨跌幅2%', '5日涨幅3.5%', '冻结研究当日涨跌幅2%',
                                 '当日涨跌幅达到2%后买入', '当日涨跌幅2%元'])
def test_candidate_quote_claims_reject_unknown_wrong_period_or_wrong_unit(env, text):
    value = env[0].get('/api/candidate-observations/600000').json()['data']
    row = opinion()
    row['summary'] = text
    with pytest.raises(ValueError):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())


@pytest.mark.parametrize('text', ['板块当日涨跌幅2%', '行业当日涨跌幅2%', '指数当日涨跌幅2%',
                                 '市场当日涨跌幅2%', '当日涨跌幅2%（采用时）', '冻结当日涨跌幅2%',
                                 '板块偏强，当日涨跌幅2%', '当日涨跌幅2%是历史记录'])
def test_candidate_quote_claims_preserve_subject_and_historical_scope(env, text):
    value = env[0].get('/api/candidate-observations/600000').json()['data']
    value['evidence']['sector']['change_pct'] = 3
    row = opinion()
    row['summary'] = text
    with pytest.raises(ValueError):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())


def test_candidate_quote_claims_do_not_borrow_another_evidence_percentage(env):
    value = env[0].get('/api/candidate-observations/600000').json()['data']
    value['quote'].pop('change_pct', None)
    value['evidence']['sector']['change_pct'] = 2
    row = opinion()
    row['summary'] = '当日涨跌幅2%'
    with pytest.raises(ValueError):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
