from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text

PLAN_SOURCE_IDS = {"auto", "local_first", "online_first", "multi", "csv"}
_logger = logging.getLogger(__name__)


def source_health_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    env_path = os.getenv("AQSP_SOURCE_HEALTH", "").strip()
    if env_path:
        return Path(env_path)
    return Path("data/source_health.json")


def read_source_health(path: str | Path | None = None) -> dict[str, Any]:
    resolved = source_health_path(path)
    if not resolved.exists():
        return _empty_health()
    try:
        return json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        _logger.warning("数据源健康文件损坏，已按空健康状态处理: %s", exc)
        return _empty_health()


def record_source_success(
    requested_source: str,
    actual_source: str,
    *,
    path: str | Path | None = None,
) -> None:
    def update(health: dict[str, Any]) -> dict[str, Any]:
        ts = now_shanghai().isoformat(timespec="seconds")
        fallback_used = requested_source != actual_source

        health["updated_at"] = ts
        health["consecutive_failures"] = 0
        health["last_success"] = ts
        health["last_requested_source"] = requested_source
        health["last_actual_source"] = actual_source
        health["last_error"] = ""
        health["fallback_used"] = fallback_used

        plan = _bucket(health, "plans", requested_source)
        plan["successes"] += 1
        plan["last_success"] = ts
        plan["last_actual_source"] = actual_source
        if fallback_used:
            plan["fallback_successes"] += 1

        source = _bucket(health, "sources", actual_source)
        source["successes"] += 1
        source["last_success"] = ts
        return health

    _update_source_health(path, update)


def record_source_failure(
    requested_source: str,
    error_message: str,
    *,
    path: str | Path | None = None,
) -> None:
    def update(health: dict[str, Any]) -> dict[str, Any]:
        ts = now_shanghai().isoformat(timespec="seconds")

        health["updated_at"] = ts
        health["consecutive_failures"] = int(health.get("consecutive_failures", 0)) + 1
        health["last_failure"] = ts
        health["last_requested_source"] = requested_source
        health["last_error"] = error_message
        health["fallback_used"] = False

        plan = _bucket(health, "plans", requested_source)
        plan["failures"] += 1
        plan["last_failure"] = ts
        plan["last_error"] = error_message

        if requested_source not in PLAN_SOURCE_IDS:
            source = _bucket(health, "sources", requested_source)
            source["failures"] += 1
            source["last_failure"] = ts
            source["last_error"] = error_message
        return health

    _update_source_health(path, update)


def prioritize_source_ids(
    source_ids: list[str],
    *,
    path: str | Path | None = None,
) -> list[str]:
    health = read_source_health(path)
    source_stats = health.get("sources", {})
    base_index = {source_id: idx for idx, source_id in enumerate(source_ids)}

    def sort_key(source_id: str) -> tuple[int, int, int, float, int]:
        stats = source_stats.get(source_id, {})
        last_success = _iso_to_timestamp(stats.get("last_success", ""))
        failures = int(stats.get("failures", 0))
        successes = int(stats.get("successes", 0))
        has_success = 0 if last_success > 0 else 1
        return (
            has_success,
            failures,
            -successes,
            -last_success,
            base_index[source_id],
        )

    return sorted(source_ids, key=sort_key)


def describe_source_health(
    requested_source: str,
    actual_source: str,
    *,
    path: str | Path | None = None,
) -> tuple[str, str, bool]:
    health = read_source_health(path)
    plan = health.get("plans", {}).get(requested_source, {})
    actual = health.get("sources", {}).get(actual_source, {})
    fallback_used = requested_source != actual_source

    plan_successes = int(plan.get("successes", 0))
    plan_failures = int(plan.get("failures", 0))
    source_successes = int(actual.get("successes", 0))
    source_failures = int(actual.get("failures", 0))

    if fallback_used:
        return (
            "fallback",
            f"fallback 到 {actual_source}；plan成功/失败 {plan_successes}/{plan_failures}；源成功/失败 {source_successes}/{source_failures}",
            True,
        )
    if source_failures >= 2 and source_successes == 0:
        return (
            "degraded",
            f"{actual_source} 最近失败偏多；源成功/失败 {source_successes}/{source_failures}",
            False,
        )
    if source_successes == 0 and source_failures == 0:
        return (
            "cold_start",
            f"{actual_source} 暂无健康历史，处于冷启动观察期",
            False,
        )
    return (
        "healthy",
        f"{actual_source} 健康；源成功/失败 {source_successes}/{source_failures}",
        False,
    )


def notification_level_for_health_label(label: str) -> str:
    if label == "degraded":
        return "critical"
    if label in {"fallback", "cold_start"}:
        return "warning"
    if label == "healthy":
        return "info"
    return "info"


@dataclass(frozen=True)
class SourceAuthState:
    source_id: str
    status: str
    message: str
    checked_at: str


def record_source_auth(
    source_id: str,
    status: str,
    message: str,
    *,
    path: str | Path | None = None,
) -> None:
    def update(health: dict[str, Any]) -> dict[str, Any]:
        auth = health.setdefault("auth", {})
        auth[source_id] = {
            "status": status,
            "message": message,
            "checked_at": now_shanghai().isoformat(timespec="seconds"),
        }
        return health

    _update_source_health(path, update)


def read_source_auth(
    source_id: str,
    *,
    path: str | Path | None = None,
) -> SourceAuthState | None:
    health = read_source_health(path)
    raw = health.get("auth", {}).get(source_id)
    if not isinstance(raw, dict):
        return None
    return SourceAuthState(
        source_id=source_id,
        status=str(raw.get("status", "") or ""),
        message=str(raw.get("message", "") or ""),
        checked_at=str(raw.get("checked_at", "") or ""),
    )


def _empty_health() -> dict[str, Any]:
    return {
        "updated_at": "",
        "consecutive_failures": 0,
        "last_success": "",
        "last_failure": "",
        "last_requested_source": "",
        "last_actual_source": "",
        "last_error": "",
        "fallback_used": False,
        "plans": {},
        "sources": {},
        "auth": {},
    }


def _bucket(health: dict[str, Any], section: str, key: str) -> dict[str, Any]:
    section_data = health.setdefault(section, {})
    bucket = section_data.setdefault(
        key,
        {
            "successes": 0,
            "failures": 0,
            "fallback_successes": 0,
            "last_success": "",
            "last_failure": "",
            "last_actual_source": "",
            "last_error": "",
        },
    )
    return bucket


def _write_source_health(
    health: dict[str, Any],
    path: str | Path | None = None,
) -> None:
    resolved = source_health_path(path)
    atomic_write_text(
        resolved,
        json.dumps(health, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _update_source_health(
    path: str | Path | None,
    update: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    resolved = source_health_path(path)
    with advisory_lock(resolved):
        health = read_source_health(resolved)
        _write_source_health(update(health), resolved)


def _iso_to_timestamp(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0
