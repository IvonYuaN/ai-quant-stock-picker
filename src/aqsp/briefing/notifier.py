from __future__ import annotations

from typing import Any

from aqsp.briefing.generator import Briefing


def send_briefing(briefing: Briefing, notifier: Any = None) -> None:
    markdown = briefing.to_markdown()
    if notifier is not None:
        notifier(markdown)
        return
    from aqsp.notifier import notify_markdown

    notify_markdown(markdown)
