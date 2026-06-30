#!/usr/bin/env python3
"""Diagnose local AQSP runtime state without contacting brokers or trading."""

from __future__ import annotations

import json
import os
import platform
import re
import struct
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from aqsp.data.source_health import notification_level_for_health_label
from aqsp.data.registry import list_registry_entries, local_data_status
from aqsp.data.tdx_vipdoc_source import TDX_DAY_RECORD_SIZE
from aqsp.cli import WALKFORWARD_GATE_PATH, _check_notification_gate
from aqsp.ledger.runtime import (
    REAL_SIGNAL_STATUSES,
    cold_start_min_days,
    collect_simulated_signal_dates,
    count_independent_signal_days,
    count_paper_tracking_days,
    ledger_signal_date,
    latest_independent_signal_day,
)
from aqsp.core.time import now_shanghai
from aqsp.notifier import configured_notification_channels
from aqsp.research.summary import load_research_summary, research_findings_display
from aqsp.runtime.gate_notify import gate_reason_fingerprint


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RuntimePaths:
    ledger: Path
    paper_ledger: Path
    risk_state: Path
    walkforward_production_status: Path
    gate_notify_state: Path
    notify_state: Path
    monitor_notify_state: Path
    dashboard: Path
    latest_report: Path
    latest_csv: Path
    sqlite_db: Path


def _runtime_path(env_name: str, default: str) -> Path:
    raw = os.getenv(env_name, default).strip() or default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _load_dotenv_defaults(path: Path | None = None) -> None:
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        clean_value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, clean_value)


def _runtime_paths() -> RuntimePaths:
    return RuntimePaths(
        ledger=_runtime_path("AQSP_LEDGER", "data/predictions.jsonl"),
        paper_ledger=_runtime_path("AQSP_PAPER_LEDGER", "data/paper_trades.jsonl"),
        risk_state=_runtime_path("AQSP_RISK_STATE", "data/risk_state.json"),
        walkforward_production_status=_runtime_path(
            "AQSP_WALKFORWARD_PRODUCTION_STATUS",
            "data/walkforward_production_status.json",
        ),
        gate_notify_state=_runtime_path(
            "AQSP_GATE_NOTIFY_STATE_PATH", "data/gate_notify_state.json"
        ),
        notify_state=_runtime_path("AQSP_NOTIFY_STATE_PATH", "data/notify_state.json"),
        monitor_notify_state=_runtime_path(
            "AQSP_MONITOR_NOTIFY_STATE_PATH", "data/monitor_notify_state.json"
        ),
        dashboard=_runtime_path("AQSP_DASHBOARD", "dist/dashboard/index.html"),
        latest_report=_runtime_path("AQSP_REPORT", "reports/latest.md"),
        latest_csv=_runtime_path("AQSP_OUTPUT_CSV", "reports/latest.csv"),
        sqlite_db=_runtime_path("AQSP_SQLITE_DB_PATH", "data/astocks_raw.db"),
    )


def _default_runtime_path(default: str) -> Path:
    path = Path(default).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"_invalid_json": line[:120]})
    return rows


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_pipeline_history(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((root / "logs" / "pipeline").glob("*.json")):
        payload = _read_json_file(path)
        if not payload:
            continue
        rows.append(
            {
                "date": path.stem,
                "success": payload.get("overall_success") is True,
            }
        )
    return rows


def _read_daily_log_history(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    daily_dir = root / "logs" / "daily"
    if not daily_dir.exists():
        return rows
    for path in sorted(daily_dir.glob("run-*.log")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        segments = re.split(r"(?=^=== aqsp run @ )", text, flags=re.MULTILINE)
        for segment in segments:
            if not segment.startswith("=== aqsp run @ "):
                continue
            match = re.match(r"^=== aqsp run @ (.+?) ===$", segment.splitlines()[0].strip())
            run_date = _daily_log_segment_date(match.group(1) if match else "")
            if not run_date:
                continue
            if "=== outputs ===" not in segment and "=== aqsp dashboard @" not in segment:
                continue
            if "aqsp run failed:" in segment:
                continue
            rows.append({"date": run_date, "success": True})
    return rows


def _daily_log_segment_date(header: str) -> str:
    tokens = header.split()
    if len(tokens) >= 6:
        candidate = " ".join(tokens[:4] + tokens[5:6])
        try:
            return datetime.strptime(candidate, "%a %b %d %H:%M:%S %Y").date().isoformat()
        except ValueError:
            return ""
    return ""


def _file_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    return f"present ({path.stat().st_size} bytes)"


def _successful_run_history_summary(root: Path) -> dict[str, Any]:
    history_path = _runtime_path("AQSP_DAILY_RUN_HISTORY", "data/daily_run_history.jsonl")
    history_rows = _read_jsonl(history_path)
    pipeline_rows = _read_pipeline_history(root)
    daily_log_rows = _read_daily_log_history(root)
    ledger_run_rows = [
        {
            "date": str(row.get("signal_date") or "").strip(),
            "success": True,
        }
        for row in _read_jsonl(_runtime_path("AQSP_LEDGER", "data/predictions.jsonl"))
        if str(row.get("symbol") or "").strip() == "__RUN__"
        and str(row.get("status") or "").strip()
        in {"run_completed_no_picks", "blocked_by_circuit_breaker"}
        and str(row.get("signal_date") or "").strip()
    ]
    merged: dict[str, bool] = {}
    source_labels: list[str] = []
    for label, rows in (
        ("daily_run_history", history_rows),
        ("pipeline_logs", pipeline_rows),
        ("daily_logs", daily_log_rows),
        ("ledger_run_events", ledger_run_rows),
    ):
        any_rows = False
        for row in rows:
            run_date = str(row.get("date") or row.get("run_date") or "").strip()
            if not run_date:
                continue
            any_rows = True
            merged[run_date] = merged.get(run_date, False) or (
                row.get("success") is True or row.get("exit_code") == 0
            )
        if any_rows:
            source_labels.append(label)
    successful_dates = sorted(day for day, ok in merged.items() if ok)
    latest_success = successful_dates[-1] if successful_dates else ""
    return {
        "path": str(history_path),
        "count": len(successful_dates),
        "latest": latest_success,
        "source": "+".join(source_labels) if source_labels else "-",
    }


def _count_log_occurrences(path: Path, marker: str) -> int:
    if not path.exists():
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    return text.count(marker)


def _runtime_cadence_summary(root: Path) -> dict[str, int]:
    today = now_shanghai().date().isoformat()
    return {
        "daily_runs": _count_log_occurrences(
            root / "logs" / "daily" / f"run-{today}.log",
            "=== aqsp run @ ",
        ),
        "news_runs": _count_log_occurrences(
            root / "logs" / "news" / f"news-{today}.log",
            "开始消息面雷达",
        ),
        "monitor_runs": _count_log_occurrences(
            root / "logs" / "monitor" / f"monitor-{today}.log",
            "AQSP 服务器监控开始",
        ),
    }


def _blocked_runtime_day_count(rows: list[dict[str, Any]]) -> int:
    blocked_days = {
        ledger_signal_date(row)
        for row in rows
        if str(row.get("symbol") or "").strip() == "__RUN__"
        and str(row.get("status") or "").strip() == "blocked_by_circuit_breaker"
        and ledger_signal_date(row)
    }
    return len(blocked_days)


def _real_signal_row_count(rows: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if str(row.get("status") or "").strip() in REAL_SIGNAL_STATUSES
        and str(row.get("symbol") or "").strip() != "__RUN__"
        and not bool(row.get("is_simulated"))
    )


def _latest_real_signal_day(rows: list[dict[str, Any]]) -> str:
    del rows
    return latest_independent_signal_day(str(_runtime_path("AQSP_LEDGER", "data/predictions.jsonl")))


def _state_count(value: object) -> int:
    return len(value) if isinstance(value, dict) else 0


def _gate_state_summary(path: Path) -> dict[str, Any]:
    payload = _read_json_file(path)
    sent_by_date = payload.get("sent_by_date", {})
    latest_date = ""
    latest_status = ""
    latest_fingerprint = ""
    legacy_format = False
    if isinstance(sent_by_date, dict) and sent_by_date:
        latest_date = max(str(key) for key in sent_by_date)
        latest_entry = sent_by_date.get(latest_date)
        if isinstance(latest_entry, dict):
            latest_status = str(latest_entry.get("status", "") or "")
            latest_fingerprint = str(latest_entry.get("fingerprint", "") or "")
        elif isinstance(latest_entry, str):
            latest_status = "legacy"
            latest_fingerprint = latest_entry
            legacy_format = True
    return {
        "path": str(path),
        "present": path.exists(),
        "invalid_json": bool(payload.get("invalid_json")),
        "days": _state_count(sent_by_date),
        "latest_date": latest_date,
        "latest_status": latest_status,
        "latest_fingerprint": latest_fingerprint,
        "legacy_format": legacy_format,
        "state_updated_at": str(payload.get("updated_at", "") or ""),
    }


def _current_gate_expectation(signal_days: int) -> dict[str, Any]:
    gate_path = _runtime_path("AQSP_WALKFORWARD_GATE_PATH", WALKFORWARD_GATE_PATH)
    gate_ok, gate_reasons = _check_notification_gate(
        cold_start_days=signal_days,
        gate_path=str(gate_path),
    )
    return {
        "ok": gate_ok,
        "reasons": gate_reasons,
        "fingerprint": gate_reason_fingerprint(gate_reasons) if gate_reasons else "",
        "path": str(gate_path),
    }


def _notify_state_summary(path: Path) -> dict[str, Any]:
    payload = _read_json_file(path)
    return {
        "path": str(path),
        "present": path.exists(),
        "invalid_json": bool(payload.get("invalid_json")),
        "sent": _state_count(payload.get("sent")),
        "pending": _state_count(payload.get("pending")),
        "failed": _state_count(payload.get("failed")),
        "updated_at": str(payload.get("updated_at", "") or ""),
    }


def _configured_notify_enabled() -> bool:
    return str(os.getenv("AQSP_NOTIFY", "") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _walkforward_production_status_summary(path: Path) -> dict[str, Any]:
    payload = _read_json_file(path)
    coverage = payload.get("coverage", {})
    if not isinstance(coverage, dict):
        coverage = {}
    status = str(payload.get("status", "") or "")
    child_exit_code = payload.get("child_exit_code")
    if status == "running":
        child_pid = payload.get("child_pid")
        pid_value = payload.get("pid")
        child_active = False
        pid_active = False
        if isinstance(child_pid, int) and child_pid > 0:
            try:
                os.kill(child_pid, 0)
            except OSError:
                child_active = False
            else:
                child_active = True
        if isinstance(pid_value, int) and pid_value > 0:
            try:
                os.kill(pid_value, 0)
            except OSError:
                pid_active = False
            else:
                pid_active = True
        updated_at = str(payload.get("updated_at", "") or "")
        timeout_seconds = payload.get("timeout_seconds")
        timed_out = False
        try:
            if updated_at and isinstance(timeout_seconds, int) and timeout_seconds > 0:
                timed_out = (
                    now_shanghai() - datetime.fromisoformat(updated_at)
                ).total_seconds() > timeout_seconds
        except ValueError:
            timed_out = False
        if (isinstance(child_pid, int) and child_pid > 0 and not child_active) or (
            not child_active and not pid_active
        ) or (not child_active and timed_out):
            status = "timeout"
            child_exit_code = 124 if not isinstance(child_exit_code, int) else child_exit_code
    return {
        "path": str(path),
        "present": path.exists(),
        "status": status,
        "updated_at": str(payload.get("updated_at", "") or ""),
        "detail": str(payload.get("detail", "") or ""),
        "effective_symbols": payload.get("effective_symbols"),
        "child_exit_code": child_exit_code,
        "db_path": str(payload.get("db_path", "") or ""),
        "gate_path": str(payload.get("gate_path", "") or ""),
        "report_path": str(payload.get("report_path", "") or ""),
        "coverage_symbols": coverage.get("covered_symbols"),
    }


def _scheduler_runtime_lines(system_name: str | None = None) -> list[str]:
    system = system_name or platform.system()
    if system == "Darwin":
        wrapper = Path.home() / ".aqsp/aqsp_daily_run_wrapper.sh"
        launch_agent = Path.home() / "Library/LaunchAgents/com.aqsp.daily.plist"
        repo_wrapper = PROJECT_ROOT / "scripts" / "launchd" / "aqsp_daily_run_wrapper.sh"
        return [
            "- scheduler: launchd",
            f"- launchd_wrapper: {_file_status(wrapper)}",
            f"- launch_agent: {_file_status(launch_agent)}",
            f"- launchd_wrapper_drift: {_wrapper_drift_summary(wrapper, repo_wrapper)}",
        ]
    if system == "Linux":
        return [
            "- scheduler: bt_panel_or_cron",
            "- launchd: not_applicable (macOS only)",
        ]
    return [
        f"- scheduler: unknown ({system or '-'})",
        "- launchd: not_applicable (macOS only)",
    ]


def _source_health_summary() -> dict[str, Any]:
    path = _runtime_path("AQSP_SOURCE_HEALTH", "data/source_health.json")
    if not path.exists():
        return {"path": str(path), "present": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"path": str(path), "present": True, "invalid_json": True}
    return {
        "path": str(path),
        "present": True,
        "consecutive_failures": payload.get("consecutive_failures", 0),
        "last_requested_source": payload.get("last_requested_source", ""),
        "last_actual_source": payload.get("last_actual_source", ""),
        "last_success": payload.get("last_success", ""),
        "last_failure": payload.get("last_failure", ""),
        "fallback_used": payload.get("fallback_used", False),
        "auth": payload.get("auth", {}),
    }


def _auth_health_lines(source_health: dict[str, Any]) -> list[str]:
    auth = source_health.get("auth", {})
    if not isinstance(auth, dict) or not auth:
        return ["- none"]
    lines: list[str] = []
    for source_id, raw in sorted(auth.items()):
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", "") or "unknown")
        checked_at = str(raw.get("checked_at", "") or "-")
        message = str(raw.get("message", "") or "-")
        lines.append(
            f"- {source_id}: status={status} checked_at={checked_at} message={message}"
        )
    return lines or ["- none"]


def _large_return_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flagged: list[dict[str, Any]] = []
    for row in rows:
        value = row.get("return_pct")
        if not isinstance(value, int | float):
            continue
        if abs(float(value)) <= 30:
            continue
        flagged.append(
            {
                "symbol": row.get("symbol"),
                "signal_date": row.get("signal_date"),
                "status": row.get("status"),
                "return_pct": value,
            }
        )
    return flagged


def _latest_run_source_runtime(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latest = next(
        (
            row
            for row in reversed(rows)
            if row.get("run_requested_source") or row.get("run_actual_source")
        ),
        {},
    )
    label = str(latest.get("run_source_health_label", "") or "")
    return {
        "requested_source": str(latest.get("run_requested_source", "") or ""),
        "actual_source": str(latest.get("run_actual_source", "") or ""),
        "health_label": label or "unknown",
        "health_message": str(latest.get("run_source_health_message", "") or ""),
        "fallback_used": bool(latest.get("run_fallback_used", False)),
        "notify_level": notification_level_for_health_label(label),
    }


def _tdx_vipdoc_summary(base: Path | None = None) -> dict[str, Any]:
    root = base or PROJECT_ROOT / "private_data/tdx"
    vipdoc = root / "vipdoc" if (root / "vipdoc").exists() else root
    files = sorted(vipdoc.glob("*/lday/*.day"))
    latest = ""
    symbol_count = 0
    for path in files:
        raw = path.read_bytes()
        if len(raw) < TDX_DAY_RECORD_SIZE:
            continue
        trade_date = struct.unpack_from("<I", raw, len(raw) - TDX_DAY_RECORD_SIZE)[0]
        text = str(trade_date)
        if len(text) != 8:
            continue
        latest = max(latest, f"{text[:4]}-{text[4:6]}-{text[6:]}")
        symbol_count += 1
    return {
        "path": str(vipdoc),
        "present": vipdoc.exists(),
        "day_files": len(files),
        "symbols_with_records": symbol_count,
        "latest": latest,
    }


def _wrapper_drift_summary(wrapper: Path, repo_wrapper: Path) -> str:
    if not wrapper.exists() or not repo_wrapper.exists():
        return "unknown"
    current = wrapper.read_text(encoding="utf-8", errors="ignore")
    expected = repo_wrapper.read_text(encoding="utf-8", errors="ignore")
    if current == expected:
        return "in_sync"
    current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()[:12]
    expected_hash = hashlib.sha256(expected.encode("utf-8")).hexdigest()[:12]
    if any(token in current for token in ("aqsp paper", "aqsp dashboard", "周末跳过")):
        return f"drifted_legacy current={current_hash} expected={expected_hash}"
    return f"drifted current={current_hash} expected={expected_hash}"


def _ready_source_lines() -> list[str]:
    lines: list[str] = []
    for entry in list_registry_entries():
        if not entry.runtime_ready:
            continue
        lines.append(
            f"- {entry.id}: local_data={local_data_status(entry)} "
            f"daily={'yes' if entry.supports_daily else 'no'} "
            f"intraday={'yes' if entry.supports_intraday else 'no'} "
            f"realtime={'yes' if entry.supports_realtime else 'no'}"
        )
    return lines


def main() -> int:
    _load_dotenv_defaults()
    paths = _runtime_paths()

    ledger_rows = _read_jsonl(paths.ledger)
    paper_rows = _read_jsonl(paths.paper_ledger)
    signal_days = count_independent_signal_days(str(paths.ledger))
    simulated_signal_days = len(collect_simulated_signal_dates(str(paths.ledger)))
    paper_days = count_paper_tracking_days(str(paths.paper_ledger))
    blocked_runtime_days = _blocked_runtime_day_count(ledger_rows)
    real_signal_rows = _real_signal_row_count(ledger_rows)
    latest_real_signal_day = _latest_real_signal_day(ledger_rows)
    cold_start_target = cold_start_min_days()
    latest_signal = max(
        (str(row.get("signal_date", "")) for row in ledger_rows),
        default="",
    )
    report = [
        "# AQSP Runtime Diagnosis",
        "",
        f"- project_root: {PROJECT_ROOT}",
        f"- ledger: {_file_status(paths.ledger)} rows={len(ledger_rows)} latest={latest_signal or '-'}",
        f"- paper_ledger: {_file_status(paths.paper_ledger)} rows={len(paper_rows)}",
        f"- signal_days: {signal_days}/{cold_start_target}",
        f"- signal_rows: {real_signal_rows}",
        f"- latest_real_signal_day: {latest_real_signal_day or '-'}",
        f"- simulated_signal_days: {simulated_signal_days}",
        f"- paper_days: {paper_days}/{cold_start_target}",
        f"- blocked_runtime_days: {blocked_runtime_days}",
        f"- risk_state: {_file_status(paths.risk_state)}",
        *_scheduler_runtime_lines(),
        f"- dashboard: {_file_status(paths.dashboard)}",
        f"- latest_report: {_file_status(paths.latest_report)}",
        f"- latest_csv: {_file_status(paths.latest_csv)}",
        f"- sqlite_db: {_file_status(paths.sqlite_db)} path={paths.sqlite_db}",
        "",
        "## Data Sources",
        *_ready_source_lines(),
        "",
        "## Local TDX Vipdoc",
    ]
    tdx = _tdx_vipdoc_summary()
    report.extend(
        [
            f"- path: {tdx['path']}",
            f"- present: {tdx['present']}",
            f"- day_files: {tdx['day_files']}",
            f"- symbols_with_records: {tdx['symbols_with_records']}",
            f"- latest: {tdx['latest'] or '-'}",
            "",
            "## Source Health",
        ]
    )
    source_health = _source_health_summary()
    report.extend(
        [
            f"- path: {source_health['path']}",
            f"- present: {source_health.get('present', False)}",
            f"- consecutive_failures: {source_health.get('consecutive_failures', '-')}",
            f"- last_requested_source: {source_health.get('last_requested_source', '-') or '-'}",
            f"- last_actual_source: {source_health.get('last_actual_source', '-') or '-'}",
            f"- last_success: {source_health.get('last_success', '-') or '-'}",
            f"- last_failure: {source_health.get('last_failure', '-') or '-'}",
            f"- fallback_used: {source_health.get('fallback_used', '-')}",
            "",
            "## Source Auth",
            *_auth_health_lines(source_health),
            "",
            "## Notification Level",
        ]
    )
    runtime_source = _latest_run_source_runtime(ledger_rows)
    source_route = (
        runtime_source["actual_source"] or runtime_source["requested_source"] or "-"
    )
    if (
        runtime_source["requested_source"]
        and runtime_source["actual_source"]
        and runtime_source["requested_source"] != runtime_source["actual_source"]
    ):
        source_route = (
            f"{runtime_source['requested_source']} -> {runtime_source['actual_source']}"
        )
    report.extend(
        [
            f"- notify_level: {runtime_source['notify_level']}",
            f"- source_health_label: {runtime_source['health_label']}",
            f"- source_route: {source_route}",
            f"- fallback_used: {runtime_source['fallback_used']}",
            f"- source_message: {runtime_source['health_message'] or '-'}",
            "",
            "## Notify State",
        ]
    )
    walkforward_status = _walkforward_production_status_summary(
        paths.walkforward_production_status
    )
    gate_state = _gate_state_summary(paths.gate_notify_state)
    gate_expected = _current_gate_expectation(signal_days)
    notify_state = _notify_state_summary(paths.notify_state)
    monitor_state = _notify_state_summary(paths.monitor_notify_state)
    run_history = _successful_run_history_summary(PROJECT_ROOT)
    cadence = _runtime_cadence_summary(PROJECT_ROOT)
    notify_warnings: list[str] = []
    default_ledger = _default_runtime_path("data/predictions.jsonl")
    default_paper_ledger = _default_runtime_path("data/paper_trades.jsonl")
    if paths.ledger.resolve(strict=False) != default_ledger.resolve(strict=False):
        notify_warnings.append(
            f"- warning_ledger_path_drift: AQSP_LEDGER={paths.ledger} (expected {default_ledger})"
        )
    if (
        paths.paper_ledger.resolve(strict=False)
        != default_paper_ledger.resolve(strict=False)
    ):
        notify_warnings.append(
            f"- warning_paper_ledger_path_drift: AQSP_PAPER_LEDGER={paths.paper_ledger} (expected {default_paper_ledger})"
        )
    if cadence["daily_runs"] > 1:
        notify_warnings.append(
            f"- warning_daily_runs_today: {cadence['daily_runs']} (daily 应为收盘 1 次；若明显偏多，先检查宝塔是否把 bt_task.sh daily 绑到高频调度)"
        )
    if (
        "cold_start" in str(gate_state["latest_fingerprint"] or "").split("|")
        and signal_days >= cold_start_target
    ):
        notify_warnings.append(
            (
                f"- warning_gate_cold_start_mismatch: signal_days={signal_days}/{cold_start_target} "
                "但最新 gate 仍是 cold_start；优先核对线上 ledger 路径、运行入口和部署版本"
            )
        )
    if gate_expected["ok"] and gate_state["present"]:
        notify_warnings.append(
            "- warning_gate_state_stale_open: 当前 gate 已放行，但 gate_notify_state 仍存在；"
            "先修复状态文件再上线"
        )
    if (
        (not gate_expected["ok"])
        and gate_state["present"]
        and gate_state["latest_fingerprint"] != gate_expected["fingerprint"]
    ):
        notify_warnings.append(
            (
                "- warning_gate_state_drift: "
                f"state={gate_state['latest_fingerprint'] or '-'} "
                f"expected={gate_expected['fingerprint'] or '-'} "
                f"(gate_path={gate_expected['path']})"
            )
        )
    if gate_state["present"] is False and signal_days >= cold_start_target:
        notify_warnings.append(
            (
                f"- warning_gate_state_missing: signal_days={signal_days}/{cold_start_target} "
                "但 gate_notify_state 缺失；双门去重不会持久化，先核对线上状态文件路径和部署版本"
            )
        )
    if not configured_notification_channels():
        notify_warnings.append(
            "- warning_no_notify_channel: 当前未配置任何手机/IM 通知通道，服务器即使运行成功也不会直达手机"
        )
    if not _configured_notify_enabled():
        notify_warnings.append(
            "- warning_notify_disabled: AQSP_NOTIFY=false；定时链即使运行成功也不会发送正常手机通知"
        )
    report.extend(
        [
            f"- walkforward_production_status_file: {_file_status(paths.walkforward_production_status)}",
            f"- walkforward_production_status: {walkforward_status['status'] or '-'} updated={walkforward_status['updated_at'] or '-'}",
            f"- walkforward_production_effective_symbols: {walkforward_status['effective_symbols'] if walkforward_status['effective_symbols'] is not None else '-'}",
            f"- walkforward_production_child_exit: {walkforward_status['child_exit_code'] if walkforward_status['child_exit_code'] is not None else '-'}",
            f"- walkforward_production_detail: {walkforward_status['detail'] or '-'}",
            f"- configured_notify_enabled: {_configured_notify_enabled()}",
            f"- configured_notify_channels: {','.join(configured_notification_channels()) or '-'}",
            f"- gate_notify_state: {_file_status(paths.gate_notify_state)}",
            f"- gate_days: {gate_state['days']} latest={gate_state['latest_date'] or '-'}",
            f"- gate_latest_status: {gate_state['latest_status'] or '-'}",
            f"- gate_latest_fingerprint: {gate_state['latest_fingerprint'] or '-'}",
            f"- gate_expected_ok: {gate_expected['ok']}",
            f"- gate_expected_fingerprint: {gate_expected['fingerprint'] or '-'}",
            f"- gate_legacy_format: {gate_state['legacy_format']}",
            f"- gate_updated_at: {gate_state['state_updated_at'] or '-'}",
            f"- notify_state: {_file_status(paths.notify_state)}",
            f"- notify_counts: sent={notify_state['sent']} pending={notify_state['pending']} failed={notify_state['failed']}",
            f"- notify_updated_at: {notify_state['updated_at'] or '-'}",
            f"- monitor_notify_state: {_file_status(paths.monitor_notify_state)}",
            f"- monitor_counts: sent={monitor_state['sent']} pending={monitor_state['pending']} failed={monitor_state['failed']}",
            f"- monitor_updated_at: {monitor_state['updated_at'] or '-'}",
            f"- successful_run_days: {run_history['count']} latest={run_history['latest'] or '-'} source={run_history['source']}",
            f"- cadence_today: daily={cadence['daily_runs']} news={cadence['news_runs']} monitor={cadence['monitor_runs']}",
            *notify_warnings,
            "",
            "## Research Runtime",
        ]
    )
    research_summary = load_research_summary()
    if research_summary is None:
        report.append("- summary: unavailable")
        report.append("")
    else:
        report.extend(
            [
                f"- total_findings: {research_summary.total_findings}",
                f"- findings_display: {research_findings_display(research_summary)}",
                f"- implemented_families: {research_summary.implemented_family_count}",
                f"- report_only_families: {research_summary.report_only_family_count}",
                f"- gated_families: {research_summary.gated_family_count}",
                f"- source_candidates: {len(research_summary.source_candidates)}",
                "- next_actions:",
                "",
            ]
        )
        for item in research_summary.next_actions[:3]:
            report.append(
                f"- {item.priority} {item.kind} {item.item_id}: {item.blocker or '-'}"
            )
        for item in research_summary.prereq_items[:3]:
            missing_env = ",".join(item.missing_env_vars) or "-"
            report.append(
                f"- prereq {item.kind} {item.item_id}: status={item.status} missing_env={missing_env}"
            )
        report.append("")
    report.extend(
        [
            "## Data Quality Flags",
        ]
    )
    flags = _large_return_rows(ledger_rows) + _large_return_rows(paper_rows)
    if flags:
        for flag in flags:
            report.append(f"- large_abs_return: {flag}")
    else:
        report.append("- no large absolute return rows over 30%")

    if paths.risk_state.exists():
        try:
            state = json.loads(paths.risk_state.read_text(encoding="utf-8"))
            report.append(f"- cooldown_until: {state.get('cooldown_until')}")
        except json.JSONDecodeError:
            report.append("- risk_state invalid json")

    print("\n".join(report))
    return 1 if flags else 0


if __name__ == "__main__":
    raise SystemExit(main())
