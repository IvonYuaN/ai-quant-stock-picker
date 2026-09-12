"""Tests for scripts/deploy_immutable_release.sh.

These are shell-level integration tests (no live prod/runner required):
  * bash -n syntax
  * --help exits 0
  * --dry-run exits 0 and mutates nothing under the configured releases root
  * library mode (AQSP_DEPLOY_LIB=1) unit-tests the two pure helpers that the
    PR #46 upstream script lacked:
      - assert_idle_window(): optional allowed-window rejection
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
    bash = """
set -e
export AQSP_DEPLOY_LIB=1
source "%s"
AQSP_DEPLOY_ALLOWED_WINDOW="00:00-00:01"
if assert_idle_window; then
  echo ALLOWED
else
  echo REJECTED
fi
""" % SCRIPT
    r = subprocess.run(["bash", "-c", bash], capture_output=True, text=True)
    assert r.returncode != 0 or "REJECTED" in r.stdout, r.stdout + r.stderr


@requires_script
def test_idle_window_allows_inside_window():
    bash = """
set -e
export AQSP_DEPLOY_LIB=1
source "%s"
AQSP_DEPLOY_ALLOWED_WINDOW="00:00-23:59"
if assert_idle_window; then
  echo ALLOWED
else
  echo REJECTED
fi
""" % SCRIPT
    r = subprocess.run(["bash", "-c", bash], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "ALLOWED" in r.stdout


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
