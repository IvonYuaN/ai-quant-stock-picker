from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts import check_release_consistency as checker
from scripts import push_with_report
from scripts.write_release_manifest import build_manifest, write_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHA_A = "a" * 40
SHA_B = "b" * 40


def _fake_git(monkeypatch, values: dict[tuple[str, ...], tuple[bool, str]]) -> None:
    def run(_root: Path, *args: str) -> tuple[bool, str]:
        return values.get(tuple(args), (False, "missing fake git result"))

    monkeypatch.setattr(checker, "_git", run)


def _manifest(root: Path, commit: str = SHA_A) -> Path:
    path = root / ".aqsp-release.json"
    # 不可变发布需要已校验的 content_digest（防篡改）；非 immutable 模式下该字段惰性忽略。
    files = checker._release_files_for_digest(root, path)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "commit": commit,
                "branch": "main",
                "tree": "tree",
                "remote": "origin",
                "remote_url": "https://github.com/example/aqsp.git",
                "content_digest": checker._content_digest(root, files),
            }
        ),
        encoding="utf-8",
    )
    return path


def test_release_consistency_rejects_release_not_published(
    monkeypatch, tmp_path: Path
) -> None:
    _manifest(tmp_path)
    _fake_git(
        monkeypatch,
        {
            ("rev-parse", "HEAD"): (True, SHA_A),
            ("rev-parse", "--verify", "refs/remotes/origin/main"): (True, SHA_B),
            ("status", "--porcelain=v1", "--untracked-files=all"): (True, ""),
        },
    )

    findings = checker.audit(
        project_root=tmp_path,
        runtime_root=tmp_path,
        remote="origin",
        branch="main",
        canonical_link=None,
        manifest_path=tmp_path / ".aqsp-release.json",
        overlay_path=tmp_path / "missing-overlay.json",
        active_files=[],
        require_overlay=False,
    )

    assert any(item.code == "release_not_published" for item in findings)


def test_release_consistency_rejects_overlay_from_another_release(
    monkeypatch, tmp_path: Path
) -> None:
    _manifest(tmp_path)
    overlay = tmp_path / "overlay.json"
    overlay.write_text(
        json.dumps(
            {
                "source": {"commit": SHA_B},
                "managed_files": ["scripts/runner.sh"],
                "file_hashes": {"scripts/runner.sh": "c" * 64},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/runner.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    _fake_git(
        monkeypatch,
        {
            ("rev-parse", "HEAD"): (True, SHA_A),
            ("rev-parse", "--verify", "refs/remotes/origin/main"): (True, SHA_A),
            ("status", "--porcelain=v1", "--untracked-files=all"): (True, ""),
        },
    )

    findings = checker.audit(
        project_root=tmp_path,
        runtime_root=tmp_path,
        remote="origin",
        branch="main",
        canonical_link=None,
        manifest_path=tmp_path / ".aqsp-release.json",
        overlay_path=overlay,
        active_files=[],
        require_overlay=True,
    )

    assert any(item.code == "overlay_source_mismatch" for item in findings)


def test_release_consistency_rejects_legacy_active_entry(tmp_path: Path) -> None:
    _manifest(tmp_path)
    entry = tmp_path / "active.sh"
    entry.write_text("streamlit run old.py --server.port 8501\n", encoding="utf-8")
    findings = checker.audit(
        project_root=tmp_path,
        runtime_root=tmp_path,
        remote="origin",
        branch="main",
        canonical_link=None,
        manifest_path=tmp_path / ".aqsp-release.json",
        overlay_path=tmp_path / "missing-overlay.json",
        active_files=["active.sh"],
        require_overlay=False,
    )

    assert {item.code for item in findings} >= {"legacy_entry_reference"}


def test_write_release_manifest_contains_commit_and_content_digest(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "tracked.txt").write_text("value\n", encoding="utf-8")
    monkeypatch_values = {
        ("status", "--porcelain", "--untracked-files=all"): "",
        ("rev-parse", "HEAD"): SHA_A,
        ("rev-parse", "HEAD^{tree}"): "tree-sha",
        ("branch", "--show-current"): "main",
        ("config", "--get", "remote.origin.url"): "https://github.com/example/aqsp.git",
        ("ls-files", "-z"): "tracked.txt\0",
    }

    import scripts.write_release_manifest as writer

    def fake_git(_root: Path, *args: str) -> str:
        return monkeypatch_values[args]

    original = writer._git
    writer._git = fake_git
    try:
        payload = build_manifest(tmp_path)
        output = tmp_path / "manifest.json"
        write_manifest(payload, output)
    finally:
        writer._git = original

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["commit"] == SHA_A
    assert saved["file_count"] == 1
    assert len(saved["content_digest"]) == 64


def test_push_with_report_exposes_github_failure(monkeypatch, tmp_path: Path) -> None:
    calls = iter(
        [
            subprocess.CompletedProcess(["git", "rev-parse"], 0, SHA_A + "\n", ""),
            subprocess.CompletedProcess(["git", "status"], 0, "", ""),
            subprocess.CompletedProcess(
                ["git", "push"], 128, "", "HTTP/2 stream was not closed cleanly"
            ),
        ]
    )

    def fake_run(*_args, **_kwargs):
        return next(calls)

    monkeypatch.setattr(push_with_report.subprocess, "run", fake_run)
    code, payload = push_with_report.push(tmp_path, remote="origin", branch="main")

    assert code == 128
    assert payload["status"] == "failed"
    assert "HTTP/2" in payload["output"]


def test_push_with_report_rejects_remote_commit_mismatch(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        push_with_report,
        "_run",
        lambda _root, command: {
            ("git", "rev-parse", "HEAD"): subprocess.CompletedProcess(
                command, 0, SHA_A + "\n", ""
            ),
            (
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
            ): subprocess.CompletedProcess(command, 0, "", ""),
            (
                "git",
                "push",
                "--porcelain",
                "origin",
                "HEAD:refs/heads/main",
            ): subprocess.CompletedProcess(command, 0, "", ""),
            (
                "git",
                "ls-remote",
                "origin",
                "refs/heads/main",
            ): subprocess.CompletedProcess(
                command, 0, SHA_B + "\trefs/heads/main\n", ""
            ),
        }[tuple(command)],
    )

    code, payload = push_with_report.push(tmp_path, remote="origin", branch="main")

    assert code == 1
    assert payload["status"] == "failed"
    assert payload["reason"] == "remote_commit_mismatch"


def test_deployment_bundle_checks_release_identity_tools() -> None:
    script = (PROJECT_ROOT / "scripts" / "test_vibe_research_deployment.sh").read_text(
        encoding="utf-8"
    )
    assert "check_release_consistency.py" in script
    assert "write_release_manifest.py" in script
    assert "push_with_report.py" in script
    assert "check_runtime_storage.py" in script


def test_release_consistency_rejects_dirty_release(monkeypatch, tmp_path: Path) -> None:
    _manifest(tmp_path)
    _fake_git(
        monkeypatch,
        {
            ("rev-parse", "HEAD"): (True, SHA_A),
            ("rev-parse", "--verify", "refs/remotes/origin/main"): (True, SHA_A),
            ("status", "--porcelain=v1", "--untracked-files=all"): (
                True,
                " M scripts/release_task_entrypoint.sh",
            ),
        },
    )

    findings = checker.audit(
        project_root=tmp_path,
        runtime_root=tmp_path,
        remote="origin",
        branch="main",
        canonical_link=None,
        manifest_path=tmp_path / ".aqsp-release.json",
        overlay_path=tmp_path / "missing-overlay.json",
        active_files=[],
        require_overlay=False,
    )

    assert any(item.code == "release_dirty" for item in findings)


def test_release_consistency_immutable_release_ignores_remote_and_overlay(
    monkeypatch, tmp_path: Path
) -> None:
    manifest = _manifest(tmp_path)
    _fake_git(
        monkeypatch,
        {
            ("rev-parse", "HEAD"): (False, "git metadata unavailable"),
            ("rev-parse", "--verify", "refs/remotes/origin/main"): (True, SHA_B),
            ("status", "--porcelain=v1", "--untracked-files=all"): (
                False,
                "git metadata unavailable",
            ),
        },
    )

    findings = checker.audit(
        project_root=tmp_path,
        runtime_root=tmp_path / "runtime",
        remote="origin",
        branch="main",
        canonical_link=None,
        manifest_path=manifest,
        overlay_path=tmp_path / "runtime" / "overlay.json",
        active_files=[],
        require_overlay=False,
        immutable_release=True,
    )

    assert findings == []


def test_release_consistency_immutable_release_allows_release_generated_files(
    monkeypatch, tmp_path: Path
) -> None:
    manifest = _manifest(tmp_path)
    _fake_git(
        monkeypatch,
        {
            ("rev-parse", "HEAD"): (False, "git metadata unavailable"),
            ("rev-parse", "--verify", "refs/remotes/origin/main"): (True, SHA_B),
            ("status", "--porcelain=v1", "--untracked-files=all"): (
                True,
                "?? .aqsp-release.json\n?? .venv-vibe-research\n",
            ),
        },
    )

    findings = checker.audit(
        project_root=tmp_path,
        runtime_root=tmp_path / "runtime",
        remote="origin",
        branch="main",
        canonical_link=None,
        manifest_path=manifest,
        overlay_path=tmp_path / "runtime" / "overlay.json",
        active_files=[],
        require_overlay=False,
        immutable_release=True,
    )

    assert findings == []


def test_write_release_manifest_refuses_dirty_git_source(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "tracked.txt").write_text("value\n", encoding="utf-8")
    import scripts.write_release_manifest as writer

    original = writer._git

    def fake_git(_root: Path, *args: str) -> str:
        if args == ("status", "--porcelain", "--untracked-files=all"):
            return " M tracked.txt"
        if args == ("rev-parse", "HEAD"):
            return SHA_A
        raise AssertionError(args)

    writer._git = fake_git
    try:
        try:
            writer.build_manifest(tmp_path)
        except ValueError as exc:
            assert "worktree is dirty" in str(exc)
        else:
            raise AssertionError("dirty release source was stamped")
    finally:
        writer._git = original


def test_install_script_acl_covers_every_env_declared_read_path() -> None:
    """ACL 必须覆盖环境文件里声明的**所有**运行时读取路径，而不是只覆盖快照那两个。

    2026-09-18 生产事故：provision 只给 `SNAPSHOT_PATH` / `SNAPSHOT_INDEX_PATH` 加 ACL，
    而环境文件其实声明了 8 条读取路径。`predictions.jsonl`、`debate_results.jsonl`、
    `paper_trades.jsonl`、`news_catalysts_latest.json` 都是 600，隔离用户读不到 ——
    **选股绩效、讨论、纸面交易、消息面雷达四个功能静默降级成空数据**，
    而 provision 的验证只查那两个文件，所以一直报成功、没人发现。

    这条测试守住两件事：读取路径必须**从环境文件派生**（新增路径只改环境文件），
    以及 ACL 应用与逐条验证必须遍历同一个列表。
    """
    script = (
        PROJECT_ROOT / "scripts" / "install_vibe_research_systemd.sh"
    ).read_text(encoding="utf-8")
    env_example = (
        PROJECT_ROOT / "deploy" / "systemd" / "aqsp-vibe-research.env.example"
    ).read_text(encoding="utf-8")

    # 1) 从环境文件派生读取路径（绝对路径型的 AQSP_* 变量），不硬编码
    assert "grep -oE '^AQSP_[A-Z0-9_]+='" in script, (
        "应从环境文件派生读取路径，而不是硬编码文件名"
    )

    # 2) ACL 应用与逐条验证都必须遍历 READ_PATHS。
    #    少一处就会重现「加了 ACL 但没验证」或「验证了但没加 ACL」。
    assert script.count('for read_path in "${READ_PATHS[@]}"') == 2, (
        "ACL 应用与验证必须各遍历一次 READ_PATHS"
    )

    # 3) 曾经静默失效的那几条读取路径必须仍被声明 —— 去掉声明等于让它们失去 ACL
    declared = {
        line.split("=", 1)[0]
        for line in env_example.splitlines()
        if line.startswith("AQSP_") and line.split("=", 1)[-1].startswith("/")
    }
    for required in (
        "AQSP_LEDGER",
        "AQSP_DEBATE_RESULTS",
        "AQSP_PAPER_LEDGER",
        "AQSP_NEWS_JSON_OUTPUT",
    ):
        assert required in declared, (
            f"{required} 必须作为绝对路径读取路径声明在环境示例里 —— "
            "它曾因缺少 ACL 而静默失效（2026-09-18）"
        )
