from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from scripts import check_deployment_closure as closure
from scripts.remote_runtime_probe import ProbeCheck


SHA_A = "a" * 40
SHA_B = "b" * 40


def _completed(
    command: list[str], code: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, code, stdout, stderr)


def test_deployment_closure_uses_remote_release_commit_when_local_history_diverges(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_run(_root: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
        values = {
            ("git", "rev-parse", "HEAD"): _completed(command, 0, SHA_A + "\n"),
            ("git", "status", "--porcelain", "--untracked-files=all"): _completed(
                command, 0, ""
            ),
            (
                "git",
                "ls-remote",
                "origin",
                "refs/heads/codex/monitor-walkforward",
            ): _completed(
                command, 0, SHA_B + "\trefs/heads/codex/monitor-walkforward\n"
            ),
        }
        return values[tuple(command)]

    monkeypatch.setattr(closure, "_run", fake_run)
    monkeypatch.setattr(
        closure,
        "_probe_remote",
        lambda **_kwargs: closure.ClosureCheck("remote_health", "ok", "ok"),
    )
    monkeypatch.setattr(
        closure,
        "_snapshot_contract",
        lambda **_kwargs: closure.ClosureCheck("snapshot_contract", "ok", "ok"),
    )

    report = closure.assess(
        root=tmp_path,
        remote="origin",
        branch="codex/monitor-walkforward",
        ssh_target="aqsp-server",
        base_url="https://lh.ifidy.cn",
        expected_end="",
        timeout=1.0,
        skip_github_ci=True,
        skip_remote=False,
        skip_snapshot=False,
    )

    assert report.status == "verified"
    assert report.commit == SHA_B
    assert any(
        item.name == "remote_commit" and item.status == "ok" for item in report.checks
    )


def test_deployment_closure_ignores_user_project_profile_only(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_run(_root: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
        assert command == ["git", "status", "--porcelain", "--untracked-files=all"]
        return _completed(command, 0, " M .codex/project-profile.md\n")

    monkeypatch.setattr(closure, "_run", fake_run)

    result = closure._worktree_clean(tmp_path)

    assert result.status == "ok"
    assert "ignored local project profile" in result.detail


def test_deployment_closure_rejects_other_dirty_release_input(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_run(_root: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
        assert command == ["git", "status", "--porcelain", "--untracked-files=all"]
        return _completed(
            command,
            0,
            " M .codex/project-profile.md\n M scripts/daily_pipeline.py\n",
        )

    monkeypatch.setattr(closure, "_run", fake_run)

    result = closure._worktree_clean(tmp_path)

    assert result.status == "failed"
    assert result.detail == " M scripts/daily_pipeline.py"


def test_deployment_closure_ignores_untracked_local_artifacts(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_run(_root: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
        assert command == ["git", "status", "--porcelain", "--untracked-files=all"]
        return _completed(command, 0, "?? notes.txt\n?? data/local.db\n")

    monkeypatch.setattr(closure, "_run", fake_run)

    assert closure._worktree_clean(tmp_path).status == "ok"


def test_deployment_closure_uses_github_api_when_git_remote_times_out(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_run(_root: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["git", "ls-remote"]:
            return _completed(command, 124, stderr="timed out")
        assert command[:3] == ["gh", "api", "repos/IvonYuaN/ai-quant-stock-picker/git/ref/heads/codex/monitor-walkforward"]
        return _completed(command, 0, SHA_B + "\n")

    monkeypatch.setattr(closure, "_run", fake_run)

    commit, check = closure._remote_branch_commit(
        tmp_path, remote="origin", branch="codex/monitor-walkforward"
    )

    assert commit == SHA_B
    assert check.status == "ok"
    assert "GitHub API fallback" in check.detail


def test_deployment_closure_requires_successful_ci(monkeypatch, tmp_path: Path) -> None:
    def fake_run(_root: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return _completed(command, 0, SHA_A + "\n")
        if command[:3] == ["git", "status", "--porcelain"]:
            return _completed(command, 0, "")
        if command[:3] == ["git", "ls-remote", "origin"]:
            return _completed(
                command, 0, SHA_A + "\trefs/heads/codex/monitor-walkforward\n"
            )
        if command[:3] == ["gh", "run", "list"]:
            return _completed(
                command,
                0,
                json.dumps(
                    [
                        {
                            "workflowName": "CI",
                            "headSha": SHA_A,
                            "status": "completed",
                            "conclusion": "failure",
                            "databaseId": 1,
                        }
                    ]
                ),
            )
        raise AssertionError(command)

    monkeypatch.setattr(closure, "_run", fake_run)
    monkeypatch.setattr(
        closure,
        "_probe_remote",
        lambda **_kwargs: closure.ClosureCheck("remote_health", "ok", "ok"),
    )
    monkeypatch.setattr(
        closure,
        "_snapshot_contract",
        lambda **_kwargs: closure.ClosureCheck("snapshot_contract", "ok", "ok"),
    )

    report = closure.assess(
        root=tmp_path,
        remote="origin",
        branch="codex/monitor-walkforward",
        ssh_target="aqsp-server",
        base_url="https://lh.ifidy.cn",
        expected_end="",
        timeout=1.0,
        skip_github_ci=False,
        skip_remote=False,
        skip_snapshot=False,
    )

    assert report.status == "failed"
    assert any(
        item.name == "github_ci" and item.status == "failed" for item in report.checks
    )


def test_deployment_closure_defaults_expected_end_to_latest_completed_day(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict[str, str] = {}

    def fake_assess(**kwargs: object) -> closure.ClosureReport:
        captured["expected_end"] = str(kwargs["expected_end"])
        return closure.ClosureReport(
            status="verified", commit=SHA_A, branch="test", checks=()
        )

    monkeypatch.setattr(
        closure, "latest_completed_trading_day", lambda: date(2026, 7, 27)
    )
    monkeypatch.setattr(closure, "assess", fake_assess)

    assert (
        closure.main(["--root", str(tmp_path), "--skip-remote", "--skip-snapshot"]) == 0
    )
    assert captured["expected_end"] == "2026-07-27"


def test_deployment_closure_rejects_unreachable_remote(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        closure,
        "build_probe_report",
        lambda **_kwargs: (
            ProbeCheck("ssh_target", "info", "target"),
            ProbeCheck("tcp", "ok", "22"),
            ProbeCheck("ssh_banner", "timeout", "banner timeout"),
            ProbeCheck("http", "failed", "health timeout"),
        ),
    )
    monkeypatch.setattr(
        "scripts.remote_runtime_probe._resolve_ssh_target",
        lambda _target: ("127.0.0.1", 22, "root"),
    )

    check = closure._probe_remote(
        ssh_target="aqsp-server", base_url="https://lh.ifidy.cn", timeout=1.0
    )

    assert check.status == "failed"
    assert "ssh_banner=timeout" in check.detail


def test_snapshot_contract_requires_variant_suite_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        closure,
        "_read_json_url",
        lambda _url, *, timeout: {
            "data": {
                "selected_date": "2026-07-24",
                "available_dates": ["2026-07-24", "2026-07-23"],
                "source": {"latest_trade_date": "2026-07-24"},
                "variant_suite": {
                    "schema_version": "variant-suite-v2",
                    "end_date": "2026-07-24",
                    "selected_symbols": 300,
                    "variant_count": 100,
                },
                "variants": [
                    {
                        "holdings_date": "2026-07-24",
                        "previous_holdings_date": "2026-07-23",
                        "holdings": [],
                        "previous_holdings": [],
                        "recent_actions": [],
                        "adjustments": ["今日/昨日持仓无变化，未发生换票。"],
                        "technical_evidence": [
                            {
                                "macd_hist": 0.12,
                                "kdj_j": 55.0,
                                "volume_ratio": 1.35,
                                "atr_pct": 2.4,
                            }
                        ],
                    }
                ]
                * 100,
            }
        },
    )

    check = closure._snapshot_contract(
        base_url="https://lh.ifidy.cn", expected_end="2026-07-24", timeout=1.0
    )

    assert check.status == "ok"
    assert "selected_symbols=300" in check.detail
    assert "variants=100" in check.detail


def test_snapshot_contract_accepts_explicit_pending_variant_suite(monkeypatch) -> None:
    monkeypatch.setattr(
        closure,
        "_read_json_url",
        lambda _url, *, timeout: {
            "data": {
                "selected_date": "2026-07-24",
                "available_dates": ["2026-07-24"],
                "source": {"latest_trade_date": "2026-07-24"},
                "variant_suite": {
                    "schema_version": "variant-suite-v2",
                    "last_error": "变体等待：当前未到北京时间 21:00",
                },
                "variants": [],
            }
        },
    )

    check = closure._snapshot_contract(
        base_url="https://example.test", expected_end="2026-07-24", timeout=1.0
    )

    assert check.status == "ok"
    assert check.detail.endswith("variant_suite=pending")


def test_snapshot_contract_accepts_current_intraday_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(closure, "today_shanghai", lambda: date(2026, 7, 27))
    monkeypatch.setattr(
        closure,
        "_read_json_url",
        lambda _url, *, timeout: {
            "data": {
                "selected_date": "2026-07-27",
                "available_dates": ["2026-07-27"],
                "source": {"latest_trade_date": "2026-07-27"},
                "variant_suite": {
                    "schema_version": "variant-suite-v2",
                    "last_error": "变体等待：当前未到北京时间 21:00",
                },
                "variants": [],
            }
        },
    )

    check = closure._snapshot_contract(
        base_url="https://example.test", expected_end="2026-07-24", timeout=1.0
    )

    assert check.status == "ok"
    assert check.detail.endswith("variant_suite=pending")


def test_snapshot_contract_rejects_incomplete_variant_outside_first(
    monkeypatch,
) -> None:
    variant = {
        "holdings_date": "2026-07-24",
        "previous_holdings_date": "2026-07-23",
        "holdings": [],
        "previous_holdings": [],
        "recent_actions": [],
        "adjustments": ["今日/昨日持仓无变化，未发生换票。"],
        "technical_evidence": [
            {
                "macd_hist": 0.12,
                "kdj_j": 55.0,
                "volume_ratio": 1.35,
                "atr_pct": 2.4,
            }
        ],
    }
    variants = [dict(variant) for _ in range(100)]
    variants[65]["technical_evidence"] = []
    monkeypatch.setattr(
        closure,
        "_read_json_url",
        lambda _url, *, timeout: {
            "data": {
                "selected_date": "2026-07-24",
                "available_dates": ["2026-07-24", "2026-07-23"],
                "source": {"latest_trade_date": "2026-07-24"},
                "variant_suite": {
                    "schema_version": "variant-suite-v2",
                    "end_date": "2026-07-24",
                    "selected_symbols": 300,
                    "variant_count": 100,
                },
                "variants": variants,
            }
        },
    )

    check = closure._snapshot_contract(
        base_url="https://lh.ifidy.cn", expected_end="2026-07-24", timeout=1.0
    )

    assert check.status == "failed"
    assert check.detail == "variant[65] technical_evidence incomplete"


def test_snapshot_contract_rejects_wrong_variant_date(monkeypatch) -> None:
    monkeypatch.setattr(
        closure,
        "_read_json_url",
        lambda _url, *, timeout: {
            "data": {
                "selected_date": "2026-07-23",
                "available_dates": ["2026-07-23", "2026-07-22"],
                "source": {"latest_trade_date": "2026-07-23"},
                "variant_suite": {
                    "schema_version": "variant-suite-v2",
                    "end_date": "2026-07-23",
                    "selected_symbols": 300,
                },
                "variants": [
                    {
                        "holdings_date": "2026-07-23",
                        "previous_holdings_date": "2026-07-22",
                        "holdings": [],
                        "previous_holdings": [],
                        "recent_actions": [],
                        "adjustments": ["今日/昨日持仓无变化，未发生换票。"],
                        "technical_evidence": [
                            {
                                "macd_hist": 0.12,
                                "kdj_j": 55.0,
                                "volume_ratio": 1.35,
                                "atr_pct": 2.4,
                            }
                        ],
                    }
                ],
            }
        },
    )

    check = closure._snapshot_contract(
        base_url="https://lh.ifidy.cn", expected_end="2026-07-24", timeout=1.0
    )

    assert check.status == "failed"
    assert "expected 2026-07-24" in check.detail


def test_deployment_closure_script_help_runs_directly() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_deployment_closure.py", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "check_deployment_closure" in result.stdout


def test_snapshot_contract_rejects_missing_source_provenance(monkeypatch) -> None:
    monkeypatch.setattr(
        closure,
        "_read_json_url",
        lambda _url, *, timeout: {
            "data": {
                "selected_date": "2026-07-24",
                "available_dates": ["2026-07-24"],
            }
        },
    )

    check = closure._snapshot_contract(
        base_url="https://lh.ifidy.cn", expected_end="2026-07-24", timeout=1.0
    )

    assert check.status == "failed"
    assert check.detail == "source missing"


def test_snapshot_contract_allows_variant_previous_date_outside_home_date_index(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        closure,
        "_read_json_url",
        lambda _url, *, timeout: {
            "data": {
                "selected_date": "2026-07-24",
                "available_dates": ["2026-07-24"],
                "source": {"latest_trade_date": "2026-07-24"},
                "variant_suite": {
                    "schema_version": "variant-suite-v2",
                    "end_date": "2026-07-24",
                    "selected_symbols": 300,
                },
                "variants": [
                    {
                        "holdings_date": "2026-07-24",
                        "previous_holdings_date": "2026-07-23",
                        "holdings": [],
                        "previous_holdings": [],
                        "recent_actions": [],
                        "adjustments": ["今日/昨日持仓无变化，未发生换票。"],
                        "technical_evidence": [
                            {
                                "macd_hist": 0.12,
                                "kdj_j": 55.0,
                                "volume_ratio": 1.35,
                                "atr_pct": 2.4,
                            }
                        ],
                    }
                ],
            }
        },
    )

    check = closure._snapshot_contract(
        base_url="https://lh.ifidy.cn", expected_end="2026-07-24", timeout=1.0
    )

    assert check.status == "failed"
    assert "variant_suite variant_count" in check.detail


def test_snapshot_contract_rejects_selected_date_ahead_of_market_data(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        closure,
        "_read_json_url",
        lambda _url, *, timeout: {
            "data": {
                "selected_date": "2026-07-27",
                "available_dates": ["2026-07-27", "2026-07-24", "2026-07-23"],
                "source": {"latest_trade_date": "2026-07-24"},
                "variant_suite": {
                    "schema_version": "variant-suite-v2",
                    "end_date": "2026-07-24",
                    "selected_symbols": 300,
                },
                "variants": [
                    {
                        "holdings_date": "2026-07-24",
                        "previous_holdings_date": "2026-07-23",
                        "holdings": [],
                        "previous_holdings": [],
                        "recent_actions": [],
                        "adjustments": ["今日/昨日持仓无变化，未发生换票。"],
                        "technical_evidence": [
                            {
                                "macd_hist": 0.12,
                                "kdj_j": 55.0,
                                "volume_ratio": 1.35,
                                "atr_pct": 2.4,
                            }
                        ],
                    }
                ],
            }
        },
    )

    check = closure._snapshot_contract(
        base_url="https://lh.ifidy.cn", expected_end="2026-07-24", timeout=1.0
    )

    assert check.status == "failed"
    assert "selected_date 2026-07-27 != expected 2026-07-24" in check.detail


def test_snapshot_contract_rejects_missing_variant_technical_evidence(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        closure,
        "_read_json_url",
        lambda _url, *, timeout: {
            "data": {
                "selected_date": "2026-07-24",
                "available_dates": ["2026-07-24", "2026-07-23"],
                "source": {"latest_trade_date": "2026-07-24"},
                "variant_suite": {
                    "schema_version": "variant-suite-v2",
                    "end_date": "2026-07-24",
                    "selected_symbols": 300,
                },
                "variants": [
                    {
                        "holdings_date": "2026-07-24",
                        "previous_holdings_date": "2026-07-23",
                        "holdings": [],
                        "previous_holdings": [],
                        "recent_actions": [],
                        "adjustments": ["今日/昨日持仓无变化，未发生换票。"],
                        "technical_evidence": [],
                    }
                ],
            }
        },
    )

    check = closure._snapshot_contract(
        base_url="https://lh.ifidy.cn", expected_end="2026-07-24", timeout=1.0
    )

    assert check.status == "failed"
    assert "technical_evidence incomplete" in check.detail


def test_snapshot_contract_rejects_short_or_incomplete_variant_snapshot(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        closure,
        "_read_json_url",
        lambda _url, *, timeout: {
            "data": {
                "selected_date": "2026-07-24",
                "available_dates": ["2026-07-24", "2026-07-23"],
                "source": {"latest_trade_date": "2026-07-24"},
                "variant_suite": {
                    "schema_version": "variant-suite-v2",
                    "end_date": "2026-07-24",
                    "selected_symbols": 300,
                    "variant_count": 100,
                },
                "variants": [
                    {
                        "holdings_date": "2026-07-24",
                        "previous_holdings_date": "2026-07-23",
                        "holdings": [],
                        "previous_holdings": [],
                        "recent_actions": [],
                        "adjustments": ["今日/昨日持仓无变化，未发生换票。"],
                        "technical_evidence": [
                            {
                                "macd_hist": 0.12,
                                "kdj_j": 55.0,
                                "volume_ratio": 1.35,
                                "atr_pct": 2.4,
                            }
                        ],
                    }
                ],
            }
        },
    )

    check = closure._snapshot_contract(
        base_url="https://lh.ifidy.cn", expected_end="2026-07-24", timeout=1.0
    )

    assert check.status == "failed"
    assert "snapshot variants < 24" in check.detail


def test_snapshot_contract_rejects_non_numeric_technical_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        closure,
        "_read_json_url",
        lambda _url, *, timeout: {
            "data": {
                "selected_date": "2026-07-24",
                "available_dates": ["2026-07-24", "2026-07-23"],
                "source": {"latest_trade_date": "2026-07-24"},
                "variant_suite": {
                    "schema_version": "variant-suite-v2",
                    "end_date": "2026-07-24",
                    "selected_symbols": 300,
                    "variant_count": 100,
                },
                "variants": [
                    {
                        "holdings_date": "2026-07-24",
                        "previous_holdings_date": "2026-07-23",
                        "holdings": [],
                        "previous_holdings": [],
                        "recent_actions": [],
                        "adjustments": ["今日/昨日持仓无变化，未发生换票。"],
                        "technical_evidence": [
                            {
                                "macd_hist": None,
                                "kdj_j": None,
                                "volume_ratio": None,
                                "atr_pct": None,
                            }
                        ],
                    }
                ],
            }
        },
    )

    check = closure._snapshot_contract(
        base_url="https://lh.ifidy.cn", expected_end="2026-07-24", timeout=1.0
    )

    assert check.status == "failed"
    assert "technical_evidence incomplete" in check.detail
