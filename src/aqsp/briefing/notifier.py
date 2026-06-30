from __future__ import annotations

from typing import Any
from pathlib import Path

from aqsp.briefing.generator import Briefing
from aqsp.config import load_runtime_config
from aqsp.notification_runtime import (
    dispatch_notification_once,
    mark_notification_failed,
    mark_notification_sent,
    notification_state_path,
    reserve_notification,
)
from aqsp.notify_templates import build_briefing_notification
from aqsp.notifier import (
    _build_smart_summary_card,
    notify_feishu_card,
    NotifyResult,
)


def send_smart_summary_card(
    briefing: Briefing,
    *,
    state_path: str | Path | None = None,
    kind: str | None = None,
) -> None:
    summary = briefing.generate_smart_summary()
    if not summary.strip():
        return
    title = f"选股简报 {briefing.date}"
    notify_kind = kind or f"briefing-card:{briefing.date}"
    resolved_state_path = state_path or notification_state_path()
    state_markdown = f"{title}\n\n{summary}"
    if not reserve_notification(
        kind=notify_kind,
        markdown=state_markdown,
        state_path=resolved_state_path,
    ):
        return
    card = _build_smart_summary_card(title, summary)
    results = notify_feishu_card(card) or []
    if any(result.ok for result in results):
        mark_notification_sent(
            kind=notify_kind,
            markdown=state_markdown,
            state_path=resolved_state_path,
        )
    else:
        mark_notification_failed(
            kind=notify_kind,
            markdown=state_markdown,
            state_path=resolved_state_path,
        )


def compose_briefing_notification_markdown(
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
    *,
    state_path: str | Path | None = None,
) -> list[NotifyResult]:
    send_smart_summary_card(briefing)
    markdown = compose_briefing_notification_markdown(
        briefing,
        source_status=source_status,
    )
    if notifier is not None:
        return notifier(markdown)

    return dispatch_notification_once(
        markdown,
        mode=load_runtime_config().notify_mode,
        prefix="briefing notify",
        kind=f"briefing:{briefing.date[:10]}",
        state_path=state_path or notification_state_path(),
    )
