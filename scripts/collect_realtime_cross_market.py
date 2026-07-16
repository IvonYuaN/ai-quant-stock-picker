#!/usr/bin/env python3
"""Collect bounded realtime cross-market context into an atomic sidecar."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from aqsp.core.time import now_shanghai, to_shanghai
from aqsp.data.market_context_source import fetch_live_market_context_payload
from aqsp.market_context import build_realtime_cross_market_context
from aqsp.utils.jsonl_io import atomic_write_text

SCHEMA_VERSION = "1.0"
DEFAULT_OUTPUT_PATH = Path("data/runtime/realtime_cross_market_context.json")
DEFAULT_TIMEOUT_SECONDS = 1.0


def _previous_usable_payload(output: str | Path) -> dict[str, object] | None:
    try:
        previous = json.loads(Path(output).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    payload = previous.get("payload") if isinstance(previous, dict) else None
    if not isinstance(payload, dict):
        return None
    if not any(
        isinstance(item, dict) and item.get("value") is not None
        for item in payload.values()
    ):
        return None
    return payload


def collect_realtime_cross_market(
    output: str | Path,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> dict[str, object]:
    """Fetch and atomically persist a sidecar without propagating source errors."""
    generated = to_shanghai(now or now_shanghai())
    generated_at = generated.isoformat(timespec="seconds")
    previous_payload = _previous_usable_payload(output)
    try:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        payload = fetch_live_market_context_payload(
            timeout_seconds=timeout_seconds,
            now=generated,
        )
        source_status = build_realtime_cross_market_context(
            payload,
            now=generated,
        ).status
        status = (
            source_status if source_status in {"fresh", "partial"} else "unavailable"
        )
        sidecar: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "status": status,
            "payload": payload,
        }
        if status == "unavailable" and previous_payload is not None:
            sidecar.update(
                status="stale_cache",
                payload=previous_payload,
                warning="当前实时源不可用，保留上次观测并按原时间戳判定新鲜度",
            )
        serialized = json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n"
    except Exception as exc:
        print(f"realtime cross-market collection degraded: {exc}", file=sys.stderr)
        sidecar = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "status": "unavailable",
            "payload": {},
        }
        if previous_payload is not None:
            sidecar.update(
                status="stale_cache",
                payload=previous_payload,
                warning="当前实时源异常，保留上次观测并按原时间戳判定新鲜度",
            )
        serialized = json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n"

    atomic_write_text(output, serialized)
    return sidecar


def build_parser() -> argparse.ArgumentParser:
    """Build the sidecar CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the collector; source failures degrade, artifact write failures surface."""
    args = build_parser().parse_args(argv)
    try:
        collect_realtime_cross_market(
            args.output,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as exc:
        print(f"realtime cross-market sidecar write failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
