from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

from aqsp.data.source_health import notification_level_for_health_label


@dataclass(frozen=True)
class NotifyResult:
    channel: str
    ok: bool
    detail: str


def prepend_source_status_banner(
    markdown: str,
    source_status: dict[str, Any] | None = None,
) -> str:
    if not source_status:
        return markdown
    requested = str(source_status.get("requested_source", "") or "")
    actual = str(source_status.get("actual_source", "") or "")
    label = str(source_status.get("health_label", "") or "unknown")
    message = str(source_status.get("health_message", "") or "暂无说明")
    notify_level = notification_level_for_health_label(label)
    route = actual or requested or "unknown"
    if requested and actual and requested != actual:
        route = f"{requested} -> {actual}"
    banner = (
        "## 数据源状态\n\n"
        f"- 通知级别: **{notify_level}**\n"
        f"- 健康: **{label}**\n"
        f"- 路径: **{route}**\n"
        f"- 说明: {message}\n"
    )
    if label in {"fallback", "degraded", "cold_start"}:
        banner += "- 提示: 本次结果请降低信任度，优先人工复核。\n"
    return f"{banner}\n{markdown}"


def notify_markdown(markdown: str) -> list[NotifyResult]:
    results: list[NotifyResult] = []
    for sender in (_send_telegram, _send_wechat, _send_feishu, _send_generic_webhook):
        result = sender(markdown)
        if result is not None:
            results.append(result)
    return results


def _send_telegram(markdown: str) -> NotifyResult | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": markdown[:3900], "parse_mode": "Markdown"}
    return _post("telegram", url, json=payload)


def _send_wechat(markdown: str) -> NotifyResult | None:
    url = os.getenv("WECHAT_WEBHOOK_URL", "").strip()
    if not url:
        return None
    payload = {"msgtype": "markdown", "markdown": {"content": markdown[:3800]}}
    return _post("wechat", url, json=payload)


def _send_feishu(markdown: str) -> NotifyResult | None:
    url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not url:
        return None
    payload = {"msg_type": "text", "content": {"text": markdown[:3800]}}
    return _post("feishu", url, json=payload)


def _send_generic_webhook(markdown: str) -> NotifyResult | None:
    url = os.getenv("GENERIC_WEBHOOK_URL", "").strip()
    if not url:
        return None
    return _post("generic_webhook", url, json={"text": markdown})


def _post(channel: str, url: str, **kwargs: object) -> NotifyResult:
    try:
        response = requests.post(url, timeout=15, **kwargs)
        response.raise_for_status()
    except requests.RequestException as exc:
        return NotifyResult(channel, False, str(exc))
    return NotifyResult(channel, True, f"HTTP {response.status_code}")
