#!/usr/bin/env python3
"""Diagnose local AQSP runtime state without contacting brokers or trading."""

from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aqsp.data.registry import list_registry_entries, local_data_status
from aqsp.data.tdx_vipdoc_source import TDX_DAY_RECORD_SIZE


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RuntimePaths:
    ledger: Path
    paper_ledger: Path
    risk_state: Path
    dashboard: Path
    latest_report: Path
    latest_csv: Path


def _runtime_path(env_name: str, default: str) -> Path:
    raw = os.getenv(env_name, default).strip() or default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _runtime_paths() -> RuntimePaths:
    return RuntimePaths(
        ledger=_runtime_path("AQSP_LEDGER", "data/predictions.jsonl"),
        paper_ledger=_runtime_path("AQSP_PAPER_LEDGER", "data/paper_trades.jsonl"),
        risk_state=_runtime_path("AQSP_RISK_STATE", "data/risk_state.json"),
        dashboard=_runtime_path("AQSP_DASHBOARD", "dist/dashboard/index.html"),
        latest_report=_runtime_path("AQSP_REPORT", "reports/latest.md"),
        latest_csv=_runtime_path("AQSP_OUTPUT_CSV", "reports/latest.csv"),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"_invalid_json": line[:120]})
    return rows


def _file_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    return f"present ({path.stat().st_size} bytes)"


def _large_return_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flagged: list[dict[str, Any]] = []
    for row in rows:
        value = row.get("return_pct")
        if not isinstance(value, int | float):
            continue
        if abs(float(value)) <= 30:
            continue
        flagged.append(
            {
                "symbol": row.get("symbol"),
                "signal_date": row.get("signal_date"),
                "status": row.get("status"),
                "return_pct": value,
            }
        )
    return flagged


def _tdx_vipdoc_summary(base: Path | None = None) -> dict[str, Any]:
    root = base or PROJECT_ROOT / "private_data/tdx"
    vipdoc = root / "vipdoc" if (root / "vipdoc").exists() else root
    files = sorted(vipdoc.glob("*/lday/*.day"))
    latest = ""
    symbol_count = 0
    for path in files:
        raw = path.read_bytes()
        if len(raw) < TDX_DAY_RECORD_SIZE:
            continue
        trade_date = struct.unpack_from("<I", raw, len(raw) - TDX_DAY_RECORD_SIZE)[0]
        text = str(trade_date)
        if len(text) != 8:
            continue
        latest = max(latest, f"{text[:4]}-{text[4:6]}-{text[6:]}")
        symbol_count += 1
    return {
        "path": str(vipdoc),
        "present": vipdoc.exists(),
        "day_files": len(files),
        "symbols_with_records": symbol_count,
        "latest": latest,
    }


def _ready_source_lines() -> list[str]:
    lines: list[str] = []
    for entry in list_registry_entries():
        if not entry.runtime_ready:
            continue
        lines.append(
            f"- {entry.id}: local_data={local_data_status(entry)} "
            f"daily={'yes' if entry.supports_daily else 'no'} "
            f"intraday={'yes' if entry.supports_intraday else 'no'} "
            f"realtime={'yes' if entry.supports_realtime else 'no'}"
        )
    return lines


def main() -> int:
    paths = _runtime_paths()
    wrapper = Path.home() / ".aqsp/aqsp_daily_run_wrapper.sh"
    launch_agent = Path.home() / "Library/LaunchAgents/com.aqsp.daily.plist"

    ledger_rows = _read_jsonl(paths.ledger)
    paper_rows = _read_jsonl(paths.paper_ledger)
    latest_signal = max(
        (str(row.get("signal_date", "")) for row in ledger_rows),
        default="",
    )
    report = [
        "# AQSP Runtime Diagnosis",
        "",
        f"- project_root: {PROJECT_ROOT}",
        f"- ledger: {_file_status(paths.ledger)} rows={len(ledger_rows)} latest={latest_signal or '-'}",
        f"- paper_ledger: {_file_status(paths.paper_ledger)} rows={len(paper_rows)}",
        f"- risk_state: {_file_status(paths.risk_state)}",
        f"- launchd_wrapper: {_file_status(wrapper)}",
        f"- launch_agent: {_file_status(launch_agent)}",
        f"- dashboard: {_file_status(paths.dashboard)}",
        f"- latest_report: {_file_status(paths.latest_report)}",
        f"- latest_csv: {_file_status(paths.latest_csv)}",
        "",
        "## Data Sources",
        *_ready_source_lines(),
        "",
        "## Local TDX Vipdoc",
    ]
    tdx = _tdx_vipdoc_summary()
    report.extend(
        [
            f"- path: {tdx['path']}",
            f"- present: {tdx['present']}",
            f"- day_files: {tdx['day_files']}",
            f"- symbols_with_records: {tdx['symbols_with_records']}",
            f"- latest: {tdx['latest'] or '-'}",
            "",
        ]
    )
    report.extend(
        [
        "## Data Quality Flags",
        ]
    )
    flags = _large_return_rows(ledger_rows) + _large_return_rows(paper_rows)
    if flags:
        for flag in flags:
            report.append(f"- large_abs_return: {flag}")
    else:
        report.append("- no large absolute return rows over 30%")

    if paths.risk_state.exists():
        try:
            state = json.loads(paths.risk_state.read_text(encoding="utf-8"))
            report.append(f"- cooldown_until: {state.get('cooldown_until')}")
        except json.JSONDecodeError:
            report.append("- risk_state invalid json")

    print("\n".join(report))
    return 1 if flags else 0


if __name__ == "__main__":
    raise SystemExit(main())
