"""Synthetic actual-fill invariants; no network or production writes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

import alert_events as ae
import app as app_module
import portfolio as pf
import tenant
import trade_journal as tj

CODE = '600000'
DAY = '2026-10-27'


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, 'CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(ae, 'CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(tenant, 'current_tenant', tenant.ContextVar('g4_boundaries', default='local'))
    monkeypatch.setattr(pf, 'today_shanghai', lambda: date(2026, 10, 27))
    monkeypatch.setattr(tj, 'today_shanghai', lambda: date(2026, 10, 27))
    monkeypatch.setattr(pf.risk_rules, 'get_next_trading_day', lambda value: value + timedelta(days=1))
    monkeypatch.setattr(pf.astock, 'tencent_quote', lambda codes: {})
    pf._save({'holdings': [{'code': CODE, 'name': '合成股份', 'shares': 100, 'cost': 12,
                            'opened_at': '2026-10-20', 'position_id': 'p1',
                            'lots': [{'shares': 100, 'opened_at': '2026-10-20'}]}],
              'risk_rules': [], 'closed': []})
    ae._save([])
    return tmp_path


def fill(operation_id: str, direction: str = 'sell', shares: float = 40, fees: float | None = 0) -> tj.TradeInput:
    current = tj.read_journal(CODE)['position']
    return tj.TradeInput(operation_id, direction, CODE, DAY, 10 if direction == 'sell' else 9,
                         shares, fees, current['position_id'], current['revision'])


def pair() -> dict:
    tj.record_trade(fill('sell'))
    return tj.record_trade(fill('buy', 'buy', 20))


def allocation(shares: float = 20, confirm: bool = True) -> list[dict]:
    return [{'sell_receipt_id': 'sell', 'buy_receipt_id': 'buy', 'shares': shares,
             'confirm_same_day_order': confirm, 'confirm_cross_position': False}]


def test_trade_fees_exact_retry_returns_original_response_before_cas(store):
    sold = tj.record_trade(fill('sell', fees=None))
    first = tj.update_fees('sell', 2, sold['revision'], 'fee-1')
    second = tj.update_fees('sell', 3, first['revision'], 'fee-2')
    assert second['receipts'][0]['actual_fees'] == 3
    assert tj.update_fees('sell', 2, sold['revision'], 'fee-1') == first
    assert tj.read_journal(CODE)['receipts'][0]['actual_fees'] == 3
    before = deepcopy(pf._load())
    with pytest.raises(tj.TradeConflictError):
        tj.update_fees('sell', 4, sold['revision'], 'fee-stale')
    assert pf._load() == before
    with pytest.raises(ValueError, match='内容不同'):
        tj.update_fees('sell', 8, sold['revision'], 'fee-1')


def test_trade_fill_same_id_changed_request_is_rejected(store):
    request = fill('sell')
    tj.record_trade(request)
    before = deepcopy(pf._load())
    with pytest.raises(ValueError, match='内容不同'):
        tj.record_trade(replace(request, shares=20))
    assert pf._load() == before


def test_trade_two_concurrent_fills_share_revision_only_one_can_commit(store):
    original = fill('sale-a', shares=20)
    barrier = Barrier(2)
    def attempt(operation_id: str) -> str:
        barrier.wait(timeout=5)
        try:
            tj.record_trade(replace(original, operation_id=operation_id))
            return 'saved'
        except tj.TradeConflictError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(attempt, ['sale-a', 'sale-b']))
    assert sorted(result) == ['conflict', 'saved']
    assert pf._load()['holdings'][0]['shares'] == 80
    assert len(pf._load()['trade_receipts']) == 1


def test_trade_clear_reopen_creates_new_identity_rejects_old_workspace(store):
    old = fill('old-draft', 'buy', 10)
    sold = tj.record_trade(fill('exit', shares=100))
    assert sold['position']['position_id'] is None
    reopened = tj.record_trade(fill('reopen', 'buy', 100))
    assert reopened['position']['position_id'] not in {None, 'p1'}
    before = deepcopy(pf._load())
    with pytest.raises(tj.TradeConflictError):
        tj.record_trade(old)
    assert pf._load() == before
    assert len({receipt['position_id'] for receipt in reopened['receipts']}) == 2


def test_trade_hidden_sale_retry_finishes_interrupted_open_alert(store, monkeypatch):
    ae._save([{'id': 'alert-one', 'symbol': CODE, 'position_id': 'p1', 'status': 'OPEN'}])
    request = replace(fill('exit', shares=100), alert_id='alert-one')
    save = ae._save
    monkeypatch.setattr(ae, '_save', lambda value: (_ for _ in ()).throw(OSError('synthetic alert write failure')))
    with pytest.raises(OSError):
        tj.record_trade(request)
    monkeypatch.setattr(ae, '_save', save)
    pf.remove_closed(0)
    assert pf.get_portfolio()['closed'] == []
    result = tj.record_trade(request)
    assert result['summary']['sold_shares'] == 100
    assert ae._load()[0]['status'] == 'HANDLED'
    assert len(pf._load()['trade_receipts']) == 1
    assert len(pf._load()['closed']) == 1


def test_trade_hidden_legacy_receipt_preserves_alert_crash_recovery(store, monkeypatch):
    ae._save([{'id': 'old-alert', 'symbol': CODE, 'position_id': 'p1', 'status': 'OPEN'}])
    save = ae._save
    monkeypatch.setattr(ae, '_save', lambda value: (_ for _ in ()).throw(OSError('synthetic alert write failure')))
    with pytest.raises(OSError):
        ae.close_from_event('old-alert', DAY, 10, 100)
    monkeypatch.setattr(ae, '_save', save)
    pf.remove_closed(0)
    event = ae._load()[0]
    event['status'] = 'EXPIRED'
    ae._save([event])
    result = ae.close_from_event('old-alert', DAY, 10, 100)
    assert result['event']['status'] == 'HANDLED'
    assert pf._load()['holdings'] == []
    assert len(pf._load()['closed']) == 1


def test_trade_unknown_tenant_does_not_copy_or_reveal_owner_receipts(store):
    sold = tj.record_trade(fill('owner-sale'))
    owner_path = pf._pf_file()
    token = tenant.current_tenant.set('isolated_other')
    try:
        other = tj.read_journal(CODE)
        assert other['receipts'] == [] and other['links'] == []
        assert other['position']['position_id'] is None
        with pytest.raises(ValueError, match='不存在'):
            tj.update_fees('owner-sale', 1, sold['revision'], 'other-fee')
        assert not (store / 'users' / 'isolated_other' / 'portfolio.json').exists()
    finally:
        tenant.current_tenant.reset(token)
    assert pf._pf_file() == owner_path
    assert tj.read_journal(CODE) == sold


def test_trade_same_day_link_needs_confirmation_without_partial_write(store):
    bought = pair()
    before = deepcopy(pf._load())
    with pytest.raises(ValueError, match='同日'):
        tj.save_link('t-link', allocation(confirm=False), bought['revision'], 'link-refused')
    assert pf._load() == before
    saved = tj.save_link('t-link', allocation(), bought['revision'], 'link-ok')
    assert saved['summary']['net_difference'] == 20


def test_trade_link_edit_retire_preserves_audit_and_exact_retry(store):
    bought = pair()
    first = tj.save_link('t-link', allocation(), bought['revision'], 'link-1')
    edited = tj.save_link('t-link', allocation(10), first['revision'], 'link-2')
    retired = tj.save_link('t-link', [], edited['revision'], 'link-3')
    assert retired['summary']['bought_back_shares'] == 0
    assert retired['summary']['net_difference'] is None
    assert tj.save_link('t-link', allocation(), bought['revision'], 'link-1') == first
    data = pf._load()
    assert len(data['trade_receipts']) == 2
    assert data['trade_links'][0]['allocations'] == []
    audits = [item for item in data['trade_audit'] if item['kind'] == 'link']
    assert len(audits) == 3
    assert audits[-1]['before']['allocations'][0]['shares'] == 10
    assert data['trade_mutations']['link-1']['result'] == first
    with pytest.raises(ValueError, match='内容不同'):
        tj.save_link('t-link', allocation(5), bought['revision'], 'link-1')


def test_trade_fees_links_and_link_retirement_invalidate_position_revision(store):
    bought = pair()
    first = bought['position']['revision']
    fee = tj.update_fees('sell', 2, bought['revision'], 'fee-1')
    assert fee['position']['revision'] != first
    linked = tj.save_link('t-link', allocation(), fee['revision'], 'link-1')
    assert linked['position']['revision'] != fee['position']['revision']
    retired = tj.save_link('t-link', [], linked['revision'], 'link-2')
    assert retired['position']['revision'] != linked['position']['revision']


@pytest.mark.parametrize('legacy_kind', ['add', 'close'])
def test_trade_old_operation_id_cannot_be_reused_by_new_journal(store, legacy_kind):
    if legacy_kind == 'add':
        pf.add_holding(CODE, 10, 12, '2026-10-20', 'old-id')
    else:
        pf.close_position(CODE, DAY, 10, 10, 12, 'old-id')
        pf.remove_closed(0)
    before = deepcopy(pf._load())
    with pytest.raises(ValueError, match='旧成交|不能复用'):
        tj.record_trade(fill('old-id', shares=10))
    assert pf._load() == before


def test_trade_api_returns_conflict_and_honors_original_retry(store):
    with TestClient(app_module.app) as client:
        draft = asdict(fill('api-sale'))
        first = client.post('/api/portfolio/trades', json=draft)
        assert first.status_code == 200
        retry = client.post('/api/portfolio/trades', json=draft)
        assert retry.json() == first.json()
        conflict = client.post('/api/portfolio/trades', json={**draft, 'operation_id': 'api-stale'})
        assert conflict.status_code == 409
        altered = client.post('/api/portfolio/trades', json={**draft, 'shares': 1})
        assert altered.status_code == 400
        read = client.get('/api/portfolio/trades', params={'code': CODE})
        assert read.status_code == 200
        assert len(read.json()['data']['receipts']) == 1


def test_trade_api_fee_and_link_endpoints_have_cas_and_exact_retry(store):
    bought = pair()
    with TestClient(app_module.app) as client:
        fee_body = {'actual_fees': 2, 'expected_revision': bought['revision'], 'operation_id': 'api-fee'}
        fee = client.patch('/api/portfolio/trades/sell/fees', json=fee_body)
        assert fee.status_code == 200
        assert client.patch('/api/portfolio/trades/sell/fees', json=fee_body).json() == fee.json()
        assert client.patch('/api/portfolio/trades/sell/fees', json={**fee_body, 'actual_fees': 3}).status_code == 400
        assert client.patch('/api/portfolio/trades/sell/fees', json={**fee_body, 'operation_id': 'api-fee-stale'}).status_code == 409
        link_body = {'allocations': allocation(), 'expected_revision': fee.json()['data']['revision'], 'operation_id': 'api-link'}
        linked = client.put('/api/portfolio/trade-links/api-t', json=link_body)
        assert linked.status_code == 200
        assert linked.json()['data']['summary']['net_difference'] == 19
        assert client.put('/api/portfolio/trade-links/api-t', json=link_body).json() == linked.json()
        assert client.put('/api/portfolio/trade-links/api-t', json={**link_body, 'operation_id': 'api-link-stale'}).status_code == 409


def test_trade_interrupted_journal_full_sale_recovers_after_monitor_expiry(store, monkeypatch):
    ae._save([{'id': 'alert-one', 'symbol': CODE, 'position_id': 'p1', 'status': 'OPEN'}])
    request = replace(fill('exit', shares=100), alert_id='alert-one')
    save = ae._save
    monkeypatch.setattr(ae, '_save', lambda value: (_ for _ in ()).throw(OSError('synthetic alert write failure')))
    with pytest.raises(OSError):
        tj.record_trade(request)
    monkeypatch.setattr(ae, '_save', save)
    event = ae._load()[0]
    event['status'] = 'EXPIRED'
    ae._save([event])
    pf.remove_closed(0)
    tj.record_trade(request)
    assert ae._load()[0]['status'] == 'HANDLED'
    assert len(pf._load()['trade_receipts']) == 1


def test_trade_local_receipts_are_not_cloned_into_new_browser_tenant(store):
    tj.record_trade(fill('sell'))
    token = tenant.current_tenant.set('new-browser-g4')
    try:
        assert pf.get_portfolio()['holdings'] == []
        assert not (store / 'users' / 'new-browser-g4' / 'portfolio.json').exists()
    finally:
        tenant.current_tenant.reset(token)


def test_trade_api_rejects_boolean_price_fees_and_unconfirmed_string_order(store):
    client = TestClient(app_module.app)
    for key in ('price', 'shares', 'actual_fees'):
        body = asdict(fill('bad-' + key)) | {key: True}
        response = client.post('/api/portfolio/trades', json=body)
        assert response.status_code == 422, response.text
    current = pair()
    response = client.put('/api/portfolio/trade-links/bad', json={
        'operation_id': 'bad-link', 'expected_revision': current['revision'],
        'allocations': [allocation()[0] | {'confirm_same_day_order': 'true'}]})
    assert response.status_code == 422, response.text


def test_trade_cancelled_link_still_advances_position_version(store):
    current = pair()
    original = current['position']['revision']
    linked = tj.save_link('t', allocation(), current['revision'], 'link')
    cancelled = tj.save_link('t', [], linked['revision'], 'cancel')
    assert cancelled['position']['revision'] != original
    assert cancelled['summary']['bought_back_shares'] == 0
