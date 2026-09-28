#!/usr/bin/env python3
"""Export dashboard CSV/ledger artifacts into a small SQLite database."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from aqsp.core.time import now_shanghai
from aqsp.data.source_health import notification_level_for_health_label
from aqsp.research.summary import load_research_summary
from aqsp.report import RESULT_COLUMNS

CANDIDATE_EXPORT_COLUMNS = RESULT_COLUMNS + [
    "portfolio_action",
    "candidate_status",
    "candidate_blocker",
    "candidate_next_step",
    "candidate_review_window",
    "candidate_review_priority",
]


LEDGER_EXPORT_COLUMNS = [
    "id",
    "signal_date",
    "symbol",
    "name",
    "score",
    "rating",
    "status",
    "return_pct",
    "thresholds_version",
    "strategies",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if not df.empty or list(df.columns):
        return df
    return pd.DataFrame(columns=columns)


def _normalize_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    df = _ensure_columns(df, columns)
    for column in columns:
        if column not in df.columns:
            df[column] = ""
    ordered = list(columns) + [column for column in df.columns if column not in columns]
    return df.loc[:, ordered]


def _latest_runtime_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return next(
        (
            row
            for row in reversed(rows)
            if row.get("run_requested_source") or row.get("run_actual_source")
        ),
        rows[-1] if rows else {},
    )


def export_db(csv_path: Path, ledger_path: Path, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if csv_path.exists():
        try:
            candidates = pd.read_csv(csv_path, dtype=str).fillna("")
        except EmptyDataError:
            candidates = pd.DataFrame()
    else:
        candidates = pd.DataFrame()
    candidates = _normalize_columns(candidates, CANDIDATE_EXPORT_COLUMNS)
    ledger_rows = read_jsonl(ledger_path)
    ledger = pd.DataFrame(ledger_rows)
    for col in ledger.columns:
        ledger[col] = ledger[col].map(
            lambda value: (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else value
            )
        )
    ledger = _normalize_columns(ledger, LEDGER_EXPORT_COLUMNS)
    latest_row = _latest_runtime_row(ledger_rows)
    research_summary = load_research_summary()
    next_action = (
        research_summary.next_actions[0]
        if research_summary is not None and research_summary.next_actions
        else None
    )
    run_meta = pd.DataFrame(
        [
            {
                "generated_at": now_shanghai().isoformat(timespec="seconds"),
                "candidate_count": len(candidates),
                "ledger_count": len(ledger),
                "requested_source": latest_row.get("run_requested_source", ""),
                "actual_source": latest_row.get("run_actual_source", ""),
                "source_health_label": latest_row.get("run_source_health_label", ""),
                "source_health_message": latest_row.get(
                    "run_source_health_message", ""
                ),
                "notify_level": notification_level_for_health_label(
                    str(latest_row.get("run_source_health_label", "") or "")
                ),
                "fallback_used": latest_row.get("run_fallback_used", ""),
                "research_total_findings": (
                    research_summary.total_findings
                    if research_summary is not None
                    else 0
                ),
                "research_absorbed_families": (
                    len(research_summary.absorbed_families)
                    if research_summary is not None
                    else 0
                ),
                "research_report_only_families": (
                    research_summary.report_only_family_count
                    if research_summary is not None
                    else 0
                ),
                "research_gated_families": (
                    research_summary.gated_family_count
                    if research_summary is not None
                    else 0
                ),
                "research_next_action_id": next_action.item_id
                if next_action is not None
                else "",
                "research_next_action_kind": next_action.kind
                if next_action is not None
                else "",
                "research_next_action_priority": next_action.priority
                if next_action is not None
                else "",
                "research_next_action_blocker": next_action.blocker
                if next_action is not None
                else "",
            }
        ]
    )
    with sqlite3.connect(db_path) as conn:
        candidates.to_sql("latest_candidates", conn, if_exists="replace", index=False)
        ledger.to_sql("ledger", conn, if_exists="replace", index=False)
        run_meta.to_sql("run_meta", conn, if_exists="replace", index=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="reports/close.csv")
    parser.add_argument("--ledger", default="data/predictions.jsonl")
    parser.add_argument("--db", default="dist/dashboard/aqsp.db")
    args = parser.parse_args()
    export_db(Path(args.csv), Path(args.ledger), Path(args.db))
    print(f"db={args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
