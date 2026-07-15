from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from aqsp.core.time import now_shanghai
from aqsp.runtime.gate_notify import (
    build_gate_notification_markdown,
    mark_gate_notification_failed,
    mark_gate_notification_sent,
    reserve_gate_notification,
)
from aqsp.notifier import (
    NotifyResult,
    notify_gate_markdown,
    notify_markdown_via_config,
    print_notify_results,
)
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text

DEFAULT_NOTIFY_FAILURE_RETRY_MINUTES = 60
DEFAULT_NOTIFY_PENDING_TTL_MINUTES = 30


@dataclass(frozen=True)
class ScheduledNotificationArtifacts:
    markdown: str
    notify_enabled: bool


def _is_unstable_state_path(path: Path) -> bool:
    volatile_roots = (Path("/tmp"), Path("/private/tmp"))
    if os.getenv("PYTEST_CURRENT_TEST") and any(
        part.startswith("pytest-of-") for part in path.parts
    ):
        return False
    return any(path == root or root in path.parents for root in volatile_roots)


def notification_state_path(default: str | Path = "data/notify_state.json") -> Path:
    raw = os.getenv("AQSP_NOTIFY_STATE_PATH", str(default))
    path = Path(raw).expanduser()
    if path.is_absolute():
        resolved = path.resolve(strict=False)
        if _is_unstable_state_path(resolved):
            raise OSError(
                f"AQSP_NOTIFY_STATE_PATH must not use volatile tmp path: {path}"
            )
        return resolved
    root = Path(os.getenv("AQSP_PROJECT_ROOT", Path(__file__).resolve().parents[2]))
    return (root / path).resolve(strict=False)


def should_send_notification(
    *,
    kind: str,
    markdown: str,
    state_path: str | Path,
    retry_minutes: int = DEFAULT_NOTIFY_FAILURE_RETRY_MINUTES,
) -> bool:
    path = Path(state_path)
    fingerprint = notification_fingerprint(kind=kind, markdown=markdown)
    with advisory_lock(path):
        state = _read_notification_state(path)
        sent = state.get("sent", {})
        pending = state.get("pending", {})
        failed = state.get("failed", {})
        if isinstance(sent, dict) and _state_entry_matches(
            sent.get(str(kind)), fingerprint
        ):
            return False
        if isinstance(pending, dict) and _state_entry_matches_recent(
            pending.get(str(kind)),
            fingerprint,
            ttl_minutes=DEFAULT_NOTIFY_PENDING_TTL_MINUTES,
        ):
            return False
        if isinstance(failed, dict) and _state_entry_matches_recent(
            failed.get(str(kind)),
            fingerprint,
            ttl_minutes=retry_minutes,
        ):
            return False
    return True


def reserve_notification(
    *,
    kind: str,
    markdown: str,
    state_path: str | Path,
    retry_minutes: int = DEFAULT_NOTIFY_FAILURE_RETRY_MINUTES,
) -> bool:
    path = Path(state_path)
    fingerprint = notification_fingerprint(kind=kind, markdown=markdown)
    with advisory_lock(path):
        state = _read_notification_state(path)
        sent = state.get("sent", {})
        pending = state.get("pending", {})
        failed = state.get("failed", {})
        if not isinstance(sent, dict):
            sent = {}
        if not isinstance(pending, dict):
            pending = {}
        if not isinstance(failed, dict):
            failed = {}
        if _state_entry_matches(sent.get(str(kind)), fingerprint) or (
            _state_entry_matches_recent(
                pending.get(str(kind)),
                fingerprint,
                ttl_minutes=DEFAULT_NOTIFY_PENDING_TTL_MINUTES,
            )
            or _state_entry_matches_recent(
                failed.get(str(kind)),
                fingerprint,
                ttl_minutes=retry_minutes,
            )
        ):
            return False
        pending[str(kind)] = _state_entry(fingerprint, markdown)
        failed.pop(str(kind), None)
        _write_notification_state(path, sent=sent, pending=pending, failed=failed)
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
        pending = state.get("pending", {})
        failed = state.get("failed", {})
        if not isinstance(sent, dict):
            sent = {}
        if not isinstance(pending, dict):
            pending = {}
        if not isinstance(failed, dict):
            failed = {}
        sent[str(kind)] = _state_entry(fingerprint, markdown)
        pending.pop(str(kind), None)
        failed.pop(str(kind), None)
        _write_notification_state(path, sent=sent, pending=pending, failed=failed)


def mark_notification_failed(
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
        pending = state.get("pending", {})
        failed = state.get("failed", {})
        if not isinstance(sent, dict):
            sent = {}
        if not isinstance(pending, dict):
            pending = {}
        if not isinstance(failed, dict):
            failed = {}
        pending.pop(str(kind), None)
        failed[str(kind)] = _state_entry(fingerprint, markdown)
        _write_notification_state(path, sent=sent, pending=pending, failed=failed)


def _write_notification_state(
    path: Path,
    *,
    sent: dict[str, object],
    pending: dict[str, object],
    failed: dict[str, object],
) -> None:
    atomic_write_text(
        path,
        json.dumps(
            {
                "sent": sent,
                "pending": pending,
                "failed": failed,
                "updated_at": now_shanghai().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def notification_fingerprint(*, kind: str, markdown: str) -> str:
    del markdown
    return str(kind).strip()


def notification_content_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.strip().encode("utf-8")).hexdigest()


def _legacy_content_fingerprint_matches(value: str, fingerprint: str) -> bool:
    prefix = f"{fingerprint}:"
    if not value.startswith(prefix):
        return False
    digest = value[len(prefix) :]
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def _state_entry(fingerprint: str, markdown: str) -> dict[str, str]:
    return {
        "fingerprint": fingerprint,
        "content_hash": notification_content_hash(markdown),
        "updated_at": now_shanghai().isoformat(timespec="seconds"),
    }


def _state_entry_matches(entry: object, fingerprint: str) -> bool:
    if isinstance(entry, str):
        return entry == fingerprint or _legacy_content_fingerprint_matches(
            entry, fingerprint
        )
    if isinstance(entry, dict):
        value = str(entry.get("fingerprint") or "")
        return value == fingerprint or _legacy_content_fingerprint_matches(
            value, fingerprint
        )
    return False


def _state_entry_matches_recent(
    entry: object,
    fingerprint: str,
    *,
    ttl_minutes: int,
) -> bool:
    if not _state_entry_matches(entry, fingerprint):
        return False
    if isinstance(entry, str):
        return False
    updated_at = str(entry.get("updated_at") or "")
    try:
        updated = datetime.fromisoformat(updated_at)
    except ValueError:
        return False
    age_seconds = (now_shanghai() - updated).total_seconds()
    return age_seconds < max(int(ttl_minutes), 1) * 60


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
    if not reserve_notification(
        kind=kind,
        markdown=state_markdown,
        state_path=state_path,
    ):
        print_fn(f"{prefix}: skipped duplicate")
        return []
    try:
        results = dispatch_notification(
            markdown,
            mode=mode,
            prefix=prefix,
            summary_markdown=summary_markdown,
        )
    except Exception:
        mark_notification_failed(
            kind=kind,
            markdown=state_markdown,
            state_path=state_path,
        )
        raise
    if any(result.ok for result in results):
        mark_notification_sent(
            kind=kind,
            markdown=state_markdown,
            state_path=state_path,
        )
    else:
        mark_notification_failed(
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
    state_path: str | Path | None = None,
    reserve_before_send: bool = False,
) -> list[NotifyResult]:
    markdown = build_gate_notification_markdown(
        run_date=run_date,
        gate_reasons=gate_reasons,
        next_actions=next_actions,
    )
    if state_path is not None and reserve_before_send:
        if not reserve_gate_notification(
            gate_ok=False,
            gate_reasons=gate_reasons,
            state_path=state_path,
            run_date=run_date,
        ):
            print(f"{prefix}: skipped duplicate")
            return []
    results = notify_gate_markdown(markdown)
    print_notify_results(results, prefix=prefix)
    if state_path is not None:
        if any(result.ok for result in results):
            mark_gate_notification_sent(
                gate_reasons=gate_reasons,
                state_path=state_path,
                run_date=run_date,
            )
        else:
            mark_gate_notification_failed(
                gate_reasons=gate_reasons,
                state_path=state_path,
                run_date=run_date,
            )
    return results


def gate_notification_allowed(
    task_id: str | None = None,
    *,
    notify_requested: bool | None = None,
) -> bool:
    value = task_id or os.getenv("AQSP_RUN_TASK_ID", "")
    normalized = str(value or "").strip().lower()
    notify_flag = str(os.getenv("AQSP_NOTIFY", "") or "").strip().lower()
    gate_notify_flag = str(os.getenv("AQSP_GATE_NOTIFY", "") or "").strip().lower()
    notify_enabled = (
        bool(notify_requested)
        if notify_requested is not None
        else notify_flag in {"1", "true", "yes", "on"}
    )
    gate_notify_enabled = gate_notify_flag in {"1", "true", "yes", "on"}
    return (
        normalized in {"daily", "scheduled"}
        and notify_enabled
        and gate_notify_enabled
    )


def gate_notification_task(task_id: str | None = None) -> bool:
    value = task_id or os.getenv("AQSP_RUN_TASK_ID", "")
    normalized = str(value or "").strip().lower()
    return normalized in {"daily", "scheduled"}


def high_frequency_task(task_id: str | None = None) -> bool:
    value = task_id or os.getenv("AQSP_RUN_TASK_ID", "")
    normalized = str(value or "").strip().lower()
    return normalized in {"intraday", "midday", "monitor", "news", "status"}


def finalize_scheduled_outputs(
    *,
    markdown: str,
    report_path: str,
    output_csv_path: str,
    table: Any,
    print_fn: Callable[[str], None],
) -> None:
    if report_path:
        atomic_write_text(report_path, markdown)
    if output_csv_path:
        atomic_write_text(output_csv_path, table.to_csv(index=False))
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
    mark_gate_notification_failed_fn: Callable[..., None] | None = None,
    mark_gate_notification_suppressed_fn: Callable[..., None] | None = None,
    gate_state_path: str | Path | None = None,
    task_id: str | None = None,
) -> ScheduledNotificationArtifacts:
    output_markdown = markdown
    notify_enabled = bool(args_notify)
    normalized_task_id = str(task_id or os.getenv("AQSP_RUN_TASK_ID", "")).strip().lower()
    is_high_frequency = high_frequency_task(task_id)
    is_gate_task = gate_notification_task(task_id)
    is_manual_task = normalized_task_id == "manual"

    if not gate_ok:
        if is_high_frequency or (not is_gate_task and not is_manual_task):
            print_fn("gate notify: skipped outside daily task")
            notify_enabled = False
            return ScheduledNotificationArtifacts(
                markdown=output_markdown,
                notify_enabled=notify_enabled,
            )
        gate_allowed = gate_notification_allowed(
            task_id,
            notify_requested=notify_enabled,
        )
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
        should_send_gate = False
        if notify_enabled and gate_allowed:
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
        else:
            print_fn("gate notify: notification disabled or skipped")
        if not gate_allowed or not notify_enabled:
            should_send_gate = False
        if should_send_gate:
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
                        state_path=gate_state_path,
                        reserve_before_send=True,
                    )
                if not _has_successful_notify_result(results):
                    if mark_gate_notification_failed_fn is not None:
                        _call_gate_mark_failed(
                            mark_gate_notification_failed_fn,
                            gate_reasons=gate_reasons,
                            gate_state_path=gate_state_path,
                            run_date=latest_iso,
                        )
                    print_fn(
                        "gate notify: delivery failed; suppressing duplicate retries today"
                    )
                elif mark_gate_notification_sent_fn is not None:
                    _call_gate_mark_sent(
                        mark_gate_notification_sent_fn,
                        gate_reasons=gate_reasons,
                        gate_state_path=gate_state_path,
                        run_date=latest_iso,
                    )
            except Exception as exc:  # noqa: BLE001
                if mark_gate_notification_failed_fn is not None:
                    _call_gate_mark_failed(
                        mark_gate_notification_failed_fn,
                        gate_reasons=gate_reasons,
                        gate_state_path=gate_state_path,
                        run_date=latest_iso,
                    )
                print_fn(f"gate notify failed: {exc}")
        elif mark_gate_notification_suppressed_fn is not None:
            _call_gate_mark_suppressed(
                mark_gate_notification_suppressed_fn,
                gate_reasons=gate_reasons,
                gate_state_path=gate_state_path,
                run_date=latest_iso,
            )
        notify_enabled = False
    elif is_high_frequency:
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


def _call_gate_mark_failed(
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


def _call_gate_mark_suppressed(
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
    news_summary: str = "",
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
        news_summary=news_summary,
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
        news_summary=news_summary,
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
