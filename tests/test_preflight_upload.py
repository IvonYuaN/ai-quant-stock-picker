from __future__ import annotations

from scripts import preflight_upload
from scripts.preflight_upload import (
    UploadFinding,
    _git_lines,
    check_upload_candidates,
    find_untracked_runtime_paths,
)


def test_preflight_blocks_runtime_private_artifacts() -> None:
    findings = check_upload_candidates(
        [
            "src/aqsp/cli.py",
            "private_data/tdx/sh/lday/sh600519.day",
            "data/debate_results.jsonl",
            "data/ledger.jsonl",
            "data/llm_calls.jsonl",
            "data/new_runtime_dump.jsonl",
            "data/source_health.json",
            "data/predictions.jsonl",
            "data/temp_cache.db",
            "reports/custom.html",
            "reports/custom.txt",
            "reports/latest.md",
        ]
    )

    assert sorted(findings, key=lambda item: item.path) == [
        UploadFinding(
            "data/debate_results.jsonl",
            "forbidden runtime/private artifact",
        ),
        UploadFinding(
            "data/ledger.jsonl",
            "forbidden runtime/private artifact",
        ),
        UploadFinding(
            "data/llm_calls.jsonl",
            "forbidden runtime/private artifact",
        ),
        UploadFinding(
            "data/new_runtime_dump.jsonl",
            "forbidden runtime/private artifact",
        ),
        UploadFinding(
            "data/predictions.jsonl",
            "forbidden runtime/private artifact",
        ),
        UploadFinding(
            "data/source_health.json",
            "forbidden runtime/private artifact",
        ),
        UploadFinding("data/temp_cache.db", "forbidden runtime/private artifact"),
        UploadFinding(
            "private_data/tdx/sh/lday/sh600519.day",
            "forbidden runtime/private artifact",
        ),
        UploadFinding("reports/custom.html", "forbidden runtime/private artifact"),
        UploadFinding("reports/custom.txt", "forbidden runtime/private artifact"),
        UploadFinding("reports/latest.md", "forbidden runtime/private artifact"),
    ]


def test_preflight_allows_research_registry() -> None:
    assert check_upload_candidates(["data/open_source_research.jsonl"]) == []


def test_preflight_blocks_untracked_runtime_files_but_ignores_tests_and_docs() -> None:
    assert find_untracked_runtime_paths(
        ["src/aqsp/cli.py"],
        [
            "src/aqsp/new_runtime.py",
            "scripts/new_runtime.sh",
            "tests/test_new_runtime.py",
            "docs/runtime-notes.md",
        ],
    ) == ["scripts/new_runtime.sh", "src/aqsp/new_runtime.py"]


def test_preflight_reuses_secret_assignment_scan_for_shell_scripts(
    monkeypatch, tmp_path
) -> None:
    script = tmp_path / "scripts" / "secret_check.sh"
    script.parent.mkdir(parents=True)
    script.write_text('export HT_APIKEY="real-api-key"\n', encoding="utf-8")

    monkeypatch.setattr(preflight_upload, "PROJECT_ROOT", tmp_path)

    assert check_upload_candidates(["scripts/secret_check.sh"]) == [
        UploadFinding(
            "scripts/secret_check.sh",
            str(script) + ":1: non-empty HT_APIKEY",
        )
    ]


def test_git_lines_runs_from_repo_root_when_called_elsewhere(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)

    files = _git_lines(["ls-files", "pyproject.toml"])

    assert files == ["pyproject.toml"]
