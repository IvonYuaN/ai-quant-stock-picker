"""Unowned candidate AI -> human adoption -> durable off-page observations."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import httpx
import pytest

import app
import aqsp_bridge
import candidate_discussion as discussion
import candidate_monitor as monitor
import candidate_observation as resources
import decision_context as dc
import live_risk_service
import market
import portfolio as pf
import position_discussion as transport
import tenant
from aqsp.utils.jsonl_io import atomic_write_text

NOW = datetime(2026, 10, 27, 10, tzinfo=ZoneInfo('Asia/Shanghai'))
QUOTE = {'name': '合成候选', 'price': 10.2, 'last_close': 10, 'high': 10.5, 'low': 9.8,
         'change_pct': 2, 'quote_time': '20261027100000', 'source': '合成报价源'}


def snapshot() -> dict:
    return {'data': {'selected_date': '2026-10-27', 'generated_at': '2026-10-27T09:55:00+08:00',
                    'stale_after': '2026-10-28T08:00:00+08:00',
                    'candidates': [{'symbol': '600000', 'display_name': '合成候选', 'score': 75,
                                    'freshness': 'fresh', 'data_source': 'synthetic',
                                    'data_fetched_at': '2026-10-27T09:55:00+08:00', 'data_timestamp_source': 'source_time',
                                    'deterministic_reasons': ['合成量价依据']}],
                    'source': {'status': 'fresh'}, 'debates': [],
                    'recommendation_gate': {'recommendation_allowed': True, 'status': 'ready', 'reasons': []}},
            'meta': {'historical': False, 'stale': False}}


def opinion(peers: dict | None = None, *, blocked: bool = False) -> dict:
    return {'summary': '先核对当前量价证据', 'evidence_ids': ['research'], 'uncertainties': ['板块证据待补'],
            'actions': [] if blocked else [{'choice': 'pullback', 'price_ref': 'low', 'condition': '到价后人工核对是否止跌', 'invalidation': '研究或风险变化后取消'}],
            'reply_to': list(peers or {})[:1], 'challenge': '同行指出的源缺口仍需核对' if peers else ''}


def summary(*, blocked: bool = False) -> dict:
    return {'conclusion': '先等待条件，再人工核对', 'agreements': ['不追价'], 'disagreements': ['确认力度未定'],
            'missing_evidence': ['板块证据待补'], 'focus_choice': 'defer' if blocked else 'pullback', 'focus_reason': '当前依据尚需核验',
            'routes': [{'choice': choice, 'judgment': 'not_now' if blocked and choice != 'defer' else 'investigate',
                        'reason': '结合证据核验', 'evidence_ids': ['research'], 'needs': []} for choice in ('pullback', 'confirmation', 'defer')],
            'actions': [] if blocked else opinion()['actions']}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, 'CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(market, '_MARKET_CACHE_DIR', str(tmp_path / 'market'))
    monkeypatch.setattr(tenant, 'current_tenant', tenant.ContextVar('candidate-test', default='local'))
    for module in (resources, discussion, monitor):
        monkeypatch.setattr(module, 'now_shanghai', lambda: NOW)
    monkeypatch.setattr(dc, 'now_shanghai', lambda: NOW)
    data = snapshot()
    monkeypatch.setattr(aqsp_bridge, 'snapshot_response', lambda: deepcopy(data))
    quote = deepcopy(QUOTE)
    calls = []
    monkeypatch.setattr(resources.astock, 'tencent_quote', lambda codes: {code: deepcopy(quote) for code in codes})
    monkeypatch.setattr(transport, 'resolve_endpoint', lambda cfg: transport.ModelEndpoint('https://example.test/v1/chat/completions', 'private-key', 'fixture-model'))

    async def model(endpoint, payload, timeout, limits, *, system):
        calls.append(payload)
        assert system == discussion.SYSTEM and '未持有' not in transport.SYSTEM
        schema = payload['output_schema']['title']
        return json.dumps(summary(blocked=payload['entry_blocked']) if schema == 'CandidateSummary'
                          else opinion(payload.get('peer_opinions'), blocked=payload['entry_blocked']), ensure_ascii=False)

    monkeypatch.setattr(transport, 'call_model', model)
    return TestClient(app.app), data, quote, calls


def analyze(env) -> tuple[dict, dict]:
    client, _, _, _ = env
    value = client.get('/api/candidate-observations/600000').json()['data']
    response = client.post('/api/candidate-observations/600000/discussion', json={
        'expected_version': value['version'], 'expected_evidence_version': value['evidence_version']})
    assert response.status_code == 200, response.text
    result = response.json()['data']
    assert result['status'] == 'complete', result
    updated = client.get('/api/candidate-observations/600000').json()['data']
    return updated, result


def adoption(value: dict, result: dict, **patch) -> dict:
    return {'expected_version': value['version'], 'expected_evidence_version': result['evidence_version'],
            'discussion_id': result['id'], 'generated_at': result['generated_at'], 'choice': 'pullback',
            'action_index': 0, 'comparator': 'at_or_below', 'operation_id': 'adopt-one', **patch}


def test_candidate_observation_full_flow_when_user_has_no_positions(env):
    client, _, quote, calls = env
    assert client.get('/api/candidate-observations').json()['data']['items'] == []
    value, result = analyze(env)
    assert len(calls) == 7 and len(result['rounds']) == 2
    assert value['ownership'] == {'status': 'unowned', 'position_id': None}
    body = adoption(value, result)
    adopted = client.post('/api/candidate-observations/600000/adopt', json=body)
    assert adopted.status_code == 200, adopted.text
    watch = adopted.json()['data']['watch']
    assert watch['price'] == 9.8 and watch['source_ref']['source'] == '合成报价源'
    assert not (resources.root() / 'portfolio.json').exists()
    assert monitor.check_candidates() == {'checked': 1, 'price_watch': 0, 'needs_review': 0, 'notified': 0}
    quote.update(price=9.8, change_pct=2)
    assert monitor.check_candidates()['price_watch'] == 1
    assert client.get('/api/candidate-observations').json()['data']['items'][0]['observation']['state'] == 'price_watch'
    stopped = client.post('/api/candidate-observations/600000/stop', json={'expected_version': 2, 'operation_id': 'stop-one'})
    assert stopped.status_code == 200
    assert monitor.check_candidates()['checked'] == 0
    assert client.post('/api/candidate-observations/600000/stop', json={'expected_version': 2, 'operation_id': 'stop-one'}).status_code == 200
    assert not (resources.root() / 'portfolio.json').exists()


def test_candidate_adopt_allows_quote_ticks_but_rejects_research_or_direction_change(env):
    client, data, quote, _ = env
    value, result = analyze(env)
    body = adoption(value, result)
    quote.update(price=10.3, quote_time='20261027100100')
    for module in (resources, discussion, monitor):
        setattr(module, 'now_shanghai', lambda: NOW + timedelta(minutes=1))
    assert client.post('/api/candidate-observations/600000/adopt', json=body).status_code == 200
    # An intent cannot be changed by replaying an operation ID with new content.
    assert client.post('/api/candidate-observations/600000/adopt', json=body | {'choice': 'confirmation'}).status_code == 409
    data['data']['candidates'][0]['score'] = 70
    assert client.post('/api/candidate-observations/600000/adopt', json=body | {'operation_id': 'new-op', 'expected_version': 2}).status_code == 409
    assert monitor.check_candidates()['needs_review'] == 1


@pytest.mark.parametrize('patch', [{'discussion_id': 'forged'}, {'generated_at': 'forged'}, {'expected_evidence_version': 'forged'},
                                  {'expected_version': 9}, {'action_index': 999}, {'choice': 'confirmation'}, {'comparator': None}])
def test_candidate_adopt_rejects_invalid_or_stale_binding_when_resource_unchanged(env, patch):
    client, _, _, _ = env
    value, result = analyze(env)
    before = resources.store_path().read_bytes()
    response = client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result, **patch))
    assert response.status_code in {400, 409}, response.text
    assert resources.store_path().read_bytes() == before


@pytest.mark.parametrize('gate', [None, {'recommendation_allowed': False, 'status': 'research_validation_not_ready'}])
def test_candidate_hard_gate_allows_explanation_and_defer_only_when_blocked(env, gate):
    client, data, _, _ = env
    data['data']['recommendation_gate'] = gate
    value, result = analyze(env)
    assert value['entry_blocked'] is True and result['summary']['focus_choice'] == 'defer'
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 409
    response = client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result, choice='defer', action_index=None, comparator=None))
    assert response.status_code == 200, response.text
    assert response.json()['data']['observation']['state'] == 'deferred'


@pytest.mark.parametrize('mutation', ['owned', 'corrupt', 'stale', 'future_source', 'source_missing', 'quote_stale'])
def test_candidate_blocks_model_when_ownership_or_source_invalid(env, mutation):
    client, data, quote, calls = env
    if mutation == 'owned':
        atomic_write_text(resources.root() / 'portfolio.json', json.dumps({'holdings': [{'code': '600000', 'position_id': 'own'}]}))
    elif mutation == 'corrupt':
        atomic_write_text(resources.root() / 'portfolio.json', '{broken')
    elif mutation == 'stale':
        data['data']['stale_after'] = '2026-10-27T09:00:00+08:00'
    elif mutation == 'future_source':
        data['data']['candidates'][0]['data_fetched_at'] = '2026-10-28T09:00:00+08:00'
    elif mutation == 'source_missing':
        data['data']['candidates'][0]['data_source'] = ''
    else:
        quote['quote_time'] = '20261026150000'
    value = client.get('/api/candidate-observations/600000').json()['data']
    assert value['available'] is False
    response = client.post('/api/candidate-observations/600000/discussion', json={'expected_version': 0, 'expected_evidence_version': value['evidence_version']})
    assert response.status_code == 409 and not calls


def test_candidate_rechecks_ownership_before_adopting_when_position_added(env):
    client, _, _, _ = env
    value, result = analyze(env)
    atomic_write_text(resources.root() / 'portfolio.json', json.dumps({'holdings': [{'code': '600000', 'position_id': 'own'}]}))
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 409


def test_candidate_real_app_middleware_isolates_resources_when_tenant_differs(env):
    client, _, _, _ = env
    value, result = analyze(env)
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 200
    other = client.get('/api/candidate-observations', headers={'X-User-Id': 'other-user'})
    assert other.status_code == 200 and other.json()['data']['items'] == []
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result), headers={'X-User-Id': 'other-user'}).status_code == 409
    assert client.get('/api/candidate-observations').json()['data']['items'][0]['watch']['status'] == 'active'


def test_candidate_corrupt_store_fails_closed_when_read_or_model_called(env):
    client, _, _, calls = env
    atomic_write_text(resources.store_path(), '{broken')
    assert client.get('/api/candidate-observations').status_code == 503
    assert client.get('/api/candidate-observations/600000').status_code == 503
    assert not calls


@pytest.mark.parametrize('text', ['价格跌到8.65时可考虑，止损在8.00', '回落到八元五角后考虑', '跌破八点五时放弃'])
def test_candidate_model_rejects_unreferenced_numeric_prices_when_text_not_sourced(env, text):
    value = resources.workspace('600000')
    row = summary()
    row['actions'][0]['condition'] = text
    with pytest.raises(ValueError, match='数字交易条件'):
        discussion.parse(json.dumps(row), discussion.CandidateSummary, value, set())


def test_candidate_model_rejects_unsupported_refs_or_gate_override_when_output_forged(env):
    value = resources.workspace('600000')
    row = summary()
    row['actions'][0]['price_ref'] = 'unsupported'
    with pytest.raises(ValueError, match='没有来源'):
        discussion.parse(json.dumps(row), discussion.CandidateSummary, value, set())
    with pytest.raises(ValueError, match='门禁'):
        discussion.parse(json.dumps(summary()), discussion.CandidateSummary, value | {'entry_blocked': True}, set())


def test_candidate_model_failure_is_recoverable_when_all_roles_fail(env, monkeypatch):
    client, _, _, _ = env
    async def fail(*args, **kwargs):
        raise transport.ModelHTTPError(429)
    monkeypatch.setattr(transport, 'call_model', fail)
    value = resources.workspace('600000')
    response = client.post('/api/candidate-observations/600000/discussion', json={'expected_version': 0, 'expected_evidence_version': value['evidence_version']})
    assert response.status_code == 200
    assert response.json()['data']['status'] == 'failed'
    latest = resources.workspace('600000')
    assert latest['analysis']['status'] == 'failed' and latest['version'] == 1
    assert len(latest['discussion']['rounds']) == 1


def test_candidate_model_commit_rejects_during_research_change(env, monkeypatch):
    client, data, _, _ = env
    original = transport.call_model
    async def changing(*args, **kwargs):
        raw = await original(*args, **kwargs)
        data['data']['candidates'][0]['score'] = 70
        return raw
    monkeypatch.setattr(transport, 'call_model', changing)
    value = resources.workspace('600000')
    response = client.post('/api/candidate-observations/600000/discussion', json={'expected_version': 0, 'expected_evidence_version': value['evidence_version']})
    assert response.status_code == 409
    assert resources.workspace('600000')['discussion'] is None


def test_candidate_monitor_finds_zero_position_tenant_when_only_candidate_resource_exists(env, monkeypatch):
    client, _, _, _ = env
    value, result = analyze(env)
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 200
    monkeypatch.setattr(live_risk_service, '_refresh_shared_strategy_sources',
                        lambda codes: ({}, {'state': 'skipped'}))
    summary = live_risk_service.check_existing_tenants(notify=False)
    assert summary['candidate_checks'] == 1 and summary['tenants_checked'] == 1 and summary['failed_tenants'] == 0
    assert not (resources.root() / 'portfolio.json').exists()


def test_candidate_monitor_notification_deduplicates_across_checks_when_price_reached(env, monkeypatch):
    client, _, quote, _ = env
    value, result = analyze(env)
    client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result))
    quote.update(price=9.8)
    import aqsp.notification_runtime as notify
    sent = []
    def dispatch(markdown, **kwargs):
        sent.append(markdown)
        notify.mark_notification_sent(kind=kwargs['kind'], markdown=markdown, state_path=kwargs['state_path'])
        return [SimpleNamespace(ok=True)]
    monkeypatch.setattr(notify, 'dispatch_notification_once', dispatch)
    assert monitor.check_candidates(notify=True)['notified'] == 1
    assert monitor.check_candidates(notify=True)['notified'] == 0
    quote.update(price=9.7)
    assert monitor.check_candidates(notify=True)['notified'] == 0
    assert len(sent) == 1 and '核验' in sent[0]


def test_candidate_monitor_holds_uncertain_send_when_success_receipt_missing(env, monkeypatch):
    client, _, quote, _ = env
    value, result = analyze(env)
    client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result))
    quote.update(price=9.8)
    import aqsp.notification_runtime as notify
    sent = []
    monkeypatch.setattr(notify, 'dispatch_notification_once', lambda *args, **kwargs: sent.append(args) or [SimpleNamespace(ok=True)])
    assert monitor.check_candidates(notify=True)['notified'] == 0
    assert monitor.check_candidates(notify=True)['notified'] == 0
    assert len(sent) == 1


def test_candidate_monitor_revalidates_stop_before_delivery_when_plan_changed(env, monkeypatch):
    client, _, quote, _ = env
    value, result = analyze(env)
    client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result))
    quote.update(price=9.8)
    original = monitor.deliver
    def stop_then_deliver(batch):
        resources.stop('600000', {'expected_version': 2, 'operation_id': 'before-send-stop'})
        return original(batch)
    monkeypatch.setattr(monitor, 'deliver', stop_then_deliver)
    assert monitor.check_candidates(notify=True)['notified'] == 0


def test_candidate_resource_budget_reclaims_stopped_resources_when_new_candidate_added():
    data = {'schema_version': 1, 'resources': {f'{i:06d}': {'symbol': f'{i:06d}', 'version': 1, 'watch': {'status': 'stopped'}} for i in range(100)}}
    assert resources.new_row(data, '600000')['symbol'] == '600000'
    assert len(data['resources']) == 100


def test_candidate_stream_disconnect_cancels_network_and_persists_recovery(env, monkeypatch):
    stopped = []
    async def blocked(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(True)
    monkeypatch.setattr(transport, 'call_model', blocked)
    async def run():
        value = resources.workspace('600000')
        stream = discussion.stream_discussion('600000', 0, value['evidence_version'])
        assert 'preparing' in await anext(stream)
        await anext(stream)
        await stream.aclose()
    asyncio.run(run())
    assert stopped and resources.workspace('600000')['analysis']['status'] == 'cancelled'


def test_candidate_transport_uses_specific_system_and_keeps_position_default_when_not_overridden(monkeypatch):
    real_client, seen = httpx.AsyncClient, []
    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]})
    monkeypatch.setattr(transport.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    async def run():
        endpoint = transport.ModelEndpoint('https://example.test/v1/chat/completions', 'key', 'model')
        await transport.call_model(endpoint, {}, 1, transport.DiscussionLimits(), system=discussion.SYSTEM)
        await transport.call_model(endpoint, {}, 1, transport.DiscussionLimits())
    asyncio.run(run())
    assert seen[0]['messages'][0]['content'] == discussion.SYSTEM
    assert seen[1]['messages'][0]['content'] == transport.SYSTEM


def test_candidate_recovers_orphaned_running_state_when_model_lease_is_free(env):
    client, _, _, _ = env
    data = {'schema_version': 1, 'resources': {'600000': {'symbol': '600000', 'version': 1,
            'analysis': {'status': 'running', 'stage': 'challenge', 'run_id': 'old-run'}, 'watch': None, 'discussion': None}}}
    resources.save_store(data)
    detail = client.get('/api/candidate-observations/600000').json()['data']
    assert detail['analysis']['status'] == 'cancelled' and detail['analysis']['stage'] == 'interrupted'
    assert client.get('/api/candidate-observations').json()['data']['items'][0]['analysis']['status'] == 'cancelled'


def test_candidate_preserves_running_state_when_real_model_lease_is_owned(env):
    import fcntl
    client, _, _, _ = env
    data = {'schema_version': 1, 'resources': {'600000': {'symbol': '600000', 'version': 1,
            'analysis': {'status': 'running', 'stage': 'challenge', 'run_id': 'live-run'}, 'watch': None, 'discussion': None}}}
    resources.save_store(data)
    with resources.discussion_lease_path().open('a') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert client.get('/api/candidate-observations/600000').json()['data']['analysis']['status'] == 'running'


def test_candidate_rejects_adoption_when_original_model_quote_has_expired(env, monkeypatch):
    client, _, quote, _ = env
    value, result = analyze(env)
    later = NOW + timedelta(hours=1)
    monkeypatch.setattr(resources, 'now_shanghai', lambda: later)
    quote['quote_time'] = '20261027110000'
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 409


def test_candidate_stale_snapshot_debug_override_cannot_approve_new_observation(env, monkeypatch):
    client, data, _, _ = env
    monkeypatch.setenv('AQSP_ALLOW_STALE_SNAPSHOT', '1')
    data['data']['stale_after'] = '2026-10-27T09:00:00+08:00'
    value = client.get('/api/candidate-observations/600000').json()['data']
    assert not value['available']


def test_candidate_monitor_never_sends_when_default_notify_false(env, monkeypatch):
    client, _, quote, _ = env
    value, result = analyze(env)
    client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result))
    quote['price'] = 9.8
    monkeypatch.setattr(monitor, 'deliver', lambda *args: pytest.fail('default monitor must not send'))
    assert monitor.check_candidates()['price_watch'] == 1


def test_candidate_adopt_rechecks_research_at_commit_when_replaced_after_quote_read(env, monkeypatch):
    client, data, _, _ = env
    value, result = analyze(env)
    original = resources.workspace
    def changed(*args, **kwargs):
        captured = original(*args, **kwargs)
        data['data']['candidates'][0]['score'] = 70
        return captured
    monkeypatch.setattr(resources, 'workspace', changed)
    response = client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result))
    assert response.status_code == 409
    assert resources.load_store()['resources']['600000']['watch'] is None


def test_candidate_stream_returns_progress_and_result_when_completed(env):
    client, _, _, _ = env
    value = resources.workspace('600000')
    response = client.post('/api/candidate-observations/600000/discussion/stream', json={
        'expected_version': 0, 'expected_evidence_version': value['evidence_version']})
    assert response.status_code == 200
    records = [json.loads(line) for line in response.text.splitlines()]
    assert records[0] == {'event': 'progress', 'stage': 'preparing'}
    assert records[-1]['event'] == 'result' and records[-1]['data']['status'] == 'complete'


def test_candidate_monitor_recovers_durable_success_after_post_send_write_failed(env, monkeypatch):
    client, _, quote, _ = env
    value, result = analyze(env)
    client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result))
    quote['price'] = 9.8
    import aqsp.notification_runtime as notify
    original = monitor.atomic_write_text
    fail_after_sent = []
    sends = []
    def write(path, text):
        if path.name == 'candidate-observation-delivery.json' and fail_after_sent:
            raise OSError('synthetic disk error after dispatch')
        return original(path, text)
    def send(markdown, **kwargs):
        sends.append(markdown)
        notify.mark_notification_sent(kind=kwargs['kind'], markdown=markdown, state_path=kwargs['state_path'])
        fail_after_sent.append(True)
        return [SimpleNamespace(ok=True)]
    monkeypatch.setattr(notify, 'dispatch_notification_once', send)
    monkeypatch.setattr(monitor, 'atomic_write_text', write)
    with pytest.raises(OSError):
        monitor.check_candidates(notify=True)
    monkeypatch.setattr(monitor, 'atomic_write_text', original)
    assert monitor.check_candidates(notify=True)['notified'] == 0
    assert len(sends) == 1


@pytest.mark.parametrize('field,value', [('generated_at', '2026-10-28T09:00:00+08:00'),
                                       ('selected_date', '2026-10-28')])
def test_candidate_rejects_future_research_when_current_label_is_forged(env, field, value):
    client, data, _, _ = env
    data['data'][field] = value
    assert client.get('/api/candidate-observations/600000').json()['data']['available'] is False


def test_candidate_rejects_old_provenance_when_fresh_label_is_forged(env):
    client, data, _, _ = env
    data['data']['candidates'][0]['data_fetched_at'] = '2026-10-20T09:00:00+08:00'
    assert client.get('/api/candidate-observations/600000').json()['data']['available'] is False


def test_candidate_preserves_source_failure_gate_when_recommendation_flag_is_true(env):
    client, data, _, _ = env
    data['data']['source']['status'] = '盘中任务失败：cursor未推进'
    value, result = analyze(env)
    assert value['entry_blocked'] and result['summary']['focus_choice'] == 'defer'


@pytest.mark.parametrize('text', ['当前上涨2.00%，有源评分75分；这些事实不证明可以买入。', '600000当前有源报价10.2，仍需核对T+1规则和风险。', '缺乏大盘板块确认时，预设突破点的观察意义有限。'])
def test_candidate_model_accepts_sourced_descriptive_facts_when_actions_use_refs(env, text):
    value = resources.workspace('600000')
    row = opinion()
    row['summary'] = text
    raw = '```json\n' + json.dumps(row) + '\n```'
    assert discussion.parse(raw, discussion.CandidateOpinion, value, set())['summary'] == text


def test_candidate_model_checks_fact_provenance_without_rejecting_sourced_action_prices(env):
    value = resources.workspace('600000')
    row = opinion()
    row['summary'] = '预计上涨19.5%，适合买入'
    with pytest.raises(ValueError, match='无来源'):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    row = opinion()
    row['actions'][0]['condition'] = '回落至9.8元后核对'
    assert discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())['actions'][0]['price_ref'] == 'low'


@pytest.mark.parametrize('text', ['回落至75元后核对', '回落至75后核对', '回落至2元后核对'])
def test_candidate_model_rejects_score_and_change_as_action_prices_when_numbers_have_sources(env, text):
    value = resources.workspace('600000')
    row = opinion()
    row['actions'][0]['condition'] = text
    with pytest.raises(ValueError, match='有源价格'):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())


def test_candidate_model_rejects_wrong_fact_unit_when_number_is_sourced_score(env):
    value = resources.workspace('600000')
    row = opinion()
    row['summary'] = '当前75元，预计涨75%'
    with pytest.raises(ValueError, match='单位'):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())


@pytest.mark.parametrize('coverage_note', ['', '本轮无新研究；保留原依据与讨论'])
def test_candidate_keeps_adopted_price_and_notification_identity_when_writer_only_refreshes_times(env, monkeypatch, coverage_note):
    client, data, quote, _ = env
    value, result = analyze(env)
    original_evidence = deepcopy(result['evidence_snapshot'])
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 200
    quote.update(price=9.8, low=9.6, change_pct=1)
    import aqsp.notification_runtime as notify
    sent = []
    def dispatch(markdown, **kwargs):
        sent.append(markdown)
        notify.mark_notification_sent(kind=kwargs['kind'], markdown=markdown, state_path=kwargs['state_path'])
        return [SimpleNamespace(ok=True)]
    monkeypatch.setattr(notify, 'dispatch_notification_once', dispatch)
    for minute in (1, 2):
        later = NOW + timedelta(minutes=minute)
        monkeypatch.setattr(resources, 'now_shanghai', lambda: later)
        monkeypatch.setattr(monitor, 'now_shanghai', lambda: later)
        quote['quote_time'] = f'20261027100{minute}00'
        data['data']['generated_at'] = later.isoformat()
        data['data']['stale_after'] = (later + timedelta(hours=8)).isoformat()
        data['data']['candidates'][0]['data_fetched_at'] = later.isoformat()
        if coverage_note:
            data['data']['candidates'][0]['coverage_note'] = coverage_note if minute == 1 else ''
        checked = monitor.check_candidates(notify=True)
        assert checked['price_watch'] == 1 and checked['needs_review'] == 0
        current = resources.workspace('600000')
        assert current['evidence_version'] == value['evidence_version']
        assert current['watch']['price'] == 9.8 and current['watch']['source_ref'] == result['price_refs']['low']
        assert current['discussion']['evidence_snapshot'] == original_evidence
    assert len(sent) == 1


def test_candidate_keeps_adopted_watch_when_real_writer_omits_and_restores_candidate(env, tmp_path):
    from dataclasses import replace
    from aqsp.web.home_snapshot import (
        HomeDashboardSnapshot, HomeSnapshotCandidate, HomeSnapshotColdstart,
        HomeSnapshotSource, HomeSnapshotRecommendationGate,
        write_home_dashboard_snapshot, load_home_dashboard_snapshot,
    )

    client, data, quote, _ = env
    raw = data['data']['candidates'][0]
    original = HomeSnapshotCandidate(
        symbol='600000', display_name='合成候选', score=75,
        research_status='优先复核', next_step='原观察条件', context='原研究依据',
        deterministic_reasons=('合成量价依据',), freshness='fresh', data_source='synthetic',
        data_fetched_at=raw['data_fetched_at'], data_timestamp_source='source_time',
    )
    first = HomeDashboardSnapshot(
        schema_version='v1', generated_at=data['data']['generated_at'],
        selected_date='2026-10-27', available_dates=('2026-10-27',), candidates=(original,),
        summaries=(), source=HomeSnapshotSource('synthetic', '2026-10-27', 0, 'fresh'),
        coldstart=HomeSnapshotColdstart('ready', ''), stale_after=data['data']['stale_after'],
        recommendation_gate=HomeSnapshotRecommendationGate(True, 'ready', ()),
    )
    path = tmp_path / 'writer-home.json'
    write_home_dashboard_snapshot(path, first)
    data['data'] = load_home_dashboard_snapshot(path).to_dict()
    value, result = analyze(env)
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 200
    for candidates in ((), (), (original,)):
        write_home_dashboard_snapshot(path, replace(first, candidates=candidates))
        data['data'] = load_home_dashboard_snapshot(path).to_dict()
        current = resources.workspace('600000')
        assert current['evidence_version'] == value['evidence_version']
        assert current['observation']['state'] == 'waiting'
        assert current['watch']['price'] == result['price_refs']['low']['price']


def test_candidate_rejects_new_entry_adoption_when_only_original_research_is_preserved(env):
    client, data, _, _ = env
    value, result = analyze(env)
    data['data']['candidates'][0]['coverage_note'] = '本轮无新研究'
    response = client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result))
    assert response.status_code == 409
    assert '本轮无新研究' in response.text
    assert resources.workspace('600000')['watch'] is None


def test_candidate_coverage_only_allows_defer_analysis_and_preserves_existing_watch(env):
    client, data, _, calls = env
    value, original = analyze(env)
    adopted = client.post('/api/candidate-observations/600000/adopt', json=adoption(value, original)).json()['data']
    data['data']['candidates'][0]['coverage_note'] = '本轮无新研究；保留原依据'
    current = client.get('/api/candidate-observations/600000').json()['data']
    assert current['entry_blocked'] is False
    assert current['new_entry_blocked'] is True
    assert current['watch']['status'] == 'active'
    # Current discussion receives a separate entry restriction while the old
    # adopted watch remains valid because the substantive evidence is unchanged.
    result = client.post('/api/candidate-observations/600000/discussion', json={
        'expected_version': current['version'], 'expected_evidence_version': current['evidence_version']})
    assert result.status_code == 200, result.text
    discussion_result = result.json()['data']
    assert discussion_result['summary']['focus_choice'] == 'defer'
    assert all(route['judgment'] != 'investigate' for route in discussion_result['summary']['routes'] if route['choice'] != 'defer')
    assert all(action['choice'] == 'defer' for action in discussion_result['summary']['actions'])
    assert len(calls) == 14
    # The refreshed resource retains the exact existing watch; no coverage-only
    # transition manufactures a thesis change or consumes its price notification.
    latest = client.get('/api/candidate-observations/600000').json()['data']
    assert latest['watch'] == adopted['watch']
    assert latest['observation']['state'] == 'waiting'


@pytest.mark.parametrize('invalidation_kind', ['candidate_change', 'research_status'])
def test_candidate_explicit_invalidation_blocks_new_entry_and_pauses_existing_watch(env, invalidation_kind):
    client, data, _, _ = env
    value, original = analyze(env)
    adopted = client.post('/api/candidate-observations/600000/adopt', json=adoption(value, original)).json()['data']
    if invalidation_kind == 'candidate_change':
        data['data']['candidate_changes'] = [{'symbol': '600000', 'display_name': '合成候选', 'status': 'invalidated'}]
    else:
        data['data']['candidates'][0]['research_status'] = '日内已失效'
    current = client.get('/api/candidate-observations/600000').json()['data']
    assert current['new_entry_blocked'] is True
    assert '明确失效' in current['new_entry_block_reason']
    assert current['observation']['state'] == 'needs_review'
    assert current['observation']['reason'] == 'research_changed'
    result = client.post('/api/candidate-observations/600000/discussion', json={
        'expected_version': current['version'], 'expected_evidence_version': current['evidence_version']})
    assert result.status_code == 200, result.text
    discussion_result = result.json()['data']
    assert discussion_result['summary']['focus_choice'] == 'defer'
    assert all(route['judgment'] != 'investigate' for route in discussion_result['summary']['routes'] if route['choice'] != 'defer')
    latest = client.get('/api/candidate-observations/600000').json()['data']
    response = client.post('/api/candidate-observations/600000/adopt', json=adoption(
        latest, discussion_result, operation_id='adopt-invalidated'))
    assert response.status_code == 409
    assert '明确失效' in response.text
    assert client.get('/api/candidate-observations/600000').json()['data']['watch'] == adopted['watch']


@pytest.mark.parametrize('change,label', [
    ('score', '个股评分'), ('strategy', '个股评分'), ('technical_price', '个股评分'),
    ('news', '个股评分'), ('gate', '风险门禁'), ('parameters', '策略参数'),
    ('date', '研究日期'), ('source', '研究来源'), ('quote_source', '行情来源'),
    ('direction', '方向'), ('debate', '研究讨论'),
])
def test_candidate_pauses_and_explains_substantive_changes_when_watch_adopted(env, change, label):
    client, data, quote, _ = env
    value, result = analyze(env)
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 200
    candidate = data['data']['candidates'][0]
    if change == 'score':
        candidate['score'] = 74
    elif change == 'strategy':
        candidate['strategies'] = ['changed-strategy']
    elif change == 'technical_price':
        candidate['technical_metrics'] = [{'key': 'support', 'label': '有源参考价', 'value': '9.7'}]
    elif change == 'news':
        candidate['news_catalyst_summary'] = '新的风险公告'
    elif change == 'gate':
        data['data']['recommendation_gate']['recommendation_allowed'] = False
    elif change == 'parameters':
        data['data']['parameter_snapshot'] = {'version': 'next', 'effective_from': '2026-10-26', 'fingerprint': 'a' * 64}
    elif change == 'date':
        data['data']['selected_date'] = '2026-10-26'
    elif change == 'source':
        data['data']['source']['effective'] = 'new-research-source'
    elif change == 'quote_source':
        quote['source'] = '新的报价源'
    elif change == 'direction':
        quote['change_pct'] = -1
    else:
        data['data']['debates'] = [{'symbol': '600000', 'conclusion': '研究结论改变'}]
    assert monitor.check_candidates()['needs_review'] == 1
    current = resources.workspace('600000')
    assert current['observation']['state'] == 'needs_review'
    assert label in current['observation']['detail']
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result, expected_version=2, operation_id='after-change')).status_code == 409


@pytest.mark.parametrize('change', ['expired', 'future_generated', 'future_source', 'old_quote', 'absent'])
def test_candidate_never_uses_stable_identity_to_bypass_source_validity_when_refresh_invalid(env, change):
    client, data, quote, _ = env
    value, result = analyze(env)
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 200
    if change == 'expired':
        data['data']['stale_after'] = NOW.isoformat()
    elif change == 'future_generated':
        data['data']['generated_at'] = (NOW + timedelta(minutes=1)).isoformat()
    elif change == 'future_source':
        data['data']['candidates'][0]['data_fetched_at'] = (NOW + timedelta(minutes=1)).isoformat()
    elif change == 'old_quote':
        quote['quote_time'] = '20261026150000'
    else:
        data['data']['candidates'] = []
    assert monitor.check_candidates()['needs_review'] == 1
    assert resources.workspace('600000')['observation']['state'] == 'evidence_unavailable'


def test_candidate_commits_model_and_adoption_when_publication_time_only_changes(env, monkeypatch):
    client, data, _, _ = env
    original = transport.call_model
    async def refreshing(*args, **kwargs):
        raw = await original(*args, **kwargs)
        data['data']['generated_at'] = '2026-10-27T09:59:00+08:00'
        data['data']['candidates'][0]['data_fetched_at'] = '2026-10-27T09:59:00+08:00'
        return raw
    monkeypatch.setattr(transport, 'call_model', refreshing)
    value, result = analyze(env)
    assert result['evidence_snapshot']['research']['generated_at'] == '2026-10-27T09:55:00+08:00'
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 200


def test_candidate_legacy_watch_requires_one_review_without_rewriting_frozen_intent(env):
    client, _, _, _ = env
    value, result = analyze(env)
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 200
    data = resources.load_store()
    old = data['resources']['600000']['watch']
    old.pop('evidence_basis')
    old['evidence_version'] = 'legacy-digest'
    resources.save_store(data)
    current = resources.workspace('600000')
    assert current['watch'] == old
    assert current['observation']['state'] == 'needs_review'
    assert '旧版依据' in current['observation']['detail']


def test_candidate_defer_stays_quiet_on_refresh_but_reviews_new_evidence(env):
    client, data, _, _ = env
    value, result = analyze(env)
    response = client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result, choice='defer', action_index=None, comparator=None))
    assert response.status_code == 200
    data['data']['generated_at'] = '2026-10-27T09:59:00+08:00'
    assert monitor.check_candidates()['needs_review'] == 0
    assert resources.workspace('600000')['observation']['state'] == 'deferred'
    data['data']['candidates'][0]['deterministic_reasons'].append('新的有源依据')
    assert monitor.check_candidates()['needs_review'] == 1


def test_candidate_review_preserves_adoption_snapshot_when_quote_only_refreshes(env) -> None:
    client, _, quote, calls = env
    value, result = analyze(env)
    adopted = client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).json()['data']
    frozen = deepcopy(adopted['watch'])
    assert frozen['evidence_snapshot']['quote']['price'] == QUOTE['price']
    quote['price'] = 10.3
    _, reviewed = analyze(env)
    context = reviewed['review_context']
    assert context['status'] == 'adopted' and context['original']['snapshot_complete']
    assert context['original']['choice'] == 'pullback'
    assert context['original']['condition'] == frozen['condition']
    assert context['original']['invalidation'] == frozen['invalidation']
    assert context['quote_updated'] and not context['material_changed'] and context['changes'] == []
    assert json.loads(json.dumps(calls[-1]['review_context'])) == context
    assert 'adopted_plan' in calls[-1]['evidence'] and 'review_changes' in calls[-1]['evidence']
    assert client.get('/api/candidate-observations/600000').json()['data']['watch'] == frozen
    assert 'tenant' not in json.dumps(context)


def test_candidate_review_compares_original_evidence_and_keeps_old_prices_out_of_actions(env) -> None:
    client, data, quote, _ = env
    value, result = analyze(env)
    frozen = client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).json()['data']['watch']
    data['data']['candidates'][0]['score'] = 80
    quote.update(price=10.7, low=9.1, high=11, last_close=10.3)
    _, reviewed = analyze(env)
    context = reviewed['review_context']
    assert context['original']['evidence_snapshot']['research']['candidate']['score'] == 75
    assert context['current']['evidence_snapshot']['research']['candidate']['score'] == 80
    assert any(row['category'] == 'candidate' for row in context['changes'])
    value = resources.workspace('600000')
    value['review_context'] = context
    row = opinion()
    row['summary'] = '采用时低点9.8元，评分75分；现在评分80分，原价仅作历史对比。'
    assert discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    row['actions'][0]['condition'] = '跌破9.8元后重新核对'
    with pytest.raises(ValueError, match='有源价格'):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    row['actions'][0]['condition'] = '等有源参考价后核对'
    row['summary'] = '采用时低点123.45元'
    with pytest.raises(ValueError, match='无来源|单位'):
        discussion.parse(json.dumps(row), discussion.CandidateOpinion, value, set())
    assert resources.workspace('600000')['watch'] == frozen


def test_candidate_review_marks_missing_original_evidence_without_using_last_workspace(env) -> None:
    client, _, _, _ = env
    value, result = analyze(env)
    client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result))
    data = resources.load_store()
    watch = data['resources']['600000']['watch']
    for key in ('evidence_snapshot', 'price_refs', 'evidence_identity'):
        watch.pop(key)
    resources.save_store(data)
    _, reviewed = analyze(env)
    original = reviewed['review_context']['original']
    assert original['condition'] == watch['condition']
    assert original['evidence_snapshot'] == {} and not original['snapshot_complete']
    assert reviewed['review_context']['missing_evidence']


def test_candidate_review_refuses_foreign_symbol_snapshot_when_stored_watch_identity_differs(env) -> None:
    client, _, _, _ = env
    value, result = analyze(env)
    client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result))
    data = resources.load_store()
    data['resources']['600000']['watch']['evidence_identity']['symbol'] = '000001'
    resources.save_store(data)
    _, reviewed = analyze(env)
    assert reviewed['review_context']['status'] == 'identity_mismatch'
    assert reviewed['review_context']['original'] is None
    assert reviewed['review_context']['missing_evidence']


def test_candidate_review_does_not_read_another_tenant_adoption(env) -> None:
    client, _, _, calls = env
    value, result = analyze(env)
    client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result))
    headers = {'X-User-Id': 'review-other-user'}
    other = client.get('/api/candidate-observations/600000', headers=headers).json()['data']
    response = client.post('/api/candidate-observations/600000/discussion', headers=headers, json={
        'expected_version': other['version'], 'expected_evidence_version': other['evidence_version']})
    assert response.status_code == 200, response.text
    assert response.json()['data']['review_context']['status'] == 'not_adopted'
    assert calls[-1]['review_context']['original'] is None
    assert client.get('/api/candidate-observations/600000').json()['data']['watch']['status'] == 'active'


def test_candidate_review_rejects_stale_commit_when_adopted_watch_stops_during_model(env, monkeypatch) -> None:
    client, _, _, _ = env
    value, result = analyze(env)
    adopted = client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).json()['data']
    original = transport.call_model
    async def changed(endpoint, payload, timeout, limits, *, system):
        if 'rounds' in payload:
            resources.stop('600000', {'expected_version': adopted['version'] + 1, 'operation_id': 'stop-during-review'})
        return await original(endpoint, payload, timeout, limits, system=system)
    monkeypatch.setattr(transport, 'call_model', changed)
    response = client.post('/api/candidate-observations/600000/discussion', json={
        'expected_version': adopted['version'], 'expected_evidence_version': adopted['evidence_version']})
    assert response.status_code == 409
    stored = resources.load_store()['resources']['600000']
    assert stored['watch']['status'] == 'stopped' and stored['discussion']['id'] == result['id']


@pytest.mark.parametrize('state', ['failed', 'partial_failed', 'unknown'])
def test_candidate_blocks_entry_when_run_failed_even_with_fresh_quotes(env, state):
    client, data, _, _ = env
    data['data']['source'].update(status='fresh', task_id='intraday', run_status=state)
    value, result = analyze(env)
    assert value['entry_blocked'] and result['summary']['focus_choice'] == 'defer'


def test_candidate_preserves_adopted_watch_when_only_run_provenance_changes(env):
    client, data, _, _ = env
    value, result = analyze(env)
    original_evidence = deepcopy(result['evidence_snapshot'])
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 200
    for task, state, detail in (
        ('intraday', 'running', '正在刷新'),
        ('intraday', 'completed', '研究已完成'),
        ('main_chain', '', ''),
    ):
        data['data']['source'].update(task_id=task, run_status=state,
                                      run_updated_at=NOW.isoformat(), run_detail=detail)
        current = resources.workspace('600000')
        assert current['evidence_version'] == value['evidence_version']
        assert current['observation']['state'] == 'waiting'
        assert current['watch']['price'] == result['price_refs']['low']['price']
        assert current['discussion']['evidence_snapshot'] == original_evidence


def test_candidate_requires_review_when_run_availability_changes_but_not_failure_wording(env):
    client, data, _, _ = env
    value, result = analyze(env)
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 200
    data['data']['source'].update(task_id='intraday', run_status='failed',
                                  run_updated_at=NOW.isoformat(), run_detail='研究失败')
    original_gate = deepcopy(data['data']['recommendation_gate'])
    data['data']['recommendation_gate'] = {'recommendation_allowed': False,
                                          'status': 'research_run_not_ready', 'reasons': ['研究失败']}
    failed = resources.workspace('600000')
    assert failed['evidence_version'] != value['evidence_version']
    assert failed['observation']['state'] == 'needs_review' and failed['entry_blocked']
    data['data']['source'].update(run_status='partial_failed', run_detail='补充失败详情')
    data['data']['recommendation_gate']['reasons'] = ['补充失败详情']
    assert resources.workspace('600000')['evidence_version'] == failed['evidence_version']
    data['data']['source'].update(run_status='completed', run_detail='恢复成功')
    data['data']['recommendation_gate'] = original_gate
    assert resources.workspace('600000')['evidence_version'] == value['evidence_version']
