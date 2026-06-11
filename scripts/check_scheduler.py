#!/usr/bin/env python3
"""Diagnose AQSP scheduled tasks without touching system configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import subprocess

from aqsp.core.time import now_shanghai


PROJECT_ROOT = Path(
    os.environ.get("AQSP_PROJECT_ROOT", Path(__file__).resolve().parents[1])
).resolve()
TODAY = now_shanghai().date().isoformat()


@dataclass(frozen=True)
class CheckResult:
    label: str
    ok: bool
    detail: str


def _run(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        return 127, str(exc)
    output = (result.stdout + result.stderr).strip()
    return result.returncode, output


def _truthy(value: str | None) -> bool:
    return (value or "").lower() in {"1", "true", "yes", "on"}


def _exists(path: Path) -> CheckResult:
    return CheckResult(
        str(path), path.exists(), "exists" if path.exists() else "missing"
    )


def check_project_root() -> CheckResult:
    git_dir = PROJECT_ROOT / ".git"
    return CheckResult(
        "project root",
        git_dir.is_dir(),
        f"{PROJECT_ROOT} ({'git repo' if git_dir.is_dir() else 'not a git repo'})",
    )


def check_python_import() -> CheckResult:
    python_bin = PROJECT_ROOT / ".venv" / "bin" / "python3"
    if not python_bin.exists():
        python_bin = Path("python3")
    code, output = _run(
        [str(python_bin), "-c", "import aqsp; import aqsp.cli; print('ok')"],
        PROJECT_ROOT,
    )
    return CheckResult("python import", code == 0, output or "ok")


def check_bt_script() -> CheckResult:
    script = PROJECT_ROOT / "scripts" / "bt_task.sh"
    if not script.exists():
        return CheckResult("bt_task.sh", False, "missing")
    text = script.read_text(encoding="utf-8")
    expected = ["daily", "intraday", "midday", "coldstart", "monitor", "status"]
    missing = [item for item in expected if item not in text]
    return CheckResult(
        "bt_task.sh",
        not missing,
        "missing actions: " + ",".join(missing) if missing else "actions ok",
    )


def check_crontab() -> CheckResult:
    code, output = _run(["crontab", "-l"])
    if code != 0:
        return CheckResult("crontab", False, output or "crontab unavailable")
    relevant = [line for line in output.splitlines() if "bt_task.sh" in line]
    return CheckResult(
        "BT scheduled jobs",
        bool(relevant),
        "\n".join(relevant) if relevant else "no bt_task.sh entries found",
    )


def check_logs() -> list[CheckResult]:
    paths = [
        PROJECT_ROOT / "logs" / "bt" / f"bt-daily-{TODAY}.log",
        PROJECT_ROOT / "logs" / "bt" / f"bt-intraday-{TODAY}.log",
        PROJECT_ROOT / "logs" / "bt" / f"bt-midday-{TODAY}.log",
        PROJECT_ROOT / "logs" / "bt" / f"bt-monitor-{TODAY}.log",
        PROJECT_ROOT / "logs" / "deploy" / f"sync-{TODAY}.log",
    ]
    return [_exists(path) for path in paths]


def check_locks() -> list[CheckResult]:
    lock_dir = PROJECT_ROOT / ".locks"
    locks = sorted(lock_dir.glob("*.lock")) if lock_dir.exists() else []
    if not locks:
        return [CheckResult("locks", True, "no active lock directories")]
    results: list[CheckResult] = []
    for lock in locks:
        age = max(0.0, now_shanghai().timestamp() - lock.stat().st_mtime)
        stale = age > 6 * 60 * 60
        results.append(
            CheckResult(
                f"lock {lock.name}",
                not stale,
                f"age={age / 60:.1f}min {'stale?' if stale else 'active/recent'}",
            )
        )
    return results


def main() -> int:
    print("AQSP scheduler diagnosis")
    print(f"time: {now_shanghai().isoformat(timespec='seconds')}")
    print(f"project: {PROJECT_ROOT}")
    print()

    checks = [
        check_project_root(),
        check_python_import(),
        check_bt_script(),
        check_crontab(),
        *check_logs(),
        *check_locks(),
    ]

    has_error = False
    for result in checks:
        marker = "OK" if result.ok else "WARN"
        print(f"[{marker}] {result.label}: {result.detail}")
        has_error = has_error or not result.ok

    if _truthy(os.environ.get("AQSP_SCHEDULER_STRICT")) and has_error:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
