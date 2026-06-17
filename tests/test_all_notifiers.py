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
    assert "%E5%8D%88%E7%9B%98%E5%88%86%E6%9E%90-2026-06-11" in mock_post.call_args.args[0]


def test_send_bark_url_encodes_title_and_body(monkeypatch):
    monkeypatch.setenv("BARK_URL", "https://api.day.app/yourkey")
    from aqsp.notifier import _send_bark

    mock_post = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    with patch("aqsp.notifier.requests.post", mock_post):
        result = _send_bark("# 标题 含 空格\n\n内容/带?特殊#符号")

    assert result is not None
    called_url = mock_post.call_args.args[0]
    assert "%20" in called_url
    assert "%2F" in called_url
    assert "%3F" in called_url


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


def test_send_wechat_marks_business_failure(monkeypatch):
    monkeypatch.setenv("WECHAT_WEBHOOK_URL", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test")
    from aqsp.notifier import _send_wechat

    mock_post = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"errcode": 93000, "errmsg": "invalid markdown"}
    mock_post.return_value = mock_response

    with patch("aqsp.notifier.requests.post", mock_post):
        result = _send_wechat("# 标题\n\n内容")

    assert result is not None
    assert result.ok is False
    assert "invalid markdown" in result.detail


def test_notify_markdown_suppresses_real_delivery_in_codex_session(monkeypatch):
    from aqsp.notifier import notify_markdown

    monkeypatch.setenv("CODEX_THREAD_ID", "thread-1")
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "test_sendkey")

    with patch("aqsp.notifier._should_suppress_real_notifications", return_value=True):
        with patch("aqsp.notifier.requests.post") as mock_post:
            results = notify_markdown("# 收盘研究日报-2026-06-15\n\n内容")

    assert results
    assert results[0].ok is True
    assert results[0].detail == "suppressed in Codex session"
    mock_post.assert_not_called()


def test_notify_markdown_allows_delivery_when_explicitly_enabled(monkeypatch):
    from aqsp.notifier import notify_markdown

    monkeypatch.setenv("CODEX_THREAD_ID", "thread-1")
    monkeypatch.setenv("AQSP_ALLOW_REAL_NOTIFICATIONS", "1")
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "test_sendkey")

    with patch("aqsp.notifier.requests.post") as mock_post:
        results = notify_markdown("# 收盘研究日报-2026-06-15\n\n内容")

    assert results
    mock_post.assert_called_once()


def test_notify_markdown_via_config_fanout_splits_summary_and_full_channels(monkeypatch):
    from aqsp.notifier import NotifyResult, notify_markdown_via_config

    sent: list[tuple[str, str]] = []

    def make_sender(channel: str):
        def _sender(markdown: str) -> NotifyResult:
            sent.append((channel, markdown))
            return NotifyResult(channel, True, "HTTP 200")

        return _sender

    monkeypatch.setattr("aqsp.notifier._send_serverchan", make_sender("serverchan"))
    monkeypatch.setattr("aqsp.notifier._send_wechat", make_sender("wechat"))
    monkeypatch.setattr("aqsp.notifier._send_bark", make_sender("bark"))
    monkeypatch.setattr("aqsp.notifier._send_pushplus", make_sender("pushplus"))
    monkeypatch.setattr("aqsp.notifier._send_telegram", make_sender("telegram"))
    monkeypatch.setattr("aqsp.notifier._send_feishu", make_sender("feishu"))
    monkeypatch.setattr("aqsp.notifier._send_dingtalk", make_sender("dingtalk"))
    monkeypatch.setattr("aqsp.notifier._send_discord", make_sender("discord"))
    monkeypatch.setattr("aqsp.notifier._send_slack", make_sender("slack"))
    monkeypatch.setattr(
        "aqsp.notifier._send_generic_webhook", make_sender("generic_webhook")
    )

    results = notify_markdown_via_config(
        "# 完整版\n\n## 研究\n\n完整内容",
        mode="fanout",
        summary_markdown="# 摘要版\n\n## 结论\n\n摘要内容",
    )

    assert len(results) == 10
    summary_channels = {"serverchan", "wechat", "bark", "pushplus", "telegram"}
    full_channels = {"feishu", "dingtalk", "discord", "slack", "generic_webhook"}
    for channel, body in sent:
        if channel in summary_channels:
            assert "摘要版" in body
            assert "完整内容" not in body
        if channel in full_channels:
            assert "完整版" in body
            assert "完整内容" in body


def test_notify_markdown_via_config_summary_only_uses_summary_channels(monkeypatch):
    from aqsp.notifier import NotifyResult, notify_markdown_via_config

    sent: list[str] = []

    def make_sender(channel: str):
        def _sender(markdown: str) -> NotifyResult:
            sent.append(f"{channel}:{markdown}")
            return NotifyResult(channel, True, "HTTP 200")

        return _sender

    monkeypatch.setattr("aqsp.notifier._send_serverchan", make_sender("serverchan"))
    monkeypatch.setattr("aqsp.notifier._send_wechat", make_sender("wechat"))
    monkeypatch.setattr("aqsp.notifier._send_bark", make_sender("bark"))
    monkeypatch.setattr("aqsp.notifier._send_pushplus", make_sender("pushplus"))
    monkeypatch.setattr("aqsp.notifier._send_telegram", make_sender("telegram"))
    monkeypatch.setattr("aqsp.notifier._send_feishu", make_sender("feishu"))

    results = notify_markdown_via_config(
        "# 完整版\n\n完整内容",
        mode="summary",
        summary_markdown="# 摘要版\n\n摘要内容",
    )

    assert [result.channel for result in results] == [
        "serverchan",
        "wechat",
        "bark",
        "pushplus",
        "telegram",
    ]
    assert all("摘要版" in item for item in sent)
    assert all("完整内容" not in item for item in sent)


def test_notify_markdown_via_config_summary_falls_back_to_full_channels_when_needed(
    monkeypatch,
):
    from aqsp.notifier import NotifyResult, notify_markdown_via_config

    def empty_sender(_markdown: str) -> NotifyResult | None:
        return None

    sent: list[tuple[str, str]] = []

    def make_sender(channel: str):
        def _sender(markdown: str) -> NotifyResult:
            sent.append((channel, markdown))
            return NotifyResult(channel, True, "HTTP 200")

        return _sender

    monkeypatch.setattr("aqsp.notifier._send_serverchan", empty_sender)
    monkeypatch.setattr("aqsp.notifier._send_wechat", empty_sender)
    monkeypatch.setattr("aqsp.notifier._send_bark", empty_sender)
    monkeypatch.setattr("aqsp.notifier._send_pushplus", empty_sender)
    monkeypatch.setattr("aqsp.notifier._send_telegram", empty_sender)
    monkeypatch.setattr("aqsp.notifier._send_feishu", make_sender("feishu"))
    monkeypatch.setattr("aqsp.notifier._send_dingtalk", make_sender("dingtalk"))
    monkeypatch.setattr("aqsp.notifier._send_discord", make_sender("discord"))
    monkeypatch.setattr("aqsp.notifier._send_slack", make_sender("slack"))
    monkeypatch.setattr(
        "aqsp.notifier._send_generic_webhook", make_sender("generic_webhook")
    )

    results = notify_markdown_via_config(
        "# 完整版\n\n内容",
        mode="summary",
        summary_markdown="# 摘要版\n\n摘要内容",
    )

    assert [result.channel for result in results] == [
        "feishu",
        "dingtalk",
        "discord",
        "slack",
        "generic_webhook",
    ]
    assert all("摘要内容" in item[1] for item in sent)


def test_dispatch_gate_notification_builds_and_routes_summary(monkeypatch):
    from aqsp.notification_runtime import dispatch_gate_notification
    from aqsp.notifier import NotifyResult

    seen: dict[str, str] = {}

    def fake_notify(markdown: str):
        seen["markdown"] = markdown
        return [NotifyResult("serverchan", True, "HTTP 200")]

    monkeypatch.setattr(
        "aqsp.notification_runtime.notify_gate_markdown",
        fake_notify,
    )

    results = dispatch_gate_notification(
        run_date="2026-06-15",
        gate_reasons=["冷启动未满: 0/30 个独立信号日", "DSR 未过门: 0.0（需 >1.0）"],
        next_actions=["继续按日运行主链，先把冷启动样本积累到 30 个独立信号日。"],
        mode="summary",
    )

    assert len(results) == 1
    assert seen["markdown"].startswith("# 通知未放行-2026-06-15")


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
