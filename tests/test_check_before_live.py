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
    (root / "reports" / "walkforward-grid-latest.md").write_text(
        "### PBO 失败定位\n"
        "CSCV 失败组合占比\n"
        "最差对齐周期\n"
        "训练选中变体\n"
        "测试最优变体\n",
        encoding="utf-8",
    )


def _prepare_ready_runtime(root: Path) -> None:
    (root / ".env").write_text(
        "AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_raw.db\n",
        encoding="utf-8",
    )
    _write_json(
        root / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.24,
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": True,
            "both_pass": True,
            "n_periods": 12,
        },
    )
    _write_jsonl(
        root / "data/predictions.jsonl",
        [
            {
                "signal_date": f"2026-05-{day:02d}",
                "symbol": "600519",
                "status": "watch_only",
            }
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
            "dsr_pass": True,
            "pbo_pass": False,
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
            "dsr_pass": True,
            "pbo_pass": True,
            "both_pass": True,
            "n_periods": 12,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    assert any(
        finding.gate == "walkforward_gate" and not finding.ok for finding in findings
    )


def test_check_before_live_blocks_non_boolean_gate_flags(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.24,
            "pbo_valid": "true",
            "dsr_pass": "true",
            "pbo_pass": "true",
            "both_pass": "true",
            "n_periods": 12,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))
    finding = next(item for item in findings if item.gate == "walkforward_gate")

    assert finding.ok is False
    assert "pbo_valid flag missing/invalid/false" in finding.detail
    assert "both_pass flag missing/invalid/false" in finding.detail


def test_check_before_live_blocks_non_integer_period_count(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.24,
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": True,
            "both_pass": True,
            "n_periods": True,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))
    finding = next(item for item in findings if item.gate == "walkforward_gate")

    assert finding.ok is False
    assert "n_periods missing/invalid" in finding.detail


def test_check_before_live_blocks_boolean_or_nan_metrics(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": True,
            "pbo": "NaN",
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": True,
            "both_pass": True,
            "n_periods": 12,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))
    finding = next(item for item in findings if item.gate == "walkforward_gate")

    assert finding.ok is False
    assert "deflated_sharpe missing/invalid" in finding.detail
    assert "pbo missing/invalid" in finding.detail


def test_check_before_live_blocks_string_numeric_metrics(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": "1.2",
            "pbo": "0.24",
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": True,
            "both_pass": True,
            "n_periods": 12,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))
    finding = next(item for item in findings if item.gate == "walkforward_gate")

    assert finding.ok is False
    assert "deflated_sharpe missing/invalid" in finding.detail
    assert "pbo missing/invalid" in finding.detail


def test_check_before_live_explains_zero_period_walkforward_block(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.24,
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": True,
            "both_pass": True,
            "n_periods": 0,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))
    finding = next(item for item in findings if item.gate == "walkforward_gate")

    assert finding.ok is False
    assert "n_periods=FAIL(0)" in finding.detail
    assert "blockers: n_periods=0" in finding.detail


def test_check_before_live_blocks_when_paper_samples_are_too_small(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_jsonl(
        tmp_path / "data/predictions.jsonl",
        [
            {
                "signal_date": f"2026-05-{day:02d}",
                "symbol": "600519",
                "status": "watch_only",
            }
            for day in range(1, 10)
        ],
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    assert any(
        finding.gate == "paper_sample_size" and not finding.ok for finding in findings
    )


def test_check_before_live_ignores_simulated_and_strategy_grouped_samples(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    rows = [
        {
            "signal_date": f"2026-05-{day:02d}",
            "signal_day_group": f"2026-05-{day:02d}_volume_breakout",
            "symbol": "600519",
            "status": "watch_only",
        }
        for day in range(1, 16)
    ]
    rows.extend(
        {
            "signal_date": f"2026-05-{day:02d}",
            "signal_day_group": f"2026-05-{day:02d}_mock",
            "symbol": "000001",
            "is_simulated": True,
        }
        for day in range(16, 31)
    )
    rows.append(
        {
            "signal_date": "2026-05-01",
            "signal_day_group": "2026-05-01_rps_momentum",
            "symbol": "300750",
            "status": "watch_only",
        }
    )
    _write_jsonl(tmp_path / "data/predictions.jsonl", rows)

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "paper_sample_size")
    assert finding.ok is False
    assert finding.detail == "15/30 real independent signal days"


def test_check_before_live_counts_runtime_signal_date_aliases(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    rows = [
        {
            "signal_day_group": f"2026-05-{day:02d}_ma_pullback",
            "symbol": "600519",
            "status": "watch_only",
        }
        for day in range(1, 11)
    ]
    rows.extend(
        {
            "created_at": f"2026-05-{day:02d}T18:00:00+08:00",
            "symbol": "000001",
            "rating": "watch",
        }
        for day in range(11, 21)
    )
    rows.extend(
        {
            "date": f"2026-05-{day:02d}",
            "symbol": "601318",
            "score": 42.0,
        }
        for day in range(21, 31)
    )
    _write_jsonl(tmp_path / "data/predictions.jsonl", rows)

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "paper_sample_size")
    assert finding.ok is True
    assert finding.detail == "30/30 real independent signal days"


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


def test_check_before_live_merges_history_with_legacy_pipeline_logs(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_jsonl(
        tmp_path / "data/daily_run_history.jsonl",
        [{"date": f"2026-06-{day:02d}", "success": True} for day in range(15, 19)]
        + [{"date": "2026-06-19", "success": False, "exit_code": 1}],
    )
    pipeline_dir = tmp_path / "logs" / "pipeline"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        pipeline_dir / "2026-06-12.json",
        {
            "started_at": "2026-06-12T18:00:00+08:00",
            "finished_at": "2026-06-12T18:01:00+08:00",
            "overall_success": True,
            "steps": [{"name": "策略运行", "success": True}],
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 20))

    finding = next(item for item in findings if item.gate == "successful_daily_runs")
    assert finding.ok is True
    assert (
        finding.detail
        == "5/5 successful daily run days (daily_run_history+pipeline_logs)"
    )


def test_check_before_live_counts_legacy_pipeline_logs_when_history_is_missing(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / "data/daily_run_history.jsonl").unlink()
    pipeline_dir = tmp_path / "logs" / "pipeline"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    for day in range(1, 6):
        _write_json(
            pipeline_dir / f"2026-06-{day:02d}.json",
            {
                "started_at": f"2026-06-{day:02d}T18:00:00+08:00",
                "finished_at": f"2026-06-{day:02d}T18:01:00+08:00",
                "overall_success": True,
                "steps": [
                    {"name": "数据更新", "success": True},
                    {"name": "策略运行", "success": True},
                ],
            },
        )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "successful_daily_runs")
    assert finding.ok is True
    assert finding.detail == "5/5 successful daily run days (pipeline_logs)"


def test_check_before_live_blocks_when_dashboard_output_is_missing(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / "dist/dashboard/index.html").unlink()

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    assert any(
        finding.gate == "dashboard_html" and not finding.ok for finding in findings
    )


def test_check_before_live_blocks_every_10_minute_notify_cron(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cron = tmp_path / "cron.txt"
    cron.write_text(
        "*/10 9-14 * * 1-5 /bin/bash /opt/aqsp/scripts/bt_task.sh daily --notify\n",
        encoding="utf-8",
    )

    findings = check_before_live(
        root=tmp_path,
        today=date(2026, 6, 14),
        cron_path=cron,
    )

    finding = next(item for item in findings if item.gate == "scheduler_notify_cadence")
    assert finding.ok is False
    assert "high-frequency notify risk" in finding.detail


def test_check_before_live_allows_intraday_without_notify(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cron = tmp_path / "cron.txt"
    cron.write_text(
        "*/10 9-14 * * 1-5 /bin/bash /opt/aqsp/scripts/bt_task.sh intraday\n",
        encoding="utf-8",
    )

    findings = check_before_live(
        root=tmp_path,
        today=date(2026, 6, 14),
        cron_path=cron,
    )

    finding = next(item for item in findings if item.gate == "scheduler_notify_cadence")
    assert finding.ok is True


def test_check_before_live_blocks_unstable_gate_notify_state_path(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / ".env").write_text(
        "AQSP_GATE_NOTIFY_STATE_PATH=/tmp/gate_notify_state.json\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "gate_notify_state_path")
    assert finding.ok is False
    assert "unstable external path" in finding.detail


def test_check_before_live_blocks_bt_wrapper_high_frequency_notify(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cron_dir = tmp_path / "bt-cron"
    cron_dir.mkdir()
    (cron_dir / "aqsp-intraday").write_text(
        "#!/bin/bash\n"
        "cd /opt/aqsp\n"
        "/bin/bash /opt/aqsp/scripts/bt_task.sh intraday --notify\n",
        encoding="utf-8",
    )

    findings = check_before_live(
        root=tmp_path,
        today=date(2026, 6, 14),
        cron_dir=cron_dir,
    )

    finding = next(item for item in findings if item.gate == "scheduler_notify_cadence")
    assert finding.ok is False
    assert "intraday" in finding.detail


def test_check_before_live_blocks_bt_wrapper_that_bypasses_bt_task(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cron_dir = tmp_path / "bt-cron"
    cron_dir.mkdir()
    (cron_dir / "aqsp-daily-direct").write_text(
        "#!/bin/bash\ncd /opt/aqsp\n/bin/bash /opt/aqsp/scripts/daily_pipeline.sh\n",
        encoding="utf-8",
    )

    findings = check_before_live(
        root=tmp_path,
        today=date(2026, 6, 14),
        cron_dir=cron_dir,
    )

    finding = next(item for item in findings if item.gate == "scheduler_notify_cadence")
    assert finding.ok is False
    assert "bypasses bt_task.sh" in finding.detail


def test_check_before_live_allows_bt_wrapper_through_unified_entry(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cron_dir = tmp_path / "bt-cron"
    cron_dir.mkdir()
    (cron_dir / "aqsp-daily").write_text(
        "#!/bin/bash\ncd /opt/aqsp\n/bin/bash /opt/aqsp/scripts/bt_task.sh daily\n",
        encoding="utf-8",
    )

    findings = check_before_live(
        root=tmp_path,
        today=date(2026, 6, 14),
        cron_dir=cron_dir,
    )

    finding = next(item for item in findings if item.gate == "scheduler_notify_cadence")
    assert finding.ok is True


def test_check_before_live_requires_pbo_diagnostics_when_gate_failed(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.75,
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": False,
            "both_pass": False,
            "n_periods": 12,
        },
    )
    (tmp_path / "reports" / "walkforward-grid-latest.md").write_text(
        "### PBO 失败定位\nCSCV 失败组合占比\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "pbo_diagnostics")
    assert finding.ok is False
    assert "训练选中变体" in finding.detail


def test_check_before_live_accepts_pbo_diagnostics_when_gate_failed(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.75,
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": False,
            "both_pass": False,
            "n_periods": 12,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "pbo_diagnostics")
    assert finding.ok is True


def test_check_before_live_blocks_qfq_walkforward_price_mode(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / ".env").write_text(
        "AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_qfq.db\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "walkforward_price_mode")
    assert finding.ok is False
    assert "qfq historical database" in finding.detail


def test_check_before_live_allows_raw_gate_metadata_over_qfq_env(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / ".env").write_text(
        "AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_qfq.db\n",
        encoding="utf-8",
    )
    gate_path = tmp_path / "data" / "walkforward_gate.json"
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    payload.update(
        {
            "source": "sqlite_db",
            "sqlite_db_path": "/opt/market-data/astocks_raw.db",
            "price_mode": "raw",
        }
    )
    _write_json(gate_path, payload)

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "walkforward_price_mode")
    assert finding.ok is True
