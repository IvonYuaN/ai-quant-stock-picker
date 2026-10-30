"""Upstream errors remain bounded and actionable without leaking provider text."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import position_discussion as discussion


@pytest.mark.parametrize('code,kind', [
    ('insufficient_quota', 'quota_exhausted'), ('rate_limit_exceeded', 'rate_limited'),
    ('overloaded_error', 'provider_busy'), ('model_not_found', 'model_unavailable'),
    ('invalid_api_key', 'access_denied'), ('private-key', 'upstream_status'),
])
def test_model_upstream_classifies_explicit_code_when_provider_reports_error(code: str, kind: str) -> None:
    error = discussion.upstream_failure(429, json.dumps({'error': {'code': code, 'message': 'private-key https://private.invalid'}}).encode(), '30')
    failure = discussion._model_failure(error)
    assert failure['failure_kind'] == kind
    assert failure['retry_after_seconds'] == 30
    assert 'private' not in json.dumps(failure)
    assert 'private' not in str(error)


@pytest.mark.parametrize('body', [b'', b'not-json', b'[]', b'null', b'{"error":"private-key"}',
                                   b'{"error":{"message":"insufficient_quota"}}', b'{"error":{"code":[]}}', b'\xff'])
def test_model_upstream_keeps_unknown_reason_when_only_status_is_known(body: bytes) -> None:
    failure = discussion._model_failure(discussion.upstream_failure(429, body, None))
    assert failure['failure_kind'] == 'upstream_status'
    assert 'HTTP 429' in failure['issue'] and '未提供可识别原因' in failure['issue']
    assert '额度不足' not in failure['issue']


@pytest.mark.parametrize('header,expected', [('0', 0), ('86400', 86400), ('86401', None), ('99999999999', None),
                                            ('-1', None), ('1.5', None), ('NaN', None), ('٣', None), ('private-key', None)])
def test_model_upstream_accepts_bounded_seconds_when_retry_after_is_numeric(header: str, expected: int | None) -> None:
    assert discussion.upstream_failure(429, b'', header).retry_after_seconds == expected


@pytest.mark.parametrize('status,kind', [(401, 'access_denied'), (403, 'access_denied'), (503, 'provider_busy'), (500, 'upstream_status')])
def test_model_upstream_reports_status_category_when_body_is_unavailable(status: int, kind: str) -> None:
    assert discussion.upstream_failure(status, b'', None).kind == kind


@pytest.mark.parametrize('oversized', [False, True])
def test_model_upstream_reads_error_once_when_http_rejects_request(monkeypatch, oversized: bool) -> None:
    calls = []
    body = json.dumps({'error': {'code': 'insufficient_quota', 'message': 'private-key'}}).encode()
    if oversized:
        body += b' ' * 100_001
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, content=body, headers={'Retry-After': '12'})
    client_class = httpx.AsyncClient
    monkeypatch.setattr(discussion.httpx, 'AsyncClient', lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(discussion.ModelHTTPError) as captured:
        asyncio.run(discussion.call_model(discussion.ModelEndpoint('https://fixture.invalid', 'private-key', 'model'), {}, 1, discussion.DiscussionLimits()))
    assert len(calls) == 1
    result = discussion._model_failure(captured.value)
    assert result['failure_kind'] == ('upstream_status' if oversized else 'quota_exhausted')
    assert result['retry_after_seconds'] == 12 and 'private-key' not in str(result)


def test_model_upstream_classifies_type_when_provider_omits_code() -> None:
    assert discussion.upstream_failure(429, b'{"error":{"type":"rate_limit_exceeded"}}', None).kind == 'rate_limited'


@pytest.mark.parametrize('stalled', [False, True])
def test_model_upstream_preserves_boundaries_when_error_stream_breaks(monkeypatch, stalled: bool) -> None:
    closed = []
    class ErrorStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            if stalled:
                await asyncio.sleep(5)
            raise httpx.ReadError('private-key')
            yield b''
        async def aclose(self) -> None:
            closed.append(True)
    client_class = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(429, stream=ErrorStream()))
    monkeypatch.setattr(discussion.httpx, 'AsyncClient', lambda **kwargs: client_class(transport=transport, **kwargs))
    with pytest.raises(TimeoutError if stalled else discussion.ModelHTTPError) as captured:
        asyncio.run(discussion.call_model(discussion.ModelEndpoint('https://fixture.invalid', 'private-key', 'model'), {}, .02, discussion.DiscussionLimits()))
    assert closed and 'private-key' not in str(discussion._model_failure(captured.value))
