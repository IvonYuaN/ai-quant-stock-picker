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


def _compose_briefing_notification_markdown(
    briefing: Briefing,
    source_status: dict[str, str | bool] | None = None,
) -> str:
    summary = briefing.generate_smart_summary().strip()
    sections = {section.title: section.content.strip() for section in briefing.sections}
    body_parts: list[str] = []
    if summary:
        body_parts.append("## 主链摘要\n\n" + summary)
    evidence = sections.get("候选证据链", "")
    if evidence and "今日无候选标的" not in evidence:
        body_parts.append("## 候选证据链\n\n" + evidence)
    next_day = sections.get("明日重点", "")
    if next_day:
        body_parts.append("## 明日动作\n\n" + next_day)
    body = "\n\n".join(part for part in body_parts if part).strip() or briefing.to_markdown()
    return prepend_source_status_banner(
        body,
        source_status=source_status,
    )


def send_briefing(
    briefing: Briefing,
    notifier: Any = None,
    source_status: dict[str, str | bool] | None = None,
) -> None:
    send_smart_summary_card(briefing)
    markdown = _compose_briefing_notification_markdown(
        briefing,
        source_status=source_status,
    )
    if notifier is not None:
        notifier(markdown)
        return
    from aqsp.notifier import notify_markdown

    notify_markdown(markdown)
