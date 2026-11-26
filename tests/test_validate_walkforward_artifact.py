from __future__ import annotations

import json
from datetime import date

from scripts.analysis.validate_walkforward_artifact import audit_artifact


def test_audit_artifact_reports_structured_blockers(tmp_path) -> None:
    path = tmp_path / "gate.json"
    path.write_text(
        json.dumps(
            {
                "run_date": "2026-11-20",
                "data_start": "2024-01-01",
                "data_end": "2026-11-19",
                "deflated_sharpe": -0.4,
                "pbo": 0.8,
                "n_periods": 10,
                "n_variants": 4,
                "effective_symbols": 100,
                "dsr_pass": False,
                "pbo_pass": False,
                "pbo_valid": True,
                "both_pass": False,
                "cscv_reliable": False,
            }
        ),
        encoding="utf-8",
    )

    result = audit_artifact(path, today=date(2026, 11, 26))

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["effective_symbols"] == 100
    assert any("n_variants=4" in reason for reason in result["reasons"])
    assert any("effective_symbols=100" in reason for reason in result["reasons"])


def test_audit_artifact_handles_unreadable_sidecar(tmp_path) -> None:
    result = audit_artifact(tmp_path / "missing.json", today=date(2026, 11, 26))

    assert result["ok"] is False
    assert result["status"] == "unreadable"
    assert result["reasons"]
