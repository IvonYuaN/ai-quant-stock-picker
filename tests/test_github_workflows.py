from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = PROJECT_ROOT / ".github" / "workflows"


def _workflow_files(workflow_dir: Path = WORKFLOW_DIR) -> list[Path]:
    return sorted(
        path for pattern in ("*.yml", "*.yaml") for path in workflow_dir.glob(pattern)
    )


def test_github_workflow_scan_includes_yaml_extensions(tmp_path: Path) -> None:
    (tmp_path / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    (tmp_path / "monitor.yaml").write_text("name: Monitor\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("ignored\n", encoding="utf-8")

    assert [path.name for path in _workflow_files(tmp_path)] == [
        "ci.yml",
        "monitor.yaml",
    ]


def test_ci_workflow_limits_paths_and_sets_concurrency() -> None:
    text = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "paths:" in text
    assert "group: ci-${{ github.workflow }}-${{ github.ref }}" in text
    assert '"src/**"' in text
    assert '"tests/**"' in text
    assert '"test/**"' in text
    assert '".github/workflows/**"' in text
    assert '".github/workflows/ci.yml"' not in text


def test_ci_runs_upload_preflight_before_install() -> None:
    text = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert 'pip install -e ".[data,dev,web,api]"' in text
    assert "actions/setup-node@v4" in text
    assert "npm ci --prefix frontend" in text
    assert "npm run build --prefix frontend" in text
    assert "timeout-minutes: 40" in text
    assert "shard: [0, 1, 2, 3]" in text
    assert "PYTEST_TOTAL_SHARDS: 4" in text
    assert "$(cat /tmp/aqsp-test-files.txt)" in text
    assert "python3 -m scripts.preflight_upload" in text
    assert text.index("python3 -m scripts.preflight_upload") < text.index(
        "name: Install"
    )


def test_github_workflows_define_common_runtime_controls() -> None:
    offenders: list[str] = []

    for path in _workflow_files():
        text = path.read_text(encoding="utf-8")
        rel_path = path.relative_to(PROJECT_ROOT)
        for required in (
            "concurrency:",
            "cancel-in-progress: true",
            'FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"',
            "actions/checkout@v5",
            "actions/setup-python@v6",
        ):
            if required not in text:
                offenders.append(f"{rel_path}:missing {required}")

    assert offenders == []


def test_github_workflows_disable_checkout_persisted_credentials() -> None:
    offenders: list[str] = []

    for path in _workflow_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        rel_path = path.relative_to(PROJECT_ROOT)
        for index, line in enumerate(lines):
            if "uses: actions/checkout@" not in line:
                continue
            block = "\n".join(lines[index : index + 5])
            if "persist-credentials: false" not in block:
                offenders.append(f"{rel_path}:{index + 1}")

    assert offenders == []


def test_github_workflows_only_use_allowlisted_actions() -> None:
    allowed_prefixes = (
        "actions/checkout@",
        "actions/setup-python@",
        "actions/setup-node@",
    )
    offenders: list[str] = []

    for path in _workflow_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        rel_path = path.relative_to(PROJECT_ROOT)
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped.startswith("- uses: "):
                continue
            action = stripped.removeprefix("- uses: ").strip()
            if not action.startswith(allowed_prefixes):
                offenders.append(f"{rel_path}:{index + 1}:{action}")

    assert offenders == []


def test_github_workflows_do_not_upload_runtime_artifacts() -> None:
    offenders: list[str] = []

    for path in _workflow_files():
        text = path.read_text(encoding="utf-8").lower()
        if "upload-artifact" in text or "download-artifact" in text:
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert offenders == []


def test_github_workflows_do_not_run_browser_or_screenshot_collection() -> None:
    forbidden_terms = (
        "screenshot",
        "playwright",
        "chromium",
        "chrome",
        "remote-debugging",
        "headless_dashboard_check",
    )
    offenders: list[str] = []

    for path in _workflow_files():
        text = path.read_text(encoding="utf-8").lower()
        for term in forbidden_terms:
            if term in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{term}")

    assert offenders == []


def test_github_workflows_keep_repository_permissions_read_only() -> None:
    forbidden_terms = (
        "contents: write",
        "write-all",
        "actions: write",
        "checks: write",
        "id-token: write",
        "issues: write",
        "pull-requests: write",
    )
    offenders: list[str] = []

    for path in _workflow_files():
        text = path.read_text(encoding="utf-8").lower()
        rel_path = str(path.relative_to(PROJECT_ROOT))
        if "permissions:" not in text or "contents: read" not in text:
            offenders.append(f"{rel_path}:missing-contents-read")
        for term in forbidden_terms:
            if term in text:
                offenders.append(f"{rel_path}:{term}")

    assert offenders == []


def test_github_workflows_do_not_use_privileged_triggers() -> None:
    forbidden_terms = (
        "pull_request_target:",
        "workflow_run:",
    )
    offenders: list[str] = []

    for path in _workflow_files():
        text = path.read_text(encoding="utf-8").lower()
        for term in forbidden_terms:
            if term in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{term}")

    assert offenders == []


def test_pull_request_workflows_do_not_reference_secrets() -> None:
    offenders: list[str] = []

    for path in _workflow_files():
        text = path.read_text(encoding="utf-8")
        if "pull_request:" not in text:
            continue
        if "${{ secrets." in text:
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert offenders == []


def test_monitor_workflow_does_not_send_notifications_from_ephemeral_runner() -> None:
    text = (WORKFLOW_DIR / "monitor.yml").read_text(encoding="utf-8")

    assert "aqsp monitor --notify" not in text
    assert "${{ secrets." not in text


def test_scheduled_screen_workflow_is_manual_report_only() -> None:
    text = (WORKFLOW_DIR / "scheduled-screen.yml").read_text(encoding="utf-8")

    assert "schedule:" not in text
    assert "--notify" not in text
    assert "${{ secrets." not in text


def test_github_workflow_jobs_define_timeouts() -> None:
    offenders: list[str] = []

    for path in _workflow_files():
        text = path.read_text(encoding="utf-8")
        if "timeout-minutes:" not in text:
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert offenders == []


def test_github_workflow_cron_entries_document_beijing_time() -> None:
    offenders: list[str] = []

    for path in _workflow_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        rel_path = path.relative_to(PROJECT_ROOT)
        for index, line in enumerate(lines):
            if "cron:" not in line:
                continue
            context = "\n".join(lines[max(0, index - 2) : index + 1])
            if "北京时间" not in context:
                offenders.append(f"{rel_path}:{index + 1}")

    assert offenders == []
