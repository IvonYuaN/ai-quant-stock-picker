#!/usr/bin/env python3
"""Run the production short-line walk-forward gate.

This wrapper is intentionally stricter than ad-hoc `aqsp walkforward` calls:
it requires a raw sqlite database with full-market coverage before it starts the
expensive gate run. A 300-symbol run is only a smoke test and must not be used as
before-live evidence.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from bisect import bisect_left
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
import re

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aqsp.core.time import get_previous_trading_day, now_shanghai, today_shanghai
from aqsp.data.sqlite_db_source import SqliteDbSource
from aqsp.utils.jsonl_io import atomic_write_text
from aqsp.walkforward_gate import MIN_PRODUCTION_GATE_SYMBOLS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DB = Path("/opt/market-data/astocks_raw.db")
MIN_COVERAGE_RATIO = 0.8
DEFAULT_STATUS_PATH = "data/walkforward_production_status.json"
DEFAULT_SYMBOL_CACHE_PATH = "data/walkforward_production_symbols.json"
DEFAULT_LOCK_PATH = ".locks/walkforward-production.lock"
DEFAULT_COVERAGE_MODE = "auto_recent_window"
DEFAULT_LOOKBACK_YEARS = 3
PRODUCTION_TIMEOUT_FLOOR_SECONDS = 7200
PRODUCTION_TIMEOUT_SECONDS_PER_SYMBOL = 2
MIN_PRODUCTION_MEMORY_GIB = 4.0
DEFAULT_STREAM_BATCH_SIZE = 200


@dataclass(frozen=True)
class CoverageSummary:
    stock_symbols: int
    covered_symbols: int
    rows: int
    first_trade_date: str
    last_trade_date: str
    coverage_mode: str = "legacy_full_span"
    coverage_window_start: str = ""
    coverage_window_end: str = ""
    lookback_years: int | None = None
    listing_aware: bool = False
    expected_trade_days: int = 0


@dataclass(frozen=True)
class CoverageInspection:
    summary: CoverageSummary
    covered_symbols: list[str]


def _coverage_payload_from_summary(coverage: CoverageSummary) -> dict[str, object]:
    return {
        "stock_symbols": coverage.stock_symbols,
        "covered_symbols": coverage.covered_symbols,
        "rows": coverage.rows,
        "first_trade_date": coverage.first_trade_date,
        "last_trade_date": coverage.last_trade_date,
        "coverage_mode": coverage.coverage_mode,
        "coverage_window_start": coverage.coverage_window_start,
        "coverage_window_end": coverage.coverage_window_end,
        "lookback_years": coverage.lookback_years,
        "listing_aware": coverage.listing_aware,
        "expected_trade_days": coverage.expected_trade_days,
    }


def _merge_diagnostic_coverage_payload(
    raw_payload: object,
    coverage: CoverageSummary | None,
) -> dict[str, object] | None:
    payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
    if coverage is None:
        return payload or None
    summary_payload = _coverage_payload_from_summary(coverage)
    payload_covered = payload.get("covered_symbols")
    if isinstance(payload_covered, int) and payload_covered > 0:
        for key, value in summary_payload.items():
            if payload.get(key) in {"", None, "-"}:
                payload[key] = value
        return payload
    payload.update(summary_payload)
    return payload


def _status_path(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _symbol_cache_path(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _write_status(
    path: Path,
    *,
    status: str,
    args: argparse.Namespace,
    coverage: CoverageSummary | None = None,
    effective_symbols: int | None = None,
    command: list[str] | None = None,
    child_exit_code: int | None = None,
    child_pid: int | None = None,
    detail: str = "",
) -> None:
    payload: dict[str, object] = {
        "status": status,
        "updated_at": now_shanghai().isoformat(timespec="seconds"),
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "db_path": str(args.db),
        "start": args.start,
        "end": args.end,
        "grid_profile": args.grid_profile,
        "report_path": str(args.report),
        "gate_path": str(args.gate_path),
        "log_path": str(args.log),
        "timeout_seconds": args.timeout_seconds,
        "memory_mode": "full_materialization"
        if bool(getattr(args, "no_streaming", False))
        else "streaming",
        "stream_batch_size": int(
            getattr(args, "stream_batch_size", DEFAULT_STREAM_BATCH_SIZE)
        ),
    }
    if detail:
        payload["detail"] = detail
    if coverage is not None:
        payload["coverage_mode"] = coverage.coverage_mode
        payload["coverage_window"] = {
            "start": coverage.coverage_window_start,
            "end": coverage.coverage_window_end,
            "lookback_years": coverage.lookback_years,
            "listing_aware": coverage.listing_aware,
            "expected_trade_days": coverage.expected_trade_days,
        }
        payload["coverage"] = {
            "stock_symbols": coverage.stock_symbols,
            "covered_symbols": coverage.covered_symbols,
            "rows": coverage.rows,
            "first_trade_date": coverage.first_trade_date,
            "last_trade_date": coverage.last_trade_date,
            "coverage_mode": coverage.coverage_mode,
            "coverage_window_start": coverage.coverage_window_start,
            "coverage_window_end": coverage.coverage_window_end,
            "lookback_years": coverage.lookback_years,
            "listing_aware": coverage.listing_aware,
            "expected_trade_days": coverage.expected_trade_days,
        }
    if effective_symbols is not None:
        payload["effective_symbols"] = effective_symbols
    if command is not None:
        payload["command"] = command
    if child_exit_code is not None:
        payload["child_exit_code"] = child_exit_code
    if child_pid is not None:
        payload["child_pid"] = child_pid
    peak_rss_kib = getattr(args, "_peak_rss_kib", None)
    if isinstance(peak_rss_kib, int) and peak_rss_kib > 0:
        payload["child_peak_rss_kib"] = peak_rss_kib
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _write_preflight_status(
    path: Path,
    *,
    args: argparse.Namespace,
    status: str,
    detail: str,
) -> None:
    _write_status(
        path,
        status=status,
        args=args,
        detail=detail,
    )


def _effective_timeout_seconds(
    requested_timeout_seconds: int,
    *,
    effective_symbols: int,
    min_production_symbols: int,
) -> int:
    requested = int(requested_timeout_seconds or 0)
    if requested <= 0 or effective_symbols < min_production_symbols:
        return requested
    scaled_floor = max(
        PRODUCTION_TIMEOUT_FLOOR_SECONDS,
        int(effective_symbols) * PRODUCTION_TIMEOUT_SECONDS_PER_SYMBOL,
    )
    return max(requested, scaled_floor)


def _timeout_guard_detail(
    requested_timeout_seconds: int,
    effective_timeout_seconds: int,
) -> str:
    if (
        requested_timeout_seconds > 0
        and effective_timeout_seconds > requested_timeout_seconds
    ):
        return (
            f"timeout auto-raised from {requested_timeout_seconds}s "
            f"to {effective_timeout_seconds}s for production coverage"
        )
    return ""


def _total_memory_gib() -> float | None:
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            for line in meminfo.read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) / 1024 / 1024
        except (OSError, ValueError):
            pass

    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        if pages > 0 and page_size > 0:
            return pages * page_size / 1024**3
    except (AttributeError, OSError, ValueError):
        pass

    if os.name == "nt":
        try:

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.ullTotalPhys / 1024**3
        except (AttributeError, OSError, TypeError):
            pass
    return None


def _low_memory_blocker(min_memory_gib: float) -> str:
    total_gib = _total_memory_gib()
    if total_gib is None:
        return (
            "server total memory could not be detected; refusing to start production "
            "walk-forward (fail-closed). Run on a host with readable memory metadata"
        )
    if total_gib >= float(min_memory_gib):
        return ""
    return (
        f"server memory {total_gib:.1f}GiB < required {float(min_memory_gib):.1f}GiB; "
        "run production walk-forward on a larger host or use the bounded streaming workflow"
    )


def _execute_child_walkforward(
    *,
    command: list[str],
    env: dict[str, str],
    cwd: Path,
    timeout_seconds: int,
    status_path: Path,
    args: argparse.Namespace,
    coverage: CoverageSummary,
    effective_symbols: int,
) -> tuple[int, int | None]:
    heartbeat_seconds = max(15, int(getattr(args, "heartbeat_seconds", 60) or 60))
    process = subprocess.Popen(
        command,
        env=env,
        cwd=cwd,
        start_new_session=True,
    )
    child_pid = process.pid
    args._peak_rss_kib = 0
    started_monotonic = time.monotonic()
    _write_status(
        status_path,
        status="running",
        args=args,
        coverage=coverage,
        effective_symbols=effective_symbols,
        command=command,
        child_pid=child_pid,
        detail="child walkforward started",
    )
    while True:
        elapsed_seconds = int(max(0, time.monotonic() - started_monotonic))
        remaining_seconds: float | None = None
        if timeout_seconds > 0:
            remaining_seconds = max(0.0, timeout_seconds - elapsed_seconds)
            if remaining_seconds <= 0:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise subprocess.TimeoutExpired(cmd=command, timeout=timeout_seconds)
        wait_timeout = (
            heartbeat_seconds
            if remaining_seconds is None
            else min(float(heartbeat_seconds), remaining_seconds)
        )
        try:
            exit_code = process.wait(timeout=max(0.1, wait_timeout))
            peak = _process_peak_rss_kib(child_pid)
            if peak is not None:
                args._peak_rss_kib = max(int(args._peak_rss_kib), peak)
            return exit_code, child_pid
        except subprocess.TimeoutExpired:
            peak = _process_peak_rss_kib(child_pid)
            if peak is not None:
                args._peak_rss_kib = max(int(args._peak_rss_kib), peak)
            _write_status(
                status_path,
                status="running",
                args=args,
                coverage=coverage,
                effective_symbols=effective_symbols,
                command=command,
                child_pid=child_pid,
                detail=f"child walkforward running; elapsed={elapsed_seconds}s",
            )


def _read_status(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pid_active(pid_value: object) -> bool:
    if isinstance(pid_value, bool) or not isinstance(pid_value, int) or pid_value <= 0:
        return False
    try:
        os.kill(pid_value, 0)
    except OSError:
        return False
    return True


def _pid_cmdline(pid_value: object) -> tuple[str, ...]:
    if isinstance(pid_value, bool) or not isinstance(pid_value, int) or pid_value <= 0:
        return ()
    cmdline_path = Path("/proc") / str(pid_value) / "cmdline"
    try:
        raw = cmdline_path.read_bytes()
    except OSError:
        return ()
    return tuple(
        part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part
    )


def _process_peak_rss_kib(pid_value: int | None) -> int | None:
    if not isinstance(pid_value, int) or pid_value <= 0:
        return None
    try:
        status_text = Path(f"/proc/{pid_value}/status").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in status_text.splitlines():
        if line.startswith("VmHWM:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return None


def _production_wrapper_cmdline_matches(cmdline: tuple[str, ...]) -> bool:
    joined = " ".join(cmdline)
    if "--dry-run" in cmdline or "--repair-only" in cmdline:
        return False
    return "scripts/run_production_walkforward_gate.py" in joined


def _production_child_cmdline_matches(cmdline: tuple[str, ...]) -> bool:
    joined = " ".join(cmdline)
    required = (
        "-m aqsp walkforward",
        "--source sqlite_db",
        "--pool all",
        "--grid-cscv",
        "--symbols-file",
    )
    return all(needle in joined for needle in required)


def _active_running_production_detail(payload: dict[str, object]) -> str:
    if str(payload.get("status") or "").strip() not in {"running", "blocked_running"}:
        return ""
    child_pid = payload.get("child_pid")
    if _pid_active(child_pid) and _production_child_cmdline_matches(
        _pid_cmdline(child_pid)
    ):
        return f"active production walk-forward child already running: pid={child_pid}"
    pid = payload.get("pid")
    if _pid_active(pid) and _production_wrapper_cmdline_matches(_pid_cmdline(pid)):
        return f"active production walk-forward wrapper already running: pid={pid}"
    return ""


def _write_blocked_running_status(
    path: Path,
    *,
    payload: dict[str, object],
    detail: str,
) -> None:
    blocked_payload = dict(payload)
    blocked_payload["status"] = "blocked_running"
    blocked_payload["detail"] = detail
    blocked_payload["blocked_by_pid"] = os.getpid()
    blocked_payload["updated_at"] = now_shanghai().isoformat(timespec="seconds")
    atomic_write_text(
        path, json.dumps(blocked_payload, indent=2, ensure_ascii=False) + "\n"
    )


def _lock_path(raw_path: str | Path) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _lock_meta_path(lock_path: Path) -> Path:
    return lock_path / "meta.json"


def _write_lock_meta(lock_path: Path) -> None:
    payload = {
        "pid": os.getpid(),
        "cmdline": sys.argv,
        "started_at": now_shanghai().isoformat(timespec="seconds"),
    }
    atomic_write_text(
        _lock_meta_path(lock_path),
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )


def _read_lock_meta(lock_path: Path) -> dict[str, object]:
    meta_path = _lock_meta_path(lock_path)
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _production_lock_active_detail(lock_path: Path) -> str:
    payload = _read_lock_meta(lock_path)
    pid = payload.get("pid")
    if _pid_active(pid) and _production_wrapper_cmdline_matches(_pid_cmdline(pid)):
        return f"active production walk-forward lock already held: pid={pid}"
    return ""


def _acquire_production_lock(lock_path: Path) -> tuple[bool, str]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_path.mkdir()
    except FileExistsError:
        active_detail = _production_lock_active_detail(lock_path)
        if active_detail:
            return False, active_detail
        try:
            _lock_meta_path(lock_path).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return False, f"production walk-forward lock exists: {lock_path}"
        try:
            lock_path.rmdir()
        except OSError:
            return False, f"production walk-forward lock exists: {lock_path}"
        try:
            lock_path.mkdir()
        except FileExistsError:
            return False, f"production walk-forward lock exists: {lock_path}"
    _write_lock_meta(lock_path)
    return True, ""


def _release_production_lock(lock_path: Path) -> None:
    try:
        payload = _read_lock_meta(lock_path)
        if payload.get("pid") != os.getpid():
            return
        _lock_meta_path(lock_path).unlink(missing_ok=True)
        lock_path.rmdir()
    except OSError:
        return


def _status_updated_at(payload: dict[str, object]) -> datetime | None:
    raw = str(payload.get("updated_at") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _running_status_is_stale(payload: dict[str, object]) -> bool:
    if str(payload.get("status") or "").strip() != "running":
        return False
    child_pid = payload.get("child_pid")
    pid = payload.get("pid")
    child_active = _pid_active(child_pid)
    parent_active = _pid_active(pid)
    if isinstance(child_pid, int) and child_pid > 0:
        if child_active:
            return not _production_child_cmdline_matches(_pid_cmdline(child_pid))
        return True
    updated_at = _status_updated_at(payload)
    timeout_seconds = payload.get("timeout_seconds")
    timeout_value = (
        int(timeout_seconds)
        if isinstance(timeout_seconds, int) and timeout_seconds > 0
        else 0
    )
    if updated_at is not None and timeout_value > 0:
        age_seconds = (now_shanghai() - updated_at).total_seconds()
        if age_seconds > timeout_value:
            return True
    if isinstance(pid, int) and pid > 0:
        if parent_active:
            return not _production_wrapper_cmdline_matches(_pid_cmdline(pid))
        return not parent_active
    return True


def repair_stale_running_status(
    path: Path,
    *,
    args: argparse.Namespace | None = None,
) -> bool:
    payload = _read_status(path)
    if not _running_status_is_stale(payload):
        return False

    payload["status"] = "timeout"
    payload["child_exit_code"] = (
        int(payload["child_exit_code"])
        if isinstance(payload.get("child_exit_code"), int)
        else 124
    )
    detail = str(payload.get("detail") or "").strip()
    stale_detail = "stale running status auto-repaired"
    payload["detail"] = (
        f"{detail}; {stale_detail}"
        if detail and stale_detail not in detail
        else stale_detail
    )
    payload["updated_at"] = now_shanghai().isoformat(timespec="seconds")
    if args is not None:
        payload.setdefault("db_path", str(args.db))
        payload.setdefault("start", args.start)
        payload.setdefault("end", args.end)
        payload.setdefault("grid_profile", args.grid_profile)
        payload.setdefault("report_path", str(args.report))
        payload.setdefault("gate_path", str(args.gate_path))
        payload.setdefault("log_path", str(args.log))
        payload.setdefault("timeout_seconds", args.timeout_seconds)
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return True


def _compact_day(raw: str) -> str:
    return date.fromisoformat(raw).strftime("%Y%m%d")


def _parse_db_day(raw: str) -> date | None:
    text = str(raw or "").strip()
    if len(text) == 8 and text.isdigit():
        return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _shift_years(raw: date, years: int) -> date:
    try:
        return raw.replace(year=raw.year - years)
    except ValueError:
        return raw.replace(month=2, day=28, year=raw.year - years)


def _default_production_end() -> str:
    return get_previous_trading_day(today_shanghai()).isoformat()


def _default_production_start(*, end: str, lookback_years: int) -> str:
    end_day = date.fromisoformat(end)
    return (
        _shift_years(end_day, max(int(lookback_years), 1)) + timedelta(days=1)
    ).isoformat()


def _normalize_coverage_mode(raw: str) -> str:
    normalized = str(raw or "").strip().lower().replace("-", "_")
    aliases = {
        "auto_recent": "auto_recent_window",
        "auto_recent_window": "auto_recent_window",
        "rolling": "auto_recent_window",
        "recent": "auto_recent_window",
        "legacy": "legacy_full_span",
        "legacy_full_span": "legacy_full_span",
        "full_span": "legacy_full_span",
    }
    if normalized not in aliases:
        raise SystemExit(
            "unsupported coverage mode: "
            f"{raw!r}; expected one of auto_recent_window/full_span/legacy"
        )
    return aliases[normalized]


def _resolve_coverage_window(
    *,
    start: str,
    end: str,
    coverage_mode: str,
    lookback_years: int,
) -> tuple[str, str, bool]:
    normalized_mode = _normalize_coverage_mode(coverage_mode)
    start_day = date.fromisoformat(start)
    end_day = date.fromisoformat(end)
    if normalized_mode == "legacy_full_span":
        return start, end, False
    if lookback_years <= 0:
        raise SystemExit(f"lookback-years must be >= 1, got {lookback_years}")
    window_start_day = _shift_years(end_day, lookback_years) + timedelta(days=1)
    if window_start_day < start_day:
        window_start_day = start_day
    return window_start_day.isoformat(), end, True


def _align_inspection_metadata(
    inspection: CoverageInspection,
    *,
    start: str,
    end: str,
    coverage_mode: str,
    lookback_years: int,
) -> CoverageInspection:
    coverage_start, coverage_end, listing_aware = _resolve_coverage_window(
        start=start,
        end=end,
        coverage_mode=coverage_mode,
        lookback_years=lookback_years,
    )
    summary = inspection.summary
    normalized_mode = _normalize_coverage_mode(coverage_mode)
    if (
        summary.coverage_mode == normalized_mode
        and summary.coverage_window_start
        and summary.coverage_window_end
    ):
        return inspection
    return CoverageInspection(
        summary=replace(
            summary,
            coverage_mode=normalized_mode,
            coverage_window_start=coverage_start,
            coverage_window_end=coverage_end,
            lookback_years=lookback_years if listing_aware else None,
            listing_aware=listing_aware,
            expected_trade_days=summary.expected_trade_days,
        ),
        covered_symbols=inspection.covered_symbols,
    )


def _date_within_lag(left: str, right: str, *, max_days: int) -> bool:
    left_day = _parse_db_day(left)
    right_day = _parse_db_day(right)
    if left_day is None or right_day is None:
        return False
    delta = (right_day - left_day).days
    return 0 <= delta <= max_days


def _symbol_from_ts_code(ts_code: object) -> str:
    text = str(ts_code).strip()
    return text.split(".", 1)[0] if "." in text else text


def _raw_sqlite_source(db_path: Path) -> SqliteDbSource:
    if not db_path.exists():
        raise SystemExit(f"raw sqlite db missing: {db_path}")
    source = SqliteDbSource(db_path=db_path, cache=None)
    price_mode = source.price_mode()
    if price_mode != "raw":
        raise SystemExit(
            f"production gate requires raw sqlite db, got price_mode={price_mode}: {db_path}"
        )
    return source


def select_covered_symbols(
    db_path: Path,
    *,
    start: str,
    end: str,
    coverage_mode: str = DEFAULT_COVERAGE_MODE,
    lookback_years: int = DEFAULT_LOOKBACK_YEARS,
) -> list[str]:
    return inspect_raw_coverage_with_symbols(
        db_path,
        start=start,
        end=end,
        coverage_mode=coverage_mode,
        lookback_years=lookback_years,
    ).covered_symbols


def inspect_raw_coverage(db_path: Path, *, start: str, end: str) -> CoverageSummary:
    return inspect_raw_coverage_with_symbols(db_path, start=start, end=end).summary


def inspect_raw_coverage_with_symbols(
    db_path: Path,
    *,
    start: str,
    end: str,
    coverage_mode: str = DEFAULT_COVERAGE_MODE,
    lookback_years: int = DEFAULT_LOOKBACK_YEARS,
) -> CoverageInspection:
    _raw_sqlite_source(db_path)
    coverage_start, coverage_end, listing_aware = _resolve_coverage_window(
        start=start,
        end=end,
        coverage_mode=coverage_mode,
        lookback_years=lookback_years,
    )
    return inspect_raw_coverage_window_with_symbols(
        db_path,
        requested_start=start,
        requested_end=end,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        coverage_mode=coverage_mode,
        lookback_years=lookback_years,
        listing_aware=listing_aware,
    )


def inspect_raw_coverage_window_with_symbols(
    db_path: Path,
    *,
    requested_start: str,
    requested_end: str,
    coverage_start: str,
    coverage_end: str,
    coverage_mode: str,
    lookback_years: int,
    listing_aware: bool,
) -> CoverageInspection:
    _raw_sqlite_source(db_path)
    start_str = _compact_day(coverage_start)
    end_str = _compact_day(coverage_end)
    with sqlite3.connect(db_path) as conn:
        stock_rows = conn.execute(
            "SELECT ts_code FROM stocks ORDER BY ts_code"
        ).fetchall()
        market_days = [
            str(raw_day[0] or "")
            for raw_day in conn.execute(
                """
                SELECT DISTINCT trade_date
                FROM daily_qfq
                WHERE trade_date >= ? AND trade_date <= ?
                ORDER BY trade_date
                """,
                (start_str, end_str),
            ).fetchall()
            if str(raw_day[0] or "")
        ]
        row = conn.execute(
            """
            SELECT COUNT(*), MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date)
            FROM daily_qfq
            WHERE trade_date >= ? AND trade_date <= ?
            """,
            (start_str, end_str),
        ).fetchone()
        first_market_day = str(row[1] or "")
        last_market_day = str(row[2] or "")
        expected_rows = int(row[3] or 0)
        coverage_rows = conn.execute(
            """
            SELECT
                ts_code,
                MIN(trade_date) AS first_seen_any,
                MIN(CASE WHEN trade_date >= ? AND trade_date <= ? THEN trade_date END) AS first_seen_window,
                MAX(CASE WHEN trade_date >= ? AND trade_date <= ? THEN trade_date END) AS last_seen_window,
                SUM(CASE WHEN trade_date >= ? AND trade_date <= ? THEN 1 ELSE 0 END) AS rows_window
            FROM daily_qfq
            GROUP BY ts_code
            HAVING rows_window > 0
            ORDER BY ts_code
            """,
            (start_str, end_str, start_str, end_str, start_str, end_str),
        ).fetchall()

    covered: list[str] = []
    if first_market_day and last_market_day and market_days:
        market_end_index = len(market_days) - 1
        for (
            ts_code,
            first_any,
            first_window,
            last_window,
            count_window,
        ) in coverage_rows:
            first_any_text = str(first_any or "")
            first_window_text = str(first_window or "")
            last_window_text = str(last_window or "")
            if not first_window_text or not last_window_text:
                continue
            eligible_start = first_market_day
            if listing_aware and first_any_text and first_any_text > first_market_day:
                eligible_start = first_any_text
            if not _date_within_lag(eligible_start, first_window_text, max_days=10):
                continue
            if not _date_within_lag(last_window_text, last_market_day, max_days=10):
                continue
            eligible_index = bisect_left(market_days, eligible_start)
            eligible_trade_days = market_end_index - eligible_index + 1
            min_required_rows = max(1, int(eligible_trade_days * MIN_COVERAGE_RATIO))
            if int(count_window or 0) < min_required_rows:
                continue
            covered.append(_symbol_from_ts_code(ts_code))

    return CoverageInspection(
        summary=CoverageSummary(
            stock_symbols=len(stock_rows),
            covered_symbols=len(covered),
            rows=int(row[0] or 0),
            first_trade_date=first_market_day,
            last_trade_date=last_market_day,
            coverage_mode=_normalize_coverage_mode(coverage_mode),
            coverage_window_start=coverage_start,
            coverage_window_end=coverage_end,
            lookback_years=lookback_years if listing_aware else None,
            listing_aware=listing_aware,
            expected_trade_days=expected_rows,
        ),
        covered_symbols=covered,
    )


def _db_mtime_epoch(db_path: Path) -> int:
    return int(db_path.stat().st_mtime)


def _canonical_path(path: Path) -> str:
    return str(path.expanduser().resolve(strict=False))


def _raw_sqlite_max_trade_date(db_path: Path) -> str | None:
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT MAX(trade_date) FROM daily_qfq").fetchone()
    except sqlite3.Error:
        return None
    raw_date = str(row[0] or "").strip() if row else ""
    parsed = _parse_db_day(raw_date)
    return parsed.isoformat() if parsed is not None else None


def _raw_sqlite_has_daily_table(db_path: Path) -> bool:
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_qfq'"
            ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _read_json_object(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def validate_production_cutoff_consistency(
    *,
    db_path: Path,
    requested_end: str,
    gate_path: Path,
    raw_max_trade_date: str | None = None,
    coverage_last_trade_date: str | None = None,
) -> str:
    """Reject evidence that claims a date beyond the raw database cutoff."""
    raw_max = raw_max_trade_date or _raw_sqlite_max_trade_date(db_path)
    if raw_max is None:
        return "raw sqlite MAX(trade_date) is missing/invalid; refusing production evidence"
    raw_max_day = _parse_db_day(raw_max)
    requested_day = _parse_db_day(requested_end)
    if raw_max_day is None or requested_day is None:
        return (
            "raw sqlite cutoff or requested end is malformed: "
            f"raw_max={raw_max!r} requested_end={requested_end!r}"
        )
    if requested_day > raw_max_day:
        return (
            f"requested end={requested_day.isoformat()} exceeds raw sqlite "
            f"MAX(trade_date)={raw_max_day.isoformat()}"
        )

    if coverage_last_trade_date:
        coverage_day = _parse_db_day(coverage_last_trade_date)
        if coverage_day is None or coverage_day > raw_max_day:
            return (
                "coverage last_trade_date exceeds raw sqlite cutoff: "
                f"coverage={coverage_last_trade_date!r} "
                f"raw_max={raw_max_day.isoformat()}"
            )

    if not gate_path.exists():
        return ""
    payload = _read_json_object(gate_path)
    if payload is None:
        return f"production gate sidecar is missing/invalid: {gate_path}"

    sidecar_db = str(payload.get("sqlite_db_path") or "").strip()
    if sidecar_db and _canonical_path(Path(sidecar_db)) != _canonical_path(db_path):
        return (
            "production gate sidecar sqlite_db_path mismatch: "
            f"sidecar={sidecar_db!r} requested={_canonical_path(db_path)!r}"
        )

    raw_sidecar_end = payload.get("data_end")
    if raw_sidecar_end not in (None, ""):
        sidecar_day = _parse_db_day(str(raw_sidecar_end))
        if sidecar_day is None:
            return f"production gate sidecar data_end malformed: {raw_sidecar_end!r}"
        if sidecar_day != requested_day:
            return (
                "production gate sidecar cutoff mismatch: "
                f"data_end={sidecar_day.isoformat()} "
                f"requested_end={requested_day.isoformat()}"
            )
        if sidecar_day > raw_max_day:
            return (
                "production gate sidecar data_end exceeds raw sqlite cutoff: "
                f"data_end={sidecar_day.isoformat()} "
                f"raw_max={raw_max_day.isoformat()}"
            )

    coverage_payload = payload.get("production_gate_coverage")
    if isinstance(coverage_payload, dict):
        sidecar_last = coverage_payload.get("last_trade_date")
        if sidecar_last:
            sidecar_last_day = _parse_db_day(str(sidecar_last))
            if sidecar_last_day is None or sidecar_last_day > raw_max_day:
                return (
                    "production gate coverage last_trade_date exceeds raw sqlite cutoff: "
                    f"coverage={sidecar_last!r} raw_max={raw_max_day.isoformat()}"
                )
    return ""


def load_cached_coverage_symbols(
    cache_path: Path,
    *,
    db_path: Path,
    start: str,
    end: str,
    min_symbols: int,
    coverage_mode: str,
    lookback_years: int,
    raw_max_trade_date: str | None = None,
) -> CoverageInspection | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    expected = {
        "cache_path": _canonical_path(cache_path),
        "db_path": _canonical_path(db_path),
        "db_mtime_epoch": _db_mtime_epoch(db_path),
        "start": start,
        "end": end,
        "min_symbols": int(min_symbols),
        "coverage_mode": _normalize_coverage_mode(coverage_mode),
        "lookback_years": int(lookback_years),
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            return None
    if (
        raw_max_trade_date is not None
        and payload.get("raw_max_trade_date") != raw_max_trade_date
    ):
        return None
    summary_raw = payload.get("summary")
    covered_symbols = payload.get("covered_symbols")
    if not isinstance(summary_raw, dict) or not isinstance(covered_symbols, list):
        return None
    try:
        summary = CoverageSummary(
            stock_symbols=int(summary_raw["stock_symbols"]),
            covered_symbols=int(summary_raw["covered_symbols"]),
            rows=int(summary_raw["rows"]),
            first_trade_date=str(summary_raw["first_trade_date"]),
            last_trade_date=str(summary_raw["last_trade_date"]),
            coverage_mode=str(
                summary_raw.get("coverage_mode")
                or payload.get("coverage_mode")
                or "legacy_full_span"
            ),
            coverage_window_start=str(summary_raw.get("coverage_window_start") or ""),
            coverage_window_end=str(summary_raw.get("coverage_window_end") or ""),
            lookback_years=(
                int(summary_raw["lookback_years"])
                if summary_raw.get("lookback_years") is not None
                else None
            ),
            listing_aware=bool(summary_raw.get("listing_aware", False)),
            expected_trade_days=int(summary_raw.get("expected_trade_days") or 0),
        )
    except (KeyError, TypeError, ValueError):
        return None
    normalized_symbols = [
        str(symbol).strip() for symbol in covered_symbols if str(symbol).strip()
    ]
    if len(normalized_symbols) != summary.covered_symbols:
        return None
    return CoverageInspection(summary=summary, covered_symbols=normalized_symbols)


def write_cached_coverage_symbols(
    cache_path: Path,
    *,
    db_path: Path,
    start: str,
    end: str,
    min_symbols: int,
    inspection: CoverageInspection,
) -> None:
    payload = {
        "cache_path": _canonical_path(cache_path),
        "db_path": _canonical_path(db_path),
        "db_mtime_epoch": _db_mtime_epoch(db_path),
        "start": start,
        "end": end,
        "min_symbols": int(min_symbols),
        "coverage_mode": inspection.summary.coverage_mode,
        "lookback_years": inspection.summary.lookback_years or 0,
        "coverage_window": {
            "start": inspection.summary.coverage_window_start,
            "end": inspection.summary.coverage_window_end,
            "listing_aware": inspection.summary.listing_aware,
            "expected_trade_days": inspection.summary.expected_trade_days,
        },
        "updated_at": now_shanghai().isoformat(timespec="seconds"),
        "raw_max_trade_date": _raw_sqlite_max_trade_date(db_path),
        "summary": {
            "stock_symbols": inspection.summary.stock_symbols,
            "covered_symbols": inspection.summary.covered_symbols,
            "rows": inspection.summary.rows,
            "first_trade_date": inspection.summary.first_trade_date,
            "last_trade_date": inspection.summary.last_trade_date,
            "coverage_mode": inspection.summary.coverage_mode,
            "coverage_window_start": inspection.summary.coverage_window_start,
            "coverage_window_end": inspection.summary.coverage_window_end,
            "lookback_years": inspection.summary.lookback_years,
            "listing_aware": inspection.summary.listing_aware,
            "expected_trade_days": inspection.summary.expected_trade_days,
        },
        "covered_symbols": inspection.covered_symbols,
    }
    atomic_write_text(
        cache_path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )


def build_walkforward_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "aqsp",
        "walkforward",
        "--source",
        "sqlite_db",
        "--pool",
        "all",
        "--start",
        args.start,
        "--end",
        args.end,
        "--window-mode",
        "rolling_recent",
        "--grid-cscv",
        "--grid-profile",
        args.grid_profile,
        "--skip-pit-financials",
        "--allow-heldout",
        *(
            ["--symbols-file", args.symbols_file]
            if getattr(args, "symbols_file", "")
            else []
        ),
        "--report",
        args.report,
        "--gate-path",
        args.gate_path,
        "--cache-path",
        args.cache_path,
        "--log",
        args.log,
    ]
    if not bool(getattr(args, "no_streaming", False)):
        command[command.index("--skip-pit-financials") : command.index("--skip-pit-financials")] = [
            "--engine",
            "builtin",
            "--streaming",
            "--stream-batch-size",
            str(getattr(args, "stream_batch_size", DEFAULT_STREAM_BATCH_SIZE)),
        ]
    return command


def annotate_production_gate_metadata(
    *,
    gate_path: Path,
    db_path: Path,
    coverage: CoverageSummary,
    effective_symbols: int,
) -> int:
    """Stamp production coverage evidence onto the child gate sidecar."""
    if not gate_path.exists():
        raise SystemExit(
            f"production gate sidecar missing after child run: {gate_path}"
        )
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"production gate sidecar is not an object: {gate_path}")
    child_effective_symbols = payload.get("effective_symbols")
    if int(effective_symbols) >= MIN_PRODUCTION_GATE_SYMBOLS:
        min_allowed_symbols = max(
            MIN_PRODUCTION_GATE_SYMBOLS,
            int(int(effective_symbols) * MIN_COVERAGE_RATIO),
        )
    else:
        min_allowed_symbols = int(effective_symbols)
    if (
        isinstance(child_effective_symbols, bool)
        or not isinstance(child_effective_symbols, int)
        or child_effective_symbols > int(effective_symbols)
        or child_effective_symbols < min_allowed_symbols
    ):
        raise SystemExit(
            "child walk-forward effective_symbols mismatch: "
            f"child={child_effective_symbols!r} wrapper={effective_symbols}; "
            f"require child <= wrapper and >= {min_allowed_symbols}"
        )
    payload.update(
        {
            "source": "sqlite_db",
            "sqlite_db_path": str(db_path),
            "price_mode": "raw",
            "coverage_mode": coverage.coverage_mode,
            "coverage_window": {
                "start": coverage.coverage_window_start,
                "end": coverage.coverage_window_end,
                "lookback_years": coverage.lookback_years,
                "listing_aware": coverage.listing_aware,
                "expected_trade_days": coverage.expected_trade_days,
            },
            "production_gate_coverage": {
                "stock_symbols": coverage.stock_symbols,
                "covered_symbols": child_effective_symbols,
                "selected_symbols": int(effective_symbols),
                "rows": coverage.rows,
                "first_trade_date": coverage.first_trade_date,
                "last_trade_date": coverage.last_trade_date,
                "coverage_mode": coverage.coverage_mode,
                "coverage_window_start": coverage.coverage_window_start,
                "coverage_window_end": coverage.coverage_window_end,
                "lookback_years": coverage.lookback_years,
                "listing_aware": coverage.listing_aware,
                "expected_trade_days": coverage.expected_trade_days,
            },
        }
    )
    atomic_write_text(
        gate_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    )
    print(
        "production gate metadata stamped: "
        f"effective_symbols={child_effective_symbols} selected_symbols={effective_symbols} "
        f"price_mode=raw db={db_path}"
    )
    return child_effective_symbols


def _coverage_summary_from_status_payload(
    payload: dict[str, object],
) -> CoverageSummary | None:
    raw = payload.get("coverage")
    if not isinstance(raw, dict):
        return None
    try:
        return CoverageSummary(
            stock_symbols=int(raw["stock_symbols"]),
            covered_symbols=int(raw["covered_symbols"]),
            rows=int(raw["rows"]),
            first_trade_date=str(raw["first_trade_date"]),
            last_trade_date=str(raw["last_trade_date"]),
            coverage_mode=str(raw.get("coverage_mode") or DEFAULT_COVERAGE_MODE),
            coverage_window_start=str(raw.get("coverage_window_start") or ""),
            coverage_window_end=str(raw.get("coverage_window_end") or ""),
            lookback_years=(
                int(raw["lookback_years"])
                if raw.get("lookback_years") not in (None, "")
                else None
            ),
            listing_aware=bool(raw.get("listing_aware", False)),
            expected_trade_days=int(raw.get("expected_trade_days") or 0),
        )
    except (KeyError, TypeError, ValueError):
        return None


def repair_status_backed_production_metadata(
    status_path: Path,
    *,
    args: argparse.Namespace,
) -> bool:
    payload = _read_status(status_path)
    if not isinstance(payload, dict):
        return False
    if str(payload.get("status") or "").strip() not in {"failed_metadata", "completed"}:
        return False
    coverage = _coverage_summary_from_status_payload(payload)
    effective_symbols = payload.get("effective_symbols")
    if (
        coverage is None
        or isinstance(effective_symbols, bool)
        or not isinstance(effective_symbols, int)
    ):
        return False
    final_effective_symbols = annotate_production_gate_metadata(
        gate_path=Path(args.gate_path),
        db_path=args.db,
        coverage=coverage,
        effective_symbols=effective_symbols,
    )
    _write_status(
        status_path,
        status="completed",
        args=args,
        coverage=coverage,
        effective_symbols=final_effective_symbols,
        command=payload.get("command")
        if isinstance(payload.get("command"), list)
        else None,
        child_exit_code=(
            int(payload["child_exit_code"])
            if isinstance(payload.get("child_exit_code"), int)
            else 0
        ),
        child_pid=(
            int(payload["child_pid"])
            if isinstance(payload.get("child_pid"), int)
            else None
        ),
        detail="production metadata repaired from status coverage",
    )
    return True


def write_minimal_pbo_diagnostics(
    *,
    gate_path: Path,
    report_path: Path,
    coverage: CoverageSummary | None = None,
    overwrite: bool = False,
) -> bool:
    """Write a reviewable PBO failure report when the child report is missing."""
    if (report_path.exists() and not overwrite) or not gate_path.exists():
        return False
    try:
        payload = json.loads(gate_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(payload, dict) or payload.get("pbo_pass") is not False:
        return False

    pbo = payload.get("pbo")
    dsr = payload.get("deflated_sharpe")
    display_effective_symbols = payload.get("effective_symbols", "-")
    if isinstance(display_effective_symbols, bool) or not isinstance(
        display_effective_symbols, int
    ):
        display_effective_symbols = "-"
    coverage_payload = _merge_diagnostic_coverage_payload(
        payload.get("production_gate_coverage"),
        coverage,
    )
    if isinstance(coverage_payload, dict):
        covered_symbols = coverage_payload.get("covered_symbols")
        if isinstance(covered_symbols, int) and covered_symbols > 0:
            display_effective_symbols = covered_symbols
    elif coverage is not None:
        display_effective_symbols = coverage.covered_symbols
    diagnostic_lines: list[str] = []
    if not _append_grid_diagnostics_report(
        diagnostic_lines, payload.get("grid_diagnostics")
    ):
        diagnostic_lines.extend(
            [
                "### PBO 失败定位",
                "",
                "| 指标 | 值 |",
                "|------|-----|",
                "| CSCV 失败组合占比 | - |",
                "| Lambda 中位数 | - |",
                "| Lambda 均值 | - |",
                "| Sharpe 变体分散度 | - |",
                "| Return 变体分散度 | - |",
                "| 最优变体 | - |",
                "| 最弱变体 | - |",
                "",
                "| 最差对齐周期 | 测试窗口 | 平均收益 | 分散度 | 亏损变体数 | 全池平均收益 | 全池下跌占比 | 样本数 |",
                "|--------------|----------|----------|--------|------------|--------------|--------------|--------|",
                "| - | - | - | - | - | - | - | - |",
                "",
                "| 训练选中变体 | 训练块 | 测试块 | 训练 Sharpe | 测试 Sharpe | 测试倒数排名 | 测试最优变体 | Lambda |",
                "|--------------|--------|--------|-------------|-------------|--------------|--------------|--------|",
                "| - | - | - | - | - | - | - | - |",
                "",
            ]
        )
    lines = [
        "# Walk-Forward 生产门禁诊断",
        "",
        "## TL;DR",
        "",
        "- 结论: BLOCK，当前 sidecar 未通过双门，不允许作为上线证据。",
        f"- DSR: {_format_report_float(dsr)}",
        f"- PBO: {_format_report_pct(pbo)}",
        f"- Gate 日期: {payload.get('run_date', '-')}",
        f"- 回测区间: {payload.get('data_start', '-')} ~ {payload.get('data_end', '-')}",
        "",
        *diagnostic_lines,
        "## 生产覆盖",
        "",
        f"**标的数量**: {display_effective_symbols}",
        "",
        "| 项目 | 值 |",
        "|------|-----|",
        f"| effective_symbols | {display_effective_symbols} |",
        f"| price_mode | {payload.get('price_mode', '-')} |",
        f"| sqlite_db_path | {payload.get('sqlite_db_path', '-')} |",
    ]
    if isinstance(coverage_payload, dict):
        lines.extend(
            [
                f"| stock_symbols | {coverage_payload.get('stock_symbols', '-')} |",
                f"| covered_symbols | {coverage_payload.get('covered_symbols', '-')} |",
                f"| rows | {coverage_payload.get('rows', '-')} |",
                f"| first_trade_date | {coverage_payload.get('first_trade_date', '-')} |",
                f"| last_trade_date | {coverage_payload.get('last_trade_date', '-')} |",
            ]
        )
        optional_pairs = (
            ("coverage_mode", coverage_payload.get("coverage_mode")),
            ("coverage_window_start", coverage_payload.get("coverage_window_start")),
            ("coverage_window_end", coverage_payload.get("coverage_window_end")),
            ("lookback_years", coverage_payload.get("lookback_years")),
            ("listing_aware", coverage_payload.get("listing_aware")),
            ("expected_trade_days", coverage_payload.get("expected_trade_days")),
        )
        for key, value in optional_pairs:
            if value in {"", None, "-"}:
                continue
            lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            "## 处理",
            "",
            "- 重新运行 `scripts/run_production_walkforward_gate.py` 生成完整多变体 CSCV 报告。",
            "- 不要手工改写 DSR/PBO/pass 标志；只能由 walk-forward 结果写入 sidecar。",
        ]
    )
    atomic_write_text(report_path, "\n".join(lines) + "\n")
    return True


def diagnostic_report_path(report_path: Path) -> Path:
    name = report_path.name
    if name.endswith("-latest.md"):
        return report_path.with_name(
            f"{name[: -len('-latest.md')]}-diagnostic-latest.md"
        )
    return report_path.with_suffix(f".diagnostic{report_path.suffix}")


def formal_report_backup_path(report_path: Path) -> Path:
    name = report_path.name
    if name.endswith("-latest.md"):
        return report_path.with_name(f"{name[: -len('-latest.md')]}-formal-latest.md")
    return report_path.with_suffix(f".formal{report_path.suffix}")


def preserve_formal_report_snapshot(report_path: Path) -> None:
    if not report_path.exists():
        return
    backup_path = formal_report_backup_path(report_path)
    try:
        atomic_write_text(backup_path, report_path.read_text(encoding="utf-8"))
    except OSError:
        return


def warn_if_report_path_not_writable(report_path: Path) -> None:
    if not report_path.exists():
        return
    if os.access(report_path, os.W_OK):
        return
    print(
        "WARN: formal production walk-forward report is not writable by current user: "
        f"{report_path}. Child walk-forward may fail to refresh the formal report; "
        "fix file ownership/permissions on the server."
    )


def _format_report_float(value: object) -> str:
    return f"{float(value):.4f}" if isinstance(value, (int, float)) else "-"


def _format_report_pct(value: object) -> str:
    return f"{float(value):.2%}" if isinstance(value, (int, float)) else "-"


def _format_optional_float(value: object, *, pct: bool = False) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    return f"{float(value):.2%}" if pct else f"{float(value):.4f}"


def _append_grid_diagnostics_report(lines: list[str], details: object) -> bool:
    if not isinstance(details, dict) or not details:
        return False

    n_combos = details.get("n_combos")
    n_lambda_le_0 = details.get("n_lambda_le_0")
    fail_rate = None
    if isinstance(n_combos, int) and n_combos > 0 and isinstance(n_lambda_le_0, int):
        fail_rate = n_lambda_le_0 / n_combos

    lines.extend(
        [
            "### PBO 失败定位",
            "",
            "| 指标 | 值 |",
            "|------|-----|",
            f"| CSCV 组合数 | {n_combos if isinstance(n_combos, int) else '-'} |",
            f"| λ<=0 组合数 | {n_lambda_le_0 if isinstance(n_lambda_le_0, int) else '-'} |",
            f"| CSCV 失败组合占比 | {_format_optional_float(fail_rate, pct=True)} |",
            f"| Lambda 中位数 | {_format_optional_float(details.get('lambda_median'))} |",
            f"| Lambda 均值 | {_format_optional_float(details.get('lambda_mean'))} |",
            f"| Sharpe 变体分散度 | {_format_optional_float(details.get('variant_dispersion_sharpe'))} |",
            f"| Return 变体分散度 | {_format_optional_float(details.get('variant_dispersion_return'), pct=True)} |",
            f"| 最优变体 | {details.get('best_variant', '-')} |",
            f"| 最弱变体 | {details.get('worst_variant', '-')} |",
            "",
        ]
    )

    worst_periods = details.get("worst_periods")
    if isinstance(worst_periods, list) and worst_periods:
        lines.extend(
            [
                "| 最差对齐周期 | 测试窗口 | 平均收益 | 分散度 | 亏损变体数 | 全池平均收益 | 全池下跌占比 | 样本数 |",
                "|--------------|----------|----------|--------|------------|--------------|--------------|--------|",
            ]
        )
        for item in worst_periods:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"| #{item.get('period_index', '-')} | "
                f"{item.get('period', '-')} | "
                f"{_format_optional_float(item.get('mean_return'), pct=True)} | "
                f"{_format_optional_float(item.get('dispersion'), pct=True)} | "
                f"{item.get('negative_variant_count', '-')} | "
                f"{_format_optional_float(item.get('market_avg_return'), pct=True)} | "
                f"{_format_optional_float(item.get('market_negative_ratio'), pct=True)} | "
                f"{item.get('market_sample_count', '-')} |"
            )
        lines.append("")

    inversions = details.get("selection_inversions")
    if isinstance(inversions, list) and inversions:
        lines.extend(
            [
                "| 训练选中变体 | 训练块 | 测试块 | 训练 Sharpe | 测试 Sharpe | 测试倒数排名 | 测试最优变体 | Lambda |",
                "|--------------|--------|--------|-------------|-------------|--------------|--------------|--------|",
            ]
        )
        for item in inversions:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"| {item.get('selected_variant', '-')} | "
                f"{item.get('train_blocks', '-')} | "
                f"{item.get('test_blocks', '-')} | "
                f"{_format_optional_float(item.get('train_sharpe'))} | "
                f"{_format_optional_float(item.get('test_sharpe'))} | "
                f"{item.get('test_rank_from_bottom', '-')} | "
                f"{item.get('test_best_variant', '-')} | "
                f"{_format_optional_float(item.get('lambda'))} |"
            )
        lines.append("")
    return True


def _extract_report_value(text: str, key: str) -> str:
    patterns = (
        rf"\|\s*{re.escape(key)}\s*\|\s*([^\|\n]+?)\s*\|",
        rf"\*\*{re.escape(key)}\*\*\s*[:：]\s*([^\n]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return str(match.group(1)).strip()
    return ""


def repair_production_gate_metadata(
    *,
    gate_path: Path,
    report_path: Path,
    db_path: Path | None = None,
) -> bool:
    if not gate_path.exists() or not report_path.exists():
        return False
    try:
        payload = json.loads(gate_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(payload, dict):
        return False
    try:
        report_text = report_path.read_text(encoding="utf-8")
    except OSError:
        return False

    changed = False
    if "effective_symbols" not in payload:
        raw_effective = _extract_report_value(report_text, "effective_symbols")
        if raw_effective in {"", "-"}:
            raw_effective = _extract_report_value(report_text, "标的数量")
        if raw_effective in {"", "-"}:
            raw_effective = _extract_report_value(report_text, "covered_symbols")
        if raw_effective.isdigit():
            payload["effective_symbols"] = int(raw_effective)
            changed = True
    if str(payload.get("price_mode") or "").strip() in {"", "-"}:
        raw_price_mode = _extract_report_value(report_text, "price_mode")
        if raw_price_mode in {"-", ""} and db_path:
            raw_price_mode = "raw" if "raw" in db_path.name.lower() else ""
        if raw_price_mode:
            payload["price_mode"] = raw_price_mode
            changed = True
    if str(payload.get("sqlite_db_path") or "").strip() in {"", "-"}:
        extracted_db = _extract_report_value(report_text, "sqlite_db_path")
        resolved_db = str(db_path) if db_path else extracted_db
        if extracted_db not in {"", "-"}:
            resolved_db = extracted_db
        if resolved_db and resolved_db != "-":
            payload["sqlite_db_path"] = resolved_db
            changed = True
    coverage_keys = (
        "stock_symbols",
        "covered_symbols",
        "rows",
        "first_trade_date",
        "last_trade_date",
        "coverage_mode",
        "coverage_window_start",
        "coverage_window_end",
        "lookback_years",
        "listing_aware",
        "expected_trade_days",
    )
    coverage = payload.get("production_gate_coverage")
    if not isinstance(coverage, dict):
        coverage = {}
    coverage_changed = False
    if "selected_symbols" not in coverage and isinstance(
        payload.get("effective_symbols"), int
    ):
        coverage["selected_symbols"] = int(payload["effective_symbols"])
        coverage_changed = True
    for key in coverage_keys:
        if key in coverage and str(coverage.get(key) or "").strip():
            continue
        value = _extract_report_value(report_text, key)
        if not value:
            continue
        if (
            key
            in {
                "stock_symbols",
                "covered_symbols",
                "rows",
                "lookback_years",
                "expected_trade_days",
            }
            and value.isdigit()
        ):
            coverage[key] = int(value)
        elif key == "listing_aware":
            coverage[key] = value.lower() == "true"
        else:
            coverage[key] = value
        coverage_changed = True
    if coverage_changed:
        payload["production_gate_coverage"] = coverage
        changed = True
    if not changed:
        return False
    atomic_write_text(
        gate_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repair-only", action="store_true")
    parser.add_argument("--db", type=Path, default=DEFAULT_RAW_DB)
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--min-symbols", type=int, default=MIN_PRODUCTION_GATE_SYMBOLS)
    parser.add_argument(
        "--grid-profile", choices=("stable", "exploratory"), default="stable"
    )
    parser.add_argument(
        "--report", default="reports/walkforward-grid-raw-production-latest.md"
    )
    parser.add_argument(
        "--cache-path", default="data/walkforward_raw_production_cache.db"
    )
    parser.add_argument("--log", default="logs/walkforward-raw-production.log")
    parser.add_argument("--gate-path", default="data/walkforward_gate.json")
    parser.add_argument(
        "--status-path",
        default=DEFAULT_STATUS_PATH,
        help="write production walkforward wrapper status for remote diagnosis",
    )
    parser.add_argument(
        "--lock-path",
        default=DEFAULT_LOCK_PATH,
        help="atomic production child lock; prevents concurrent heavy walk-forward runs",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=7200,
        help="stop the production walk-forward run if it hangs during data loading/backtest",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--coverage-mode",
        default=DEFAULT_COVERAGE_MODE,
        help="coverage filter mode: auto_recent_window (default) or legacy/full_span",
    )
    parser.add_argument(
        "--lookback-years",
        type=int,
        default=DEFAULT_LOOKBACK_YEARS,
        help="rolling coverage window length for auto_recent_window mode",
    )
    parser.add_argument(
        "--symbols-cache-path",
        default=DEFAULT_SYMBOL_CACHE_PATH,
        help="cache production coverage-selected symbols to avoid repeating full-market sqlite scans",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=int,
        default=60,
        help="status heartbeat interval while the child walkforward process is still running",
    )
    parser.add_argument(
        "--min-memory-gib",
        type=float,
        default=MIN_PRODUCTION_MEMORY_GIB,
        help="minimum host memory for launching the child production walk-forward",
    )
    parser.add_argument(
        "--stream-batch-size",
        type=int,
        default=DEFAULT_STREAM_BATCH_SIZE,
        help="bounded production streaming batch size; does not reduce coverage or gate thresholds",
    )
    parser.add_argument(
        "--no-streaming",
        action="store_true",
        help="disable the bounded production workflow; low-memory hosts will fail closed",
    )
    args = parser.parse_args()
    if args.stream_batch_size <= 0:
        parser.error("--stream-batch-size must be positive")
    args.coverage_mode = _normalize_coverage_mode(args.coverage_mode)
    if not str(args.end or "").strip():
        args.end = _default_production_end()
    if not str(args.start or "").strip():
        args.start = _default_production_start(
            end=args.end,
            lookback_years=args.lookback_years,
        )
    status_path = _status_path(args.status_path)
    symbols_cache_path = _symbol_cache_path(args.symbols_cache_path)
    repair_stale_running_status(status_path, args=args)
    active_payload = _read_status(status_path)
    active_detail = _active_running_production_detail(active_payload)
    if active_detail and not args.repair_only:
        _write_blocked_running_status(
            status_path,
            payload=active_payload,
            detail=active_detail,
        )
        print(f"BLOCK: {active_detail}")
        return 2

    resource_blocker = _low_memory_blocker(args.min_memory_gib)
    if (
        resource_blocker
        and _total_memory_gib() is None
        and not args.dry_run
        and not args.repair_only
    ):
        _write_preflight_status(
            status_path,
            args=args,
            status="blocked_resources",
            detail=resource_blocker,
        )
        print(f"BLOCK: {resource_blocker}")
        return 2
    if resource_blocker and args.no_streaming and not args.dry_run and not args.repair_only:
        _write_preflight_status(
            status_path,
            args=args,
            status="blocked_resources",
            detail=f"{resource_blocker}; bounded streaming workflow was disabled",
        )
        print(f"BLOCK: {resource_blocker}; bounded streaming workflow was disabled")
        return 2
    if resource_blocker and not args.no_streaming and not args.dry_run and not args.repair_only:
        print(
            "INFO: host is below the full-materialization memory floor; "
            f"using bounded streaming batches of {args.stream_batch_size} symbols "
            "without lowering any production gate."
        )

    if args.repair_only:
        stale_repaired = repair_stale_running_status(status_path, args=args)
        status_payload = _read_status(status_path)
        status_coverage = (
            _coverage_summary_from_status_payload(status_payload)
            if isinstance(status_payload, dict)
            else None
        )
        status_backed_repaired = repair_status_backed_production_metadata(
            status_path,
            args=args,
        )
        repaired = repair_production_gate_metadata(
            gate_path=Path(args.gate_path),
            report_path=Path(args.report),
            db_path=args.db,
        )
        diagnostic_repaired = write_minimal_pbo_diagnostics(
            gate_path=Path(args.gate_path),
            report_path=diagnostic_report_path(Path(args.report)),
            coverage=status_coverage,
            overwrite=True,
        )
        if stale_repaired and (
            status_backed_repaired or repaired or diagnostic_repaired
        ):
            print("production gate status and metadata repaired")
            return 0
        if status_backed_repaired and (repaired or diagnostic_repaired):
            print("production gate metadata repaired from status and report")
            return 0
        if status_backed_repaired:
            print("production gate metadata repaired from status")
            return 0
        if stale_repaired:
            print("production gate status repaired")
            return 0
        if repaired or diagnostic_repaired:
            print("production gate metadata repaired")
            return 0
        print("production gate metadata unchanged")
        return 1

    _write_preflight_status(
        status_path,
        args=args,
        status="inspecting_coverage",
        detail="inspecting raw sqlite full-market coverage",
    )
    if not args.db.exists():
        _write_preflight_status(
            status_path,
            args=args,
            status="blocked_db",
            detail=f"raw sqlite db missing: {args.db}",
        )
        print(f"BLOCK: raw sqlite db missing: {args.db}")
        return 2
    raw_max_trade_date = None
    if _raw_sqlite_has_daily_table(args.db):
        raw_max_trade_date = _raw_sqlite_max_trade_date(args.db)
        cutoff_blocker = validate_production_cutoff_consistency(
            db_path=args.db,
            requested_end=args.end,
            gate_path=Path(args.gate_path),
            raw_max_trade_date=raw_max_trade_date,
        )
        if cutoff_blocker:
            _write_preflight_status(
                status_path,
                args=args,
                status="blocked_cutoff",
                detail=cutoff_blocker,
            )
            print(f"BLOCK: {cutoff_blocker}")
            return 2
    inspection = load_cached_coverage_symbols(
        symbols_cache_path,
        db_path=args.db,
        start=args.start,
        end=args.end,
        min_symbols=args.min_symbols,
        coverage_mode=args.coverage_mode,
        lookback_years=args.lookback_years,
        raw_max_trade_date=raw_max_trade_date,
    )
    if inspection is None:
        inspection = inspect_raw_coverage_with_symbols(
            args.db,
            start=args.start,
            end=args.end,
            coverage_mode=args.coverage_mode,
            lookback_years=args.lookback_years,
        )
        inspection = _align_inspection_metadata(
            inspection,
            start=args.start,
            end=args.end,
            coverage_mode=args.coverage_mode,
            lookback_years=args.lookback_years,
        )
        write_cached_coverage_symbols(
            symbols_cache_path,
            db_path=args.db,
            start=args.start,
            end=args.end,
            min_symbols=args.min_symbols,
            inspection=inspection,
        )
        print(f"production gate symbols cache refreshed: {symbols_cache_path}")
    else:
        inspection = _align_inspection_metadata(
            inspection,
            start=args.start,
            end=args.end,
            coverage_mode=args.coverage_mode,
            lookback_years=args.lookback_years,
        )
        print(f"production gate symbols cache hit: {symbols_cache_path}")
    coverage = inspection.summary
    if raw_max_trade_date is not None:
        cutoff_blocker = validate_production_cutoff_consistency(
            db_path=args.db,
            requested_end=args.end,
            gate_path=Path(args.gate_path),
            raw_max_trade_date=raw_max_trade_date,
            coverage_last_trade_date=coverage.last_trade_date,
        )
        if cutoff_blocker:
            _write_status(
                status_path,
                status="blocked_cutoff",
                args=args,
                coverage=coverage,
                effective_symbols=coverage.covered_symbols,
                detail=cutoff_blocker,
            )
            print(f"BLOCK: {cutoff_blocker}")
            return 2
    print(
        "production gate raw coverage: "
        f"stocks={coverage.stock_symbols} covered={coverage.covered_symbols} "
        f"rows={coverage.rows} range={coverage.first_trade_date}..{coverage.last_trade_date} "
        f"mode={coverage.coverage_mode} window={coverage.coverage_window_start}..{coverage.coverage_window_end}"
    )
    if coverage.covered_symbols < args.min_symbols:
        _write_status(
            status_path,
            status="blocked_coverage",
            args=args,
            coverage=coverage,
            effective_symbols=coverage.covered_symbols,
            detail=f"need {args.min_symbols}, got {coverage.covered_symbols}",
        )
        print(
            "BLOCK: raw full-market coverage is insufficient; "
            f"need {args.min_symbols}, got {coverage.covered_symbols}."
        )
        print(
            "Backfill missing raw history first: .venv/bin/python "
            "scripts/update_sqlite_daily.py "
            f"{args.db} --price-mode raw --start-date {args.start} "
            f"--target-date {args.end} --fill-history-gaps --limit 0"
        )
        print(
            "Only for a clean rebuild, append --force-from-start after taking a database backup."
        )
        return 2

    covered_symbols = inspection.covered_symbols
    if len(covered_symbols) < args.min_symbols:
        _write_status(
            status_path,
            status="blocked_symbols",
            args=args,
            coverage=coverage,
            effective_symbols=len(covered_symbols),
            detail=f"need {args.min_symbols}, got {len(covered_symbols)} selected symbols",
        )
        print(
            "BLOCK: selected production symbols are insufficient; "
            f"need {args.min_symbols}, got {len(covered_symbols)}."
        )
        return 2

    if args.dry_run:
        command = build_walkforward_command(args)
        print("production gate command:", " ".join(command))
        _write_status(
            status_path,
            status="dry_run",
            args=args,
            coverage=coverage,
            effective_symbols=len(covered_symbols),
            command=command,
            detail="dry-run only; child walkforward not started",
        )
        return 0

    lock_path = _lock_path(args.lock_path)
    lock_acquired, lock_detail = _acquire_production_lock(lock_path)
    if not lock_acquired:
        _write_blocked_running_status(
            status_path,
            payload=_read_status(status_path),
            detail=lock_detail,
        )
        print(f"BLOCK: {lock_detail}")
        return 2

    tmp_symbols_path: Path | None = None
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix="aqsp-walkforward-symbols-",
        suffix=".txt",
        delete=False,
    ) as tmp_symbols:
        tmp_symbols.write("\n".join(covered_symbols) + "\n")
        tmp_symbols_path = Path(tmp_symbols.name)
    args.symbols_file = str(tmp_symbols_path)
    print(f"production gate selected symbols: {len(covered_symbols)}")
    _write_status(
        status_path,
        status="preparing_child",
        args=args,
        coverage=coverage,
        effective_symbols=len(covered_symbols),
        detail="selected covered symbols; preparing child walkforward",
    )

    command = build_walkforward_command(args)
    print("production gate command:", " ".join(command))
    env = os.environ.copy()
    try:
        env["AQSP_SQLITE_DB_PATH"] = str(args.db)
        env["AQSP_SQLITE_PREFILTERED_SYMBOLS"] = "1"
        warn_if_report_path_not_writable(Path(args.report))
        preserve_formal_report_snapshot(Path(args.report))
        requested_timeout_seconds = int(args.timeout_seconds or 0)
        effective_timeout_seconds = _effective_timeout_seconds(
            requested_timeout_seconds,
            effective_symbols=len(covered_symbols),
            min_production_symbols=args.min_symbols,
        )
        if effective_timeout_seconds != requested_timeout_seconds:
            args.timeout_seconds = effective_timeout_seconds
            print(
                "production gate timeout auto-raised: "
                f"{requested_timeout_seconds}s -> {effective_timeout_seconds}s "
                f"for {len(covered_symbols)} symbols"
            )
        try:
            child_exit_code, child_pid = _execute_child_walkforward(
                command=command,
                env=env,
                cwd=PROJECT_ROOT,
                timeout_seconds=effective_timeout_seconds,
                status_path=status_path,
                args=args,
                coverage=coverage,
                effective_symbols=len(covered_symbols),
            )
            if child_exit_code != 0:
                diagnostic_path = diagnostic_report_path(Path(args.report))
                if write_minimal_pbo_diagnostics(
                    gate_path=Path(args.gate_path),
                    report_path=diagnostic_path,
                    coverage=coverage,
                    overwrite=True,
                ):
                    print(
                        f"production gate diagnostic report written: {diagnostic_path}"
                    )
                _write_status(
                    status_path,
                    status="failed",
                    args=args,
                    coverage=coverage,
                    effective_symbols=len(covered_symbols),
                    command=command,
                    child_exit_code=child_exit_code,
                    child_pid=child_pid,
                    detail="child walkforward returned non-zero",
                )
                return child_exit_code
            try:
                if raw_max_trade_date is not None:
                    cutoff_blocker = validate_production_cutoff_consistency(
                        db_path=args.db,
                        requested_end=args.end,
                        gate_path=Path(args.gate_path),
                        raw_max_trade_date=raw_max_trade_date,
                        coverage_last_trade_date=coverage.last_trade_date,
                    )
                    if cutoff_blocker:
                        raise SystemExit(cutoff_blocker)
                final_effective_symbols = annotate_production_gate_metadata(
                    gate_path=Path(args.gate_path),
                    db_path=args.db,
                    coverage=coverage,
                    effective_symbols=len(covered_symbols),
                )
            except (json.JSONDecodeError, OSError, SystemExit) as exc:
                _write_status(
                    status_path,
                    status="failed_metadata",
                    args=args,
                    coverage=coverage,
                    effective_symbols=len(covered_symbols),
                    command=command,
                    child_exit_code=child_exit_code,
                    child_pid=child_pid,
                    detail=str(exc),
                )
                print(f"BLOCK: failed to stamp production gate metadata: {exc}")
                return 2
            diagnostic_path = diagnostic_report_path(Path(args.report))
            if write_minimal_pbo_diagnostics(
                gate_path=Path(args.gate_path),
                report_path=diagnostic_path,
                coverage=coverage,
                overwrite=True,
            ):
                print(f"production gate diagnostic report written: {diagnostic_path}")
            _write_status(
                status_path,
                status="completed",
                args=args,
                coverage=coverage,
                effective_symbols=final_effective_symbols,
                command=command,
                child_exit_code=child_exit_code,
                child_pid=child_pid,
                detail="child walkforward completed",
            )
            return 0
        except subprocess.TimeoutExpired:
            _write_status(
                status_path,
                status="timeout",
                args=args,
                coverage=coverage,
                effective_symbols=len(covered_symbols),
                command=command,
                child_exit_code=124,
                detail="child walkforward timed out",
            )
            print(
                "BLOCK: production walk-forward timed out; "
                f"timeout_seconds={effective_timeout_seconds}. "
                "Reduce the covered symbol batch or inspect sqlite fetch performance."
            )
            return 124
    finally:
        _release_production_lock(lock_path)
        if tmp_symbols_path is not None:
            tmp_symbols_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
