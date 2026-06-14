from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from scripts.check_before_live import check_before_live


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _write_runtime_outputs(root: Path) -> None:
    for rel in (
        "reports/latest.md",
        "reports/briefing.md",
        "reports/closing_review.md",
        "dist/dashboard/index.html",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok\n", encoding="utf-8")


def _prepare_ready_runtime(root: Path) -> None:
    _write_json(
        root / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.24,
            "pbo_valid": True,
            "both_pass": True,
            "n_periods": 12,
        },
    )
    _write_jsonl(
        root / "data/predictions.jsonl",
        [
            {"signal_date": f"2026-05-{day:02d}", "symbol": "600519"}
            for day in range(1, 31)
        ],
    )
    _write_jsonl(
        root / "data/daily_run_history.jsonl",
        [{"date": f"2026-06-{day:02d}", "success": True} for day in range(1, 6)],
    )
    _write_runtime_outputs(root)


def test_check_before_live_passes_when_all_hard_gates_are_met(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    assert all(finding.ok for finding in findings)


def test_check_before_live_blocks_when_walkforward_gate_failed(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.75,
            "pbo_valid": True,
            "both_pass": False,
            "n_periods": 12,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    assert any(
        finding.gate == "walkforward_gate" and not finding.ok for finding in findings
    )


def test_check_before_live_blocks_when_walkforward_metrics_are_invalid(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": "not-a-number",
            "pbo": 0.24,
            "pbo_valid": True,
            "both_pass": True,
            "n_periods": 12,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    assert any(
        finding.gate == "walkforward_gate" and not finding.ok for finding in findings
    )


def test_check_before_live_blocks_when_paper_samples_are_too_small(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_jsonl(
        tmp_path / "data/predictions.jsonl",
        [
            {"signal_date": f"2026-05-{day:02d}", "symbol": "600519"}
            for day in range(1, 10)
        ],
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    assert any(
        finding.gate == "paper_sample_size" and not finding.ok for finding in findings
    )


def test_check_before_live_blocks_when_daily_run_history_is_missing(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / "data/daily_run_history.jsonl").unlink()

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    assert any(
        finding.gate == "successful_daily_runs" and not finding.ok
        for finding in findings
    )


def test_check_before_live_blocks_when_dashboard_output_is_missing(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / "dist/dashboard/index.html").unlink()

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    assert any(
        finding.gate == "dashboard_html" and not finding.ok for finding in findings
    )
