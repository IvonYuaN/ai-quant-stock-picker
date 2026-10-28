"""A run event is evidence of that task/session, never market freshness."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from aqsp.web.research_status import intraday_run

NOW = datetime(2026, 10, 28, 18, tzinfo=ZoneInfo('Asia/Shanghai'))


@pytest.mark.parametrize('state', ['running', 'failed', 'partial_failed', 'completed'])
def test_research_status_preserves_same_day_intraday_event_when_identity_known(state: str) -> None:
    result = intraday_run({'status': state, 'task_id': 'intraday', 'updated_at': '2026-10-28T06:00:00Z',
                           'reason': '合成运行记录'}, '2026-10-28', current=NOW)
    assert result.status == state and result.updated_at == '2026-10-28T14:00:00+08:00'
    assert result.detail == '合成运行记录'


@pytest.mark.parametrize('patch', [
    {'updated_at': ''}, {'updated_at': '2026-10-28T14:00:00'},
    {'updated_at': '2026-10-28T19:00:00+08:00'}, {'updated_at': '2026-10-27T14:00:00+08:00'},
    {'task_id': 'main_chain'}, {'status': 'invented'},
    {'task_id': 'intraday-invented'}, {'task_id': 'intraday-2026-10-27-1305'},
    {'task_id': 'intraday-2026-10-28-2500'}, {'task_id': 'intraday-2026-10-28-1500'},
])
def test_research_status_keeps_unknown_when_task_or_source_time_unverifiable(patch: dict) -> None:
    raw = {'task_id': 'intraday', 'status': 'completed', 'updated_at': '2026-10-28T14:00:00+08:00', **patch}
    assert intraday_run(raw, '2026-10-28', current=NOW).status == 'unknown'


def test_research_status_keeps_unknown_when_record_missing() -> None:
    assert intraday_run({}, '2026-10-28', current=NOW).status == 'unknown'


def test_research_status_accepts_dated_task_when_event_follows_same_session() -> None:
    result = intraday_run({'task_id': 'intraday-2026-10-28-1305', 'status': 'completed',
                           'updated_at': '2026-10-28T14:00:00+08:00'}, '2026-10-28', current=NOW)
    assert result.status == 'completed'
