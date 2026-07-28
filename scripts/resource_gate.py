#!/usr/bin/env python3
"""Fail closed for optional heavy jobs when the host is already constrained."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from aqsp.core.time import now_shanghai, to_iso8601
from aqsp.utils.jsonl_io import atomic_write_text


SKIP_EXIT_CODE = 75


@dataclass(frozen=True)
class HostResources:
    cpu_count: int
    load_1m: float | None
    available_memory_mb: int | None


@dataclass(frozen=True)
class ResourceDecision:
    accepted: bool
    detail: str
    resources: HostResources


def read_host_resources() -> HostResources:
    """Read portable host signals without spawning monitoring processes."""
    cpu_count = max(1, int(os.cpu_count() or 1))
    try:
        load_1m: float | None = float(os.getloadavg()[0])
    except (AttributeError, OSError):
        load_1m = None

    available_memory_mb: int | None = None
    meminfo = Path("/proc/meminfo")
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                available_memory_mb = int(line.split()[1]) // 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    return HostResources(cpu_count, load_1m, available_memory_mb)


def evaluate_resources(
    resources: HostResources,
    *,
    min_free_memory_mb: int,
    max_load_per_cpu: float,
    blocked_locks: Sequence[Path],
) -> ResourceDecision:
    """Keep optional work out of an occupied host; unknown memory stays portable."""
    active_locks = tuple(str(path) for path in blocked_locks if path.exists())
    if active_locks:
        return ResourceDecision(
            False, f"active runtime lock: {','.join(active_locks)}", resources
        )
    if (
        resources.available_memory_mb is not None
        and resources.available_memory_mb < min_free_memory_mb
    ):
        return ResourceDecision(
            False,
            f"free memory {resources.available_memory_mb}MB < {min_free_memory_mb}MB",
            resources,
        )
    if (
        resources.load_1m is not None
        and resources.load_1m / resources.cpu_count > max_load_per_cpu
    ):
        return ResourceDecision(
            False,
            f"load/core {resources.load_1m / resources.cpu_count:.2f} > {max_load_per_cpu:.2f}",
            resources,
        )
    return ResourceDecision(True, "capacity available", resources)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--min-free-memory-mb", type=int, default=768)
    parser.add_argument("--max-load-per-cpu", type=float, default=0.70)
    parser.add_argument("--blocked-lock", action="append", type=Path, default=[])
    parser.add_argument("--status-path", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.min_free_memory_mb < 0:
        raise ValueError("min_free_memory_mb must be non-negative")
    if args.max_load_per_cpu <= 0:
        raise ValueError("max_load_per_cpu must be positive")
    decision = evaluate_resources(
        read_host_resources(),
        min_free_memory_mb=args.min_free_memory_mb,
        max_load_per_cpu=args.max_load_per_cpu,
        blocked_locks=args.blocked_lock,
    )
    payload = {
        "task": args.task,
        "checked_at": to_iso8601(now_shanghai()),
        "accepted": decision.accepted,
        "detail": decision.detail,
        "resources": asdict(decision.resources),
    }
    if args.status_path:
        args.status_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            args.status_path, json.dumps(payload, ensure_ascii=False) + "\n"
        )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if decision.accepted else SKIP_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
