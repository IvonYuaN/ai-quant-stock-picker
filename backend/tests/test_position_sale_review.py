"""Synthetic sale/remaining-position review must not require fee calculations."""
from __future__ import annotations

# ruff: noqa: F811 -- imported pytest fixtures are intentionally injected

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

import portfolio as pf
import position_discussion as discussion
import position_strategy_watch as watches
from position_followup import observe_plan
from test_position_action_plan import NOW, plan_client  # noqa: F401
from test_position_discussion import discussion_client  # noqa: F401


def worksheet(client: Any, position_id: str) -> dict[str, Any]:
    response = client.get(f'/api/positions/{position_id}/action-plan')
    assert response.status_code == 200, response.text
    return response.json()['data']


def guards(value: dict[str, Any]) -> dict[str, Any]:
    return {'expected_position': value['position_snapshot'], 'expected_revision': value['revision']}


def review_body(value: dict[str, Any]) -> dict[str, Any]:
    return {**guards(value), 'expected_sale_id': value['recorded_sale']['id']}


def analyze(client: Any, position_id: str, choice: str | None) -> dict[str, Any]:
    value = worksheet(client, position_id)
    response = client.post(f'/api/positions/{position_id}/discussion', json={'choice': choice, **guards(value)})
    assert response.status_code == 200, response.text
    result = response.json()['data']
    assert result['status'] == 'complete' and result['summary']
    return result


def adopt(client: Any, position_id: str, result: dict[str, Any], choice: str) -> Any:
    value = worksheet(client, position_id)
    return client.post(f'/api/positions/{position_id}/strategy-watch', json={
        **guards(value), 'choice': choice,
        'source_discussion': 'comparison' if result['choice'] is None else 'plan',
        'generated_at': result['generated_at'], 'action_index': 0, 'comparator': 'at_or_above',
        'expected_analysis_basis_id': result['analysis_basis_id']})


@pytest.fixture
def sale_client(discussion_client: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch) -> tuple[Any, str]:
    client, _, _, _ = discussion_client
    monkeypatch.setattr(discussion, 'now_shanghai', lambda: NOW)
    monkeypatch.setattr(watches, 'now_shanghai', lambda: NOW)
    return client, pf._load()['holdings'][0]['position_id']


@pytest.mark.parametrize('choice', ['hold', 'reduce', 't_watch'])
def test_position_sale_review_resumes_new_strategy_without_fees_when_human_confirms(sale_client: tuple[Any, str], choice: str) -> None:
    client, position_id = sale_client
    value = worksheet(client, position_id)
    assert client.post(f'/api/positions/{position_id}/plan', json={'choice': choice, **guards(value)}).status_code == 200
    pf.close_position('002129', '2026-10-23', 9.5, 200, 12, operation_id='sale-first')
    before = deepcopy(pf._load())
    value = worksheet(client, position_id)
    assert value['recorded_sale']['id'] == 'sale-first' and value['sale_requires_review']
    response = client.post(f'/api/positions/{position_id}/sale-review', json=review_body(value))
    assert response.status_code == 200, response.text
    confirmed = response.json()['data']
    assert not confirmed['sale_requires_review']
    assert confirmed['position_snapshot']['shares'] == 800
    assert confirmed['revision'] != value['revision']
    assert confirmed['review_plan'].get('parameters') is None
    assert 'watch_stopped' not in confirmed['review_plan']
    assert {key: row for key, row in pf._load().items() if key != 'position_reviews'} == {
        key: row for key, row in before.items() if key != 'position_reviews'}
    original = before['position_reviews'][position_id]
    saved = pf._load()['position_reviews'][position_id]
    assert saved['constraints'] == original['constraints']
    assert saved['review_plan']['position_snapshot'] == original['review_plan']['position_snapshot']
    assert saved['review_plan']['trade_revision'] == original['review_plan']['trade_revision']
    result = analyze(client, position_id, choice)
    assert worksheet(client, position_id)['review_plan']['discussion'] == result
    response = adopt(client, position_id, result, choice)
    assert response.status_code == 200, response.text
    adopted = response.json()['data']
    assert not adopted['sale_requires_review'] and adopted['review_plan'].get('parameters') is None
    observed = observe_plan(adopted, pf._load()['position_reviews'][position_id], current=NOW)
    assert observed.reason not in {'sale', 'plan_changed', 'watch_changed'}


def test_position_sale_review_current_analysis_remains_readable_before_human_confirmation(sale_client: tuple[Any, str]) -> None:
    client, position_id = sale_client
    pf.close_position('002129', '2026-10-23', 9.5, 200, 12, operation_id='sale-first')
    analyze(client, position_id, None)
    result = analyze(client, position_id, 't_watch')
    value = worksheet(client, position_id)
    assert value['review_plan']['discussion'] == result
    assert value['sale_requires_review']
    response = client.post(f'/api/positions/{position_id}/sale-review', json=review_body(value))
    assert response.status_code == 200, response.text
    assert response.json()['data']['review_plan'].get('discussion') is None
    assert pf._load()['position_reviews'][position_id].get('comparison') is None
    assert adopt(client, position_id, result, 't_watch').status_code in {400, 409}


@pytest.mark.parametrize('method', ['choice', 'adoption'])
@pytest.mark.parametrize('choice', ['hold', 'reduce'])
def test_position_sale_review_confirmation_survives_route_change_when_receipt_unchanged(
    sale_client: tuple[Any, str], method: str, choice: str,
) -> None:
    client, position_id = sale_client
    pf.close_position('002129', '2026-10-23', 9.5, 200, 12, operation_id='sale-first')
    value = worksheet(client, position_id)
    assert client.post(f'/api/positions/{position_id}/sale-review', json=review_body(value)).status_code == 200
    if method == 'choice':
        value = worksheet(client, position_id)
        assert client.post(f'/api/positions/{position_id}/plan', json={'choice': choice, **guards(value)}).status_code == 200
    else:
        result = analyze(client, position_id, None)
        assert adopt(client, position_id, result, choice).status_code == 200
    value = worksheet(client, position_id)
    assert value['review_plan']['choice'] == choice
    assert not value['sale_requires_review']
    assert value['review_plan']['acknowledged_sale_id'] == 'sale-first'
    pf.close_position('002129', '2026-10-23', 9.4, 100, 12, operation_id='sale-second')
    assert worksheet(client, position_id)['sale_requires_review']


def test_position_sale_review_can_track_buyback_after_confirmed_real_sale_without_calculator(
    sale_client: tuple[Any, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, position_id = sale_client
    pf.close_position('002129', '2026-10-23', 9.5, 200, 12, operation_id='sale-first')
    value = worksheet(client, position_id)
    assert client.post(f'/api/positions/{position_id}/sale-review', json=review_body(value)).status_code == 200
    original = discussion.call_model

    async def buyback_model(*args: Any, **kwargs: Any) -> str:
        payload = json.loads(await original(*args, **kwargs))
        for action in payload.get('actions', []):
            action['action'] = 'buy_review'
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(discussion, 'call_model', buyback_model)
    result = analyze(client, position_id, 't_watch')
    response = adopt(client, position_id, result, 't_watch')
    assert response.status_code == 200, response.text
    adopted = response.json()['data']
    assert adopted['review_plan']['watch']['action'] == 'buy_review'
    assert adopted['scenario'] is None and not adopted['review_plan'].get('parameters')
    observed = observe_plan(adopted, pf._load()['position_reviews'][position_id], current=NOW)
    assert observed.reason not in {'sale', 't_sale_required', 'plan_changed', 'watch_changed'}


def test_position_sale_review_does_not_restore_old_watch_or_calculation_when_confirmed(sale_client: tuple[Any, str]) -> None:
    from test_position_action_plan import PARAMS
    client, position_id = sale_client
    result = analyze(client, position_id, 't_watch')
    assert adopt(client, position_id, result, 't_watch').status_code == 200
    value = worksheet(client, position_id)
    assert client.post(f'/api/positions/{position_id}/plan', json={
        'choice': 't_watch', 'parameters': PARAMS, **guards(value)}).status_code == 200
    original = deepcopy(pf._load()['position_reviews'][position_id]['review_plan'])
    pf.close_position('002129', '2026-10-23', 9.5, 200, 12, operation_id='sale-first')
    value = worksheet(client, position_id)
    response = client.post(f'/api/positions/{position_id}/sale-review', json=review_body(value))
    assert response.status_code == 200, response.text
    confirmed = response.json()['data']
    assert confirmed['scenario'] is None and confirmed['scenario_issue']
    for key in ('position_snapshot', 'trade_revision', 'parameters'):
        assert confirmed['review_plan'][key] == original[key]
    assert 'watch' not in confirmed['review_plan'] and confirmed['review_plan']['watch_stopped']
    audit = pf._load()['position_reviews'][position_id]['watch_audit'][-1]
    assert audit['action'] == 'stop' and audit['reason'] == 'sale_review'
    assert audit['previous'] == original['watch']
    observed = observe_plan(confirmed, pf._load()['position_reviews'][position_id], current=NOW)
    assert observed.state == 'unplanned' and observed.reason == 'watch_stopped'
    pf.close_position('002129', '2026-10-23', 9.4, 100, 12, operation_id='sale-second')
    second = worksheet(client, position_id)
    assert second['sale_requires_review'] and second['review_plan']['watch_stopped']
    observed = observe_plan(second, pf._load()['position_reviews'][position_id], current=NOW)
    assert observed.state == 'needs_review' and observed.reason == 'sale'


@pytest.mark.parametrize('source', ['comparison', 'plan'])
def test_position_sale_review_cannot_adopt_new_watch_until_latest_sale_confirmed(sale_client: tuple[Any, str], source: str) -> None:
    client, position_id = sale_client
    pf.close_position('002129', '2026-10-23', 9.5, 200, 12, operation_id='sale-first')
    result = analyze(client, position_id, None if source == 'comparison' else 't_watch')
    before = deepcopy(pf._load())
    response = adopt(client, position_id, result, 't_watch')
    assert response.status_code == 400 and '先核对' in response.text
    assert pf._load() == before


def test_position_sale_review_stops_legacy_watch_adopted_after_sale_without_reviving_it(sale_client: tuple[Any, str]) -> None:
    client, position_id = sale_client
    result = analyze(client, position_id, 't_watch')
    assert adopt(client, position_id, result, 't_watch').status_code == 200
    pf.close_position('002129', '2026-10-23', 9.5, 200, 12, operation_id='sale-first')
    # Shape allowed by the previous release: current-position observation was
    # adopted before acknowledging the sale. Confirmation must not revive it.
    current = worksheet(client, position_id)
    data = pf._load()
    plan = data['position_reviews'][position_id]['review_plan']
    plan['watch'].update(position_snapshot=deepcopy(current['position_snapshot']),
                         trade_revision=current['trade_revision'])
    plan['position_snapshot'] = deepcopy(current['position_snapshot'])
    pf._save(data)
    value = worksheet(client, position_id)
    original_watch = deepcopy(value['review_plan']['watch'])
    response = client.post(f'/api/positions/{position_id}/sale-review', json=review_body(value))
    assert response.status_code == 200, response.text
    confirmed = response.json()['data']
    assert not confirmed['sale_requires_review']
    assert 'watch' not in confirmed['review_plan'] and confirmed['review_plan']['watch_stopped']
    root = pf._load()['position_reviews'][position_id]
    assert root['watch_audit'][-1]['previous'] == original_watch
    assert observe_plan(confirmed, root, current=NOW).reason == 'watch_stopped'


def test_position_sale_review_rejects_second_sale_and_stale_receipt_without_writes(sale_client: tuple[Any, str]) -> None:
    client, position_id = sale_client
    pf.close_position('002129', '2026-10-23', 9.5, 200, 12, operation_id='sale-first')
    first = worksheet(client, position_id)
    assert client.post(f'/api/positions/{position_id}/sale-review', json=review_body(first)).status_code == 200
    pf.close_position('002129', '2026-10-23', 9.4, 100, 12, operation_id='sale-second')
    current = worksheet(client, position_id)
    assert current['sale_requires_review'] and current['recorded_sale']['id'] == 'sale-second'
    before = deepcopy(pf._load())
    for body in (review_body(first), review_body(current) | {'expected_sale_id': 'sale-first'}):
        assert client.post(f'/api/positions/{position_id}/sale-review', json=body).status_code == 409
        assert pf._load() == before
    assert client.post(f'/api/positions/{position_id}/sale-review', json=review_body(current)).status_code == 200


def test_position_sale_review_duplicate_confirmation_does_not_increment_version(sale_client: tuple[Any, str]) -> None:
    client, position_id = sale_client
    pf.close_position('002129', '2026-10-23', 9.5, 200, 12, operation_id='sale-first')
    value = worksheet(client, position_id)
    response = client.post(f'/api/positions/{position_id}/sale-review', json=review_body(value))
    assert response.status_code == 200
    before = deepcopy(pf._load())
    for body in (review_body(value), review_body(response.json()['data'])):
        assert client.post(f'/api/positions/{position_id}/sale-review', json=body).status_code == 409
        assert pf._load() == before


def test_position_sale_review_rejects_risk_change_after_display_without_writes(sale_client: tuple[Any, str]) -> None:
    client, position_id = sale_client
    pf.close_position('002129', '2026-10-23', 9.5, 200, 12, operation_id='sale-first')
    value = worksheet(client, position_id)
    data = pf._load()
    data['risk_rules'][0]['revision'] += 1
    pf._save(data)
    before = deepcopy(pf._load())
    assert client.post(f'/api/positions/{position_id}/sale-review', json=review_body(value)).status_code == 409
    assert pf._load() == before


def test_position_sale_review_rejects_foreign_tenant_and_closed_reopened_position(sale_client: tuple[Any, str]) -> None:
    client, position_id = sale_client
    pf.close_position('002129', '2026-10-23', 9.5, 200, 12, operation_id='sale-first')
    value = worksheet(client, position_id)
    before = deepcopy(pf._load())
    assert client.post(f'/api/positions/{position_id}/sale-review', headers={'X-User-Id': 'sale-foreign'},
                       json=review_body(value)).status_code == 409
    assert not (Path(pf.CACHE_DIR) / 'users' / 'sale-foreign' / 'portfolio.json').exists()
    assert pf._load() == before
    pf.close_position('002129', '2026-10-23', 9.4, 800, 12, operation_id='sale-exit')
    assert client.post(f'/api/positions/{position_id}/sale-review', json=review_body(value)).status_code == 409
    pf.add_holding('002129', 100, 9, '2026-10-23', operation_id='sale-reopen')
    new_id = pf._load()['holdings'][0]['position_id']
    assert new_id != position_id
    before = deepcopy(pf._load())
    assert client.post(f'/api/positions/{position_id}/sale-review', json=review_body(value)).status_code == 409
    fresh = worksheet(client, new_id)
    assert client.post(f'/api/positions/{new_id}/sale-review', json={**guards(fresh), 'expected_sale_id': 'sale-first'}).status_code == 409
    assert pf._load() == before


@pytest.mark.parametrize('patch', [{'expected_sale_id': ''}, {'expected_sale_id': None},
                                 {'expected_revision': ''}, {'unrequested': 'value'}])
def test_position_sale_review_rejects_invalid_request_when_contract_missing(sale_client: tuple[Any, str], patch: dict[str, Any]) -> None:
    client, position_id = sale_client
    pf.close_position('002129', '2026-10-23', 9.5, 200, 12, operation_id='sale-first')
    value = worksheet(client, position_id)
    before = deepcopy(pf._load())
    assert client.post(f'/api/positions/{position_id}/sale-review', json=review_body(value) | patch).status_code == 422
    assert pf._load() == before


def test_position_sale_review_rejects_whitespace_and_missing_sale_without_writes(sale_client: tuple[Any, str]) -> None:
    client, position_id = sale_client
    value = worksheet(client, position_id)
    before = deepcopy(pf._load())
    assert client.post(f'/api/positions/{position_id}/sale-review', json={**guards(value), 'expected_sale_id': 'missing'}).status_code == 409
    assert client.post(f'/api/positions/{position_id}/sale-review', json={**guards(value), 'expected_sale_id': '  '}).status_code == 400
    assert pf._load() == before
