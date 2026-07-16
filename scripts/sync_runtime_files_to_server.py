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
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from aqsp.core.time import now_shanghai


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE_ROOT = "/opt/aqsp"
DEFAULT_BACKUP_DIR = "/opt/aqsp/runtime-backups"
DEFAULT_REMOTE_OVERLAY_STATE = ".state/runtime-sync-overlay.json"
DEFAULT_REMOTE_RUNTIME_LOCK = ".locks/server-runtime.lock"
RUNTIME_SYNC_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "scripts/intraday_refresh.sh": ("scripts/merge_intraday_news.py",),
    "scripts/smoke_market_context_runtime.py": (
        "src/aqsp/market_context.py",
        "src/aqsp/portfolio/manager.py",
        "src/aqsp/strategies/thresholds.py",
    ),
    "scripts/daily_pipeline.py": (
        "src/aqsp/portfolio/manager.py",
        "src/aqsp/portfolio/risk_summary.py",
    ),
    "src/aqsp/briefing/generator.py": (
        "src/aqsp/goal_switches.py",
        "src/aqsp/research/price_path.py",
        "config/goal_switches.yaml",
    ),
    "src/aqsp/cli.py": (
        "src/aqsp/data/market_context_source.py",
        "src/aqsp/goal_switches.py",
        "config/goal_switches.yaml",
    ),
    "scripts/write_home_snapshot.py": (
        "src/aqsp/data/market_context_source.py",
        "src/aqsp/market_context.py",
        "src/aqsp/web/data_provider.py",
        "src/aqsp/web/home_snapshot.py",
    ),
    "src/aqsp/data/market_context_source.py": ("src/aqsp/market_context.py",),
    "src/aqsp/config.py": (
        "src/aqsp/goal_switches.py",
        "config/goal_switches.yaml",
    ),
    "src/aqsp/data/news_source.py": ("config/news_sources.yaml",),
    "src/aqsp/data/pit_financial.py": ("src/aqsp/data/pit_policy.py",),
    "src/aqsp/data/source_readiness.py": (
        "src/aqsp/goal_switches.py",
        "config/goal_switches.yaml",
    ),
    "src/aqsp/market_context.py": (
        "src/aqsp/goal_switches.py",
        "config/goal_switches.yaml",
    ),
    "src/aqsp/notify_templates.py": (
        "src/aqsp/goal_switches.py",
        "config/goal_switches.yaml",
    ),
    "src/aqsp/portfolio/manager.py": ("src/aqsp/portfolio/risk_summary.py",),
    "src/aqsp/research/__init__.py": (
        "src/aqsp/research/factor_expression.py",
        "src/aqsp/research/price_path.py",
        "src/aqsp/research/repo_intake.py",
    ),
    "src/aqsp/research/summary.py": ("src/aqsp/research/repo_intake.py",),
    "src/aqsp/strategies/__init__.py": (
        "src/aqsp/strategies/catalog.py",
        "config/strategy_sources.yaml",
    ),
    "src/aqsp/walkforward_gate.py": ("src/aqsp/backtest/audit.py",),
    "src/aqsp/web/dashboard.py": (
        "src/aqsp/config.py",
        "src/aqsp/data/source_readiness.py",
        "src/aqsp/goal_switches.py",
        "src/aqsp/presentation.py",
        "src/aqsp/research/summary.py",
        "src/aqsp/web/archive_safety.py",
        "src/aqsp/web/data_provider.py",
        "src/aqsp/web/home_snapshot.py",
        "config/goal_switches.yaml",
    ),
    "src/aqsp/web/dashboard_beginner.py": (
        "src/aqsp/web/dashboard_beginner_compat.py",
        "src/aqsp/web/data_provider.py",
    ),
    "src/aqsp/web/dashboard_beginner_compat.py": ("src/aqsp/web/data_provider.py",),
}


@dataclass(frozen=True)
class SyncPlan:
    ssh_target: str
    remote_root: str
    backup_dir: str
    files: tuple[str, ...]


def _patch_paths(patch_path: Path) -> tuple[str, ...]:
    """Return the checked, project-relative targets in a unified patch."""
    before: str | None = None
    targets: list[str] = []
    for line in patch_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("--- "):
            before_raw = line[4:].split("\t", 1)[0]
            before = "" if before_raw == "/dev/null" else before_raw.removeprefix("a/")
            continue
        if not line.startswith("+++ "):
            continue
        after = line[4:].split("\t", 1)[0].removeprefix("b/")
        if before is None or (before and before != after) or after == "/dev/null":
            raise ValueError("patch must modify existing files without renames")
        candidate = Path(after)
        if candidate.is_absolute() or ".." in candidate.parts or after == "/dev/null":
            raise ValueError(f"unsafe patch path: {after}")
        if after not in targets:
            targets.append(after)
        before = None
    if not targets:
        raise ValueError("patch has no valid file targets")
    return tuple(targets)


def _transactional_patch_command(
    plan: SyncPlan,
    *,
    remote_patch: str,
    expected_hashes: dict[str, str],
    target_hashes: dict[str, str],
) -> str:
    """Build a single remote transaction with locking and automatic rollback."""
    payload = json.dumps(
        {
            "root": plan.remote_root,
            "backup_dir": plan.backup_dir,
            "patch": remote_patch,
            "files": list(plan.files),
            "expected_hashes": expected_hashes,
            "target_hashes": target_hashes,
            "modules": list(_module_import_targets(plan.files)),
        },
        ensure_ascii=False,
    )
    python = """\
import fcntl
import hashlib
import json
import os
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

payload = json.loads(os.environ['PAYLOAD'])
root = Path(payload['root'])
files = [Path(item) for item in payload['files']]
lock_path = root / '.state' / 'runtime-patch.lock'
lock_path.parent.mkdir(parents=True, exist_ok=True)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

with lock_path.open('a+', encoding='utf-8') as lock:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    for relative in files:
        path = root / relative
        expected = payload['expected_hashes'][relative.as_posix()]
        if expected == 'MISSING' and path.exists():
            raise RuntimeError(f'expected missing file: {relative}')
        if expected != 'MISSING' and digest(path) != expected:
            raise RuntimeError(f'baseline hash mismatch: {relative}')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    backup_dir = Path(payload['backup_dir'])
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f'runtime-patch-{stamp}.tar.gz'
    with tarfile.open(backup, 'w:gz') as archive:
        for relative in files:
            if (root / relative).exists():
                archive.add(root / relative, arcname=relative.as_posix())
    try:
        subprocess.run(['patch', '-p1', '--batch', '--forward', '-i', payload['patch']], cwd=root, check=True)
        for relative in files:
            path = root / relative
            if digest(path) != payload['target_hashes'][relative.as_posix()]:
                raise RuntimeError(f'target hash mismatch: {relative}')
        python = root / '.venv' / 'bin' / 'python'
        for module in payload['modules']:
            subprocess.run([str(python), '-c', f'import {module}'], cwd=root, env={'PYTHONPATH': 'src:.'}, check=True)
    except BaseException:
        for relative in files:
            if payload['expected_hashes'][relative.as_posix()] == 'MISSING':
                (root / relative).unlink(missing_ok=True)
        with tarfile.open(backup, 'r:gz') as archive:
            for member in archive.getmembers():
                target = (root / member.name).resolve()
                if root not in target.parents:
                    raise RuntimeError(f'unsafe backup member: {member.name}')
                archive.extract(member, root)
        raise
    finally:
        Path(payload['patch']).unlink(missing_ok=True)
    print(json.dumps({'verified': True, 'backup_path': str(backup), 'files': payload['files']}))
"""
    return "PAYLOAD=" + shlex.quote(payload) + " python3 - <<'PY'\n" + python + "PY"


def _parse_hashes(values: list[str], *, files: tuple[str, ...]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for value in values:
        relative, separator, digest = str(value).partition("=")
        if (
            not separator
            or relative not in files
            or (digest != "MISSING" and len(digest) != 64)
        ):
            raise SystemExit("baseline hashes must use selected path=sha256 values")
        hashes[relative] = "MISSING" if digest == "MISSING" else digest.lower()
    if set(hashes) != set(files):
        raise SystemExit("a baseline hash is required for every patched file")
    return hashes


def apply_unified_patch(
    plan: SyncPlan,
    *,
    patch_path: Path,
    expected_hashes: dict[str, str],
) -> dict[str, object]:
    target_hashes = {
        relative: hashlib.sha256((PROJECT_ROOT / relative).read_bytes()).hexdigest()
        for relative in plan.files
    }
    patch_digest = hashlib.sha256(patch_path.read_bytes()).hexdigest()[:16]
    remote_patch = f"/tmp/aqsp-runtime-patch-{patch_digest}.diff"
    _run(["scp", str(patch_path), f"{plan.ssh_target}:{remote_patch}"])
    try:
        result = _ssh(
            plan.ssh_target,
            _transactional_patch_command(
                plan,
                remote_patch=remote_patch,
                expected_hashes=expected_hashes,
                target_hashes=target_hashes,
            ),
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "remote transaction failed").strip()
        raise SystemExit(f"transactional patch rolled back: {detail}") from exc
    for line in reversed(result.stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("verified"):
            return payload
    raise SystemExit("transactional patch did not report verification")


def _overlay_manifest_remote_path(remote_root: str) -> str:
    root = str(remote_root or "").rstrip("/") or DEFAULT_REMOTE_ROOT
    return f"{root}/{DEFAULT_REMOTE_OVERLAY_STATE}"


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


def _expand_runtime_dependencies(
    project_root: Path,
    files: tuple[str, ...],
) -> tuple[str, ...]:
    expanded = list(files)
    index = 0
    while index < len(expanded):
        current = expanded[index]
        index += 1
        for dependency in RUNTIME_SYNC_DEPENDENCIES.get(current, ()):
            if dependency in expanded:
                continue
            if not (project_root / dependency).exists():
                raise SystemExit(f"runtime dependency missing locally: {dependency}")
            expanded.append(dependency)
    return tuple(expanded)


def _backup_name(files: tuple[str, ...]) -> str:
    stamp = now_shanghai().strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256("\n".join(files).encode("utf-8")).hexdigest()[:12]
    return f"runtime-sync-{stamp}-{digest}.tar.gz"


def _run(
    command: list[str], *, cwd: Path | None = None, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
        input=input_text,
    )


def _source_metadata() -> dict[str, object]:
    """Describe the local source without copying its worktree contents."""
    commit = _run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT).stdout.strip()
    status = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=PROJECT_ROOT,
    ).stdout
    return {
        "commit": commit,
        "dirty": bool(status.strip()),
        "status_hash": hashlib.sha256(status.encode("utf-8")).hexdigest(),
    }


def _ssh(ssh_target: str, remote_command: str) -> subprocess.CompletedProcess[str]:
    return _run(["ssh", ssh_target, remote_command])


def _remote_runtime_lock_path(remote_root: str) -> str:
    root = str(remote_root or "").rstrip("/") or DEFAULT_REMOTE_ROOT
    return f"{root}/{DEFAULT_REMOTE_RUNTIME_LOCK}"


def _acquire_remote_runtime_lock(plan: SyncPlan) -> None:
    """Acquire the server lock shared with server_sync_and_run.sh."""
    lock_path = _remote_runtime_lock_path(plan.remote_root)
    lock_info_path = f"{lock_path}/meta.env"
    command = (
        f"mkdir -p {shlex.quote(str(Path(lock_path).parent))} && "
        f"if ! mkdir {shlex.quote(lock_path)}; then "
        "echo 'remote runtime lock is busy' >&2; exit 75; fi && "
        f"printf 'LOCK_RUNNER=runtime-sync\\nLOCK_STARTED_AT=%s\\n' "
        f"\"$(date '+%Y-%m-%d %H:%M:%S')\" > {shlex.quote(lock_info_path)}"
    )
    try:
        _ssh(plan.ssh_target, command)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "remote runtime lock is busy").strip()
        raise SystemExit(f"remote runtime lock unavailable: {detail}") from exc


def _release_remote_runtime_lock(plan: SyncPlan) -> None:
    lock_path = _remote_runtime_lock_path(plan.remote_root)
    command = (
        f"rm -f {shlex.quote(lock_path + '/meta.env')} && "
        f"rmdir {shlex.quote(lock_path)} 2>/dev/null || true"
    )
    _ssh(plan.ssh_target, command)


def _create_local_archive(project_root: Path, files: tuple[str, ...]) -> Path:
    handle = tempfile.NamedTemporaryFile(
        prefix="aqsp-runtime-sync-", suffix=".tar.gz", delete=False
    )
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


def _restore_remote_backup(plan: SyncPlan, backup_path: str) -> None:
    """Restore only this sync batch after a post-upload verification failure."""
    quoted_files = " ".join(shlex.quote(item) for item in plan.files)
    if backup_path.endswith(" (no existing files)"):
        command = f"cd {shlex.quote(plan.remote_root)} && rm -f -- {quoted_files}"
    else:
        command = (
            f"cd {shlex.quote(plan.remote_root)} && "
            f"rm -f -- {quoted_files} && "
            f"tar -xzf {shlex.quote(backup_path)} -C {shlex.quote(plan.remote_root)}"
        )
    _ssh(plan.ssh_target, command)


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
        hashes[relative] = hashlib.sha256(
            (project_root / relative).read_bytes()
        ).hexdigest()
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


def _module_import_targets(files: tuple[str, ...]) -> tuple[str, ...]:
    modules: list[str] = []
    for relative in files:
        path = Path(relative)
        if path.suffix != ".py":
            continue
        parts = path.parts
        if len(parts) < 3 or parts[0] != "src" or parts[1] != "aqsp":
            continue
        module_parts = list(parts[1:])
        module_parts[-1] = module_parts[-1][:-3]
        if module_parts[-1] == "__init__":
            module_parts.pop()
        module = ".".join(module_parts)
        if module and module not in modules:
            modules.append(module)
    return tuple(modules)


def _script_smoke_targets(files: tuple[str, ...]) -> tuple[str, ...]:
    scripts: list[str] = []
    for relative in files:
        path = Path(relative)
        if path.suffix != ".py":
            continue
        parts = path.parts
        if len(parts) < 2 or parts[0] != "scripts":
            continue
        if relative not in scripts:
            scripts.append(relative)
    return tuple(scripts)


def _remote_import_smoke(plan: SyncPlan) -> tuple[str, ...]:
    modules = _module_import_targets(plan.files)
    scripts = _script_smoke_targets(plan.files)
    if not modules and not scripts:
        return ()
    python = (
        "import importlib\n"
        "import runpy\n"
        f"modules = {list(modules)!r}\n"
        f"scripts = {list(scripts)!r}\n"
        "for module in modules:\n"
        "    importlib.import_module(module)\n"
        "    print(f'module:{module}')\n"
        "for script in scripts:\n"
        "    runpy.run_path(script, run_name='__aqsp_runtime_sync_smoke__')\n"
        "    print(f'script:{script}')\n"
    )
    command = (
        f"cd {shlex.quote(plan.remote_root)} && "
        "PYTHONPATH=src:. .venv/bin/python - <<'PY'\n"
        f"{python}"
        "PY"
    )
    try:
        result = _ssh(plan.ssh_target, command)
    except subprocess.CalledProcessError as exc:
        detail = "\n".join(
            part.strip()
            for part in (exc.stdout or "", exc.stderr or "")
            if part.strip()
        )
        raise RuntimeError(
            f"remote runtime import smoke failed: {detail or exc}"
        ) from exc
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def _local_import_smoke(project_root: Path, files: tuple[str, ...]) -> tuple[str, ...]:
    modules = _module_import_targets(files)
    scripts = _script_smoke_targets(files)
    if not modules and not scripts:
        return ()
    python = project_root / ".venv/bin/python"
    executable = str(python) if python.is_file() else sys.executable
    python_code = (
        "import importlib\n"
        "import runpy\n"
        "import sys\n"
        "from pathlib import Path\n"
        "root = Path.cwd()\n"
        "sys.path[:0] = [str(root / 'src'), str(root)]\n"
        f"modules = {list(modules)!r}\n"
        f"scripts = {list(scripts)!r}\n"
        "for module in modules:\n"
        "    importlib.import_module(module)\n"
        "    print(f'module:{module}')\n"
        "for script in scripts:\n"
        "    runpy.run_path(script, run_name='__aqsp_runtime_sync_smoke__')\n"
        "    print(f'script:{script}')\n"
    )
    result = _run(
        [executable, "-"],
        cwd=project_root,
        input_text=python_code,
    )
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def _is_local_overlay_plan(plan: SyncPlan) -> bool:
    return Path(plan.remote_root).resolve() == PROJECT_ROOT.resolve()


def _read_local_overlay_manifest(project_root: Path) -> dict[str, object]:
    manifest_path = project_root / DEFAULT_REMOTE_OVERLAY_STATE
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"local overlay manifest missing: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit("local overlay manifest is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise SystemExit("local overlay manifest must be a JSON object")
    return payload


def _read_remote_overlay_manifest(plan: SyncPlan) -> dict[str, object]:
    manifest_path = _overlay_manifest_remote_path(plan.remote_root)
    command = (
        f"cd {shlex.quote(plan.remote_root)} && "
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        f"path = Path({manifest_path!r})\n"
        "if not path.exists():\n"
        "    raise SystemExit(f'overlay manifest missing: {path}')\n"
        "print(path.read_text(encoding='utf-8'))\n"
        "PY"
    )
    result = _ssh(plan.ssh_target, command)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit("remote overlay manifest is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise SystemExit("remote overlay manifest must be a JSON object")
    return payload


def _overlay_files_from_manifest(manifest: dict[str, object]) -> tuple[str, ...]:
    files = manifest.get("managed_files")
    if not isinstance(files, list):
        raise SystemExit("remote overlay manifest missing managed_files")
    normalized: list[str] = []
    for item in files:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    if not normalized:
        raise SystemExit("remote overlay manifest has no managed files")
    return tuple(normalized)


def verify_remote_overlay(plan: SyncPlan) -> dict[str, object]:
    local_plan = _is_local_overlay_plan(plan)
    manifest = (
        _read_local_overlay_manifest(PROJECT_ROOT)
        if local_plan
        else _read_remote_overlay_manifest(plan)
    )
    managed_files = _overlay_files_from_manifest(manifest)
    selected_files = plan.files or managed_files
    expected_hashes = dict(manifest.get("file_hashes") or {})
    if local_plan:
        local_hashes = _local_hashes(PROJECT_ROOT, selected_files)
        remote_hashes = local_hashes
        imported_modules = _local_import_smoke(PROJECT_ROOT, selected_files)
    else:
        remote_plan = SyncPlan(
            ssh_target=plan.ssh_target,
            remote_root=plan.remote_root,
            backup_dir=plan.backup_dir,
            files=selected_files,
        )
        remote_hashes = _remote_hashes(remote_plan)
        imported_modules = _remote_import_smoke(remote_plan)
        local_hashes = _local_hashes(PROJECT_ROOT, selected_files)
    mismatches = {
        relative: {
            "expected": str(expected_hashes.get(relative, "MISSING")),
            "remote": remote_hashes.get(relative, "MISSING"),
        }
        for relative in selected_files
        if remote_hashes.get(relative) != expected_hashes.get(relative)
    }
    local_mismatches = {
        relative: {
            "expected": str(expected_hashes.get(relative, "MISSING")),
            "local": local_hashes.get(relative, "MISSING"),
        }
        for relative in selected_files
        if local_hashes.get(relative) != expected_hashes.get(relative)
    }
    source = manifest.get("source")
    metadata_mismatches: list[str] = []
    if not isinstance(source, dict) or not str(source.get("commit") or "").strip():
        metadata_mismatches.append("source.commit")
    if not str(manifest.get("sync_id") or "").strip():
        metadata_mismatches.append("sync_id")
    if not str(manifest.get("backup_path") or "").strip():
        metadata_mismatches.append("backup_path")
    return {
        "ssh_target": plan.ssh_target,
        "remote_root": plan.remote_root,
        "overlay_manifest": _overlay_manifest_remote_path(plan.remote_root),
        "files": list(selected_files),
        "import_smoke_modules": list(imported_modules),
        "verified": not mismatches and not local_mismatches and not metadata_mismatches,
        "mismatches": mismatches,
        "local_mismatches": local_mismatches,
        "metadata_mismatches": metadata_mismatches,
        "source": source,
        "sync_id": str(manifest.get("sync_id") or ""),
        "backup_path": str(manifest.get("backup_path") or ""),
        "updated_at": str(manifest.get("updated_at") or ""),
    }


def _merge_overlay_manifest(
    existing: dict[str, object] | None,
    *,
    files: tuple[str, ...],
    hashes: dict[str, str],
    synced_at: str,
    source_metadata: dict[str, object] | None = None,
    backup_path: str = "",
    sync_id: str = "",
) -> dict[str, object]:
    existing = existing if isinstance(existing, dict) else {}
    existing_files_raw = existing.get("managed_files")
    existing_files = (
        tuple(
            str(item).strip() for item in existing_files_raw if str(item or "").strip()
        )
        if isinstance(existing_files_raw, list)
        else ()
    )
    existing_hashes_raw = existing.get("file_hashes")
    existing_hashes = (
        {
            str(relative): str(value)
            for relative, value in existing_hashes_raw.items()
            if str(relative).strip() and str(value).strip()
        }
        if isinstance(existing_hashes_raw, dict)
        else {}
    )
    managed_files = sorted(set(existing_files) | set(files))
    file_hashes = {
        relative: hashes.get(relative, existing_hashes.get(relative, ""))
        for relative in managed_files
    }
    merged: dict[str, object] = {
        "managed_files": managed_files,
        "file_hashes": file_hashes,
        "updated_at": synced_at,
    }
    if source_metadata is not None:
        merged["source"] = source_metadata
    if backup_path:
        merged["backup_path"] = backup_path
    if sync_id:
        merged["sync_id"] = sync_id
    return merged


def _write_remote_overlay_manifest(
    plan: SyncPlan,
    *,
    local_hashes: dict[str, str],
    backup_path: str = "",
    source_metadata: dict[str, object] | None = None,
) -> str:
    remote_manifest = _overlay_manifest_remote_path(plan.remote_root)
    synced_at = now_shanghai().isoformat()
    source = source_metadata if source_metadata is not None else _source_metadata()
    sync_id = hashlib.sha256(
        (synced_at + "\n" + "\n".join(plan.files)).encode("utf-8")
    ).hexdigest()[:16]
    payload = _merge_overlay_manifest(
        None,
        files=plan.files,
        hashes=local_hashes,
        synced_at=synced_at,
        source_metadata=source,
        backup_path=backup_path,
        sync_id=sync_id,
    )
    python = (
        "from pathlib import Path\n"
        "import json\n"
        "import os\n"
        f"manifest_path = Path({remote_manifest!r})\n"
        "manifest_path.parent.mkdir(parents=True, exist_ok=True)\n"
        "existing = {}\n"
        "if manifest_path.exists():\n"
        "    try:\n"
        "        existing = json.loads(manifest_path.read_text(encoding='utf-8'))\n"
        "    except Exception:\n"
        "        existing = {}\n"
        f"payload = json.loads({json.dumps(json.dumps(payload, ensure_ascii=False))})\n"
        "previous = {\n"
        "    key: existing[key]\n"
        "    for key in ('sync_id', 'source', 'backup_path')\n"
        "    if key in existing\n"
        "}\n"
        "existing_files = existing.get('managed_files', [])\n"
        "existing_hashes = existing.get('file_hashes', {})\n"
        "if not isinstance(existing_files, list):\n"
        "    existing_files = []\n"
        "if not isinstance(existing_hashes, dict):\n"
        "    existing_hashes = {}\n"
        "payload_files = payload.get('managed_files', [])\n"
        "payload_hashes = payload.get('file_hashes', {})\n"
        "managed_files = sorted({\n"
        "    item for item in (*existing_files, *payload_files)\n"
        "    if isinstance(item, str) and item.strip()\n"
        "})\n"
        "file_hashes = {\n"
        "    item: payload_hashes.get(item, existing_hashes.get(item, ''))\n"
        "    for item in managed_files\n"
        "}\n"
        "manifest = {\n"
        "    'managed_files': managed_files,\n"
        "    'file_hashes': file_hashes,\n"
        "    'updated_at': payload.get('updated_at', ''),\n"
        "}\n"
        "for key in ('source', 'backup_path', 'sync_id'):\n"
        "    if key in payload:\n"
        "        manifest[key] = payload[key]\n"
        "if previous:\n"
        "    manifest['previous_sync'] = previous\n"
        "temporary_path = manifest_path.with_name(manifest_path.name + '.tmp')\n"
        "temporary_path.write_text(\n"
        "    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\\n',\n"
        "    encoding='utf-8',\n"
        ")\n"
        "os.replace(temporary_path, manifest_path)\n"
        "print(manifest_path.as_posix())\n"
    )
    command = f"cd {shlex.quote(plan.remote_root)} && python3 - <<'PY'\n{python}PY"
    result = _ssh(plan.ssh_target, command)
    return result.stdout.strip() or remote_manifest


def sync_files(plan: SyncPlan) -> dict[str, object]:
    archive_path = _create_local_archive(PROJECT_ROOT, plan.files)
    backup_path = ""
    imported_modules: tuple[str, ...] = ()
    overlay_manifest = ""
    mismatches: dict[str, dict[str, str]] = {}
    lock_acquired = False
    try:
        _acquire_remote_runtime_lock(plan)
        lock_acquired = True
        existing_files = _remote_existing_files(plan)
        backup_path = _backup_remote_files(plan, existing_files)
        try:
            _upload_and_extract(plan, archive_path)
            local_hashes = _local_hashes(PROJECT_ROOT, plan.files)
            remote_hashes = _remote_hashes(plan)
            mismatches = {
                relative: {
                    "local": local_hashes[relative],
                    "remote": remote_hashes.get(relative, "MISSING"),
                }
                for relative in plan.files
                if remote_hashes.get(relative) != local_hashes[relative]
            }
            if mismatches:
                raise RuntimeError(f"runtime sync hash mismatch: {mismatches}")
            imported_modules = _remote_import_smoke(plan)
            overlay_manifest = _write_remote_overlay_manifest(
                plan,
                local_hashes=local_hashes,
                backup_path=backup_path,
            )
        except BaseException:
            _restore_remote_backup(plan, backup_path)
            raise
    finally:
        if lock_acquired:
            _release_remote_runtime_lock(plan)
        archive_path.unlink(missing_ok=True)
    return {
        "ssh_target": plan.ssh_target,
        "remote_root": plan.remote_root,
        "backup_path": backup_path,
        "files": list(plan.files),
        "overlay_manifest": overlay_manifest,
        "import_smoke_modules": list(imported_modules),
        "verified": not mismatches,
        "mismatches": mismatches,
    }


def build_plan(
    args: argparse.Namespace, *, allow_empty_files: bool = False
) -> SyncPlan:
    raw_files = list(args.files or [])
    files = (
        ()
        if allow_empty_files and not raw_files
        else _normalize_files(PROJECT_ROOT, raw_files)
    )
    return SyncPlan(
        ssh_target=str(args.ssh_target or "").strip() or "aqsp-server",
        remote_root=str(args.remote_root or "").strip() or DEFAULT_REMOTE_ROOT,
        backup_dir=str(args.backup_dir or "").strip() or DEFAULT_BACKUP_DIR,
        files=()
        if allow_empty_files and not files
        else _expand_runtime_dependencies(PROJECT_ROOT, files),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-target", default="aqsp-server")
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR)
    parser.add_argument(
        "--verify-overlay",
        action="store_true",
        help="only verify the remote runtime overlay manifest, hashes, and import smoke",
    )
    parser.add_argument(
        "--apply-unified-patch",
        default="",
        help="apply a checked unified patch under a remote transaction lock",
    )
    parser.add_argument(
        "--baseline-hash",
        action="append",
        default=[],
        help="required with --apply-unified-patch: project/path=sha256",
    )
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)

    patch_path = (
        Path(args.apply_unified_patch).resolve() if args.apply_unified_patch else None
    )
    if patch_path:
        files = _patch_paths(patch_path)
        plan = SyncPlan(
            ssh_target=args.ssh_target,
            remote_root=args.remote_root,
            backup_dir=args.backup_dir,
            files=files,
        )
        result = apply_unified_patch(
            plan,
            patch_path=patch_path,
            expected_hashes=_parse_hashes(args.baseline_hash, files=files),
        )
        try:
            result["overlay_manifest"] = _write_remote_overlay_manifest(
                plan,
                local_hashes=_local_hashes(PROJECT_ROOT, plan.files),
                backup_path=str(result.get("backup_path") or ""),
            )
        except BaseException:
            backup_path = str(result.get("backup_path") or "")
            if backup_path:
                _restore_remote_backup(plan, backup_path)
            raise
    else:
        plan = build_plan(args, allow_empty_files=args.verify_overlay)
        result = (
            verify_remote_overlay(plan) if args.verify_overlay else sync_files(plan)
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
