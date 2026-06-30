from __future__ import annotations

from pathlib import Path

import pytest

from scripts import sync_runtime_files_to_server as sync_mod


def test_normalize_files_dedupes_and_returns_project_relative_paths(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    file_path = project_root / "scripts" / "demo.sh"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("echo ok\n", encoding="utf-8")

    normalized = sync_mod._normalize_files(
        project_root,
        ["scripts/demo.sh", str(file_path)],
    )

    assert normalized == ("scripts/demo.sh",)


def test_normalize_files_rejects_paths_outside_project_root(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="outside project root"):
        sync_mod._normalize_files(project_root, [str(outside)])


def test_backup_name_contains_hash_suffix() -> None:
    name = sync_mod._backup_name(("scripts/a.py", "src/aqsp/b.py"))

    assert name.startswith("runtime-sync-")
    assert name.endswith(".tar.gz")
    assert len(name.split("-")[-1].split(".")[0]) == 12
