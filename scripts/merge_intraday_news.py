#!/usr/bin/env python3
"""Merge the current-day news artifact into the intraday candidate CSV."""

from __future__ import annotations

import argparse
import csv
import json
import ast
from pathlib import Path
from typing import Any

from aqsp.models import PickResult
from aqsp.market_context import (
    build_market_context_artifact,
    market_context_metrics_for_pick,
)
from aqsp.core.time import today_shanghai
from aqsp.news.catalysts import load_catalyst_report_artifact
from aqsp.news.entity_graph import DEFAULT_ENTITY_GRAPH
from aqsp.news.watch_candidates import (
    NewsUniverseInstrument,
    discover_watch_candidates,
)


_TUPLE_FIELDS = {
    "strategies",
    "reasons",
    "risks",
    "cross_market_summaries",
    "cross_market_themes",
    "cross_market_rule_ids",
    "cross_market_validation_signals",
    "cross_market_invalidation_signals",
}


def _float_value(row: dict[str, str], key: str, default: float = 0.0) -> float:
    try:
        value = str(row.get(key, "") or "").strip()
        return default if not value else float(value)
    except (TypeError, ValueError):
        return default


def _text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value or "").strip()
    if not text:
        return ()
    if text[:1] in "[(":
        for parser in (json.loads, ast.literal_eval):
            try:
                return _text_tuple(parser(text))
            except (TypeError, ValueError, SyntaxError, json.JSONDecodeError):
                continue
    for separator in ("；", ";", "|"):
        if separator in text:
            return tuple(part.strip() for part in text.split(separator) if part.strip())
    return (text,)


def _pick_from_row(row: dict[str, str]) -> PickResult:
    close = _float_value(row, "close")
    excluded = {
        "symbol",
        "name",
        "date",
        "close",
        "score",
        "rating",
        "entry_type",
        "ideal_buy",
        "stop_loss",
        "take_profit",
        "position",
        "strategies",
        "reasons",
        "risks",
    }
    metrics: dict[str, Any] = {}
    for key, value in row.items():
        if not key or key in excluded or value in ("", None):
            continue
        metrics[key] = _text_tuple(value) if key in _TUPLE_FIELDS else value
    return PickResult(
        symbol=str(row.get("symbol", "") or "").zfill(6),
        name=str(row.get("name", "") or ""),
        date=str(row.get("date", "") or ""),
        close=close,
        score=_float_value(row, "score"),
        rating=str(row.get("rating", "") or "watch"),
        entry_type=str(row.get("entry_type", "") or "intraday_observation"),
        ideal_buy=_float_value(row, "ideal_buy", close),
        stop_loss=_float_value(row, "stop_loss", close),
        take_profit=_float_value(row, "take_profit", close),
        position=str(row.get("position", "") or ""),
        strategies=_text_tuple(row.get("strategies", "")),
        reasons=_text_tuple(row.get("reasons", "")),
        risks=_text_tuple(row.get("risks", "")),
        metrics=metrics,
    )


def merge_intraday_news(
    csv_path: Path,
    news_path: Path,
    *,
    output_path: Path | None = None,
    symbols: tuple[str, ...] = (),
) -> int:
    """Annotate rows and append current-message observation candidates."""
    output_path = output_path or csv_path
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if not fieldnames:
        raise ValueError(f"candidate CSV has no header: {csv_path}")

    report = load_catalyst_report_artifact(
        news_path,
        expected_date=today_shanghai().isoformat(),
        max_age_seconds=30 * 60,
    )
    if report is None:
        raise ValueError(f"news artifact unavailable: {news_path}")
    existing_symbols = {
        str(row.get("symbol", "") or "").strip()
        for row in rows
        if str(row.get("symbol", "") or "").strip() not in {"", "__RUN__"}
    }
    graph_metadata = {
        symbol: NewsUniverseInstrument(
            symbol=symbol,
            name=entity.canonical,
            sectors=entity.sectors,
        )
        for entity in DEFAULT_ENTITY_GRAPH.entities
        if entity.kind == "company"
        for symbol in entity.symbols
    }
    universe = tuple(
        graph_metadata.get(symbol, NewsUniverseInstrument(symbol=symbol))
        for symbol in dict.fromkeys((*symbols, *sorted(existing_symbols)))
        if symbol.isdigit() and len(symbol) == 6
    )
    artifact = build_market_context_artifact(
        catalyst_report=report,
        news_universe=universe,
    )
    # Formal market context keeps the evidence gate strict. Intraday still
    # records a separate observation-only row for a fresh direct/industry link
    # so the user can see the message without promoting it to a candidate.
    observation_candidates = getattr(artifact, "news_watch_candidates", ()) or ()
    if not observation_candidates:
        observation_candidates = discover_watch_candidates(
            report.events,
            universe,
            require_structured_evidence=False,
        )
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

    observation_rows: list[dict[str, str]] = []
    for watch in observation_candidates:
        symbol = str(getattr(watch, "symbol", "") or "").strip()
        if not symbol or symbol in existing_symbols:
            continue
        sectors = tuple(getattr(watch, "affected_sectors", ()) or ())
        path = tuple(getattr(watch, "transmission_path", ()) or ())
        observation_rows.append(
            {
                "symbol": symbol,
                "name": str(getattr(watch, "name", "") or ""),
                "date": today_shanghai().isoformat(),
                "score": "0",
                "rating": "watch",
                "entry_type": "intraday_news_observation",
                "reasons": str(getattr(watch, "summary", "") or ""),
                "risks": "；".join(
                    str(item)
                    for item in (getattr(watch, "invalidation_signals", ()) or ())
                ),
                "quality_gate_action": "observe",
                "observation_only": "true",
                "paper_review_eligible": "false",
                "candidate_status": "消息产业链观察",
                "news_catalyst_title": str(
                    getattr(watch, "event_title", "") or ""
                ),
                "news_catalyst_summary": str(
                    getattr(watch, "summary", "") or ""
                ),
                "news_catalyst_source": str(getattr(watch, "source", "") or ""),
                "news_catalyst_url": str(
                    getattr(watch, "source_url", "") or ""
                ),
                "news_catalyst_published_at": str(
                    getattr(watch, "published_at", "") or ""
                ),
                "news_catalyst_verification": str(
                    getattr(watch, "verification", "") or ""
                ),
                "news_catalyst_sectors": "；".join(sectors),
                "news_catalyst_transmission_path": "；".join(path),
                "news_catalyst_validation_signals": "；".join(
                    str(item)
                    for item in (getattr(watch, "validation_signals", ()) or ())
                ),
                "news_catalyst_invalidation_signals": "；".join(
                    str(item)
                    for item in (getattr(watch, "invalidation_signals", ()) or ())
                ),
            }
        )
        existing_symbols.add(symbol)
    if observation_rows:
        for row in observation_rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        rows.extend(observation_rows)

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
    parser.add_argument(
        "--symbols",
        default="",
        help="当前实时批次代码，逗号分隔；仅用于消息观察候选扩展",
    )
    args = parser.parse_args()
    symbols = tuple(item.strip() for item in args.symbols.split(",") if item.strip())
    count = merge_intraday_news(
        args.csv,
        args.news_json,
        output_path=args.output,
        symbols=symbols,
    )
    print(
        f"merged current news context into {args.output or args.csv}: candidates={count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
