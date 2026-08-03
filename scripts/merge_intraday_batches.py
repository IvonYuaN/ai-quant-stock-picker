#!/usr/bin/env python3
"""Atomically accumulate same-day intraday candidates across cursor batches."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


_REQUIRED_TECHNICAL_FIELDS = ("volume_ratio", "macd_hist", "kdj_j")
_TECHNICAL_DOWNGRADE_FIELDS = (
    "technical_quality_status",
    "quality_gate_action",
    "observation_only",
    "research_recommendation",
    "paper_review_eligible",
    "candidate_status",
    "candidate_blocker",
)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _row_date(row: dict[str, str]) -> str:
    return str(row.get("signal_date") or row.get("date") or "").strip()[:10]


def _score(row: dict[str, str]) -> float:
    try:
        return float(row.get("score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _has_finite_number(value: object) -> bool:
    try:
        return math.isfinite(float(str(value).strip()))
    except (TypeError, ValueError):
        return False


def _enforce_technical_evidence(row: dict[str, str]) -> dict[str, str]:
    missing = tuple(
        field
        for field in _REQUIRED_TECHNICAL_FIELDS
        if not _has_finite_number(row.get(field))
    )
    if not missing:
        return row
    downgraded = dict(row)
    reason = f"技术证据不完整: 缺少 {', '.join(missing)}"
    existing_blocker = str(downgraded.get("candidate_blocker") or "").strip()
    existing_status = str(downgraded.get("candidate_status") or "").strip()
    downgraded.update(
        {
            "technical_quality_status": "incomplete",
            "quality_gate_action": "observe",
            "observation_only": "true",
            "research_recommendation": "false",
            "paper_review_eligible": "false",
            "candidate_status": existing_status or "技术证据不完整",
            "candidate_blocker": (
                f"{existing_blocker}；{reason}" if existing_blocker else reason
            ),
        }
    )
    return downgraded


def merge_batches(existing_path: Path, batch_path: Path, *, signal_date: str) -> int:
    """Merge one validated batch without retaining prior dates or duplicate symbols."""
    existing = _read_rows(existing_path)
    batch = _read_rows(batch_path)
    if not batch:
        raise ValueError("intraday batch CSV is empty")
    headers = tuple(
        dict.fromkeys(
            (
                *(key for row in (*existing, *batch) for key in row),
                *_REQUIRED_TECHNICAL_FIELDS,
                *_TECHNICAL_DOWNGRADE_FIELDS,
            )
        )
    )
    if "symbol" not in headers:
        raise ValueError("intraday batch CSV lacks symbol")
    candidates: dict[str, dict[str, str]] = {}
    for row in existing:
        symbol = str(row.get("symbol") or "").strip()
        if symbol and symbol != "__RUN__" and _row_date(row) == signal_date:
            candidates[symbol] = row
    run_row: dict[str, str] | None = None
    for row in batch:
        symbol = str(row.get("symbol") or "").strip()
        if symbol == "__RUN__":
            run_row = row
        elif symbol and _row_date(row) == signal_date:
            candidates[symbol] = row
    if run_row is None:
        raise ValueError("intraday batch CSV lacks run metadata")
    if _row_date(run_row) and _row_date(run_row) != signal_date:
        raise ValueError("intraday batch run metadata date does not match signal date")
    normalized_candidates = tuple(
        _enforce_technical_evidence(row) for row in candidates.values()
    )
    output = [run_row, *sorted(normalized_candidates, key=_score, reverse=True)]
    temporary = existing_path.with_suffix(existing_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output)
    temporary.replace(existing_path)
    return len(candidates)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--existing", required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    print(merge_batches(Path(args.existing), Path(args.batch), signal_date=args.date))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
