from pathlib import Path
import os
import subprocess
import sys


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
    assert "AQSP_NEWS_JSON_OUTPUT" in script
    assert "AQSP_INTRADAY_CURSOR_PATH" in script
    assert 'cd "$RUNTIME_ROOT"' in script
    assert "AQSP_HOME_SNAPSHOT_PATH" in script
    assert "AQSP_REPORT=" in script
    assert "AQSP_DASHBOARD_HTML=" in script
    assert "AQSP_RELEASE_MANIFEST" in script
    assert "AQSP_RELEASE_COMMIT" in script
    assert 'exec /bin/bash "${RELEASE_ROOT}/scripts/bt_task.sh" "$@"' in script

    bt_task = (PROJECT_ROOT / "scripts/bt_task.sh").read_text(encoding="utf-8")
    assert "AQSP_IMMUTABLE_RELEASE:-false" in bt_task
    assert "Git repo not found: ${PROJECT_ROOT}" in bt_task


def test_release_task_entrypoint_does_not_allow_runtime_root_to_replace_code_root() -> (
    None
):
    script = (PROJECT_ROOT / "scripts/release_task_entrypoint.sh").read_text(
        encoding="utf-8"
    )

    assert 'AQSP_PROJECT_ROOT="$RUNTIME_ROOT"' not in script
    assert "runtime output must be under ${RUNTIME_DATA_ROOT}" in script
    assert "dist/dashboard" not in script


def test_release_task_entrypoint_has_shared_runtime_fallback() -> None:
    script = (PROJECT_ROOT / "scripts/release_task_entrypoint.sh").read_text(
        encoding="utf-8"
    )

    assert 'AQSP_RUNTIME_VENV_DIR="/opt/aqsp-vibe-venv"' in script
    assert '找不到可用 AQSP runtime Python' in script


def test_release_task_entrypoint_maps_relative_runtime_paths_once_to_data(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    runtime = tmp_path / "runtime"
    marker = tmp_path / "env.txt"
    (release / "scripts").mkdir(parents=True)
    (release / "scripts" / "bt_task.sh").write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "AQSP_LEDGER=$AQSP_LEDGER" "AQSP_REPORT=$AQSP_REPORT" > "$MARKER"\n',
        encoding="utf-8",
    )
    (release / "scripts" / "bt_task.sh").chmod(0o755)
    env = {
        **os.environ,
        "AQSP_RELEASE_ROOT": str(release),
        "AQSP_RUNTIME_ROOT": str(runtime),
        "AQSP_RUNTIME_DATA_ROOT": str(runtime / "data"),
        "AQSP_PYTHON": sys.executable,
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
    ]


def test_release_task_entrypoint_loads_private_vibe_runtime_environment(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    runtime = tmp_path / "runtime"
    marker = tmp_path / "env.txt"
    private_env = tmp_path / "vibe-research.env"
    (release / "scripts").mkdir(parents=True)
    (release / "scripts" / "bt_task.sh").write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\n\' "VIBE_RESEARCH_PYTHON_BIN=$VIBE_RESEARCH_PYTHON_BIN" > "$MARKER"\n',
        encoding="utf-8",
    )
    (release / "scripts" / "bt_task.sh").chmod(0o755)
    private_env.write_text(
        "VIBE_RESEARCH_PYTHON_BIN=/opt/aqsp-vibe-venv/bin/python\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "AQSP_RELEASE_ROOT": str(release),
        "AQSP_RUNTIME_ROOT": str(runtime),
        "AQSP_RUNTIME_DATA_ROOT": str(runtime / "data"),
        "AQSP_VIBE_ENV_FILE": str(private_env),
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
        "VIBE_RESEARCH_PYTHON_BIN=/opt/aqsp-vibe-venv/bin/python",
    ]
