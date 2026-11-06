"""Tenant-scoped human review decisions for immutable research variants."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
import threading
from typing import Any, Literal
from uuid import uuid4

import aqsp_bridge
import portfolio as pf
import tenant
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock

VariantReviewChoice = Literal["continue_validation", "defer", "reject"]
_CHOICES = frozenset({"continue_validation", "defer", "reject"})
_LOCK = threading.RLock()


class VariantReviewConflict(ValueError):
    """The source artifact or review revision changed after it was read."""


class VariantReviewUnavailable(ValueError):
    """A current immutable research artifact cannot be verified."""


def _file() -> str:
    tenant_id = tenant.current_tenant.get()
    root = pf.CACHE_DIR if tenant_id == "local" else os.path.join(pf.CACHE_DIR, "users", tenant_id)
    return os.path.join(root, "research_variant_decisions.json")


def _load(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {"schema_version": 1, "events": []}
    try:
        with open(path, encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("研究复核记录不可读取，已停止写入以保护原记录") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("events"), list)
        or any(not isinstance(event, dict) for event in payload["events"])
    ):
        raise ValueError("研究复核记录格式无效，已停止写入以保护原记录")
    return payload


def _save(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _current_artifacts() -> tuple[str, str, list[dict[str, Any]]]:
    try:
        snapshot = aqsp_bridge.load_surface().current
        suite = snapshot.variant_suite
        batch_id = str(suite.batch_id or "").strip()
        generated_at = str(suite.generated_at or "").strip()
        if not batch_id or not generated_at:
            if not snapshot.variants and not batch_id:
                return batch_id, generated_at, []
            raise ValueError("missing variant batch identity")
        suite_data = asdict(suite)
        artifacts = []
        for variant in snapshot.variants:
            variant_data = asdict(variant)
            identity = {
                "suite": suite_data,
                "variant": variant_data,
            }
            canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            artifacts.append({
                "artifact_id": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                "batch_id": batch_id,
                "generated_at": generated_at,
                "suite": suite_data,
                "variant_id": variant_data["variant_id"],
                "variant": variant_data,
            })
        return batch_id, generated_at, artifacts
    except Exception as exc:  # noqa: BLE001 -- snapshot parser errors stay behind a safe boundary
        raise VariantReviewUnavailable("当前研究变体快照不可用，无法核对复核对象") from exc


def _latest_events(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        artifact_id = str(event.get("artifact_id", ""))
        if artifact_id and (
            artifact_id not in latest
            or int(event.get("revision", 0)) > int(latest[artifact_id].get("revision", 0))
        ):
            latest[artifact_id] = event
    return latest


def list_reviews() -> dict[str, Any]:
    """Return current artifacts with each tenant-owned latest decision summary."""
    batch_id, generated_at, artifacts = _current_artifacts()
    path = _file()
    with _LOCK:
        events = _load(path)["events"]
    latest = _latest_events(events)
    current = []
    for artifact in artifacts:
        decision = latest.get(artifact["artifact_id"])
        decision_summary = ({
            key: decision[key]
            for key in ("id", "choice", "note", "revision", "created_at")
            if key in decision
        } if decision else None)
        current.append({
            "artifact_id": artifact["artifact_id"],
            "batch_id": artifact["batch_id"],
            "generated_at": artifact["generated_at"],
            "variant_id": artifact["variant_id"],
            "revision": int(decision.get("revision", 0)) if decision else 0,
            "latest_decision": decision_summary,
        })
    return {
        "batch_id": batch_id,
        "generated_at": generated_at,
        "artifacts": current,
    }


def list_events() -> list[dict[str, Any]]:
    """Read the personal audit log even when the current variant batch is unavailable."""
    path = _file()
    with _LOCK:
        events = _load(path)["events"]
    return sorted(reversed(events), key=lambda item: str(item.get("created_at", "")), reverse=True)


def record_review(
    *,
    artifact_id: str,
    batch_id: str,
    variant_id: str,
    choice: VariantReviewChoice,
    note: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Append a human research disposition against the exact current artifact."""
    if choice not in _CHOICES:
        raise ValueError("复核选择无效")
    clean_note = note.strip()
    if not clean_note or len(clean_note) > 800:
        raise ValueError("复核理由必须为 1 到 800 个字符")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected_revision 必须是非负整数")

    current_batch_id, _generated_at, artifacts = _current_artifacts()
    artifact = next((
        item for item in artifacts
        if item["artifact_id"] == artifact_id and item["variant"]["variant_id"] == variant_id
    ), None)
    if current_batch_id != batch_id or artifact is None:
        raise VariantReviewConflict("当前研究变体快照已更新，请重新读取后再记录")

    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load(path)
        latest = _latest_events(payload["events"]).get(artifact_id)
        current_revision = int(latest.get("revision", 0)) if latest else 0
        if latest and latest.get("choice") == choice and latest.get("note") == clean_note:
            return latest
        if expected_revision != current_revision:
            raise VariantReviewConflict("该变体的个人复核已被更新，请重新读取")
        event = {
            "id": str(uuid4()),
            "kind": "variant_decision",
            "artifact_id": artifact_id,
            "batch_id": batch_id,
            "variant_id": variant_id,
            "suite": artifact["suite"],
            "variant": artifact["variant"],
            "choice": choice,
            "note": clean_note,
            "revision": current_revision + 1,
            "created_at": now_shanghai().isoformat(timespec="seconds"),
        }
        payload["events"].append(event)
        _save(path, payload)
    return json.loads(json.dumps(event, ensure_ascii=False))


__all__ = [
    "VariantReviewChoice",
    "VariantReviewConflict",
    "VariantReviewUnavailable",
    "list_events",
    "list_reviews",
    "record_review",
]
