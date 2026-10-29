"""Candidate monitor checks reuse one source batch and never revive old notices."""
# ruff: noqa: F811 -- reuse the isolated candidate API fixture
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace
from typing import Any

import pytest

import candidate_monitor as monitor
import candidate_observation as resources
from aqsp import notification_runtime as runtime
from test_candidate_observation import NOW, adoption, analyze, env  # noqa: F401


def adopt(env: tuple) -> None:
    value, result = analyze(env)
    assert env[0].post('/api/candidate-observations/600000/adopt', json=adoption(value, result)).status_code == 200


def successful_channel(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    def dispatch(markdown: str, **kwargs: Any) -> list[SimpleNamespace]:
        sent.append(markdown)
        runtime.mark_notification_sent(kind=kwargs['kind'], markdown=markdown, state_path=kwargs['state_path'])
        return [SimpleNamespace(ok=True)]

    monkeypatch.setattr(runtime, 'dispatch_notification_once', dispatch)
    return sent


def test_candidate_monitor_reuses_batch_quote_when_notice_revalidated(env: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    adopt(env)
    env[2]['price'] = 9.8
    calls = []

    def quote(codes: list[str]) -> dict[str, Any]:
        calls.append(list(codes))
        return {code: deepcopy(env[2]) for code in codes}

    monkeypatch.setattr(monitor.astock, 'tencent_quote', quote)
    sent = successful_channel(monkeypatch)
    assert monitor.check_candidates(notify=True)['notified'] == 1
    assert calls == [['600000']]
    assert len(sent) == 1
    journal = json.loads((resources.root() / 'candidate-observation-delivery.json').read_text())
    assert '_source_quote' not in next(iter(journal['batches'].values()))['candidate']


def test_candidate_monitor_rejects_batch_quote_when_source_expires_before_send(env: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    adopt(env)
    env[2]['price'] = 9.8
    calls = []

    def quote(codes: list[str]) -> dict[str, Any]:
        calls.append(list(codes))
        return {code: deepcopy(env[2]) for code in codes}

    monkeypatch.setattr(monitor.astock, 'tencent_quote', quote)
    sent = successful_channel(monkeypatch)
    deliver = monitor.deliver

    def delayed(batch: dict[str, Any]) -> bool:
        later = NOW + timedelta(minutes=31)
        monkeypatch.setattr(monitor, 'now_shanghai', lambda: later)
        monkeypatch.setattr(resources, 'now_shanghai', lambda: later)
        return deliver(batch)

    monkeypatch.setattr(monitor, 'deliver', delayed)
    assert monitor.check_candidates(notify=True)['notified'] == 0
    assert not sent and calls == [['600000']]


def test_candidate_monitor_rearms_only_new_check_when_failed_unknown_notice_has_expired(env: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    adopt(env)
    env[2].clear()
    attempts = []
    monkeypatch.setattr(runtime, 'dispatch_notification_once', lambda *args, **kwargs: attempts.append(args) or [SimpleNamespace(ok=False)])
    assert monitor.check_candidates(notify=True)['notified'] == 0
    assert len(attempts) == 1
    later = NOW + timedelta(minutes=16)
    monkeypatch.setattr(monitor, 'now_shanghai', lambda: later)
    monkeypatch.setattr(resources, 'now_shanghai', lambda: later)
    sent = successful_channel(monkeypatch)
    # First cancel stale frozen content; do not send the old check as a new one.
    assert monitor.check_candidates(notify=True)['notified'] == 0
    journal_path = resources.root() / 'candidate-observation-delivery.json'
    frozen = next(iter(json.loads(journal_path.read_text())['batches'].values()))
    assert frozen['cancelled'] and not sent
    assert frozen['candidate']['observation']['observed_at'] == NOW.isoformat()
    # A later valid observation may replace only the cancelled unsent content.
    assert monitor.check_candidates(notify=True)['notified'] == 1
    assert len(sent) == 1 and f'检查时点：{later.isoformat()}' in sent[0]
    assert monitor.check_candidates(notify=True)['notified'] == 0
    assert len(sent) == 1


@pytest.mark.parametrize('observed_at', [
    (NOW - timedelta(minutes=16)).isoformat(),
    (NOW + timedelta(minutes=1)).isoformat(),
    '2026-10-27T10:00:00',
    'invalid',
])
def test_candidate_monitor_rejects_frozen_unavailable_notice_when_check_time_invalid(env: tuple, monkeypatch: pytest.MonkeyPatch, observed_at: str) -> None:
    adopt(env)
    env[2].clear()
    batches = []
    monkeypatch.setattr(monitor, 'deliver', lambda batch: batches.append(deepcopy(batch)) or True)
    monitor.check_candidates(notify=True)
    batch = batches[0]
    assert batch['observation']['state'] == 'evidence_unavailable'
    batch['observation']['observed_at'] = observed_at
    assert not monitor._validate(batch)
