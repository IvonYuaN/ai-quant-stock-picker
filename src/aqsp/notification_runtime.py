from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from aqsp.core.time import now_shanghai
from aqsp.runtime.gate_notify import build_gate_notification_markdown
from aqsp.notifier import (
    NotifyResult,
    notify_gate_markdown,
    notify_markdown_via_config,
    print_notify_results,
)
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text


@dataclass(frozen=True)
class ScheduledNotificationArtifacts:
    markdown: str
    notify_enabled: bool


def should_send_notification(
    *,
    kind: str,
    markdown: str,
    state_path: str | Path,
) -> bool:
    path = Path(state_path)
    fingerprint = notification_fingerprint(kind=kind, markdown=markdown)
    with advisory_lock(path):
        state = _read_notification_state(path)
        sent = state.get("sent", {})
        if isinstance(sent, dict) and sent.get(str(kind)) == fingerprint:
            return False
    return True


def mark_notification_sent(
    *,
    kind: str,
    markdown: str,
    state_path: str | Path,
) -> None:
    path = Path(state_path)
    fingerprint = notification_fingerprint(kind=kind, markdown=markdown)
    with advisory_lock(path):
        state = _read_notification_state(path)
        sent = state.get("sent", {})
        if not isinstance(sent, dict):
            sent = {}
        sent[str(kind)] = fingerprint
        atomic_write_text(
            path,
            json.dumps(
                {
                    "sent": sent,
                    "updated_at": now_shanghai().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )


def notification_fingerprint(*, kind: str, markdown: str) -> str:
    normalized_kind = str(kind).strip()
    digest = hashlib.sha256(markdown.strip().encode("utf-8")).hexdigest()
    return f"{normalized_kind}:{digest}"


def dispatch_notification(
    markdown: str,
    *,
    mode: str,
    prefix: str,
    summary_markdown: str | None = None,
) -> list[NotifyResult]:
    results = notify_markdown_via_config(
        markdown,
        mode=mode,
        summary_markdown=summary_markdown,
    )
    print_notify_results(results, prefix=prefix)
    return results


def dispatch_notification_once(
    markdown: str,
    *,
    mode: str,
    prefix: str,
    kind: str,
    state_path: str | Path,
    summary_markdown: str | None = None,
    print_fn: Callable[[str], None] = print,
) -> list[NotifyResult]:
    state_markdown = summary_markdown or markdown
    if not should_send_notification(
        kind=kind,
        markdown=state_markdown,
        state_path=state_path,
    ):
        print_fn(f"{prefix}: skipped duplicate")
        return []
    results = dispatch_notification(
        markdown,
        mode=mode,
        prefix=prefix,
        summary_markdown=summary_markdown,
    )
    if any(result.ok for result in results):
        mark_notification_sent(
            kind=kind,
            markdown=state_markdown,
            state_path=state_path,
        )
    return results


def dispatch_gate_notification(
    *,
    run_date: str,
    gate_reasons: list[str],
    next_actions: list[str],
    mode: str,
    prefix: str = "gate notify",
) -> list[NotifyResult]:
    markdown = build_gate_notification_markdown(
        run_date=run_date,
        gate_reasons=gate_reasons,
        next_actions=next_actions,
    )
    results = notify_gate_markdown(markdown)
    print_notify_results(results, prefix=prefix)
    return results


def gate_notification_allowed(task_id: str | None = None) -> bool:
    value = task_id if task_id is not None else os.getenv("AQSP_RUN_TASK_ID", "")
    normalized = str(value or "").strip().lower()
    return normalized in {"daily", "scheduled", "manual"}


def finalize_scheduled_outputs(
    *,
    markdown: str,
    report_path: str,
    output_csv_path: str,
    table: Any,
    print_fn: Callable[[str], None],
) -> None:
    if report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(markdown, encoding="utf-8")
    if output_csv_path:
        Path(output_csv_path).parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(output_csv_path, index=False)
    print_fn(markdown)


def finalize_scheduled_notification(
    *,
    markdown: str,
    args_notify: bool,
    gate_ok: bool,
    gate_reasons: list[str],
    next_actions: list[str],
    latest_iso: str,
    notify_mode: str,
    dispatch_gate_notification_fn: Callable[..., list[NotifyResult]],
    should_send_gate_notification_fn: Callable[..., bool],
    format_notification_gate_block_fn: Callable[[list[str], list[str]], str],
    legacy_notify_fn: Callable[[str], list[NotifyResult]] | None,
    print_fn: Callable[[str], None],
    gate_block_markdown: str = "",
    mark_gate_notification_sent_fn: Callable[..., None] | None = None,
    gate_state_path: str | Path | None = None,
    task_id: str | None = None,
) -> ScheduledNotificationArtifacts:
    output_markdown = markdown
    notify_enabled = bool(args_notify)

    if notify_enabled and not gate_ok:
        print_fn("⛔ 双门未达，--notify 自动失效。原因：")
        for reason in gate_reasons:
            print_fn(f"   - {reason}")
        print_fn("📌 处理项：")
        for action in next_actions:
            print_fn(f"   - {action}")
        gate_block = gate_block_markdown or format_notification_gate_block_fn(
            gate_reasons, next_actions
        )
        output_markdown = gate_block + markdown
        if not gate_notification_allowed(task_id):
            print_fn("gate notify: skipped outside daily task")
        else:
            try:
                should_send_gate = _call_gate_should_send(
                    should_send_gate_notification_fn,
                    gate_ok=gate_ok,
                    gate_reasons=gate_reasons,
                    gate_state_path=gate_state_path,
                    run_date=latest_iso,
                )
            except Exception as exc:  # noqa: BLE001
                print_fn(f"gate notify state failed: {exc}")
                should_send_gate = False
            if should_send_gate:
                if mark_gate_notification_sent_fn is not None:
                    _call_gate_mark_sent(
                        mark_gate_notification_sent_fn,
                        gate_reasons=gate_reasons,
                        gate_state_path=gate_state_path,
                        run_date=latest_iso,
                    )
                try:
                    if legacy_notify_fn is not None:
                        gate_markdown = build_gate_notification_markdown(
                            run_date=latest_iso,
                            gate_reasons=gate_reasons,
                            next_actions=next_actions,
                        )
                        results = legacy_notify_fn(gate_markdown)
                    else:
                        results = dispatch_gate_notification_fn(
                            run_date=latest_iso,
                            gate_reasons=gate_reasons,
                            next_actions=next_actions,
                            mode=notify_mode,
                        )
                    if not _has_successful_notify_result(results):
                        print_fn("gate notify: delivery failed; suppressing duplicate retries today")
                except Exception as exc:  # noqa: BLE001
                    print_fn(f"gate notify failed: {exc}")
        notify_enabled = False

    return ScheduledNotificationArtifacts(
        markdown=output_markdown,
        notify_enabled=notify_enabled,
    )


def _has_successful_notify_result(results: list[NotifyResult] | None) -> bool:
    return any(getattr(result, "ok", False) for result in (results or []))


def _call_gate_should_send(
    callback: Callable[..., bool],
    *,
    gate_ok: bool,
    gate_reasons: list[str],
    gate_state_path: str | Path | None,
    run_date: str,
) -> bool:
    try:
        return callback(
            gate_ok=gate_ok,
            gate_reasons=gate_reasons,
            state_path=gate_state_path,
            run_date=run_date,
        )
    except TypeError:
        return callback(gate_ok, gate_reasons)


def _call_gate_mark_sent(
    callback: Callable[..., None],
    *,
    gate_reasons: list[str],
    gate_state_path: str | Path | None,
    run_date: str,
) -> None:
    try:
        callback(
            gate_reasons=gate_reasons,
            state_path=gate_state_path,
            run_date=run_date,
        )
    except TypeError:
        callback(gate_reasons)


def dispatch_scheduled_daily_notification(
    *,
    notify_enabled: bool,
    notify_mode: str,
    latest_iso: str,
    tradable: list[Any],
    picks: list[Any],
    portfolio_summary: Any,
    debate_results: Any,
    actual_source: str,
    source_health_label: str,
    source_health_message: str,
    requested_source: str,
    cold_start_days: int,
    cold_start_min_days: int,
    is_cold_start: bool,
    circuit_breaker_reason: str,
    snapshot_diff: Any,
    title_label: str,
    build_daily_run_notification_fn: Callable[..., str],
    dispatch_notification_fn: Callable[..., list[NotifyResult]],
    validation_summary: dict[str, object] | None = None,
    notification_kind: str = "",
) -> None:
    if not notify_enabled:
        return

    daily_markdown = build_daily_run_notification_fn(
        run_date=latest_iso,
        tradable=tradable,
        candidates=picks,
        portfolio_summary=portfolio_summary,
        debate_results=debate_results,
        actual_source=actual_source,
        source_health_label=source_health_label,
        source_health_message=source_health_message,
        requested_source=requested_source,
        cold_start_days=cold_start_days,
        cold_start_min_days=cold_start_min_days,
        is_cold_start=is_cold_start,
        circuit_breaker_reason=circuit_breaker_reason,
        snapshot_diff=snapshot_diff,
        validation_summary=validation_summary,
        mode=notify_mode,
        title_label=title_label,
    )
    daily_summary = build_daily_run_notification_fn(
        run_date=latest_iso,
        tradable=tradable,
        candidates=picks,
        portfolio_summary=portfolio_summary,
        debate_results=debate_results,
        actual_source=actual_source,
        source_health_label=source_health_label,
        source_health_message=source_health_message,
        requested_source=requested_source,
        cold_start_days=cold_start_days,
        cold_start_min_days=cold_start_min_days,
        is_cold_start=is_cold_start,
        circuit_breaker_reason=circuit_breaker_reason,
        snapshot_diff=snapshot_diff,
        validation_summary=validation_summary,
        mode="summary",
        title_label=title_label,
    )
    dispatch_kwargs: dict[str, Any] = {
        "prefix": "notify",
        "mode": notify_mode,
        "summary_markdown": daily_summary,
    }
    if notification_kind:
        dispatch_kwargs["kind"] = notification_kind
    dispatch_notification_fn(
        daily_markdown,
        **dispatch_kwargs,
    )


def _read_notification_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
