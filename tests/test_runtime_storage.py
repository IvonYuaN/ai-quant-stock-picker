from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from scripts.check_runtime_storage import StorageLayout, inspect_storage, prune_releases


def _layout(tmp_path: Path, *, env_file: bool = True) -> StorageLayout:
    root = tmp_path / "aqsp"
    releases = root / "releases"
    releases.mkdir(parents=True)
    current = releases / "current-aaa"
    rollback = releases / "rollback-bbb"
    current.mkdir()
    rollback.mkdir()
    (releases / "old-ccc").mkdir()
    (root / "current").symlink_to(current)
    (root / "rollback").symlink_to(rollback)
    runtime = root / "data"
    runtime.mkdir()
    shared_venv = tmp_path / "aqsp-vibe-venv"
    shared_venv.mkdir()
    raw_history = runtime / "walkforward_raw_production_cache.db"
    raw_history.write_text("raw\n", encoding="utf-8")
    env_path = root / ".env"
    if env_file:
        env_path.write_text(
            f"AQSP_LEDGER={runtime}/predictions.jsonl\n"
            f"AQSP_REPORT={runtime}/reports/latest.md\n",
            encoding="utf-8",
        )
    return StorageLayout(
        root=root,
        releases=releases,
        current=root / "current",
        rollback=root / "rollback",
        runtime=runtime,
        shared_venv=shared_venv,
        raw_history=raw_history,
        env_file=env_path if env_file else None,
    )


def test_storage_audit_keeps_current_and_single_rollback_and_finds_old_release(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)

    report = inspect_storage(layout)

    assert report.ok
    assert report.current_target == layout.releases / "current-aaa"
    assert report.rollback_target == layout.releases / "rollback-bbb"
    assert report.deletable == (layout.releases / "old-ccc",)


def test_storage_prune_only_removes_unreferenced_release(tmp_path: Path) -> None:
    layout = _layout(tmp_path)

    report = prune_releases(layout, apply=True)

    assert report.ok
    assert (layout.releases / "current-aaa").is_dir()
    assert (layout.releases / "rollback-bbb").is_dir()
    assert not (layout.releases / "old-ccc").exists()
    assert layout.shared_venv.is_dir()
    assert layout.raw_history.is_file()
    assert layout.runtime.is_dir()


def test_storage_audit_rejects_runtime_artifact_outside_data(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    assert layout.env_file is not None
    layout.env_file.write_text(
        "AQSP_LEDGER=/tmp/aqsp/releases/current-aaa/data/predictions.jsonl\n",
        encoding="utf-8",
    )

    report = inspect_storage(layout)

    assert not report.ok
    assert any(item.code == "runtime_env_outside_data" for item in report.findings)
    assert (layout.releases / "old-ccc").is_dir()


def test_storage_audit_rejects_current_and_rollback_aliasing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    layout.rollback.unlink()
    layout.rollback.symlink_to(layout.releases / "current-aaa")

    report = inspect_storage(layout)

    assert not report.ok
    assert any(item.code == "same_current_rollback" for item in report.findings)


def test_storage_prune_is_fail_closed_when_release_links_are_missing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    layout.current.unlink()

    report = prune_releases(layout, apply=True)

    assert not report.ok
    assert (layout.releases / "old-ccc").is_dir()


def test_storage_audit_rejects_shared_venv_inside_release(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    layout = replace(layout, shared_venv=layout.releases / "old-ccc")

    report = inspect_storage(layout)

    assert not report.ok
    assert any(item.code == "venv_inside_release" for item in report.findings)


@pytest.mark.parametrize("path_name", ["current", "rollback"])
def test_release_link_must_point_to_direct_child(tmp_path: Path, path_name: str) -> None:
    layout = _layout(tmp_path)
    link = getattr(layout, path_name)
    link.unlink()
    nested = layout.releases / "old-ccc" / "nested"
    nested.mkdir()
    link.symlink_to(nested)

    report = inspect_storage(layout)

    assert not report.ok
    assert any(item.code == f"unsafe_{path_name}_link" for item in report.findings)
