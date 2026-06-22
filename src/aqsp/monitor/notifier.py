from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from aqsp.config import load_runtime_config
from aqsp.core.time import now_shanghai
from aqsp.notification_style import compact_notification_markdown
from aqsp.notify_templates import build_monitor_notification
from aqsp.notifier import notify_markdown, print_notify_results
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text

if TYPE_CHECKING:
    from .checker import MonitorResult


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
        print("monitor notify: skipped duplicate critical alert")
        return

    alert_msg = build_monitor_notification(
        new_triggered,
        mode=load_runtime_config().notify_mode,
    )
    notify_results = notify_markdown(alert_msg)
    print_notify_results(notify_results, prefix="monitor notify")
    if any(result.ok for result in notify_results):
        _mark_monitor_alerts_sent(
            state_path=state_path,
            results=new_triggered,
            fingerprint=fingerprint,
            date_key=date_key,
        )
    else:
        print("monitor notify: delivery failed; suppressing duplicate retries today")


def _monitor_notify_state_path() -> Path:
    raw = os.getenv("AQSP_MONITOR_NOTIFY_STATE_PATH", "data/monitor_notify_state.json")
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    root = Path(os.getenv("AQSP_PROJECT_ROOT", Path(__file__).resolve().parents[3]))
    return root / path


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
        new_results = [
            result for result in results if _monitor_alert_key(result) not in day_state
        ]
        if not new_results:
            return []
        for result in new_results:
            day_state[_monitor_alert_key(result)] = "pending"
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
            day_state[_monitor_alert_key(result)] = "sent"
        sent_by_date[date_key] = day_state
        _write_monitor_notify_state(
            state_path=state_path,
            fingerprint=fingerprint,
            date_key=date_key,
            status="sent",
            sent_by_date=sent_by_date,
        )


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
    path = _monitor_notify_state_path()
    today = now_shanghai().date().isoformat()
    with advisory_lock(path):
        state = _read_monitor_notify_state(path)
        if state.get("date") != today:
            path.unlink(missing_ok=True)
