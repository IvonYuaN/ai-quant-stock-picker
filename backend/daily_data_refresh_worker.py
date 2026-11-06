"""Run the existing bounded daily refresh CLI and persist resumable progress."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from aqsp.utils.jsonl_io import atomic_write_text  # noqa: E402


def _read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_status(path: Path, status: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(status, ensure_ascii=False, sort_keys=True) + "\n")


def run_worker(
    *,
    run_id: str,
    target_day: str,
    db_path: Path,
    cursor_path: Path,
    state_path: Path,
) -> int:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = _read_object(state_path)
        if state.get("run_id") == run_id and state.get("pid") == os.getpid():
            break
        time.sleep(0.05)
    else:
        return 2

    log_dir = state_path.parent.parent / "logs" / "daily-refresh"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{run_id}.log"
    script = PROJECT_ROOT / "scripts" / "data" / "refresh_sqlite_batch.py"
    command = [
        sys.executable,
        str(script),
        "--db",
        str(db_path),
        "--state",
        str(cursor_path),
        "--target-date",
        target_day,
        "--batches",
        os.environ.get("AQSP_DATA_REFRESH_BATCHES", "0"),
        "--batch-size",
        os.environ.get("AQSP_DATA_REFRESH_BATCH_SIZE", "120"),
        "--max-runtime-seconds",
        os.environ.get("AQSP_DATA_REFRESH_MAX_RUNTIME_SECONDS", "480"),
        "--query-timeout-seconds",
        os.environ.get("AQSP_DATA_QUERY_TIMEOUT_SECONDS", "4"),
    ]
    state.update({"status": "running", "updated_at": _timestamp()})
    _write_status(state_path, state)
    with log_path.open("a", encoding="utf-8") as log_handle:
        child = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
        state["child_pid"] = child.pid
        _write_status(state_path, state)
        while child.poll() is None:
            cursor = _read_object(cursor_path)
            if cursor.get("target_day") == target_day:
                state.update(
                    {
                        "processed_symbols": int((cursor.get("last_batch") or {}).get("processed_symbols") or 0),
                        "eligible_covered_count": int(cursor.get("eligible_covered_count") or 0),
                        "eligible_coverage_pct": float(cursor.get("eligible_coverage_pct") or 0),
                        "last_batch": cursor.get("last_batch") or {},
                        "updated_at": _timestamp(),
                    }
                )
                _write_status(state_path, state)
            time.sleep(1)
        return_code = child.wait()

    cursor = _read_object(cursor_path)
    coverage = float(cursor.get("eligible_coverage_pct") or 0)
    status = "failed" if return_code not in {0, 75} else "complete" if coverage >= 1 else "partial"
    state.update(
        {
            "status": status,
            "updated_at": _timestamp(),
            "finished_at": _timestamp(),
            "exit_code": return_code,
            "eligible_covered_count": int(cursor.get("eligible_covered_count") or 0),
            "eligible_coverage_pct": coverage,
            "last_batch": cursor.get("last_batch") or {},
            "error": (
                "数据源暂不可用，游标已保留，可稍后续传"
                if return_code == 75
                else "日线刷新失败，请查看当前覆盖与任务游标"
                if return_code != 0
                else None
            ),
        }
    )
    _write_status(state_path, state)
    return return_code


def _timestamp() -> str:
    from aqsp.core.time import now_shanghai

    return now_shanghai().isoformat(timespec="seconds")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--target-day", required=True)
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument("--cursor-path", required=True, type=Path)
    parser.add_argument("--state-path", required=True, type=Path)
    args = parser.parse_args()
    return run_worker(
        run_id=args.run_id,
        target_day=args.target_day,
        db_path=args.db_path,
        cursor_path=args.cursor_path,
        state_path=args.state_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
