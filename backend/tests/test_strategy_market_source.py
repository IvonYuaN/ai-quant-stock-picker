"""Explicit market recovery keeps provenance and completeness independent of emotion."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

import httpx
import pytest

import decision_context as dc
import market
import position_context_source as industries
import position_strategy_evidence as evidence
from strategy_market_source import BreadthSourceLimits, fetch_strategy_breadth, valid_strategy_breadth
from test_position_action_plan import NOW, QUOTE, HOLDING


@pytest.fixture(autouse=True)
def source_clock(monkeypatch):
    monkeypatch.setattr(evidence, 'now_shanghai', lambda: NOW)


def fact(**patch) -> dict:
    return {'up': 800, 'down': 3000, 'flat': 0, 'total': 3800, 'as_of': NOW.isoformat(),
            'latest_source_at': NOW.isoformat(), 'source': '东方财富全A逐只行情宽度', 'breadth': '偏弱', **patch}


def row(code: str, change: object, **patch) -> dict:
    return {'f12': code, 'f13': 0, 'f3': change, 'f124': int(NOW.timestamp()), **patch}


def fetch_pages(pages: list[dict], *, max_bytes=10000) -> tuple[dict, list[str]]:
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=pages[int(request.url.params['pn']) - 1])
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await fetch_strategy_breadth(client, max_response_bytes=max_bytes,
                                                limits=BreadthSourceLimits(page_size=2, max_pages=3))
    return asyncio.run(run()), calls


def test_strategy_breadth_counts_every_stock_once_when_multiple_pages_and_suspension() -> None:
    value, calls = fetch_pages([{'data': {'total': 3, 'diff': [row('000001', 1), row('000002', -1)]}},
                               {'data': {'total': 3, 'diff': [row('000003', '-', f124=None)]}}])
    assert {key: value[key] for key in ('up', 'down', 'flat', 'total')} == {'up': 1, 'down': 1, 'flat': 0, 'total': 3}
    assert value['as_of'] == value['latest_source_at'] == NOW.isoformat()
    assert valid_strategy_breadth(value, current=NOW)
    assert len(calls) == 2 and 'fid=f12' in calls[0] and 'f124' in calls[0]


@pytest.mark.parametrize('first,second', [
    ({'diff': [row('000001', 1)]}, None),
    ({'total': 0, 'diff': []}, None),
    ({'total': 7, 'diff': [row('000001', 1), row('000002', -1)]}, None),
    ({'total': 3, 'diff': [row('000001', 1)]}, None),
    ({'total': 2, 'diff': [row('000001', 1), row('000001', -1)]}, None),
    ({'total': 1, 'diff': [row('000001', 1, f124=None)]}, None),
    ({'total': 1, 'diff': [row('000001', 'bad')]}, None),
    ({'total': 1, 'diff': [row('000001', 0, f13=True)]}, None),
    ({'total': 1, 'diff': [row('not-code', 0)]}, None),
    ({'total': 3, 'diff': [row('000001', 1), row('000002', -1)]}, {'total': 4, 'diff': [row('000003', 0)]}),
    ({'total': 3, 'diff': [row('000001', 1), row('000002', -1)]}, {'total': 3, 'diff': []}),
    ({'total': 1, 'diff': [row('000001', '-')]}, None),
])
def test_strategy_breadth_rejects_incomplete_or_unverifiable_source_when_page_invalid(first, second) -> None:
    with pytest.raises(ValueError):
        fetch_pages([{'data': first}, {'data': second}])


def test_strategy_breadth_enforces_total_response_budget_when_each_page_is_small() -> None:
    pages = [{'data': {'total': 3, 'diff': [row('000001', 1), row('000002', -1)]}},
             {'data': {'total': 3, 'diff': [row('000003', 0)]}}]
    first_size = len(json.dumps(pages[0], separators=(',', ':')).encode())
    with pytest.raises(ValueError, match='总大小预算'):
        fetch_pages(pages, max_bytes=first_size + 10)


@pytest.mark.parametrize('patch', [
    {'as_of': (NOW - timedelta(days=1)).isoformat()},
    {'latest_source_at': (NOW + timedelta(minutes=1)).isoformat()},
    {'as_of': '2026-10-23'}, {'source': ''}, {'up': True}, {'up': 0, 'down': 0},
    {'total': 1}, {'breadth': '普涨'},
])
def test_strategy_breadth_rejects_invalid_or_stale_fact_when_read_after_fetch(patch) -> None:
    assert not valid_strategy_breadth(fact(**patch), current=NOW)


def test_strategy_breadth_restores_market_without_emotion_or_overview_when_explicit_refresh(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(market, '_MARKET_CACHE_DIR', str(tmp_path / 'market'))
    monkeypatch.setattr(market.time, 'time', lambda: NOW.timestamp())
    before = dc.load_cached_market_overview(checked_at=NOW)
    assert before is None
    async def fetch(codes, limits, completed):
        completed['strategy_breadth'] = fact()
        completed['strategy_indices'] = {'000001': {**QUOTE, 'change_pct': -2}}
        completed[evidence._sector_key(codes)] = {code: {'industry': '银行', 'name': '银行',
            'membership_as_of': NOW.isoformat(), 'as_of': NOW.isoformat(), 'change_pct': -2,
            'source': '合成行业源'} for code in codes}
        return []
    monkeypatch.setattr(evidence, '_fetch_sources', fetch)
    result = asyncio.run(evidence.refresh_strategy_sources(['002129'], checked_at=NOW))
    assert result.state == 'updated'
    assert not (tmp_path / 'market' / 'overview.json').exists()
    current = evidence.load_strategy_context({**HOLDING, 'quote': {**QUOTE, 'change_pct': -2}},
                                           ['002129'], tmp_path / 'absent.db', checked_at=NOW)
    assert current['market']['status'] == 'weak' and current['market']['breadth_counts'] == {'up': 800, 'down': 3000}
    assert current['market']['as_of'] == NOW.isoformat() and current['market']['source'] == fact()['source']
    assert current['sector']['status'] == 'falling'
    monkeypatch.setattr(evidence, '_fetch_sources', lambda *args: pytest.fail('warm read must not fetch'))
    assert asyncio.run(evidence.refresh_strategy_sources(['002129'], checked_at=NOW)).state == 'cached'
    # Valid cache cannot make an expired source usable; failed sources do not
    # renew a plan or borrowing an emotion pool date.
    later = NOW + timedelta(minutes=16)
    assert dc.load_cached_market_overview(checked_at=later) is None


def test_strategy_refresh_uses_completion_clock_when_network_and_cache_writes_advance(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(market, '_MARKET_CACHE_DIR', str(tmp_path / 'market'))
    clock = [NOW]
    monkeypatch.setattr(market.time, 'time', lambda: clock[0].timestamp())
    monkeypatch.setattr(evidence, 'now_shanghai', lambda: clock[0])
    async def fetch(codes, limits, completed):
        clock[0] = NOW + timedelta(seconds=1)
        completed['strategy_breadth'] = fact()
        completed['strategy_indices'] = {'000001': {**QUOTE, 'change_pct': -2}}
        completed[evidence._sector_key(codes)] = {code: {'industry': '银行', 'name': '银行',
            'membership_as_of': NOW.isoformat(), 'as_of': NOW.isoformat(), 'change_pct': -2,
            'source': '合成行业源'} for code in codes}
        return []
    monkeypatch.setattr(evidence, '_fetch_sources', fetch)
    result = asyncio.run(evidence.refresh_strategy_sources(['002129'], checked_at=NOW))
    assert result.state == 'updated'
    assert dc.load_cached_market_overview(checked_at=clock[0])['sentiment']['as_of'] == NOW.isoformat()


@pytest.mark.parametrize('stamp', [(NOW - timedelta(days=1)).isoformat(), (NOW + timedelta(minutes=1)).isoformat()])
def test_strategy_refresh_reports_partial_when_shape_complete_but_source_time_invalid(monkeypatch, tmp_path, stamp) -> None:
    monkeypatch.setattr(market, '_MARKET_CACHE_DIR', str(tmp_path / 'market'))
    monkeypatch.setattr(market.time, 'time', lambda: NOW.timestamp())
    async def fetch(codes, limits, completed):
        completed['strategy_breadth'] = fact(as_of=stamp, latest_source_at=stamp)
        completed['strategy_indices'] = {'000001': {**QUOTE, 'as_of': stamp}}
        completed[evidence._sector_key(codes)] = {code: {'industry': '银行', 'name': '银行',
            'membership_as_of': stamp, 'as_of': stamp, 'change_pct': -2, 'source': '合成行业源'} for code in codes}
        return []
    monkeypatch.setattr(evidence, '_fetch_sources', fetch)
    assert asyncio.run(evidence.refresh_strategy_sources(['002129'], checked_at=NOW)).state == 'partial'
    assert dc.load_cached_market_overview(checked_at=NOW) is None


def test_industry_source_finds_matching_industry_after_first_100_when_second_page_contains_target() -> None:
    calls = []
    def handle(request):
        calls.append(str(request.url))
        if '/ulist.' in request.url.path:
            return httpx.Response(200, json={'data': {'diff': [{'f12': '002129', 'f100': '银行', 'f124': int(NOW.timestamp())}]}})
        rows = [{'f14': f'其他行业{i}', 'f3': 1, 'f124': int(NOW.timestamp())} for i in range(100)] if request.url.params['pn'] == '1' else [{'f14': '银行', 'f3': -1, 'f124': int(NOW.timestamp())}]
        return httpx.Response(200, json={'data': {'total': 101, 'diff': rows}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await industries.fetch_position_sectors(['002129'], client, max_response_bytes=20000)
    value = asyncio.run(run())
    assert value['002129']['industry'] == '银行' and value['002129']['as_of'] == NOW.isoformat()
    assert len(calls) == 3 and 'pn=2' in calls[-1]


@pytest.mark.parametrize('last', [{'data': {'total': 101, 'diff': []}}, {'data': {'total': 101, 'diff': [{'f14': '其他行业0'}]}}])
def test_industry_source_rejects_incomplete_or_duplicate_page_when_target_not_found(last) -> None:
    first = {'data': {'total': 101, 'diff': [{'f14': f'其他行业{i}'} for i in range(100)]}}
    rows: list[dict] = []
    members = [{'f100': '银行'}]
    assert industries._append_industry_page(first, members, rows, industries.PositionContextSourceConfig())
    with pytest.raises(ValueError):
        industries._append_industry_page(last, members, rows, industries.PositionContextSourceConfig())


def test_strategy_breadth_preserves_existing_overview_fields_when_independent_cache_is_valid(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(market, '_MARKET_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(market.time, 'time', lambda: NOW.timestamp())
    prior = {'sentiment': {'date': '2026-10-22'}, 'sectors': [{'name': '历史资金流'}]}
    market._write_cache('overview', deepcopy(prior))
    market._write_cache('strategy_breadth', fact())
    merged = dc.load_cached_market_overview(checked_at=NOW)
    assert merged['sectors'] == prior['sectors'] and merged['sentiment']['as_of'] == NOW.isoformat()
    assert json.loads((Path(tmp_path) / 'overview.json').read_text())['val'] == prior


def test_strategy_refresh_cancels_real_breadth_page_when_total_deadline_expires(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(market, '_MARKET_CACHE_DIR', str(tmp_path / 'market'))
    monkeypatch.setattr(market.time, 'time', lambda: NOW.timestamp())
    real_client = httpx.AsyncClient
    stopped, active = [], set()
    fields = [''] * 53
    for index, value in {1: '合成指数', 3: '3000', 30: '20261023100000', 32: '-2'}.items():
        fields[index] = value
    index_body = ('v_sh000001="' + '~'.join(fields) + '";').encode('gbk')
    async def handle(request):
        if 'qt.gtimg.cn' in str(request.url):
            return httpx.Response(200, content=index_body)
        if '/ulist.' in request.url.path:
            return httpx.Response(200, json={'data': {'diff': [{'f12': '002129', 'f100': '银行', 'f124': int(NOW.timestamp())}]}})
        if request.url.params['fs'] == 'm:90+t:2':
            return httpx.Response(200, json={'data': {'total': 1, 'diff': [{'f14': '银行', 'f3': -2, 'f124': int(NOW.timestamp())}]}})
        if request.url.params['pn'] == '1':
            return httpx.Response(200, json={'data': {'total': 1001, 'diff': [row(f'{i:06d}', 1) for i in range(1000)]}})
        active.add('breadth-second-page')
        try:
            await asyncio.Event().wait()
        finally:
            active.clear()
            stopped.append('breadth-second-page')
    monkeypatch.setattr(evidence.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    result = asyncio.run(evidence.refresh_strategy_sources(['002129'], checked_at=NOW,
                         limits=evidence.StrategyEvidenceLimits(total_timeout_seconds=0.1)))
    assert result.state == 'partial' and stopped == ['breadth-second-page'] and not active
    assert evidence.load_cached_strategy_sectors(['002129'], checked_at=NOW)['002129']['industry'] == '银行'
    assert evidence._indices(NOW)['indices'] and dc.load_cached_market_overview(checked_at=NOW) is None
    # Cancellation has completed before the global source lease becomes free.
    import fcntl
    with (Path(market._MARKET_CACHE_DIR) / '.strategy-evidence.lock').open('a') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
