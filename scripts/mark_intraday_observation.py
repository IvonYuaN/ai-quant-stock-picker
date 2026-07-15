#!/usr/bin/env python3
"""Mark a fresh provisional intraday CSV as observation-only.

This recovery path intentionally does not create or modify the intraday ledger.
It exists for a timeout after the provisional CSV was flushed but before ledger
post-processing completed.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

FRESH_TIERS = {"realtime", "terminal_realtime"}
WATCH_TIERS = {"delayed_realtime"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _freshness(rows: list[dict[str, str]]) -> str:
    run_row = next(
        (row for row in rows if _text(row.get("symbol")) == "__RUN__"),
        {},
    )
    tier = _text(run_row.get("run_source_freshness_tier")).lower()
    try:
        lag_days = int(float(_text(run_row.get("run_data_lag_days"))))
    except ValueError:
        return "unknown"
    if lag_days < 0 or not tier or lag_days > 0:
        return "unknown"
    if tier in WATCH_TIERS:
        return "watch"
    if tier in FRESH_TIERS:
        return "fresh"
    return "unknown"


def mark_intraday_observation(
    csv_path: Path,
    report_path: Path,
    *,
    reason: str,
    minimum_mtime: float = 0.0,
) -> dict[str, Any]:
    """Mark a current, fresh provisional CSV without touching the ledger."""
    if not csv_path.is_file() or not report_path.is_file():
        raise FileNotFoundError("provisional CSV and report are both required")
    if csv_path.stat().st_mtime < minimum_mtime:
        raise ValueError("provisional CSV was not written by the current run")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if "symbol" not in fields or not any(
        _text(row.get("symbol")) == "__RUN__" for row in rows
    ):
        raise ValueError("provisional CSV lacks a runtime marker")

    freshness_status = _freshness(rows)
    if freshness_status not in {"fresh", "watch"}:
        raise ValueError(f"provisional CSV freshness is {freshness_status}")

    extra_fields = (
        "intraday_artifact_mode",
        "observation_only",
        "quality_gate_action",
        "quality_gate_reasons",
        "paper_review_eligible",
        "candidate_status",
        "candidate_blocker",
        "candidate_next_step",
        "candidate_review_window",
        "candidate_review_priority",
        "portfolio_action",
    )
    all_fields = list(dict.fromkeys([*fields, *extra_fields]))
    candidate_count = 0
    for row in rows:
        symbol = _text(row.get("symbol"))
        if symbol == "__RUN__":
            row["quality_gate_status"] = "observation_only"
            row["freshness_status"] = freshness_status
            continue
        if not symbol:
            continue
        candidate_count += 1
        reasons = [
            item
            for item in _text(row.get("quality_gate_reasons")).split(";")
            if item
        ]
        if "observation_only" not in reasons:
            reasons.append("observation_only")
        row.update(
            {
                "intraday_artifact_mode": "observation_only",
                "observation_only": "true",
                "quality_gate_action": "observe",
                "quality_gate_reasons": ";".join(dict.fromkeys(reasons)),
                "paper_review_eligible": "false",
                "candidate_status": "盘中观察",
                "candidate_blocker": "质量门未完整结束，当前仅观察，不作推荐",
                "candidate_next_step": "下一轮质量门完整通过且可成交后，再评估纸面复核",
                "candidate_review_window": "下一轮质量门恢复后",
                "candidate_review_priority": "low",
                "portfolio_action": "observation_only",
            }
        )

    temporary_csv = csv_path.with_suffix(csv_path.suffix + ".observation.tmp")
    with temporary_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=all_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary_csv.replace(csv_path)

    banner = (
        "## 盘中产物模式\n"
        "- observation_only: true\n"
        "- 当前仅作为实时盘中观察展示，不是推荐、纸面复核或正式 ledger 输入。\n"
        f"- 原因: {reason}\n"
        "- 下一轮质量门完整通过后，才恢复候选资格。\n\n"
    )
    report = report_path.read_text(encoding="utf-8")
    if "## 盘中产物模式" not in report:
        report_path.write_text(banner + report, encoding="utf-8")
    return {
        "candidate_count": candidate_count,
        "freshness_status": freshness_status,
        "observation_only": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--minimum-mtime", type=float, default=0.0)
    args = parser.parse_args(argv)
    result = mark_intraday_observation(
        args.csv,
        args.report,
        reason=args.reason,
        minimum_mtime=args.minimum_mtime,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
