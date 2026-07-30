#!/usr/bin/env python3
"""CLI bridge between bounded shell tasks and the agent-run audit registry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aqsp.audit.agent_runs import AgentRunRegistry


_SKIP_START_ERRORS = (
    "agent_run_id is already active",
    "scope is already active",
    "parallel limit reached",
)


def _start(args: argparse.Namespace) -> int:
    registry = AgentRunRegistry(Path(args.path))
    record = registry.register(
        parent_run_id=args.parent_run_id,
        agent_run_id=args.agent_run_id,
        scope=args.scope,
        pid=args.pid,
        deadline_seconds=args.deadline_seconds,
    )
    print(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


def _finish(args: argparse.Namespace) -> int:
    registry = AgentRunRegistry(Path(args.path))
    record = registry.finish(
        args.agent_run_id,
        status=args.status,
        exit_reason=args.exit_reason,
    )
    print(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent_run_registry")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("--path", required=True)
    start.add_argument("--parent-run-id", required=True)
    start.add_argument("--agent-run-id", required=True)
    start.add_argument("--scope", required=True)
    start.add_argument("--pid", type=int, required=True)
    start.add_argument("--deadline-seconds", type=float, required=True)

    finish = subparsers.add_parser("finish")
    finish.add_argument("--path", required=True)
    finish.add_argument("--agent-run-id", required=True)
    finish.add_argument(
        "--status",
        choices=("completed", "failed", "timed_out", "skipped"),
        required=True,
    )
    finish.add_argument("--exit-reason", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "start":
            return _start(args)
        return _finish(args)
    except (OSError, ValueError) as exc:
        print(f"agent run registry failed: {exc}")
        if args.command == "start" and any(
            message in str(exc) for message in _SKIP_START_ERRORS
        ):
            return 75
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
