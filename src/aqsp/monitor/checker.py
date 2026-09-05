from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml

from aqsp.core.time import is_trading_day, today_shanghai
from aqsp.data.trading_calendar import trading_day_lag
from aqsp.ledger.base import (
    is_ledger_row_paper_review_eligible,
    read_ledger,
)
from aqsp.paper import read_paper_trades
from aqsp.ratings import is_tradable_rating
from aqsp.walkforward_gate import MAX_GATE_AGE_DAYS, validate_walkforward_gate_payload

# 这些 production status 值代表「本周自评估确实跑过且失败/未完成」，监控必须标
# triggered（critical），绝不能当作 skipped 或健康。注意包含 "failed"/"failed_metadata"：
# 子进程非 0 退出时 wrapper 写 status="failed"，但 gate sidecar 可能未刷新，
# 过去正是这类失败导致「状态文件明写 failed，监控却静默」的盲区。
WALKFORWARD_BLOCKED_STATUSES: frozenset[str] = frozenset(
    {
        "blocked_resources",
        "blocked_db",
        "blocked_cutoff",
        "blocked_coverage",
        "blocked_symbols",
        "timeout",
        "failed",
        "failed_metadata",
    }
)


def _read_production_status_status(status_path: Path) -> str | None:
    """读取 walkforward production status 文件的 status 字段；缺失/损坏返回 None。"""
    if not status_path.exists():
        return None
    try:
        raw = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    value = raw.get("status")
    return str(value).strip() if value else None


@dataclass(frozen=True)
class MonitorResult:
    name: str
    triggered: bool
    severity: Literal["critical", "warning", "info"]
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    # True 表示该检查因数据不可用被跳过（如 CI 环境无持久 data/、或
    # 该项被配置为 required=false 而目标文件缺失）。
    # 被跳过的检查 triggered 恒为 False，但**绝不代表该项健康**——
    # 调用方必须显式统计并暴露 skipped 数量，否则 CI 会出现
    # “绿灯但零监控”的假绿。
    skipped: bool = False


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
                elif monitor.check == "screening_liveness":
                    result = self._check_screening_liveness(monitor.params)
                elif monitor.check == "empty_picks":
                    result = self._check_empty_picks(monitor.params)
                elif monitor.check == "daily_report_freshness":
                    result = self._check_daily_report_freshness(monitor.params)
                else:
                    result = MonitorResult(
                        name=monitor.name,
                        triggered=True,
                        severity=monitor.severity,
                        message=f"Unknown check: {monitor.check}",
                        details={"check": monitor.check},
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
                    # 必须透传 skipped：数据依赖型检查在缺失数据时会标记 skipped，
                    # 若此处重建时丢弃，会导致“被跳过 ≠ 健康”的假绿防护失效。
                    skipped=result.skipped,
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
                    skipped=True,
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
            # 数据依赖型检查：在缺少持久数据（CI / 冒烟）环境，gate 本就不该存在。
            # 但若流水线已经写入 production status（如 failed/blocked/timeout），说明本周
            # 自评估确实跑过且失败——这是真实的运行失败，必须标 triggered 而非 skipped，
            # 否则会出现「状态文件明写 failed，监控却报 skipped=健康」的假绿盲区
            # （这正是过去 30+ 次 walkforward-gate 静默失败无人知的根因之一）。
            # 仅当 gate 与 status 都不存在时，才回到 skipped（CI/无数据环境预期如此）。
            failed_status = _read_production_status_status(status_path)
            if (
                failed_status is not None
                and failed_status in WALKFORWARD_BLOCKED_STATUSES
            ):
                return MonitorResult(
                    name="walkforward_runtime",
                    triggered=True,
                    severity="critical",
                    message=(
                        f"walk-forward 未完成: {failed_status}"
                        "（gate 未落盘，但状态文件记录失败）"
                    ),
                    details={
                        "gate_path": str(gate_path),
                        "status_path": str(status_path),
                        "production_status": failed_status,
                    },
                )
            return MonitorResult(
                name="walkforward_runtime",
                triggered=False,
                severity="warning",
                message=(
                    "walk-forward gate 文件缺失，跳过自评估状态检查"
                    "（CI/无持久数据环境预期如此；生产环境应由流水线保证 gate 被写入）"
                ),
                details={"gate_path": str(gate_path), "status_path": str(status_path)},
                skipped=True,
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
        blocked_statuses = WALKFORWARD_BLOCKED_STATUSES
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

    def _check_screening_liveness(self, params: dict[str, Any]) -> MonitorResult:
        """心跳检查：最近一次筛选是否停更（cron/流水线是否还在跑）。

        仅看业务正确性层面的"是否还在产出"，不重复数据新鲜度检查。
        """
        ledger_path = Path(str(params.get("ledger_path", "data/predictions.jsonl")))
        max_staleness = int(params.get("max_staleness_trading_days", 2))
        if max_staleness < 0:
            raise ValueError("max_staleness_trading_days must be non-negative")

        if not ledger_path.exists():
            return MonitorResult(
                name="screening_liveness",
                triggered=False,
                severity="warning",
                message="账本文件不存在，无法判断筛选心跳（CI 环境可能无持久数据）",
                details={"ledger_path": str(ledger_path)},
                skipped=True,
            )

        try:
            rows = read_ledger(str(ledger_path))
        except Exception as e:
            return MonitorResult(
                name="screening_liveness",
                triggered=True,
                severity="critical",
                message=f"读取账本失败: {e}",
                details={"error": str(e)},
            )

        signal_dates = [
            str(r.get("signal_date", "")).strip()
            for r in rows
            if str(r.get("signal_date", "")).strip()
        ]
        if not signal_dates:
            return MonitorResult(
                name="screening_liveness",
                triggered=True,
                severity="critical",
                message="账本无任何 signal_date 记录，筛选可能从未成功运行",
                details={"ledger_path": str(ledger_path)},
            )

        latest = max(signal_dates)
        latest_date = date.fromisoformat(latest)
        today = today_shanghai()
        lag = trading_day_lag(latest_date, today)
        if lag > max_staleness:
            return MonitorResult(
                name="screening_liveness",
                triggered=True,
                severity="critical",
                message=(
                    f"筛选疑似停更：最近信号日 {latest}，滞后 {lag} 个交易日"
                    f"（阈值 {max_staleness}）"
                ),
                details={
                    "latest_signal_date": latest,
                    "trading_lag_days": lag,
                    "max_staleness_trading_days": max_staleness,
                },
            )
        return MonitorResult(
            name="screening_liveness",
            triggered=False,
            severity="critical",
            message=f"筛选心跳正常，最近信号日 {latest}（滞后 {lag} 个交易日）",
            details={"latest_signal_date": latest, "trading_lag_days": lag},
        )

    def _check_empty_picks(self, params: dict[str, Any]) -> MonitorResult:
        """空结果检查：最近一次筛选是否产出 0 只可交易标的却不报警。

        注意：这是业务正确性盲区的关键修复——跑成功但 0 标的，
        walk-forward 与新鲜度检查都不会触发，此处显式补上。
        """
        ledger_path = Path(str(params.get("ledger_path", "data/predictions.jsonl")))
        paper_path = Path(
            str(params.get("paper_ledger_path", "data/paper_trades.jsonl"))
        )
        if not ledger_path.exists():
            return MonitorResult(
                name="empty_picks",
                triggered=False,
                severity="warning",
                message="账本不存在，跳过空结果检查",
                details={"ledger_path": str(ledger_path)},
                skipped=True,
            )

        try:
            rows = read_ledger(str(ledger_path))
        except Exception as e:
            return MonitorResult(
                name="empty_picks",
                triggered=True,
                severity="warning",
                message=f"读取账本失败: {e}",
                details={"error": str(e)},
            )

        signal_dates = [
            str(r.get("signal_date", "")).strip()
            for r in rows
            if str(r.get("signal_date", "")).strip()
        ]
        if not signal_dates:
            return MonitorResult(
                name="empty_picks",
                triggered=False,
                severity="warning",
                message="账本无信号日期，跳过空结果检查",
                skipped=True,
            )

        latest = max(signal_dates)
        latest_rows = [
            r for r in rows if str(r.get("signal_date", "")).strip() == latest
        ]
        tradable = [
            r
            for r in latest_rows
            if is_tradable_rating(str(r.get("rating", "")).strip())
            and is_ledger_row_paper_review_eligible(r)
        ]

        paper_rows: list[dict] = []
        if paper_path.exists():
            try:
                paper_rows = [
                    r
                    for r in read_paper_trades(str(paper_path))
                    if str(r.get("signal_date", "")).strip() == latest
                ]
            except Exception:
                paper_rows = []

        if not tradable and not paper_rows:
            return MonitorResult(
                name="empty_picks",
                triggered=True,
                severity="warning",
                message=f"最近一次筛选（{latest}）未产出任何可交易标的",
                details={"signal_date": latest},
            )
        return MonitorResult(
            name="empty_picks",
            triggered=False,
            severity="warning",
            message=f"最近一次筛选（{latest}）产出 {len(tradable)} 只可交易标的",
            details={"signal_date": latest, "tradable": len(tradable)},
        )

    def _check_daily_report_freshness(self, params: dict[str, Any]) -> MonitorResult:
        """v2 研究日报当日新鲜度：监控当日/最近交易日是否真的落地了 v2 日报。

        对 PR #70「临时 --report 目录被清导致 v2 日报从不落地」业务盲区的监控
        补强——主流程跑成功、其他检查全绿，但 v2 日报若因路径 bug 未落地，此前
        无任何检查能发现。此处显式校验落盘文件的新鲜度。

        路径解析与 ``cli._write_daily_research_report`` 完全一致（PR #70 修复后）：
          1) ``AQSP_DAILY_RESEARCH_REPORT`` 显式覆盖（仅目录则补 daily_report.md）
          2) 否则 ``AQSP_RUNTIME_DATA_ROOT/reports/daily_report.md``
          3) 无 env 时回退 ``params.report_path``（默认 ``reports/daily_report.md``，dev 行为）

        新鲜度用 ``trading_day_lag(report_date, today)``：周末/节假日会把 anchor 退到
        上一交易日，因此周五的日报整个周末都算新鲜，不会误报；交易日滞后超过阈值才触发。
        """
        report_path_env = os.environ.get("AQSP_DAILY_RESEARCH_REPORT", "").strip()
        runtime_data_root = os.environ.get("AQSP_RUNTIME_DATA_ROOT", "").strip()
        if report_path_env:
            report_path = Path(report_path_env)
            if not report_path.suffix:
                report_path = report_path / "daily_report.md"
        elif runtime_data_root:
            report_path = Path(runtime_data_root) / "reports" / "daily_report.md"
        else:
            report_path = Path(
                str(params.get("report_path", "reports/daily_report.md"))
            )

        max_lag_trading_days = int(params.get("max_lag_trading_days", 2))
        if max_lag_trading_days < 0:
            raise ValueError("max_lag_trading_days must be non-negative")

        # 是否有“持久数据环境”信号：生产 cron 会注入 AQSP_RUNTIME_DATA_ROOT
        # （或显式 AQSP_DAILY_RESEARCH_REPORT）；CI/冒烟无这两个 env。
        has_persistent_env = bool(report_path_env or runtime_data_root)

        if not report_path.exists():
            if not has_persistent_env:
                # CI/无持久数据环境：dev 回退路径相对、无持久 data，v2 日报本就不该
                # 存在，无法评估 → 标记 skipped 而非误报（与 stale_data 等约定一致）。
                return MonitorResult(
                    name="daily_report_freshness",
                    triggered=False,
                    severity="warning",
                    message=(
                        "v2 研究日报文件不存在，跳过新鲜度检查"
                        "（CI/无持久数据环境预期如此；生产环境应由 scheduled 运行写入）"
                    ),
                    details={"report_path": str(report_path)},
                    skipped=True,
                )
            # 生产环境（持久 env 已设）却无日报 → 真实故障：v2 日报未落地。
            return MonitorResult(
                name="daily_report_freshness",
                triggered=True,
                severity="critical",
                message=f"v2 研究日报未生成：期望路径 {report_path} 不存在",
                details={"report_path": str(report_path)},
            )

        try:
            mtime = report_path.stat().st_mtime
            report_date = date.fromtimestamp(mtime)
        except OSError as exc:
            return MonitorResult(
                name="daily_report_freshness",
                triggered=True,
                severity="critical",
                message=f"v2 研究日报无法读取: {exc}",
                details={"report_path": str(report_path)},
            )

        today = today_shanghai()
        lag = trading_day_lag(report_date, today)
        if lag > max_lag_trading_days:
            return MonitorResult(
                name="daily_report_freshness",
                triggered=True,
                severity="critical",
                message=(
                    f"v2 研究日报滞后 {lag} 个交易日，超过阈值 {max_lag_trading_days}；"
                    f"最后生成于 {report_date.isoformat()}"
                ),
                details={
                    "report_path": str(report_path),
                    "report_date": report_date.isoformat(),
                    "trading_lag_days": lag,
                    "max_lag_trading_days": max_lag_trading_days,
                    "today": today.isoformat(),
                    "is_trading_day_today": is_trading_day(today),
                },
            )
        return MonitorResult(
            name="daily_report_freshness",
            triggered=False,
            severity="warning",
            message=(
                f"v2 研究日报新鲜度正常，滞后 {lag} 个交易日"
                f"（最后生成于 {report_date.isoformat()}）"
            ),
            details={
                "report_path": str(report_path),
                "report_date": report_date.isoformat(),
                "trading_lag_days": lag,
                "today": today.isoformat(),
                "is_trading_day_today": is_trading_day(today),
            },
        )
