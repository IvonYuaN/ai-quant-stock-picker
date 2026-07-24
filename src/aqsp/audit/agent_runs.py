"""Append-only coordination records for bounded multi-agent work."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from aqsp.core.time import now_shanghai, parse_iso8601, to_iso8601
from aqsp.utils.jsonl_io import advisory_lock


AgentRunStatus = Literal["running", "completed", "failed", "timed_out", "skipped"]
_TERMINAL_STATUSES = frozenset({"completed", "failed", "timed_out", "skipped"})


@dataclass(frozen=True)
class AgentRunRecord:
    """One immutable state transition for a bounded agent subtask."""

    parent_run_id: str
    agent_run_id: str
    scope: str
    pid: int
    started_at: str
    deadline_at: str
    status: AgentRunStatus
    exit_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> AgentRunRecord:
        return cls(
            parent_run_id=str(payload["parent_run_id"]),
            agent_run_id=str(payload["agent_run_id"]),
            scope=str(payload["scope"]),
            pid=int(payload["pid"]),
            started_at=str(payload["started_at"]),
            deadline_at=str(payload["deadline_at"]),
            status=str(payload["status"]),  # type: ignore[arg-type]
            exit_reason=str(payload.get("exit_reason", "")),
        )


class AgentRunRegistry:
    """File-backed registry enforcing the project's bounded parallelism contract."""

    def __init__(self, path: str | Path, *, max_parallel_per_parent: int = 3) -> None:
        if max_parallel_per_parent < 1:
            raise ValueError("max_parallel_per_parent must be positive")
        self.path = Path(path)
        self.max_parallel_per_parent = max_parallel_per_parent

    def register(
        self,
        *,
        parent_run_id: str,
        agent_run_id: str,
        scope: str,
        pid: int,
        deadline_seconds: float,
        current: datetime | None = None,
    ) -> AgentRunRecord:
        """Register a new active run after enforcing scope and capacity limits."""
        if not parent_run_id or not agent_run_id or not scope:
            raise ValueError("parent_run_id, agent_run_id, and scope are required")
        if pid < 1:
            raise ValueError("pid must be positive")
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        current = current or now_shanghai()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("current must be timezone-aware")
        record = AgentRunRecord(
            parent_run_id=parent_run_id,
            agent_run_id=agent_run_id,
            scope=scope,
            pid=pid,
            started_at=to_iso8601(current),
            deadline_at=to_iso8601(current + timedelta(seconds=deadline_seconds)),
            status="running",
        )
        with advisory_lock(self.path):
            active = self._active_unlocked(current)
            if any(item.agent_run_id == agent_run_id for item in active):
                raise ValueError(f"agent_run_id is already active: {agent_run_id}")
            if any(item.scope == scope for item in active):
                raise ValueError(f"scope is already active: {scope}")
            parent_count = sum(item.parent_run_id == parent_run_id for item in active)
            if parent_count >= self.max_parallel_per_parent:
                raise ValueError(
                    f"parent run reached parallel limit: {self.max_parallel_per_parent}"
                )
            self._append_unlocked(record)
        return record

    def finish(
        self,
        agent_run_id: str,
        *,
        status: AgentRunStatus,
        exit_reason: str,
    ) -> AgentRunRecord:
        """Write a terminal transition without mutating prior audit records."""
        if status not in _TERMINAL_STATUSES:
            raise ValueError("finish requires a terminal status")
        if not exit_reason:
            raise ValueError("exit_reason is required")
        with advisory_lock(self.path):
            latest = self._latest_unlocked().get(agent_run_id)
            if latest is None:
                raise ValueError(f"unknown agent_run_id: {agent_run_id}")
            if latest.status != "running":
                raise ValueError(f"agent_run_id is not running: {agent_run_id}")
            record = AgentRunRecord(
                **{
                    **latest.to_dict(),
                    "status": status,
                    "exit_reason": exit_reason,
                }
            )
            self._append_unlocked(record)
        return record

    def active(self, *, current: datetime | None = None) -> tuple[AgentRunRecord, ...]:
        """Return live records; deadline-expired work is intentionally excluded."""
        current = current or now_shanghai()
        with advisory_lock(self.path):
            return tuple(self._active_unlocked(current))

    def _active_unlocked(self, current: datetime) -> list[AgentRunRecord]:
        return [
            record
            for record in self._latest_unlocked().values()
            if record.status == "running"
            and parse_iso8601(record.deadline_at) > current
        ]

    def _latest_unlocked(self) -> dict[str, AgentRunRecord]:
        if not self.path.exists():
            return {}
        latest: dict[str, AgentRunRecord] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            record = AgentRunRecord.from_dict(payload)
            latest[record.agent_run_id] = record
        return latest

    def _append_unlocked(self, record: AgentRunRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True)
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
