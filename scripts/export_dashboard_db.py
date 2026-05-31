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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def export_db(csv_path: Path, ledger_path: Path, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if csv_path.exists():
        try:
            candidates = pd.read_csv(csv_path, dtype=str).fillna("")
        except EmptyDataError:
            candidates = pd.DataFrame()
    else:
        candidates = pd.DataFrame()
    ledger = pd.DataFrame(read_jsonl(ledger_path))
    for col in ledger.columns:
        ledger[col] = ledger[col].map(
            lambda value: json.dumps(value, ensure_ascii=False)
            if isinstance(value, (dict, list))
            else value
        )
    run_meta = pd.DataFrame(
        [
            {
                "generated_at": now_shanghai().isoformat(timespec="seconds"),
                "candidate_count": len(candidates),
                "ledger_count": len(ledger),
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
