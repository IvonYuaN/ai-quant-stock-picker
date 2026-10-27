"""Durable tenant-local delivery of human position-plan observations.

The caller owns semantic keys and freshness/position validation. This module
freezes each notification; only cancelled work proven not to have sent may
accept a newer valid observation.
No portfolio lock is held across outbound I/O; only this delivery journal is
locked. An uncertain send requires a durable success receipt before recovery.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote

import alert_events as ae
from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai, to_shanghai
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text


@dataclass(frozen=True)
class FollowupDeliveryResult:
    notify: tuple[str, ...]
    suppressed: bool


@dataclass(frozen=True)
class _Batch:
    candidate: dict[str, Any]
    markdown: str
    created_at: str
    sent_at: str | None = None
    uncertain: bool = False
    cancelled: bool = False


def _timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must contain timezone")
    return to_shanghai(parsed).isoformat()


def _candidate(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("candidate must be an object")
    fields = ("key", "position_id", "symbol", "name", "state", "title", "detail", "mode")
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in fields):
        raise ValueError("candidate requires identity, state and explanation")
    if not value["key"].startswith("position-plan|") or len(value["key"]) <= len("position-plan|"):
        raise ValueError("candidate requires a position-plan key")
    _timestamp(value.get("observed_at"))
    if value.get("quote_as_of") is not None:
        _timestamp(value["quote_as_of"])
    # Defensive copy also rejects non-JSON state or non-finite numeric guards.
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _text(value: str) -> str:
    """Keep a field on one Markdown line; do not allow injected links/headings."""
    line = " ".join(value.split())
    return re.sub(r"([\\`*_{}\[\]()<>#!|])", r"\\\1", line)


def _render(candidate: dict[str, Any]) -> str:
    href = "https://lh.ifidy.cn/my/holdings/" + quote(candidate["position_id"], safe="") + "/plan"
    return "\n".join((
        "# 持仓计划有变化 · 待复核", "",
        f"**{_text(candidate['name'])} {_text(candidate['symbol'])}** · {_text(candidate['title'])}",
        _text(candidate["detail"]),
        f"检查时点：{_timestamp(candidate['observed_at'])}",
        f"行情源时点：{_timestamp(candidate['quote_as_of']) if candidate.get('quote_as_of') else '缺失，不能作为当前到价依据'}",
        f"[查看处理计划]({href})", "",
        "到价不等于买卖信号或已成交；请核对当前行情、条件与余仓，真实交易由你决定。",
    ))


def _load_batches(path: Path) -> list[_Batch]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DataError("计划通知凭据损坏，已停止外发") from exc
    try:
        if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1 or not isinstance(raw.get("batches"), list):
            raise ValueError("invalid journal")
        batches = []
        for row in raw["batches"]:
            if not isinstance(row, dict) or any(type(row.get(field)) is not bool for field in ("uncertain", "cancelled")):
                raise ValueError("invalid delivery state")
            candidate = _candidate(row["candidate"])
            if row["markdown"] != _render(candidate):
                raise ValueError("frozen message does not match evidence")
            batches.append(_Batch(candidate, row["markdown"], _timestamp(row["created_at"]),
                                  _timestamp(row["sent_at"]) if row.get("sent_at") is not None else None,
                                  row["uncertain"], row["cancelled"]))
        if len({batch.candidate["key"] for batch in batches}) != len(batches):
            raise ValueError("duplicate delivery identity")
        return batches
    except (KeyError, ValueError, TypeError) as exc:
        raise DataError("计划通知凭据结构无效，已停止外发") from exc


def _save_batches(path: Path, batches: list[_Batch]) -> None:
    atomic_write_text(path, json.dumps({"version": 1, "batches": [asdict(batch) for batch in batches]},
                                      ensure_ascii=False, sort_keys=True, allow_nan=False))


def _runtime_state(path: Path) -> dict[str, Any]:
    """Validate all receipt sections before runtime can silently repair bad JSON."""
    with advisory_lock(path):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DataError("计划通知外发凭据损坏，已停止外发") from exc
    try:
        if not isinstance(state, dict):
            raise ValueError("invalid runtime state")
        for section in ("sent", "pending", "failed"):
            records = state.get(section, {})
            if not isinstance(records, dict):
                raise ValueError("invalid receipt section")
            for key, entry in records.items():
                if not key.startswith("position-plan|") or not isinstance(entry, dict) or entry.get("fingerprint") != key:
                    raise ValueError("invalid receipt identity")
                _timestamp(entry.get("updated_at"))
                digest = entry.get("content_hash")
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise ValueError("invalid receipt content hash")
        return state
    except (TypeError, ValueError) as exc:
        raise DataError("计划通知外发凭据结构无效，已停止外发") from exc


def _sent_receipt(batch: _Batch, state: dict[str, Any]) -> str | None:
    from aqsp.notification_runtime import notification_content_hash

    receipt = state.get("sent", {}).get(batch.candidate["key"])
    if receipt is None:
        return None
    if receipt["content_hash"] != notification_content_hash(batch.markdown):
        raise DataError("计划通知成功凭据与冻结内容不一致，已停止外发")
    return _timestamp(receipt["updated_at"])


def _updated_observation(previous: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Re-arm only the same semantic event with newer, non-regressing evidence."""
    if any(previous[field] != candidate[field] for field in ("position_id", "symbol", "state", "mode")):
        return False
    if datetime.fromisoformat(candidate["observed_at"]) <= datetime.fromisoformat(previous["observed_at"]):
        return False
    previous_source = previous.get("quote_as_of")
    current_source = candidate.get("quote_as_of")
    return previous_source is None or (
        current_source is not None
        and datetime.fromisoformat(current_source) >= datetime.fromisoformat(previous_source)
    )


def deliver_followup_notifications(
    candidates: list[dict[str, Any]], *, validate: Callable[[dict[str, Any]], bool],
) -> FollowupDeliveryResult:
    """Deliver each stable semantic key once, with restart-safe uncertain sends.

    The caller must encode its frequency period in ``key`` and only call this
    function when notifications are enabled. ``validate`` receives the original
    frozen candidate (or newer observation when cancelled unsent work recovers),
    must recheck current ownership/plan/freshness, and must
    release any portfolio locks before returning. OSError/DataError propagate;
    an incomplete durable write never licenses another outbound attempt.
    """
    from aqsp.notification_runtime import dispatch_notification_once

    incoming: dict[str, dict[str, Any]] = {}
    for value in candidates:
        candidate = _candidate(value)
        key = candidate["key"]
        if key in incoming and incoming[key] != candidate:
            raise ValueError("同一计划通知键对应冲突内容，未外发")
        incoming[key] = candidate
    root = Path(ae._tenant_dir())
    journal = root / "position-plan-delivery.json"
    notify_state = root / "notify-state-position-plan.json"
    messages: list[str] = []
    suppressed = False
    with advisory_lock(journal):
        batches = _load_batches(journal)
        state = _runtime_state(notify_state)
        for index, batch in enumerate(batches):
            sent_at = _sent_receipt(batch, state)
            if batch.sent_at is None and sent_at:
                batches[index] = replace(batch, sent_at=sent_at, uncertain=False)
                _save_batches(journal, batches)
        known = {batch.candidate["key"] for batch in batches}
        for key, candidate in incoming.items():
            if key not in known:
                batches.append(_Batch(deepcopy(candidate), _render(candidate), now_shanghai().isoformat()))
                _save_batches(journal, batches)
        for index, batch in enumerate(batches):
            key = batch.candidate["key"]
            if key not in incoming:
                continue
            if batch.sent_at:
                suppressed = True
                continue
            if batch.cancelled:
                candidate = incoming[key]
                if (batch.uncertain or key in state.get("pending", {})
                        or not _updated_observation(batch.candidate, candidate)
                        or not validate(deepcopy(candidate))):
                    suppressed = True
                    continue
                # Keep the runtime key and failed receipt: refreshed content must
                # not bypass the existing failure cooldown. Never do this on the
                # same iteration that first invalidates an old frozen candidate.
                batch = _Batch(deepcopy(candidate), _render(candidate), now_shanghai().isoformat())
                batches[index] = batch
                _save_batches(journal, batches)
            if batch.uncertain:
                suppressed = True
                messages.append("position-plan blocked: 上次外发结果未确认，已停止重复发送；请核对渠道记录")
                continue
            if not validate(deepcopy(batch.candidate)):
                batches[index] = replace(batch, cancelled=True)
                _save_batches(journal, batches)
                suppressed = True
                continue
            batches[index] = replace(batch, uncertain=True)
            _save_batches(journal, batches)
            results = dispatch_notification_once(batch.markdown, mode="summary", prefix="position-plan",
                                                 kind=key, state_path=notify_state)
            sent_at = _sent_receipt(batch, _runtime_state(notify_state))
            if sent_at:
                batches[index] = replace(batch, sent_at=sent_at, uncertain=False)
                _save_batches(journal, batches)
                messages.append("position-plan: 计划变化通知已发送")
            elif any(result.ok for result in results):
                # A channel report alone cannot replace its durable receipt.
                suppressed = True
                messages.append("position-plan blocked: 外发凭据未确认，已停止重复发送；请核对渠道记录")
            else:
                batches[index] = replace(batch, uncertain=False)
                _save_batches(journal, batches)
                suppressed = True
                messages.append("position-plan: 通知尚未发送或未确认；保留跟进状态，受发送冷却限制")
    return FollowupDeliveryResult(tuple(messages), suppressed)
