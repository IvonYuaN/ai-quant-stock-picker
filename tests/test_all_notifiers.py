from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_send_dingtalk_returns_none_when_no_url(monkeypatch):
    monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)
    from aqsp.notifier import _send_dingtalk

    result = _send_dingtalk("test")
    assert result is None


def test_send_dingtalk_sends_with_secret(monkeypatch):
    monkeypatch.setenv(
        "DINGTALK_WEBHOOK_URL", "https://oapi.dingtalk.com/robot/send?access_token=test"
    )
    monkeypatch.setenv("DINGTALK_SECRET", "test_secret")
    from aqsp.notifier import _send_dingtalk

    mock_post = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    with patch("aqsp.notifier.requests.post", mock_post):
        result = _send_dingtalk("test message")

    assert result is not None
    assert result.channel == "dingtalk"
    assert result.ok is True


def test_send_feishu_uses_interactive_card(monkeypatch):
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://open.feishu.cn/webhook/test")
    from aqsp.notifier import _send_feishu

    mock_post = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    markdown = "# 收盘总览\n\n## 🧭 一眼看懂\n\n**🎯 今日结论**：仅供研究复核"
    with patch("aqsp.notifier.requests.post", mock_post):
        result = _send_feishu(markdown)

    assert result is not None
    assert result.channel == "feishu"
    payload = mock_post.call_args.kwargs["json"]
    assert payload["msg_type"] == "interactive"
    assert payload["card"]["header"]["title"]["content"] == "收盘总览"
    assert payload["card"]["header"]["template"] == "turquoise"
    assert payload["card"]["elements"][0]["tag"] == "markdown"
    assert "## 结论" in payload["card"]["elements"][0]["content"]
    assert "- 今日结论: 仅供研究复核" in payload["card"]["elements"][0]["content"]


def test_send_dingtalk_uses_markdown_title(monkeypatch):
    monkeypatch.setenv(
        "DINGTALK_WEBHOOK_URL", "https://oapi.dingtalk.com/robot/send?access_token=test"
    )
    monkeypatch.delenv("DINGTALK_SECRET", raising=False)
    from aqsp.notifier import _send_dingtalk

    mock_post = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    with patch("aqsp.notifier.requests.post", mock_post):
        result = _send_dingtalk("# 午盘分析-2026-06-11\n\n内容")

    assert result is not None
    payload = mock_post.call_args.kwargs["json"]
    assert payload["markdown"]["title"] == "午盘分析-2026-06-11"


def test_send_bark_returns_none_when_no_url(monkeypatch):
    monkeypatch.delenv("BARK_URL", raising=False)
    from aqsp.notifier import _send_bark

    result = _send_bark("test")
    assert result is None


def test_send_bark_sends(monkeypatch):
    monkeypatch.setenv("BARK_URL", "https://api.day.app/yourkey")
    from aqsp.notifier import _send_bark

    mock_post = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    with patch("aqsp.notifier.requests.post", mock_post):
        result = _send_bark("# 午盘分析-2026-06-11\n\ntest message")

    assert result is not None
    assert result.channel == "bark"
    assert "午盘分析-2026-06-11" in mock_post.call_args.args[0]


def test_send_pushplus_returns_none_when_no_token(monkeypatch):
    monkeypatch.delenv("PUSHPLUS_TOKEN", raising=False)
    from aqsp.notifier import _send_pushplus

    result = _send_pushplus("test")
    assert result is None


def test_send_pushplus_sends(monkeypatch):
    monkeypatch.setenv("PUSHPLUS_TOKEN", "test_token")
    from aqsp.notifier import _send_pushplus

    mock_post = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    with patch("aqsp.notifier.requests.post", mock_post):
        result = _send_pushplus("# 午盘分析-2026-06-11\n\ntest message")

    assert result is not None
    assert result.channel == "pushplus"
    payload = mock_post.call_args.kwargs["json"]
    assert payload["title"] == "午盘分析-2026-06-11"


def test_send_discord_returns_none_when_no_url(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    from aqsp.notifier import _send_discord

    result = _send_discord("test")
    assert result is None


def test_send_discord_sends(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/...")
    from aqsp.notifier import _send_discord

    mock_post = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    with patch("aqsp.notifier.requests.post", mock_post):
        result = _send_discord("test message")

    assert result is not None
    assert result.channel == "discord"


def test_send_slack_returns_none_when_no_url(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    from aqsp.notifier import _send_slack

    result = _send_slack("test")
    assert result is None


def test_send_slack_sends(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/...")
    from aqsp.notifier import _send_slack

    mock_post = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    with patch("aqsp.notifier.requests.post", mock_post):
        result = _send_slack("test message")

    assert result is not None
    assert result.channel == "slack"
