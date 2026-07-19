#!/usr/bin/env python3
"""Write a reproducible identity manifest for an immutable AQSP release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


HEX40 = set("0123456789abcdef")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _commit(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 40 or set(normalized) - HEX40:
        raise ValueError("commit must be a 40-character hexadecimal SHA")
    return normalized


def _tracked_files(root: Path) -> list[str]:
    output = _git(root, "ls-files", "-z")
    return sorted(item for item in output.split("\0") if item)


def _content_digest(root: Path, files: list[str]) -> str:
    digest = hashlib.sha256()
    for relative in files:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"tracked release file is missing: {relative}")
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def build_manifest(
    root: Path,
    *,
    commit: str | None = None,
    branch: str | None = None,
    remote: str | None = None,
    remote_url: str | None = None,
) -> dict[str, object]:
    root = root.resolve()
    has_git = (root / ".git").exists()
    if has_git:
        status = _git(root, "status", "--porcelain", "--untracked-files=all")
        if status:
            raise ValueError(f"worktree is dirty; refuse to stamp release: {status!r}")
    if commit is None:
        if not has_git:
            raise ValueError("--commit is required when release root has no .git")
        commit = _git(root, "rev-parse", "HEAD")
    commit = _commit(commit)
    if has_git:
        actual = _commit(_git(root, "rev-parse", "HEAD"))
        if actual != commit:
            raise ValueError(f"requested commit {commit} differs from HEAD {actual}")
        tree = _git(root, "rev-parse", "HEAD^{tree}")
        branch = branch or _git(root, "branch", "--show-current") or "detached"
        remote = remote or "origin"
        remote_url = remote_url or _git(root, "config", "--get", f"remote.{remote}.url")
        files = _tracked_files(root)
    else:
        tree = "unavailable"
        branch = branch or "immutable-release"
        remote = remote or "unknown"
        remote_url = remote_url or "unknown"
        files = []
    return {
        "schema_version": 1,
        "commit": commit,
        "tree": tree,
        "branch": branch,
        "remote": remote,
        "remote_url": remote_url,
        "release_root": str(root),
        "file_count": len(files),
        "content_digest": _content_digest(root, files) if files else "unverified",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_manifest(manifest: dict[str, object], output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="write_release_manifest")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--branch")
    parser.add_argument("--remote")
    parser.add_argument("--remote-url")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output = args.output or root / ".aqsp-release.json"
    try:
        manifest = build_manifest(
            root,
            commit=args.commit,
            branch=args.branch,
            remote=args.remote,
            remote_url=args.remote_url,
        )
        write_manifest(manifest, output)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"release manifest failed: {exc}", flush=True)
        return 1
    print(f"release manifest written: {output.resolve()}")
    print(f"release commit: {manifest['commit']}")
    print(f"release content digest: {manifest['content_digest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
