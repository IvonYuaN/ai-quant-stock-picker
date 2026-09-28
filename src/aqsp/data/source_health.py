from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

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
    response_time_ms: float | None = None,
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
        source["consecutive_failures"] = 0

        # Record response time
        if response_time_ms is not None:
            source.setdefault("response_times", [])
            source["response_times"].append(response_time_ms)
            # Keep last 100 response times
            source["response_times"] = source["response_times"][-100:]
            source["avg_response_time_ms"] = sum(source["response_times"]) / len(source["response_times"])

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
            source["consecutive_failures"] = source.get("consecutive_failures", 0) + 1
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
        consecutive_failures = int(stats.get("consecutive_failures", 0))
        has_success = 0 if last_success > 0 else 1
        return (
            has_success,
            consecutive_failures,
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


# ========== Enhanced Health Monitoring ==========


@dataclass
class HealthCheckResult:
    """单次健康检查结果"""
    source_id: str
    success: bool
    response_time_ms: float
    error_message: str = ""
    checked_at: str = field(default_factory=lambda: now_shanghai().isoformat(timespec="seconds"))


@dataclass
class SourceHealthStatus:
    """数据源健康状态"""
    source_id: str
    is_healthy: bool
    consecutive_failures: int
    success_rate: float
    avg_response_time_ms: float
    last_success: str
    last_failure: str
    last_error: str
    total_checks: int

    def should_alert(self, failure_threshold: int = 3) -> bool:
        """是否需要发送告警"""
        return self.consecutive_failures >= failure_threshold


class DataSourceMonitor:
    """数据源主动健康监控器"""

    def __init__(
        self,
        health_path: str | Path | None = None,
        check_timeout_seconds: float = 10.0,
        failure_threshold: int = 3,
    ):
        self.health_path = health_path
        self.check_timeout_seconds = check_timeout_seconds
        self.failure_threshold = failure_threshold
        self._lock = threading.Lock()
        self._logger = logging.getLogger(f"{__name__}.DataSourceMonitor")

    def check_source_health(
        self,
        source_id: str,
        check_func: Callable[[], bool],
    ) -> HealthCheckResult:
        """执行单个数据源的健康检查

        Args:
            source_id: 数据源ID
            check_func: 健康检查函数，返回True表示健康

        Returns:
            HealthCheckResult: 检查结果
        """
        start_time = time.time()
        try:
            success = check_func()
            response_time_ms = (time.time() - start_time) * 1000
            return HealthCheckResult(
                source_id=source_id,
                success=success,
                response_time_ms=response_time_ms,
            )
        except Exception as exc:
            response_time_ms = (time.time() - start_time) * 1000
            error_msg = f"{type(exc).__name__}: {str(exc)}"
            self._logger.warning(f"数据源 {source_id} 健康检查失败: {error_msg}")
            return HealthCheckResult(
                source_id=source_id,
                success=False,
                response_time_ms=response_time_ms,
                error_message=error_msg,
            )

    def record_check_result(self, result: HealthCheckResult) -> None:
        """记录健康检查结果到持久化存储"""
        if result.success:
            record_source_success(
                requested_source=result.source_id,
                actual_source=result.source_id,
                path=self.health_path,
                response_time_ms=result.response_time_ms,
            )
        else:
            record_source_failure(
                requested_source=result.source_id,
                error_message=result.error_message,
                path=self.health_path,
            )

    def get_source_status(self, source_id: str) -> SourceHealthStatus:
        """获取数据源当前健康状态"""
        health = read_source_health(self.health_path)
        sources = health.get("sources", {})
        source_data = sources.get(source_id, {})

        successes = int(source_data.get("successes", 0))
        failures = int(source_data.get("failures", 0))
        total_checks = successes + failures
        success_rate = successes / total_checks if total_checks > 0 else 0.0

        consecutive_failures = int(source_data.get("consecutive_failures", 0))
        is_healthy = consecutive_failures < self.failure_threshold

        return SourceHealthStatus(
            source_id=source_id,
            is_healthy=is_healthy,
            consecutive_failures=consecutive_failures,
            success_rate=success_rate,
            avg_response_time_ms=float(source_data.get("avg_response_time_ms", 0.0)),
            last_success=str(source_data.get("last_success", "")),
            last_failure=str(source_data.get("last_failure", "")),
            last_error=str(source_data.get("last_error", "")),
            total_checks=total_checks,
        )

    def get_all_source_statuses(self) -> dict[str, SourceHealthStatus]:
        """获取所有数据源的健康状态"""
        health = read_source_health(self.health_path)
        sources = health.get("sources", {})
        return {
            source_id: self.get_source_status(source_id)
            for source_id in sources
        }

    def get_unhealthy_sources(self) -> list[SourceHealthStatus]:
        """获取所有不健康的数据源"""
        all_statuses = self.get_all_source_statuses()
        return [
            status for status in all_statuses.values()
            if not status.is_healthy
        ]

    def should_trigger_failover(self, source_id: str) -> bool:
        """判断是否应该触发故障切换"""
        status = self.get_source_status(source_id)
        return status.consecutive_failures >= self.failure_threshold

    def generate_health_report(self) -> dict[str, Any]:
        """生成健康报告"""
        all_statuses = self.get_all_source_statuses()
        unhealthy = [s for s in all_statuses.values() if not s.is_healthy]

        report = {
            "generated_at": now_shanghai().isoformat(timespec="seconds"),
            "total_sources": len(all_statuses),
            "healthy_sources": len(all_statuses) - len(unhealthy),
            "unhealthy_sources": len(unhealthy),
            "sources": {},
        }

        for source_id, status in all_statuses.items():
            report["sources"][source_id] = {
                "is_healthy": status.is_healthy,
                "consecutive_failures": status.consecutive_failures,
                "success_rate": round(status.success_rate, 3),
                "avg_response_time_ms": round(status.avg_response_time_ms, 2),
                "last_success": status.last_success,
                "last_failure": status.last_failure,
                "last_error": status.last_error[:200] if status.last_error else "",
            }

        return report


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
            "consecutive_failures": 0,
            "last_success": "",
            "last_failure": "",
            "last_actual_source": "",
            "last_error": "",
            "response_times": [],
            "avg_response_time_ms": 0.0,
        },
    )
    return bucket


def _write_source_health(
    health: dict[str, Any],
    path: str | Path | None = None,
) -> None:
    resolved = source_health_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
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
