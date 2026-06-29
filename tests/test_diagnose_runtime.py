from __future__ import annotations

import os
import struct
from datetime import date

from aqsp.research.summary import (
    ResearchActionItem,
    ResearchFamilySummary,
    ResearchPrereqItem,
    ResearchSummary,
)
from aqsp.data.tdx_vipdoc_source import TDX_DAY_RECORD
from scripts.diagnose_runtime import (
    PROJECT_ROOT,
    _large_return_rows,
    _latest_run_source_runtime,
    _load_dotenv_defaults,
    _runtime_paths,
    _scheduler_runtime_lines,
    _tdx_vipdoc_summary,
    _wrapper_drift_summary,
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
    assert paths.sqlite_db == PROJECT_ROOT / "data/astocks_raw.db"


def test_load_dotenv_defaults_does_not_override_explicit_env(
    tmp_path,
    monkeypatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_qfq.db",
                "AQSP_REPORT='reports/from-env.md'",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("AQSP_SQLITE_DB_PATH", raising=False)
    monkeypatch.setenv("AQSP_REPORT", "reports/explicit.md")

    _load_dotenv_defaults(env_file)

    assert os.environ["AQSP_SQLITE_DB_PATH"] == "/opt/market-data/astocks_qfq.db"
    assert os.environ["AQSP_REPORT"] == "reports/explicit.md"


def test_latest_run_source_runtime_derives_notify_level() -> None:
    rows = [
        {
            "signal_date": "2026-05-20",
            "run_requested_source": "auto",
            "run_actual_source": "eastmoney",
            "run_source_health_label": "fallback",
            "run_source_health_message": "fallback 到 eastmoney；plan成功/失败 5/1，源成功/失败 5/0",
            "run_fallback_used": True,
        }
    ]

    result = _latest_run_source_runtime(rows)

    assert result["notify_level"] == "warning"
    assert result["health_label"] == "fallback"
    assert result["fallback_used"] is True


def test_scheduler_runtime_lines_are_platform_specific() -> None:
    assert _scheduler_runtime_lines("Linux") == [
        "- scheduler: bt_panel_or_cron",
        "- launchd: not_applicable (macOS only)",
    ]

    darwin_lines = _scheduler_runtime_lines("Darwin")
    assert "- scheduler: launchd" in darwin_lines
    assert any(line.startswith("- launchd_wrapper:") for line in darwin_lines)
    assert any(line.startswith("- launch_agent:") for line in darwin_lines)
    assert any(line.startswith("- launchd_wrapper_drift:") for line in darwin_lines)


def test_wrapper_drift_summary_flags_legacy_wrapper(tmp_path) -> None:
    current = tmp_path / "current.sh"
    expected = tmp_path / "expected.sh"
    current.write_text("echo test\n# aqsp paper\n# 周末跳过\n", encoding="utf-8")
    expected.write_text("echo test\n", encoding="utf-8")

    result = _wrapper_drift_summary(current, expected)

    assert result.startswith("drifted_legacy ")


def test_diagnose_runtime_auth_health_lines_include_recorded_sources() -> None:
    from scripts.diagnose_runtime import _auth_health_lines

    lines = _auth_health_lines(
        {
            "auth": {
                "baostock": {
                    "status": "login_failed",
                    "checked_at": "2026-06-02T17:30:00+08:00",
                    "message": "baostock 登录失败",
                }
            }
        }
    )

    assert lines == [
        "- baostock: status=login_failed checked_at=2026-06-02T17:30:00+08:00 message=baostock 登录失败"
    ]


def test_diagnose_runtime_main_reports_research_runtime(
    tmp_path, monkeypatch, capsys
) -> None:
    from scripts import diagnose_runtime

    ledger = tmp_path / "predictions.jsonl"
    paper = tmp_path / "paper.jsonl"
    ledger.write_text("", encoding="utf-8")
    paper.write_text("", encoding="utf-8")
    monkeypatch.setenv("AQSP_LEDGER", str(ledger))
    monkeypatch.setenv("AQSP_PAPER_LEDGER", str(paper))
    monkeypatch.setattr(
        "scripts.diagnose_runtime.load_research_summary",
        lambda: ResearchSummary(
            generated_at="",
            total_findings=113,
            pipeline_summaries=(),
            absorbed_families=(),
            source_candidates=(),
            next_actions=(
                ResearchActionItem(
                    kind="data_source",
                    item_id="tushare",
                    name="Tushare Pro",
                    stage="next_adapter",
                    priority="P1",
                    blocker="token only from env",
                    reference_hint="https://tushare.pro",
                ),
            ),
            prereq_items=(
                ResearchPrereqItem(
                    kind="data_source",
                    item_id="tushare",
                    name="Tushare Pro",
                    status="needs_env",
                    missing_env_vars=("TUSHARE_TOKEN",),
                    fixture_hints=(),
                    user_action="配置 TUSHARE_TOKEN",
                    code_action="接交易日历",
                    registry_runtime_ready=False,
                ),
            ),
            implemented_family_count=5,
            report_only_family_count=1,
            gated_family_count=2,
        ),
    )

    exit_code = diagnose_runtime.main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "## Research Runtime" in output
    assert "- total_findings: 113" in output
    assert "- findings_display: 113 条" in output
    assert "- report_only_families: 1" in output
    assert "- gated_families: 2" in output
    assert "- P1 data_source tushare: token only from env" in output
    assert (
        "- prereq data_source tushare: status=needs_env missing_env=TUSHARE_TOKEN"
        in output
    )


def test_diagnose_runtime_main_labels_config_backed_research_queue(
    tmp_path, monkeypatch, capsys
) -> None:
    from scripts import diagnose_runtime

    ledger = tmp_path / "predictions.jsonl"
    paper = tmp_path / "paper.jsonl"
    ledger.write_text("", encoding="utf-8")
    paper.write_text("", encoding="utf-8")
    monkeypatch.setenv("AQSP_LEDGER", str(ledger))
    monkeypatch.setenv("AQSP_PAPER_LEDGER", str(paper))
    monkeypatch.setattr(
        "scripts.diagnose_runtime.load_research_summary",
        lambda: ResearchSummary(
            generated_at="",
            total_findings=0,
            pipeline_summaries=(),
            absorbed_families=(
                ResearchFamilySummary(
                    family_id="market_regime_timing_filter",
                    name="大盘择时 / 市场状态过滤",
                    status="research_absorbed",
                    runtime_stage="report_only",
                    absorbed_from_count=4,
                    runtime_gate_count=4,
                ),
            ),
            source_candidates=(),
            next_actions=(),
            prereq_items=(),
            implemented_family_count=5,
            report_only_family_count=1,
            gated_family_count=0,
        ),
    )

    exit_code = diagnose_runtime.main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "- total_findings: 0" in output
    assert "- findings_display: 未落盘（按配置吸收队列展示）" in output


def test_diagnose_runtime_main_reports_signal_and_notify_state(
    tmp_path, monkeypatch, capsys
) -> None:
    from scripts import diagnose_runtime

    ledger = tmp_path / "predictions.jsonl"
    paper = tmp_path / "paper.jsonl"
    gate_state = tmp_path / "gate_notify_state.json"
    notify_state = tmp_path / "notify_state.json"
    monitor_state = tmp_path / "monitor_notify_state.json"
    walkforward_status = tmp_path / "walkforward_production_status.json"
    ledger.write_text(
        "\n".join(
            [
                '{"signal_date":"2026-06-20","symbol":"600000","status":"pending","thresholds_version":"v1"}',
                '{"signal_date":"2026-06-21","symbol":"__RUN__","status":"run_completed_no_picks"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paper.write_text(
        '{"signal_date":"2026-06-20","symbol":"600000","status":"pending_entry"}\n',
        encoding="utf-8",
    )
    gate_state.write_text(
        '{"sent_by_date":{"2026-06-21":{"fingerprint":"cold_start|dsr","status":"failed","updated_at":"2026-06-21T18:00:00+08:00"}}}\n',
        encoding="utf-8",
    )
    notify_state.write_text(
        '{"sent":{"pipeline-summary:2026-06-21:gate_block":{"fingerprint":"pipeline-summary:2026-06-21:gate_block","updated_at":"2026-06-21T18:00:00+08:00"}},"pending":{},"failed":{},"updated_at":"2026-06-21T18:00:00+08:00"}\n',
        encoding="utf-8",
    )
    monitor_state.write_text(
        '{"sent":{},"pending":{"data_source_failure":{"fingerprint":"data_source_failure","updated_at":"2026-06-21T18:05:00+08:00"}},"failed":{},"updated_at":"2026-06-21T18:05:00+08:00"}\n',
        encoding="utf-8",
    )
    walkforward_status.write_text(
        '{"status":"timeout","updated_at":"2026-06-21T18:10:00+08:00","effective_symbols":3200,"child_exit_code":124,"detail":"child walkforward timed out"}\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("AQSP_LEDGER", str(ledger))
    monkeypatch.setenv("AQSP_PAPER_LEDGER", str(paper))
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "test_key")
    monkeypatch.setenv(
        "AQSP_WALKFORWARD_PRODUCTION_STATUS", str(walkforward_status)
    )
    monkeypatch.setenv("AQSP_GATE_NOTIFY_STATE_PATH", str(gate_state))
    monkeypatch.setenv("AQSP_NOTIFY_STATE_PATH", str(notify_state))
    monkeypatch.setenv("AQSP_MONITOR_NOTIFY_STATE_PATH", str(monitor_state))
    monkeypatch.setattr(
        "scripts.diagnose_runtime.load_research_summary",
        lambda: None,
    )

    exit_code = diagnose_runtime.main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "- signal_days: 2/30" in output
    assert "- simulated_signal_days: 0" in output
    assert "- paper_days: 1/30" in output
    assert "- walkforward_production_status: timeout updated=2026-06-21T18:10:00+08:00" in output
    assert "- walkforward_production_child_exit: 124" in output
    assert "- configured_notify_channels: serverchan" in output
    assert "- gate_days: 1 latest=2026-06-21" in output
    assert "- gate_latest_status: failed" in output
    assert "- gate_latest_fingerprint: cold_start|dsr" in output
    assert "- gate_legacy_format: False" in output
    assert "- gate_updated_at: -" in output
    assert "- notify_counts: sent=1 pending=0 failed=0" in output
    assert "- monitor_counts: sent=0 pending=1 failed=0" in output


def test_diagnose_runtime_marks_legacy_gate_state_format(
    tmp_path, monkeypatch, capsys
) -> None:
    from scripts import diagnose_runtime

    ledger = tmp_path / "predictions.jsonl"
    paper = tmp_path / "paper.jsonl"
    gate_state = tmp_path / "gate_notify_state.json"
    ledger.write_text("", encoding="utf-8")
    paper.write_text("", encoding="utf-8")
    gate_state.write_text(
        '{"run_date":"2026-06-22","updated_at":"2026-06-22T18:08:40+08:00","sent_by_date":{"2026-06-22":"cold_start|dsr"}}\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("AQSP_LEDGER", str(ledger))
    monkeypatch.setenv("AQSP_PAPER_LEDGER", str(paper))
    monkeypatch.setenv("AQSP_GATE_NOTIFY_STATE_PATH", str(gate_state))
    monkeypatch.setattr("scripts.diagnose_runtime.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        "scripts.diagnose_runtime.load_research_summary",
        lambda: None,
    )

    exit_code = diagnose_runtime.main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "- gate_latest_status: legacy" in output
    assert "- gate_legacy_format: True" in output
    assert "- gate_updated_at: 2026-06-22T18:08:40+08:00" in output


def test_diagnose_runtime_treats_stale_running_walkforward_status_as_timeout(
    tmp_path, monkeypatch, capsys
) -> None:
    from scripts import diagnose_runtime

    ledger = tmp_path / "predictions.jsonl"
    paper = tmp_path / "paper.jsonl"
    walkforward_status = tmp_path / "walkforward_production_status.json"
    ledger.write_text("", encoding="utf-8")
    paper.write_text("", encoding="utf-8")
    walkforward_status.write_text(
        '{"status":"running","updated_at":"2026-06-21T18:10:00+08:00","pid":999999,"effective_symbols":3200,"detail":"child walkforward started"}\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("AQSP_LEDGER", str(ledger))
    monkeypatch.setenv("AQSP_PAPER_LEDGER", str(paper))
    monkeypatch.setenv(
        "AQSP_WALKFORWARD_PRODUCTION_STATUS", str(walkforward_status)
    )
    monkeypatch.setattr(
        "scripts.diagnose_runtime.os.kill",
        lambda *_args: (_ for _ in ()).throw(OSError("missing pid")),
    )
    monkeypatch.setattr(
        "scripts.diagnose_runtime.load_research_summary",
        lambda: None,
    )

    exit_code = diagnose_runtime.main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "- walkforward_production_status: timeout updated=2026-06-21T18:10:00+08:00" in output
    assert "- walkforward_production_child_exit: 124" in output


def test_diagnose_runtime_main_counts_successful_run_days_from_legacy_daily_logs(
    tmp_path, monkeypatch, capsys
) -> None:
    from scripts import diagnose_runtime

    ledger = tmp_path / "predictions.jsonl"
    paper = tmp_path / "paper.jsonl"
    ledger.write_text("", encoding="utf-8")
    paper.write_text("", encoding="utf-8")
    daily_dir = tmp_path / "logs" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    for day in range(1, 4):
        (daily_dir / f"run-2026-06-0{day}.log").write_text(
            "\n".join(
                [
                    f"=== aqsp run @ Mon Jun 0{day} 18:00:00 CST 2026 ===",
                    "=== outputs ===",
                    "ok",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    monkeypatch.setenv("AQSP_LEDGER", str(ledger))
    monkeypatch.setenv("AQSP_PAPER_LEDGER", str(paper))
    monkeypatch.setattr("scripts.diagnose_runtime.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        "scripts.diagnose_runtime.load_research_summary",
        lambda: None,
    )

    exit_code = diagnose_runtime.main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "- successful_run_days: 3 latest=2026-06-03 source=daily_logs" in output


def test_diagnose_runtime_reports_simulated_signal_days(
    tmp_path, monkeypatch, capsys
) -> None:
    from scripts import diagnose_runtime

    ledger = tmp_path / "predictions.jsonl"
    paper = tmp_path / "paper.jsonl"
    ledger.write_text(
        "\n".join(
            [
                '{"signal_date":"2026-06-20","symbol":"600000","status":"pending","thresholds_version":"v1"}',
                '{"signal_date":"2026-06-21","symbol":"600001","status":"pending","thresholds_version":"v1","is_simulated":true}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paper.write_text("", encoding="utf-8")

    monkeypatch.setenv("AQSP_LEDGER", str(ledger))
    monkeypatch.setenv("AQSP_PAPER_LEDGER", str(paper))
    monkeypatch.setattr("scripts.diagnose_runtime.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        "scripts.diagnose_runtime.load_research_summary",
        lambda: None,
    )

    exit_code = diagnose_runtime.main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "- signal_days: 1/30" in output
    assert "- signal_rows: 1" in output
    assert "- latest_real_signal_day: 2026-06-20" in output
    assert "- simulated_signal_days: 1" in output


def test_diagnose_runtime_counts_ledger_run_events_as_successful_run_days(
    tmp_path, monkeypatch, capsys
) -> None:
    from scripts import diagnose_runtime

    ledger = tmp_path / "predictions.jsonl"
    paper = tmp_path / "paper.jsonl"
    ledger.write_text(
        "\n".join(
            [
                '{"signal_date":"2026-06-20","symbol":"__RUN__","status":"blocked_by_circuit_breaker"}',
                '{"signal_date":"2026-06-21","symbol":"__RUN__","status":"run_completed_no_picks"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paper.write_text("", encoding="utf-8")

    monkeypatch.setenv("AQSP_LEDGER", str(ledger))
    monkeypatch.setenv("AQSP_PAPER_LEDGER", str(paper))
    monkeypatch.setattr("scripts.diagnose_runtime.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        "scripts.diagnose_runtime.load_research_summary",
        lambda: None,
    )

    exit_code = diagnose_runtime.main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "- latest_real_signal_day: 2026-06-21" in output
    assert "- blocked_runtime_days: 1" in output
    assert "- successful_run_days: 2 latest=2026-06-21 source=ledger_run_events" in output


def test_diagnose_runtime_ignores_failed_legacy_daily_log_segments(
    tmp_path, monkeypatch, capsys
) -> None:
    from scripts import diagnose_runtime

    ledger = tmp_path / "predictions.jsonl"
    paper = tmp_path / "paper.jsonl"
    ledger.write_text("", encoding="utf-8")
    paper.write_text("", encoding="utf-8")
    daily_dir = tmp_path / "logs" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    (daily_dir / "run-2026-06-01.log").write_text(
        "\n".join(
            [
                "=== aqsp run @ Mon Jun 01 09:00:00 CST 2026 ===",
                "aqsp run failed: 1",
                "=== aqsp run @ Mon Jun 01 18:00:00 CST 2026 ===",
                "=== outputs ===",
                "ok",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("AQSP_LEDGER", str(ledger))
    monkeypatch.setenv("AQSP_PAPER_LEDGER", str(paper))
    monkeypatch.setattr("scripts.diagnose_runtime.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        "scripts.diagnose_runtime.load_research_summary",
        lambda: None,
    )

    exit_code = diagnose_runtime.main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "- successful_run_days: 1 latest=2026-06-01 source=daily_logs" in output
