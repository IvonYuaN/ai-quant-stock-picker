from __future__ import annotations

import json
from pathlib import Path

from aqsp.core.time import today_shanghai
from aqsp.monitor.checker import MonitorChecker


def _write_ledger(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_config(path: Path, monitors: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'version: "1.2.0"\nmonitors:\n'
        + "".join(
            f"  - name: {m['name']}\n"
            f"    description: {m.get('description', '')}\n"
            f"    enabled: {m.get('enabled', True)}\n"
            f"    check: {m['check']}\n"
            f"    params: {m.get('params', {})}\n"
            f"    severity: {m.get('severity', 'info')}\n"
            for m in monitors
        ),
        encoding="utf-8",
    )


def test_screening_liveness_ok_when_recent(tmp_path: Path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    _write_ledger(
        ledger,
        [{"signal_date": today_shanghai().isoformat(), "symbol": "600000"}],
    )
    cfg = tmp_path / "monitors.yaml"
    _write_config(
        cfg,
        [
            {
                "name": "screening_liveness",
                "check": "screening_liveness",
                "params": {"ledger_path": str(ledger), "max_staleness_trading_days": 2},
                "severity": "critical",
            }
        ],
    )
    results = MonitorChecker(str(cfg)).check_all()
    assert len(results) == 1
    assert results[0].triggered is False
    assert results[0].name == "screening_liveness"


def test_screening_liveness_triggers_when_stale(tmp_path: Path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    _write_ledger(
        ledger,
        [{"signal_date": "2020-01-01", "symbol": "600000"}],
    )
    cfg = tmp_path / "monitors.yaml"
    _write_config(
        cfg,
        [
            {
                "name": "screening_liveness",
                "check": "screening_liveness",
                "params": {"ledger_path": str(ledger), "max_staleness_trading_days": 2},
                "severity": "critical",
            }
        ],
    )
    results = MonitorChecker(str(cfg)).check_all()
    assert results[0].triggered is True
    assert results[0].severity == "critical"


def test_screening_liveness_missing_file_not_triggered(tmp_path: Path) -> None:
    cfg = tmp_path / "monitors.yaml"
    _write_config(
        cfg,
        [
            {
                "name": "screening_liveness",
                "check": "screening_liveness",
                "params": {
                    "ledger_path": str(tmp_path / "nope.jsonl"),
                    "max_staleness_trading_days": 2,
                },
                "severity": "critical",
            }
        ],
    )
    results = MonitorChecker(str(cfg)).check_all()
    assert results[0].triggered is False
    assert "不存在" in results[0].message


def test_empty_picks_triggers_when_no_tradable(tmp_path: Path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    _write_ledger(
        ledger,
        [
            {"signal_date": "2026-08-31", "symbol": "600000", "rating": "watch"},
            {"signal_date": "2026-08-31", "symbol": "600001", "rating": "avoid"},
        ],
    )
    cfg = tmp_path / "monitors.yaml"
    _write_config(
        cfg,
        [
            {
                "name": "empty_picks",
                "check": "empty_picks",
                "params": {"ledger_path": str(ledger)},
                "severity": "warning",
            }
        ],
    )
    results = MonitorChecker(str(cfg)).check_all()
    assert results[0].triggered is True
    assert results[0].severity == "warning"


def test_empty_picks_ok_when_tradable_present(tmp_path: Path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    _write_ledger(
        ledger,
        [
            {
                "signal_date": "2026-08-31",
                "symbol": "600000",
                "rating": "strong_buy_candidate",
            }
        ],
    )
    cfg = tmp_path / "monitors.yaml"
    _write_config(
        cfg,
        [
            {
                "name": "empty_picks",
                "check": "empty_picks",
                "params": {"ledger_path": str(ledger)},
                "severity": "warning",
            }
        ],
    )
    results = MonitorChecker(str(cfg)).check_all()
    assert results[0].triggered is False


def test_empty_picks_ok_when_paper_trades_present(tmp_path: Path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    _write_ledger(
        ledger,
        [{"signal_date": "2026-08-31", "symbol": "600000", "rating": "watch"}],
    )
    paper = tmp_path / "paper_trades.jsonl"
    _write_ledger(
        paper,
        [{"signal_date": "2026-08-31", "symbol": "600000", "status": "closed"}],
    )
    cfg = tmp_path / "monitors.yaml"
    _write_config(
        cfg,
        [
            {
                "name": "empty_picks",
                "check": "empty_picks",
                "params": {
                    "ledger_path": str(ledger),
                    "paper_ledger_path": str(paper),
                },
                "severity": "warning",
            }
        ],
    )
    results = MonitorChecker(str(cfg)).check_all()
    assert results[0].triggered is False


def test_unknown_check_surfaces_triggered(tmp_path: Path) -> None:
    cfg = tmp_path / "monitors.yaml"
    _write_config(
        cfg,
        [
            {
                "name": "typo_check",
                "check": "does_not_exist",
                "params": {},
                "severity": "warning",
            }
        ],
    )
    results = MonitorChecker(str(cfg)).check_all()
    assert results[0].triggered is True
    assert "Unknown check" in results[0].message
