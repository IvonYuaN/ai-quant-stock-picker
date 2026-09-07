from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_fake_session(status_code: int = 200, body: dict | None = None) -> MagicMock:
    """构造一个 mock HTTP session，模拟 build_http_session() 的契约。

    notifier._post() 走 build_http_session() 返回的 session.post，
    所以测试必须 patch session factory —— 直接 patch requests.post
    已脱靶（PR #76 合并后，notifier 改用统一 factory）。
    """
    response = MagicMock()
    response.status_code = status_code
    response.headers = {"content-type": "application/json"}
    response.json.return_value = body if body is not None else {"code": 0}
    response.raise_for_status = MagicMock()

    session = MagicMock()
    session.post.return_value = response
    session.close = MagicMock()
    return session


def test_send_notification_delegates_to_markdown(monkeypatch):
    from aqsp.notifier import NotifyResult, send_notification

    with patch(
        "aqsp.notifier.notify_markdown",
        return_value=[NotifyResult("telegram", True, "HTTP 200")],
    ) as mock_notify:
        result = send_notification("收盘复盘", "今日无可执行标的")

    assert result[0].channel == "telegram"
    mock_notify.assert_called_once()
    assert "# 收盘复盘" in mock_notify.call_args[0][0]


def test_notify_markdown_sends_serverchan_when_sendkey_present(monkeypatch):
    monkeypatch.setenv("AQSP_ALLOW_REAL_NOTIFICATIONS", "1")
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "test_sendkey")
    from aqsp.notifier import notify_markdown

    fake_session = _make_fake_session(body={"code": 0})
    with patch("aqsp.notifier.build_http_session", return_value=fake_session):
        result = notify_markdown("test message")

    assert len(result) == 1
    assert result[0].channel == "serverchan"
    assert result[0].ok is True
    fake_session.post.assert_called_once()
    assert "sctapi.ftqq.com/test_sendkey.send" in fake_session.post.call_args.args[0]


def test_serverchan_uses_markdown_title(monkeypatch):
    monkeypatch.setenv("AQSP_ALLOW_REAL_NOTIFICATIONS", "1")
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "test_sendkey")
    from aqsp.notifier import _send_serverchan

    fake_session = _make_fake_session(body={"code": 0})
    with patch("aqsp.notifier.build_http_session", return_value=fake_session):
        result = _send_serverchan("# 午盘分析-2026-06-11\n\n内容")

    assert result is not None
    payload = fake_session.post.call_args.kwargs["data"]
    assert payload["title"] == "午盘分析-2026-06-11"


def test_prepend_source_status_banner_keeps_title_before_source_status():
    from aqsp.notifier import prepend_source_status_banner

    content = "# 收盘总览\n\n## 核心结论\n- 总体状态: 成功"
    merged = prepend_source_status_banner(
        content,
        {
            "requested_source": "auto",
            "actual_source": "eastmoney",
            "health_label": "healthy",
            "health_message": "eastmoney 健康",
        },
    )

    assert merged.startswith("# 收盘总览")
    assert merged.count("# 收盘总览") == 1
    assert "## 数据" in merged
    assert "## 结论" in merged
    assert merged.index("## 结论") < merged.index("## 数据")


def test_prepend_source_status_banner_moves_degraded_source_before_content():
    from aqsp.notifier import prepend_source_status_banner

    content = "# 收盘总览\n\n## 核心结论\n- 总体状态: 成功"
    merged = prepend_source_status_banner(
        content,
        {
            "requested_source": "auto",
            "actual_source": "eastmoney",
            "health_label": "fallback",
            "health_message": "fallback 到 eastmoney",
        },
    )

    assert merged.startswith("# 收盘总览")
    assert merged.index("## 数据") < merged.index("## 结论")
    assert "需人工复核" in merged


def test_format_notify_results_formats_all_channels():
    from aqsp.notifier import NotifyResult, format_notify_results

    lines = format_notify_results(
        [
            NotifyResult("serverchan", True, "HTTP 200"),
            NotifyResult("wechat", False, "HTTP 500"),
        ],
        prefix="daily notify",
    )

    assert lines == [
        "daily notify serverchan: ok (HTTP 200)",
        "daily notify wechat: failed (HTTP 500)",
    ]


def test_format_notify_results_handles_empty_channels():
    from aqsp.notifier import format_notify_results

    assert format_notify_results([], prefix="daily notify") == [
        "daily notify skipped: No notification channel configured."
    ]
