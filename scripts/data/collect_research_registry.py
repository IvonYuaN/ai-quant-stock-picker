#!/usr/bin/env python3
"""Print the local research registry for data sources and strategy ideas.

This script is intentionally local-first: network discovery results should be
reviewed, then added to config/data_sources.yaml or config/strategy_sources.yaml
with provenance. That keeps the trading system reproducible instead of letting
search results mutate runtime behavior.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root)

    data_sources = _load_yaml(root / "config" / "data_sources.yaml")
    strategy_sources = _load_yaml(root / "config" / "strategy_sources.yaml")

    print("# Data Source Registry")
    for source in data_sources.get("sources", []):
        ready = "ready" if source.get("runtime_ready") else "candidate"
        print(
            f"- {source['id']} ({ready}): {source['name']} - {source.get('reference', '')}"
        )

    print("\n# Strategy Source Registry")
    for family in strategy_sources.get("families", []):
        print(
            f"- {family['id']} ({family.get('current_status', '')}): {family['name']}"
        )
        print(f"  hypothesis: {family.get('hypothesis', '')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
