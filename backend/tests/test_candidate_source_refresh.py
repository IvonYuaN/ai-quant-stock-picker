"""Candidate source recovery is explicit, shared, bounded and plan independent."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json

import pytest

from test_candidate_observation import NOW, analyze, adoption, opinion, env as env
import candidate_discussion as discussion
import candidate_observation as resources
import market
import position_strategy_evidence as sources


@pytest.fixture
def refresh(env, monkeypatch):
    monkeypatch.setattr(market.time, 'time', lambda: NOW.timestamp())
    calls = []
    async def fetch(codes, limits, completed):
        calls.append(codes)
        completed[sources._sector_key(codes)] = {code: {
            'industry': '银行', 'name': '银行', 'membership_as_of': NOW.isoformat(),
            'as_of': NOW.isoformat(), 'change_pct': 1, 'source': '合成板块源',
        } for code in codes}
        completed['strategy_indices'] = {}
        return []
    monkeypatch.setattr(sources, '_fetch_sources', fetch)
    return env, calls


def test_candidate_source_refresh_recovers_sector_without_creating_position_or_watch(refresh):
    (client, _, _, model_calls), calls = refresh
    before = client.get('/api/candidate-observations/600000').json()['data']
    assert before['evidence']['sector']['status'] == 'unknown' and calls == []
    response = client.post('/api/candidate-observations/600000/strategy-evidence', json={})
    assert response.status_code == 200, response.text
    value = response.json()['data']
    assert value['evidence']['sector']['status'] == 'rising'
    assert value['evidence']['sector']['as_of'] == NOW.isoformat()
    assert value['source_refresh']['state'] == 'partial'  # No index fact invented.
    assert calls == [('600000',)] and not model_calls
    assert not resources.store_path().exists() and not (resources.root() / 'portfolio.json').exists()
    assert client.get('/api/candidate-observations/600000').json()['data']['evidence']['sector']['status'] == 'rising'
    client.post('/api/candidate-observations/600000/strategy-evidence', json={})
    assert calls == [('600000',)]  # Explicit retries share the negative cooldown.


def test_candidate_source_read_reuses_position_batch_without_network_or_tenant_state(refresh):
    (client, _, _, _), calls = refresh
    asyncio.run(sources.refresh_strategy_sources(['600000', '600001'], checked_at=NOW))
    assert client.get('/api/candidate-observations/600000').json()['data']['evidence']['sector']['status'] == 'rising'
    assert calls == [('600000', '600001')]
    assert not (resources.root() / 'portfolio.json').exists()


def test_candidate_source_refresh_preserves_existing_intent_and_requests_review_when_direction_recovers(refresh):
    (client, _, _, _), _ = refresh
    value, result = analyze(refresh[0])
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 200
    before = deepcopy(resources.load_store())
    response = client.post('/api/candidate-observations/600000/strategy-evidence', json={})
    assert response.status_code == 200
    value = response.json()['data']
    assert value['observation']['state'] == 'needs_review'
    assert resources.load_store() == before


def test_candidate_source_refresh_stays_unknown_when_network_fails(refresh, monkeypatch):
    (client, _, _, _), _ = refresh
    async def failed(*args):
        raise TimeoutError
    monkeypatch.setattr(sources, '_fetch_sources', failed)
    value = client.post('/api/candidate-observations/600000/strategy-evidence', json={}).json()['data']
    assert value['source_refresh']['state'] == 'partial'
    assert value['evidence']['sector']['status'] == 'unknown'
    assert value['evidence']['market']['status'] == 'unknown'


def test_candidate_source_refresh_rejects_unknown_candidate_before_network(refresh):
    (client, _, _, _), calls = refresh
    assert client.post('/api/candidate-observations/600001/strategy-evidence', json={}).status_code == 409
    assert calls == []


def test_candidate_model_can_describe_refreshed_sector_fact_without_using_it_as_price(refresh):
    (client, _, _, _), _ = refresh
    value = client.post('/api/candidate-observations/600000/strategy-evidence', json={}).json()['data']
    row = opinion()
    row['summary'] = '板块当前上涨1%，个股上涨2%，仍需核对风险。'
    assert discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())['summary'] == row['summary']
    row['actions'][0]['invalidation'] = '板块由+1%转负时重新核对，评分75分不代表买入许可'
    assert discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    for text in ('板块当前1元', '板块预计涨75%'):
        row['summary'] = text
        with pytest.raises(ValueError, match='单位'):
            discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    row = opinion()
    row['actions'][0]['condition'] = '回落到1元后核对'
    with pytest.raises(ValueError, match='有源价格'):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())


def test_candidate_model_describes_sourced_index_and_breadth_facts_without_inventing_units(refresh, monkeypatch):
    (client, _, _, _), _ = refresh
    monkeypatch.setattr(sources, '_indices', lambda current: {
        'status': 'unknown', 'summary': '仅指数背景', 'as_of': NOW.isoformat(), 'source': '合成指数源',
        'indices': [{'change_pct': -0.5}],
    })
    value = client.get('/api/candidate-observations/600000').json()['data']
    row = opinion()
    row['summary'] = '指数当前-0.5%，不等于市场整体转弱。'
    assert discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    monkeypatch.setattr(resources.dc, 'load_cached_market_overview', lambda **kwargs: {
        'sentiment': {'as_of': NOW.isoformat(), 'up': 3000, 'down': 1800, 'breadth': '偏强'},
    })
    value = client.get('/api/candidate-observations/600000').json()['data']
    row['summary'] = '上涨3000家、下跌1800家，仍需核对板块。'
    assert discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    row['actions'][0]['condition'] = '研究时点2026-10-27 10:00，参考上涨3000、下跌1800；评分75仅为事实，报价回落到9.8再核对。'
    assert discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    for text in ('上涨5000时再核对', '上涨3000点后复核', '评分75倍后复核'):
        row['actions'][0]['condition'] = text
        with pytest.raises(ValueError):
            discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    row['actions'] = opinion()['actions']
    row['summary'] = '上涨3000%，当前3000元'
    with pytest.raises(ValueError, match='单位'):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    for text in ('评分3000分', '目标价3000', '支撑位3000', '下跌75家', '持有3000股'):
        row['summary'] = text
        with pytest.raises(ValueError, match='单位'):
            discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())


def test_candidate_model_source_dates_do_not_license_invented_numbers(refresh):
    (client, _, _, _), _ = refresh
    value = client.get('/api/candidate-observations/600000').json()['data']
    row = opinion()
    row['summary'] = '研究日期2026-10-27，数据时间2026-10-27T09:55:00+08:00；现价10.2，评分75分。'
    assert discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    for text in ('目标价2026-10-27元', '评分2026-10-27分', '研究日期2026-10-29'):
        row['summary'] = text
        with pytest.raises(ValueError):
            discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
