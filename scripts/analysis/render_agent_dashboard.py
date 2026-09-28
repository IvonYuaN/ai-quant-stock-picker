"""Retire the legacy standalone Agent page in favor of the live Dashboard."""

from __future__ import annotations

from pathlib import Path

from aqsp.web.entrypoint import write_agent_archive_guard


def render_agent_dashboard(
    performance_path: str = "data/debate_performance.jsonl",
    debate_path: str = "data/debate_results.jsonl",
    output_path: str = "dist/dashboard/agents.html",
) -> None:
    """Write a canonical redirect while preserving the legacy call signature."""
    _ = performance_path, debate_path
    write_agent_archive_guard(Path(output_path))


if __name__ == "__main__":
    render_agent_dashboard()
