#!/usr/bin/env python3
"""Backup and sync selected AQSP runtime files to the server.

This is intentionally narrower than a git reset/pull:
- it only touches an explicit file list
- it creates a remote tar backup first
- it verifies per-file SHA256 after extraction
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE_ROOT = "/opt/aqsp"
DEFAULT_BACKUP_DIR = "/opt/aqsp/runtime-backups"


@dataclass(frozen=True)
class SyncPlan:
    ssh_target: str
    remote_root: str
    backup_dir: str
    files: tuple[str, ...]


def _normalize_files(project_root: Path, raw_files: list[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in raw_files:
        text = str(raw or "").strip()
        if not text:
            continue
        candidate = Path(text)
        path = candidate if candidate.is_absolute() else project_root / candidate
        resolved = path.resolve(strict=True)
        try:
            relative = resolved.relative_to(project_root)
        except ValueError as exc:
            raise SystemExit(f"file outside project root: {text}") from exc
        relative_text = relative.as_posix()
        if relative_text not in normalized:
            normalized.append(relative_text)
    if not normalized:
        raise SystemExit("no files selected")
    return tuple(normalized)


def _backup_name(files: tuple[str, ...]) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256("\n".join(files).encode("utf-8")).hexdigest()[:12]
    return f"runtime-sync-{stamp}-{digest}.tar.gz"


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )


def _ssh(ssh_target: str, remote_command: str) -> subprocess.CompletedProcess[str]:
    return _run(["ssh", ssh_target, remote_command])


def _create_local_archive(project_root: Path, files: tuple[str, ...]) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="aqsp-runtime-sync-", suffix=".tar.gz", delete=False)
    archive_path = Path(handle.name)
    handle.close()
    with tarfile.open(archive_path, "w:gz") as tar:
        for relative in files:
            tar.add(project_root / relative, arcname=relative)
    return archive_path


def _remote_existing_files(plan: SyncPlan) -> list[str]:
    quoted = " ".join(shlex.quote(item) for item in plan.files)
    command = (
        f"cd {shlex.quote(plan.remote_root)} && "
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "files = [\n"
        + "".join(f"    {relative!r},\n" for relative in plan.files)
        + "]\n"
        "for item in files:\n"
        "    if Path(item).exists():\n"
        "        print(item)\n"
        "PY"
    )
    del quoted
    result = _ssh(plan.ssh_target, command)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _backup_remote_files(plan: SyncPlan, existing_files: list[str]) -> str:
    backup_name = _backup_name(plan.files)
    if not existing_files:
        return f"{plan.backup_dir}/{backup_name} (no existing files)"
    quoted_files = " ".join(shlex.quote(item) for item in existing_files)
    command = (
        f"mkdir -p {shlex.quote(plan.backup_dir)} && "
        f"cd {shlex.quote(plan.remote_root)} && "
        f"tar -czf {shlex.quote(plan.backup_dir + '/' + backup_name)} {quoted_files}"
    )
    _ssh(plan.ssh_target, command)
    return f"{plan.backup_dir}/{backup_name}"


def _upload_and_extract(plan: SyncPlan, archive_path: Path) -> None:
    remote_tmp = f"/tmp/{archive_path.name}"
    _run(["scp", str(archive_path), f"{plan.ssh_target}:{remote_tmp}"])
    command = (
        f"mkdir -p {shlex.quote(plan.remote_root)} && "
        f"tar -xzf {shlex.quote(remote_tmp)} -C {shlex.quote(plan.remote_root)} && "
        f"rm -f {shlex.quote(remote_tmp)}"
    )
    _ssh(plan.ssh_target, command)


def _local_hashes(project_root: Path, files: tuple[str, ...]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in files:
        hashes[relative] = hashlib.sha256((project_root / relative).read_bytes()).hexdigest()
    return hashes


def _remote_hashes(plan: SyncPlan) -> dict[str, str]:
    command = (
        f"cd {shlex.quote(plan.remote_root)} && "
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "import hashlib\n"
        "files = [\n"
        + "".join(f"    {relative!r},\n" for relative in plan.files)
        + "]\n"
        "for item in files:\n"
        "    path = Path(item)\n"
        "    if not path.exists():\n"
        "        print(f'{item}\\tMISSING')\n"
        "        continue\n"
        "    digest = hashlib.sha256(path.read_bytes()).hexdigest()\n"
        "    print(f'{item}\\t{digest}')\n"
        "PY"
    )
    result = _ssh(plan.ssh_target, command)
    hashes: dict[str, str] = {}
    for line in result.stdout.splitlines():
        text = line.strip()
        if not text or "\t" not in text:
            continue
        relative, digest = text.split("\t", 1)
        hashes[relative] = digest
    return hashes


def sync_files(plan: SyncPlan) -> dict[str, object]:
    archive_path = _create_local_archive(PROJECT_ROOT, plan.files)
    try:
        existing_files = _remote_existing_files(plan)
        backup_path = _backup_remote_files(plan, existing_files)
        _upload_and_extract(plan, archive_path)
        local_hashes = _local_hashes(PROJECT_ROOT, plan.files)
        remote_hashes = _remote_hashes(plan)
    finally:
        archive_path.unlink(missing_ok=True)
    mismatches = {
        relative: {
            "local": local_hashes[relative],
            "remote": remote_hashes.get(relative, "MISSING"),
        }
        for relative in plan.files
        if remote_hashes.get(relative) != local_hashes[relative]
    }
    return {
        "ssh_target": plan.ssh_target,
        "remote_root": plan.remote_root,
        "backup_path": backup_path,
        "files": list(plan.files),
        "verified": not mismatches,
        "mismatches": mismatches,
    }


def build_plan(args: argparse.Namespace) -> SyncPlan:
    return SyncPlan(
        ssh_target=str(args.ssh_target or "").strip() or "aqsp-server",
        remote_root=str(args.remote_root or "").strip() or DEFAULT_REMOTE_ROOT,
        backup_dir=str(args.backup_dir or "").strip() or DEFAULT_BACKUP_DIR,
        files=_normalize_files(PROJECT_ROOT, list(args.files or [])),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-target", default="aqsp-server")
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR)
    parser.add_argument("files", nargs="+")
    args = parser.parse_args(argv)

    plan = build_plan(args)
    result = sync_files(plan)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
