"""Current source facts are independent of old alerts and use bounded I/O."""
# ruff: noqa: F811 -- import a reusable isolated API fixture
from __future__ import annotations

from breadth_fixtures import complete_breadth

import asyncio
import fcntl
import json
from pathlib import Path
import sqlite3

import httpx
import pytest

import alert_events as ae
import market
import portfolio as pf
import position_context_source as sector_source
import position_strategy_evidence as source
from position_route_review import current_position_evidence
from test_position_action_plan import HOLDING, NOW, QUOTE, plan_client  # noqa: F401 -- pytest fixture


@pytest.fixture(autouse=True)
def isolated_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(market, '_MARKET_CACHE_DIR', str(tmp_path / 'market'))
    monkeypatch.setattr(market.time, 'time', lambda: NOW.timestamp())
    monkeypatch.setattr(source, 'now_shanghai', lambda: NOW)


def cache(key: str, value: object) -> None:
    market._write_cache(key, value)


def sector(**patch) -> dict:
    return {'industry': '银行', 'name': '银行', 'membership_as_of': NOW.isoformat(),
            'as_of': NOW.isoformat(), 'change_pct': -2, 'source': '合成来源', **patch}


def context(path: Path, **holding) -> dict:
    return source.load_strategy_context({**HOLDING, 'quote': QUOTE, **holding}, ['002129'], path,
                                        checked_at=NOW)


def test_strategy_evidence_uses_current_cache_when_no_alert_exists(tmp_path):
    cache('overview', {'sentiment': complete_breadth(NOW)})
    cache(source._sector_key(['002129']), {'002129': sector()})
    result = context(tmp_path / 'absent.db')
    assert result['market']['status'] == 'weak'
    assert result['sector']['status'] == 'falling'
    assert '银行' in result['sector']['summary']
    assert not (tmp_path / 'absent.db').exists()


def test_strategy_evidence_monitor_projection_matches_read_projection(tmp_path) -> None:
    from decision_context import build_position_context, load_cached_market_overview
    from strategy_direction import direction_identity
    cache('overview', {'sentiment': complete_breadth(NOW)})
    cache(source._sector_key(['002129']), {'002129': sector()})
    cache('strategy_indices', {'000001': {**QUOTE, 'name': '合成上证', 'change_pct': -1}})
    overview = load_cached_market_overview(checked_at=NOW)
    value = sector()
    raw = build_position_context({**HOLDING, 'industry': value['industry']}, [], quote=QUOTE,
                                 market_overview=overview, sector_snapshot=value, checked_at=NOW).to_dict()
    monitor = source.enrich_strategy_context(raw, overview, value, checked_at=NOW)
    read = context(tmp_path / 'absent.db')
    assert direction_identity(monitor) == direction_identity(read)
    assert read['market']['breadth'] == '偏弱' and read['sector']['industry'] == '银行'


def test_strategy_evidence_shared_components_cool_down_across_different_symbol_sets(monkeypatch) -> None:
    calls: list[str] = []
    async def sectors(codes, *args, **kwargs):
        calls.append('sector')
        return {code: sector() for code in codes}
    async def indices(*args, **kwargs):
        calls.append('indices')
        raise ValueError('unavailable')
    async def breadth(*args, **kwargs):
        calls.append('breadth')
        raise ValueError('unavailable')
    monkeypatch.setattr(sector_source, 'fetch_position_sectors', sectors)
    monkeypatch.setattr(source, '_fetch_indices', indices)
    monkeypatch.setattr(source, 'fetch_strategy_breadth', breadth)
    first = asyncio.run(source.refresh_strategy_sources(['002129'], checked_at=NOW))
    second = asyncio.run(source.refresh_strategy_sources(['600000'], checked_at=NOW))
    assert first.state == second.state == 'partial'
    assert calls.count('sector') == 2
    assert calls.count('indices') == calls.count('breadth') == 1
    assert source._cached('strategy_indices', NOW) is None
    assert source._cached('strategy_breadth', NOW) is None


@pytest.mark.parametrize('patch', [{'membership_as_of': None}, {'membership_as_of': '2026-10-22T10:00:00+08:00'},
                                  {'as_of': '2026-10-22T10:00:00+08:00'}])
def test_strategy_evidence_keeps_sector_unknown_when_source_unverified(tmp_path, patch):
    cache(source._sector_key(['002129']), {'002129': sector(**patch)})
    assert context(tmp_path / 'absent.db', industry='猜测行业')['sector']['status'] == 'unknown'


def test_strategy_evidence_preserves_unknown_index_facts_without_inventing_market_stance(tmp_path):
    cache('strategy_indices', {'000001': {**QUOTE, 'name': '合成上证指数', 'change_pct': -2}})
    result = context(tmp_path / 'absent.db')
    evidence = current_position_evidence(result, QUOTE, current=NOW)
    assert evidence['market']['status'] == 'unknown'
    assert '合成上证指数 -2.00%' in evidence['market']['summary']
    assert evidence['market']['indices'][0]['change_pct'] == -2
    assert result['assessment']['t_plan']['eligible'] is None


@pytest.mark.parametrize('patch', [{'as_of': '2026-10-22T10:00:00+08:00'}, {'evidence': 'corrupt'},
                                  {'evidence': [None]}, {'source': ''}])
def test_strategy_evidence_discards_unknown_facts_when_source_or_structure_invalid(patch):
    row = {'status': 'unknown', 'as_of': NOW.isoformat(), 'source': '合成来源', 'evidence': ['合成指数 -2%'], **patch}
    assert current_position_evidence({'market': row}, QUOTE, current=NOW)['market']['evidence'] == []


def history(path: Path, *, mode='raw', fetched_at=None, timestamp_source='received_at') -> None:
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE ohlcv (symbol TEXT, date TEXT, high REAL, low REAL, close REAL, source TEXT, fetched_at TEXT, timestamp_source TEXT, price_mode TEXT, workload TEXT)')
        connection.executemany('INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?,?)', [
            ('002129', day, high, 8, 9, 'synthetic', fetched_at or NOW.isoformat(), timestamp_source, mode, 'historical')
            for day, high in [('2026-10-21', 9.5), ('2026-10-22', 10), ('2026-10-23', 999)]])


def test_strategy_evidence_reads_completed_raw_references_without_modifying_history(tmp_path):
    path = tmp_path / 'history.db'
    history(path)
    before = path.read_bytes()
    result = context(path)
    refs = result['action_basis']['references']
    assert any(row['label'] == '最近完成日高点' and row['price'] == 10 and row['as_of'] == '2026-10-22' for row in refs)
    assert all(row['price'] != 999 for row in refs)
    assert path.read_bytes() == before


@pytest.mark.parametrize('kwargs', [{'mode': 'qfq'}, {'fetched_at': '2026-10-24T00:00:00+08:00'},
                                  {'fetched_at': '2026-10-23T10:00:00'}, {'timestamp_source': ''}])
def test_strategy_evidence_ignores_history_when_provenance_invalid(tmp_path, kwargs):
    path = tmp_path / 'history.db'
    history(path, **kwargs)
    assert not any(row['label'].startswith('最近完成日') for row in context(path)['action_basis']['references'])


def test_strategy_evidence_ignores_history_when_schema_invalid(tmp_path):
    path = tmp_path / 'history.db'
    sqlite3.connect(path).close()
    assert source._history(path, '002129', NOW, source.StrategyEvidenceLimits()) is None


def test_strategy_evidence_refresh_cancels_tasks_before_releasing_lease(monkeypatch):
    active, stopped = set(), []

    async def fetch(codes, limits, completed):
        async def job(name):
            active.add(name)
            try:
                await asyncio.Event().wait()
            finally:
                active.remove(name)
                stopped.append(name)
        await asyncio.gather(job('sector'), job('indices'))

    monkeypatch.setattr(source, '_fetch_sources', fetch)
    result = asyncio.run(source.refresh_strategy_sources(['002129'], checked_at=NOW,
                         limits=source.StrategyEvidenceLimits(total_timeout_seconds=0.01)))
    assert result.state == 'partial' and not active and sorted(stopped) == ['indices', 'sector']
    with (Path(market._MARKET_CACHE_DIR) / '.strategy-evidence.lock').open('a') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_strategy_evidence_refresh_returns_busy_when_other_refresh_owns_lease():
    root = Path(market._MARKET_CACHE_DIR)
    root.mkdir()
    with (root / '.strategy-evidence.lock').open('a') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert asyncio.run(source.refresh_strategy_sources(['002129'], checked_at=NOW)).state == 'busy'


def test_strategy_evidence_refresh_batches_and_cools_failed_sources(monkeypatch):
    calls = []

    async def fetch(codes, limits, completed):
        calls.append(codes)
        completed['strategy_indices'] = {'000001': {**QUOTE, 'change_pct': -2}}
        return ['safe failure']

    monkeypatch.setattr(source, '_fetch_sources', fetch)
    assert asyncio.run(source.refresh_strategy_sources(['600000', '002129', '002129'], checked_at=NOW)).state == 'partial'
    assert asyncio.run(source.refresh_strategy_sources(['002129', '600000'], checked_at=NOW)).state == 'cached'
    assert calls == [('002129', '600000')]
    assert json.loads(Path(market._cache_path('strategy_indices')).read_text())['val']['000001']['change_pct'] == -2


def test_strategy_evidence_async_sector_fetch_uses_two_batches_and_bounds_response():
    calls = []

    def handle(request):
        calls.append(str(request.url))
        rows = [{'f12': '002129', 'f100': '银行', 'f124': int(NOW.timestamp())}] if '/ulist.np/' in str(request.url) else [{'f14': '银行', 'f3': -200, 'f124': int(NOW.timestamp())}]
        return httpx.Response(200, json={'data': {'total': len(rows), 'diff': rows}})

    async def run(limit):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await sector_source.fetch_position_sectors(['002129', '600000'], client, max_response_bytes=limit)

    result = asyncio.run(run(10000))
    assert result['002129']['industry'] == '银行' and len(calls) == 2
    assert result['002129']['change_pct'] == -2
    assert 'data.eastmoney.com/dataapi/bkzj/getbkzj' in calls[1]
    assert 'secids=0.002129%2C1.600000' in calls[0]
    with pytest.raises(ValueError, match='大小预算'):
        asyncio.run(run(10))


def test_strategy_evidence_refresh_retains_other_source_when_one_upstream_fails(monkeypatch):
    real_client = httpx.AsyncClient
    calls = []
    fields = [''] * 53
    for index, value in {1: '合成指数', 3: '3000', 30: '20261023100000', 32: '-2'}.items():
        fields[index] = value
    payload = ('v_sh000001="' + '~'.join(fields) + '";').encode('gbk')

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=payload) if 'qt.gtimg.cn' in str(request.url) else httpx.Response(503, text='secret upstream body')

    monkeypatch.setattr(source.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    result = asyncio.run(source.refresh_strategy_sources(['002129'], checked_at=NOW))
    assert result.state == 'partial' and 'secret' not in result.issue
    assert len(calls) == 3
    assert source._indices(NOW)['indices'][0]['name'] == '合成指数'


def test_strategy_evidence_discards_index_quote_when_source_stamp_expired(tmp_path):
    cache('strategy_indices', {'000001': {**QUOTE, 'quote_time': '20261022150000', 'change_pct': -2}})
    assert context(tmp_path / 'absent.db')['market']['evidence'] == ()


def test_strategy_evidence_get_ignores_old_alert_context_and_never_refreshes(plan_client, monkeypatch):
    client, event = plan_client
    events = ae._load()
    events[0]['position_context']['market'] = {'status': 'weak', 'source': '旧提醒', 'as_of': NOW.isoformat()}
    ae._save(events)
    monkeypatch.setattr(source, '_fetch_sources', lambda *args: pytest.fail('GET must not refresh'))
    result = client.get(f'/api/alerts/{event}/action-plan')
    assert result.status_code == 200
    assert result.json()['data']['route_review']['evidence']['market']['status'] == 'unknown'
    assert not result.json()['data']['basis']['joint_weakness']


def test_strategy_evidence_post_refreshes_shared_sources_without_business_writes(plan_client, monkeypatch):
    client, _ = plan_client
    ae._save([])
    before, events = pf._load(), ae._load()
    pid = before['holdings'][0]['position_id']

    async def fetch(codes, limits, completed):
        from test_strategy_market_source import fact
        completed[source._sector_key(codes)] = {'002129': sector()}
        completed['strategy_indices'] = {'000001': {**QUOTE, 'change_pct': -2}}
        completed['strategy_breadth'] = fact()
        return []

    monkeypatch.setattr(source, '_fetch_sources', fetch)
    result = client.post(f'/api/positions/{pid}/strategy-evidence')
    assert result.status_code == 200, result.text
    data = result.json()['data']
    assert data['strategy_evidence_refresh']['state'] == 'updated'
    assert data['route_review']['evidence']['sector']['status'] == 'falling'
    assert pf._load() == before and ae._load() == events


def test_strategy_evidence_does_not_fetch_when_position_unknown(plan_client, monkeypatch):
    client, _ = plan_client
    monkeypatch.setattr(source, 'refresh_strategy_sources', lambda *args, **kwargs: pytest.fail('unknown ID fetched'))
    assert client.post('/api/positions/guess/strategy-evidence').status_code == 409
    monkeypatch.setattr(pf, '_pf_file', lambda: '/nonexistent/tenant/portfolio.json')
    assert client.post('/api/positions/guess/strategy-evidence').status_code == 409


@pytest.mark.parametrize('mutation', ['add', 'close', 'reenter'])
def test_strategy_evidence_discards_refresh_when_position_changes(plan_client, monkeypatch, mutation):
    client, _ = plan_client
    pid = pf._load()['holdings'][0]['position_id']

    async def refresh(*args, **kwargs):
        if mutation == 'add':
            pf.add_holding('002129', 100, 10, '2026-10-23')
        else:
            ae.close_from_operation('002129', '2026-10-23', 9, 1000, 12, 'refresh-sale')
            if mutation == 'reenter':
                pf.add_holding('002129', 100, 10, '2026-10-23', 'refresh-reenter')
        return source.StrategyRefresh('updated')

    monkeypatch.setattr(source, 'refresh_strategy_sources', refresh)
    result = client.post(f'/api/positions/{pid}/strategy-evidence')
    assert result.status_code == 409 and '持仓已变化' in result.text


def test_strategy_evidence_projects_typed_facts_into_real_review_validator_without_prose_parsing(tmp_path) -> None:
    from strategy_review_context import validate_position_action_numbers
    cache('overview', {'sentiment': complete_breadth(NOW)})
    cache(source._sector_key(['002129']), {'002129': sector(change_pct=1)})
    loaded = context(tmp_path / 'absent.db')
    evidence = current_position_evidence(loaded, {**QUOTE, 'change_pct': 2}, current=NOW)
    assert evidence['market']['breadth_counts'] == {'up': 800, 'down': 3000}
    assert evidence['sector']['industry'] == '银行' and evidence['sector']['change_pct'] == 1
    assert evidence['stock']['change_pct'] == 2
    # Erasing display prose must not remove, or manufacture, numeric authority.
    for row in evidence.values():
        row['summary'], row['evidence'] = '不含数字的合成说明', []
    action = {'actions': [{'condition': '板块上涨1%，个股上涨2%，上涨800家、下跌3000家，仍需人工核对T+1',
                           'invalidation': '来源失效则暂缓'}]}
    validate_position_action_numbers(action, evidence, {})
    action['actions'][0]['condition'] = '板块上涨3%说明可以买入'
    with pytest.raises(ValueError):
        validate_position_action_numbers(action, evidence, {})


def test_strategy_evidence_detects_industry_change_after_real_projection_when_direction_unchanged(tmp_path) -> None:
    from copy import deepcopy
    from strategy_review_context import position_review_context
    cache(source._sector_key(['002129']), {'002129': sector(change_pct=1)})
    original = current_position_evidence(context(tmp_path / 'absent.db'), QUOTE, current=NOW)
    watch = {'choice': 'hold', 'condition': '等待确认', 'invalidation': '行业变化重新核对',
             'position_snapshot': {'position_id': 'p1'}, 'evidence_snapshot': deepcopy(original)}
    worksheet = {'symbol': '002129', 'position_id': 'p1', 'position_snapshot': {'position_id': 'p1'},
                 'review_plan': {'choice': 'hold', 'watch': watch}}
    unchanged = position_review_context(worksheet, original, {})
    assert not unchanged['material_changed']
    cache(source._sector_key(['002129']), {'002129': sector(industry='半导体', name='半导体', change_pct=1)})
    current = current_position_evidence(context(tmp_path / 'absent.db'), QUOTE, current=NOW)
    assert current['sector']['status'] == original['sector']['status'] == 'rising'
    assert current['sector']['source'] == original['sector']['source']
    review = position_review_context(worksheet, current, {})
    changes = [row for row in review['changes'] if row['category'] == 'sector']
    assert review['material_changed'] and len(changes) == 1
    assert changes[0]['before']['industry'] == '银行' and changes[0]['after']['industry'] == '半导体'


def test_strategy_evidence_does_not_promote_invalid_source_numbers_into_review_facts(tmp_path) -> None:
    cache('overview', {'sentiment': {'date': '20261022', 'up': 800, 'down': 3000, 'breadth': '偏弱'}})
    cache(source._sector_key(['002129']), {'002129': sector(as_of='2026-10-22T10:00:00+08:00', change_pct=1)})
    result = current_position_evidence(context(tmp_path / 'absent.db'), QUOTE, current=NOW)
    assert 'breadth_counts' not in result['market']
    assert 'change_pct' not in result['sector'] and 'industry' not in result['sector']
