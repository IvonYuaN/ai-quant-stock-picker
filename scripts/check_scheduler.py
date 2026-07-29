#!/usr/bin/env python3
"""Diagnose AQSP scheduled tasks without touching system configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable


PROJECT_ROOT = Path(
    os.environ.get("AQSP_PROJECT_ROOT", Path(__file__).resolve().parents[1])
).resolve()
RUNTIME_ROOT = Path(os.environ.get("AQSP_RUNTIME_ROOT", PROJECT_ROOT)).resolve()
RUNTIME_DATA_ROOT = Path(
    os.environ.get("AQSP_RUNTIME_DATA_ROOT", RUNTIME_ROOT / "data")
).resolve()
RUNTIME_LOCK_ROOT = Path(
    os.environ.get("AQSP_RUNTIME_LOCK_DIR", RUNTIME_DATA_ROOT / ".locks")
).resolve()
BT_CRON_DIR = Path(os.environ.get("AQSP_BT_CRON_DIR", "/www/server/cron")).resolve()
REQUIRED_SCHEDULED_ACTIONS = frozenset(
    {
        "daily",
        "intraday",
        "midday",
        "data-refresh",
        "coldstart",
        "variant-refresh",
        "walkforward-gate",
        "monitor",
        "news",
    }
)
SCHEDULED_ACTIONS = REQUIRED_SCHEDULED_ACTIONS | frozenset({"data-refresh-retry"})
MULTI_WINDOW_ACTIONS = frozenset({"news"})
LEGACY_CRON_TERMS = (
    "daily_run.sh",
    "intraday_refresh.sh",
    "midday_refresh.sh",
    "daily_pipeline.sh",
    "coldstart_daily.sh",
    "server_monitor.sh",
    "news_catalysts.sh",
    "streamlit",
    "8501",
    "dist/dashboard",
    "release_task_entrypoint.sh",
    "bt_task.sh",
)
BT_LEGACY_ENTRY_PATTERN = re.compile(
    r"(?:AQSP_RUNNER_SCRIPT=|/scripts/)"
    r"(?:daily_run|daily_pipeline|intraday_refresh|midday_refresh|coldstart_daily|"
    r"variant_refresh|run_production_walkforward_gate|server_monitor|news_catalysts)\.sh"
)
BT_ACTION_PATTERN = re.compile(
    r"(?:release_task_entrypoint|bt_task)\.sh\s+("
    + "|".join(sorted(SCHEDULED_ACTIONS))
    + r")\b"
)
for candidate in (PROJECT_ROOT / "src", PROJECT_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from aqsp.core.time import now_shanghai  # noqa: E402

TODAY = now_shanghai().date().isoformat()


@dataclass(frozen=True)
class CheckResult:
    label: str
    ok: bool
    detail: str


def _run(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    env = os.environ.copy()
    source_paths = (str(PROJECT_ROOT / "src"), str(PROJECT_ROOT))
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        (
            *source_paths,
            *(item for item in existing_pythonpath.split(os.pathsep) if item),
        )
    )
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
            env=env,
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
    manifest = PROJECT_ROOT / ".aqsp-release.json"
    immutable_release = manifest.is_file()
    return CheckResult(
        "project root",
        git_dir.is_dir() or immutable_release,
        f"{PROJECT_ROOT} ("
        + (
            "git repo"
            if git_dir.is_dir()
            else "immutable release"
            if immutable_release
            else "not a git repo or release"
        )
        + ")",
    )


def check_python_import() -> CheckResult:
    configured = os.environ.get("AQSP_RUNTIME_PYTHON")
    candidates = [
        Path(configured) if configured else None,
        Path(os.environ.get("AQSP_RUNTIME_VENV_DIR", "")) / "bin" / "python3",
        Path(os.environ.get("AQSP_SHARED_VENV_DIR", "/opt/aqsp-vibe-venv"))
        / "bin"
        / "python3",
        RUNTIME_ROOT.parent / "aqsp-vibe-venv" / "bin" / "python3",
        PROJECT_ROOT / ".venv" / "bin" / "python3",
        Path("python3"),
    ]
    python_bin = next(
        (item for item in candidates if item and item.exists()), Path("python3")
    )
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
    expected = [
        "daily",
        "intraday",
        "midday",
        "coldstart",
        "variant-refresh",
        "monitor",
        "news",
        "status",
    ]
    missing = [item for item in expected if item not in text]
    return CheckResult(
        "bt_task.sh",
        not missing,
        "missing actions: " + ",".join(missing) if missing else "actions ok",
    )


def check_crontab() -> CheckResult:
    code, output = _run(["crontab", "-l"])
    if code != 0:
        return CheckResult("system crontab", True, output or "crontab unavailable")
    relevant = [
        line
        for line in output.splitlines()
        if line.strip()
        and not line.lstrip().startswith("#")
        and any(term in line for term in LEGACY_CRON_TERMS)
    ]
    if relevant:
        return CheckResult(
            "system crontab",
            False,
            "direct or legacy AQSP system cron entries; production should use BT Panel wrappers only:\n"
            + "\n".join(relevant),
        )
    return CheckResult(
        "system crontab",
        True,
        "no direct/legacy AQSP entries; production schedule should be managed by BT Panel",
    )


def _flock_owner(line: str) -> tuple[str, str] | None:
    try:
        tokens = shlex.split(line)
    except ValueError:
        return None
    try:
        flock_index = tokens.index("flock")
    except ValueError:
        return None
    lock_path = ""
    command = ""
    index = flock_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-c" and index + 1 < len(tokens):
            command = tokens[index + 1]
            break
        if token.startswith("-"):
            index += 1
            continue
        if not lock_path:
            lock_path = token
        index += 1
    if not lock_path or not command:
        return None
    return lock_path, command


def check_cron_lock_collisions() -> CheckResult:
    """Reject one outer flock being reused by different BaoTa task wrappers."""
    code, output = _run(["crontab", "-l"])
    if code != 0:
        return CheckResult("cron outer locks", True, output or "crontab unavailable")
    owners: dict[str, set[str]] = {}
    for line in output.splitlines():
        owner = _flock_owner(line)
        if owner is None:
            continue
        lock_path, command_path = owner
        owners.setdefault(lock_path, set()).add(command_path)
    collisions = {
        lock_path: sorted(commands)
        for lock_path, commands in owners.items()
        if len(commands) > 1
    }
    if collisions:
        detail = "; ".join(
            f"{lock_path} -> {','.join(commands)}"
            for lock_path, commands in sorted(collisions.items())
        )
        return CheckResult(
            "cron outer locks",
            False,
            "different tasks share one flock and can suppress each other: " + detail,
        )
    return CheckResult("cron outer locks", True, "no cross-task flock collisions")


def _scheduled_action_sources(
    crontab: str, read_wrapper: Callable[[Path], str | None]
) -> dict[str, set[str]]:
    sources: dict[str, set[str]] = {}
    action_pattern = re.compile(
        r"(?:release_task_entrypoint|bt_task)\.sh\s+("
        + "|".join(sorted(SCHEDULED_ACTIONS))
        + r")\b"
    )
    for line in crontab.splitlines():
        owner = _flock_owner(line)
        if owner is None:
            continue
        try:
            wrapper_command = shlex.split(owner[1])
        except ValueError:
            wrapper_command = []
        wrapper_tokens = [
            token
            for token in wrapper_command
            if not token.startswith("-") and Path(token).name not in {"bash", "sh"}
        ]
        if not wrapper_tokens:
            continue
        wrapper = Path(wrapper_tokens[0])
        text = read_wrapper(wrapper)
        if text is None:
            continue
        for action in action_pattern.findall(text):
            sources.setdefault(action, set()).add(str(wrapper))
    return sources


def _scheduled_actions(
    crontab: str, read_wrapper: Callable[[Path], str | None]
) -> set[str]:
    return set(_scheduled_action_sources(crontab, read_wrapper))


def check_bt_panel_actions() -> CheckResult:
    code, output = _run(["crontab", "-l"])
    if code != 0:
        return CheckResult("BT Panel actions", True, output or "crontab unavailable")

    def read_wrapper(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    actions = _scheduled_actions(output, read_wrapper)
    if not actions:
        return CheckResult(
            "BT Panel actions", True, "no readable BT Panel wrappers in system crontab"
        )
    missing = sorted(REQUIRED_SCHEDULED_ACTIONS - actions)
    return CheckResult(
        "BT Panel actions",
        not missing,
        "scheduled actions: "
        + ",".join(sorted(actions))
        + ("; missing: " + ",".join(missing) if missing else ""),
    )


def check_duplicate_bt_panel_actions() -> CheckResult:
    """Reject duplicate BaoTa wrappers that launch the same heavy action."""
    code, output = _run(["crontab", "-l"])
    if code != 0:
        return CheckResult(
            "BT Panel duplicate actions", True, output or "crontab unavailable"
        )

    def read_wrapper(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    sources = _scheduled_action_sources(output, read_wrapper)
    duplicates = {
        action: sorted(wrappers)
        for action, wrappers in sources.items()
        if len(wrappers) > 1 and action not in MULTI_WINDOW_ACTIONS
    }
    if duplicates:
        detail = "; ".join(
            f"{action} -> {','.join(wrappers)}"
            for action, wrappers in sorted(duplicates.items())
        )
        return CheckResult(
            "BT Panel duplicate actions",
            False,
            "same action is scheduled by multiple wrappers: " + detail,
        )
    return CheckResult("BT Panel duplicate actions", True, "no duplicate task actions")


def _bt_wrapper_actions(text: str) -> set[str]:
    """Extract real scheduler commands, ignoring wrapper comments."""
    return {
        action
        for line in text.splitlines()
        if not line.lstrip().startswith("#")
        for action in BT_ACTION_PATTERN.findall(line)
    }


def check_bt_panel_wrapper_integrity(
    cron_dir: Path = BT_CRON_DIR,
    expected_actions: frozenset[str] = REQUIRED_SCHEDULED_ACTIONS,
) -> CheckResult:
    """Detect old or duplicate AQSP BaoTa wrappers before they can overlap."""
    if not cron_dir.is_dir():
        return CheckResult(
            "BT Panel wrapper audit", True, "BT Panel cron dir unavailable"
        )

    action_sources: dict[str, list[Path]] = {}
    legacy_sources: list[Path] = []
    for wrapper in sorted(
        path
        for path in cron_dir.iterdir()
        if path.is_file() and path.suffix not in {".lock", ".log", ".pl"}
    ):
        try:
            text = wrapper.read_text(encoding="utf-8")
        except OSError:
            continue
        active_lines = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        if BT_LEGACY_ENTRY_PATTERN.search(active_lines):
            legacy_sources.append(wrapper)
        for action in _bt_wrapper_actions(text):
            action_sources.setdefault(action, []).append(wrapper)

    if legacy_sources:
        return CheckResult(
            "BT Panel wrapper audit",
            False,
            "legacy direct AQSP task wrapper(s): "
            + ",".join(str(path) for path in legacy_sources),
        )
    duplicates = {
        action: paths
        for action, paths in action_sources.items()
        if len(paths) > 1 and action not in MULTI_WINDOW_ACTIONS
    }
    if duplicates:
        detail = "; ".join(
            f"{action} -> {','.join(str(path) for path in paths)}"
            for action, paths in sorted(duplicates.items())
        )
        return CheckResult(
            "BT Panel wrapper audit",
            False,
            "same action is scheduled by multiple BT wrappers: " + detail,
        )
    if not action_sources:
        return CheckResult(
            "BT Panel wrapper audit",
            not expected_actions,
            "no AQSP BT Panel wrappers"
            if not expected_actions
            else "missing scheduled actions: " + ",".join(sorted(expected_actions)),
        )
    missing = sorted(expected_actions - set(action_sources))
    if missing:
        return CheckResult(
            "BT Panel wrapper audit",
            False,
            "missing scheduled actions: " + ",".join(missing),
        )
    return CheckResult(
        "BT Panel wrapper audit",
        True,
        "scheduled actions: " + ",".join(sorted(action_sources)),
    )


def check_logs() -> list[CheckResult]:
    bt_dir = RUNTIME_DATA_ROOT / "logs" / "bt"
    bt_logs = sorted(bt_dir.glob(f"bt-*-{TODAY}.log")) if bt_dir.exists() else []
    expected = ("daily", "intraday", "midday", "monitor")
    seen_actions = {
        path.name.removeprefix("bt-").removesuffix(f"-{TODAY}.log") for path in bt_logs
    }
    missing = [action for action in expected if action not in seen_actions]
    results = [
        CheckResult(
            "BT Panel logs",
            bool(bt_logs),
            "actions today: " + ",".join(sorted(seen_actions))
            if bt_logs
            else "no bt logs today yet",
        )
    ]
    if missing:
        results.append(
            CheckResult(
                "BT Panel expected cadence",
                True,
                "not seen today yet: " + ",".join(missing),
            )
        )
    deploy_log = RUNTIME_DATA_ROOT / "logs" / "deploy" / f"sync-{TODAY}.log"
    if (PROJECT_ROOT / ".aqsp-release.json").is_file() and not deploy_log.exists():
        results.append(
            CheckResult(
                str(deploy_log),
                True,
                "not required for an immutable release",
            )
        )
    else:
        results.append(_exists(deploy_log))
    return results


def check_locks() -> list[CheckResult]:
    lock_dir = RUNTIME_LOCK_ROOT
    locks = sorted(lock_dir.glob("*.lock")) if lock_dir.exists() else []
    if not locks:
        return [CheckResult("locks", True, "no active lock directories")]
    results: list[CheckResult] = []
    for lock in locks:
        age = max(0.0, now_shanghai().timestamp() - lock.stat().st_mtime)
        stale = age > 6 * 60 * 60
        info_file = lock / "meta.env"
        runner = "unknown"
        pid = "unknown"
        started_at = "unknown"
        pid_active = False
        if info_file.exists():
            for line in info_file.read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition("=")
                value = value.strip().strip('"').strip("'")
                if key == "LOCK_RUNNER" and value:
                    runner = value
                elif key == "LOCK_PID" and value:
                    pid = value
                elif key == "LOCK_STARTED_AT" and value:
                    started_at = value
            try:
                pid_active = pid.isdigit() and Path(f"/proc/{pid}").exists()
            except OSError:
                pid_active = False
        results.append(
            CheckResult(
                f"lock {lock.name}",
                not stale,
                "runner="
                + runner
                + f" pid={pid} started_at={started_at} age={age / 60:.1f}min "
                + ("pid-active" if pid_active else "pid-missing")
                + (" stale?" if stale else " active/recent"),
            )
        )
    return results


def main() -> int:
    print("AQSP scheduler diagnosis")
    print(f"time: {now_shanghai().isoformat(timespec='seconds')}")
    print(f"project: {PROJECT_ROOT}")
    print()

    scheduler_checks = [
        check_project_root(),
        check_python_import(),
        check_bt_script(),
        check_crontab(),
        check_cron_lock_collisions(),
        check_bt_panel_actions(),
        check_duplicate_bt_panel_actions(),
        check_bt_panel_wrapper_integrity(),
    ]
    checks = [
        *scheduler_checks,
        *check_logs(),
        *check_locks(),
    ]

    has_error = False
    for result in checks:
        marker = "OK" if result.ok else "WARN"
        print(f"[{marker}] {result.label}: {result.detail}")
        has_error = has_error or not result.ok

    strict_schedule_error = any(not result.ok for result in scheduler_checks)
    if _truthy(os.environ.get("AQSP_SCHEDULER_STRICT")) and has_error:
        return 1
    if (
        _truthy(os.environ.get("AQSP_SCHEDULER_STRICT_SCHEDULE"))
        and strict_schedule_error
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
