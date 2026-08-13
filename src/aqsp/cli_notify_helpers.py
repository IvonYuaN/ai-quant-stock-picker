"""Notification dispatch helpers extracted from ``cli.py``.

Contains the notification routing and dedup dispatch logic: the mutable
``notify_markdown`` reference (monkeypatched by tests), the
``NOTIFY_STATE_PATH`` constant, and the two core dispatch functions.

All symbols are re-exported by ``cli.py`` for backward compatibility.
"""

from __future__ import annotations

import os

from aqsp.cli_notification_gate import _resolve_runtime_state_path
from aqsp.notification_runtime import (
    dispatch_notification_once as _dispatch_notification_once_impl,
    mark_notification_failed,
    mark_notification_sent,
    reserve_notification,
)
from aqsp.notifier import (
    notify_markdown as _notify_markdown_default,
    notify_markdown_via_config,
    print_notify_results,
)

NOTIFY_STATE_PATH = "data/notify_state.json"

#: Mutable notifier reference — tests monkeypatch this attribute on
#: *this* module (``cli_notify_helpers.notify_markdown``) to stub delivery.
notify_markdown = _notify_markdown_default


def _notify_via_config(markdown: str, *, mode: str) -> list:
    if notify_markdown is not _notify_markdown_default:
        return notify_markdown(markdown)
    return notify_markdown_via_config(markdown, mode=mode)


def _dispatch_notification_once(
    markdown: str,
    *,
    prefix: str,
    mode: str,
    kind: str,
    summary_markdown: str | None = None,
) -> list:
    state_path = _resolve_runtime_state_path(
        os.getenv("AQSP_NOTIFY_STATE_PATH", NOTIFY_STATE_PATH)
    )
    if notify_markdown is not _notify_markdown_default:
        payload = (
            summary_markdown
            if str(mode).strip().lower() == "summary" and summary_markdown
            else markdown
        )
        if not reserve_notification(
            kind=kind,
            markdown=payload,
            state_path=state_path,
        ):
            print(f"{prefix}: skipped duplicate")
            return []
        try:
            results = notify_markdown(payload)
            print_notify_results(results, prefix=prefix)
        except Exception:
            mark_notification_failed(
                kind=kind,
                markdown=payload,
                state_path=state_path,
            )
            raise
        if any(result.ok for result in results):
            mark_notification_sent(
                kind=kind,
                markdown=payload,
                state_path=state_path,
            )
        else:
            mark_notification_failed(
                kind=kind,
                markdown=payload,
                state_path=state_path,
            )
        return results
    return _dispatch_notification_once_impl(
        markdown,
        mode=mode,
        prefix=prefix,
        kind=kind,
        state_path=state_path,
        summary_markdown=summary_markdown,
    )
