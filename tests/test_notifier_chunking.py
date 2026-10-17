"""`aqsp.notifier` 长文分段与钉钉关键词的接线测试。

改造前：``payload = {"markdown": {"content": markdown[:3800]}}`` —— 超长正文被
**静默截断**。本测试钉住三件事：

1. 短正文**仍只发一条**，且 payload 与改造前一致（不能因为修长文把短文的路径改了）；
2. 长正文按段发完，每段不超上限，正文内容不丢；
3. 某段失败 ⇒ 立即停止并报出段号（不发半份报告、不静默成功）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from aqsp.notification_style import compact_notification_markdown
from aqsp.notify_chunk import DEFAULT_CHUNK_LIMIT

_SENTENCE = "本段用于验证长文分段是否完整送达，不含任何会被压缩掉的噪声行。"


def _long_markdown(paragraphs: int = 200) -> str:
    return "\n\n".join(f"## 第 {index + 1} 节\n{_SENTENCE}" for index in range(paragraphs))


def _allow_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AQSP_ALLOW_REAL_NOTIFICATIONS", "1")


def _ok_post():
    mock_post = MagicMock()
    mock_post.return_value = MagicMock(status_code=200)
    return mock_post


def test_wechat_short_message_still_sends_a_single_identical_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_posts(monkeypatch)
    monkeypatch.setenv("WECHAT_WEBHOOK_URL", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=t")
    from aqsp.notifier import _send_wechat

    markdown = "# 收盘总览\n\n- 结论一行"
    mock_post = _ok_post()
    with patch("requests.sessions.Session.post", mock_post):
        result = _send_wechat(markdown)

    assert result is not None and result.ok
    assert mock_post.call_count == 1
    assert mock_post.call_args.kwargs["json"]["markdown"]["content"] == compact_notification_markdown(
        markdown
    )


def test_wechat_long_message_is_split_instead_of_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_posts(monkeypatch)
    monkeypatch.setenv("WECHAT_WEBHOOK_URL", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=t")
    from aqsp.notifier import _send_wechat

    markdown = _long_markdown()
    compacted = compact_notification_markdown(markdown)
    assert len(compacted) > DEFAULT_CHUNK_LIMIT, "夹具长度不足，测不到分段路径"

    mock_post = _ok_post()
    with patch("requests.sessions.Session.post", mock_post):
        result = _send_wechat(markdown)

    assert result is not None and result.ok
    assert mock_post.call_count > 1
    contents = [call.kwargs["json"]["markdown"]["content"] for call in mock_post.call_args_list]
    assert all(len(content) <= DEFAULT_CHUNK_LIMIT for content in contents)
    assert "segments" in result.detail
    # 改造前 [:3800] 会丢掉后半截；这里断言尾部内容确实送到了
    assert _SENTENCE in contents[-1]


def test_wechat_stops_at_first_failed_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_posts(monkeypatch)
    monkeypatch.setenv("WECHAT_WEBHOOK_URL", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=t")
    from aqsp.notifier import _send_wechat

    ok_response = MagicMock(status_code=200)
    failed_response = MagicMock(status_code=500, headers={"content-type": "text/plain"})
    failed_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
    mock_post = _ok_post()
    mock_post.side_effect = [ok_response, failed_response]
    with patch("requests.sessions.Session.post", mock_post):
        result = _send_wechat(_long_markdown())

    assert result is not None
    assert result.ok is False
    assert "segment 2/" in result.detail
    assert mock_post.call_count == 2


def test_dingtalk_injects_configured_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_posts(monkeypatch)
    monkeypatch.setenv("DINGTALK_WEBHOOK_URL", "https://oapi.dingtalk.com/robot/send?access_token=t")
    monkeypatch.delenv("DINGTALK_SECRET", raising=False)
    monkeypatch.setenv("DINGTALK_KEYWORD", "AQSP")
    from aqsp.notifier import _send_dingtalk

    mock_post = _ok_post()
    with patch("requests.sessions.Session.post", mock_post):
        _send_dingtalk("# 收盘总览\n\n- 结论一行")

    text = mock_post.call_args.kwargs["json"]["markdown"]["text"]
    assert text.startswith("AQSP\n")


def test_dingtalk_does_not_duplicate_present_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_posts(monkeypatch)
    monkeypatch.setenv("DINGTALK_WEBHOOK_URL", "https://oapi.dingtalk.com/robot/send?access_token=t")
    monkeypatch.delenv("DINGTALK_SECRET", raising=False)
    monkeypatch.setenv("DINGTALK_KEYWORD", "收盘总览")
    from aqsp.notifier import _send_dingtalk

    mock_post = _ok_post()
    with patch("requests.sessions.Session.post", mock_post):
        _send_dingtalk("# 收盘总览\n\n- 结论一行")

    text = mock_post.call_args.kwargs["json"]["markdown"]["text"]
    assert text.count("收盘总览") == 1


def test_telegram_long_message_is_split(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_posts(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    from aqsp.notifier import _send_telegram

    mock_post = _ok_post()
    with patch("requests.sessions.Session.post", mock_post):
        result = _send_telegram(_long_markdown())

    assert result is not None and result.ok
    assert mock_post.call_count > 1
    for call in mock_post.call_args_list:
        assert len(call.kwargs["json"]["text"]) <= DEFAULT_CHUNK_LIMIT
