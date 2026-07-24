from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml

from aqsp.core.time import today_shanghai
from aqsp.data.trading_calendar import trading_day_lag
from aqsp.ledger.base import read_ledger
from aqsp.walkforward_gate import MAX_GATE_AGE_DAYS, validate_walkforward_gate_payload


@dataclass(frozen=True)
class MonitorResult:
    name: str
    triggered: bool
    severity: Literal["critical", "warning", "info"]
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MonitorConfig:
    name: str
    description: str
    enabled: bool
    check: str
    params: dict[str, Any]
    severity: Literal["critical", "warning", "info"]


class MonitorChecker:
    def __init__(self, config_path: str = "config/monitors.yaml") -> None:
        self.config = self._load_config(config_path)

    def _load_config(self, config_path: str) -> list[MonitorConfig]:
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Monitor config not found: {config_path}")

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        monitors = []
        for item in data.get("monitors", []):
            monitors.append(
                MonitorConfig(
                    name=item["name"],
                    description=item["description"],
                    enabled=item.get("enabled", True),
                    check=item["check"],
                    params=item.get("params", {}),
                    severity=item.get("severity", "info"),
                )
            )
        return monitors

    def check_all(self) -> list[MonitorResult]:
        results = []
        for monitor in self.config:
            if not monitor.enabled:
                continue

            try:
                if monitor.check == "data_freshness":
                    result = self._check_data_freshness(monitor.params)
                elif monitor.check == "circuit_breaker":
                    result = self._check_circuit_breaker(monitor.params)
                elif monitor.check == "win_rate":
                    result = self._check_win_rate(monitor.params)
                elif monitor.check == "source_health":
                    result = self._check_source_health(monitor.params)
                elif monitor.check == "walkforward_runtime":
                    result = self._check_walkforward_runtime(monitor.params)
                else:
                    result = MonitorResult(
                        name=monitor.name,
                        triggered=False,
                        severity=monitor.severity,
                        message=f"Unknown check: {monitor.check}",
                    )
            except Exception as e:
                result = MonitorResult(
                    name=monitor.name,
                    triggered=True,
                    severity="critical",
                    message=f"Monitor check failed: {e}",
                    details={"error": str(e)},
                )
            if result.severity != "critical":
                result = MonitorResult(
                    name=result.name,
                    triggered=result.triggered,
                    severity=monitor.severity,
                    message=result.message,
                    details=result.details,
                )

            results.append(result)

        return results

    def _check_data_freshness(self, params: dict[str, Any]) -> MonitorResult:
        max_lag_days = params.get("max_lag_days", 3)
        cache_path = Path(str(params.get("cache_path", "data/cache.db")))
        required = bool(params.get("required", False))

        if not cache_path.exists():
            if not required:
                return MonitorResult(
                    name="stale_data",
                    triggered=False,
                    severity="warning",
                    message="数据缓存文件不存在，跳过本地缓存新鲜度检查",
                    details={"cache_path": str(cache_path), "required": required},
                )
            return MonitorResult(
                name="stale_data",
                triggered=True,
                severity="critical",
                message="数据缓存文件不存在",
                details={"cache_path": str(cache_path), "required": required},
            )

        try:
            import sqlite3

            with sqlite3.connect(str(cache_path), timeout=30.0) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT MAX(date) as latest_date
                    FROM ohlcv
                    WHERE symbol != '000300'
                    """
                )
                row = cursor.fetchone()

            if not row or not row[0]:
                return MonitorResult(
                    name="stale_data",
                    triggered=True,
                    severity="critical",
                    message="缓存中无数据",
                )

            latest_date = date.fromisoformat(row[0])
            today = today_shanghai()
            lag_days = trading_day_lag(latest_date, today)

            if lag_days > max_lag_days:
                return MonitorResult(
                    name="stale_data",
                    triggered=True,
                    severity="critical",
                    message=f"数据滞后 {lag_days} 个交易日，超过阈值 {max_lag_days}",
                    details={
                        "latest_date": latest_date.isoformat(),
                        "trading_lag_days": lag_days,
                        "max_trading_lag_days": max_lag_days,
                    },
                )

            return MonitorResult(
                name="stale_data",
                triggered=False,
                severity="critical",
                message=f"数据新鲜度正常，滞后 {lag_days} 个交易日",
                details={
                    "latest_date": latest_date.isoformat(),
                    "trading_lag_days": lag_days,
                },
            )

        except Exception as e:
            return MonitorResult(
                name="stale_data",
                triggered=True,
                severity="critical",
                message=f"检查数据新鲜度失败: {e}",
                details={"error": str(e)},
            )

    def _check_circuit_breaker(self, params: dict[str, Any]) -> MonitorResult:
        try:
            from aqsp.risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
            from aqsp.strategies.thresholds import load_thresholds

            thresholds = load_thresholds()
            breaker = CircuitBreaker(
                config=CircuitBreakerConfig.from_thresholds(thresholds)
            )
            if breaker.is_in_cooldown():
                return MonitorResult(
                    name="circuit_breaker",
                    triggered=False,
                    severity="critical",
                    message="组合保护冷却期中",
                    details={
                        "cooldown_until": breaker._cooldown_until.isoformat()
                        if breaker._cooldown_until
                        else None
                    },
                )

            return MonitorResult(
                name="circuit_breaker",
                triggered=False,
                severity="critical",
                message="组合熔断未触发",
            )

        except Exception as e:
            return MonitorResult(
                name="circuit_breaker",
                triggered=True,
                severity="critical",
                message=f"检查熔断状态失败: {e}",
                details={"error": str(e)},
            )

    def _check_win_rate(self, params: dict[str, Any]) -> MonitorResult:
        min_win_rate = params.get("min_win_rate", 0.3)
        min_samples = params.get("min_samples", 10)

        try:
            ledger_path = "data/predictions.jsonl"
            rows = read_ledger(ledger_path)

            validated = [r for r in rows if r.get("status") == "validated"]
            if len(validated) < min_samples:
                return MonitorResult(
                    name="win_rate_drop",
                    triggered=False,
                    severity="warning",
                    message=f"验证样本不足 {len(validated)}/{min_samples}，跳过胜率检查",
                    details={"samples": len(validated), "min_samples": min_samples},
                )

            wins = sum(1 for r in validated if r.get("win") is True)
            win_rate = wins / len(validated) if validated else 0.0

            if win_rate < min_win_rate:
                return MonitorResult(
                    name="win_rate_drop",
                    triggered=True,
                    severity="warning",
                    message=f"胜率 {win_rate:.1%} 低于阈值 {min_win_rate:.1%}",
                    details={
                        "win_rate": win_rate,
                        "min_win_rate": min_win_rate,
                        "wins": wins,
                        "total": len(validated),
                    },
                )

            return MonitorResult(
                name="win_rate_drop",
                triggered=False,
                severity="warning",
                message=f"胜率正常 {win_rate:.1%}",
                details={"win_rate": win_rate, "wins": wins, "total": len(validated)},
            )

        except Exception as e:
            return MonitorResult(
                name="win_rate_drop",
                triggered=True,
                severity="warning",
                message=f"检查胜率失败: {e}",
                details={"error": str(e)},
            )

    def _check_source_health(self, params: dict[str, Any]) -> MonitorResult:
        max_consecutive_failures = params.get("max_consecutive_failures", 3)

        try:
            health_file = Path("data/source_health.json")
            if not health_file.exists():
                return MonitorResult(
                    name="data_source_failure",
                    triggered=False,
                    severity="warning",
                    message="数据源健康记录文件不存在",
                )

            health_data = json.loads(health_file.read_text(encoding="utf-8"))
            failures = health_data.get("consecutive_failures", 0)

            if failures >= max_consecutive_failures:
                return MonitorResult(
                    name="data_source_failure",
                    triggered=True,
                    severity="warning",
                    message=f"数据源连续失败 {failures} 次，超过阈值 {max_consecutive_failures}",
                    details={
                        "consecutive_failures": failures,
                        "max_consecutive_failures": max_consecutive_failures,
                        "last_failure": health_data.get("last_failure"),
                        "last_requested_source": health_data.get(
                            "last_requested_source"
                        ),
                        "last_actual_source": health_data.get("last_actual_source"),
                        "last_error": health_data.get("last_error"),
                    },
                )

            return MonitorResult(
                name="data_source_failure",
                triggered=False,
                severity="warning",
                message=f"数据源健康，连续失败 {failures} 次",
                details={
                    "consecutive_failures": failures,
                    "last_requested_source": health_data.get("last_requested_source"),
                    "last_actual_source": health_data.get("last_actual_source"),
                },
            )

        except Exception as e:
            return MonitorResult(
                name="data_source_failure",
                triggered=True,
                severity="warning",
                message=f"检查数据源健康失败: {e}",
                details={"error": str(e)},
            )

    def _check_walkforward_runtime(self, params: dict[str, Any]) -> MonitorResult:
        """Expose failed-closed production gate state without treating a failed DSR/PBO as a run failure."""
        runtime_root = Path(os.environ.get("AQSP_RUNTIME_ROOT", "").strip() or ".")
        gate_path = Path(str(params.get("gate_path", "data/walkforward_gate.json")))
        status_path = Path(
            str(params.get("status_path", "data/walkforward_production_status.json"))
        )
        if not gate_path.is_absolute():
            gate_path = runtime_root / gate_path
        if not status_path.is_absolute():
            status_path = runtime_root / status_path
        max_age_days = int(params.get("max_age_days", MAX_GATE_AGE_DAYS))
        if max_age_days < 0:
            raise ValueError("max_age_days must be non-negative")

        if not gate_path.exists():
            return MonitorResult(
                name="walkforward_runtime",
                triggered=True,
                severity="critical",
                message="walk-forward gate 文件缺失，无法验证自评估状态",
                details={"gate_path": str(gate_path), "status_path": str(status_path)},
            )

        try:
            gate_payload = json.loads(gate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return MonitorResult(
                name="walkforward_runtime",
                triggered=True,
                severity="critical",
                message=f"walk-forward gate 无法读取: {exc}",
                details={"gate_path": str(gate_path)},
            )
        if not isinstance(gate_payload, dict):
            return MonitorResult(
                name="walkforward_runtime",
                triggered=True,
                severity="critical",
                message="walk-forward gate 格式无效",
                details={"gate_path": str(gate_path)},
            )

        status_payload: dict[str, Any] = {}
        if status_path.exists():
            try:
                raw_status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return MonitorResult(
                    name="walkforward_runtime",
                    triggered=True,
                    severity="critical",
                    message=f"walk-forward 生产状态无法读取: {exc}",
                    details={"status_path": str(status_path)},
                )
            if not isinstance(raw_status, dict):
                return MonitorResult(
                    name="walkforward_runtime",
                    triggered=True,
                    severity="critical",
                    message="walk-forward 生产状态格式无效",
                    details={"status_path": str(status_path)},
                )
            status_payload = raw_status

        validation = validate_walkforward_gate_payload(
            gate_payload,
            today=today_shanghai(),
            max_age_days=max_age_days,
        )
        status = str(status_payload.get("status") or "missing").strip()
        blocked_statuses = {
            "blocked_resources",
            "blocked_db",
            "blocked_cutoff",
            "blocked_coverage",
            "blocked_symbols",
            "timeout",
        }
        is_stale = any(
            blocker.startswith("gate stale:") for blocker in validation.blockers
        )
        details = {
            "gate_path": str(gate_path),
            "status_path": str(status_path),
            "production_status": status,
            "updated_at": status_payload.get("updated_at"),
            "run_date": gate_payload.get("run_date"),
            "gate_age_days": validation.age_days,
            "gate_blockers": list(validation.blockers),
            "production_detail": status_payload.get("detail"),
        }
        if status in blocked_statuses:
            return MonitorResult(
                name="walkforward_runtime",
                triggered=True,
                severity="critical",
                message=f"walk-forward 未完成: {status}",
                details=details,
            )
        if is_stale:
            return MonitorResult(
                name="walkforward_runtime",
                triggered=True,
                severity="critical",
                message=f"walk-forward gate 已过期: {validation.age_days} 天",
                details=details,
            )
        if status == "missing":
            return MonitorResult(
                name="walkforward_runtime",
                triggered=True,
                severity="critical",
                message="walk-forward 从未写入生产运行状态",
                details=details,
            )
        if validation.ok:
            message = "walk-forward 已完成且双门通过"
        else:
            message = "walk-forward 已完成，但 DSR/PBO 双门未通过"
        return MonitorResult(
            name="walkforward_runtime",
            triggered=False,
            severity="critical",
            message=message,
            details=details,
        )
