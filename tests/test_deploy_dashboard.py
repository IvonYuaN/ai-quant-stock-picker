from __future__ import annotations

import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "deploy_dashboard.sh"


def _fake_transport(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    (fake_bin / "ssh").write_text(
        """#!/usr/bin/env bash
set -eu
if [ "${FAKE_SSH_FAIL:-0}" = "1" ]; then
    exit 255
fi
target=""
command=""
for arg in "$@"; do
    if [ -z "$target" ] && [[ "$arg" == *@* ]]; then
        target="$arg"
        continue
    fi
    if [ -n "$target" ]; then
        command="$arg"
    fi
done
printf '%s\\n' "$command" >> "${FAKE_SSH_LOG:?}"
bash -c "$command"
""",
        encoding="utf-8",
    )
    (fake_bin / "rsync").write_text(
        """#!/usr/bin/env bash
set -eu
if [ "${FAKE_RSYNC_FAIL:-0}" = "1" ]; then
    exit 42
fi
previous=""
last=""
for arg in "$@"; do
    previous="$last"
    last="$arg"
done
source="$previous"
destination="$last"
destination="${destination#*:}"
printf 'RSYNC_DEST=%s\\n' "$destination" >> "${FAKE_RSYNC_LOG:?}"
if [ -d "$source" ]; then
    mkdir -p "${destination%/}"
    cp -R "$source"/. "${destination%/}"/
else
    mkdir -p "$(dirname "$destination")"
    cp "$source" "$destination"
fi
""",
        encoding="utf-8",
    )
    (fake_bin / "curl").write_text(
        """#!/usr/bin/env bash
if [ "${FAKE_CURL_FAIL:-0}" = "1" ]; then
    exit 6
fi
exit 0
""",
        encoding="utf-8",
    )
    for tool in fake_bin.iterdir():
        tool.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "AQSP_DEPLOY_HOST": "deploy.example",
            "AQSP_DEPLOY_USER": "aqsp",
            "AQSP_DEPLOY_CONNECT_TIMEOUT_SECONDS": "1",
            "AQSP_DEPLOY_TRANSFER_TIMEOUT_SECONDS": "2",
            "FAKE_SSH_LOG": str(tmp_path / "ssh.log"),
            "FAKE_RSYNC_LOG": str(tmp_path / "rsync.log"),
            "TMPDIR": str(tmp_path),
        }
    )
    return fake_bin, env


def _run_deploy(
    source: Path, env: dict[str, str], *, timeout: float = 10
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT_PATH), str(source)],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_deploy_dashboard_switches_atomically_and_retains_previous_version(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dist" / "dashboard"
    (source / "assets").mkdir(parents=True)
    (source / "index.html").write_text("new\n", encoding="utf-8")
    (source / "assets" / "app.js").write_text("new asset\n", encoding="utf-8")
    active = tmp_path / "www" / "dashboard"
    active.mkdir(parents=True)
    (active / "index.html").write_text("old\n", encoding="utf-8")

    _, env = _fake_transport(tmp_path)
    env["AQSP_DEPLOY_PATH"] = str(active)
    result = _run_deploy(source, env)

    assert result.returncode == 0, result.stderr
    assert (active / "index.html").read_text(encoding="utf-8") == "new\n"
    backups = list(active.parent.glob(f"{active.name}.aqsp-previous.*"))
    assert len(backups) == 1
    assert (backups[0] / "index.html").read_text(encoding="utf-8") == "old\n"
    rsync_destinations = Path(env["FAKE_RSYNC_LOG"]).read_text(encoding="utf-8")
    assert "RSYNC_DEST=" + str(active) not in rsync_destinations
    assert ".aqsp-dashboard." in rsync_destinations


def test_deploy_dashboard_keeps_active_directory_when_rsync_fails(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dashboard"
    source.mkdir()
    (source / "index.html").write_text("new\n", encoding="utf-8")
    active = tmp_path / "www" / "dashboard"
    active.mkdir(parents=True)
    (active / "index.html").write_text("old\n", encoding="utf-8")

    _, env = _fake_transport(tmp_path)
    env.update({"AQSP_DEPLOY_PATH": str(active), "FAKE_RSYNC_FAIL": "1"})
    result = _run_deploy(source, env)

    assert result.returncode != 0
    assert (active / "index.html").read_text(encoding="utf-8") == "old\n"
    assert not list(active.parent.glob(f"{active.name}.aqsp-previous.*"))


def test_deploy_dashboard_fails_before_remote_mutation_when_ssh_or_tls_is_unavailable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dashboard"
    source.mkdir()
    (source / "index.html").write_text("new\n", encoding="utf-8")
    active = tmp_path / "www" / "dashboard"
    active.mkdir(parents=True)
    (active / "index.html").write_text("old\n", encoding="utf-8")

    _, ssh_env = _fake_transport(tmp_path / "ssh-case")
    ssh_env.update({"AQSP_DEPLOY_PATH": str(active), "FAKE_SSH_FAIL": "1"})
    ssh_result = _run_deploy(source, ssh_env)
    assert ssh_result.returncode != 0
    assert (active / "index.html").read_text(encoding="utf-8") == "old\n"

    _, tls_env = _fake_transport(tmp_path / "tls-case")
    tls_env.update(
        {
            "AQSP_DEPLOY_PATH": str(active),
            "AQSP_DEPLOY_TLS_URL": "https://dashboard.example/health",
            "FAKE_CURL_FAIL": "1",
        }
    )
    tls_result = _run_deploy(source, tls_env)
    assert tls_result.returncode != 0
    assert (active / "index.html").read_text(encoding="utf-8") == "old\n"
    assert not Path(tls_env["FAKE_SSH_LOG"]).exists()
