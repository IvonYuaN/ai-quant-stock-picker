from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_ci_workflow_limits_paths_and_sets_concurrency() -> None:
    text = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "paths:" in text
    assert "cancel-in-progress: true" in text
    assert "group: ci-${{ github.workflow }}-${{ github.ref }}" in text
    assert '"src/**"' in text
    assert '"tests/**"' in text
    assert 'FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"' in text
    assert "actions/checkout@v5" in text
    assert "actions/setup-python@v6" in text


def test_scheduled_workflows_define_concurrency() -> None:
    for rel_path in (
        ".github/workflows/dry-run.yml",
        ".github/workflows/monitor.yml",
        ".github/workflows/scheduled-screen.yml",
    ):
        text = (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")
        assert "concurrency:" in text
        assert "cancel-in-progress: true" in text
        assert 'FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"' in text
        assert "actions/checkout@v5" in text
        assert "actions/setup-python@v6" in text
