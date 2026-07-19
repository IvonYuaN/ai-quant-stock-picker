from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from scripts.check_intraday_production_contract import (
    EXIT_CONTRACT_FAILED,
    EXIT_OK,
    EXIT_SKIPPED,
    evaluate_status,
    main,
)


TRADE_DATE = date(2026, 7, 17)


def _status(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "completed",
        "task_id": "intraday-2026-07-17-1305",
        "updated_at": "2026-07-17T13:20:00+08:00",
        "candidate_count": 4,
        "actionable_count": 2,
        "blocked_count": 1,
        "execution": {"resource_killed": False, "catalyst_fetch_mode": "thread"},
        "universe": {
            "batch_id": "2026-07-17:1:128",
            "universe_count": 5200,
            "batch_size": 128,
            "coverage_pct": 0.0246,
            "resolved_count": 128,
            "fetched_count": 120,
            "skipped_count": 8,
            "data_coverage_pct": 0.9375,
        },
        "freshness": {"status": "fresh", "checked_count": 128},
    }
    payload.update(overrides)
    return payload


def test_contract_check_accepts_completed_fresh_batch() -> None:
    result = evaluate_status(_status(), trade_date=TRADE_DATE)

    assert result.classification == "success"
    assert result.exit_code == EXIT_OK
    assert all(check.ok for check in result.checks)


def test_contract_check_skips_weekend_without_status() -> None:
    result = evaluate_status(None, trade_date=date(2026, 7, 19))

    assert result.classification == "skipped_non_trading_day"
    assert result.exit_code == EXIT_SKIPPED
    assert result.checks == ()


def test_contract_check_rejects_resource_kill_and_missing_runtime_contract() -> None:
    payload = _status(
        status="partial_failed",
        execution={"resource_killed": True, "catalyst_fetch_mode": "process"},
        universe={
            "batch_id": "",
            "universe_count": 0,
            "batch_size": 0,
            "coverage_pct": 0,
        },
        freshness={"status": "unknown"},
    )

    result = evaluate_status(payload, trade_date=TRADE_DATE)

    assert result.classification == "failed"
    assert result.exit_code == EXIT_CONTRACT_FAILED
    failed = {check.name for check in result.checks if not check.ok}
    assert {
        "resource_killed",
        "catalyst_fetch_mode",
        "universe_batch_id",
        "universe_coverage",
        "freshness_status",
        "run_status",
    } <= failed


def test_contract_check_rejects_inconsistent_batch_coverage() -> None:
    result = evaluate_status(
        _status(
            universe={
                "batch_id": "2026-07-17:1:128",
                "universe_count": 5200,
                "batch_size": 128,
                "coverage_pct": 0.0246,
                "resolved_count": 128,
                "fetched_count": 120,
                "skipped_count": 2,
                "data_coverage_pct": 0.9375,
            }
        ),
        trade_date=TRADE_DATE,
    )

    assert result.classification == "failed"
    assert any(check.name == "coverage_detail" and not check.ok for check in result.checks)


def test_contract_check_rejects_missing_status_on_trading_day() -> None:
    result = evaluate_status(None, trade_date=TRADE_DATE)

    assert result.classification == "failed"
    assert result.exit_code == EXIT_CONTRACT_FAILED
    assert result.checks[0].name == "status_present"


def test_contract_check_rejects_stale_status_date_even_with_candidates() -> None:
    result = evaluate_status(
        _status(
            task_id="intraday-2026-07-16-1305", updated_at="2026-07-16T13:20:00+08:00"
        ),
        trade_date=TRADE_DATE,
    )

    assert result.classification == "failed"
    assert any(check.name == "status_date" and not check.ok for check in result.checks)


def test_contract_check_rejects_batch_larger_than_resolved_universe() -> None:
    result = evaluate_status(
        _status(
            universe={
                "batch_id": "2026-07-17:1:128",
                "universe_count": 64,
                "batch_size": 128,
                "coverage_pct": 0.0246,
            }
        ),
        trade_date=TRADE_DATE,
    )

    assert result.classification == "failed"
    assert any(check.name == "batch_size" and not check.ok for check in result.checks)


def test_cli_reads_status_and_emits_json_without_mutating_it(
    tmp_path: Path,
    capsys,
) -> None:
    status_path = tmp_path / "intraday_refresh_status.json"
    status_path.write_text(json.dumps(_status(), ensure_ascii=False), encoding="utf-8")
    before = status_path.read_bytes()

    exit_code = main(
        [
            "--status-path",
            str(status_path),
            "--date",
            "2026-07-17",
            "--json",
        ]
    )

    assert exit_code == EXIT_OK
    assert status_path.read_bytes() == before
    output = json.loads(capsys.readouterr().out)
    assert output["classification"] == "success"
    assert output["observed"]["candidate_count"] == 4


def test_cli_returns_skip_code_for_weekend_without_status(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code = main(
        [
            "--status-path",
            str(tmp_path / "missing.json"),
            "--date",
            "2026-07-19",
            "--json",
        ]
    )

    assert exit_code == EXIT_SKIPPED
    assert (
        json.loads(capsys.readouterr().out)["classification"]
        == "skipped_non_trading_day"
    )
