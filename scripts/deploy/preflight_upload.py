#!/usr/bin/env python3
"""Preflight checks before pushing this project to GitHub or Gitee."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from collections.abc import Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_no_secrets import TOKEN_PATTERNS, find_non_empty_secret_assignments

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
FORBIDDEN_PREFIXES = (
    "private_data/",
    "A股量化分析数据/",
    "_external/",
    "outputs/",
    "logs/",
    "dist/",
    "data/archive/",
)
FORBIDDEN_EXACT = {
    ".env",
    "data/debate_results.jsonl",
    "data/ledger.jsonl",
    "data/llm_calls.jsonl",
    "data/paper_trades.jsonl",
    "data/predictions.jsonl",
    "data/risk_state.json",
    "data/cache.db",
    "data/source_health.json",
    "data/system_risk_state.json",
    "data/walkforward_gate.json",
    "data/weight_history.jsonl",
    "reports/latest.md",
    "reports/latest.csv",
    "reports/paper.md",
    "reports/runtime-diagnosis.md",
}
ALLOWED_EXACT = {
    "data/open_source_research.jsonl",
}
FORBIDDEN_PATTERNS = (
    "data/*.db",
    "data/*.jsonl",
    "reports/*.csv",
    "reports/*.html",
    "reports/*.md",
    "reports/*.txt",
)
RUNTIME_PATH_PREFIXES = ("src/aqsp/", "scripts/")
RUNTIME_FILE_SUFFIXES = (".py", ".sh")


@dataclass(frozen=True)
class UploadFinding:
    path: str
    reason: str


def _git_lines(args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def upload_candidate_paths() -> list[str]:
    tracked = _git_lines(["ls-files"])
    untracked = _git_lines(["ls-files", "--others", "--exclude-standard"])
    return sorted(set(tracked + untracked))


def find_untracked_runtime_paths(
    tracked_paths: Iterable[str], candidate_paths: Iterable[str]
) -> list[str]:
    """Return runtime files present locally but absent from the Git index."""
    tracked = {str(path) for path in tracked_paths}
    return sorted(
        {
            str(path)
            for path in candidate_paths
            if str(path) not in tracked
            and str(path).startswith(RUNTIME_PATH_PREFIXES)
            and str(path).endswith(RUNTIME_FILE_SUFFIXES)
        }
    )


def untracked_runtime_paths() -> list[str]:
    """Read the repository state and find untracked runtime files."""
    return find_untracked_runtime_paths(
        _git_lines(["ls-files"]),
        _git_lines(["ls-files", "--others", "--exclude-standard"]),
    )


def check_upload_candidates(paths: list[str]) -> list[UploadFinding]:
    findings: list[UploadFinding] = []
    for rel in paths:
        if rel in ALLOWED_EXACT:
            continue
        path = PROJECT_ROOT / rel
        if (
            rel in FORBIDDEN_EXACT
            or any(fnmatch(rel, pattern) for pattern in FORBIDDEN_PATTERNS)
            or any(rel.startswith(p) for p in FORBIDDEN_PREFIXES)
        ):
            findings.append(UploadFinding(rel, "forbidden runtime/private artifact"))
            continue
        if path.exists() and path.is_file() and path.stat().st_size > MAX_UPLOAD_BYTES:
            findings.append(UploadFinding(rel, "file exceeds 5 MiB upload guard"))
            continue
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, pattern in TOKEN_PATTERNS.items():
            if pattern.search(text):
                findings.append(UploadFinding(rel, f"matched secret pattern {name}"))
        for item in find_non_empty_secret_assignments(path, text):
            findings.append(UploadFinding(rel, item))
    return findings


def main() -> int:
    findings = [
        UploadFinding(path, "untracked runtime file; add it before release")
        for path in untracked_runtime_paths()
    ]
    findings.extend(check_upload_candidates(upload_candidate_paths()))
    findings.sort(key=lambda finding: (finding.path, finding.reason))
    if findings:
        print("Upload preflight failed:")
        for finding in findings:
            print(f"- {finding.path}: {finding.reason}")
        return 1
    print("Upload preflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
