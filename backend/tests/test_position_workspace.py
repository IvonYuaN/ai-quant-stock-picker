"""Canonical position roots, optional constraints, CAS and real receipt recovery."""
from __future__ import annotations

# ruff: noqa: F811 -- imported pytest fixtures are intentionally injected by name

from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import alert_events as ae
import portfolio as pf
import position_plan_service as service
from test_position_action_plan import NOW, PARAMS, QUOTE, plan_client  # noqa: F401
from test_position_discussion import discussion_client  # noqa: F401


def worksheet(client: TestClient, position_id: str) -> dict:
    response = client.get(f'/api/positions/{position_id}/action-plan')
    assert response.status_code == 200, response.text
    return response.json()['data']


def guards(value: dict) -> dict:
    return {'expected_position': value['position_snapshot'], 'expected_revision': value['revision']}


def own_id() -> str:
    return pf._load()['holdings'][0]['position_id']


def test_position_workspace_opens_and_saves_when_no_alert_exists(plan_client) -> None:
    client, _ = plan_client
    ae._save([])
    position_id = own_id()
    before = deepcopy(pf._load())
    value = worksheet(client, position_id)
    assert value['constraints']['extra_cash'] is None
    assert value['constraints']['loss_limit'] is None
    assert value['route_review']['routes'][0]['status'] == 'risk_review'
    assert any(row['level'] == 'L1' and row['status'] == 'triggered' for row in value['current_risk'])
    response = client.post(f'/api/positions/{position_id}/plan', json={'choice': 'hold', **guards(value)})
    assert response.status_code == 200
    assert worksheet(client, position_id)['review_plan']['choice'] == 'hold'
    assert ae._load() == []
    assert pf._load()['holdings'] == before['holdings']
    assert pf._load()['risk_rules'] == before['risk_rules']
    assert position_id in pf._load()['position_reviews']


def test_position_workspace_rejects_guessed_identity_when_other_tenant_has_no_file(plan_client) -> None:
    client, _ = plan_client
    position_id = own_id()
    response = client.get(f'/api/positions/{position_id}/action-plan', headers={'X-User-Id': 'foreign-g2'})
    assert response.status_code == 409
    assert not (Path(pf.CACHE_DIR) / 'users' / 'foreign-g2' / 'portfolio.json').exists()
    assert client.get('/api/alerts', headers={'X-User-Id': 'foreign-g2'}).status_code == 200
    assert not (Path(pf.CACHE_DIR) / 'users' / 'foreign-g2' / 'portfolio.json').exists()


def test_position_workspace_uses_one_root_when_reminder_is_handled_and_legacy_changes(plan_client) -> None:
    client, event_id = plan_client
    position_id = own_id()
    events = ae._load()
    events[0]['review_plan'] = {'choice': 'hold', 'updated_at': NOW.isoformat()}
    ae._save(events)
    value = worksheet(client, position_id)
    assert value['review_plan']['choice'] == 'hold'
    assert 'position_reviews' not in pf._load()
    response = client.post(f'/api/alerts/{event_id}/plan', json={'choice': 'reduce', **guards(value)})
    assert response.status_code == 200
    # A historical event is not canonical, and must never overwrite the root.
    events = ae._load()
    events[0]['review_plan'] = {'choice': 't_watch', 'updated_at': '2099-01-01T00:00:00+08:00'}
    events[0]['status'] = 'HANDLED'
    ae._save(events)
    assert client.get(f'/api/alerts/{event_id}/action-plan').json()['data']['review_plan']['choice'] == 'reduce'
    assert worksheet(client, position_id)['review_plan']['choice'] == 'reduce'
    assert ae.list_events()[0]['review_plan']['choice'] == 'reduce'
    assert pf._load()['position_reviews'][position_id]['migrated_from'] == [event_id]


def test_position_workspace_requires_cas_when_old_choice_client_has_no_guard(plan_client) -> None:
    client, event_id = plan_client
    before = deepcopy(pf._load())
    assert client.post(f'/api/alerts/{event_id}/plan', json={'choice': 'hold'}).status_code == 409
    assert client.post(f'/api/positions/{own_id()}/plan', json={'choice': 'hold'}).status_code == 409
    assert pf._load() == before


def test_position_workspace_invalidates_old_calculation_when_constraints_change(plan_client) -> None:
    client, _ = plan_client
    position_id = own_id()
    value = worksheet(client, position_id)
    assert client.post(f'/api/positions/{position_id}/plan', json={'choice': 't_watch', 'parameters': PARAMS, **guards(value)}).status_code == 200
    value = worksheet(client, position_id)
    assert value['scenario'] is not None
    result = client.post(f'/api/positions/{position_id}/constraints', json={
        'attention': 'unavailable', 'extra_cash': 0, 'loss_limit': None, **guards(value)})
    assert result.status_code == 200
    changed = result.json()['data']
    assert changed['revision'] != value['revision']
    assert changed['scenario'] is None and changed['scenario_issue']
    assert changed['basis']['personal_pause_t'] is True
    assert next(row for row in changed['route_review']['routes'] if row['choice'] == 't_watch')['status'] == 'paused'
    assert client.post(f'/api/positions/{position_id}/plan', json={'choice': 'hold', **guards(value)}).status_code == 409
    # Re-selecting the same intent cannot silently revive the old parameters.
    assert client.post(f'/api/positions/{position_id}/plan', json={'choice': 't_watch', **guards(changed)}).status_code == 200
    changed = worksheet(client, position_id)
    assert changed['scenario'] is None
    assert client.post(f'/api/positions/{position_id}/plan', json={'choice': 't_watch', 'parameters': PARAMS, **guards(changed)}).status_code == 400


def test_position_workspace_keeps_loss_baseline_when_quote_refreshes(plan_client, monkeypatch) -> None:
    client, _ = plan_client
    position_id = own_id()
    value = worksheet(client, position_id)
    result = client.post(f'/api/positions/{position_id}/constraints', json={
        'attention': 'available', 'extra_cash': 0, 'loss_limit': 500, **guards(value)})
    assert result.status_code == 200, result.text
    saved = result.json()['data']
    monkeypatch.setattr(pf.astock, 'tencent_quote', lambda codes: {code: {**QUOTE, 'price': 8.8} for code in codes})
    changed = worksheet(client, position_id)
    assert changed['revision'] == saved['revision']
    assert changed['constraints']['risk_baseline'] == saved['constraints']['risk_baseline']
    assert changed['risk_remaining'] == pytest.approx(100)
    assert changed['constraints']['extra_cash'] == 0


def test_position_workspace_rejects_late_model_when_conditions_change(discussion_client, monkeypatch) -> None:
    import position_discussion as discussion
    client, _, value, _ = discussion_client
    position_id = value['position_id']
    original = discussion.call_model
    async def changed(endpoint, payload, timeout, limits):
        if 'rounds' in payload:
            current = service.read_position_plan(position_id)
            service.save_position_constraints(position_id, {'attention': 'unavailable'}, current['position_snapshot'], current['revision'])
        return await original(endpoint, payload, timeout, limits)
    monkeypatch.setattr(discussion, 'call_model', changed)
    result = client.post(f'/api/positions/{position_id}/discussion', json={'choice': None, **guards(value)})
    assert result.status_code == 409
    assert worksheet(client, position_id)['comparison'] is None
    assert worksheet(client, position_id)['constraints']['attention'] == 'unavailable'


def test_position_workspace_recovers_sale_without_event_and_archives_when_closed(plan_client) -> None:
    client, _ = plan_client
    position_id = own_id()
    ae._save([])
    value = worksheet(client, position_id)
    assert client.post(f'/api/positions/{position_id}/plan', json={'choice': 'hold', **guards(value)}).status_code == 200
    pf.close_position('002129', '2026-10-23', 9.5, 200, 12, operation_id='no-alert-sale')
    value = worksheet(client, position_id)
    assert value['recorded_sale']['shares'] == 200
    assert value['position_snapshot']['shares'] == 800
    assert value['sale_requires_review'] is True
    assert client.post(f'/api/positions/{position_id}/plan', json={'choice': 'hold', **guards(value)}).status_code == 200
    assert worksheet(client, position_id)['sale_requires_review'] is False
    pf.close_position('002129', '2026-10-23', 9.4, 800, 12, operation_id='no-alert-exit')
    archived = worksheet(client, position_id)
    assert archived['archived'] is True
    assert archived['position_snapshot']['position_id'] == position_id
    assert client.post(f'/api/positions/{position_id}/plan', json={'choice': 'hold', **guards(archived)}).status_code == 409
    pf.add_holding('002129', 100, 9, '2026-10-23', operation_id='reopen')
    fresh = worksheet(client, own_id())
    assert fresh['position_id'] != position_id and fresh['review_plan'] == {}
    assert worksheet(client, position_id)['archived'] is True
    assert ae._load() == []


def test_position_workspace_uses_latest_rules_when_quote_io_races_with_rule_change(plan_client, monkeypatch) -> None:
    client, _ = plan_client
    position_id = own_id()
    original = pf.get_portfolio
    def changed() -> dict:
        value = original()
        data = pf._load()
        for rule in data['risk_rules']:
            rule['enabled'] = False
            rule['revision'] += 1
        pf._save(data)
        return value
    monkeypatch.setattr(pf, 'get_portfolio', changed)
    value = worksheet(client, position_id)
    assert all(row['status'] == 'disabled' for row in value['current_risk'])
    assert all(row['enabled'] is False for row in value['risk_trigger']['rules'])
    assert value['route_review']['routes'][0]['status'] == 'review'


def test_position_workspace_does_not_inherit_legacy_plan_when_snapshot_identity_differs(plan_client) -> None:
    client, event_id = plan_client
    events = ae._load()
    events[0]['review_plan'] = {'choice': 't_watch', 'updated_at': NOW.isoformat(), 'position_snapshot': {'position_id': 'old-position'}}
    ae._save(events)
    value = worksheet(client, own_id())
    assert value['review_plan'] == {}
    assert ae._load()[0]['id'] == event_id
