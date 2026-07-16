from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

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


def test_expand_runtime_dependencies_adds_transitive_runtime_modules(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    for relative in (
        "scripts/daily_pipeline.py",
        "src/aqsp/portfolio/manager.py",
        "src/aqsp/portfolio/risk_summary.py",
    ):
        path = project_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# ok\n", encoding="utf-8")

    expanded = sync_mod._expand_runtime_dependencies(
        project_root,
        ("scripts/daily_pipeline.py",),
    )

    assert expanded == (
        "scripts/daily_pipeline.py",
        "src/aqsp/portfolio/manager.py",
        "src/aqsp/portfolio/risk_summary.py",
    )


def test_expand_runtime_dependencies_fails_when_dependency_missing(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    path = project_root / "scripts/daily_pipeline.py"
    path.parent.mkdir(parents=True)
    path.write_text("# ok\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="runtime dependency missing locally"):
        sync_mod._expand_runtime_dependencies(
            project_root,
            ("scripts/daily_pipeline.py",),
        )


def test_expand_runtime_dependencies_covers_runtime_overlay_closure(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    selected = (
        "src/aqsp/cli.py",
        "src/aqsp/walkforward_gate.py",
        "src/aqsp/strategies/__init__.py",
        "src/aqsp/data/pit_financial.py",
    )
    expected_dependencies = (
        "src/aqsp/data/market_context_source.py",
        "src/aqsp/goal_switches.py",
        "config/goal_switches.yaml",
        "src/aqsp/backtest/audit.py",
        "src/aqsp/strategies/catalog.py",
        "config/strategy_sources.yaml",
        "src/aqsp/data/pit_policy.py",
        "src/aqsp/market_context.py",
    )
    for relative in (*selected, *expected_dependencies):
        path = project_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# ok\n", encoding="utf-8")

    expanded = sync_mod._expand_runtime_dependencies(project_root, selected)

    assert expanded == (*selected, *expected_dependencies)


def test_expand_runtime_dependencies_covers_market_context_smoke_script(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    expected = (
        "scripts/smoke_market_context_runtime.py",
        "src/aqsp/market_context.py",
        "src/aqsp/portfolio/manager.py",
        "src/aqsp/strategies/thresholds.py",
        "src/aqsp/goal_switches.py",
        "config/goal_switches.yaml",
        "src/aqsp/portfolio/risk_summary.py",
    )
    for relative in expected:
        path = project_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# ok\n", encoding="utf-8")

    expanded = sync_mod._expand_runtime_dependencies(
        project_root,
        ("scripts/smoke_market_context_runtime.py",),
    )

    assert expanded == expected


def test_expand_runtime_dependencies_covers_live_home_snapshot_modules(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    expected = (
        "scripts/write_home_snapshot.py",
        "src/aqsp/data/market_context_source.py",
        "src/aqsp/market_context.py",
        "src/aqsp/web/data_provider.py",
        "src/aqsp/web/home_snapshot.py",
        "src/aqsp/goal_switches.py",
        "config/goal_switches.yaml",
    )
    for relative in expected:
        path = project_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# ok\n", encoding="utf-8")

    expanded = sync_mod._expand_runtime_dependencies(
        project_root,
        ("scripts/write_home_snapshot.py",),
    )

    assert expanded == expected


def test_expand_runtime_dependencies_covers_beginner_compat_module(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    expected = (
        "src/aqsp/web/dashboard_beginner.py",
        "src/aqsp/web/dashboard_beginner_compat.py",
        "src/aqsp/web/data_provider.py",
    )
    for relative in expected:
        path = project_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# ok\n", encoding="utf-8")

    assert (
        sync_mod._expand_runtime_dependencies(
            project_root,
            ("src/aqsp/web/dashboard_beginner.py",),
        )
        == expected
    )


def test_module_import_targets_only_include_aqsp_python_modules() -> None:
    assert sync_mod._module_import_targets(
        (
            "scripts/daily_pipeline.py",
            "src/aqsp/portfolio/manager.py",
            "src/aqsp/portfolio/__init__.py",
            "src/other/demo.py",
        )
    ) == ("aqsp.portfolio.manager", "aqsp.portfolio")


def test_script_smoke_targets_include_python_scripts_only() -> None:
    assert sync_mod._script_smoke_targets(
        (
            "scripts/daily_pipeline.py",
            "scripts/bt_task.sh",
            "src/aqsp/portfolio/manager.py",
        )
    ) == ("scripts/daily_pipeline.py",)


def test_remote_import_smoke_runs_aqsp_modules_and_script_entrypoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[str] = []

    def fake_ssh(_target: str, command: str):
        commands.append(command)
        return type(
            "_Result",
            (),
            {
                "stdout": (
                    "module:aqsp.portfolio.manager\nscript:scripts/daily_pipeline.py\n"
                )
            },
        )()

    monkeypatch.setattr(sync_mod, "_ssh", fake_ssh)

    smoked = sync_mod._remote_import_smoke(
        sync_mod.SyncPlan(
            ssh_target="aqsp-server",
            remote_root="/opt/aqsp",
            backup_dir="/opt/aqsp/runtime-backups",
            files=("scripts/daily_pipeline.py", "src/aqsp/portfolio/manager.py"),
        )
    )

    assert smoked == (
        "module:aqsp.portfolio.manager",
        "script:scripts/daily_pipeline.py",
    )
    assert "runpy.run_path" in commands[0]
    assert "__aqsp_runtime_sync_smoke__" in commands[0]


def test_remote_import_smoke_reports_remote_failure_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = subprocess.CalledProcessError(
        1,
        ["ssh", "aqsp-server"],
        output="module:aqsp.market_context\n",
        stderr="ModuleNotFoundError: aqsp.data.market_context_source\n",
    )
    monkeypatch.setattr(
        sync_mod,
        "_ssh",
        lambda _target, _command: (_ for _ in ()).throw(error),
    )

    with pytest.raises(RuntimeError, match="market_context_source"):
        sync_mod._remote_import_smoke(
            sync_mod.SyncPlan(
                ssh_target="aqsp-server",
                remote_root="/opt/aqsp",
                backup_dir="/opt/aqsp/runtime-backups",
                files=("src/aqsp/market_context.py",),
            )
        )


def test_expand_runtime_dependencies_covers_research_package_exports(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    expected = (
        "src/aqsp/research/__init__.py",
        "src/aqsp/research/factor_expression.py",
        "src/aqsp/research/price_path.py",
        "src/aqsp/research/repo_intake.py",
    )
    for relative in expected:
        path = project_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# ok\n", encoding="utf-8")

    assert (
        sync_mod._expand_runtime_dependencies(
            project_root,
            ("src/aqsp/research/__init__.py",),
        )
        == expected
    )


def test_backup_name_contains_hash_suffix() -> None:
    name = sync_mod._backup_name(("scripts/a.py", "src/aqsp/b.py"))

    assert name.startswith("runtime-sync-")
    assert name.endswith(".tar.gz")
    assert len(name.split("-")[-1].split(".")[0]) == 12


def test_remote_runtime_lock_uses_server_sync_lock_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[str] = []
    monkeypatch.setattr(
        sync_mod,
        "_ssh",
        lambda _target, command: commands.append(command) or SimpleNamespace(),
    )
    plan = sync_mod.SyncPlan(
        ssh_target="aqsp-server",
        remote_root="/opt/aqsp",
        backup_dir="/opt/aqsp/runtime-backups",
        files=("scripts/a.py",),
    )

    sync_mod._acquire_remote_runtime_lock(plan)
    sync_mod._release_remote_runtime_lock(plan)

    assert ".locks/server-runtime.lock" in commands[0]
    assert "mkdir" in commands[0]
    assert "rmdir" in commands[1]


def test_sync_files_holds_remote_lock_across_full_transaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    plan = sync_mod.SyncPlan(
        ssh_target="aqsp-server",
        remote_root="/opt/aqsp",
        backup_dir="/opt/aqsp/runtime-backups",
        files=("scripts/a.py",),
    )
    monkeypatch.setattr(
        sync_mod, "_create_local_archive", lambda *_args: tmp_path / "archive"
    )
    monkeypatch.setattr(
        sync_mod, "_acquire_remote_runtime_lock", lambda _plan: calls.append("acquire")
    )
    monkeypatch.setattr(
        sync_mod, "_release_remote_runtime_lock", lambda _plan: calls.append("release")
    )
    monkeypatch.setattr(
        sync_mod, "_remote_existing_files", lambda _plan: calls.append("existing") or []
    )
    monkeypatch.setattr(
        sync_mod,
        "_backup_remote_files",
        lambda _plan, _files: calls.append("backup") or "backup.tar.gz",
    )
    monkeypatch.setattr(
        sync_mod, "_upload_and_extract", lambda *_args: calls.append("upload")
    )
    monkeypatch.setattr(
        sync_mod,
        "_local_hashes",
        lambda _root, _files: {"scripts/a.py": "a"},
    )
    monkeypatch.setattr(
        sync_mod,
        "_remote_hashes",
        lambda _plan: calls.append("hashes") or {"scripts/a.py": "a"},
    )
    monkeypatch.setattr(
        sync_mod, "_remote_import_smoke", lambda _plan: calls.append("smoke") or ()
    )
    monkeypatch.setattr(
        sync_mod,
        "_write_remote_overlay_manifest",
        lambda _plan, **_kwargs: calls.append("manifest") or "manifest.json",
    )

    result = sync_mod.sync_files(plan)

    assert result["verified"] is True
    assert calls == [
        "acquire",
        "existing",
        "backup",
        "upload",
        "hashes",
        "smoke",
        "manifest",
        "release",
    ]


def test_overlay_manifest_remote_path_defaults_under_state_dir() -> None:
    assert (
        sync_mod._overlay_manifest_remote_path("/opt/aqsp")
        == "/opt/aqsp/.state/runtime-sync-overlay.json"
    )


def test_patch_paths_rejects_renames_and_path_traversal(tmp_path: Path) -> None:
    patch = tmp_path / "unsafe.patch"
    patch.write_text(
        "--- a/src/aqsp/cli.py\n+++ b/../outside.py\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="modify existing files|unsafe patch path"):
        sync_mod._patch_paths(patch)


def test_patch_paths_accepts_a_new_project_file(tmp_path: Path) -> None:
    patch = tmp_path / "new-file.patch"
    patch.write_text(
        "--- /dev/null\n+++ b/src/aqsp/audit/decision_chain.py\n",
        encoding="utf-8",
    )

    assert sync_mod._patch_paths(patch) == ("src/aqsp/audit/decision_chain.py",)


def test_transactional_patch_command_contains_lock_backup_and_rollback() -> None:
    plan = sync_mod.SyncPlan(
        ssh_target="aqsp-server",
        remote_root="/opt/aqsp",
        backup_dir="/opt/aqsp/runtime-backups",
        files=("src/aqsp/cli.py",),
    )

    command = sync_mod._transactional_patch_command(
        plan,
        remote_patch="/tmp/runtime.patch",
        expected_hashes={"src/aqsp/cli.py": "before"},
        target_hashes={"src/aqsp/cli.py": "after"},
    )

    assert "fcntl.LOCK_EX | fcntl.LOCK_NB" in command
    assert "baseline hash mismatch" in command
    assert "expected missing file" in command
    assert "tarfile.open(backup, 'w:gz')" in command
    assert "subprocess.run(['patch', '-p1'" in command
    assert "target hash mismatch" in command
    assert "unsafe backup member" in command
    assert "archive.extract(member, root)" in command


def test_overlay_files_from_manifest_dedupes_managed_files() -> None:
    assert sync_mod._overlay_files_from_manifest(
        {"managed_files": ["scripts/a.py", "scripts/a.py", "", None]}
    ) == ("scripts/a.py",)


def test_build_plan_allows_empty_files_for_verify_overlay() -> None:
    args = argparse.Namespace(
        ssh_target="aqsp-server",
        remote_root="/opt/aqsp",
        backup_dir="/opt/aqsp/runtime-backups",
        files=[],
    )

    plan = sync_mod.build_plan(args, allow_empty_files=True)

    assert plan.files == ()


def test_merge_overlay_manifest_accumulates_managed_files_and_updates_hashes() -> None:
    merged = sync_mod._merge_overlay_manifest(
        {
            "managed_files": ["scripts/a.sh"],
            "file_hashes": {"scripts/a.sh": "old"},
            "updated_at": "2026-07-06T18:00:00+08:00",
        },
        files=("src/aqsp/config.py",),
        hashes={
            "src/aqsp/config.py": "new-config",
        },
        synced_at="2026-07-07T09:00:00+08:00",
    )

    assert merged["managed_files"] == ["scripts/a.sh", "src/aqsp/config.py"]
    assert merged["file_hashes"] == {
        "scripts/a.sh": "old",
        "src/aqsp/config.py": "new-config",
    }
    assert merged["updated_at"] == "2026-07-07T09:00:00+08:00"


def test_verify_remote_overlay_reports_local_drift_when_file_not_synced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = sync_mod.SyncPlan(
        ssh_target="aqsp-server",
        remote_root="/opt/aqsp",
        backup_dir="/opt/aqsp/runtime-backups",
        files=("src/aqsp/portfolio/manager.py",),
    )
    monkeypatch.setattr(
        sync_mod,
        "_read_remote_overlay_manifest",
        lambda _plan: {
            "managed_files": ["src/aqsp/portfolio/manager.py"],
            "file_hashes": {"src/aqsp/portfolio/manager.py": "old-hash"},
            "updated_at": "2026-07-08T17:00:00+08:00",
        },
    )
    monkeypatch.setattr(
        sync_mod,
        "_remote_hashes",
        lambda _plan: {"src/aqsp/portfolio/manager.py": "old-hash"},
    )
    monkeypatch.setattr(sync_mod, "_remote_import_smoke", lambda _plan: ())
    monkeypatch.setattr(
        sync_mod,
        "_local_hashes",
        lambda _root, _files: {"src/aqsp/portfolio/manager.py": "new-local-hash"},
    )

    result = sync_mod.verify_remote_overlay(plan)

    assert result["verified"] is False
    assert result["mismatches"] == {}
    assert result["local_mismatches"] == {
        "src/aqsp/portfolio/manager.py": {
            "expected": "old-hash",
            "local": "new-local-hash",
        }
    }


def test_verify_overlay_uses_local_paths_when_remote_root_is_project_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "aqsp"
    relative = "src/aqsp/portfolio/manager.py"
    file_path = project_root / relative
    file_path.parent.mkdir(parents=True)
    file_path.write_text("# local runtime\n", encoding="utf-8")
    files = (relative,)
    manifest_path = project_root / sync_mod.DEFAULT_REMOTE_OVERLAY_STATE
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "managed_files": list(files),
                "file_hashes": sync_mod._local_hashes(project_root, files),
                "source": {"commit": "local-test"},
                "sync_id": "sync-local-test",
                "backup_path": "/opt/aqsp/runtime-backups/test.tar.gz",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sync_mod, "PROJECT_ROOT", project_root)

    def fail_ssh(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("same-root overlay verification must not use SSH")

    monkeypatch.setattr(sync_mod, "_ssh", fail_ssh)
    monkeypatch.setattr(sync_mod, "_local_import_smoke", lambda *_args: ())

    result = sync_mod.verify_remote_overlay(
        sync_mod.SyncPlan(
            ssh_target="aqsp-server",
            remote_root=str(project_root),
            backup_dir="/opt/aqsp/runtime-backups",
            files=(),
        )
    )

    assert result["verified"] is True
    assert result["files"] == list(files)
    assert result["mismatches"] == {}
    assert result["local_mismatches"] == {}


def test_verify_overlay_uses_ssh_when_remote_root_differs_from_project_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    plan = sync_mod.SyncPlan(
        ssh_target="aqsp-server",
        remote_root="/opt/aqsp",
        backup_dir="/opt/aqsp/runtime-backups",
        files=("src/aqsp/portfolio/manager.py",),
    )
    monkeypatch.setattr(
        sync_mod,
        "_read_remote_overlay_manifest",
        lambda _plan: {
            "managed_files": ["src/aqsp/portfolio/manager.py"],
            "file_hashes": {"src/aqsp/portfolio/manager.py": "same-hash"},
            "source": {"commit": "remote-test"},
            "sync_id": "sync-remote-test",
            "backup_path": "/opt/aqsp/runtime-backups/test.tar.gz",
        },
    )
    monkeypatch.setattr(
        sync_mod,
        "_remote_hashes",
        lambda _plan: (
            calls.append("remote_hashes")
            or {"src/aqsp/portfolio/manager.py": "same-hash"}
        ),
    )
    monkeypatch.setattr(
        sync_mod,
        "_remote_import_smoke",
        lambda _plan: calls.append("remote_import_smoke") or (),
    )
    monkeypatch.setattr(
        sync_mod,
        "_local_hashes",
        lambda _root, _files: {"src/aqsp/portfolio/manager.py": "same-hash"},
    )

    def fail_local_manifest(_root: Path) -> dict[str, object]:
        raise AssertionError("different-root verification must use remote manifest")

    monkeypatch.setattr(sync_mod, "_read_local_overlay_manifest", fail_local_manifest)

    result = sync_mod.verify_remote_overlay(plan)

    assert result["verified"] is True
    assert calls == ["remote_hashes", "remote_import_smoke"]
