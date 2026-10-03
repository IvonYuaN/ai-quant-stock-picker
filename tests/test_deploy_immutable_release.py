"""Tests for scripts/deploy_immutable_release.sh.

These are shell-level integration tests (no live prod/runner required):
  * bash -n syntax
  * --help exits 0
  * --dry-run exits 0 and mutates nothing under the configured releases root
  * library mode (AQSP_DEPLOY_LIB=1) unit-tests the two pure helpers that the
    PR #46 upstream script lacked:
      - assert_idle_window(): optional allowed-window rejection
      - assert_idle_before_switch(): re-checks busyness right before the
        atomic symlink switch (issue #152: a BaoTa task that starts during
        the minutes-long frontend build would run across two releases)
      - inherit_runtime_data(): release-local data/ + reports/ inheritance
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "deploy_immutable_release.sh"
PYTHON_BIN = shutil.which("python3") or "/usr/bin/python3"
NPM_BIN = shutil.which("npm") or "/usr/bin/npm"

requires_script = pytest.mark.skipif(
    not SCRIPT.exists(), reason="deploy script missing"
)


def _pgrep_shim(tmp: Path, *, match_walkforward: bool, match_bt_task: bool) -> Path:
    """Build a fake ``pgrep`` on PATH so the idle guards are hermetic.

    ``assert_idle_window`` / ``assert_idle_before_switch`` decide "busy" purely by
    ``pgrep -f`` against the **live process table** (``[a]qsp walkforward`` /
    ``[b]t_task[.]sh``). In CI that couples these tests to whatever else happens
    to run in the same runner *and* to shard ordering: any co-located test or
    stray process whose cmdline matches flips the guard and fails the
    "nothing running" cases (observed flake: test_idle_window_allows_inside_window).

    This shim replaces ``pgrep`` on PATH with a deterministic stub, so the guards
    test their *logic* (window bounds, busy detection, --force) instead of the
    ambient process table. It matches the real patterns: exit 0 (found) only when
    the corresponding pattern is enabled.
    """
    bindir = tmp / "pgrep_shim_bin"
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "pgrep"
    # The script calls `pgrep -f "[a]qsp walkforward"` / `pgrep -f "[b]t_task[.]sh"`.
    # Match those exact bracketed regex args (as substrings of "$*"), then decide.
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "# deterministic pgrep stub for idle-guard tests\n"
        'args="$*"\n'
        f"match_walkforward={'true' if match_walkforward else 'false'}\n"
        f"match_bt_task={'true' if match_bt_task else 'false'}\n"
        'case "$args" in\n'
        '  *"[a]qsp walkforward"*) [ "$match_walkforward" = true ] && exit 0 || exit 1 ;;\n'
        '  *"[b]t_task[.]sh"*)   [ "$match_bt_task" = true ] && exit 0 || exit 1 ;;\n'
        'esac\n'
        "exit 1\n"
    )
    shim.chmod(0o755)
    return bindir


def _run_with_pgrep_shim(bash: str, tmp: Path, *, match_walkforward: bool, match_bt_task: bool):
    """Run a bash snippet with the pgrep shim first on PATH."""
    bindir = _pgrep_shim(tmp, match_walkforward=match_walkforward, match_bt_task=match_bt_task)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(["bash", "-c", bash], capture_output=True, text=True, env=env)


def _run(args, env_extra=None, cwd=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    env.setdefault("AQSP_RUNTIME_PYTHON", PYTHON_BIN)
    env.setdefault("AQSP_NPM_BIN", NPM_BIN)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(cwd or REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


@requires_script
def test_syntax():
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@requires_script
def test_help_exits_zero():
    r = _run(["--help"])
    assert r.returncode == 0
    assert "usage:" in r.stdout.lower()


@requires_script
def test_dry_run_mutates_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        releases = Path(tmp) / "releases"
        r = _run(
            ["--dry-run", "--ref", "deadbeef", "--target", "prod"],
            env_extra={
                "AQSP_REPO_ROOT": str(REPO_ROOT),
                "AQSP_RELEASES_ROOT": str(releases),
            },
        )
        assert r.returncode == 0, r.stderr
        assert "[dry-run]" in r.stdout
        # dry-run must not create the releases root nor the lock dir
        assert not releases.exists(), "dry-run created releases root (mutation!)"
        assert "git-archive" in r.stdout


@requires_script
def test_idle_window_rejects_outside_window():
    # an impossible window (1 minute at midnight) must reject current time
    bash = (
        """
set -e
export AQSP_DEPLOY_LIB=1
source "%s"
AQSP_DEPLOY_ALLOWED_WINDOW="00:00-00:01"
if assert_idle_window; then
  echo ALLOWED
else
  echo REJECTED
fi
"""
        % SCRIPT
    )
    r = subprocess.run(["bash", "-c", bash], capture_output=True, text=True)
    assert r.returncode != 0 or "REJECTED" in r.stdout, r.stdout + r.stderr


@requires_script
def test_idle_window_allows_inside_window():
    bash = (
        """
set -e
export AQSP_DEPLOY_LIB=1
source "%s"
AQSP_DEPLOY_ALLOWED_WINDOW="00:00-23:59"
if assert_idle_window; then
  echo ALLOWED
else
  echo REJECTED
fi
"""
        % SCRIPT
    )
    # hermetic: pretend no gate/walkforward & no bt_task is running, so this case
    # exercises the *window* logic only (independent of the live process table).
    with tempfile.TemporaryDirectory() as tmp:
        r = _run_with_pgrep_shim(
            bash, Path(tmp), match_walkforward=False, match_bt_task=False
        )
    assert r.returncode == 0, r.stderr
    assert "ALLOWED" in r.stdout


@requires_script
def test_pre_switch_guard_passes_when_nothing_running():
    bash = (
        """
set -e
export AQSP_DEPLOY_LIB=1
source "%s"
AQSP_DEPLOY_SWITCH_GRACE_SECONDS=5
assert_idle_before_switch
echo PRE_SWITCH_OK
"""
        % SCRIPT
    )
    # hermetic: pretend nothing is running so this asserts the guard *passes*
    # when idle, regardless of any real walkforward/bt_task process present.
    with tempfile.TemporaryDirectory() as tmp:
        r = _run_with_pgrep_shim(
            bash, Path(tmp), match_walkforward=False, match_bt_task=False
        )
    assert r.returncode == 0, r.stderr
    assert "PRE_SWITCH_OK" in r.stdout


@requires_script
def test_pre_switch_guard_rejects_while_baota_task_runs():
    """A busy BaoTa task must block the cut-over, not just the start-up check."""
    # hermetic: the pgrep shim reports a running bt_task.sh, so this exercises the
    # guard's busy-detection without depending on a real (racy) sleep process.
    bash = """
set -e
export AQSP_DEPLOY_LIB=1
source "%s"
AQSP_DEPLOY_SWITCH_GRACE_SECONDS=1
if assert_idle_before_switch; then
  echo ALLOWED
else
  echo REJECTED
fi
""" % (SCRIPT,)
    with tempfile.TemporaryDirectory() as tmp:
        r = _run_with_pgrep_shim(bash, Path(tmp), match_walkforward=False, match_bt_task=True)
    assert r.returncode != 0 or "REJECTED" in r.stdout, r.stdout + r.stderr


@requires_script
def test_pre_switch_guard_force_overrides():
    # hermetic: guard would report busy (bt_task), but FORCE=true must override.
    bash = """
set -e
export AQSP_DEPLOY_LIB=1
source "%s"
FORCE=true
AQSP_DEPLOY_SWITCH_GRACE_SECONDS=1
assert_idle_before_switch
echo FORCE_PASSED
""" % (SCRIPT,)
    with tempfile.TemporaryDirectory() as tmp:
        r = _run_with_pgrep_shim(bash, Path(tmp), match_walkforward=False, match_bt_task=True)
    assert r.returncode == 0, r.stderr
    assert "FORCE_PASSED" in r.stdout


@requires_script
def test_inherit_data_copies_current_release():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cur = base / "current"
        new = base / "new"
        (cur / "data").mkdir(parents=True)
        (cur / "reports").mkdir(parents=True)
        (cur / "data" / "f.txt").write_text("hello")
        (cur / "reports" / "r.txt").write_text("report")
        new.mkdir()
        link = base / "cur"
        link.symlink_to(cur)
        bash = """
set -e
export AQSP_DEPLOY_LIB=1
source "%s"
CURRENT_LINK="%s"
inherit_runtime_data "%s"
test -f "%s/data/f.txt" && test -f "%s/reports/r.txt" && echo INHERIT_OK || echo INHERIT_FAIL
""" % (SCRIPT, link, new, new, new)
        r = subprocess.run(["bash", "-c", bash], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert "INHERIT_OK" in r.stdout


@requires_script
def test_inherit_data_skipped_when_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cur = base / "current"
        new = base / "new"
        (cur / "data").mkdir(parents=True)
        (cur / "data" / "f.txt").write_text("hello")
        new.mkdir()
        link = base / "cur"
        link.symlink_to(cur)
        bash = """
set -e
export AQSP_DEPLOY_LIB=1
source "%s"
CURRENT_LINK="%s"
INHERIT_DATA=false
inherit_runtime_data "%s"
test -f "%s/data/f.txt" && echo INHERIT_LEAK || echo INHERIT_SKIPPED
""" % (SCRIPT, link, new, new)
        r = subprocess.run(["bash", "-c", bash], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert "INHERIT_SKIPPED" in r.stdout
