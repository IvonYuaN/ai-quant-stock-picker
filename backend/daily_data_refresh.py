"""Bounded, resumable local raw-daily refresh task and coverage status."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any
from uuid import uuid4

from aqsp.core.errors import DataError
from aqsp.core.time import get_previous_trading_day, latest_completed_trading_day, now_shanghai
from aqsp.core.runtime import runtime_data_root
from aqsp.data.daily_universe import refreshable_daily_universe
from aqsp.data.source_factory import resolve_sqlite_db_path
from aqsp.data.sqlite_db_source import SqliteDbSource
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text


class RefreshConflict(RuntimeError):
    """A daily refresh task is already active."""


class RefreshUnavailable(RuntimeError):
    """The local raw SQLite daily source cannot be inspected or refreshed."""


def _state_path() -> Path:
    configured = os.environ.get("AQSP_SQLITE_REFRESH_JOB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return runtime_data_root() / ".state" / "sqlite-refresh-job.json"


def _cursor_path() -> Path:
    configured = os.environ.get("AQSP_SQLITE_REFRESH_CURSOR_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return runtime_data_root() / ".state" / "sqlite-refresh-cursor.json"


def _read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pid_is_running(pid: object) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def start_daily_refresh() -> dict[str, Any]:
    """Start one resumable refresh run for the updater's supported A-share universe."""
    db_path = resolve_sqlite_db_path()
    if not db_path:
        raise RefreshUnavailable("没有配置或找到本地 raw SQLite 日线库")
    if not Path(db_path).is_file():
        raise RefreshUnavailable("本地 raw SQLite 日线库不存在")

    project_root = Path(__file__).resolve().parents[1]
    state_path = _state_path()
    cursor_path = _cursor_path()
    target_day = latest_completed_trading_day(now_shanghai())
    run_id = uuid4().hex
    worker = project_root / "backend" / "daily_data_refresh_worker.py"
    with advisory_lock(state_path.with_name(f"{state_path.name}.start")):
        previous = _read_object(state_path)
        if previous.get("status") in {"starting", "running"} and _pid_is_running(previous.get("pid")):
            raise RefreshConflict("受支持标的日线刷新正在运行")
        initial = {
            "run_id": run_id,
            "status": "starting",
            "target_day": target_day.isoformat(),
            "started_at": now_shanghai().isoformat(timespec="seconds"),
            "pid": None,
        }
        atomic_write_text(state_path, json.dumps(initial, ensure_ascii=False) + "\n")
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(worker),
                    "--run-id",
                    run_id,
                    "--target-day",
                    target_day.isoformat(),
                    "--db-path",
                    db_path,
                    "--cursor-path",
                    str(cursor_path),
                    "--state-path",
                    str(state_path),
                ],
                cwd=project_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as exc:
            initial.update(
                {
                    "status": "failed",
                    "finished_at": now_shanghai().isoformat(timespec="seconds"),
                    "error": "日线刷新进程无法启动",
                }
            )
            atomic_write_text(state_path, json.dumps(initial, ensure_ascii=False) + "\n")
            raise RefreshUnavailable(f"日线刷新进程无法启动：{exc}") from exc
        initial["pid"] = process.pid
        atomic_write_text(state_path, json.dumps(initial, ensure_ascii=False) + "\n")
    return {"run_id": run_id, "status": "starting", "target_day": target_day.isoformat()}


def daily_refresh_status() -> dict[str, Any]:
    """Report the actual eligible-universe coverage, not the database max date alone."""
    db_path = resolve_sqlite_db_path()
    expected_day = latest_completed_trading_day(now_shanghai())
    job = _read_object(_state_path())
    if job.get("status") in {"starting", "running"} and not _pid_is_running(job.get("pid")):
        job = {**job, "status": "unknown", "message": "刷新进程已结束，但没有写入终态；请按覆盖结果判断"}

    if not db_path or not Path(db_path).is_file():
        return {
            "availability": "missing",
            "expected_as_of": expected_day.isoformat(),
            "latest_as_of": None,
            "eligible_symbols": 0,
            "covered_symbols": 0,
            "coverage_ratio": 0.0,
            "cursor": {},
            "job": _public_job(job),
        }

    try:
        source = SqliteDbSource(db_path=db_path, cache=None)
        latest = source.get_latest_daily_date()
        reference_day = get_previous_trading_day(expected_day)
        symbols = refreshable_daily_universe(
            source, universe_limit=0, reference_day=reference_day
        )
        covered = source.get_symbols_with_daily_coverage(
            symbols,
            expected_day,
            expected_day,
            min_rows=1,
            min_coverage_ratio=1.0,
        )
    except (DataError, OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        raise RefreshUnavailable(f"读取本地日线覆盖失败：{exc}") from exc

    eligible_count = len(symbols)
    covered_count = len(covered)
    ratio = covered_count / eligible_count if eligible_count else 0.0
    if eligible_count and covered_count == eligible_count and latest == expected_day:
        availability = "ready"
    elif covered_count:
        availability = "partial"
    elif latest is not None and latest < expected_day:
        availability = "stale"
    else:
        availability = "blocked"
    cursor = _read_object(_cursor_path())
    return {
        "availability": availability,
        "expected_as_of": expected_day.isoformat(),
        "latest_as_of": latest.isoformat() if latest else None,
        "eligible_symbols": eligible_count,
        "covered_symbols": covered_count,
        "coverage_ratio": round(ratio, 6),
        "cursor": cursor if cursor.get("target_day") == expected_day.isoformat() else {},
        "job": _public_job(job),
    }


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "run_id",
        "status",
        "target_day",
        "started_at",
        "updated_at",
        "finished_at",
        "processed_symbols",
        "eligible_covered_count",
        "eligible_coverage_pct",
        "last_batch",
        "error",
        "message",
    )
    return {key: job[key] for key in allowed if key in job}
