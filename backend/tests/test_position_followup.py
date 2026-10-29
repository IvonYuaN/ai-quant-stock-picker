"""Saved-plan observations and monitor service use synthetic sources only."""
from __future__ import annotations

# ruff: noqa: F811 -- imported pytest fixture injected by name

from copy import deepcopy
from datetime import timedelta

import pytest

import alert_events as ae
import portfolio as pf
import position_followup_service as followup_service
import position_plan_service as plans
import tenant
from position_followup import followup_view, notification_key, observe_plan, plan_key
from test_position_action_plan import NOW, PARAMS, QUOTE, plan_client  # noqa: F401


def setup_plan(client, choice: str = 't_watch', parameters: dict | None = PARAMS) -> tuple[str, dict]:
    position_id = pf._load()['holdings'][0]['position_id']
    value = plans.read_position_plan(position_id)
    response = client.post(f'/api/positions/{position_id}/plan', json={
        'choice': choice, 'parameters': parameters,
        'expected_position': value['position_snapshot'], 'expected_revision': value['revision']})
    assert response.status_code == 200, response.text
    return position_id, plans.read_position_plan(position_id)


def test_position_followup_records_only_background_check_when_page_is_read(plan_client, monkeypatch) -> None:
    client, _ = plan_client
    position_id, value = setup_plan(client)
    assert value['followup']['last_check'] is None and value['followup']['mode'] == 'off'
    assert value['followup']['current_plan_key'] == plan_key(pf._load()['position_reviews'][position_id], position_id)
    portfolio = pf.get_portfolio()
    monkeypatch.setattr(pf, 'get_portfolio', lambda: (_ for _ in ()).throw(AssertionError('extra quote batch')))
    before = deepcopy(pf._load()['position_reviews'][position_id])
    result = followup_service.check_plan_followups(portfolio, {}, current=NOW, notify=False)
    assert not result['skipped'] and result['observations'][0]['state'] == 'waiting'
    after = pf._load()['position_reviews'][position_id]
    assert {key: value for key, value in after.items() if key != 'followup'} == before
    assert after['followup']['revision'] == value['revision']
    assert ae.list_events()[0]['status'] == 'OPEN'


@pytest.mark.parametrize('price,state', [(9.2, 'waiting'), (9.5, 'price_watch'), (9.7, 'price_watch'), (8.9, 'below_watch')])
def test_position_followup_describes_location_when_saved_price_observed(plan_client, price, state) -> None:
    client, _ = plan_client
    position_id, value = setup_plan(client)
    root = pf._load()['position_reviews'][position_id]
    value['basis']['price'] = price
    observation = observe_plan(value, root, current=NOW)
    assert observation.state == state
    assert observation.quote_as_of == value['basis']['quote_as_of']
    assert observation.observed_at == NOW.isoformat()
    assert '成交完成' not in observation.detail
    if state == 'price_watch':
        assert '尚未确认' in observation.detail and '不等于' in observation.detail


def test_position_followup_never_uses_moving_daily_highs_when_prices_not_saved(plan_client) -> None:
    client, _ = plan_client
    position_id, value = setup_plan(client, parameters=None)
    root = pf._load()['position_reviews'][position_id]
    value['basis']['price'] = 99
    result = observe_plan(value, root, current=NOW)
    assert result.state == 'missing_prices'
    assert notification_key({**root, 'followup_mode': 'changes'}, result.to_dict(), current=NOW) is None


def test_position_followup_key_ignores_quote_and_dynamic_rule_high_when_same_state(plan_client) -> None:
    client, _ = plan_client
    position_id, value = setup_plan(client)
    root = {**pf._load()['position_reviews'][position_id], 'followup_mode': 'changes'}
    value['basis']['price'] = 9.5
    first = observe_plan(value, root, current=NOW).to_dict()
    value['basis']['price'] = 9.8
    value['revision'] = 'risk-high-water-changed'
    second = observe_plan(value, root, current=NOW + timedelta(minutes=5)).to_dict()
    assert notification_key(root, first, current=NOW) == notification_key(root, second, current=NOW + timedelta(days=1))
    assert plan_key(root, position_id) != plan_key({**root, 'version': root['version'] + 1}, position_id)
    root['followup_mode'] = 'daily'
    assert notification_key(root, first, current=NOW) != notification_key(root, second, current=NOW + timedelta(days=1))


def test_position_followup_requires_review_when_conditions_change_or_sale_recorded(plan_client) -> None:
    client, _ = plan_client
    position_id, _ = setup_plan(client)
    root = pf._load()['position_reviews'][position_id]
    root['constraints_version'] = 1
    value = plans.read_position_plan(position_id)
    assert observe_plan(value, root, current=NOW).state == 'needs_review'
    value['sale_requires_review'] = True
    assert observe_plan(value, root, current=NOW).reason == 'sale'


def test_position_followup_pauses_with_real_personal_or_market_reason_when_restricted(plan_client) -> None:
    client, _ = plan_client
    position_id, value = setup_plan(client)
    root = pf._load()['position_reviews'][position_id]
    value['basis']['personal_pause_t'] = True
    assert observe_plan(value, root, current=NOW).state == 'paused'
    value['basis']['personal_pause_t'] = False
    value['basis']['joint_weakness'] = True
    assert observe_plan(value, root, current=NOW).reason == 'joint_weakness'
    value['basis']['quote_issue'] = '缺少有效行情'
    assert observe_plan(value, root, current=NOW).state == 'evidence_unavailable'


def test_position_followup_preference_cas_does_not_expire_plan_when_only_mode_changes(plan_client) -> None:
    client, _ = plan_client
    position_id, value = setup_plan(client)
    body = {'mode': 'changes', 'expected_mode': 'off', 'expected_position': value['position_snapshot'], 'expected_revision': value['revision']}
    response = client.post(f'/api/positions/{position_id}/followup', json=body)
    assert response.status_code == 200, response.text
    assert response.json()['data']['revision'] == value['revision']
    assert response.json()['data']['followup']['mode'] == 'changes'
    assert client.post(f'/api/positions/{position_id}/followup', json=body).status_code == 409
    assert client.post(f'/api/positions/{position_id}/followup', json={**body, 'mode': 'always'}).status_code == 400
    assert client.post(f'/api/positions/{position_id}/followup', json=body, headers={'X-User-Id': 'foreign-followup'}).status_code == 409


def test_position_followup_does_not_commit_old_observation_when_user_changes_during_check(plan_client, monkeypatch) -> None:
    client, _ = plan_client
    position_id, _ = setup_plan(client)
    portfolio = pf.get_portfolio()
    original = followup_service.observe_plan
    def changed(value, root, *, current):
        observation = original(value, root, current=current)
        plans.save_position_plan(position_id, 'hold', expected_position=value['position_snapshot'], expected_revision=value['revision'])
        return observation
    monkeypatch.setattr(followup_service, 'observe_plan', changed)
    result = followup_service.check_plan_followups(portfolio, {}, current=NOW)
    assert result['skipped'] and result['observations'] == []
    assert 'followup' not in pf._load()['position_reviews'][position_id]


def test_position_followup_displays_stale_history_when_risk_version_or_time_changes(plan_client) -> None:
    client, _ = plan_client
    position_id, value = setup_plan(client)
    followup_service.check_plan_followups(pf.get_portfolio(), {}, current=NOW)
    root = pf._load()['position_reviews'][position_id]
    assert not followup_view(root, value['revision'], value['position_snapshot'], current=NOW)['requires_refresh']
    assert followup_view(root, 'new-rules', value['position_snapshot'], current=NOW)['requires_refresh']
    assert followup_view(root, value['revision'], value['position_snapshot'], current=NOW + timedelta(days=1))['requires_refresh']


def test_position_followup_does_not_leak_tenant_or_keep_active_when_position_closed(plan_client, monkeypatch) -> None:
    client, _ = plan_client
    monkeypatch.setattr(followup_service, 'now_shanghai', lambda: NOW)
    position_id, _ = setup_plan(client)
    portfolio = pf.get_portfolio()
    portfolio['holdings'][0]['quote'] = {**QUOTE, 'price': 9.7}
    followup_service.check_plan_followups(portfolio, {}, current=NOW)
    items = client.get('/api/position-followups').json()['data']['items']
    assert items[0]['state'] == 'price_watch'
    assert client.get('/api/position-followups', headers={'X-User-Id': 'foreign-followup'}).json()['data']['items'] == []
    pf.close_position('002129', '2026-10-23', 9.5, 1000, 12, operation_id='followup-exit')
    result = followup_service.check_plan_followups(pf.get_portfolio(), {}, current=NOW)
    assert result['observations'][0]['state'] == 'exited'
    assert client.get('/api/position-followups').json()['data']['items'] == []
    assert pf._load()['position_reviews'][position_id]['followup']['state'] == 'exited'
    pf.add_holding('002129', 100, 9, '2026-10-23')
    assert pf.get_portfolio()['holdings'][0]['position_id'] != position_id
    assert client.get('/api/position-followups').json()['data']['items'] == []


def test_position_followup_read_mode_defaults_off_when_not_confirmed(plan_client) -> None:
    client, _ = plan_client
    position_id, _ = setup_plan(client)
    worksheet = plans.read_position_plan(position_id)
    assert worksheet['followup'] == {'mode': 'off', 'last_check': None, 'requires_refresh': False, 'issue': '',
                                     'current_plan_key': plan_key(pf._load()['position_reviews'][position_id], position_id)}
    assert tenant.current_tenant.get() == 'local'


def test_position_followup_rechecks_before_send_when_mode_plan_or_freshness_changes(plan_client, monkeypatch) -> None:
    client, _ = plan_client
    position_id, value = setup_plan(client)
    root = pf._load()['position_reviews'][position_id]
    client.post(f'/api/positions/{position_id}/followup', json={'mode': 'changes', 'expected_mode': 'off', 'expected_position': value['position_snapshot'], 'expected_revision': value['revision']})
    portfolio = pf.get_portfolio()
    portfolio['holdings'][0]['quote'] = {**QUOTE, 'price': 9.6}
    result = followup_service.check_plan_followups(portfolio, {}, current=NOW, notify=False)
    observation = result['observations'][0]
    root = pf._load()['position_reviews'][position_id]
    candidate = {**observation, 'mode': 'changes', 'key': notification_key(root, observation, current=NOW)}
    monkeypatch.setattr(followup_service, 'now_shanghai', lambda: NOW)
    assert followup_service._validate_candidate(candidate)
    monkeypatch.setattr(followup_service, 'now_shanghai', lambda: NOW + timedelta(hours=1))
    assert not followup_service._validate_candidate(candidate)
    monkeypatch.setattr(followup_service, 'now_shanghai', lambda: NOW)
    plans.save_position_plan(position_id, 'hold', expected_position=value['position_snapshot'], expected_revision=value['revision'])
    assert not followup_service._validate_candidate(candidate)


@pytest.fixture
def hold_risk_followup(plan_client, monkeypatch) -> tuple[str, dict]:
    from aqsp.web.live_candidate_view import LiveCandidateViewConfig

    client, _ = plan_client
    position_id, value = setup_plan(client, choice='hold', parameters=None)
    followup_service.save_followup_mode(position_id, 'changes', 'off', value['position_snapshot'], value['revision'])
    monkeypatch.setattr(followup_service, 'now_shanghai', lambda: NOW)
    portfolio = pf.get_portfolio()
    portfolio['holdings'][0]['quote'] = {
        **QUOTE, 'quote_time': (NOW - LiveCandidateViewConfig().max_age).strftime('%Y%m%d%H%M%S')}
    observation = followup_service.check_plan_followups(portfolio, {}, current=NOW)['observations'][0]
    assert (observation['state'], observation['reason'], observation['condition_checks']) == ('needs_review', 'risk', None)
    root = pf._load()['position_reviews'][position_id]
    return position_id, {**observation, 'mode': 'changes', 'key': notification_key(root, observation, current=NOW)}


def test_position_followup_rejects_risk_notification_when_quote_expires_before_send(hold_risk_followup, monkeypatch) -> None:
    _, candidate = hold_risk_followup
    assert followup_service._validate_candidate(candidate)
    # The inspection is only a minute old; its original source is now expired.
    monkeypatch.setattr(followup_service, 'now_shanghai', lambda: NOW + timedelta(minutes=1))
    assert not followup_service._validate_candidate(candidate)


@pytest.mark.parametrize('quote_as_of', [None, 'malformed', '2026-10-23', '2026-10-23T10:00:00'])
def test_position_followup_rejects_risk_notification_when_source_time_unverifiable(hold_risk_followup, quote_as_of) -> None:
    position_id, candidate = hold_risk_followup
    data = pf._load()
    data['position_reviews'][position_id]['followup']['quote_as_of'] = quote_as_of
    pf._save(data)
    assert not followup_service._validate_candidate({**candidate, 'quote_as_of': quote_as_of})


@pytest.mark.parametrize('reason', ['sale', 'plan_changed'])
@pytest.mark.parametrize('quote_as_of', [None, '2026-10-23T09:00:00+08:00'])
def test_position_followup_keeps_non_price_review_when_quote_unavailable(hold_risk_followup, reason, quote_as_of) -> None:
    position_id, _ = hold_risk_followup
    data = pf._load()
    root = data['position_reviews'][position_id]
    if reason == 'sale':
        data['closed'] = [{'position_id': position_id, 'code': root['symbol'], 'date': '2026-10-23',
                           'price': 9.2, 'shares': 100, 'operation_id': 'synthetic-sale'}]
    else:
        root['review_plan']['position_snapshot']['cost'] += 1
    pf._save(data)
    worksheet = plans.read_position_plan(position_id)
    worksheet['basis']['quote_as_of'] = quote_as_of
    worksheet['basis']['quote_issue'] = '行情过期'
    observation = observe_plan(worksheet, root, current=NOW).to_dict()
    assert (observation['state'], observation['reason']) == ('needs_review', reason)
    data['position_reviews'][position_id]['followup'] = observation
    pf._save(data)
    candidate = {**observation, 'mode': 'changes', 'key': notification_key(root, observation, current=NOW)}
    assert followup_service._validate_candidate(candidate)


def test_position_followup_notify_false_does_not_consume_quota_when_price_is_reached(plan_client, monkeypatch) -> None:
    from aqsp import notification_runtime
    from aqsp.notifier import NotifyResult
    import position_followup_delivery
    client, _ = plan_client
    position_id, value = setup_plan(client)
    body = {'mode': 'changes', 'expected_mode': 'off', 'expected_position': value['position_snapshot'], 'expected_revision': value['revision']}
    assert client.post(f'/api/positions/{position_id}/followup', json=body).status_code == 200
    monkeypatch.setattr(followup_service, 'now_shanghai', lambda: NOW)
    monkeypatch.setattr(position_followup_delivery, 'now_shanghai', lambda: NOW)
    monkeypatch.setattr(notification_runtime, 'now_shanghai', lambda: NOW)
    sent = []
    monkeypatch.setattr(notification_runtime, 'dispatch_notification', lambda markdown, **kwargs: sent.append(markdown) or [NotifyResult('synthetic', True, 'test')])
    portfolio = pf.get_portfolio()
    portfolio['holdings'][0]['quote'] = {**QUOTE, 'price': 9.6}
    followup_service.check_plan_followups(portfolio, {}, current=NOW, notify=False)
    assert not sent
    result = followup_service.check_plan_followups(portfolio, {}, current=NOW, notify=True)
    assert result['notify'] and len(sent) == 1
    for price in (9.2, 9.6, 9.7, 9.3, 9.9):
        portfolio['holdings'][0]['quote'] = {**QUOTE, 'price': price}
        followup_service.check_plan_followups(portfolio, {}, current=NOW, notify=True)
    assert len(sent) == 1
    assert '已到保存的' in sent[0] and '到价不等于' in sent[0]
    assert pf._load()['holdings'][0]['shares'] == 1000


def test_position_followup_does_not_seed_foreign_tenant_when_mode_saved(plan_client) -> None:
    from pathlib import Path
    client, _ = plan_client
    position_id, value = setup_plan(client)
    token = tenant.current_tenant.set('foreign-followup-empty')
    try:
        target = Path(pf._pf_file())
        assert not target.exists()
        assert followup_service.save_followup_mode(position_id, 'changes', 'off', value['position_snapshot'], value['revision']) is None
        assert not target.exists()
    finally:
        tenant.current_tenant.reset(token)


def test_position_followup_rejects_new_plan_when_quote_batch_began_before_save(plan_client) -> None:
    client, _ = plan_client
    position_id, value = setup_plan(client)
    keys = {position_id: plan_key(pf._load()['position_reviews'][position_id], position_id)}
    batch = pf.get_portfolio()
    plans.save_position_plan(position_id, 'reduce', parameters={**PARAMS, 'buy_price': None},
                             expected_position=value['position_snapshot'], expected_revision=value['revision'])
    result = followup_service.check_plan_followups(batch, {}, current=NOW, expected_plan_keys=keys)
    assert result['observations'] == [] and '取价期间' in result['skipped'][0]['reason']
    assert 'followup' not in pf._load()['position_reviews'][position_id]


def test_position_followup_keeps_joint_pause_when_quote_then_sector_evidence_lost(plan_client) -> None:
    client, _ = plan_client
    position_id, value = setup_plan(client)
    root = deepcopy(pf._load()['position_reviews'][position_id])
    value['basis']['joint_weakness'] = True
    first = observe_plan(value, root, current=NOW).to_dict()
    assert first['reason'] == 'joint_weakness'
    root['followup'] = first
    value['basis']['joint_weakness'] = False
    value['basis']['quote_issue'] = '行情过期'
    value['route_review']['evidence']['sector']['status'] = 'unknown'
    second = observe_plan(value, root, current=NOW + timedelta(minutes=1)).to_dict()
    assert second['reason'] == 'joint_evidence_unknown'
    root['followup'] = second
    value['basis']['quote_issue'] = ''
    third = observe_plan(value, root, current=NOW + timedelta(minutes=2))
    assert third.state == 'evidence_unavailable' and third.reason == 'joint_evidence_unknown'
    # An explicitly changed human plan does not inherit the previous plan's pause.
    root['version'] += 1
    assert observe_plan(value, root, current=NOW + timedelta(minutes=3)).reason != 'joint_evidence_unknown'


@pytest.mark.parametrize('corrupt', ['broken', {}, {'observed_at': 'invalid'}])
def test_position_followup_preserves_bad_record_when_check_and_page_read(plan_client, corrupt) -> None:
    client, _ = plan_client
    position_id, value = setup_plan(client)
    data = pf._load()
    data['position_reviews'][position_id]['followup'] = corrupt
    pf._save(data)
    result = followup_service.check_plan_followups(pf.get_portfolio(), {}, current=NOW)
    assert result['observations'] == [] and result['skipped'][0]['level'] == 'PLAN'
    assert pf._load()['position_reviews'][position_id]['followup'] == corrupt
    view = plans.read_position_plan(position_id)['followup']
    assert view['requires_refresh'] and view['last_check'] is None and '损坏' in view['issue']
    assert client.get('/api/position-followups').status_code == 200


def test_position_followup_candidate_rejects_frozen_content_when_latest_evidence_differs(plan_client, monkeypatch) -> None:
    client, _ = plan_client
    position_id, value = setup_plan(client)
    followup_service.save_followup_mode(position_id, 'changes', 'off', value['position_snapshot'], value['revision'])
    portfolio = pf.get_portfolio()
    portfolio['holdings'][0]['quote'] = {**QUOTE, 'price': 9.6}
    observation = followup_service.check_plan_followups(portfolio, {}, current=NOW)['observations'][0]
    root = pf._load()['position_reviews'][position_id]
    candidate = {**observation, 'mode': 'changes', 'key': notification_key(root, observation, current=NOW)}
    monkeypatch.setattr(followup_service, 'now_shanghai', lambda: NOW)
    assert followup_service._validate_candidate(candidate)
    for patch in ({'revision': 'old'}, {'detail': '旧报价'}, {'plan_key': 'old'},
                  {'position_snapshot': {}}, {'observed_at': (NOW - timedelta(hours=1)).isoformat()}):
        assert not followup_service._validate_candidate({**candidate, **patch})


@pytest.mark.parametrize('price,reason', [(8.9, 'low'), (9.6, 'high')])
def test_position_followup_does_not_drop_price_fact_when_other_risk_already_notified(plan_client, monkeypatch, price, reason) -> None:
    import position_followup_delivery as delivery
    client, _ = plan_client
    position_id, value = setup_plan(client)
    followup_service.save_followup_mode(position_id, 'changes', 'off', value['position_snapshot'], value['revision'])
    portfolio = pf.get_portfolio()
    portfolio['holdings'][0]['quote'] = {**QUOTE, 'price': price}
    captured = []
    def send(candidates, *, validate):
        captured.extend(candidates)
        return delivery.FollowupDeliveryResult((), False)
    monkeypatch.setattr(delivery, 'deliver_followup_notifications', send)
    followup_service.check_plan_followups(portfolio, {}, current=NOW, notify=True, risk_notified_positions={position_id})
    assert captured[0]['reason'] == reason


def test_position_followup_monitor_reuses_quote_and_sector_batches_when_live_check_runs(plan_client, monkeypatch) -> None:
    import decision_context
    import live_risk_service
    import position_context_source
    from aqsp.data import source_factory
    client, _ = plan_client
    position_id, _ = setup_plan(client)
    quote_calls, sector_calls = [], []
    def quote(codes):
        quote_calls.append(codes)
        return {code: {**QUOTE, 'name': '合成持仓', 'price': 9.6} for code in codes}
    monkeypatch.setattr(pf.astock, 'tencent_quote', quote)
    monkeypatch.setattr(position_context_source, 'load_position_sectors', lambda codes: sector_calls.append(codes) or {})
    monkeypatch.setattr(decision_context, 'load_cached_market_overview', lambda: {})
    monkeypatch.setattr(source_factory, 'build_data_source', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('synthetic history unavailable')))
    for rule in pf.get_risk_rules():
        pf.update_risk_rule(rule['id'], {'enabled': False})
    before = deepcopy(pf._load()['position_reviews'][position_id])
    result = live_risk_service.check_positions(notify=False)
    assert len(quote_calls) == 1 and sector_calls == [['002129']]
    observation = pf._load()['position_reviews'][position_id]['followup']
    assert observation['state'] == 'price_watch' and observation['quote_as_of'] == '2026-10-23T10:00:00+08:00'
    assert pf._load()['position_reviews'][position_id]['version'] == before['version']
    assert result['notify'] == [] and result['new_events'] == []


def test_position_followup_keeps_joint_pause_when_personal_pause_temporarily_overlays(plan_client) -> None:
    client, _ = plan_client
    position_id, value = setup_plan(client)
    root = deepcopy(pf._load()['position_reviews'][position_id])
    value['basis']['joint_weakness'] = True
    root['followup'] = observe_plan(value, root, current=NOW).to_dict()
    value['basis']['joint_weakness'] = False
    value['basis']['personal_pause_t'] = True
    value['route_review']['evidence']['sector']['status'] = 'unknown'
    root['followup'] = observe_plan(value, root, current=NOW).to_dict()
    assert root['followup']['reason'] == 'personal' and root['followup']['joint_pause_pending']
    value['basis']['personal_pause_t'] = False
    assert observe_plan(value, root, current=NOW).reason == 'joint_evidence_unknown'


def test_position_followup_skips_rule_batch_rejected_by_live_commit(plan_client, monkeypatch) -> None:
    import decision_context
    import live_risk_commit
    import live_risk_service
    import position_context_source
    from aqsp.data import source_factory
    client, _ = plan_client
    position_id, _ = setup_plan(client)
    monkeypatch.setattr(position_context_source, 'load_position_sectors', lambda codes: {})
    monkeypatch.setattr(decision_context, 'load_cached_market_overview', lambda: {})
    monkeypatch.setattr(source_factory, 'build_data_source', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('synthetic history unavailable')))
    original = live_risk_commit.commit_position_check
    def change_during_check(*args, **kwargs):
        pf.update_risk_rule('002129-L1', {'param_value': 0.12})
        return original(*args, **kwargs)
    monkeypatch.setattr(live_risk_commit, 'commit_position_check', change_during_check)
    result = live_risk_service.check_positions(notify=False)
    assert any(row['level'] == 'POSITION' for row in result['checks']['skipped'])
    assert any(row['level'] == 'PLAN' for row in result['checks']['skipped'])
    assert 'followup' not in pf._load()['position_reviews'][position_id]


def test_position_followup_rejects_post_commit_rule_change_before_build(plan_client) -> None:
    from position_followup import risk_rule_key
    client, _ = plan_client
    position_id, _ = setup_plan(client)
    keys = {position_id: risk_rule_key(pf._load()['risk_rules'], position_id)}
    batch = pf.get_portfolio()
    pf.update_risk_rule('002129-L1', {'param_value': 0.12})
    result = followup_service.check_plan_followups(batch, {}, current=NOW, expected_rule_keys=keys)
    assert result['observations'] == [] and result['skipped'][0]['level'] == 'PLAN'
