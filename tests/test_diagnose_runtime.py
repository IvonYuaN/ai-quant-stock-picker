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
