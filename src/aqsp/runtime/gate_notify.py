from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text

GATE_NOTIFY_PENDING_TTL_MINUTES = 30
GATE_NOTIFY_RETRY_MINUTES = 24 * 60


def normalize_gate_run_date(run_date: str = "") -> str:
    text = str(run_date or "").strip()
    if len(text) >= 10:
        candidate = text[:10]
        try:
            datetime.fromisoformat(candidate)
        except ValueError:
            pass
        else:
            return candidate
    return now_shanghai().date().isoformat()


def should_send_gate_notification(
    *,
    gate_ok: bool,
    gate_reasons: list[str],
    state_path: str | Path,
    run_date: str = "",
) -> bool:
    path = Path(state_path)
    with advisory_lock(path):
        if gate_ok:
            path.unlink(missing_ok=True)
            return False

        fingerprint = gate_reason_fingerprint(gate_reasons)
        date_key = normalize_gate_run_date(run_date)
        state = _read_gate_notify_state(path)
        sent_by_date = state.get("sent_by_date", {})
        day_entry = (
            sent_by_date.get(date_key) if isinstance(sent_by_date, dict) else None
        )
        day_entry = _coerce_legacy_day_entry(state, day_entry, fingerprint, date_key)
        if _entry_has_status(day_entry, fingerprint, {"sent"}):
            return False
        if _entry_recent(
            day_entry,
            fingerprint,
            statuses={"failed"},
            ttl_minutes=GATE_NOTIFY_RETRY_MINUTES,
        ):
            return False
        if _entry_recent(
            day_entry,
            fingerprint,
            statuses={"pending"},
            ttl_minutes=GATE_NOTIFY_PENDING_TTL_MINUTES,
        ):
            return False
    return True


def reserve_gate_notification(
    *,
    gate_ok: bool,
    gate_reasons: list[str],
    state_path: str | Path,
    run_date: str = "",
) -> bool:
    path = Path(state_path)
    with advisory_lock(path):
        if gate_ok:
            path.unlink(missing_ok=True)
            return False

        fingerprint = gate_reason_fingerprint(gate_reasons)
        date_key = normalize_gate_run_date(run_date)
        state = _read_gate_notify_state(path)
        sent_by_date = state.get("sent_by_date", {})
        if not isinstance(sent_by_date, dict):
            sent_by_date = {}
        day_entry = _coerce_legacy_day_entry(
            state,
            sent_by_date.get(date_key),
            fingerprint,
            date_key,
        )
        if _entry_has_status(day_entry, fingerprint, {"sent"}):
            return False
        if _entry_recent(
            day_entry,
            fingerprint,
            statuses={"failed"},
            ttl_minutes=GATE_NOTIFY_RETRY_MINUTES,
        ):
            return False
        if _entry_recent(
            day_entry,
            fingerprint,
            statuses={"pending"},
            ttl_minutes=GATE_NOTIFY_PENDING_TTL_MINUTES,
        ):
            return False
        sent_by_date[date_key] = _state_entry(fingerprint, "pending")
        _write_gate_notify_state(
            path=path,
            fingerprint=fingerprint,
            date_key=date_key,
            sent_by_date=sent_by_date,
            status="pending",
        )
        return True


def mark_gate_notification_sent(
    *,
    gate_reasons: list[str],
    state_path: str | Path,
    run_date: str = "",
) -> None:
    path = Path(state_path)
    with advisory_lock(path):
        fingerprint = gate_reason_fingerprint(gate_reasons)
        date_key = normalize_gate_run_date(run_date)
        state = _read_gate_notify_state(path)
        sent_by_date = state.get("sent_by_date", {})
        if not isinstance(sent_by_date, dict):
            sent_by_date = {}
        sent_by_date[date_key] = _state_entry(fingerprint, "sent")

        _write_gate_notify_state(
            path=path,
            fingerprint=fingerprint,
            date_key=date_key,
            sent_by_date=sent_by_date,
            status="sent",
        )


def mark_gate_notification_failed(
    *,
    gate_reasons: list[str],
    state_path: str | Path,
    run_date: str = "",
) -> None:
    path = Path(state_path)
    with advisory_lock(path):
        fingerprint = gate_reason_fingerprint(gate_reasons)
        date_key = normalize_gate_run_date(run_date)
        state = _read_gate_notify_state(path)
        sent_by_date = state.get("sent_by_date", {})
        if not isinstance(sent_by_date, dict):
            sent_by_date = {}
        sent_by_date[date_key] = _state_entry(fingerprint, "failed")

        _write_gate_notify_state(
            path=path,
            fingerprint=fingerprint,
            date_key=date_key,
            sent_by_date=sent_by_date,
            status="failed",
        )


def _write_gate_notify_state(
    *,
    path: Path,
    fingerprint: str,
    date_key: str,
    sent_by_date: dict[str, object],
    status: str,
) -> None:
    atomic_write_text(
        path,
        json.dumps(
            {
                "fingerprint": fingerprint,
                "run_date": date_key,
                "sent_by_date": sent_by_date,
                "normalized_reasons": fingerprint.split("|"),
                "status": status,
                "updated_at": now_shanghai().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def build_gate_notification_markdown(
    *,
    run_date: str,
    gate_reasons: list[str],
    next_actions: list[str],
) -> str:
    lines = [
        f"# 通知未放行-{run_date}",
        "",
        "## 结论",
        "",
        "- 本次正常通知未放行。",
    ]
    if gate_reasons:
        lines.extend(["", "## 阻塞原因", ""])
        lines.extend(f"- {reason}" for reason in gate_reasons[:4])
    if next_actions:
        lines.extend(["", "## 处理", ""])
        lines.extend(f"- {action}" for action in next_actions[:3])
    return "\n".join(lines)


def gate_reason_fingerprint(gate_reasons: list[str]) -> str:
    normalized: list[str] = []
    for reason in gate_reasons:
        token = _normalize_gate_reason(reason)
        if token not in normalized:
            normalized.append(token)
    return "|".join(normalized) or "blocked_unknown"


def _normalize_gate_reason(reason: str) -> str:
    text = str(reason).strip()
    if text.startswith("冷启动未满"):
        return "cold_start"
    if text.startswith("DSR 未过门"):
        return "dsr"
    if text.startswith("PBO 未过门"):
        return "pbo"
    if text.startswith("双门 sidecar 不存在"):
        return "sidecar_missing"
    if text.startswith("双门 sidecar 解析失败"):
        return "sidecar_parse_failed"
    if text.startswith("双门 sidecar run_date 异常"):
        return "run_date_invalid"
    if text.startswith("双门结果过期"):
        return "gate_stale"
    if text.startswith("双门 sidecar 无有效回测周期"):
        return "n_periods_invalid"
    if text.startswith("双门 sidecar 的 data_end 格式异常"):
        return "data_end_invalid"
    if text.startswith("双门成绩用了 held-out 数据"):
        return "heldout_contaminated"
    if text.startswith("双门全市场覆盖缺失"):
        return "market_coverage_missing"
    if text.startswith("双门全市场覆盖不足"):
        return "market_coverage_insufficient"
    if text.startswith("DSR pass 标志无效"):
        return "dsr_flag_invalid"
    if text.startswith("PBO pass 标志无效"):
        return "pbo_flag_invalid"
    if text.startswith("PBO 有效性标志无效"):
        return "pbo_valid_flag_invalid"
    if text.startswith("双门总标志无效"):
        return "both_pass_flag_invalid"
    return text


def _read_gate_notify_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _state_entry(fingerprint: str, status: str) -> dict[str, str]:
    return {
        "fingerprint": fingerprint,
        "status": status,
        "updated_at": now_shanghai().isoformat(timespec="seconds"),
    }


def _entry_fingerprint(entry: object) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return str(entry.get("fingerprint") or "")
    return ""


def _entry_status(entry: object) -> str:
    if isinstance(entry, dict):
        return str(entry.get("status") or "")
    return "sent" if isinstance(entry, str) else ""


def _entry_has_status(
    entry: object,
    fingerprint: str,
    statuses: set[str],
) -> bool:
    return _entry_fingerprint(entry) == fingerprint and _entry_status(entry) in statuses


def _entry_recent(
    entry: object,
    fingerprint: str,
    *,
    statuses: set[str],
    ttl_minutes: int,
) -> bool:
    if not _entry_has_status(entry, fingerprint, statuses):
        return False
    if isinstance(entry, str):
        return False
    updated_at = str(entry.get("updated_at") or "")
    try:
        updated = datetime.fromisoformat(updated_at)
    except ValueError:
        return False
    return (now_shanghai() - updated).total_seconds() < max(ttl_minutes, 1) * 60


def _coerce_legacy_day_entry(
    state: dict[str, object],
    entry: object,
    fingerprint: str,
    date_key: str,
) -> object:
    if not isinstance(entry, str) or entry != fingerprint:
        return entry
    if state.get("fingerprint") != fingerprint or state.get("run_date") != date_key:
        return entry
    status = str(state.get("status") or "sent")
    return {
        "fingerprint": fingerprint,
        "status": status,
        "updated_at": str(state.get("updated_at") or ""),
    }
