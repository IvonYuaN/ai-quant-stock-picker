from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from aqsp.audit.agent_runs import AgentRunRegistry
from aqsp.core.time import SHANGHAI_TZ


def test_agent_run_registry_blocks_same_scope_when_active(tmp_path) -> None:
    registry = AgentRunRegistry(tmp_path / "agent_runs.jsonl")
    current = datetime(2026, 7, 24, 9, tzinfo=SHANGHAI_TZ)
    registry.register(
        parent_run_id="parent-a",
        agent_run_id="agent-a",
        scope="src/aqsp/data",
        pid=101,
        deadline_seconds=60,
        current=current,
    )

    with pytest.raises(ValueError, match="scope is already active"):
        registry.register(
            parent_run_id="parent-b",
            agent_run_id="agent-b",
            scope="src/aqsp/data",
            pid=102,
            deadline_seconds=60,
            current=current,
        )


def test_agent_run_registry_enforces_parent_parallel_limit(tmp_path) -> None:
    registry = AgentRunRegistry(
        tmp_path / "agent_runs.jsonl", max_parallel_per_parent=2
    )
    current = datetime(2026, 7, 24, 9, tzinfo=SHANGHAI_TZ)
    for index in range(2):
        registry.register(
            parent_run_id="parent-a",
            agent_run_id=f"agent-{index}",
            scope=f"scope-{index}",
            pid=101 + index,
            deadline_seconds=60,
            current=current,
        )

    with pytest.raises(ValueError, match="parallel limit"):
        registry.register(
            parent_run_id="parent-a",
            agent_run_id="agent-3",
            scope="scope-3",
            pid=103,
            deadline_seconds=60,
            current=current,
        )


def test_agent_run_registry_releases_scope_after_terminal_record(tmp_path) -> None:
    registry = AgentRunRegistry(tmp_path / "agent_runs.jsonl")
    current = datetime(2026, 7, 24, 9, tzinfo=SHANGHAI_TZ)
    registry.register(
        parent_run_id="parent-a",
        agent_run_id="agent-a",
        scope="tests",
        pid=101,
        deadline_seconds=60,
        current=current,
    )
    finished = registry.finish(
        "agent-a", status="completed", exit_reason="tests_passed"
    )

    assert finished.status == "completed"
    replacement = registry.register(
        parent_run_id="parent-b",
        agent_run_id="agent-b",
        scope="tests",
        pid=102,
        deadline_seconds=60,
        current=current,
    )
    assert replacement.agent_run_id == "agent-b"


def test_agent_run_registry_drops_expired_runs_from_capacity(tmp_path) -> None:
    registry = AgentRunRegistry(tmp_path / "agent_runs.jsonl")
    current = datetime(2026, 7, 24, 9, tzinfo=SHANGHAI_TZ)
    registry.register(
        parent_run_id="parent-a",
        agent_run_id="agent-a",
        scope="src/aqsp/audit",
        pid=101,
        deadline_seconds=1,
        current=current,
    )

    assert registry.active(current=current + timedelta(seconds=2)) == ()
    replacement = registry.register(
        parent_run_id="parent-a",
        agent_run_id="agent-b",
        scope="src/aqsp/audit",
        pid=102,
        deadline_seconds=60,
        current=current + timedelta(seconds=2),
    )
    assert replacement.status == "running"
