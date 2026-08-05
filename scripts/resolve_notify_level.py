#!/usr/bin/env python3
"""Resolve legacy notification metadata from the latest ledger row."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from aqsp.data.source_health import notification_level_for_health_label


def _latest_row(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    latest: dict[str, Any] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            latest = value
    return latest


def resolve(path: Path, field: str) -> str:
    row = _latest_row(path)
    label = str(row.get("run_source_health_label") or row.get("source_health_label") or "unknown")
    if field == "label":
        return label
    if field == "level":
        return notification_level_for_health_label(label)
    if field == "route":
        return str(row.get("source_route") or row.get("run_source_route") or "unknown")
    raise ValueError(f"unsupported field: {field}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="resolve_notify_level")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--field", choices=("level", "label", "route"), required=True)
    args = parser.parse_args()
    print(resolve(args.ledger, args.field))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
