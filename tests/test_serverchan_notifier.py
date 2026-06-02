from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_send_notification_delegates_to_markdown(monkeypatch):
    from aqsp.notifier import NotifyResult, send_notification

    with patch(
        "aqsp.notifier.notify_markdown",
        return_value=[NotifyResult("serverchan", True, "HTTP 200")],
    ) as mock_notify:
        result = send_notification("收盘复盘", "今日无可执行标的")

    assert result[0].channel == "serverchan"
    mock_notify.assert_called_once()
    assert "# 收盘复盘" in mock_notify.call_args[0][0]


def test_send_serverchan_returns_none_when_no_key(monkeypatch):
    monkeypatch.delenv("SERVERCHAN_SENDKEY", raising=False)
    from aqsp.notifier import _send_serverchan

    result = _send_serverchan("test message")
    assert result is None


def test_send_serverchan_returns_result_when_key_present(monkeypatch):
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "test_sendkey")
    from aqsp.notifier import _send_serverchan

    mock_post = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    with patch("aqsp.notifier.requests.post", mock_post):
        result = _send_serverchan("test message")

    assert result is not None
    assert result.channel == "serverchan"
    assert result.ok is True
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "test_sendkey" in call_kwargs[0][0]


def test_send_serverchan_truncates_long_content(monkeypatch):
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "test_sendkey")
    from aqsp.notifier import _send_serverchan

    mock_post = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    long_message = "x" * 5000

    with patch("aqsp.notifier.requests.post", mock_post):
        _send_serverchan(long_message)

    call_kwargs = mock_post.call_args
    payload = call_kwargs[1]["data"]
    assert len(payload["desp"]) <= 4000


def test_send_serverchan_handles_error(monkeypatch):
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "test_sendkey")
    from aqsp.notifier import _send_serverchan

    import requests

    with patch(
        "aqsp.notifier.requests.post",
        side_effect=requests.RequestException("network error"),
    ):
        result = _send_serverchan("test message")

    assert result is not None
    assert result.channel == "serverchan"
    assert result.ok is False
    assert "network error" in result.detail
