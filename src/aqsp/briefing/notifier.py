from __future__ import annotations

from typing import Any

from aqsp.briefing.generator import Briefing
from aqsp.config import load_runtime_config
from aqsp.notify_templates import build_briefing_notification
from aqsp.notifier import (
    _build_smart_summary_card,
    notify_feishu_card,
    NotifyResult,
)


def send_smart_summary_card(briefing: Briefing) -> None:
    summary = briefing.generate_smart_summary()
    if not summary.strip():
        return
    title = f"📋 选股快报 {briefing.date}"
    card = _build_smart_summary_card(title, summary)
    notify_feishu_card(card)


def _compose_briefing_notification_markdown(
    briefing: Briefing,
    source_status: dict[str, str | bool] | None = None,
) -> str:
    return build_briefing_notification(
        briefing,
        source_status=source_status,
        mode=load_runtime_config().notify_mode,
    )


def send_briefing(
    briefing: Briefing,
    notifier: Any = None,
    source_status: dict[str, str | bool] | None = None,
) -> list[NotifyResult]:
    send_smart_summary_card(briefing)
    markdown = _compose_briefing_notification_markdown(
        briefing,
        source_status=source_status,
    )
    if notifier is not None:
        return notifier(markdown)
    from aqsp.notifier import notify_markdown

    return notify_markdown(markdown)
