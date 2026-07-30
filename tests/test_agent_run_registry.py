from __future__ import annotations

import json
from pathlib import Path

from scripts import agent_run_registry


def test_agent_run_registry_records_shell_task_lifecycle(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "agent_runs.jsonl"
    start_common = [
        "--path",
        str(path),
        "--parent-run-id",
        "scheduler:2026-07-30",
        "--agent-run-id",
        "bt-task:data-refresh:1:42",
    ]

    assert (
        agent_run_registry.main(
            [
                "start",
                *start_common,
                "--scope",
                "scheduled:data-refresh",
                "--pid",
                "42",
                "--deadline-seconds",
                "480",
            ]
        )
        == 0
    )
    assert (
        agent_run_registry.main(
            [
                "finish",
                "--path",
                str(path),
                "--agent-run-id",
                "bt-task:data-refresh:1:42",
                "--status",
                "completed",
                "--exit-reason",
                "bt_task_exit_0",
            ]
        )
        == 0
    )

    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["status"] for record in records] == ["running", "completed"]
    assert records[-1]["scope"] == "scheduled:data-refresh"


def test_agent_run_registry_returns_skip_code_when_scope_is_active(
    tmp_path: Path,
) -> None:
    path = tmp_path / "agent_runs.jsonl"
    first = [
        "start",
        "--path",
        str(path),
        "--parent-run-id",
        "scheduler:2026-07-30",
        "--scope",
        "scheduled:variant-refresh",
        "--pid",
        "42",
        "--deadline-seconds",
        "300",
    ]

    assert agent_run_registry.main([*first, "--agent-run-id", "first"]) == 0
    assert agent_run_registry.main([*first, "--agent-run-id", "second"]) == 75
