"""Serialize position notifications with durable, disjoint member receipts.

The tenant journal freezes each batch identity before outbound work. Successful
members remain consumed even when alert-journal acknowledgements fail or the
next check has different rules. The shared notifier's sent receipts recover the
window between a successful delivery and this journal's next durable write.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import alert_events as ae
from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai, to_shanghai
from aqsp.notifier import format_notify_results
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text


class AcknowledgeMembers(Protocol):
    def __call__(self, event_ids: Sequence[str], *, notified_at: str) -> None: ...


@dataclass(frozen=True)
class PositionDeliveryResult:
    notify: tuple[str, ...]
    notify_suppressed: bool


@dataclass(frozen=True)
class _Member:
    event_id: str
    period: str

    @property
    def key(self) -> tuple[str, str]:
        return self.event_id, self.period


@dataclass(frozen=True)
class _Batch:
    kind: str
    created_at: str
    members: tuple[_Member, ...]
    events: tuple[dict[str, Any], ...]
    markdown: str
    sent_at: str | None = None
    acknowledged: bool = False
    cancelled: bool = False
    uncertain: bool = False


def _timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("delivery timestamp must be an ISO timestamp")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("delivery timestamp must include a timezone")
    return to_shanghai(stamp).isoformat()


def _load_batches(path: Path) -> list[_Batch]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DataError("通知成员凭据损坏，已停止外发") from exc
    try:
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("unknown delivery journal format")
        records = raw["batches"]
        if not isinstance(records, list):
            raise ValueError("batches must be a list")
        batches = []
        for row in records:
            if not isinstance(row, dict):
                raise ValueError("batch must be an object")
            members = tuple(_Member(**member) for member in row["members"])
            if not members or any(
                not isinstance(member.event_id, str)
                or not member.event_id
                or not isinstance(member.period, str)
                or not member.period
                for member in members
            ):
                raise ValueError("invalid delivery members")
            if len({member.key for member in members}) != len(members):
                raise ValueError("duplicate delivery members")
            events = tuple(row["events"])
            if len(events) != len(members) or any(
                not isinstance(event, dict) or event.get("id") != member.event_id
                for event, member in zip(events, members, strict=True)
            ):
                raise ValueError("batch evidence must match members")
            if not isinstance(row["kind"], str) or not row["kind"].startswith(
                "live-risk|"
            ):
                raise ValueError("invalid delivery key")
            if not isinstance(row["markdown"], str):
                raise ValueError("batch markdown must be text")
            batches.append(
                _Batch(
                    kind=row["kind"],
                    created_at=_timestamp(row["created_at"]),
                    members=members,
                    events=events,
                    markdown=row["markdown"],
                    sent_at=_timestamp(row["sent_at"]) if row.get("sent_at") else None,
                    acknowledged=row.get("acknowledged") is True,
                    cancelled=row.get("cancelled") is True,
                    uncertain=row.get("uncertain") is True,
                )
            )
        if len({batch.kind for batch in batches}) != len(batches):
            raise ValueError("duplicate batch identity")
        return batches
    except (KeyError, TypeError, ValueError) as exc:
        raise DataError("通知成员凭据结构无效，已停止外发") from exc


def _save_batches(path: Path, batches: Sequence[_Batch]) -> None:
    atomic_write_text(
        path,
        json.dumps(
            {"version": 1, "batches": [asdict(batch) for batch in batches]},
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def _sent_receipt(batch: _Batch, state_path: Path) -> str | None:
    """Only sent is evidence; pending/failed/cooldown never means delivery."""
    with advisory_lock(state_path):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DataError("通知外发凭据损坏，已停止外发") from exc
    if not isinstance(state, dict) or not isinstance(state.get("sent", {}), dict):
        raise DataError("通知外发凭据结构无效，已停止外发")
    receipt = state.get("sent", {}).get(batch.kind)
    if receipt is None:
        return None
    fingerprint = receipt.get("fingerprint") if isinstance(receipt, dict) else receipt
    if not isinstance(fingerprint, str) or not (
        fingerprint == batch.kind
        or re.fullmatch(re.escape(batch.kind) + r":[0-9a-f]{64}", fingerprint)
    ):
        raise DataError("通知成功凭据与批次不一致，已停止外发")
    if isinstance(receipt, str):
        return batch.created_at
    try:
        return _timestamp(receipt.get("updated_at"))
    except ValueError as exc:
        raise DataError("通知成功凭据缺少有效时间，已停止外发") from exc


def _member(event: dict[str, Any], day: str) -> _Member:
    mode = event.get("freq_mode", "once")
    period = day if mode == "daily" else "always" if mode == "always" else "once"
    return _Member(str(event["id"]), period)


def deliver_position_alerts(
    eligible_rules: set[str],
    checked_at: str,
    *,
    render: Callable[[list[dict[str, Any]]], str],
    acknowledge: AcknowledgeMembers,
) -> PositionDeliveryResult:
    """Deliver position batches while preserving each member's frequency quota.

    ``acknowledge`` atomically records all ids with the supplied source send time.
    The tenant lock covers candidate selection, sends and acknowledgements, so a
    concurrent manual check/monitor cannot reserve overlapping members. A send
    Known send failures retain the notifier's pending/failure retry TTL. A crash
    or exception with no successful receipt blocks that uncertain batch rather
    than risking a duplicate delivery; other, disjoint members remain eligible.
    No caller should invoke this function for a read-only/notify=False check.
    """
    from aqsp.notification_runtime import dispatch_notification_once

    stamp = _timestamp(checked_at)
    day = stamp[:10]
    root = Path(ae._tenant_dir())
    journal_path = root / "live-risk-delivery.json"
    notify_path = root / "notify-state-live-risk.json"
    messages: list[str] = []
    suppressed = False
    with advisory_lock(journal_path):
        batches = _load_batches(journal_path)
        # Recover and acknowledge before selecting any new group composition.
        for index, batch in enumerate(batches):
            if batch.cancelled:
                continue
            if batch.sent_at is None:
                sent_at = _sent_receipt(batch, notify_path)
                if sent_at:
                    batch = replace(batch, sent_at=sent_at)
                    batches[index] = batch
                    _save_batches(journal_path, batches)
            if batch.sent_at and not batch.acknowledged:
                acknowledge(
                    [member.event_id for member in batch.members],
                    notified_at=batch.sent_at,
                )
                batches[index] = replace(batch, acknowledged=True)
                _save_batches(journal_path, batches)

        consumed = {
            member.key
            for batch in batches
            if batch.sent_at
            for member in batch.members
            if member.period != "always"
        }
        candidates: dict[tuple[str, str], dict[str, Any]] = {}
        for event in ae.pending_notifications(eligible_rules):
            member = _member(event, day)
            if member.key in consumed:
                continue
            # Before grouped delivery, the notifier used one stable key per
            # event. Recover those successful receipts during the upgrade too.
            if member.period != "always":
                view = {**(event.get("delivery_view") or event), "id": event["id"]}
                legacy = _Batch(
                    kind=f"live-risk|{member.event_id}|{member.period}",
                    created_at=stamp,
                    members=(member,),
                    events=(view,),
                    markdown=render([view]),
                )
                sent_at = _sent_receipt(legacy, notify_path)
                if sent_at:
                    legacy = replace(legacy, sent_at=sent_at)
                    batches.append(legacy)
                    _save_batches(journal_path, batches)
                    acknowledge([member.event_id], notified_at=sent_at)
                    batches[-1] = replace(legacy, acknowledged=True)
                    _save_batches(journal_path, batches)
                    consumed.add(member.key)
                    continue
            candidates[member.key] = event
        assigned: set[tuple[str, str]] = set()
        attempt: list[int] = []
        for index, batch in enumerate(batches):
            if batch.sent_at or batch.cancelled:
                continue
            if batch.uncertain:
                assigned.update(member.key for member in batch.members)
                suppressed = True
                messages.append(
                    "live-risk blocked: 上次外发结果未确认，已停止重复发送；请核对渠道记录"
                )
                continue
            active = [
                (member, event)
                for member, event in zip(batch.members, batch.events, strict=True)
                if member.key in candidates
            ]
            if not active:
                batches[index] = replace(batch, cancelled=True)
                _save_batches(journal_path, batches)
                continue
            members, events = zip(*active, strict=True)
            if len(members) != len(batch.members):
                batch = replace(
                    batch,
                    members=members,
                    events=events,
                    markdown=render(list(events)),
                )
                batches[index] = batch
                _save_batches(journal_path, batches)
            assigned.update(member.key for member in members)
            attempt.append(index)

        groups: dict[tuple[str, str, bool], list[dict[str, Any]]] = {}
        for key, event in candidates.items():
            if key not in assigned:
                position = (
                    str(event.get("symbol") or ""),
                    str(event.get("position_id") or ""),
                    event.get("sellable_now") is True,
                )
                groups.setdefault(position, []).append(event)
        for events in groups.values():
            views = tuple(
                {**(event.get("delivery_view") or event), "id": event["id"]}
                for event in events
            )
            batch = _Batch(
                kind=f"live-risk|batch|{uuid4().hex}",
                created_at=stamp,
                members=tuple(_member(event, day) for event in events),
                events=views,
                markdown=render(list(views)),
            )
            batches.append(batch)
            _save_batches(journal_path, batches)
            attempt.append(len(batches) - 1)

        for index in attempt:
            batch = batches[index]
            batches[index] = replace(batch, uncertain=True)
            _save_batches(journal_path, batches)
            results = dispatch_notification_once(
                batch.markdown,
                mode="summary",
                prefix="live-risk",
                kind=batch.kind,
                state_path=notify_path,
            )
            sent_at = _sent_receipt(batch, notify_path)
            if any(result.ok for result in results):
                sent_at = sent_at or now_shanghai().isoformat(timespec="seconds")
            if sent_at:
                batch = replace(batch, sent_at=sent_at)
                batches[index] = batch
                _save_batches(journal_path, batches)
                acknowledge(
                    [member.event_id for member in batch.members],
                    notified_at=sent_at,
                )
                batches[index] = replace(batch, acknowledged=True)
                _save_batches(journal_path, batches)
            else:
                suppressed = suppressed or not results
                batches[index] = replace(batch, uncertain=False)
                _save_batches(journal_path, batches)
            if results:
                messages.extend(format_notify_results(results, prefix="live-risk"))
    return PositionDeliveryResult(tuple(messages), suppressed)
