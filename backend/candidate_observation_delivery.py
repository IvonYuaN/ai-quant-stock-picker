"""Durable, tenant-local delivery for triggered candidate observations.

The observation store owns the condition and last quote. This module only
turns a triggered check into one existing notification-runtime event. The
event key is stable for an adopted observation revision, so repeated monitor
rounds cannot resend the same condition; adopting a revised condition creates
the next key.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import candidate_observations as store


def _line(value: object) -> str:
    return " ".join(str(value or "").split()).replace("`", "'")


def _markdown(item: dict[str, Any], check: dict[str, Any]) -> str:
    direction = "达到或高于" if item.get("comparator") == "at_or_above" else "达到或低于"
    quote_as_of = check.get("quote_as_of") or "缺失"
    return "\n".join(
        (
            "# 候选观察条件已触发",
            "",
            f"**{_line(item.get('name') or item.get('symbol'))} {_line(item.get('symbol'))}**",
            f"当前价格 {_line(check.get('price'))} 元，{direction} {_line(item.get('price'))} 元。",
            f"行情源时点：{_line(quote_as_of)}",
            f"等什么：{_line(item.get('condition'))}",
            f"何时取消：{_line(item.get('invalidation'))}",
            "",
            "这是人工采用的观察条件，不是买卖信号或成交记录；请重新核对当前行情与完整证据。",
        )
    )


def deliver_triggered(
    items: list[dict[str, Any]],
    checks: list[dict[str, Any]],
) -> tuple[list[str], bool]:
    """Deliver newly observed triggers through the existing dedupe runtime."""
    from aqsp.notification_runtime import dispatch_notification_once

    by_symbol = {str(item.get("symbol")): item for item in items if item.get("state") == "active"}
    root = Path(store._file()).parent
    state_path = root / "notify-state-candidate-observation.json"
    messages: list[str] = []
    suppressed = False
    for check in checks:
        if not check.get("triggered"):
            continue
        item = by_symbol.get(str(check.get("symbol")))
        if not item:
            continue
        observation_id = str(item.get("id") or "").strip()
        revision = item.get("revision")
        if not observation_id or type(revision) is not int or revision < 1:
            suppressed = True
            continue
        kind = f"candidate-observation|{observation_id}|{revision}"
        try:
            results = dispatch_notification_once(
                _markdown(item, check),
                mode="summary",
                prefix="candidate-observation",
                kind=kind,
                state_path=state_path,
            )
        except Exception:  # noqa: BLE001 -- monitor remains usable when a channel fails
            suppressed = True
            continue
        if any(result.ok for result in results):
            messages.append("candidate-observation: 候选观察触发通知已发送")
        elif not results:
            suppressed = True
        else:
            suppressed = True
    return messages, suppressed


__all__ = ["deliver_triggered"]
