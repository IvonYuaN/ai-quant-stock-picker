from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml

from aqsp.core.time import today_shanghai
from aqsp.ledger.base import read_ledger


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

            conn = sqlite3.connect(str(cache_path))
            cursor = conn.cursor()

            cursor.execute("""
                SELECT MAX(date) as latest_date 
                FROM ohlcv 
                WHERE symbol != '000300'
            """)
            row = cursor.fetchone()
            conn.close()

            if not row or not row[0]:
                return MonitorResult(
                    name="stale_data",
                    triggered=True,
                    severity="critical",
                    message="缓存中无数据",
                )

            latest_date = date.fromisoformat(row[0])
            today = today_shanghai()
            lag_days = (today - latest_date).days

            if lag_days > max_lag_days:
                return MonitorResult(
                    name="stale_data",
                    triggered=True,
                    severity="critical",
                    message=f"数据滞后 {lag_days} 天，超过阈值 {max_lag_days} 天",
                    details={
                        "latest_date": latest_date.isoformat(),
                        "lag_days": lag_days,
                        "max_lag_days": max_lag_days,
                    },
                )

            return MonitorResult(
                name="stale_data",
                triggered=False,
                severity="critical",
                message=f"数据新鲜度正常，滞后 {lag_days} 天",
                details={"latest_date": latest_date.isoformat(), "lag_days": lag_days},
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
            from aqsp.risk.circuit_breaker import CircuitBreaker

            breaker = CircuitBreaker()
            if breaker.is_in_cooldown():
                return MonitorResult(
                    name="circuit_breaker",
                    triggered=True,
                    severity="critical",
                    message="组合熔断冷却期中",
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
