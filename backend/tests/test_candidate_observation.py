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


def test_candidate_monitor_finds_zero_position_tenant_when_only_candidate_resource_exists(env):
    client, _, _, _ = env
    value, result = analyze(env)
    assert client.post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 200
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


def test_candidate_keeps_adopted_price_and_notification_identity_when_writer_only_refreshes_times(env, monkeypatch):
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
        checked = monitor.check_candidates(notify=True)
        assert checked['price_watch'] == 1 and checked['needs_review'] == 0
        current = resources.workspace('600000')
        assert current['evidence_version'] == value['evidence_version']
        assert current['watch']['price'] == 9.8 and current['watch']['source_ref'] == result['price_refs']['low']
        assert current['discussion']['evidence_snapshot'] == original_evidence
    assert len(sent) == 1


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
