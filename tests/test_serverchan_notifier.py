from __future__ import annotations

from unittest.mock import patch


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
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "test_sendkey")
    from aqsp.notifier import notify_markdown

    with patch("aqsp.notifier.requests.post") as mock_post:
        result = notify_markdown("test message")

    assert len(result) == 1
    assert result[0].channel == "serverchan"
    assert result[0].ok is True
    mock_post.assert_called_once()
    assert "sctapi.ftqq.com/test_sendkey.send" in mock_post.call_args.args[0]


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
    assert "## 数据源状态" in merged
    assert "## 核心结论" in merged
    assert merged.index("## 核心结论") < merged.index("## 数据源状态")


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
    assert merged.index("## 数据源状态") < merged.index("## 核心结论")
    assert "降低信任度" in merged
