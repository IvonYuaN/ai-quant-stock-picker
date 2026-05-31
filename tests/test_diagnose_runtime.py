from __future__ import annotations

import struct
from datetime import date

from aqsp.data.tdx_vipdoc_source import TDX_DAY_RECORD
from scripts.diagnose_runtime import (
    PROJECT_ROOT,
    _large_return_rows,
    _runtime_paths,
    _tdx_vipdoc_summary,
)


def test_large_return_rows_flags_contaminated_samples() -> None:
    rows = [
        {"symbol": "600519", "signal_date": "2026-05-29", "return_pct": 2.0},
        {"symbol": "300750", "signal_date": "2025-05-20", "return_pct": -88.9918},
    ]

    flags = _large_return_rows(rows)

    assert flags == [
        {
            "symbol": "300750",
            "signal_date": "2025-05-20",
            "status": None,
            "return_pct": -88.9918,
        }
    ]


def test_tdx_vipdoc_summary_reports_latest_day_file(tmp_path) -> None:
    day_dir = tmp_path / "vipdoc/sh/lday"
    day_dir.mkdir(parents=True)
    rows = [
        (20260528, 1000, 1010, 990, 1005, 100000.0, 1000, 0),
        (20260529, 1005, 1020, 1000, 1015, 120000.0, 1200, 0),
    ]
    day_file = day_dir / "sh600519.day"
    day_file.write_bytes(b"".join(struct.pack(TDX_DAY_RECORD.format, *r) for r in rows))

    summary = _tdx_vipdoc_summary(tmp_path)

    assert summary["present"] is True
    assert summary["day_files"] == 1
    assert summary["symbols_with_records"] == 1
    assert summary["latest"] == date(2026, 5, 29).isoformat()


def test_runtime_paths_follow_daily_run_environment(tmp_path, monkeypatch) -> None:
    ledger = tmp_path / "predictions.jsonl"
    dashboard = tmp_path / "dashboard.html"
    monkeypatch.setenv("AQSP_LEDGER", str(ledger))
    monkeypatch.setenv("AQSP_DASHBOARD", str(dashboard))
    monkeypatch.setenv("AQSP_REPORT", "reports/custom.md")

    paths = _runtime_paths()

    assert paths.ledger == ledger
    assert paths.dashboard == dashboard
    assert paths.latest_report == PROJECT_ROOT / "reports/custom.md"
