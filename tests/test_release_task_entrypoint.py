from pathlib import Path
import os
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_release_task_entrypoint_keeps_code_and_runtime_roots_separate() -> None:
    script = (PROJECT_ROOT / "scripts/release_task_entrypoint.sh").read_text(
        encoding="utf-8"
    )

    assert 'export AQSP_PROJECT_ROOT="$RELEASE_ROOT"' in script
    assert 'RUNTIME_ROOT="${AQSP_RUNTIME_ROOT:-/opt/aqsp}"' in script
    assert (
        'RUNTIME_DATA_ROOT="${AQSP_RUNTIME_DATA_ROOT:-${RUNTIME_ROOT}/data}"' in script
    )
    assert 'export AQSP_RUNTIME_DATA_ROOT="$RUNTIME_DATA_ROOT"' in script
    assert "AQSP_NEWS_JSON_OUTPUT" in script
    assert "AQSP_INTRADAY_CURSOR_PATH" in script
    assert "AQSP_AGENT_RUNS_PATH" in script
    assert "AQSP_HOME_SNAPSHOT_PATH" in script
    assert "export_runtime_path AQSP_REPORT reports/latest.md" in script
    assert (
        "export_runtime_path AQSP_DASHBOARD_HTML data/runtime/archive/dashboard/index.html"
        in script
    )
    assert "data/runtime/archive/dashboard/index.html" in script
    assert "dist/dashboard" not in script
    assert "AQSP_RELEASE_MANIFEST" in script
    assert "AQSP_RELEASE_COMMIT" in script
    assert "AQSP_SHARED_VENV_DIR:-/opt/aqsp-vibe-venv" in script
    assert "AQSP_RUNTIME_PYTHON=" in script
    assert 'exec /bin/bash "${RELEASE_ROOT}/scripts/bt_task.sh" "$@"' in script

    bt_task = (PROJECT_ROOT / "scripts/bt_task.sh").read_text(encoding="utf-8")
    assert "AQSP_IMMUTABLE_RELEASE:-false" in bt_task
    assert "Git repo not found: ${PROJECT_ROOT}" in bt_task

    intraday = (PROJECT_ROOT / "scripts/intraday_refresh.sh").read_text(
        encoding="utf-8"
    )
    assert (
        'LOG_DIR="${AQSP_INTRADAY_LOG_DIR:-${RUNTIME_DATA_ROOT}/logs/intraday}"'
        in intraday
    )
    assert (
        'TMP_ROOT="${AQSP_INTRADAY_TMP_ROOT:-${AQSP_RUNTIME_TMP_ROOT:-${RUNTIME_DATA_ROOT}/.tmp}}"'
        in intraday
    )


def test_release_task_entrypoint_does_not_allow_runtime_root_to_replace_code_root() -> (
    None
):
    script = (PROJECT_ROOT / "scripts/release_task_entrypoint.sh").read_text(
        encoding="utf-8"
    )

    assert 'AQSP_PROJECT_ROOT="$RUNTIME_ROOT"' not in script
    assert "runtime output must be under ${RUNTIME_DATA_ROOT}" in script


def test_release_task_entrypoint_maps_relative_runtime_paths_once_to_data(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    runtime = tmp_path / "runtime"
    marker = tmp_path / "env.txt"
    (release / "scripts").mkdir(parents=True)
    (release / ".aqsp-release.json").write_text(
        '{"commit": "0123456789abcdef0123456789abcdef01234567"}\n',
        encoding="utf-8",
    )
    (release / "scripts" / "bt_task.sh").write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "AQSP_LEDGER=$AQSP_LEDGER" "AQSP_REPORT=$AQSP_REPORT" "AQSP_RUNTIME_PYTHON=$AQSP_RUNTIME_PYTHON" "AQSP_BT_LOGS_DIR=$AQSP_BT_LOGS_DIR" "TZ=$TZ" > "$MARKER"\n',
        encoding="utf-8",
    )
    (release / "scripts" / "bt_task.sh").chmod(0o755)
    env = {
        **os.environ,
        "AQSP_RELEASE_ROOT": str(release),
        "AQSP_RUNTIME_ROOT": str(runtime),
        "AQSP_RUNTIME_DATA_ROOT": str(runtime / "data"),
        "MARKER": str(marker),
    }

    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "release_task_entrypoint.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8").splitlines() == [
        f"AQSP_LEDGER={runtime / 'data' / 'predictions.jsonl'}",
        f"AQSP_REPORT={runtime / 'data' / 'reports' / 'latest.md'}",
        "AQSP_RUNTIME_PYTHON=/opt/aqsp-vibe-venv/bin/python3",
        f"AQSP_BT_LOGS_DIR={runtime / 'data' / 'logs' / 'bt'}",
        "TZ=Asia/Shanghai",
    ]


def test_release_task_entrypoint_replaces_legacy_release_python(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    runtime = tmp_path / "runtime"
    marker = tmp_path / "env.txt"
    (release / "scripts").mkdir(parents=True)
    (release / ".aqsp-release.json").write_text(
        '{"commit": "0123456789abcdef0123456789abcdef01234567"}\n',
        encoding="utf-8",
    )
    (release / "scripts" / "bt_task.sh").write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "AQSP_RUNTIME_PYTHON=$AQSP_RUNTIME_PYTHON" "AQSP_IMMUTABLE_RELEASE=$AQSP_IMMUTABLE_RELEASE" > "$MARKER"\n',
        encoding="utf-8",
    )
    (release / "scripts" / "bt_task.sh").chmod(0o755)
    env = {
        **os.environ,
        "AQSP_RELEASE_ROOT": str(release),
        "AQSP_RUNTIME_ROOT": str(runtime),
        "AQSP_RUNTIME_DATA_ROOT": str(runtime / "data"),
        "AQSP_RUNTIME_PYTHON": str(release / ".venv-vibe-research" / "bin" / "python3"),
        "AQSP_IMMUTABLE_RELEASE": "false",
        "MARKER": str(marker),
    }

    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "release_task_entrypoint.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8").splitlines() == [
        "AQSP_RUNTIME_PYTHON=/opt/aqsp-vibe-venv/bin/python3",
        "AQSP_IMMUTABLE_RELEASE=true",
    ]


def test_bt_task_uses_runtime_log_directory_when_provided() -> None:
    script = (PROJECT_ROOT / "scripts" / "bt_task.sh").read_text(encoding="utf-8")

    assert 'LOG_DIR="${AQSP_BT_LOGS_DIR:-${RUNTIME_DATA_ROOT}/logs/bt}"' in script
    assert 'LOCK_DIR="${AQSP_RUNTIME_LOCK_DIR:-${RUNTIME_DATA_ROOT}/.locks}"' in script


def test_release_task_entrypoint_rejects_relative_runtime_path_escape(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    runtime = tmp_path / "runtime"
    (release / "scripts").mkdir(parents=True)
    (release / "scripts" / "bt_task.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
    )
    (release / "scripts" / "bt_task.sh").chmod(0o755)
    env = {
        **os.environ,
        "AQSP_RELEASE_ROOT": str(release),
        "AQSP_RUNTIME_ROOT": str(runtime),
        "AQSP_RUNTIME_DATA_ROOT": str(runtime / "data"),
        "AQSP_REPORT": "../release/reports/latest.md",
    }

    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "release_task_entrypoint.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "runtime output must be under" in result.stderr
