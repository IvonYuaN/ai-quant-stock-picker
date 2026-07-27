from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts import check_deployment_closure as closure
from scripts.remote_runtime_probe import ProbeCheck


SHA_A = "a" * 40
SHA_B = "b" * 40


def _completed(
    command: list[str], code: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, code, stdout, stderr)


def test_deployment_closure_rejects_unpushed_head(monkeypatch, tmp_path: Path) -> None:
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

    assert report.status == "failed"
    assert any(
        item.name == "remote_commit" and item.status == "failed"
        for item in report.checks
    )


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

    assert check.status == "ok"
    assert "selected_symbols=300" in check.detail


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
        ["python3", "scripts/check_deployment_closure.py", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "check_deployment_closure" in result.stdout


def test_snapshot_contract_rejects_frontend_missing_previous_variant_date(
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
    assert "available_dates missing previous_holdings_date" in check.detail


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
    assert "technical_evidence empty" in check.detail
