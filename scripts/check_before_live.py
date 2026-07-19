#!/usr/bin/env python3
"""Fail-closed readiness gate for human-reviewed semi-live operation."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aqsp.core.time import SHANGHAI_TZ, get_previous_trading_day, today_shanghai
from aqsp.cli import WALKFORWARD_GATE_PATH, _check_notification_gate
from aqsp.ledger.runtime import (
    collect_simulated_signal_dates,
    count_independent_signal_days,
    count_paper_tracking_days,
    ledger_signal_date,
    latest_independent_signal_day,
)
from aqsp.runtime.gate_notify import gate_reason_fingerprint
from aqsp.utils.env import read_env_value
from aqsp.walkforward_gate import (
    MAX_GATE_AGE_DAYS,
    MIN_PRODUCTION_GATE_SYMBOLS,
    validate_walkforward_gate_payload,
    validate_walkforward_market_coverage,
)
from aqsp.strategies.thresholds import Thresholds, load_thresholds


MIN_INDEPENDENT_SIGNAL_DAYS = 30
MIN_SUCCESSFUL_RUN_DAYS = 5
MAX_STRATEGY_NOT_EXECUTABLE_RATE = 0.35
MIN_EXECUTABILITY_STRATEGY_ATTEMPTS = 5
CONCRETE_DATA_SOURCE_TOKENS: tuple[str, ...] = (
    "AkshareSource(",
    "BaostockSource(",
    "EastmoneySource(",
    "EfinanceSource(",
    "MootdxSource(",
    "SinaSource(",
    "SqliteDbSource(",
    "TdxVipdocSource(",
    "TencentSource(",
    "aqsp.data.akshare_source",
    "aqsp.data.baostock_source",
    "aqsp.data.eastmoney_source",
    "aqsp.data.efinance_source",
    "aqsp.data.mootdx_source",
    "aqsp.data.sina_source",
    "aqsp.data.sqlite_db_source",
    "aqsp.data.tdx_vipdoc_source",
    "aqsp.data.tencent_source",
    "fetch_akshare",
)


@dataclass(frozen=True)
class ReadinessFinding:
    gate: str
    ok: bool
    detail: str


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _read_pipeline_history(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((root / "logs" / "pipeline").glob("*.json")):
        payload = _read_json(path)
        if not payload:
            continue
        rows.append(
            {
                "date": path.stem,
                "success": payload.get("overall_success") is True,
                "started_at": payload.get("started_at"),
                "finished_at": payload.get("finished_at"),
                "successful_steps": sum(
                    1
                    for step in payload.get("steps", [])
                    if isinstance(step, dict) and step.get("success") is True
                ),
                "total_steps": len(
                    [
                        step
                        for step in payload.get("steps", [])
                        if isinstance(step, dict)
                    ]
                ),
            }
        )
    return rows


def _read_daily_log_history(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
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
            match = re.match(
                r"^=== aqsp run @ (.+?) ===$", segment.splitlines()[0].strip()
            )
            run_date = _daily_log_segment_date(match.group(1) if match else "")
            if not run_date or run_date in seen_dates:
                continue
            if (
                "=== outputs ===" not in segment
                and "=== aqsp dashboard @" not in segment
            ):
                continue
            if "aqsp run failed:" in segment:
                continue
            rows.append(
                {
                    "date": run_date,
                    "success": True,
                    "source": "daily_log",
                }
            )
            seen_dates.add(run_date)
    return rows


def _read_ledger_run_history(root: Path) -> list[dict[str, Any]]:
    path = root / "data" / "predictions.jsonl"
    rows: list[dict[str, Any]] = []
    for row in _read_jsonl(path):
        if str(row.get("symbol") or "").strip() != "__RUN__":
            continue
        status = str(row.get("status") or "").strip()
        if status not in {"run_completed_no_picks", "blocked_by_circuit_breaker"}:
            continue
        run_date = str(row.get("signal_date") or "").strip()
        if not run_date:
            continue
        rows.append(
            {
                "date": run_date,
                "success": True,
                "source": "ledger_run_events",
                "status": status,
            }
        )
    return rows


def _daily_log_segment_date(header: str) -> str:
    tokens = header.split()
    if len(tokens) >= 6:
        candidate = " ".join(tokens[:4] + tokens[5:6])
        try:
            return (
                datetime.strptime(candidate, "%a %b %d %H:%M:%S %Y").date().isoformat()
            )
        except ValueError:
            return ""
    return ""


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _file_has_content(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def _pbo_diagnostic_report_candidates(root: Path) -> tuple[Path, ...]:
    return (
        root / "reports" / "walkforward-grid-raw-production-diagnostic-latest.md",
        root / "reports" / "walkforward-grid-raw-production-latest.md",
        root / "reports" / "walkforward-grid-latest.md",
    )


def check_before_live(
    *,
    root: Path,
    today: date,
    gate_path: Path | None = None,
    ledger_path: Path | None = None,
    run_history_path: Path | None = None,
    cron_path: Path | None = None,
    cron_dir: Path | None = None,
    paper_ledger_path: Path | None = None,
    thresholds: Thresholds | None = None,
) -> list[ReadinessFinding]:
    gate_path = gate_path or root / "data" / "walkforward_gate.json"
    ledger_path = ledger_path or root / "data" / "predictions.jsonl"
    paper_ledger_path = paper_ledger_path or root / "data" / "paper_trades.jsonl"
    run_history_path = run_history_path or root / "data" / "daily_run_history.jsonl"

    findings: list[ReadinessFinding] = []
    findings.append(_check_walkforward_gate(gate_path, today))
    findings.append(_check_walkforward_price_mode(root, gate_path))
    findings.append(
        _check_walkforward_market_coverage(
            gate_path, root / "reports" / "walkforward-grid-raw-production-latest.md"
        )
    )
    findings.append(_check_runtime_universe_cap(root))
    findings.append(_check_runtime_symbol_override(root))
    findings.append(_check_runtime_data_source_config(root))
    findings.append(_check_runtime_sqlite_price_mode(root))
    findings.append(_check_runtime_sqlite_freshness(root, today))
    findings.append(_check_coldstart_runtime_alignment(root))
    findings.append(_check_runtime_ledger_paths(root))
    findings.append(_check_short_line_subcommand_universe(root))
    findings.append(_check_cold_start_gate_config(root))
    findings.append(_check_strategy_threshold_consistency(thresholds))
    findings.append(_check_strategy_runtime_threshold_application(root))
    findings.append(_check_regime_strategy_weight_blending(root))
    findings.append(_check_runtime_regime_requires_benchmark(root))
    findings.append(_check_pbo_diagnostics(root, gate_path))
    findings.append(_check_trading_calendar_coverage(root, today))
    findings.append(_check_signal_sample_size(ledger_path))
    findings.append(_check_gate_cold_start_alignment(root, ledger_path, today=today))
    findings.append(_check_paper_tracking_sample_size(paper_ledger_path))
    findings.append(_check_signal_sample_status_boundary(root))
    findings.append(_check_strategy_executability_feedback(paper_ledger_path))
    findings.append(_check_strategy_executability_runtime_feedback(root))
    findings.append(_check_strategy_weight_snapshot_audit(root))
    findings.append(_check_auto_evolution_proposal_only(root))
    findings.append(_check_successful_runs(run_history_path, root=root))
    findings.append(
        _check_scheduler_notify_cadence(root, cron_path=cron_path, cron_dir=cron_dir)
    )
    findings.append(_check_system_cron_install_guard(root))
    findings.append(_check_launchd_wrapper_drift(root))
    findings.append(_check_cli_subcommand_notify_dedupe(root))
    findings.append(_check_news_catalysts_failed_notify_guard(root))
    findings.append(_check_run_scheduled_env_notify_guard(root))
    findings.append(_check_monitor_warning_notify_guard(root))
    findings.append(_check_monitor_wrapper_critical_only_default(root))
    findings.append(_check_pipeline_gate_block_summary_notify(root))
    findings.append(_check_git_sync_health(root))
    findings.append(_check_cli_data_source_boundary(root))
    findings.append(_check_data_source_fail_closed_contract(root))
    findings.append(_check_walkforward_service_boundary(root))
    findings.append(_check_business_layer_source_abstractions(root))
    findings.append(_check_backtest_no_global_quantile_leakage(root))
    findings.append(_check_notification_runtime_boundaries(root))
    findings.append(_check_scheduled_service_boundary(root))
    findings.append(_check_special_strategy_ledger_guards(root))
    findings.append(_check_server_monitor_exit_policy(root))
    findings.append(_check_notify_channels(root))
    findings.extend(_check_notify_state_paths(root))
    findings.extend(_check_runtime_outputs(root))
    return findings


def _check_walkforward_gate(path: Path, today: date) -> ReadinessFinding:
    gate = _read_json(path)
    if not gate:
        return ReadinessFinding(
            "walkforward_gate",
            False,
            f"missing or unreadable gate: {path}",
        )

    validation = validate_walkforward_gate_payload(
        gate,
        today=today,
        max_age_days=MAX_GATE_AGE_DAYS,
    )
    detail = validation.detail
    status_path = path.with_name("walkforward_production_status.json")
    status_payload = _read_json(status_path)
    if not validation.ok and status_payload:
        status_db_path = str(status_payload.get("db_path") or "").strip()
        if _looks_like_ephemeral_test_path(status_db_path):
            detail = (
                f"{detail}; production_status ignored: ephemeral test artifact "
                f"({status_db_path})"
            )
            return ReadinessFinding("walkforward_gate", False, detail)
        status = str(status_payload.get("status") or "").strip()
        updated_at = str(status_payload.get("updated_at") or "").strip()
        child_exit = status_payload.get("child_exit_code")
        if status == "running":
            pid_value = status_payload.get("pid")
            pid_active = False
            if isinstance(pid_value, int) and pid_value > 0:
                try:
                    os.kill(pid_value, 0)
                except OSError:
                    pid_active = False
                else:
                    pid_active = True
            if pid_active:
                return ReadinessFinding(
                    "walkforward_gate",
                    False,
                    f"production walkforward running; refreshed gate evidence pending ({updated_at or '-'})",
                )
            child_exit = 124 if not isinstance(child_exit, int) else child_exit
            detail = (
                f"{detail}; production_status: status=timeout"
                + (f", updated_at={updated_at}" if updated_at else "")
                + f", child_exit_code={child_exit}, stale_running_status"
            )
            return ReadinessFinding("walkforward_gate", False, detail)
        extra = ", ".join(
            part
            for part in (
                f"status={status}" if status else "",
                f"updated_at={updated_at}" if updated_at else "",
                (
                    f"child_exit_code={child_exit}"
                    if isinstance(child_exit, int)
                    else ""
                ),
            )
            if part
        )
        if extra:
            detail = f"{detail}; production_status: {extra}"
        if status == "timeout":
            detail = (
                f"{detail}; production rerun timed out before producing new "
                "DSR/PBO evidence; keeping previous failed gate active"
            )
    return ReadinessFinding(
        "walkforward_gate",
        validation.ok,
        detail,
    )


def _looks_like_ephemeral_test_path(raw: str) -> bool:
    text = str(raw or "").strip().lower()
    if not text:
        return False
    return any(
        token in text
        for token in (
            "/pytest-of-",
            "\\pytest-of-",
            "/tmp/",
            "/var/folders/",
            "/private/var/folders/",
        )
    )


def _check_walkforward_price_mode(root: Path, gate_path: Path) -> ReadinessFinding:
    gate = _read_json(gate_path)
    if not gate:
        return ReadinessFinding("walkforward_price_mode", False, "gate missing")

    source = str(gate.get("source") or "sqlite_db")
    price_mode = str(gate.get("price_mode") or "").strip().lower()
    db_path = str(gate.get("sqlite_db_path") or "").strip()
    if not db_path:
        db_path = read_env_value(root / ".env", "AQSP_SQLITE_DB_PATH")
    if not db_path:
        db_path = "A股量化分析数据/astocks_raw.db"
    db_name = Path(db_path).name.lower()
    if not price_mode:
        price_mode = (
            "qfq" if "qfq" in db_name else "raw" if "raw" in db_name else "unknown"
        )
    if source == "sqlite_db" and price_mode == "qfq":
        return ReadinessFinding(
            "walkforward_price_mode",
            False,
            "sqlite_db gate uses qfq historical database; real gate requires raw prices or point-in-time adjustment factors",
        )
    if source == "sqlite_db" and price_mode != "raw":
        return ReadinessFinding(
            "walkforward_price_mode",
            False,
            f"sqlite_db gate price_mode is unknown: {price_mode}; real gate must prove raw prices or point-in-time adjustment factors",
        )
    return ReadinessFinding("walkforward_price_mode", True, "ok")


def _extract_walkforward_report_symbols(report_path: Path) -> int | None:
    if not report_path.exists():
        return None
    try:
        text = report_path.read_text(encoding="utf-8")
    except OSError:
        return None
    patterns = (
        r"\*\*标的数量\*\*\s*[:：]\s*(\d+)",
        r"\|\s*effective_symbols\s*\|\s*(\d+)\s*\|",
        r"\|\s*covered_symbols\s*\|\s*(\d+)\s*\|",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def _check_walkforward_market_coverage(
    gate_path: Path, production_report_path: Path
) -> ReadinessFinding:
    gate = _read_json(gate_path)
    if not gate:
        return ReadinessFinding("walkforward_market_coverage", False, "gate missing")
    status_path = gate_path.with_name("walkforward_production_status.json")
    status_payload = _read_json(status_path)
    coverage_payload = dict(gate)
    effective_symbols = gate.get("effective_symbols")
    if not isinstance(effective_symbols, int) or isinstance(effective_symbols, bool):
        production_coverage = gate.get("production_gate_coverage")
        if isinstance(production_coverage, dict):
            covered = production_coverage.get("covered_symbols")
            if isinstance(covered, int) and not isinstance(covered, bool):
                coverage_payload["effective_symbols"] = covered
                effective_symbols = covered
        if (
            not isinstance(effective_symbols, int)
            or isinstance(effective_symbols, bool)
        ) and status_payload:
            status_coverage = status_payload.get("coverage")
            if isinstance(status_coverage, dict):
                covered = status_coverage.get("covered_symbols")
                if isinstance(covered, int) and not isinstance(covered, bool):
                    coverage_payload["effective_symbols"] = covered
    validation = validate_walkforward_market_coverage(coverage_payload)
    if validation.effective_symbols is None:
        return ReadinessFinding(
            "walkforward_market_coverage",
            False,
            "effective_symbols missing; production short-line gate requires full-market coverage",
        )
    detail = validation.detail
    report_symbols = _extract_walkforward_report_symbols(production_report_path)
    if report_symbols is None:
        return ReadinessFinding(
            "walkforward_market_coverage",
            False,
            f"{detail}; production report missing actual symbol count: {production_report_path}",
        )
    if report_symbols != validation.effective_symbols:
        return ReadinessFinding(
            "walkforward_market_coverage",
            False,
            f"{detail}; production report symbol count mismatch: "
            f"report={report_symbols}, gate={validation.effective_symbols}",
        )
    if not validation.ok:
        detail += "; 300-symbol quick gates are smoke tests only"
    return ReadinessFinding("walkforward_market_coverage", validation.ok, detail)


def _check_runtime_universe_cap(root: Path) -> ReadinessFinding:
    raw_value = read_env_value(root / ".env", "AQSP_MAX_UNIVERSE")
    if not raw_value:
        return ReadinessFinding(
            "runtime_universe_cap", True, "AQSP_MAX_UNIVERSE unset; default full market"
        )
    try:
        max_universe = int(raw_value)
    except ValueError:
        return ReadinessFinding(
            "runtime_universe_cap",
            False,
            f"AQSP_MAX_UNIVERSE invalid: {raw_value!r}",
        )
    ok = max_universe == 0 or max_universe >= MIN_PRODUCTION_GATE_SYMBOLS
    detail = (
        "full market"
        if max_universe == 0
        else f"AQSP_MAX_UNIVERSE={max_universe}; production short-line runs require 0 or >= {MIN_PRODUCTION_GATE_SYMBOLS}"
    )
    return ReadinessFinding("runtime_universe_cap", ok, detail)


def _check_runtime_symbol_override(root: Path) -> ReadinessFinding:
    raw_value = read_env_value(root / ".env", "AQSP_SYMBOLS")
    symbols = [item.strip() for item in raw_value.split(",") if item.strip()]
    if not symbols:
        return ReadinessFinding(
            "runtime_symbol_override",
            True,
            "AQSP_SYMBOLS unset; runtime can resolve full pool",
        )
    ok = len(symbols) >= MIN_PRODUCTION_GATE_SYMBOLS
    detail = (
        f"AQSP_SYMBOLS={len(symbols)} explicit symbols; "
        f"production short-line runs require empty AQSP_SYMBOLS or >= {MIN_PRODUCTION_GATE_SYMBOLS}"
    )
    return ReadinessFinding("runtime_symbol_override", ok, detail)


def _check_runtime_ledger_paths(root: Path) -> ReadinessFinding:
    env_path = root / ".env"
    prediction_values = {
        "AQSP_LEDGER": read_env_value(env_path, "AQSP_LEDGER"),
    }
    paper_values = {
        "AQSP_PAPER_LEDGER": read_env_value(env_path, "AQSP_PAPER_LEDGER"),
    }

    blockers: list[str] = []
    for key, value in prediction_values.items():
        normalized = _normalize_runtime_path(root, value or "data/predictions.jsonl")
        if normalized != _normalize_runtime_path(root, "data/predictions.jsonl"):
            blockers.append(f"{key}={value}")
    for key, value in paper_values.items():
        normalized = _normalize_runtime_path(root, value or "data/paper_trades.jsonl")
        if normalized != _normalize_runtime_path(root, "data/paper_trades.jsonl"):
            blockers.append(f"{key}={value}")

    return ReadinessFinding(
        "runtime_ledger_paths",
        not blockers,
        "ok"
        if not blockers
        else "ledger path drift may reset cold-start/sample counts: "
        + ", ".join(blockers),
    )


def _normalize_runtime_path(root: Path, value: str) -> Path:
    path = Path(str(value).strip()).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _check_runtime_data_source_config(root: Path) -> ReadinessFinding:
    env_path = root / ".env"
    source = (read_env_value(env_path, "AQSP_SOURCE") or "").strip().lower()
    fallback = (read_env_value(env_path, "AQSP_ALLOW_ONLINE_FALLBACK") or "").strip()
    allowed_sources = {"local_first", "tdx_vipdoc", "sqlite_db"}
    blockers: list[str] = []
    if not source:
        blockers.append("AQSP_SOURCE unset")
    elif source not in allowed_sources:
        blockers.append(f"AQSP_SOURCE={source}")
    if fallback.lower() not in {"false", "0", "no", "off"}:
        blockers.append(
            "AQSP_ALLOW_ONLINE_FALLBACK must be false for production readiness"
        )
    if source in {"local_first", "tdx_vipdoc"}:
        vipdoc = read_env_value(env_path, "AQSP_TDX_VIPDOC_PATH")
        if not vipdoc:
            blockers.append("AQSP_TDX_VIPDOC_PATH unset")
        else:
            vipdoc_path = _normalize_runtime_path(root, vipdoc)
            if not vipdoc_path.exists():
                blockers.append(f"AQSP_TDX_VIPDOC_PATH missing: {vipdoc}")
    return ReadinessFinding(
        "runtime_data_source_config",
        not blockers,
        "ok"
        if not blockers
        else "production runtime must use explicit local/raw source: "
        + "; ".join(blockers),
    )


def _check_runtime_sqlite_price_mode(root: Path) -> ReadinessFinding:
    env_path = root / ".env"
    source = (read_env_value(env_path, "AQSP_SOURCE") or "").strip().lower()
    db_path = read_env_value(env_path, "AQSP_SQLITE_DB_PATH")
    if source != "sqlite_db" and not db_path:
        return ReadinessFinding("runtime_sqlite_price_mode", True, "not used")
    if not db_path:
        return ReadinessFinding(
            "runtime_sqlite_price_mode",
            False,
            "AQSP_SOURCE=sqlite_db but AQSP_SQLITE_DB_PATH is unset",
        )
    db_name = Path(db_path).name.lower()
    if "qfq" in db_name or "hfq" in db_name:
        return ReadinessFinding(
            "runtime_sqlite_price_mode",
            False,
            f"AQSP_SQLITE_DB_PATH={db_path}; production runtime requires raw prices or PIT-adjusted path, not qfq/hfq",
        )
    return ReadinessFinding("runtime_sqlite_price_mode", True, "ok")


def _check_runtime_sqlite_freshness(root: Path, today: date) -> ReadinessFinding:
    env_path = root / ".env"
    source = (read_env_value(env_path, "AQSP_SOURCE") or "").strip().lower()
    raw_path = read_env_value(env_path, "AQSP_SQLITE_DB_PATH")
    if source != "sqlite_db" and not raw_path:
        return ReadinessFinding("runtime_sqlite_freshness", True, "not used")
    if not raw_path:
        return ReadinessFinding(
            "runtime_sqlite_freshness",
            False,
            "AQSP_SOURCE=sqlite_db but AQSP_SQLITE_DB_PATH is unset",
        )

    db_path = _normalize_runtime_path(root, raw_path)
    if not db_path.exists():
        return ReadinessFinding(
            "runtime_sqlite_freshness",
            False,
            f"runtime sqlite db missing: {db_path}",
        )
    if db_path.stat().st_size <= 0:
        return ReadinessFinding(
            "runtime_sqlite_freshness",
            False,
            f"runtime sqlite db empty: {db_path}",
        )

    mtime_day = datetime.fromtimestamp(db_path.stat().st_mtime, tz=SHANGHAI_TZ).date()
    required_day = get_previous_trading_day(today)
    required_day_compact = required_day.strftime("%Y%m%d")
    symbol_count: int | None = None
    query_error = ""
    try:
        with sqlite3.connect(db_path, timeout=30.0) as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT ts_code) FROM daily_qfq WHERE trade_date = ?",
                (required_day_compact,),
            ).fetchone()
        symbol_count = int(row[0] or 0) if row else 0
    except sqlite3.Error as exc:
        query_error = str(exc)

    ok = mtime_day >= required_day
    if symbol_count is not None:
        ok = ok and symbol_count >= MIN_PRODUCTION_GATE_SYMBOLS
    detail = (
        f"{db_path} mtime={mtime_day.isoformat()}; "
        f"require >= {required_day.isoformat()} (latest completed trading day)"
    )
    if symbol_count is not None:
        detail += (
            f"; {required_day.isoformat()} rows="
            f"{symbol_count}/{MIN_PRODUCTION_GATE_SYMBOLS} symbols"
        )
    elif query_error:
        detail += f"; symbol_count_unavailable={query_error}"

    sibling_qfq = db_path.with_name("astocks_qfq.db")
    if sibling_qfq.exists():
        qfq_day = datetime.fromtimestamp(
            sibling_qfq.stat().st_mtime, tz=SHANGHAI_TZ
        ).date()
        if qfq_day > mtime_day:
            detail += f"; qfq sibling newer: {qfq_day.isoformat()}"

    return ReadinessFinding("runtime_sqlite_freshness", ok, detail)


def _check_coldstart_runtime_alignment(root: Path) -> ReadinessFinding:
    env_path = root / ".env"
    source = (read_env_value(env_path, "AQSP_SOURCE") or "").strip().lower()
    runtime_db_path = read_env_value(env_path, "AQSP_SQLITE_DB_PATH")
    coldstart_db_path = read_env_value(env_path, "AQSP_COLDSTART_DB_PATH")
    coldstart_update_script = read_env_value(env_path, "AQSP_COLDSTART_UPDATE_SCRIPT")
    blockers: list[str] = []

    if source == "sqlite_db":
        if not runtime_db_path:
            blockers.append("AQSP_SQLITE_DB_PATH unset")
        elif "qfq" in Path(runtime_db_path).name.lower():
            blockers.append(f"runtime sqlite path is qfq: {runtime_db_path}")

        if coldstart_db_path:
            runtime_normalized = (
                _normalize_runtime_path(root, runtime_db_path)
                if runtime_db_path
                else None
            )
            coldstart_normalized = _normalize_runtime_path(root, coldstart_db_path)
            if runtime_normalized is None or coldstart_normalized != runtime_normalized:
                blockers.append(
                    "AQSP_COLDSTART_DB_PATH must match AQSP_SQLITE_DB_PATH for sqlite_db runtime"
                )

        if coldstart_update_script:
            script_name = Path(coldstart_update_script).name.lower()
            if script_name == "update_daily.py":
                blockers.append(
                    "AQSP_COLDSTART_UPDATE_SCRIPT points to legacy qfq updater"
                )

    script_path = root / "scripts" / "coldstart_daily.sh"
    if script_path.exists():
        text = script_path.read_text(encoding="utf-8")
        required_tokens = (
            "detect_sqlite_price_mode",
            'AQSP_COLDSTART_PRICE_MODE:-$(detect_sqlite_price_mode "$SQLITE_DB_PATH")',
            'UPDATE_ARGS+=(--price-mode "$SQLITE_PRICE_MODE")',
            "sqlite_db 运行时要求 coldstart 更新 raw 历史库",
        )
        missing = [token for token in required_tokens if token not in text]
        if missing:
            blockers.append("coldstart_daily.sh missing raw sqlite guardrails")

    return ReadinessFinding(
        "coldstart_runtime_alignment",
        not blockers,
        "ok" if not blockers else "; ".join(blockers[:4]),
    )


def _check_short_line_subcommand_universe(root: Path) -> ReadinessFinding:
    cli_path = root / "src" / "aqsp" / "cli.py"
    if not cli_path.exists():
        return ReadinessFinding(
            "short_line_subcommand_universe",
            True,
            "source tree unavailable; skipped",
        )
    text = cli_path.read_text(encoding="utf-8")
    blockers = []
    parser_blockers = []
    for marker in (
        "def run_morning_breakout",
        "def run_closing_premium",
        "def run_multi_factor",
        "def run_evolve",
    ):
        start = text.find(marker)
        if start < 0:
            continue
        end = text.find("\ndef ", start + 1)
        block = text[start:] if end < 0 else text[start:end]
        if "max_universe=300" in block:
            blockers.append(marker.removeprefix("def "))
    for default_marker in (
        'multi_factor_cmd.add_argument("--pool", default="sh300")',
        'morning_cmd.add_argument("--pool", default="sh300")',
        'closing_cmd.add_argument("--pool", default="sh300")',
        'wf.add_argument(\n        "--pool",\n        type=str,\n        default=None,',
    ):
        if default_marker in text:
            parser_blockers.append(default_marker)
    ok = not blockers and not parser_blockers
    detail_parts = []
    if blockers:
        detail_parts.append("hard-coded 300 universe cap: " + ", ".join(blockers))
    if parser_blockers:
        detail_parts.append("default small-pool entrypoints detected")
    return ReadinessFinding(
        "short_line_subcommand_universe",
        ok,
        "ok" if ok else "; ".join(detail_parts),
    )


def _check_cold_start_gate_config(root: Path) -> ReadinessFinding:
    raw_value = read_env_value(root / ".env", "AQSP_COLD_START_MIN_DAYS")
    if not raw_value:
        return ReadinessFinding(
            "cold_start_gate_config",
            True,
            f"default {MIN_INDEPENDENT_SIGNAL_DAYS} independent signal days",
        )
    try:
        min_days = int(raw_value)
    except ValueError:
        return ReadinessFinding(
            "cold_start_gate_config",
            False,
            f"AQSP_COLD_START_MIN_DAYS invalid: {raw_value!r}",
        )
    ok = min_days >= MIN_INDEPENDENT_SIGNAL_DAYS
    return ReadinessFinding(
        "cold_start_gate_config",
        ok,
        f"AQSP_COLD_START_MIN_DAYS={min_days}; production requires >= {MIN_INDEPENDENT_SIGNAL_DAYS}",
    )


def _check_strategy_threshold_consistency(
    thresholds: Thresholds | None = None,
) -> ReadinessFinding:
    try:
        current = thresholds or load_thresholds()
    except ValueError as exc:
        return ReadinessFinding(
            "strategy_threshold_consistency",
            False,
            f"thresholds unavailable: {exc}",
        )
    blockers = _strategy_threshold_consistency_blockers(current)
    return ReadinessFinding(
        "strategy_threshold_consistency",
        not blockers,
        "ok" if not blockers else "; ".join(blockers),
    )


def _strategy_threshold_consistency_blockers(thresholds: Thresholds) -> list[str]:
    composite = thresholds.composite
    blockers: list[str] = []
    blend_sum = composite.base_blend_weight + composite.regime_blend_weight
    if composite.base_blend_weight < 0 or composite.regime_blend_weight < 0:
        blockers.append("composite blend weights must be non-negative")
    if abs(blend_sum - 1.0) > 1e-9:
        blockers.append(
            f"composite blend weights must sum to 1.0 (got {blend_sum:.4f})"
        )
    if thresholds.quality.enabled and composite.quality_weight <= 0:
        blockers.append("quality.enabled=true but composite.quality_weight<=0")
    if not thresholds.quality.enabled and composite.quality_weight > 0:
        blockers.append("quality.enabled=false but composite.quality_weight>0")
    if thresholds.value.enabled and composite.value_weight <= 0:
        blockers.append("value.enabled=true but composite.value_weight<=0")
    if not thresholds.value.enabled and composite.value_weight > 0:
        blockers.append("value.enabled=false but composite.value_weight>0")
    if thresholds.volume.enabled and composite.volume_weight <= 0:
        blockers.append("volume.enabled=true but composite.volume_weight<=0")
    if not thresholds.volume.enabled and composite.volume_weight > 0:
        blockers.append("volume.enabled=false but composite.volume_weight>0")
    if thresholds.mean_reversion.enabled and composite.mean_reversion_weight <= 0:
        blockers.append(
            "mean_reversion.enabled=true but composite.mean_reversion_weight<=0"
        )
    if not thresholds.mean_reversion.enabled and composite.mean_reversion_weight > 0:
        blockers.append(
            "mean_reversion.enabled=false but composite.mean_reversion_weight>0"
        )
    if thresholds.triple_rise.enabled and composite.triple_rise_weight <= 0:
        blockers.append("triple_rise.enabled=true but composite.triple_rise_weight<=0")
    if not thresholds.triple_rise.enabled and composite.triple_rise_weight > 0:
        blockers.append("triple_rise.enabled=false but composite.triple_rise_weight>0")
    return blockers


def _check_strategy_runtime_threshold_application(root: Path) -> ReadinessFinding:
    path = root / "src" / "aqsp" / "strategy.py"
    if not path.exists():
        return ReadinessFinding(
            "strategy_runtime_threshold_application",
            True,
            "source tree unavailable; skipped",
        )
    text = path.read_text(encoding="utf-8")
    ok = _strategy_runtime_threshold_application_ok(text)
    return ReadinessFinding(
        "strategy_runtime_threshold_application",
        ok,
        "ok"
        if ok
        else "runtime screening must pass full Thresholds and skip strategy ids omitted by explicit regime weights",
    )


def _strategy_runtime_threshold_application_ok(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    screen_func = _find_ast_function(tree, "screen_universe")
    score_func = _find_ast_function(tree, "score_symbol")
    if screen_func is None or score_func is None:
        return False
    passes_full_thresholds = any(
        isinstance(node, ast.Call)
        and _ast_name(node.func) == "score_symbol"
        and len(node.args) >= 5
        and _ast_name(node.args[4]) == "current_thresholds"
        for node in ast.walk(screen_func)
    )
    skips_omitted_strategy = any(
        isinstance(node, ast.If)
        and any(isinstance(child, ast.Continue) for child in ast.walk(node))
        and "config.strategy_weights" in ast.unparse(node.test)
        and "signal.strategy_id not in config.strategy_weights"
        in ast.unparse(node.test)
        for node in ast.walk(score_func)
    )
    return passes_full_thresholds and skips_omitted_strategy


def _find_ast_function(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    return next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        ),
        None,
    )


def _ast_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _ast_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _check_regime_strategy_weight_blending(root: Path) -> ReadinessFinding:
    path = root / "src" / "aqsp" / "strategy.py"
    if not path.exists():
        return ReadinessFinding(
            "regime_strategy_weight_blending",
            True,
            "source tree unavailable; skipped",
        )
    text = path.read_text(encoding="utf-8")
    block = _cli_function_block(text, "strategy_weights_for_regime")
    helper_block = _cli_function_block(text, "_blend_regime_multiplier")
    ok = (
        "_blend_regime_multiplier(thresholds, weight)" in block
        and "base_blend_weight" in helper_block
        and "regime_blend_weight" in helper_block
    )
    return ReadinessFinding(
        "regime_strategy_weight_blending",
        ok,
        "ok"
        if ok
        else "screening strategy weights must use the same composite regime blend formula as CompositeStrategy",
    )


def _check_runtime_regime_requires_benchmark(root: Path) -> ReadinessFinding:
    path = root / "src" / "aqsp" / "regime" / "runtime.py"
    if not path.exists():
        return ReadinessFinding(
            "runtime_regime_requires_benchmark",
            True,
            "source tree unavailable; skipped",
        )
    text = path.read_text(encoding="utf-8")
    block = _cli_function_block(text, "detect_runtime_regime")
    context_block = _cli_function_block(text, "detect_runtime_regime_context")
    ok = (
        "build_synthetic_regime_frame(" not in block
        and "build_synthetic_regime_frame(" not in context_block
        and '"synthetic_market"' not in block
        and '"synthetic_market"' not in context_block
        and (
            'return ""' in block
            or 'RuntimeRegimeContext("", "", 0.0, 0.0, "missing_benchmark")'
            in context_block
        )
    )
    return ReadinessFinding(
        "runtime_regime_requires_benchmark",
        ok,
        "ok"
        if ok
        else "runtime scoring regime must fail closed when benchmark data is missing; synthetic candidate breadth is diagnostic-only",
    )


def _check_pbo_diagnostics(root: Path, gate_path: Path) -> ReadinessFinding:
    gate = _read_json(gate_path)
    if not gate or gate.get("pbo_pass") is not False:
        return ReadinessFinding("pbo_diagnostics", True, "not required")

    grid_diagnostics = gate.get("grid_diagnostics")
    if isinstance(grid_diagnostics, dict) and grid_diagnostics:
        required_keys = (
            "n_combos",
            "n_lambda_le_0",
            "worst_periods",
            "selection_inversions",
            "best_variant",
        )
        missing_keys = [
            key
            for key in required_keys
            if key not in grid_diagnostics
            or grid_diagnostics.get(key) in (None, "", [], {})
        ]
        if not missing_keys:
            return ReadinessFinding("pbo_diagnostics", True, "ok(sidecar)")

    report_path = next(
        (path for path in _pbo_diagnostic_report_candidates(root) if path.exists()),
        None,
    )
    if report_path is None:
        candidates = ", ".join(
            str(path) for path in _pbo_diagnostic_report_candidates(root)
        )
        return ReadinessFinding(
            "pbo_diagnostics",
            False,
            f"PBO failed but diagnostics report missing: {candidates}",
        )
    text = report_path.read_text(encoding="utf-8")
    required = (
        "### PBO 失败定位",
        "CSCV 失败组合占比",
        "最差对齐周期",
        "训练选中变体",
        "测试最优变体",
    )
    missing = [item for item in required if item not in text]
    return ReadinessFinding(
        "pbo_diagnostics",
        not missing,
        "ok" if not missing else "missing: " + ", ".join(missing),
    )


def _check_trading_calendar_coverage(root: Path, today: date) -> ReadinessFinding:
    path = root / "config" / "trading_holidays.json"
    payload = _read_json(path)
    if not payload:
        return ReadinessFinding("trading_calendar_coverage", False, "calendar missing")
    dates = []
    for key in ("holidays", "makeup_workdays"):
        for item in payload.get(key, []):
            parsed = _parse_date(item)
            if parsed is not None:
                dates.append(parsed)
    covered_years = {item.year for item in dates}
    required_years = {today.year}
    if today.month >= 11:
        required_years.add(today.year + 1)
    missing = sorted(required_years - covered_years)
    critical_holidays = {
        date(2026, 1, 1),
        date(2026, 2, 16),
        date(2026, 2, 17),
        date(2026, 2, 18),
        date(2026, 2, 19),
        date(2026, 2, 20),
        date(2026, 2, 21),
        date(2026, 2, 22),
        date(2026, 4, 6),
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 6, 19),
        date(2026, 9, 25),
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 5),
        date(2026, 10, 6),
        date(2026, 10, 7),
        date(2026, 10, 8),
    }
    critical_missing = sorted(
        holiday.isoformat()
        for holiday in critical_holidays
        if holiday.year in required_years and holiday not in dates
    )
    return ReadinessFinding(
        "trading_calendar_coverage",
        not missing and not critical_missing,
        "ok"
        if not missing and not critical_missing
        else "; ".join(
            part
            for part in (
                ("missing years: " + ", ".join(map(str, missing)) if missing else ""),
                (
                    "missing critical holidays: " + ", ".join(critical_missing)
                    if critical_missing
                    else ""
                ),
            )
            if part
        ),
    )


def _check_signal_sample_size(path: Path) -> ReadinessFinding:
    count = count_independent_signal_days(str(path))
    simulated_count = len(collect_simulated_signal_dates(str(path)))
    detail = f"{count}/{MIN_INDEPENDENT_SIGNAL_DAYS} real independent signal days"
    rows = _read_jsonl(path)
    latest_real_signal_day = latest_independent_signal_day(str(path))
    blocked_runtime_days = sorted(
        {
            ledger_signal_date(row)
            for row in rows
            if ledger_signal_date(row)
            and str(row.get("symbol") or "").strip() == "__RUN__"
            and str(row.get("status") or "").strip() == "blocked_by_circuit_breaker"
        }
    )
    if latest_real_signal_day:
        detail += f"; latest real signal day={latest_real_signal_day}"
    if blocked_runtime_days:
        detail += f"; blocked runtime days={len(blocked_runtime_days)}"
    if simulated_count > 0:
        detail += f"; excluded simulated days={simulated_count}"
    return ReadinessFinding(
        "signal_sample_size",
        count >= MIN_INDEPENDENT_SIGNAL_DAYS,
        detail,
    )


def _check_gate_cold_start_alignment(
    root: Path,
    ledger_path: Path,
    *,
    today: date,
) -> ReadinessFinding:
    signal_days = count_independent_signal_days(str(ledger_path))
    if signal_days < MIN_INDEPENDENT_SIGNAL_DAYS:
        return ReadinessFinding(
            "gate_cold_start_alignment",
            True,
            f"signal days below cold-start gate: {signal_days}/{MIN_INDEPENDENT_SIGNAL_DAYS}",
        )
    env_path = root / ".env"
    value = (
        _read_env_assignment(env_path, "AQSP_GATE_NOTIFY_STATE_PATH")
        if env_path.exists()
        else ""
    )
    gate_state_path = _normalize_runtime_path(
        root, value or "data/gate_notify_state.json"
    )
    gate_path = _normalize_runtime_path(
        root,
        (
            _read_env_assignment(env_path, "AQSP_WALKFORWARD_GATE_PATH")
            if env_path.exists()
            else ""
        )
        or WALKFORWARD_GATE_PATH,
    )
    gate_ok, gate_reasons = _check_notification_gate(
        cold_start_days=signal_days,
        gate_path=str(gate_path),
        validation_date=today,
    )
    expected_fingerprint = gate_reason_fingerprint(gate_reasons) if gate_reasons else ""
    payload = _read_json(gate_state_path)
    if not payload:
        if gate_ok:
            return ReadinessFinding(
                "gate_cold_start_alignment",
                True,
                (
                    f"signal days {signal_days}/{MIN_INDEPENDENT_SIGNAL_DAYS}; "
                    "current gate open and gate notify state already cleared"
                ),
            )
        return ReadinessFinding(
            "gate_cold_start_alignment",
            False,
            (
                f"signal days reached {signal_days}/{MIN_INDEPENDENT_SIGNAL_DAYS} "
                f"but gate notify state missing/unreadable: {gate_state_path}"
            ),
        )
    sent_by_date = payload.get("sent_by_date", {})
    latest_fingerprint = ""
    latest_date = ""
    if isinstance(sent_by_date, dict) and sent_by_date:
        latest_date = max(str(key) for key in sent_by_date)
        latest_entry = sent_by_date.get(latest_date)
        if isinstance(latest_entry, dict):
            latest_fingerprint = str(latest_entry.get("fingerprint") or "")
        elif isinstance(latest_entry, str):
            latest_fingerprint = latest_entry
    fingerprint_tokens = {token for token in latest_fingerprint.split("|") if token}
    if "cold_start" in fingerprint_tokens:
        return ReadinessFinding(
            "gate_cold_start_alignment",
            False,
            (
                f"signal days reached {signal_days}/{MIN_INDEPENDENT_SIGNAL_DAYS} "
                f"but latest gate fingerprint still contains cold_start"
                + (f" ({latest_date}: {latest_fingerprint})" if latest_date else "")
            ),
        )
    if gate_ok and latest_fingerprint:
        return ReadinessFinding(
            "gate_cold_start_alignment",
            False,
            (
                f"signal days reached {signal_days}/{MIN_INDEPENDENT_SIGNAL_DAYS} "
                f"and current gate is open, but gate notify state still records "
                f"{latest_fingerprint or '-'}"
            ),
        )
    if (
        (not gate_ok)
        and expected_fingerprint
        and latest_fingerprint != expected_fingerprint
    ):
        return ReadinessFinding(
            "gate_cold_start_alignment",
            False,
            (
                f"signal days reached {signal_days}/{MIN_INDEPENDENT_SIGNAL_DAYS} "
                f"but gate notify fingerprint drifted: state={latest_fingerprint or '-'} "
                f"expected={expected_fingerprint}"
            ),
        )
    return ReadinessFinding(
        "gate_cold_start_alignment",
        True,
        (
            f"signal days {signal_days}/{MIN_INDEPENDENT_SIGNAL_DAYS}; "
            f"latest gate fingerprint={latest_fingerprint or '-'}"
        ),
    )


def _check_paper_tracking_sample_size(path: Path) -> ReadinessFinding:
    count = count_paper_tracking_days(str(path))
    predictions_path = path.with_name("predictions.jsonl")
    missing_tradable_days = _paper_missing_tradable_signal_days(
        predictions_path=predictions_path,
        paper_path=path,
    )
    tradable_days = _tradable_signal_days(predictions_path)
    ceiling_active = bool(tradable_days)
    effective_target = (
        min(MIN_INDEPENDENT_SIGNAL_DAYS, len(tradable_days))
        if ceiling_active
        else MIN_INDEPENDENT_SIGNAL_DAYS
    )
    detail = f"{count}/{MIN_INDEPENDENT_SIGNAL_DAYS} real paper tracking days"
    if missing_tradable_days:
        preview = ", ".join(missing_tradable_days[:5])
        if len(missing_tradable_days) > 5:
            preview += ", ..."
        detail += (
            f"; missing tradable signal days={len(missing_tradable_days)} [{preview}]"
        )
    elif (
        ceiling_active
        and effective_target < MIN_INDEPENDENT_SIGNAL_DAYS
        and count >= effective_target
    ):
        detail += (
            f"; tradable signal day ceiling={effective_target}/"
            f"{MIN_INDEPENDENT_SIGNAL_DAYS}"
        )
    elif count < MIN_INDEPENDENT_SIGNAL_DAYS:
        detail += "; no additional tradable signal days available yet"
    return ReadinessFinding(
        "paper_tracking_sample_size",
        count >= effective_target and not missing_tradable_days,
        detail,
    )


def _paper_missing_tradable_signal_days(
    *,
    predictions_path: Path,
    paper_path: Path,
) -> list[str]:
    if not predictions_path.exists() or not paper_path.exists():
        return []
    paper_days = {
        ledger_signal_date(row)
        for row in _read_jsonl(paper_path)
        if ledger_signal_date(row)
    }
    tradable_days = _tradable_signal_days(predictions_path)
    return sorted(tradable_days - paper_days)


def _tradable_signal_days(predictions_path: Path) -> set[str]:
    tradable_days: set[str] = set()
    for row in _read_jsonl(predictions_path):
        if str(row.get("status") or "").strip() not in {
            "pending",
            "validated",
            "not_executable",
        }:
            continue
        if str(row.get("rating") or "").strip() not in {
            "buy_candidate",
            "strong_buy_candidate",
        }:
            continue
        signal_day = ledger_signal_date(row)
        if signal_day:
            tradable_days.add(signal_day)
    return tradable_days


def _check_signal_sample_status_boundary(root: Path) -> ReadinessFinding:
    path = root / "src" / "aqsp" / "ledger" / "runtime.py"
    if not path.exists():
        return ReadinessFinding(
            "signal_sample_status_boundary",
            True,
            "source tree unavailable; skipped",
        )
    text = path.read_text(encoding="utf-8")
    start = text.find("REAL_SIGNAL_STATUSES")
    end = text.find("PAPER_TRACKING_STATUSES", start)
    block = text[start:end] if start >= 0 and end > start else ""
    forbidden = [
        status
        for status in ("open", "closed", "pending_entry")
        if f'"{status}"' in block
    ]
    return ReadinessFinding(
        "signal_sample_status_boundary",
        not forbidden,
        "ok"
        if not forbidden
        else "cold-start signal samples must not count paper-only statuses: "
        + ", ".join(forbidden),
    )


def _check_strategy_executability_feedback(path: Path) -> ReadinessFinding:
    rows = _read_jsonl(path)
    attempts: dict[str, int] = {}
    blocked: dict[str, int] = {}
    attempted_statuses = {"open", "closed", "not_executable"}
    for row in rows:
        status = str(row.get("status") or "")
        if status not in attempted_statuses:
            continue
        for strategy in row.get("strategies") or []:
            key = str(strategy)
            if not key:
                continue
            attempts[key] = attempts.get(key, 0) + 1
            if status == "not_executable":
                blocked[key] = blocked.get(key, 0) + 1
    offenders = []
    for strategy, total in sorted(attempts.items()):
        if total < MIN_EXECUTABILITY_STRATEGY_ATTEMPTS:
            continue
        rate = blocked.get(strategy, 0) / total
        if rate > MAX_STRATEGY_NOT_EXECUTABLE_RATE:
            offenders.append(
                f"{strategy}={rate:.0%} ({blocked.get(strategy, 0)}/{total})"
            )
    return ReadinessFinding(
        "strategy_executability_feedback",
        not offenders,
        "ok"
        if not offenders
        else "not_executable too high: " + ", ".join(offenders[:3]),
    )


def _check_strategy_executability_runtime_feedback(root: Path) -> ReadinessFinding:
    cli_path = root / "src" / "aqsp" / "cli.py"
    runtime_path = root / "src" / "aqsp" / "ledger" / "runtime.py"
    if not cli_path.exists() or not runtime_path.exists():
        return ReadinessFinding(
            "strategy_executability_runtime_feedback",
            True,
            "source tree unavailable; skipped",
        )
    cli_text = cli_path.read_text(encoding="utf-8")
    block = _cli_function_block(cli_text, "_run_scheduled_legacy")
    missing = []
    if "weights = _runtime_strategy_weights(thresholds, regime)" not in block:
        missing.append("runtime weight resolver")

    forbidden = []
    if "strategy_executability_weight_adjustments" in block:
        forbidden.append("strategy_executability_weight_adjustments call")
    if re.search(r"\bweights\s*\[", block):
        forbidden.append("runtime weight mutation")
    if "不可成交反馈降权:" in block:
        forbidden.append("not_executable downweight output")

    ok = not missing and not forbidden
    detail = "ok"
    if not ok:
        problems = [f"missing {item}" for item in missing] + [
            f"forbidden {item}" for item in forbidden
        ]
        detail = (
            "runtime strategy weights must be deterministic and independent of not_executable history: "
            + ", ".join(problems)
        )
    return ReadinessFinding(
        "strategy_executability_runtime_feedback",
        ok,
        detail,
    )


def _check_strategy_weight_snapshot_audit(root: Path) -> ReadinessFinding:
    cli_path = root / "src" / "aqsp" / "cli.py"
    ledger_path = root / "src" / "aqsp" / "ledger" / "base.py"
    if not cli_path.exists() or not ledger_path.exists():
        return ReadinessFinding(
            "strategy_weight_snapshot_audit",
            True,
            "source tree unavailable; skipped",
        )
    cli_text = cli_path.read_text(encoding="utf-8")
    ledger_text = ledger_path.read_text(encoding="utf-8")
    required_cli_helpers = (
        "_runtime_weight_snapshot(",
        "def _attach_runtime_weight_snapshot(",
        '"strategy_weight_snapshot": snapshot',
    )
    scheduled_block = _cli_function_block(cli_text, "_run_scheduled_legacy")
    required_scheduled = (
        "screened_picks = _attach_runtime_weight_snapshot(",
        "strategy_weights=weights",
    )
    required_ledger_fields = (
        "strategy_weight_snapshot",
        "composite_score_raw",
        "composite_score_normalized",
        "base_score_before_composite",
        "final_score_after_composite",
    )
    missing = [
        f"cli:{token}" for token in required_cli_helpers if token not in cli_text
    ]
    missing.extend(
        f"scheduled:{token}"
        for token in required_scheduled
        if token not in scheduled_block
    )
    missing.extend(
        f"ledger:{field}"
        for field in required_ledger_fields
        if not re.search(
            rf'"{re.escape(field)}"\s*:\s*pick\.metrics\.get\s*\(\s*"{re.escape(field)}"',
            ledger_text,
            flags=re.DOTALL,
        )
    )
    return ReadinessFinding(
        "strategy_weight_snapshot_audit",
        not missing,
        "ok"
        if not missing
        else "runtime ranking is not reproducible from ledger: "
        + ", ".join(missing[:4]),
    )


def _check_successful_runs(path: Path, *, root: Path) -> ReadinessFinding:
    history_rows = _read_jsonl(path)
    pipeline_rows = _read_pipeline_history(root)
    daily_log_rows = _read_daily_log_history(root)
    ledger_run_rows = _read_ledger_run_history(root)
    rows = (
        history_rows
        + [
            row
            for row in pipeline_rows
            if str(row.get("date") or "").strip()
            not in {
                str(item.get("date") or item.get("run_date") or "").strip()
                for item in history_rows
            }
        ]
        + [
            row
            for row in daily_log_rows
            if str(row.get("date") or row.get("run_date") or "").strip()
            not in {
                str(item.get("date") or item.get("run_date") or "").strip()
                for item in history_rows + pipeline_rows
            }
        ]
        + [
            row
            for row in ledger_run_rows
            if str(row.get("date") or row.get("run_date") or "").strip()
            not in {
                str(item.get("date") or item.get("run_date") or "").strip()
                for item in history_rows + pipeline_rows + daily_log_rows
            }
        ]
    )
    source_parts: list[str] = []
    if history_rows:
        source_parts.append("daily_run_history")
    if pipeline_rows:
        source_parts.append("pipeline_logs")
    if daily_log_rows:
        source_parts.append("daily_logs")
    if ledger_run_rows:
        source_parts.append("ledger_run_events")
    source = "+".join(source_parts) if source_parts else "none"
    successful_days = {
        str(row.get("date") or row.get("run_date") or "").strip()
        for row in rows
        if row.get("success") is True or row.get("exit_code") == 0
    }
    count = len({day for day in successful_days if day})
    return ReadinessFinding(
        "successful_daily_runs",
        count >= MIN_SUCCESSFUL_RUN_DAYS,
        f"{count}/{MIN_SUCCESSFUL_RUN_DAYS} successful daily run days ({source})",
    )


def _check_scheduler_notify_cadence(
    root: Path, *, cron_path: Path | None, cron_dir: Path | None = None
) -> ReadinessFinding:
    texts: list[tuple[str, str]] = []
    cron_text = ""
    for path in (
        root / "scripts" / "daily_pipeline.sh",
        root / "scripts" / "daily_run.sh",
        root / "scripts" / "intraday_refresh.sh",
        root / "scripts" / "midday_refresh.sh",
        root / "scripts" / "news_catalysts.sh",
        root / "scripts" / "server_monitor.sh",
    ):
        if path.exists():
            texts.append(
                (str(path.relative_to(root)), path.read_text(encoding="utf-8"))
            )
    if cron_path is not None and cron_path.exists():
        cron_text = cron_path.read_text(encoding="utf-8")
        texts.append((str(cron_path), cron_text))
    elif cron_dir is not None:
        cron_text = _load_live_crontab_text()
        if cron_text:
            texts.append(("live_crontab", cron_text))
    cron_schedule_map = _cron_wrapper_schedule_map(cron_text)
    texts.extend(_read_cron_wrapper_texts(cron_dir))

    blockers: list[str] = []
    for label, text in texts:
        for line in text.splitlines():
            clean = line.strip()
            if not clean or clean.startswith("#"):
                continue
            has_direct_notify = (
                "--notify" in clean and "notify-critical-only" not in clean
            )
            high_frequency = _looks_like_high_frequency_schedule(clean)
            if high_frequency and (
                has_direct_notify or _looks_like_high_frequency_daily(clean)
            ):
                blockers.append(f"{label}: {clean}")
            if has_direct_notify and any(
                task in clean for task in ("intraday", "midday")
            ):
                blockers.append(f"{label}: {clean}")
        if _should_check_cron_entry_bypass(label) and _cron_wrapper_bypasses_bt_task(
            text
        ):
            blockers.append(f"{label}: trading job bypasses bt_task.sh")
        if _should_check_cron_entry_bypass(label) and _wrapper_enables_daily_notify(
            text
        ):
            blockers.append(f"{label}: wrapper enables AQSP_NOTIFY for daily")
        if _should_check_cron_entry_bypass(label) and _wrapper_enables_gate_notify(
            text
        ):
            blockers.append(f"{label}: wrapper enables AQSP_GATE_NOTIFY for daily")
        wrapper_schedule_blocker = _cron_wrapper_schedule_blocker(
            label=label,
            text=text,
            cron_schedule_map=cron_schedule_map,
        )
        if wrapper_schedule_blocker:
            blockers.append(wrapper_schedule_blocker)
        legacy_frequency_blocker = _cron_wrapper_legacy_frequency_blocker(
            label=label,
            text=text,
            cron_schedule_map=cron_schedule_map,
        )
        if legacy_frequency_blocker:
            blockers.append(legacy_frequency_blocker)

    return ReadinessFinding(
        "scheduler_notify_cadence",
        not blockers,
        "ok"
        if not blockers
        else "high-frequency notify risk: " + " | ".join(blockers[:3]),
    )


def _check_system_cron_install_guard(root: Path) -> ReadinessFinding:
    path = root / "scripts" / "install_server_cron.sh"
    if not path.exists():
        return ReadinessFinding(
            "system_cron_install_guard",
            True,
            "system cron installer unavailable; skipped",
        )
    text = path.read_text(encoding="utf-8")
    ok = (
        "AQSP_INSTALL_SYSTEM_CRON" in text
        and "system cron install skipped" in text
        and "exit 0" in text
    )
    return ReadinessFinding(
        "system_cron_install_guard",
        ok,
        "ok"
        if ok
        else "system cron installer must default to no-op so production does not double-run with BT Panel",
    )


def _check_launchd_wrapper_drift(root: Path) -> ReadinessFinding:
    wrapper = Path.home() / ".aqsp/aqsp_daily_run_wrapper.sh"
    repo_wrapper = root / "scripts" / "launchd" / "aqsp_daily_run_wrapper.sh"
    if not wrapper.exists():
        return ReadinessFinding(
            "launchd_wrapper_drift",
            True,
            "local launchd wrapper missing; skipped",
        )
    if not repo_wrapper.exists():
        return ReadinessFinding(
            "launchd_wrapper_drift",
            True,
            "repo launchd wrapper missing; skipped",
        )
    current = wrapper.read_text(encoding="utf-8", errors="ignore")
    expected = repo_wrapper.read_text(encoding="utf-8", errors="ignore")
    if current == expected:
        return ReadinessFinding("launchd_wrapper_drift", True, "ok")
    current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()[:12]
    expected_hash = hashlib.sha256(expected.encode("utf-8")).hexdigest()[:12]
    markers = [
        token
        for token in ("aqsp paper", "aqsp dashboard", "周末跳过")
        if token in current
    ]
    suffix = f" markers={','.join(markers)}" if markers else ""
    return ReadinessFinding(
        "launchd_wrapper_drift",
        False,
        f"~/.aqsp/aqsp_daily_run_wrapper.sh drifted current={current_hash} expected={expected_hash}{suffix}",
    )


def _looks_like_high_frequency_schedule(line: str) -> bool:
    fields = line.split()
    if len(fields) < 6:
        return False
    minute = fields[0]
    return minute.startswith("*/") or "," in minute or "-" in minute


def _looks_like_high_frequency_daily(line: str) -> bool:
    lower = line.lower()
    return any(
        token in lower
        for token in (
            "bt_task.sh daily",
            "run-scheduled",
            " daily_pipeline.sh",
            "/daily_pipeline.sh",
            "-m aqsp run",
        )
    )


def _load_live_crontab_text() -> str:
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def _cron_wrapper_schedule_map(cron_text: str) -> dict[str, list[str]]:
    schedule_map: dict[str, list[str]] = {}
    for line in cron_text.splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        fields = clean.split()
        if len(fields) < 6:
            continue
        command = " ".join(fields[5:])
        matches = re.findall(
            r"/www/server/cron/([a-z0-9._-]+)",
            command,
            flags=re.IGNORECASE,
        )
        if not matches:
            continue
        wrapper_name = matches[-1]
        if wrapper_name.endswith(".lock"):
            wrapper_name = wrapper_name[: -len(".lock")]
        schedule_map.setdefault(wrapper_name, []).append(" ".join(fields[:5]))
    return schedule_map


def _read_cron_wrapper_texts(cron_dir: Path | None) -> list[tuple[str, str]]:
    if cron_dir is None or not cron_dir.exists() or not cron_dir.is_dir():
        return []
    texts: list[tuple[str, str]] = []
    for path in sorted(cron_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix in {".log", ".lock", ".pl"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "aqsp" in content.lower() or "/opt/aqsp" in content:
            texts.append((str(path), content))
    return texts


def _should_check_cron_entry_bypass(label: str) -> bool:
    return not label.startswith("scripts/")


def _cron_wrapper_schedule_blocker(
    *,
    label: str,
    text: str,
    cron_schedule_map: dict[str, list[str]],
) -> str:
    wrapper_name = Path(label).name
    schedules = cron_schedule_map.get(wrapper_name, [])
    if not schedules:
        return ""
    action = _cron_wrapper_action(text)
    if not action:
        return ""
    bad_schedules = [
        schedule
        for schedule in schedules
        if not _schedule_matches_bt_action(schedule=schedule, action=action)
        and not _wrapper_time_gate_matches_action(text=text, action=action)
    ]
    if not bad_schedules:
        return ""
    preview = ", ".join(bad_schedules[:2])
    return f'{label}: unexpected wrapper cadence for {action} (cron="{preview}")'


def _cron_wrapper_legacy_frequency_blocker(
    *,
    label: str,
    text: str,
    cron_schedule_map: dict[str, list[str]],
) -> str:
    wrapper_name = Path(label).name
    schedules = cron_schedule_map.get(wrapper_name, [])
    if not schedules:
        return ""
    action = _cron_wrapper_action(text)
    if action != "news":
        return ""
    if _wrapper_time_gate_matches_action(text=text, action=action):
        return ""
    for schedule in schedules:
        fields = schedule.split()
        if len(fields) != 5:
            continue
        minute, hour, _dom, _month, weekday = fields
        weekday_normalized = weekday.replace("7", "0")
        if minute == "*/5" and hour == "*" and weekday_normalized == "*":
            return f'{label}: legacy all-day */5 cadence for news (cron="{schedule}")'
    return ""


def _cron_wrapper_action(text: str) -> str:
    match = re.search(r"bt_task\.sh\s+([a-z]+)", text)
    if match:
        return match.group(1).strip().lower()
    if "server_monitor.sh" in text:
        return "monitor"
    return ""


def _schedule_matches_bt_action(*, schedule: str, action: str) -> bool:
    fields = schedule.split()
    if len(fields) != 5:
        return True
    minute, hour, _dom, _month, weekday = fields
    normalized_action = action.strip().lower()
    weekday_normalized = weekday.replace("7", "0")
    weekday_only = weekday_normalized in {"1-5", "1,2,3,4,5"}
    if normalized_action == "intraday":
        return minute == "*/10" and weekday_only
    if normalized_action == "monitor":
        return minute == "*/15" and weekday_only
    if normalized_action == "daily":
        return minute in {"0", "00"} and hour == "18" and weekday_only
    if normalized_action == "midday":
        return minute in {"5", "05"} and hour == "12" and weekday_only
    if normalized_action == "coldstart":
        return minute == "40" and hour == "19" and weekday_only
    if normalized_action == "news":
        is_weekday_run = (
            minute == "35"
            and hour == "8"
            and weekday_normalized in {"1-5", "1,2,3,4,5"}
        )
        is_weekend_run = (
            minute in {"5", "05"}
            and hour == "9"
            and weekday_normalized in {"6,0", "6,7", "0,6", "6-0"}
        )
        return is_weekday_run or is_weekend_run
    return True


def _wrapper_time_gate_matches_action(*, text: str, action: str) -> bool:
    normalized_action = action.strip().lower()
    if normalized_action != "news":
        return False
    lowered = text.lower()
    if "time_check.py" not in lowered:
        return False
    weekday_gate = "special_time=08:35" in text and "time_list=1,2,3,4,5" in text
    weekend_gate = "special_time=09:05" in text and "time_list=6,7" in text
    return weekday_gate or weekend_gate


def _cron_wrapper_bypasses_bt_task(text: str) -> bool:
    lower = text.lower()
    if "bt_task.sh" in lower:
        return False
    if "aqsp" not in lower and "/opt/aqsp" not in lower:
        return False
    return any(
        token in lower
        for token in (
            "daily_pipeline.sh",
            "intraday_refresh.sh",
            "midday_refresh.sh",
            "coldstart_daily.sh",
            "-m aqsp run",
            "aqsp.cli run",
            "news_catalysts.sh",
        )
    )


def _wrapper_enables_daily_notify(text: str) -> bool:
    lower = text.lower()
    if "aqsp_notify=true" not in lower and "aqsp_notify=1" not in lower:
        return False
    return _looks_like_high_frequency_daily(lower) or "aqsp_run_task_id=daily" in lower


def _wrapper_enables_gate_notify(text: str) -> bool:
    lower = text.lower()
    if "aqsp_gate_notify=true" not in lower and "aqsp_gate_notify=1" not in lower:
        return False
    return _looks_like_high_frequency_daily(lower) or "aqsp_run_task_id=daily" in lower


def _check_notify_state_paths(root: Path) -> list[ReadinessFinding]:
    return [
        _check_notify_state_path(
            root,
            gate="gate_notify_state_path",
            env_name="AQSP_GATE_NOTIFY_STATE_PATH",
            default_value="data/gate_notify_state.json",
        ),
        _check_notify_state_path(
            root,
            gate="notify_state_path",
            env_name="AQSP_NOTIFY_STATE_PATH",
            default_value="data/notify_state.json",
        ),
        _check_notify_state_path(
            root,
            gate="monitor_notify_state_path",
            env_name="AQSP_MONITOR_NOTIFY_STATE_PATH",
            default_value="data/monitor_notify_state.json",
        ),
    ]


def _check_notify_channels(root: Path) -> ReadinessFinding:
    env_path = root / ".env"
    notify_enabled = str(
        read_env_value(env_path, "AQSP_NOTIFY") or ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    channel_keys = (
        "SERVERCHAN_SENDKEY",
        "WECHAT_WEBHOOK_URL",
        "BARK_URL",
        "PUSHPLUS_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "FEISHU_WEBHOOK_URL",
        "DINGTALK_WEBHOOK_URL",
        "DISCORD_WEBHOOK_URL",
        "SLACK_WEBHOOK_URL",
        "GENERIC_WEBHOOK_URL",
    )
    notify_mode = read_env_value(env_path, "AQSP_NOTIFY_MODE").strip().lower()
    if notify_mode and notify_mode not in {"summary", "full", "fanout"}:
        return ReadinessFinding(
            "notify_channels",
            False,
            "AQSP_NOTIFY_MODE must be one of summary/full/fanout",
        )
    url_keys = (
        "WECHAT_WEBHOOK_URL",
        "FEISHU_WEBHOOK_URL",
        "DINGTALK_WEBHOOK_URL",
        "DISCORD_WEBHOOK_URL",
        "SLACK_WEBHOOK_URL",
        "GENERIC_WEBHOOK_URL",
    )
    configured: list[str] = []
    for key in channel_keys:
        if read_env_value(env_path, key).strip():
            configured.append(key)
    telegram_pair = {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}
    if telegram_pair & set(configured) and not telegram_pair.issubset(set(configured)):
        return ReadinessFinding(
            "notify_channels",
            False,
            "telegram notify config incomplete: need TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID",
        )
    dingtalk_webhook_configured = "DINGTALK_WEBHOOK_URL" in configured
    dingtalk_secret_configured = bool(
        read_env_value(env_path, "DINGTALK_SECRET").strip()
    )
    if dingtalk_secret_configured and not dingtalk_webhook_configured:
        return ReadinessFinding(
            "notify_channels",
            False,
            "dingtalk notify config incomplete: DINGTALK_SECRET requires DINGTALK_WEBHOOK_URL",
        )
    invalid_webhooks = [
        key
        for key in url_keys
        if read_env_value(env_path, key).strip()
        and not _is_http_webhook_url(read_env_value(env_path, key))
    ]
    if invalid_webhooks:
        return ReadinessFinding(
            "notify_channels",
            False,
            "webhook URL must use http/https: " + ", ".join(invalid_webhooks),
        )
    if not configured:
        return ReadinessFinding(
            "notify_channels",
            False,
            "no real notification channel configured in .env; set one of "
            "SERVERCHAN_SENDKEY / WECHAT_WEBHOOK_URL / BARK_URL / PUSHPLUS_TOKEN / "
            "TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID / FEISHU_WEBHOOK_URL / "
            "DINGTALK_WEBHOOK_URL / DISCORD_WEBHOOK_URL / SLACK_WEBHOOK_URL / "
            "GENERIC_WEBHOOK_URL",
        )
    if not notify_enabled:
        return ReadinessFinding(
            "notify_channels",
            False,
            "AQSP_NOTIFY=false in .env; scheduled daily notifications are disabled",
        )
    return ReadinessFinding(
        "notify_channels",
        True,
        "configured: " + ", ".join(configured),
    )


def _is_http_webhook_url(value: str) -> bool:
    """Validate webhook URL shape without contacting the endpoint."""
    if any(char.isspace() for char in value):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _check_cli_subcommand_notify_dedupe(root: Path) -> ReadinessFinding:
    cli_path = root / "src" / "aqsp" / "cli.py"
    if not cli_path.exists():
        return ReadinessFinding(
            "cli_subcommand_notify_dedupe",
            True,
            "source tree unavailable; skipped",
        )
    text = cli_path.read_text(encoding="utf-8")
    direct_calls = text.count("_notify_via_config(")
    helper_definition = 1 if "def _notify_via_config(" in text else 0
    unsafe_calls = max(0, direct_calls - helper_definition)
    return ReadinessFinding(
        "cli_subcommand_notify_dedupe",
        unsafe_calls == 0,
        "ok"
        if unsafe_calls == 0
        else f"{unsafe_calls} direct _notify_via_config calls bypass notification state",
    )


def _check_news_catalysts_failed_notify_guard(root: Path) -> ReadinessFinding:
    cli_path = root / "src" / "aqsp" / "cli.py"
    if not cli_path.exists():
        return ReadinessFinding(
            "news_catalysts_failed_notify_guard",
            True,
            "source tree unavailable; skipped",
        )
    text = cli_path.read_text(encoding="utf-8")
    start = text.find("def run_news_catalysts(")
    if start < 0:
        return ReadinessFinding(
            "news_catalysts_failed_notify_guard",
            False,
            "run_news_catalysts missing",
        )
    next_def = text.find("\ndef ", start + 1)
    block = text[start:] if next_def < 0 else text[start:next_def]
    guard_at = block.find('report.source_status == "failed"')
    dispatch_at = block.find("_dispatch_notification_once(")
    ok = guard_at >= 0 and dispatch_at >= 0 and guard_at < dispatch_at
    return ReadinessFinding(
        "news_catalysts_failed_notify_guard",
        ok,
        "ok"
        if ok
        else "news-catalysts must suppress notification and return nonzero when source_status=failed",
    )


def _cli_function_block(text: str, function_name: str) -> str:
    start = text.find(f"def {function_name}(")
    if start < 0:
        return ""
    next_def = text.find("\ndef ", start + 1)
    return text[start:] if next_def < 0 else text[start:next_def]


def _check_run_scheduled_env_notify_guard(root: Path) -> ReadinessFinding:
    cli_path = root / "src" / "aqsp" / "cli.py"
    if not cli_path.exists():
        return ReadinessFinding(
            "run_scheduled_env_notify_guard",
            True,
            "source tree unavailable; skipped",
        )
    block = _cli_function_block(
        cli_path.read_text(encoding="utf-8"), "_run_scheduled_legacy"
    )
    ok = (
        "runtime_config = load_runtime_config()" in block
        and "notify_requested" in block
        and "env_notify_requested" in block
        and 'normalized_task_id in {"daily", "scheduled"}' in block
        and "args.notify or env_notify_requested" in block
        and "args_notify=notify_requested" in block
    )
    return ReadinessFinding(
        "run_scheduled_env_notify_guard",
        ok,
        "ok"
        if ok
        else "run_scheduled must only auto-apply AQSP_NOTIFY=true for daily/scheduled tasks",
    )


def _check_monitor_warning_notify_guard(root: Path) -> ReadinessFinding:
    cli_path = root / "src" / "aqsp" / "cli.py"
    if not cli_path.exists():
        return ReadinessFinding(
            "monitor_warning_notify_guard",
            True,
            "source tree unavailable; skipped",
        )
    block = _cli_function_block(cli_path.read_text(encoding="utf-8"), "run_monitor")
    ok = (
        "AQSP_MONITOR_NOTIFY_WARNINGS" in block
        and "warning_targets" in block
        and "notify_targets.extend(warning_targets)" in block
        and "sent_targets = send_alerts(notify_targets)" in block
        and "monitor alert still active; duplicate suppressed" in block
    )
    return ReadinessFinding(
        "monitor_warning_notify_guard",
        ok,
        "ok"
        if ok
        else "monitor warning pushes must require AQSP_MONITOR_NOTIFY_WARNINGS=true and full alert bodies must print only after notify dedupe",
    )


def _check_monitor_wrapper_critical_only_default(root: Path) -> ReadinessFinding:
    path = root / "scripts" / "server_monitor.sh"
    if not path.exists():
        return ReadinessFinding(
            "monitor_wrapper_critical_only_default",
            True,
            "server monitor wrapper unavailable; skipped",
        )
    text = path.read_text(encoding="utf-8")
    ok = (
        'NOTIFY_WARNINGS="${AQSP_MONITOR_NOTIFY_WARNINGS:-false}"' in text
        and "MONITOR_ARGS+=( --notify-critical-only )" in text
        and 'case "${NOTIFY_WARNINGS,,}"' in text
    )
    return ReadinessFinding(
        "monitor_wrapper_critical_only_default",
        ok,
        "ok"
        if ok
        else "server monitor must default to critical-only notifications; warning pushes require AQSP_MONITOR_NOTIFY_WARNINGS=true",
    )


def _check_git_sync_health(root: Path) -> ReadinessFinding:
    git_dir = root / ".git"
    if not git_dir.exists():
        return ReadinessFinding(
            "git_sync_health",
            True,
            "git metadata unavailable; skipped",
        )
    try:
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
        sync_result = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", "origin/main...HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return ReadinessFinding(
            "git_sync_health",
            True,
            "git sync probe unavailable; skipped",
        )
    if sync_result.returncode != 0:
        return ReadinessFinding(
            "git_sync_health",
            True,
            "origin/main sync probe unavailable; skipped",
        )
    parts = sync_result.stdout.strip().split()
    if len(parts) != 2:
        return ReadinessFinding(
            "git_sync_health",
            True,
            "origin/main sync probe malformed; skipped",
        )
    try:
        behind, ahead = (int(parts[0]), int(parts[1]))
    except ValueError:
        return ReadinessFinding(
            "git_sync_health",
            True,
            "origin/main sync probe malformed; skipped",
        )
    ok = behind == 0
    detail = f"branch={branch or '-'} behind={behind} ahead={ahead}"
    if not ok:
        detail += "; fast-forward sync blocked"
    return ReadinessFinding("git_sync_health", ok, detail)


def _check_pipeline_gate_block_summary_notify(root: Path) -> ReadinessFinding:
    path = root / "scripts" / "daily_pipeline.py"
    if not path.exists():
        return ReadinessFinding(
            "pipeline_gate_block_summary_notify",
            True,
            "pipeline script unavailable; skipped",
        )
    text = path.read_text(encoding="utf-8")
    block = _cli_function_block(text, "_send_pipeline_digest")
    ok = (
        'gate_block_reason = ""' in block
        and "gate_block_summary_sent" in block
        and "收盘汇总通知降级" in block
        and "strategy_gate_not_confirmed" in block
        and "_gate_block_notification_already_recorded" in block
        and "gate_block_already_notified" in block
        and "notification_state =" in block
        and 'f"pipeline-summary:{run_date}:{notification_state}"' in block
    )
    main_block = _cli_function_block(text, "main")
    send_at = main_block.find("_send_pipeline_digest(config, result, logger)")
    write_at = main_block.find("_write_result_file(result, config.project_root)")
    writes_after_send = send_at >= 0 and write_at > send_at
    return ReadinessFinding(
        "pipeline_gate_block_summary_notify",
        ok and writes_after_send,
        "ok"
        if ok and writes_after_send
        else "daily_pipeline summary notify must send a blocked digest when strategy gate is not confirmed",
    )


def _check_cli_data_source_boundary(root: Path) -> ReadinessFinding:
    paths = (
        root / "src" / "aqsp" / "cli.py",
        root / "scripts" / "daily_pipeline.py",
    )
    existing = [path for path in paths if path.exists()]
    if not existing:
        return ReadinessFinding(
            "cli_data_source_boundary",
            True,
            "source tree unavailable; skipped",
        )
    blockers: list[str] = []
    for path in existing:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(root).as_posix()
        blockers.extend(
            f"{rel}:{token}" for token in CONCRETE_DATA_SOURCE_TOKENS if token in text
        )
    return ReadinessFinding(
        "cli_data_source_boundary",
        not blockers,
        "ok"
        if not blockers
        else "entrypoint bypasses data source factory: " + ", ".join(blockers[:5]),
    )


def _check_data_source_fail_closed_contract(root: Path) -> ReadinessFinding:
    data_dir = root / "src" / "aqsp" / "data"
    if not data_dir.exists():
        return ReadinessFinding(
            "data_source_fail_closed_contract",
            True,
            "source tree unavailable; skipped",
        )
    source_files = [
        data_dir / name
        for name in (
            "akshare_source.py",
            "baostock_source.py",
            "eastmoney_source.py",
            "efinance_source.py",
            "mootdx_source.py",
            "sina_source.py",
            "sqlite_db_source.py",
            "tdx_vipdoc_source.py",
            "tencent_source.py",
        )
    ]
    offenders: list[str] = []
    for path in source_files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if (
            "require_non_empty_fetch_result" not in text
            and "raise DataError" not in text
        ):
            offenders.append(f"{path.name}: no public completeness guard")
        if "if df is not None and not df.empty" in text:
            offenders.append(f"{path.name}: skips empty frame")
        if "if data:" in text and "fetch_realtime_quote" in text:
            offenders.append(f"{path.name}: skips empty quote")
    return ReadinessFinding(
        "data_source_fail_closed_contract",
        not offenders,
        "ok"
        if not offenders
        else "data sources must fail closed: " + "; ".join(offenders[:5]),
    )


def _check_walkforward_service_boundary(root: Path) -> ReadinessFinding:
    cli_path = root / "src" / "aqsp" / "cli.py"
    service_path = root / "src" / "aqsp" / "services" / "walkforward_data.py"
    if not cli_path.exists():
        return ReadinessFinding(
            "walkforward_service_boundary",
            True,
            "source tree unavailable; skipped",
        )
    cli_text = cli_path.read_text(encoding="utf-8")
    block = _cli_function_block(cli_text, "run_walkforward")
    forbidden = (
        'args.source == "mootdx"',
        'args.source == "sina"',
        'args.source == "baostock"',
        'args.source == "sqlite_db"',
        "elif args.source",
    )
    blockers = [token for token in forbidden if token in block]
    ok = (
        "fetch_walkforward_frames(" in block
        and "WalkforwardFetchRequest(" in block
        and service_path.exists()
        and not blockers
    )
    detail = "ok"
    if not ok:
        detail = (
            "run_walkforward must delegate source-specific fetching to "
            "services.walkforward_data"
        )
        if blockers:
            detail += ": " + ", ".join(blockers)
    return ReadinessFinding("walkforward_service_boundary", ok, detail)


def _check_business_layer_source_abstractions(root: Path) -> ReadinessFinding:
    path = root / "src" / "aqsp" / "news" / "catalysts.py"
    if not path.exists():
        return ReadinessFinding(
            "business_layer_source_abstractions",
            True,
            "source tree unavailable; skipped",
        )
    text = path.read_text(encoding="utf-8")
    forbidden = ("AkshareNewsSource", "akshare_source", "import akshare")
    blockers = [token for token in forbidden if token in text]
    return ReadinessFinding(
        "business_layer_source_abstractions",
        not blockers,
        "ok"
        if not blockers
        else "news/catalysts binds concrete source: " + ", ".join(blockers),
    )


def _check_backtest_no_global_quantile_leakage(root: Path) -> ReadinessFinding:
    offenders: list[str] = []
    factor_path = root / "src/aqsp/strategies/factor_backtest.py"
    if factor_path.exists():
        text = factor_path.read_text(encoding="utf-8")
        if "MultiIndex" not in text or "raise ValueError" not in text:
            offenders.append("src/aqsp/strategies/factor_backtest.py")
        if "pd.qcut" in text and ".groupby(quantile_labels).mean()" in text:
            offenders.append("src/aqsp/strategies/factor_backtest.py")
    mining_path = root / "src/aqsp/strategies/auto_factor_mining.py"
    if mining_path.exists():
        text = mining_path.read_text(encoding="utf-8")
        if "pd.qcut" in text and ".groupby(quantile_labels).mean()" in text:
            offenders.append("src/aqsp/strategies/auto_factor_mining.py")
    offenders = sorted(set(offenders))
    return ReadinessFinding(
        "backtest_no_global_quantile_leakage",
        not offenders,
        "ok"
        if not offenders
        else "global quantile return aggregation: " + ", ".join(offenders),
    )


def _check_notification_runtime_boundaries(root: Path) -> ReadinessFinding:
    offenders: list[str] = []
    for rel in ("src/aqsp/briefing/notifier.py",):
        path = root / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "notify_markdown(" in text and "dispatch_notification_once" not in text:
            offenders.append(rel)
    monitor_path = root / "src" / "aqsp" / "monitor" / "notifier.py"
    if monitor_path.exists():
        text = monitor_path.read_text(encoding="utf-8")
        if "notify_markdown(" in text or "notify_markdown_via_config" not in text:
            offenders.append("src/aqsp/monitor/notifier.py:unscoped channel routing")
    notifier_path = root / "src" / "aqsp" / "notifier.py"
    if notifier_path.exists():
        text = notifier_path.read_text(encoding="utf-8")
        if (
            'normalized_mode == "summary"' in text
            and "_full_senders()"
            in _cli_function_block(text, "notify_markdown_via_config")
            and "AQSP_NOTIFY_SUMMARY_FALLBACK_FULL" not in text
        ):
            offenders.append("src/aqsp/notifier.py:implicit summary fallback")
    runtime_path = root / "src" / "aqsp" / "notification_runtime.py"
    if runtime_path.exists():
        text = runtime_path.read_text(encoding="utf-8")
        fingerprint_start = text.find("def notification_fingerprint(")
        next_def = text.find("\ndef ", fingerprint_start + 1)
        fingerprint_block = (
            text[fingerprint_start:]
            if next_def < 0
            else text[fingerprint_start:next_def]
        )
        if (
            fingerprint_start < 0
            or "hashlib.sha256" in fingerprint_block
            or "markdown.strip()" in fingerprint_block
            or "content_hash" not in text
        ):
            offenders.append("src/aqsp/notification_runtime.py:unstable fingerprint")
    return ReadinessFinding(
        "notification_runtime_boundaries",
        not offenders,
        "ok"
        if not offenders
        else "direct notify without runtime dedupe: " + ", ".join(offenders),
    )


def _check_auto_evolution_proposal_only(root: Path) -> ReadinessFinding:
    path = root / "src" / "aqsp" / "strategies" / "auto_evolution.py"
    if not path.exists():
        return ReadinessFinding(
            "auto_evolution_proposal_only",
            True,
            "auto evolution source unavailable; skipped",
        )
    block = _cli_function_block(path.read_text(encoding="utf-8"), "_apply_evolution")
    ok = (
        "threshold_proposals.jsonl" in block
        and "status" in block
        and "proposal_only" in block
        and "write_text" not in block
        and "subn(" not in block
    )
    return ReadinessFinding(
        "auto_evolution_proposal_only",
        ok,
        "ok"
        if ok
        else "auto evolution must write proposals only; thresholds.yaml changes require walk-forward and manual review",
    )


def _check_scheduled_service_boundary(root: Path) -> ReadinessFinding:
    service_path = root / "src" / "aqsp" / "services" / "scheduled.py"
    cli_path = root / "src" / "aqsp" / "cli.py"
    if not service_path.exists() or not cli_path.exists():
        return ReadinessFinding(
            "scheduled_service_boundary",
            True,
            "source tree unavailable; skipped",
        )
    service_text = service_path.read_text(encoding="utf-8")
    cli_text = cli_path.read_text(encoding="utf-8")
    ok = (
        "def run_scheduled_service" in service_text
        and "scheduled.run_scheduled_service" in cli_text
    )
    return ReadinessFinding(
        "scheduled_service_boundary",
        ok,
        "ok" if ok else "run_scheduled must dispatch through services.scheduled",
    )


def _check_special_strategy_ledger_guards(root: Path) -> ReadinessFinding:
    cli_path = root / "src" / "aqsp" / "cli.py"
    ledger_path = root / "src" / "aqsp" / "ledger" / "special_signals.py"
    if not cli_path.exists() or not ledger_path.exists():
        return ReadinessFinding(
            "special_strategy_ledger_guards",
            True,
            "source tree unavailable; skipped",
        )
    cli_text = cli_path.read_text(encoding="utf-8")
    ledger_text = ledger_path.read_text(encoding="utf-8")
    helper = _cli_function_block(cli_text, "_special_strategy_ledger_write_allowed")
    morning_block = _cli_function_block(cli_text, "run_morning_breakout")
    closing_block = _cli_function_block(cli_text, "run_closing_premium")
    ok = (
        "with advisory_lock(path):" in ledger_text
        and "is_trading_day(today)" in helper
        and "assert_fresh_data(" in helper
        and 'workload="live_short"' in helper
        and "_special_strategy_ledger_write_allowed(" in morning_block
        and "_special_strategy_ledger_write_allowed(" in closing_block
        and "_fetch_special_strategy_frames(" in morning_block
        and "_fetch_special_strategy_frames(" in closing_block
        and "_special_strategy_runtime_ready(" in morning_block
        and "_special_strategy_runtime_ready(" in closing_block
    )
    return ReadinessFinding(
        "special_strategy_ledger_guards",
        ok,
        "ok"
        if ok
        else "special strategy ledger writes must be locked and gated by trading day, freshness, intraday merge, and runtime regime",
    )


def _check_server_monitor_exit_policy(root: Path) -> ReadinessFinding:
    path = root / "scripts" / "server_monitor.sh"
    if not path.exists():
        return ReadinessFinding(
            "server_monitor_exit_policy",
            True,
            "server monitor script unavailable; skipped",
        )
    text = path.read_text(encoding="utf-8")
    ok = "AQSP_MONITOR_EXIT_ON_ALERT" in text and "避免外层调度重复告警" in text
    return ReadinessFinding(
        "server_monitor_exit_policy",
        ok,
        "ok" if ok else "server monitor should swallow handled alert exits by default",
    )


def _check_notify_state_path(
    root: Path,
    *,
    gate: str,
    env_name: str,
    default_value: str,
) -> ReadinessFinding:
    env_path = root / ".env"
    value = _read_env_assignment(env_path, env_name) if env_path.exists() else ""
    if not value:
        value = default_value
    path = Path(value)
    ok = not path.is_absolute() or str(path).startswith(str(root))
    return ReadinessFinding(
        gate,
        ok,
        value if ok else f"unstable external path for {env_name}: {value}",
    )


def _read_env_assignment(env_path: Path, env_name: str) -> str:
    # Immutable release tasks export private runtime paths explicitly; those
    # must take precedence over an optional release-local .env file.
    process_value = os.environ.get(env_name)
    if process_value is not None:
        return process_value.strip().strip('"').strip("'")
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{env_name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _check_runtime_outputs(root: Path) -> list[ReadinessFinding]:
    required = (
        ("latest_report", root / "reports" / "latest.md"),
        ("briefing_report", root / "reports" / "briefing.md"),
        ("closing_review", root / "reports" / "closing_review.md"),
        ("dashboard_html", root / "dist" / "dashboard" / "index.html"),
    )
    return [
        ReadinessFinding(name, _file_has_content(path), str(path))
        for name, path in required
    ]


def _print_findings(findings: list[ReadinessFinding]) -> None:
    ready = all(finding.ok for finding in findings)
    print("BEFORE_LIVE_STATUS=" + ("PASS" if ready else "BLOCK"))
    for finding in findings:
        status = "PASS" if finding.ok else "BLOCK"
        print(f"[{status}] {finding.gate}: {finding.detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="AQSP runtime/project root")
    parser.add_argument("--today", default="", help="Override today as YYYY-MM-DD")
    parser.add_argument("--gate", default="", help="Override walkforward gate path")
    parser.add_argument("--ledger", default="", help="Override predictions ledger path")
    parser.add_argument(
        "--paper-ledger", default="", help="Override paper trades ledger path"
    )
    parser.add_argument(
        "--run-history", default="", help="Override daily run history jsonl"
    )
    parser.add_argument("--cron", default="", help="Optional crontab dump to audit")
    parser.add_argument(
        "--cron-dir",
        default="/www/server/cron",
        help="Optional BT Panel cron wrapper directory to audit",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    today = _parse_date(args.today) or today_shanghai()
    findings = check_before_live(
        root=root,
        today=today,
        gate_path=Path(args.gate) if args.gate else None,
        ledger_path=Path(args.ledger) if args.ledger else None,
        paper_ledger_path=Path(args.paper_ledger) if args.paper_ledger else None,
        run_history_path=Path(args.run_history) if args.run_history else None,
        cron_path=Path(args.cron) if args.cron else None,
        cron_dir=Path(args.cron_dir) if args.cron_dir else None,
    )
    _print_findings(findings)
    return 0 if all(finding.ok for finding in findings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
