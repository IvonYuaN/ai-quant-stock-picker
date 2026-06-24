from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote
from typing import Any

import requests

from aqsp.core.time import today_shanghai
from aqsp.data.source_health import notification_level_for_health_label
from aqsp.notification_style import compact_notification_markdown


@dataclass(frozen=True)
class NotifyResult:
    channel: str
    ok: bool
    detail: str


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _should_suppress_real_notifications() -> bool:
    if _env_flag("AQSP_ALLOW_REAL_NOTIFICATIONS"):
        return False
    if os.getenv("PYTEST_CURRENT_TEST", "").strip():
        return True
    if _env_flag("CODEX_CI") or os.getenv("CODEX_THREAD_ID", "").strip():
        return True
    return False


def format_notify_results(
    results: list[NotifyResult],
    *,
    prefix: str = "notify",
) -> list[str]:
    if not results:
        return [f"{prefix} skipped: No notification channel configured."]
    lines: list[str] = []
    for result in results:
        status = "ok" if result.ok else "failed"
        lines.append(f"{prefix} {result.channel}: {status} ({result.detail})")
    return lines


def print_notify_results(
    results: list[NotifyResult],
    *,
    prefix: str = "notify",
) -> None:
    for line in format_notify_results(results, prefix=prefix):
        print(line)


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
    body = markdown.lstrip()
    banner = "\n".join(
        [
            "## 数据源状态",
            "",
            f"- 通知级别: {notify_level}",
            f"- 健康: {label}",
            f"- 路径: {route}",
            f"- 说明: {message}",
        ]
    )
    if label in {"fallback", "degraded", "cold_start"}:
        banner += "\n- 提示: 数据源已降级，本次结果请降低信任度并优先人工复核。"
    if body.startswith("# "):
        lines = body.splitlines()
        title = lines[0].strip()
        rest = "\n".join(lines[1:]).lstrip()
        if label in {"fallback", "degraded", "cold_start"}:
            return compact_notification_markdown(f"{title}\n\n{banner}\n\n{rest}")
        return compact_notification_markdown(f"{title}\n\n{rest}\n\n{banner}")
    if label in {"fallback", "degraded", "cold_start"}:
        return compact_notification_markdown(f"{banner}\n\n{body}")
    return compact_notification_markdown(f"{body}\n\n{banner}")


def notify_markdown(markdown: str) -> list[NotifyResult]:
    markdown = compact_notification_markdown(markdown)
    results: list[NotifyResult] = []
    for sender in _all_senders():
        result = sender(markdown)
        if result is not None:
            results.append(result)
    return results


def notify_markdown_via_config(
    markdown: str,
    *,
    mode: str,
    summary_markdown: str | None = None,
) -> list[NotifyResult]:
    normalized_mode = str(mode).strip().lower()
    if normalized_mode == "summary":
        summary_body = compact_notification_markdown(summary_markdown or markdown)
        results = _notify_with_senders(summary_body, _summary_senders())
        if results or not _env_flag("AQSP_NOTIFY_SUMMARY_FALLBACK_FULL"):
            return results
        results = _notify_with_senders(summary_body, _full_senders())
        return results

    if normalized_mode == "full":
        return notify_markdown(markdown)

    summary_body = compact_notification_markdown(summary_markdown or markdown)
    full_body = compact_notification_markdown(markdown)
    results = _notify_with_senders(summary_body, _summary_senders())
    results.extend(_notify_with_senders(full_body, _full_senders()))
    return results


def notify_gate_markdown(markdown: str) -> list[NotifyResult]:
    markdown = compact_notification_markdown(markdown)
    results = _notify_with_senders(markdown, _summary_senders())
    if results:
        return results[:1]
    results = _notify_with_senders(markdown, _full_senders())
    if results:
        return results[:1]
    return []


def send_notification(title: str, content: str) -> list[NotifyResult]:
    markdown = f"# {title}\n\n{content}".strip()
    try:
        from aqsp.config import load_runtime_config

        mode = load_runtime_config().notify_mode
    except Exception:  # noqa: BLE001
        mode = "full"
    results = notify_markdown_via_config(markdown, mode=mode)
    if results:
        return results
    return notify_markdown(markdown)


def _summary_senders():
    return (
        _send_serverchan,
        _send_wechat,
        _send_bark,
        _send_pushplus,
        _send_telegram,
    )


def _full_senders():
    return (
        _send_feishu,
        _send_dingtalk,
        _send_discord,
        _send_slack,
        _send_generic_webhook,
    )


def _all_senders():
    return (
        _send_telegram,
        _send_serverchan,
        _send_wechat,
        *_full_senders(),
        _send_bark,
        _send_pushplus,
    )


def configured_notification_channels() -> tuple[str, ...]:
    channels: list[str] = []
    if os.getenv("SERVERCHAN_SENDKEY", "").strip():
        channels.append("serverchan")
    if os.getenv("WECHAT_WEBHOOK_URL", "").strip():
        channels.append("wechat")
    if os.getenv("BARK_URL", "").strip():
        channels.append("bark")
    if os.getenv("PUSHPLUS_TOKEN", "").strip():
        channels.append("pushplus")
    if (
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        and os.getenv("TELEGRAM_CHAT_ID", "").strip()
    ):
        channels.append("telegram")
    if os.getenv("FEISHU_WEBHOOK_URL", "").strip():
        channels.append("feishu")
    if os.getenv("DINGTALK_WEBHOOK_URL", "").strip():
        channels.append("dingtalk")
    if os.getenv("DISCORD_WEBHOOK_URL", "").strip():
        channels.append("discord")
    if os.getenv("SLACK_WEBHOOK_URL", "").strip():
        channels.append("slack")
    if os.getenv("GENERIC_WEBHOOK_URL", "").strip():
        channels.append("generic_webhook")
    return tuple(channels)


def _notify_with_senders(
    markdown: str,
    senders: tuple,
) -> list[NotifyResult]:
    results: list[NotifyResult] = []
    for sender in senders:
        result = sender(markdown)
        if result is not None:
            results.append(result)
    return results


def _send_telegram(markdown: str) -> NotifyResult | None:
    markdown = compact_notification_markdown(markdown)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": markdown[:3900], "parse_mode": "Markdown"}
    return _post("telegram", url, json=payload)


def _send_wechat(markdown: str) -> NotifyResult | None:
    markdown = compact_notification_markdown(markdown)
    url = os.getenv("WECHAT_WEBHOOK_URL", "").strip()
    if not url:
        return None
    payload = {"msgtype": "markdown", "markdown": {"content": markdown[:3800]}}
    return _post("wechat", url, json=payload)


def _send_serverchan(markdown: str) -> NotifyResult | None:
    markdown = compact_notification_markdown(markdown)
    sendkey = os.getenv("SERVERCHAN_SENDKEY", "").strip()
    if not sendkey:
        return None
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    payload = {
        "title": _notification_title(markdown),
        "desp": markdown[:12000],
    }
    return _post("serverchan", url, data=payload)


def _send_feishu(markdown: str) -> NotifyResult | None:
    markdown = compact_notification_markdown(markdown)
    url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not url:
        return None
    payload = _build_smart_summary_card(_extract_markdown_title(markdown), markdown)
    return _post("feishu", url, json=payload)


def _send_dingtalk(markdown: str) -> NotifyResult | None:
    markdown = compact_notification_markdown(markdown)
    url = os.getenv("DINGTALK_WEBHOOK_URL", "").strip()
    secret = os.getenv("DINGTALK_SECRET", "").strip()
    if not url:
        return None
    title = _notification_title(markdown)
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": markdown[:3800]},
    }
    if secret:
        import time
        import hmac
        import hashlib
        import base64

        timestamp = str(round(time.time() * 1000))
        secret_enc = secret.encode("utf-8")
        string_to_sign = f"{timestamp}\n{secret}"
        string_to_sign_enc = string_to_sign.encode("utf-8")
        hmac_code = hmac.new(
            secret_enc, string_to_sign_enc, digestmod=hashlib.sha256
        ).digest()
        sign = base64.b64encode(hmac_code).decode("utf-8")
        url = f"{url}&timestamp={timestamp}&sign={sign}"
    return _post("dingtalk", url, json=payload)


def _send_bark(markdown: str) -> NotifyResult | None:
    markdown = compact_notification_markdown(markdown)
    url = os.getenv("BARK_URL", "").strip()
    if not url:
        return None
    if not url.endswith("/"):
        url += "/"
    title = _notification_title(markdown)
    url = f"{url}{quote(title, safe='')}/{quote(markdown[:500], safe='')}"
    return _post("bark", url)


def _send_pushplus(markdown: str) -> NotifyResult | None:
    markdown = compact_notification_markdown(markdown)
    token = os.getenv("PUSHPLUS_TOKEN", "").strip()
    if not token:
        return None
    url = "https://www.pushplus.plus/send"
    payload = {
        "token": token,
        "title": _notification_title(markdown),
        "content": markdown[:4000],
        "template": "markdown",
    }
    return _post("pushplus", url, json=payload)


def _send_discord(markdown: str) -> NotifyResult | None:
    markdown = compact_notification_markdown(markdown)
    url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        return None
    payload = {"content": markdown[:2000]}
    return _post("discord", url, json=payload)


def _send_slack(markdown: str) -> NotifyResult | None:
    markdown = compact_notification_markdown(markdown)
    url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        return None
    payload = {"text": markdown[:4000]}
    return _post("slack", url, json=payload)


def _build_smart_summary_card(title: str, summary_markdown: str) -> dict[str, Any]:
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "turquoise",
            },
            "elements": [
                {"tag": "markdown", "content": summary_markdown[:3800]},
            ],
        },
    }


def _notification_title(markdown: str) -> str:
    title = _extract_markdown_title(markdown)
    if title != "AQSP 通知":
        return title
    return f"通知-{today_shanghai().isoformat()}"


def _extract_markdown_title(markdown: str) -> str:
    for line in markdown.splitlines():
        clean = line.strip()
        if clean.startswith("# "):
            return clean[2:].strip()[:80] or "AQSP 通知"
    return "AQSP 通知"


def notify_feishu_card(card: dict[str, Any]) -> NotifyResult | None:
    url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not url:
        return None
    return _post("feishu", url, json=card)


def _send_generic_webhook(markdown: str) -> NotifyResult | None:
    markdown = compact_notification_markdown(markdown)
    url = os.getenv("GENERIC_WEBHOOK_URL", "").strip()
    if not url:
        return None
    return _post("generic_webhook", url, json={"text": markdown})


def _post(channel: str, url: str, **kwargs: object) -> NotifyResult:
    if _should_suppress_real_notifications():
        return NotifyResult(channel, True, "suppressed in Codex session")
    try:
        response = requests.post(url, timeout=15, **kwargs)
        response.raise_for_status()
    except requests.RequestException as exc:
        return NotifyResult(channel, False, str(exc))
    business_error = _extract_business_error(channel, response)
    if business_error:
        return NotifyResult(channel, False, business_error)
    return NotifyResult(channel, True, f"HTTP {response.status_code}")


def _extract_business_error(channel: str, response: requests.Response) -> str:
    content_type = response.headers.get("content-type", "").lower()
    data: Any | None = None
    if "json" in content_type:
        try:
            data = response.json()
        except ValueError:
            data = None
    else:
        try:
            data = response.json()
        except ValueError:
            return ""
    if not isinstance(data, dict):
        return ""

    if channel == "serverchan":
        code = data.get("code")
        if code not in (None, 0):
            return f"serverchan code={code}: {data.get('message') or data.get('info') or 'unknown error'}"
    elif channel == "wechat":
        code = data.get("errcode")
        if code not in (None, 0):
            return f"wechat errcode={code}: {data.get('errmsg') or 'unknown error'}"
    elif channel == "feishu":
        code = data.get("code")
        if code not in (None, 0):
            return f"feishu code={code}: {data.get('msg') or data.get('message') or 'unknown error'}"
    elif channel == "dingtalk":
        code = data.get("errcode")
        if code not in (None, 0):
            return f"dingtalk errcode={code}: {data.get('errmsg') or 'unknown error'}"
    elif channel == "pushplus":
        code = data.get("code")
        if code not in (None, 200):
            return f"pushplus code={code}: {data.get('msg') or data.get('message') or 'unknown error'}"
    elif channel == "telegram":
        if data.get("ok") is False:
            return f"telegram: {data.get('description') or 'unknown error'}"
    elif channel == "generic_webhook":
        if any(key in data for key in ("success", "ok")):
            success = data.get("success", data.get("ok"))
            if success is False:
                return f"generic_webhook: {data.get('message') or data.get('error') or 'unknown error'}"
    return ""
