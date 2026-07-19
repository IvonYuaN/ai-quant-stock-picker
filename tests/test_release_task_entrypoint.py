from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_release_task_entrypoint_keeps_code_and_runtime_roots_separate() -> None:
    script = (PROJECT_ROOT / "scripts/release_task_entrypoint.sh").read_text(
        encoding="utf-8"
    )

    assert 'export AQSP_PROJECT_ROOT="$RELEASE_ROOT"' in script
    assert 'RUNTIME_ROOT="${AQSP_RUNTIME_ROOT:-/opt/aqsp}"' in script
    assert 'AQSP_NEWS_JSON_OUTPUT' in script
    assert 'AQSP_INTRADAY_CURSOR_PATH' in script
    assert 'AQSP_HOME_SNAPSHOT_PATH' in script
    assert 'AQSP_RELEASE_MANIFEST' in script
    assert 'AQSP_RELEASE_COMMIT' in script
    assert 'exec /bin/bash "${RELEASE_ROOT}/scripts/bt_task.sh" "$@"' in script

    bt_task = (PROJECT_ROOT / "scripts/bt_task.sh").read_text(encoding="utf-8")
    assert 'AQSP_IMMUTABLE_RELEASE:-false' in bt_task
    assert 'Git repo not found: ${PROJECT_ROOT}' in bt_task


def test_release_task_entrypoint_does_not_allow_runtime_root_to_replace_code_root() -> None:
    script = (PROJECT_ROOT / "scripts/release_task_entrypoint.sh").read_text(
        encoding="utf-8"
    )

    assert 'AQSP_PROJECT_ROOT="$RUNTIME_ROOT"' not in script
