from __future__ import annotations

from typing import Any

from aqsp.briefing.generator import Briefing
from aqsp.notifier import (
    _build_smart_summary_card,
    notify_feishu_card,
    prepend_source_status_banner,
)


def send_smart_summary_card(briefing: Briefing) -> None:
    summary = briefing.generate_smart_summary()
    if not summary.strip():
        return
    title = f"📋 选股快报 {briefing.date}"
    card = _build_smart_summary_card(title, summary)
    notify_feishu_card(card)


def send_briefing(
    briefing: Briefing,
    notifier: Any = None,
    source_status: dict[str, str | bool] | None = None,
) -> None:
    send_smart_summary_card(briefing)
    markdown = prepend_source_status_banner(
        briefing.to_markdown(),
        source_status=source_status,
    )
    if notifier is not None:
        notifier(markdown)
        return
    from aqsp.notifier import notify_markdown

    notify_markdown(markdown)
