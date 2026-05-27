from __future__ import annotations

import os
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class NotifyResult:
    channel: str
    ok: bool
    detail: str


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
