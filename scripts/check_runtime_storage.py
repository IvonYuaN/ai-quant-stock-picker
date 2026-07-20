#!/usr/bin/env python3
"""Audit and safely prune AQSP immutable releases.

The command is deliberately fail-closed: it only removes direct child
directories of the release directory, and only after proving that the
runtime data, shared venv, raw history, current release, and rollback release
are outside the deletion set.
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


OUTPUT_ENV_KEYS = frozenset(
    {
        "AQSP_LEDGER",
        "AQSP_PAPER_LEDGER",
        "AQSP_DEBATE_RESULTS",
        "AQSP_INTRADAY_LEDGER",
        "AQSP_REPORT",
        "AQSP_DASHBOARD_HTML",
        "AQSP_DASHBOARD_DB",
        "AQSP_INTRADAY_DASHBOARD_HTML",
        "AQSP_INTRADAY_DASHBOARD_DB",
        "AQSP_INTRADAY_REPORT",
        "AQSP_INTRADAY_LATEST_CSV",
        "AQSP_INTRADAY_OUTPUT_CSV",
        "AQSP_INTRADAY_STATUS",
        "AQSP_INTRADAY_REFRESH_STATUS_PATH",
        "AQSP_INTRADAY_CURSOR_PATH",
        "AQSP_OUTPUT_CSV",
        "AQSP_HOME_SNAPSHOT_PATH",
        "AQSP_HOME_SNAPSHOT_INDEX_PATH",
        "AQSP_NEWS_OUTPUT",
        "AQSP_NEWS_JSON_OUTPUT",
        "AQSP_NEWS_ARCHIVE_DIR",
        "AQSP_BT_LOGS_DIR",
        "AQSP_RISK_STATE",
        "AQSP_WALKFORWARD_GATE_PATH",
        "AQSP_WALKFORWARD_PRODUCTION_STATUS",
        "AQSP_GATE_NOTIFY_STATE_PATH",
        "AQSP_REALTIME_CROSS_MARKET_PATH",
        "AQSP_RUNTIME_SYMBOL_CACHE",
        "AQSP_INTRADAY_FAST_SYMBOL_CACHE",
        "AQSP_INTRADAY_FAST_SYMBOL_CSVS",
        "AQSP_RESEARCH_SURFACE_SNAPSHOT",
    }
)


@dataclass(frozen=True)
class StorageLayout:
    root: Path
    releases: Path
    current: Path
    rollback: Path
    runtime: Path
    shared_venv: Path
    raw_history: Path
    env_file: Path | None = None


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class StorageReport:
    findings: tuple[Finding, ...]
    releases: tuple[Path, ...]
    deletable: tuple[Path, ...]
    current_target: Path | None
    rollback_target: Path | None

    @property
    def ok(self) -> bool:
        return not any(item.severity == "error" for item in self.findings)


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _inside(path: Path, parent: Path) -> bool:
    try:
        _resolved(path).relative_to(_resolved(parent))
    except ValueError:
        return False
    return True


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in OUTPUT_ENV_KEYS:
            values[key] = value.strip().strip("'\"")
    return values


def _env_path_values(layout: StorageLayout) -> Iterable[tuple[str, Path]]:
    if layout.env_file is None:
        return ()
    values = _parse_env(layout.env_file)
    result: list[tuple[str, Path]] = []
    for key, value in values.items():
        if key == "AQSP_INTRADAY_FAST_SYMBOL_CSVS":
            result.extend(
                (key, Path(item.strip()))
                for item in value.split(",")
                if item.strip()
            )
        elif value:
            result.append((key, Path(value)))
    return result


def inspect_storage(layout: StorageLayout) -> StorageReport:
    """Return a fail-closed report without changing the filesystem."""
    findings: list[Finding] = []
    releases_dir = _resolved(layout.releases)
    runtime = _resolved(layout.runtime)
    shared_venv = _resolved(layout.shared_venv)
    raw_history = _resolved(layout.raw_history)

    if not releases_dir.is_dir():
        findings.append(Finding("error", "missing_releases", f"release directory missing: {releases_dir}"))
        releases: list[Path] = []
    else:
        releases = sorted(
            (item for item in releases_dir.iterdir() if item.is_dir() and not item.is_symlink()),
            key=lambda item: item.name,
        )
    if not runtime.is_absolute() or _inside(runtime, releases_dir):
        findings.append(Finding("error", "runtime_inside_release", f"runtime must be outside releases: {runtime}"))
    if _inside(shared_venv, releases_dir):
        findings.append(Finding("error", "venv_inside_release", f"shared venv must be outside releases: {shared_venv}"))
    if _inside(raw_history, releases_dir):
        findings.append(Finding("error", "raw_inside_release", f"raw history must be outside releases: {raw_history}"))
    if not runtime.is_absolute() or not shared_venv.is_absolute() or not raw_history.is_absolute():
        findings.append(Finding("error", "relative_protected_path", "runtime, shared venv and raw history must be absolute paths"))

    current_target = _link_target(layout.current, releases_dir, "current", findings)
    rollback_target = _link_target(layout.rollback, releases_dir, "rollback", findings)
    protected = {target for target in (current_target, rollback_target) if target is not None}
    deletable = tuple(item for item in releases if _resolved(item) not in protected)

    if current_target is not None and rollback_target is not None and current_target == rollback_target:
        findings.append(Finding("error", "same_current_rollback", "current and rollback must be different releases"))
    if len(releases) < 2:
        findings.append(Finding("error", "insufficient_releases", "at least current and one rollback release are required"))

    if layout.env_file is not None:
        if not layout.env_file.is_file():
            findings.append(Finding("error", "missing_env", f"environment file missing: {layout.env_file}"))
        for key, raw_path in _env_path_values(layout):
            if not raw_path.is_absolute():
                findings.append(Finding("error", "relative_runtime_env", f"{key} must be absolute: {raw_path}"))
            elif not _inside(raw_path, runtime):
                findings.append(Finding("error", "runtime_env_outside_data", f"{key} points outside runtime data: {raw_path}"))

    for protected_path, code in ((runtime, "runtime"), (shared_venv, "shared_venv"), (raw_history, "raw_history")):
        if protected_path.exists():
            findings.append(Finding("info", f"protected_{code}", f"protected from cleanup: {protected_path}"))

    return StorageReport(tuple(findings), tuple(releases), deletable, current_target, rollback_target)


def _link_target(link: Path, releases_dir: Path, label: str, findings: list[Finding]) -> Path | None:
    if not link.is_symlink():
        findings.append(Finding("error", f"missing_{label}_link", f"{label} must be a symlink: {link}"))
        return None
    target = _resolved(link)
    if not _inside(target, releases_dir) or target.parent != releases_dir:
        findings.append(Finding("error", f"unsafe_{label}_link", f"{label} does not point to a direct release: {link} -> {target}"))
        return None
    if not target.is_dir():
        findings.append(Finding("error", f"missing_{label}_target", f"{label} target is not a directory: {target}"))
        return None
    return target


def prune_releases(layout: StorageLayout, *, apply: bool = False) -> StorageReport:
    """Remove only unreferenced release directories, then audit again."""
    before = inspect_storage(layout)
    if not before.ok:
        return before
    if not apply:
        return before
    releases_dir = _resolved(layout.releases)
    for candidate in before.deletable:
        resolved = _resolved(candidate)
        if resolved.parent != releases_dir or resolved in {
            before.current_target,
            before.rollback_target,
        }:
            raise RuntimeError(f"refusing to remove unsafe release: {candidate}")
        if candidate.is_symlink() or not candidate.is_dir():
            raise RuntimeError(f"refusing to remove non-directory release: {candidate}")
        shutil.rmtree(candidate)
    return inspect_storage(layout)


def _layout_from_args(args: argparse.Namespace) -> StorageLayout:
    root = Path(args.root).resolve()
    return StorageLayout(
        root=root,
        releases=Path(args.releases).resolve(),
        current=Path(args.current).resolve(),
        rollback=Path(args.rollback).resolve(),
        runtime=Path(args.runtime).resolve(),
        shared_venv=Path(args.shared_venv).resolve(),
        raw_history=Path(args.raw_history).resolve(),
        env_file=Path(args.env_file).resolve() if args.env_file else None,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="check_runtime_storage")
    parser.add_argument("--root", default="/opt/aqsp")
    parser.add_argument("--releases", default="/opt/aqsp-releases")
    parser.add_argument(
        "--current", default="/opt/aqsp-releases/aqsp-scheduler-current"
    )
    parser.add_argument(
        "--rollback", default="/opt/aqsp-releases/aqsp-scheduler-rollback"
    )
    parser.add_argument("--runtime", default="/opt/aqsp/data")
    parser.add_argument("--shared-venv", default="/opt/aqsp-vibe-venv")
    parser.add_argument("--raw-history", default="/opt/aqsp/data/walkforward_raw_production_cache.db")
    parser.add_argument("--env-file")
    parser.add_argument("--apply", action="store_true", help="删除未引用旧 release；默认只检查")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    layout = _layout_from_args(args)
    report = prune_releases(layout, apply=args.apply)
    payload = {
        "ok": report.ok,
        "releases": [str(item) for item in report.releases],
        "deletable": [str(item) for item in report.deletable],
        "current": str(report.current_target) if report.current_target else None,
        "rollback": str(report.rollback_target) if report.rollback_target else None,
        "findings": [asdict(item) for item in report.findings],
    }
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"runtime storage: {'PASS' if report.ok else 'FAIL'}")
        print(f"current={payload['current']} rollback={payload['rollback']}")
        print(f"releases={len(report.releases)} deletable={len(report.deletable)} apply={args.apply}")
        for finding in report.findings:
            print(f"[{finding.severity}] {finding.code}: {finding.message}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
