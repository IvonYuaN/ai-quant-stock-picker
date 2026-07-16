#!/usr/bin/env python3
"""Merge the current-day news artifact into the intraday candidate CSV."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from aqsp.market_context import (
    build_market_context_artifact,
    market_context_metrics_for_pick,
)
from aqsp.news.catalysts import load_catalyst_report_artifact
from scripts.backfill_intraday_debate import _pick_from_row


def merge_intraday_news(
    csv_path: Path,
    news_path: Path,
    *,
    output_path: Path | None = None,
) -> int:
    """Annotate current intraday rows with the exact current news artifact."""
    output_path = output_path or csv_path
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if not fieldnames:
        raise ValueError(f"candidate CSV has no header: {csv_path}")

    report = load_catalyst_report_artifact(news_path)
    if report is None:
        raise ValueError(f"news artifact unavailable: {news_path}")
    artifact = build_market_context_artifact(catalyst_report=report)
    run_lines = list(artifact.summary_lines)
    for line in artifact.warnings:
        if line not in run_lines:
            run_lines.append(line)

    known_fields = {
        key
        for row in rows
        for key in row
        if key.startswith("news_catalyst_") or key.startswith("cross_market_")
    }
    for key in (
        "run_market_context_lines",
        "run_market_context_overview",
        "news_catalyst_judgement",
        "news_catalyst_lead",
        "news_catalyst_source",
        "news_catalyst_title",
        "news_catalyst_published_at",
        "news_catalyst_transmission_hypothesis",
        "news_catalyst_confidence",
        "news_catalyst_priority_score",
        "news_catalyst_supports",
        "news_catalyst_opposes",
        "news_catalyst_needs_review",
    ):
        known_fields.add(key)
    fieldnames.extend(key for key in sorted(known_fields) if key not in fieldnames)

    for row in rows:
        symbol = str(row.get("symbol", "") or "").strip()
        if symbol == "__RUN__":
            row["run_market_context_lines"] = "；".join(run_lines)
            row["run_market_context_overview"] = artifact.cross_market_overview
            continue
        if not symbol:
            continue
        pick = _pick_from_row(row)
        metrics = market_context_metrics_for_pick(pick, artifact)
        for key in known_fields:
            if key in metrics:
                value: Any = metrics[key]
                if isinstance(value, (tuple, list)):
                    value = "；".join(str(item) for item in value)
                row[key] = str(value)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output_path)
    return sum(
        1
        for row in rows
        if str(row.get("symbol", "") or "").strip() not in {"", "__RUN__"}
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--news-json", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    count = merge_intraday_news(args.csv, args.news_json, output_path=args.output)
    print(
        f"merged current news context into {args.output or args.csv}: candidates={count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
