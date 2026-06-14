#!/usr/bin/env python3
"""Fail-closed readiness gate for human-reviewed semi-live operation."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from aqsp.core.time import today_shanghai


MIN_INDEPENDENT_SIGNAL_DAYS = 30
MIN_SUCCESSFUL_RUN_DAYS = 5
MAX_GATE_AGE_DAYS = 35
MIN_DSR = 1.0
MAX_PBO = 0.5


@dataclass(frozen=True)
class ReadinessFinding:
    gate: str
    ok: bool
    detail: str


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _file_has_content(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def check_before_live(
    *,
    root: Path,
    today: date,
    gate_path: Path | None = None,
    ledger_path: Path | None = None,
    run_history_path: Path | None = None,
) -> list[ReadinessFinding]:
    gate_path = gate_path or root / "data" / "walkforward_gate.json"
    ledger_path = ledger_path or root / "data" / "predictions.jsonl"
    run_history_path = run_history_path or root / "data" / "daily_run_history.jsonl"

    findings: list[ReadinessFinding] = []
    findings.append(_check_walkforward_gate(gate_path, today))
    findings.append(_check_paper_sample_size(ledger_path))
    findings.append(_check_successful_runs(run_history_path))
    findings.extend(_check_runtime_outputs(root))
    return findings


def _check_walkforward_gate(path: Path, today: date) -> ReadinessFinding:
    gate = _read_json(path)
    if not gate:
        return ReadinessFinding(
            "walkforward_gate",
            False,
            f"missing or unreadable gate: {path}",
        )

    run_date = _parse_date(gate.get("run_date"))
    if run_date is None:
        return ReadinessFinding("walkforward_gate", False, "run_date missing/invalid")
    age_days = (today - run_date).days
    if age_days > MAX_GATE_AGE_DAYS:
        return ReadinessFinding(
            "walkforward_gate",
            False,
            f"gate stale: {age_days} days > {MAX_GATE_AGE_DAYS}",
        )

    dsr = _parse_float(gate.get("deflated_sharpe"))
    pbo = _parse_float(gate.get("pbo"))
    n_periods = _parse_int(gate.get("n_periods"))
    if dsr is None or pbo is None or n_periods is None:
        return ReadinessFinding(
            "walkforward_gate",
            False,
            "deflated_sharpe, pbo or n_periods missing/invalid",
        )
    pbo_valid = bool(gate.get("pbo_valid", pbo > 0.0))
    both_pass = bool(gate.get("both_pass"))
    ok = (
        both_pass
        and dsr > MIN_DSR
        and pbo_valid
        and 0.0 < pbo < MAX_PBO
        and n_periods > 0
    )
    return ReadinessFinding(
        "walkforward_gate",
        ok,
        (
            f"DSR={dsr:.4f} > {MIN_DSR}, PBO={pbo:.2%} < {MAX_PBO:.0%}, "
            f"pbo_valid={pbo_valid}, n_periods={n_periods}, age_days={age_days}"
        ),
    )


def _check_paper_sample_size(path: Path) -> ReadinessFinding:
    rows = _read_jsonl(path)
    signal_days = {
        str(row.get("signal_day_group") or row.get("signal_date") or "").strip()
        for row in rows
        if str(row.get("signal_day_group") or row.get("signal_date") or "").strip()
    }
    count = len(signal_days)
    return ReadinessFinding(
        "paper_sample_size",
        count >= MIN_INDEPENDENT_SIGNAL_DAYS,
        f"{count}/{MIN_INDEPENDENT_SIGNAL_DAYS} independent signal days",
    )


def _check_successful_runs(path: Path) -> ReadinessFinding:
    rows = _read_jsonl(path)
    successful_days = {
        str(row.get("date") or row.get("run_date") or "").strip()
        for row in rows
        if row.get("success") is True or row.get("exit_code") == 0
    }
    count = len({day for day in successful_days if day})
    return ReadinessFinding(
        "successful_daily_runs",
        count >= MIN_SUCCESSFUL_RUN_DAYS,
        f"{count}/{MIN_SUCCESSFUL_RUN_DAYS} successful daily run days",
    )


def _check_runtime_outputs(root: Path) -> list[ReadinessFinding]:
    required = (
        ("latest_report", root / "reports" / "latest.md"),
        ("briefing_report", root / "reports" / "briefing.md"),
        ("closing_review", root / "reports" / "closing_review.md"),
        ("dashboard_html", root / "dist" / "dashboard" / "index.html"),
    )
    return [
        ReadinessFinding(name, _file_has_content(path), str(path))
        for name, path in required
    ]


def _print_findings(findings: list[ReadinessFinding]) -> None:
    ready = all(finding.ok for finding in findings)
    print("BEFORE_LIVE_STATUS=" + ("PASS" if ready else "BLOCK"))
    for finding in findings:
        status = "PASS" if finding.ok else "BLOCK"
        print(f"[{status}] {finding.gate}: {finding.detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="AQSP runtime/project root")
    parser.add_argument("--today", default="", help="Override today as YYYY-MM-DD")
    parser.add_argument("--gate", default="", help="Override walkforward gate path")
    parser.add_argument("--ledger", default="", help="Override predictions ledger path")
    parser.add_argument(
        "--run-history", default="", help="Override daily run history jsonl"
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    today = _parse_date(args.today) or today_shanghai()
    findings = check_before_live(
        root=root,
        today=today,
        gate_path=Path(args.gate) if args.gate else None,
        ledger_path=Path(args.ledger) if args.ledger else None,
        run_history_path=Path(args.run_history) if args.run_history else None,
    )
    _print_findings(findings)
    return 0 if all(finding.ok for finding in findings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
