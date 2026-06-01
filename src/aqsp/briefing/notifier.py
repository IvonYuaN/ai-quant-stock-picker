from __future__ import annotations

from typing import Any

from aqsp.briefing.generator import Briefing
from aqsp.notifier import prepend_source_status_banner


def send_briefing(
    briefing: Briefing,
    notifier: Any = None,
    source_status: dict[str, str | bool] | None = None,
) -> None:
    markdown = prepend_source_status_banner(
        briefing.to_markdown(),
        source_status=source_status,
    )
    if notifier is not None:
        notifier(markdown)
        return
    from aqsp.notifier import notify_markdown

    notify_markdown(markdown)
