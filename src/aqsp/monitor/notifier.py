from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from aqsp.config import load_runtime_config
from aqsp.core.time import now_shanghai
from aqsp.notification_style import compact_notification_markdown
from aqsp.notify_templates import build_monitor_notification
from aqsp.notifier import notify_markdown_via_config, print_notify_results
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text

if TYPE_CHECKING:
    from .checker import MonitorResult

MONITOR_NOTIFY_PENDING_TTL_MINUTES = 30
MONITOR_NOTIFY_FAILURE_RETRY_MINUTES = 60


def format_alert(results: list[MonitorResult]) -> str:
    """Format triggered monitors into alert message."""
    now = now_shanghai()

    critical = [r for r in results if r.severity == "critical" and r.triggered]
    warnings = [r for r in results if r.severity == "warning" and r.triggered]

    lines = [
        "# 系统监控告警",
        "",
        "## 结论",
        "",
        f"- 检查时间: {now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 严重告警: {len(critical)}",
        f"- 一般告警: {len(warnings)}",
        "",
    ]

    if critical:
        lines.append("## 严重")
        lines.append("")
        for r in critical:
            lines.append(f"### {r.name}")
            lines.append(f"- 描述: {r.message}")
            if r.details:
                for key, value in r.details.items():
                    lines.append(f"- {key}: {value}")
            lines.append("")

    if warnings:
        lines.append("## 警告")
        lines.append("")
        for r in warnings:
            lines.append(f"### {r.name}")
            lines.append(f"- 描述: {r.message}")
            if r.details:
                for key, value in r.details.items():
                    lines.append(f"- {key}: {value}")
            lines.append("")

    if not critical and not warnings:
        lines.append("- 总体状态: 正常")

    return compact_notification_markdown("\n".join(lines))


def send_alerts(results: list[MonitorResult]) -> None:
    """Send alerts for triggered monitors via notifier."""
    triggered = [r for r in results if r.triggered]
    if not triggered:
        _expire_monitor_notify_state()
        return

    fingerprint = _monitor_alert_fingerprint(triggered)
    date_key = now_shanghai().date().isoformat()
    state_path = _monitor_notify_state_path()
    try:
        new_triggered = _reserve_monitor_alerts(
            state_path=state_path,
            results=triggered,
            fingerprint=fingerprint,
            date_key=date_key,
        )
    except OSError as exc:
        print(f"monitor notify: state write failed; fail closed: {exc}")
        return
    if not new_triggered:
        print("monitor notify: skipped duplicate alert")
        return

    notify_mode = load_runtime_config().notify_mode
    alert_msg = build_monitor_notification(
        new_triggered,
        mode=notify_mode,
    )
    notify_results = notify_markdown_via_config(alert_msg, mode=notify_mode)
    print_notify_results(notify_results, prefix="monitor notify")
    if any(result.ok for result in notify_results):
        _mark_monitor_alerts_sent(
            state_path=state_path,
            results=new_triggered,
            fingerprint=fingerprint,
            date_key=date_key,
        )
    else:
        _mark_monitor_alerts_failed(
            state_path=state_path,
            results=new_triggered,
            fingerprint=fingerprint,
            date_key=date_key,
        )
        print("monitor notify: delivery failed; retry after cooldown")


def _monitor_notify_state_path() -> Path:
    raw = os.getenv("AQSP_MONITOR_NOTIFY_STATE_PATH", "data/monitor_notify_state.json")
    path = Path(raw).expanduser()
    if path.is_absolute():
        resolved = path.resolve(strict=False)
        if _is_unstable_state_path(resolved):
            raise OSError(
                f"AQSP_MONITOR_NOTIFY_STATE_PATH must not use volatile tmp path: {path}"
            )
        return resolved
    root = Path(os.getenv("AQSP_PROJECT_ROOT", Path(__file__).resolve().parents[3]))
    return (root / path).resolve(strict=False)


def _is_unstable_state_path(path: Path) -> bool:
    volatile_roots = (Path("/tmp"), Path("/private/tmp"))
    return any(path == root or root in path.parents for root in volatile_roots)


def _monitor_alert_fingerprint(results: list[MonitorResult]) -> str:
    parts: list[str] = []
    for result in sorted(results, key=lambda item: item.name):
        if not result.triggered:
            continue
        parts.append(f"{result.name}|{result.severity}")
    raw = "\n".join(parts) or "no-triggered"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _monitor_alert_key(result: MonitorResult) -> str:
    return f"{result.name}|{result.severity}"


def _read_monitor_notify_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _reserve_monitor_alerts(
    *,
    state_path: Path,
    results: list[MonitorResult],
    fingerprint: str,
    date_key: str,
) -> list[MonitorResult]:
    with advisory_lock(state_path):
        state = _read_monitor_notify_state(state_path)
        sent_by_date = state.get("sent_by_date", {})
        if not isinstance(sent_by_date, dict):
            sent_by_date = {}
        day_state = sent_by_date.get(date_key, {})
        if not isinstance(day_state, dict):
            day_state = {}
        new_results = []
        for result in results:
            entry = day_state.get(_monitor_alert_key(result))
            if _monitor_state_entry_blocks(entry):
                continue
            new_results.append(result)
        if not new_results:
            return []
        for result in new_results:
            day_state[_monitor_alert_key(result)] = _monitor_state_entry("pending")
        sent_by_date[date_key] = day_state
        _write_monitor_notify_state(
            state_path=state_path,
            fingerprint=fingerprint,
            date_key=date_key,
            status="pending",
            sent_by_date=sent_by_date,
        )
        return new_results


def _mark_monitor_alerts_sent(
    *,
    state_path: Path,
    results: list[MonitorResult],
    fingerprint: str,
    date_key: str,
) -> None:
    with advisory_lock(state_path):
        state = _read_monitor_notify_state(state_path)
        sent_by_date = state.get("sent_by_date", {})
        if not isinstance(sent_by_date, dict):
            sent_by_date = {}
        day_state = sent_by_date.get(date_key, {})
        if not isinstance(day_state, dict):
            day_state = {}
        for result in results:
            day_state[_monitor_alert_key(result)] = _monitor_state_entry("sent")
        sent_by_date[date_key] = day_state
        _write_monitor_notify_state(
            state_path=state_path,
            fingerprint=fingerprint,
            date_key=date_key,
            status="sent",
            sent_by_date=sent_by_date,
        )


def _mark_monitor_alerts_failed(
    *,
    state_path: Path,
    results: list[MonitorResult],
    fingerprint: str,
    date_key: str,
) -> None:
    with advisory_lock(state_path):
        state = _read_monitor_notify_state(state_path)
        sent_by_date = state.get("sent_by_date", {})
        if not isinstance(sent_by_date, dict):
            sent_by_date = {}
        day_state = sent_by_date.get(date_key, {})
        if not isinstance(day_state, dict):
            day_state = {}
        for result in results:
            day_state[_monitor_alert_key(result)] = _monitor_state_entry("failed")
        sent_by_date[date_key] = day_state
        _write_monitor_notify_state(
            state_path=state_path,
            fingerprint=fingerprint,
            date_key=date_key,
            status="failed",
            sent_by_date=sent_by_date,
        )


def _monitor_state_entry(status: str) -> dict[str, str]:
    return {
        "status": status,
        "updated_at": now_shanghai().isoformat(timespec="seconds"),
    }


def _monitor_state_entry_blocks(entry: object) -> bool:
    if isinstance(entry, str):
        return entry in {"pending", "sent", "failed"}
    if not isinstance(entry, dict):
        return False
    status = str(entry.get("status") or "")
    if status == "sent":
        return True
    ttl = (
        MONITOR_NOTIFY_PENDING_TTL_MINUTES
        if status == "pending"
        else MONITOR_NOTIFY_FAILURE_RETRY_MINUTES
        if status == "failed"
        else 0
    )
    if ttl <= 0:
        return False
    try:
        updated = datetime.fromisoformat(str(entry.get("updated_at") or ""))
    except ValueError:
        return False
    return (now_shanghai() - updated).total_seconds() < ttl * 60


def _write_monitor_notify_state(
    *,
    state_path: Path,
    fingerprint: str,
    date_key: str,
    status: str,
    sent_by_date: dict[str, object] | None = None,
) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        state_path,
        json.dumps(
            {
                "fingerprint": fingerprint,
                "date": date_key,
                "status": status,
                "sent_by_date": sent_by_date or {},
                "updated_at": now_shanghai().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def _expire_monitor_notify_state() -> None:
    try:
        path = _monitor_notify_state_path()
    except OSError as exc:
        print(f"monitor notify: state path invalid; fail closed: {exc}")
        return
    today = now_shanghai().date().isoformat()
    with advisory_lock(path):
        state = _read_monitor_notify_state(path)
        if state.get("date") != today:
            path.unlink(missing_ok=True)
