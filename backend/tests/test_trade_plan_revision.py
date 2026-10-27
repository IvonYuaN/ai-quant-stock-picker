"""Actual fees and manual associations invalidate stale plan/model writes."""
from __future__ import annotations

# ruff: noqa: F811 -- shared synthetic fixture injected by pytest name
from dataclasses import asdict

import portfolio as pf
import position_plan_service as service
import trade_journal as journal
from test_position_action_plan import PARAMS, plan_client  # noqa: F401


def test_trade_plan_fee_correction_invalidates_calculation_and_old_guard(plan_client):
    client, _ = plan_client
    holding = pf._load()['holdings'][0]
    position_id, code = holding['position_id'], holding['code']
    revision = journal.read_journal(code)['position']['revision']
    fill = journal.TradeInput('sale', 'sell', code, '2026-10-23', 9.5, 100, None, position_id, revision)
    assert client.post('/api/portfolio/trades', json=asdict(fill)).status_code == 200
    ws = service.read_position_plan(position_id)
    guards = {'expected_position': ws['position_snapshot'], 'expected_revision': ws['revision']}
    assert client.post(f'/api/positions/{position_id}/plan', json={'choice': 't_watch', 'parameters': PARAMS, **guards}).status_code == 200
    before = service.read_position_plan(position_id)
    assert before['scenario'] is not None
    trade_view = journal.read_journal(code)
    journal.update_fees('sale', 2, trade_view['revision'], 'fee')
    after = service.read_position_plan(position_id)
    assert after['position_snapshot'] == before['position_snapshot']
    assert after['revision'] != before['revision']
    assert after['scenario'] is None and '费用' in after['scenario_issue']
    response = client.post(f'/api/positions/{position_id}/plan', json={
        'choice': 'hold', 'expected_position': before['position_snapshot'], 'expected_revision': before['revision']})
    assert response.status_code == 409
