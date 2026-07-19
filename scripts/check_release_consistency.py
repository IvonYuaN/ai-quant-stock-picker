#!/usr/bin/env python3
"""Audit Git, the canonical release, runtime overlay, and active entrypoints."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
OLD_ENTRY_TERMS = ("streamlit", "8501", "dist/dashboard")


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str


def _git(root: Path, *args: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        return False, str(exc)
    output = (result.stdout or result.stderr).strip()
    return result.returncode == 0, output


def _remote_head(root: Path, remote_url: str, branch: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", "ls-remote", remote_url, f"refs/heads/{branch}"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    line = next((item for item in result.stdout.splitlines() if item.strip()), "")
    return (True, line.split()[0]) if line else (False, "remote branch not found")


def _read_json(path: Path, label: str, findings: list[Finding]) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        findings.append(Finding("error", "missing_manifest", f"{label} missing: {path}"))
        return None
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(Finding("error", "invalid_manifest", f"{label} invalid: {path}: {exc}"))
        return None
    if not isinstance(value, dict):
        findings.append(Finding("error", "invalid_manifest", f"{label} must be a JSON object: {path}"))
        return None
    return value


def _validate_sha(value: object, pattern: re.Pattern[str], label: str, findings: list[Finding]) -> str:
    text = str(value or "").strip().lower()
    if not pattern.fullmatch(text):
        findings.append(Finding("error", "invalid_identity", f"{label} is not a valid SHA: {value!r}"))
    return text


def _check_overlay(
    release_root: Path,
    overlay_path: Path,
    release_commit: str,
    findings: list[Finding],
) -> None:
    if not overlay_path.exists():
        findings.append(Finding("error", "missing_overlay", f"runtime overlay missing: {overlay_path}"))
        return
    manifest = _read_json(overlay_path, "runtime overlay", findings)
    if manifest is None:
        return
    source = manifest.get("source")
    source_commit = source.get("commit") if isinstance(source, dict) else None
    if str(source_commit or "").lower() != release_commit:
        findings.append(Finding("error", "overlay_source_mismatch", f"overlay source commit {source_commit!r} != release {release_commit}"))
    files = manifest.get("managed_files")
    hashes = manifest.get("file_hashes")
    if not isinstance(files, list) or not files or not isinstance(hashes, dict):
        findings.append(Finding("error", "invalid_overlay", "overlay must contain managed_files and file_hashes"))
        return
    for item in files:
        relative = str(item or "")
        expected = str(hashes.get(relative) or "").lower()
        path = release_root / relative
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            findings.append(Finding("error", "unsafe_overlay_path", f"unsafe overlay path: {relative!r}"))
            continue
        if not SHA64.fullmatch(expected):
            findings.append(Finding("error", "invalid_overlay_hash", f"overlay hash invalid for {relative}"))
            continue
        if not path.is_file():
            findings.append(Finding("error", "overlay_file_missing", f"overlay file missing from release: {relative}"))
            continue
        import hashlib

        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            findings.append(Finding("error", "overlay_hash_mismatch", f"overlay hash mismatch: {relative}"))


def _check_old_entries(root: Path, active_files: list[str], findings: list[Finding]) -> None:
    for relative in active_files:
        path = root / relative
        if not path.is_file():
            findings.append(Finding("error", "active_entry_missing", f"active entry missing: {path}"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for term in OLD_ENTRY_TERMS:
            if term in text:
                findings.append(Finding("error", "legacy_entry_reference", f"{relative} references retired entry {term!r}"))


def audit(
    *,
    project_root: Path,
    runtime_root: Path,
    remote: str,
    branch: str,
    canonical_link: Path | None,
    manifest_path: Path,
    overlay_path: Path,
    active_files: list[str],
    require_overlay: bool,
) -> list[Finding]:
    findings: list[Finding] = []
    project_root = project_root.resolve()
    release_root = project_root
    if canonical_link is not None:
        if not canonical_link.is_symlink():
            findings.append(Finding("error", "canonical_link_missing", f"canonical release is not a symlink: {canonical_link}"))
        else:
            release_root = canonical_link.resolve()
            if release_root != project_root:
                findings.append(Finding("error", "canonical_root_mismatch", f"canonical release {release_root} != checked release {project_root}"))
    manifest = _read_json(manifest_path, "release manifest", findings)
    release_commit = ""
    if manifest is not None:
        release_commit = _validate_sha(manifest.get("commit"), SHA40, "release commit", findings)
        if manifest.get("schema_version") != 1:
            findings.append(Finding("error", "unsupported_manifest", "release manifest schema_version must be 1"))
        if str(manifest.get("branch") or "") != branch:
            findings.append(Finding("error", "branch_mismatch", f"manifest branch {manifest.get('branch')!r} != expected {branch!r}"))
    has_head, head = _git(project_root, "rev-parse", "HEAD")
    if has_head and release_commit and head.lower() != release_commit:
        findings.append(Finding("error", "release_head_mismatch", f"release HEAD {head} != manifest {release_commit}"))
    has_remote, remote_head = _git(project_root, "rev-parse", "--verify", f"refs/remotes/{remote}/{branch}")
    remote_failure_reported = False
    if not has_remote and manifest is not None and not (project_root / ".git").exists():
        remote_url = str(manifest.get("remote_url") or "").strip()
        if remote_url and remote_url != "unknown":
            has_remote, remote_head = _remote_head(project_root, remote_url, branch)
            if not has_remote:
                findings.append(Finding("error", "remote_ref_unavailable", f"remote branch unavailable: {remote_url} {branch}: {remote_head}"))
                remote_failure_reported = True
        else:
            findings.append(Finding("error", "remote_url_missing", "immutable release manifest has no remote_url for GitHub verification"))
            remote_failure_reported = True
    if not has_remote and not remote_failure_reported:
        findings.append(Finding("error", "remote_ref_missing", f"remote ref unavailable: {remote}/{branch}: {remote_head}"))
    elif release_commit and remote_head.lower() != release_commit:
        findings.append(Finding("error", "release_not_published", f"release {release_commit} != GitHub {remote}/{branch} {remote_head}; push/fetch state is not consistent"))
    status_ok, status = _git(project_root, "status", "--porcelain=v1", "--untracked-files=all")
    if status_ok and status:
        findings.append(Finding("error", "release_dirty", f"release worktree is dirty: {status.replace(chr(10), '; ')}"))
    if require_overlay or overlay_path.exists():
        if release_commit:
            _check_overlay(release_root, overlay_path, release_commit, findings)
    _check_old_entries(release_root, active_files, findings)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_release_consistency")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--runtime-root", type=Path, default=Path("/opt/aqsp"))
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--canonical-link", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--overlay", type=Path)
    parser.add_argument("--active-file", action="append", default=[])
    parser.add_argument("--require-overlay", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()
    runtime_root = args.runtime_root.resolve()
    canonical = args.canonical_link
    manifest = args.manifest or project_root / ".aqsp-release.json"
    overlay = args.overlay or runtime_root / ".state/runtime-sync-overlay.json"
    active_files = args.active_file or [
        "scripts/release_task_entrypoint.sh",
        "scripts/bt_task.sh",
    ]
    findings = audit(
        project_root=project_root,
        runtime_root=runtime_root,
        remote=args.remote,
        branch=args.branch,
        canonical_link=canonical,
        manifest_path=manifest,
        overlay_path=overlay,
        active_files=active_files,
        require_overlay=args.require_overlay,
    )
    if args.as_json:
        print(json.dumps([asdict(item) for item in findings], ensure_ascii=False, indent=2))
    elif findings:
        print("Release consistency FAILED:")
        for item in findings:
            print(f"- [{item.code}] {item.message}")
    else:
        print("Release consistency PASSED.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
