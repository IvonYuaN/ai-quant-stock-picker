"""Execute one persisted user backtest run and retain bounded progress/log evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from aqsp.core.time import now_shanghai  # noqa: E402
import backtest_runs  # noqa: E402

MAX_LOG_BYTES = 1024 * 1024
MAX_RUN_SECONDS = 2 * 60 * 60
CHILD_TERMINATE_GRACE_SECONDS = 10
POLL_SECONDS = 0.5
_CANCEL_REQUESTED = False


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write(path: Path, state: dict[str, Any]) -> None:
    backtest_runs._write_state(path, state)


def _request_cancel(_signum: int, _frame: object) -> None:
    global _CANCEL_REQUESTED
    _CANCEL_REQUESTED = True


def _timestamp() -> str:
    return now_shanghai().isoformat(timespec="seconds")


def run_worker(run_id: str, state_path: Path, project_root: Path) -> int:
    global _CANCEL_REQUESTED
    _CANCEL_REQUESTED = False
    signal.signal(signal.SIGTERM, _request_cancel)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = _read(state_path)
        if state.get("run_id") == run_id and state.get("pid") == os.getpid():
            break
        time.sleep(0.05)
    else:
        return 2

    run_dir = state_path.parent
    if _CANCEL_REQUESTED:
        state.update({
            "status": "cancelled", "phase": "cancelled", "progress": None,
            "message": "任务启动前收到取消请求，未启动回测进程",
            "updated_at": _timestamp(), "finished_at": _timestamp(),
        })
        _write(state_path, state)
        return 0
    command = backtest_runs.build_worker_command(state.get("config") or {}, run_dir)
    (run_dir / "report.md").unlink(missing_ok=True)
    (run_dir / "gate.json").unlink(missing_ok=True)
    log_path = run_dir / "run.log"
    state.update({"status": "running", "phase": "running", "message": "Walk-forward 正在执行", "updated_at": _timestamp()})
    _write(state_path, state)
    env = os.environ.copy()
    env["AQSP_RUNTIME_DATA_ROOT"] = str(run_dir / "runtime")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(project_root / "src"), str(project_root), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    child = subprocess.Popen(
        command, cwd=project_root, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=0, close_fds=True, env=env, start_new_session=True,
    )
    log_size = 0
    latest = state["message"]
    line_buffer = bytearray()
    selector = selectors.DefaultSelector()
    assert child.stdout is not None
    selector.register(child.stdout, selectors.EVENT_READ)
    started = time.monotonic()
    last_state_write = started
    terminal_status = "completed"
    with log_path.open("w", encoding="utf-8") as log:
        while child.poll() is None:
            elapsed = time.monotonic() - started
            if _CANCEL_REQUESTED:
                terminal_status = "cancelled"
                break
            if elapsed >= MAX_RUN_SECONDS:
                terminal_status = "timed_out"
                break
            for _key, _mask in selector.select(timeout=POLL_SECONDS):
                chunk = os.read(child.stdout.fileno(), 65536)
                if not chunk:
                    selector.unregister(child.stdout)
                    continue
                if log_size < MAX_LOG_BYTES:
                    written = chunk[: MAX_LOG_BYTES - log_size]
                    log.write(written.decode("utf-8", errors="replace"))
                    log_size += len(written)
                line_buffer.extend(chunk)
                if len(line_buffer) > 8192:
                    line_buffer = line_buffer[-8192:]
                if b"\n" in line_buffer:
                    lines = line_buffer.split(b"\n")
                    line_buffer = bytearray(lines.pop())
                    latest = lines[-1].decode("utf-8", errors="replace").strip()[:500] or latest
                    state.update({"message": latest, "updated_at": _timestamp()})
                    _write(state_path, state)
            if time.monotonic() - last_state_write >= 2:
                state["elapsed_seconds"] = int(elapsed)
                state["progress"] = None
                state["updated_at"] = _timestamp()
                _write(state_path, state)
                last_state_write = time.monotonic()

        return_code: int | None = None
        if terminal_status != "completed" and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                return_code = child.wait(timeout=CHILD_TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                return_code = child.wait()
            except ProcessLookupError:
                return_code = child.wait()
        if return_code is None:
            return_code = child.wait()
        log.flush()
        os.fsync(log.fileno())
    selector.close()

    report_path = run_dir / "report.md"
    gate_path = run_dir / "gate.json"
    report_exists = report_path.is_file() and report_path.stat().st_size <= backtest_runs.MAX_REPORT_BYTES
    gate = _read(gate_path)
    evidence_files = sorted(
        (run_dir / "runtime" / "strategy_oos_evidence" / "composite").glob("*.json")
    )
    oos_evidence = _read(evidence_files[-1]) if evidence_files else {}
    completed = terminal_status == "completed" and return_code == 0 and report_exists and bool(gate)
    if terminal_status == "completed" and not completed:
        terminal_status = "failed"
    final_status = "completed" if completed else terminal_status
    failure_message = latest or "回测失败；未发布正式策略"
    if final_status == "failed":
        failure_message = f"回测失败：{failure_message}"
    state.update({
        "status": final_status,
        "phase": "complete" if completed else final_status,
        "progress": 100 if completed else None,
        "message": "回测完成，报告与 gate 证据已隔离保存" if completed else "回测已取消" if final_status == "cancelled" else "回测超过 2 小时资源上限" if final_status == "timed_out" else failure_message,
        "updated_at": _timestamp(),
        "finished_at": _timestamp(),
        "exit_code": return_code,
        "artifact_id": hashlib.sha256(report_path.read_bytes() + gate_path.read_bytes()).hexdigest() if completed else None,
        "gate": {
            **{key: gate.get(key) for key in ("run_date", "start", "end", "dsr", "pbo", "both_pass", "price_mode", "effective_symbols", "n_periods") if key in gate},
            "cscv_reliability": ((gate.get("grid_diagnostics") or {}).get("cscv_reliability")),
            "cscv_warnings": list((gate.get("grid_diagnostics") or {}).get("cscv_warnings") or []),
        } if completed else None,
        "oos_evidence": {
            key: oos_evidence.get(key)
            for key in ("artifact_id", "evidence_status", "effective_symbols", "blockers")
            if key in oos_evidence
        } if completed and oos_evidence else None,
    })
    _write(state_path, state)
    return return_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--state-path", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    args = parser.parse_args()
    return run_worker(args.run_id, args.state_path, args.project_root)


if __name__ == "__main__":
    raise SystemExit(main())
