from __future__ import annotations

import os
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_fake_server_status_runtime(tmp_path: Path) -> tuple[Path, Path]:
    project_root = tmp_path / "project"
    fake_bin = tmp_path / "bin"
    (project_root / ".venv" / "bin").mkdir(parents=True)
    (project_root / "src" / "aqsp").mkdir(parents=True)
    (project_root / "scripts").mkdir()
    fake_bin.mkdir()

    (project_root / "src" / "aqsp" / "cli.py").write_text(
        "# fake cli\n", encoding="utf-8"
    )
    for name in (
        "check_before_live.py",
        "remote_runtime_probe.py",
        "check_release_consistency.py",
    ):
        (project_root / "scripts" / name).write_text("# fake check\n", encoding="utf-8")

    fake_python = """#!/usr/bin/env bash
case "$*" in
  *diagnose_runtime.py) exit "${FAKE_RUNTIME_EXIT:-0}" ;;
  *aqsp\\ doctor*) printf 'doctor output\\n' ; exit "${FAKE_DOCTOR_EXIT:-0}" ;;
  *check_before_live.py*) printf 'before-live output\\n' ; exit "${FAKE_BEFORE_LIVE_EXIT:-0}" ;;
  *remote_runtime_probe.py*) printf 'remote probe output\\n' ; exit "${FAKE_REMOTE_PROBE_EXIT:-0}" ;;
  *check_release_consistency.py*) printf 'release consistency output\\n' ; exit "${FAKE_RELEASE_EXIT:-0}" ;;
esac
printf 'unexpected fake python args: %s\\n' "$*" >&2
exit 42
"""
    for path in (project_root / ".venv" / "bin" / "python3", fake_bin / "python3"):
        path.write_text(fake_python, encoding="utf-8")
        path.chmod(0o755)

    fake_git = """#!/usr/bin/env bash
case "${1:-}" in
  log) printf 'fake commit\\n' ;;
  status) exit 0 ;;
  *) exit 0 ;;
esac
"""
    git_path = fake_bin / "git"
    git_path.write_text(fake_git, encoding="utf-8")
    git_path.chmod(0o755)
    return project_root, fake_bin


def _run_fake_server_status(
    project_root: Path,
    fake_bin: Path,
    **overrides: str,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "AQSP_PROJECT_ROOT": str(project_root),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        **overrides,
    }
    return subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "server_status.sh")],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_server_status_script_covers_runtime_sections() -> None:
    script = (PROJECT_ROOT / "scripts" / "server_status.sh").read_text(encoding="utf-8")

    assert 'print_section "GIT"' in script
    assert 'print_section "RELEASE CONSISTENCY"' in script
    assert "check_release_consistency.py" in script
    assert "AQSP_RELEASE_MANIFEST" in script
    assert "git status --short --untracked-files=no" in script
    assert "untracked runtime files:" in script
    assert 'print_section "CRON"' in script
    assert 'print_section "CRON AQSP AUDIT"' in script
    assert 'print_section "LOCKS"' in script
    assert 'print_section "ARTIFACTS"' in script
    assert 'print_section "RUNTIME"' in script
    assert 'print_section "DOCTOR"' in script
    assert 'print_section "BEFORE LIVE"' in script
    assert 'print_section "REMOTE PROBE"' in script
    assert 'print_section "DEPLOY LOG"' in script
    assert 'print_section "INTRADAY LOG"' in script
    assert 'print_section "DAILY LOG"' in script
    assert "scripts/diagnose_runtime.py" in script
    assert "scripts/check_before_live.py" in script
    assert "scripts/remote_runtime_probe.py" in script
    assert "server-runtime.lock" in script
    assert "server-monitor.lock" in script
    assert "pid-active" in script
    assert "direct-aqsp-cron-needs-review" in script
    assert "bt-wrapper action=" in script
    assert 'cron="' in script
    assert 'gate="' in script
    assert 'days="' in script
    assert 'env="' in script
    assert "AQSP_[A-Z0-9_]+=" in script
    assert "Mon-Fri" in script
    assert "Sat-Sun" in script
    assert "script:Mon-Fri" in script
    assert "script:09:35-11:30/13:05-14:57" in script
    assert "script:11:35-12:30" in script
    assert "for action in intraday midday daily coldstart monitor news; do" in script
    assert "bt-status-" not in script
    assert "found_direct" in script
    assert 'echo "none"' in script
    assert 'python3" -m aqsp doctor' in script or "-m aqsp doctor" in script


def test_server_status_propagates_critical_check_failures_after_showing_all_sections(
    tmp_path: Path,
) -> None:
    project_root, fake_bin = _write_fake_server_status_runtime(tmp_path)

    result = _run_fake_server_status(
        project_root,
        fake_bin,
        FAKE_DOCTOR_EXIT="11",
        FAKE_BEFORE_LIVE_EXIT="12",
        FAKE_REMOTE_PROBE_EXIT="23",
        FAKE_RUNTIME_EXIT="17",
    )

    assert result.returncode == 11
    output = result.stdout + result.stderr
    assert "===== DOCTOR =====" in output
    assert "===== BEFORE LIVE =====" in output
    assert "===== REMOTE PROBE =====" in output
    assert "===== DEPLOY LOG =====" in output
    assert "===== DAILY LOG =====" in output
    assert "RELEASE CONSISTENCY" in output
    assert "critical check failed: check_before_live (exit=12)" in output
    assert "critical check failed: remote_runtime_probe (exit=23)" in output
    assert "critical checks failed; server status exit=11" in output


def test_server_status_does_not_block_on_optional_runtime_diagnosis_failure(
    tmp_path: Path,
) -> None:
    project_root, fake_bin = _write_fake_server_status_runtime(tmp_path)
    diagnose_runtime = project_root / "scripts" / "diagnose_runtime.py"
    diagnose_runtime.write_text("# fake runtime diagnosis\n", encoding="utf-8")

    result = _run_fake_server_status(
        project_root,
        fake_bin,
        FAKE_RUNTIME_EXIT="17",
    )

    assert result.returncode == 0
    output = result.stdout + result.stderr
    assert "===== RUNTIME =====" in output
    assert "===== REMOTE PROBE =====" in output
    assert "critical checks failed" not in output
