"""Public sector facts can be reused by symbol without reading tenant state."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import market
import position_strategy_evidence as source


NOW = datetime(2026, 10, 23, 10, 0, tzinfo=ZoneInfo('Asia/Shanghai'))
A, B = '002129', '600000'


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(market, '_MARKET_CACHE_DIR', str(tmp_path / 'market'))
    monkeypatch.setattr(market.time, 'time', lambda: NOW.timestamp())
    monkeypatch.setattr(source, 'now_shanghai', lambda: NOW)


def sector(**patch) -> dict:
    return {'industry': '银行', 'name': '银行', 'membership_as_of': NOW.isoformat(),
            'as_of': NOW.isoformat(), 'change_pct': -2, 'source': '合成公开来源', **patch}


def fake_refresh(monkeypatch, rows: dict) -> list:
    calls = []

    async def fetch(codes, limits, completed):
        calls.append(codes)
        completed[source._sector_key(codes)] = rows
        completed['strategy_indices'] = {'000001': {'as_of': NOW.isoformat(), 'change_pct': -2}}
        completed['strategy_breadth'] = {'up': 800, 'down': 3000, 'flat': 0, 'total': 3800,
            'as_of': NOW.isoformat(), 'latest_source_at': NOW.isoformat(),
            'source': '东方财富全A逐只行情宽度', 'coverage_complete': True, 'breadth': '偏弱'}
        return []

    monkeypatch.setattr(source, '_fetch_sources', fetch)
    return calls


def test_strategy_sector_cache_reuses_single_symbol_when_batch_refreshed(monkeypatch):
    original = sector(as_of=(NOW - timedelta(seconds=12)).isoformat())
    calls = fake_refresh(monkeypatch, {A: original, B: sector()})
    assert asyncio.run(source.refresh_strategy_sources([A, B], checked_at=NOW)).state == 'updated'
    assert source.load_cached_strategy_sectors([A], checked_at=NOW) == {A: original}
    assert source.load_cached_strategy_sectors([B, A], checked_at=NOW) == {A: original, B: sector()}
    assert asyncio.run(source.refresh_strategy_sources([A], checked_at=NOW)).state == 'cached'
    assert calls == [(A, B)]


def test_strategy_sector_cache_refresh_works_when_no_portfolio_exists(tmp_path, monkeypatch):
    calls = fake_refresh(monkeypatch, {A: sector()})
    assert asyncio.run(source.refresh_strategy_sources([A], checked_at=NOW)).state == 'updated'
    assert source.load_cached_strategy_sectors([A], checked_at=NOW)[A] == sector()
    assert calls == [(A,)]
    assert not list(tmp_path.rglob('portfolio.json'))


def test_strategy_sector_cache_keeps_source_stamp_when_quote_advances_during_request(monkeypatch):
    arrived = NOW + timedelta(seconds=3)
    fact = sector(as_of=arrived.isoformat(), membership_as_of=arrived.isoformat())
    fake_refresh(monkeypatch, {A: fact})
    monkeypatch.setattr(market.time, 'time', lambda: arrived.timestamp())
    monkeypatch.setattr(source, 'now_shanghai', lambda: arrived)
    assert asyncio.run(source.refresh_strategy_sources([A], checked_at=NOW)).state == 'updated'
    assert source.load_cached_strategy_sectors([A], checked_at=NOW) == {}
    assert source.load_cached_strategy_sectors([A], checked_at=arrived) == {A: fact}


def test_strategy_sector_cache_does_not_fabricate_missing_symbol_when_batch_partial(monkeypatch):
    fake_refresh(monkeypatch, {A: sector()})
    assert asyncio.run(source.refresh_strategy_sources([A, B], checked_at=NOW)).state == 'partial'
    assert source.load_cached_strategy_sectors([A, B], checked_at=NOW) == {A: sector()}
    assert source.load_cached_strategy_sectors([B], checked_at=NOW) == {}


def test_strategy_sector_cache_preserves_ttl_when_source_refresh_fails(monkeypatch):
    fake_refresh(monkeypatch, {A: sector()})
    asyncio.run(source.refresh_strategy_sources([A], checked_at=NOW))
    path = Path(market._cache_path(source._sector_key([A])))
    before = path.read_bytes()
    expired = NOW + timedelta(seconds=market._TTL)
    monkeypatch.setattr(market.time, 'time', lambda: expired.timestamp())

    async def failed(*args):
        raise TimeoutError

    monkeypatch.setattr(source, '_fetch_sources', failed)
    assert asyncio.run(source.refresh_strategy_sources([A], checked_at=expired)).state == 'partial'
    assert source.load_cached_strategy_sectors([A], checked_at=expired) == {}
    assert path.read_bytes() == before


def test_strategy_sector_cache_cools_failed_batch_when_subset_requested(monkeypatch):
    calls = []

    async def failed(codes, limits, completed):
        calls.append(codes)
        raise TimeoutError

    monkeypatch.setattr(source, '_fetch_sources', failed)
    assert asyncio.run(source.refresh_strategy_sources([A, B], checked_at=NOW)).state == 'partial'
    assert asyncio.run(source.refresh_strategy_sources([A], checked_at=NOW)).state == 'cached'
    assert source.load_cached_strategy_sectors([A, B], checked_at=NOW) == {}
    assert calls == [(A, B)]
    later = NOW + timedelta(seconds=market._NEG_TTL)
    monkeypatch.setattr(market.time, 'time', lambda: later.timestamp())
    assert asyncio.run(source.refresh_strategy_sources([A], checked_at=later)).state == 'partial'
    assert calls == [(A, B), (A,)]


@pytest.mark.parametrize('patch', [
    {'membership_as_of': None}, {'membership_as_of': (NOW + timedelta(seconds=1)).isoformat()},
    {'as_of': '2026-10-22T15:00:00+08:00'}, {'as_of': '2026-10-23'},
    {'as_of': (NOW + timedelta(seconds=1)).isoformat()}, {'source': ''},
    {'change_pct': float('nan')}, {'name': '不匹配行业'},
])
def test_strategy_sector_cache_rejects_unverified_fact_when_source_invalid(patch):
    market._write_cache(source._sector_key([A]), {A: sector(**patch)})
    assert source.load_cached_strategy_sectors([A], checked_at=NOW) == {}


def test_strategy_sector_cache_bounds_disk_reads_when_file_oversized_or_linked(tmp_path):
    limits = source.StrategyEvidenceLimits(max_cache_bytes=32)
    market._write_cache(source._sector_key([A]), {A: sector()})
    assert source.load_cached_strategy_sectors([A], checked_at=NOW, limits=limits) == {}
    target = tmp_path / 'private.json'
    target.write_text(json.dumps({'ts': NOW.timestamp(), 'val': {B: sector()}}))
    Path(market._cache_path(source._sector_key([B]))).symlink_to(target)
    assert source.load_cached_strategy_sectors([B], checked_at=NOW) == {}


@pytest.mark.parametrize('raw', ['null', '[]', '{"ts": [], "val": {}}', '{"ts": true, "val": {}}',
                               '{"ts": ' + '9' * 1000 + ', "val": {}}', 'broken'])
def test_strategy_sector_cache_stays_unknown_when_envelope_invalid(raw):
    path = Path(market._cache_path(source._sector_key([A])))
    path.parent.mkdir(parents=True)
    path.write_text(raw)
    assert source.load_cached_strategy_sectors([A], checked_at=NOW) == {}


def test_strategy_sector_cache_stays_unknown_when_file_encoding_invalid():
    path = Path(market._cache_path(source._sector_key([A])))
    path.parent.mkdir(parents=True)
    path.write_bytes(b'\xff')
    assert source.load_cached_strategy_sectors([A], checked_at=NOW) == {}


def test_strategy_sector_cache_only_exposes_public_fields_when_cache_contains_extra_state(monkeypatch):
    fake_refresh(monkeypatch, {A: sector(tenant='u_private', shares=1000, cost=9, position_id='private')})
    asyncio.run(source.refresh_strategy_sources([A], checked_at=NOW))
    assert source.load_cached_strategy_sectors([A], checked_at=NOW) == {A: sector()}
    stored = json.loads(Path(market._cache_path(source._sector_key([A]))).read_text())['val']
    assert stored == {A: sector()}


def test_strategy_sector_cache_does_not_read_disk_or_network_when_symbol_budget_exceeded(monkeypatch):
    monkeypatch.setattr(source, '_cached', lambda *args, **kwargs: pytest.fail('outside read budget'))
    monkeypatch.setattr(source, '_fetch_sources', lambda *args: pytest.fail('GET must not fetch'))
    assert source.load_cached_strategy_sectors([str(code).zfill(6) for code in range(101)], checked_at=NOW) == {}
    assert source.load_cached_strategy_sectors(['../secret', '１２３４５６'], checked_at=NOW) == {}
