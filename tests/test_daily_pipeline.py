from __future__ import annotations

import argparse
import importlib.util
import logging
from pathlib import Path
import sys

import pandas as pd
import pytest


def _load_daily_pipeline_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "daily_pipeline.py"
    spec = importlib.util.spec_from_file_location(
        "test_daily_pipeline_module", module_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_config_prefers_env_source_when_cli_source_missing(monkeypatch) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    monkeypatch.setenv("AQSP_SOURCE", "eastmoney")

    args = argparse.Namespace(
        project_root="",
        source="",
        mode="",
        limit=0,
        max_universe=0,
        min_avg_amount=0,
        max_data_lag_days=0,
        enable_online_factors=False,
        ledger="",
        report="",
        csv="",
        briefing="",
        dashboard_html="",
        dashboard_db="",
        paper_ledger="",
        closing_review="",
        notify=False,
        dry_run=False,
        enable_debate=False,
    )

    config = daily_pipeline._build_config(args)

    assert config.source == "eastmoney"


def test_build_config_enables_debate_from_env(monkeypatch) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")

    args = argparse.Namespace(
        project_root="",
        source="",
        mode="",
        limit=0,
        max_universe=0,
        min_avg_amount=0,
        max_data_lag_days=0,
        enable_online_factors=False,
        ledger="",
        report="",
        csv="",
        briefing="",
        dashboard_html="",
        dashboard_db="",
        paper_ledger="",
        closing_review="",
        notify=False,
        dry_run=False,
        enable_debate=False,
    )

    config = daily_pipeline._build_config(args)

    assert config.enable_debate is True


def test_build_config_enables_notify_and_auto_evolution_from_env(monkeypatch) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    monkeypatch.setenv("AQSP_NOTIFY", "true")
    monkeypatch.setenv("AQSP_ENABLE_AUTO_EVOLUTION", "true")

    args = argparse.Namespace(
        project_root="",
        source="",
        mode="",
        limit=0,
        max_universe=0,
        min_avg_amount=0,
        max_data_lag_days=0,
        enable_online_factors=False,
        ledger="",
        report="",
        csv="",
        briefing="",
        dashboard_html="",
        dashboard_db="",
        paper_ledger="",
        closing_review="",
        notify=False,
        dry_run=False,
        enable_debate=False,
    )

    config = daily_pipeline._build_config(args)

    assert config.notify is True
    assert config.enable_auto_evolution is True


def test_morning_breakout_uses_sh300_pool(monkeypatch) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    captured: list[str] = []

    def fake_main(argv: list[str]) -> int:
        captured[:] = argv
        return 0

    monkeypatch.setattr("aqsp.cli.main", fake_main)
    monkeypatch.setattr(
        daily_pipeline,
        "_resolve_symbols",
        lambda _config, _logger: ["600519", "300750"],
    )

    config = daily_pipeline.PipelineConfig(
        project_root=Path.cwd(),
        source="eastmoney",
        mode="close",
        limit=10,
        max_universe=50,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        allow_online_fallback=True,
        ledger_path="data/predictions.jsonl",
        report_path="reports/latest.md",
        csv_path="reports/latest.csv",
        briefing_path="reports/briefing.md",
        paper_report_path="reports/paper.md",
        dashboard_html="dist/dashboard/index.html",
        dashboard_db="dist/dashboard/aqsp.db",
        paper_ledger="data/paper_trades.jsonl",
        closing_review_path="reports/closing_review.md",
        notify=False,
        notify_mode="summary",
        dry_run=False,
        enable_debate=False,
        enable_auto_evolution=False,
    )

    daily_pipeline._step_morning_breakout(config, logging.getLogger("test"))

    assert captured == [
        "morning-breakout",
        "--source",
        "eastmoney",
        "--symbols",
        "600519,300750",
        "--pool",
        "sh300",
        "--top",
        "5",
    ]


def test_adaptive_learning_converts_rows_to_dataframe(
    monkeypatch, tmp_path: Path
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    ledger_path = tmp_path / "predictions.jsonl"
    ledger_path.write_text("placeholder\n", encoding="utf-8")

    rows = [
        {
            "status": "validated",
            "signal_date": "2026-06-01",
            "return_pct": 1.2,
            "strategies": ["volume_breakout"],
        }
    ]

    monkeypatch.setattr("aqsp.ledger.base.read_ledger", lambda _path: rows)

    class FakeLearner:
        def __init__(self):
            self.config = type("Cfg", (), {"min_independent_signal_days": 14})()

        def compute_weights(self, ledger_df: pd.DataFrame) -> dict[str, float]:
            assert isinstance(ledger_df, pd.DataFrame)
            return {"volume_breakout": 1.1}

    class FakeDecayDetector:
        def detect(self, ledger_df: pd.DataFrame) -> list[object]:
            assert isinstance(ledger_df, pd.DataFrame)
            return []

    monkeypatch.setattr("aqsp.ledger.learner.PerformanceLearner", FakeLearner)
    monkeypatch.setattr("aqsp.ledger.learner.StrategyDecayDetector", FakeDecayDetector)

    config = daily_pipeline.PipelineConfig(
        project_root=tmp_path,
        source="eastmoney",
        mode="close",
        limit=10,
        max_universe=50,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        allow_online_fallback=True,
        ledger_path=ledger_path.name,
        report_path="reports/latest.md",
        csv_path="reports/latest.csv",
        briefing_path="reports/briefing.md",
        paper_report_path="reports/paper.md",
        dashboard_html="dist/dashboard/index.html",
        dashboard_db="dist/dashboard/aqsp.db",
        paper_ledger="data/paper_trades.jsonl",
        closing_review_path="reports/closing_review.md",
        notify=False,
        notify_mode="summary",
        dry_run=False,
        enable_debate=False,
        enable_auto_evolution=False,
    )

    result = daily_pipeline._step_adaptive_learning(config, logging.getLogger("test"))

    assert result["weights_updated"] is True
    assert result["weights"] == {"volume_breakout": 1.1}
    assert result["decay_alerts"] == 0
    assert result["cold_start_skip"] is True


def test_adaptive_learning_skips_decay_alerts_during_cold_start(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    ledger_path = tmp_path / "predictions.jsonl"
    ledger_path.write_text("placeholder\n", encoding="utf-8")

    rows = [
        {
            "status": "validated",
            "signal_date": "2026-06-01",
            "return_pct": -1.2,
            "strategies": ["volume_breakout"],
        },
        {
            "status": "validated",
            "signal_date": "2026-06-02",
            "return_pct": -0.8,
            "strategies": ["volume_breakout"],
        },
    ]

    monkeypatch.setattr("aqsp.ledger.base.read_ledger", lambda _path: rows)

    class FakeLearner:
        def __init__(self):
            self.config = type("Cfg", (), {"min_independent_signal_days": 14})()

        def compute_weights(self, ledger_df: pd.DataFrame) -> dict[str, float]:
            return {"volume_breakout": 1.0}

    class FakeDecayDetector:
        def detect(self, ledger_df: pd.DataFrame) -> list[object]:
            raise AssertionError("decay detector should be skipped during cold start")

    monkeypatch.setattr("aqsp.ledger.learner.PerformanceLearner", FakeLearner)
    monkeypatch.setattr("aqsp.ledger.learner.StrategyDecayDetector", FakeDecayDetector)

    config = daily_pipeline.PipelineConfig(
        project_root=tmp_path,
        source="eastmoney",
        mode="close",
        limit=10,
        max_universe=50,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        allow_online_fallback=True,
        ledger_path=ledger_path.name,
        report_path="reports/latest.md",
        csv_path="reports/latest.csv",
        briefing_path="reports/briefing.md",
        paper_report_path="reports/paper.md",
        dashboard_html="dist/dashboard/index.html",
        dashboard_db="dist/dashboard/aqsp.db",
        paper_ledger="data/paper_trades.jsonl",
        closing_review_path="reports/closing_review.md",
        notify=False,
        notify_mode="summary",
        dry_run=False,
        enable_debate=False,
        enable_auto_evolution=False,
    )

    with caplog.at_level(logging.INFO):
        result = daily_pipeline._step_adaptive_learning(
            config, logging.getLogger("test")
        )

    assert result["decay_alerts"] == 0
    assert result["cold_start_skip"] is True
    assert "冷启动未满" in caplog.text


def test_auto_evolution_step_reads_output_when_success(
    monkeypatch, tmp_path: Path
) -> None:
    daily_pipeline = _load_daily_pipeline_module()

    def fake_main(argv: list[str]) -> int:
        output = Path(argv[argv.index("--output") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            '{"strategy_name":"composite","confidence":0.82,"performance_improvement":0.11,"reason":"regime adaptation"}',
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr("aqsp.cli.main", fake_main)

    config = daily_pipeline.PipelineConfig(
        project_root=tmp_path,
        source="eastmoney",
        mode="close",
        limit=10,
        max_universe=50,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        allow_online_fallback=True,
        ledger_path="data/predictions.jsonl",
        report_path="reports/latest.md",
        csv_path="reports/latest.csv",
        briefing_path="reports/briefing.md",
        paper_report_path="reports/paper.md",
        dashboard_html="dist/dashboard/index.html",
        dashboard_db="dist/dashboard/aqsp.db",
        paper_ledger="data/paper_trades.jsonl",
        closing_review_path="reports/closing_review.md",
        notify=False,
        notify_mode="summary",
        dry_run=False,
        enable_debate=False,
        enable_auto_evolution=True,
    )

    result = daily_pipeline._step_auto_evolution(config, logging.getLogger("test"))

    assert result["evolved"] is True
    assert result["strategy_name"] == "composite"
    assert result["confidence"] == 0.82


def test_auto_evolution_step_raises_when_cli_fails(
    monkeypatch, tmp_path: Path
) -> None:
    daily_pipeline = _load_daily_pipeline_module()

    monkeypatch.setattr("aqsp.cli.main", lambda _argv: 1)

    config = daily_pipeline.PipelineConfig(
        project_root=tmp_path,
        source="eastmoney",
        mode="close",
        limit=10,
        max_universe=50,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        allow_online_fallback=True,
        ledger_path="data/predictions.jsonl",
        report_path="reports/latest.md",
        csv_path="reports/latest.csv",
        briefing_path="reports/briefing.md",
        paper_report_path="reports/paper.md",
        dashboard_html="dist/dashboard/index.html",
        dashboard_db="dist/dashboard/aqsp.db",
        paper_ledger="data/paper_trades.jsonl",
        closing_review_path="reports/closing_review.md",
        notify=False,
        notify_mode="summary",
        dry_run=False,
        enable_debate=False,
        enable_auto_evolution=True,
    )

    with pytest.raises(Exception, match="策略自进化失败"):
        daily_pipeline._step_auto_evolution(config, logging.getLogger("test"))


def test_closing_review_step_writes_output_and_skips_fanout_notify_in_summary_mode(
    monkeypatch, tmp_path: Path
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    captured: list[str] = []

    def fake_main(argv: list[str]) -> int:
        captured[:] = argv
        report_path = tmp_path / "reports" / "closing_review.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("# 收盘复盘\n", encoding="utf-8")
        return 0

    monkeypatch.setattr("aqsp.cli.main", fake_main)

    config = daily_pipeline.PipelineConfig(
        project_root=tmp_path,
        source="eastmoney",
        mode="close",
        limit=10,
        max_universe=50,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        allow_online_fallback=True,
        ledger_path="data/predictions.jsonl",
        report_path="reports/latest.md",
        csv_path="reports/latest.csv",
        briefing_path="reports/briefing.md",
        paper_report_path="reports/paper.md",
        dashboard_html="dist/dashboard/index.html",
        dashboard_db="dist/dashboard/aqsp.db",
        paper_ledger="data/paper_trades.jsonl",
        closing_review_path="reports/closing_review.md",
        notify=True,
        notify_mode="summary",
        dry_run=False,
        enable_debate=False,
        enable_auto_evolution=False,
    )

    result = daily_pipeline._step_closing_review(config, logging.getLogger("test"))

    assert captured == [
        "closing-review",
        "--output",
        "reports/closing_review.md",
    ]
    assert result["report_path"] == "reports/closing_review.md"


def test_send_pipeline_digest_sends_summary_notification(
    monkeypatch, tmp_path: Path
) -> None:
    daily_pipeline = _load_daily_pipeline_module()

    ledger_path = tmp_path / "data" / "predictions.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        '{"run_requested_source":"auto","run_actual_source":"eastmoney","run_source_health_label":"fallback","run_source_health_message":"fallback 到 eastmoney"}\n',
        encoding="utf-8",
    )
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "symbol": "600519",
                "name": "贵州茅台",
                "date": "2026-06-02",
                "close": 1498.0,
                "score": 71.0,
                "rating": "strong_buy_candidate",
                "entry_type": "close",
                "ideal_buy": 1495.0,
                "stop_loss": 1450.0,
                "take_profit": 1600.0,
                "position": "10%-30%",
                "portfolio_action": "promote",
            },
            {
                "symbol": "300750",
                "name": "宁德时代",
                "date": "2026-06-02",
                "close": 205.0,
                "score": 64.0,
                "rating": "watch",
                "entry_type": "watch",
                "ideal_buy": 0.0,
                "stop_loss": 0.0,
                "take_profit": 0.0,
                "position": "watch",
                "portfolio_action": "downgrade",
            },
        ]
    ).to_csv(reports_dir / "latest.csv", index=False)

    sent: dict[str, str] = {}

    monkeypatch.setattr(
        "aqsp.notifier.send_notification",
        lambda title, content: sent.update({"title": title, "content": content}) or [],
    )

    config = daily_pipeline.PipelineConfig(
        project_root=tmp_path,
        source="eastmoney",
        mode="close",
        limit=10,
        max_universe=50,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        allow_online_fallback=True,
        ledger_path="data/predictions.jsonl",
        report_path="reports/latest.md",
        csv_path="reports/latest.csv",
        briefing_path="reports/briefing.md",
        paper_report_path="reports/paper.md",
        dashboard_html="dist/dashboard/index.html",
        dashboard_db="dist/dashboard/aqsp.db",
        paper_ledger="data/paper_trades.jsonl",
        closing_review_path="reports/closing_review.md",
        notify=True,
        notify_mode="summary",
        dry_run=False,
        enable_debate=False,
        enable_auto_evolution=False,
    )

    result = daily_pipeline.PipelineResult(
        started_at="2026-06-02T18:00:00+08:00",
        finished_at="2026-06-02T18:00:30+08:00",
        duration_seconds=30.0,
        steps=[
            daily_pipeline.StepResult("数据更新", True, 1.0),
            daily_pipeline.StepResult("策略运行", True, 2.0),
        ],
        overall_success=True,
        summary="ok",
    )

    daily_pipeline._send_pipeline_digest(config, result, logging.getLogger("test"))

    assert sent["title"] == "收盘总览"
    assert "结论:" in sent["content"]
    assert "数据源状态" in sent["content"]
    assert "主链候选" in sent["content"]
    assert "PM主裁决: 上调 1 / 降级 1 / 维持 0" in sent["content"]
    assert "600519 贵州茅台 | 重点关注 | PM 上调优先级 | 评分 71.0" in sent["content"]
    assert "300750 宁德时代 | 候选观察池 | PM 降级观察 | 评分 64.0" in sent["content"]
    assert "风险与分歧" in sent["content"]
    assert "明日动作" in sent["content"]
    assert "运行侧写" not in sent["content"]
    assert "# 收盘总览" not in sent["content"]


def test_run_step_logs_stable_completion_label(caplog) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    logger = logging.getLogger("test.pipeline")

    with caplog.at_level(logging.INFO, logger="test.pipeline"):
        result = daily_pipeline._run_step("策略运行", lambda: {"ok": True}, logger)

    assert result.success is True
    assert "✓ 完成步骤: 策略运行" in caplog.text


def test_closing_premium_uses_explicit_symbols(monkeypatch) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    captured: list[str] = []

    def fake_main(argv: list[str]) -> int:
        captured[:] = argv
        return 0

    monkeypatch.setattr("aqsp.cli.main", fake_main)
    monkeypatch.setattr(
        daily_pipeline,
        "_resolve_symbols",
        lambda _config, _logger: ["000001", "601318"],
    )

    config = daily_pipeline.PipelineConfig(
        project_root=Path.cwd(),
        source="eastmoney",
        mode="close",
        limit=10,
        max_universe=50,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        allow_online_fallback=True,
        ledger_path="data/predictions.jsonl",
        report_path="reports/latest.md",
        csv_path="reports/latest.csv",
        briefing_path="reports/briefing.md",
        paper_report_path="reports/paper.md",
        dashboard_html="dist/dashboard/index.html",
        dashboard_db="dist/dashboard/aqsp.db",
        paper_ledger="data/paper_trades.jsonl",
        closing_review_path="reports/closing_review.md",
        notify=False,
        notify_mode="summary",
        dry_run=False,
        enable_debate=False,
        enable_auto_evolution=False,
    )

    daily_pipeline._step_closing_premium(config, logging.getLogger("test"))

    assert captured == [
        "closing-premium",
        "--source",
        "eastmoney",
        "--symbols",
        "000001,601318",
        "--pool",
        "sh300",
        "--top",
        "5",
    ]


def test_run_pipeline_excludes_intraday_sub_strategies(monkeypatch) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    executed: list[str] = []

    def fake_run_step(name: str, fn, logger, dry_run: bool = False):
        executed.append(name)
        return daily_pipeline.StepResult(name, True, 0.0)

    monkeypatch.setattr(daily_pipeline, "_run_step", fake_run_step)
    monkeypatch.setattr(daily_pipeline, "_is_trade_day", lambda _d: True)

    config = daily_pipeline.PipelineConfig(
        project_root=Path.cwd(),
        source="eastmoney",
        mode="close",
        limit=10,
        max_universe=50,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        allow_online_fallback=True,
        ledger_path="data/predictions.jsonl",
        report_path="reports/latest.md",
        csv_path="reports/latest.csv",
        briefing_path="reports/briefing.md",
        paper_report_path="reports/paper.md",
        dashboard_html="dist/dashboard/index.html",
        dashboard_db="dist/dashboard/aqsp.db",
        paper_ledger="data/paper_trades.jsonl",
        closing_review_path="reports/closing_review.md",
        notify=False,
        notify_mode="summary",
        dry_run=False,
        enable_debate=False,
        enable_auto_evolution=False,
    )

    result = daily_pipeline.run_pipeline(config)

    assert result.overall_success is True
    assert executed == [
        "数据更新",
        "策略运行",
        "收盘复盘",
        "预测验证",
        "虚拟盘同步",
        "自适应学习",
        "策略自进化",
        "报告生成",
        "Dashboard刷新",
        "数据清理",
    ]


def test_run_pipeline_marks_overall_failure_when_later_step_fails(
    monkeypatch,
) -> None:
    daily_pipeline = _load_daily_pipeline_module()

    def fake_run_step(name: str, fn, logger, dry_run: bool = False):
        return daily_pipeline.StepResult(
            name,
            name != "策略自进化",
            0.0,
            "" if name != "策略自进化" else "数据错误: 策略自进化失败, exit_code=1",
        )

    monkeypatch.setattr(daily_pipeline, "_run_step", fake_run_step)
    monkeypatch.setattr(daily_pipeline, "_is_trade_day", lambda _d: True)

    config = daily_pipeline.PipelineConfig(
        project_root=Path.cwd(),
        source="eastmoney",
        mode="close",
        limit=10,
        max_universe=50,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        allow_online_fallback=True,
        ledger_path="data/predictions.jsonl",
        report_path="reports/latest.md",
        csv_path="reports/latest.csv",
        briefing_path="reports/briefing.md",
        paper_report_path="reports/paper.md",
        dashboard_html="dist/dashboard/index.html",
        dashboard_db="dist/dashboard/aqsp.db",
        paper_ledger="data/paper_trades.jsonl",
        closing_review_path="reports/closing_review.md",
        notify=False,
        notify_mode="summary",
        dry_run=False,
        enable_debate=False,
        enable_auto_evolution=True,
    )

    result = daily_pipeline.run_pipeline(config)

    assert result.overall_success is False
    assert "✗ 策略自进化" in result.summary


def test_sync_paper_trades_writes_report(monkeypatch, tmp_path: Path) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    ledger_path = tmp_path / "predictions.jsonl"
    ledger_path.write_text('{"symbol":"600519","status":"pending"}\n', encoding="utf-8")

    monkeypatch.setattr(
        daily_pipeline,
        "_build_data_source",
        lambda _config: object(),
    )
    monkeypatch.setattr(
        "aqsp.data.fetch_with_source",
        lambda _source, _symbols, days=60: {
            "600519": pd.DataFrame([{"date": "2026-06-02"}])
        },
    )
    monkeypatch.setattr(
        "aqsp.ledger.base.read_ledger",
        lambda _path: [{"symbol": "600519", "status": "pending"}],
    )
    monkeypatch.setattr(
        "aqsp.paper.read_paper_trades",
        lambda _path: [{"symbol": "600519", "status": "open"}],
    )

    class FakeSummary:
        opened = 1
        closed = 0
        open_positions = 1
        pending_entry = 0
        not_executable = 0

    monkeypatch.setattr(
        "aqsp.paper.sync_paper_trades",
        lambda **_kwargs: FakeSummary(),
    )
    monkeypatch.setattr(
        "aqsp.paper.render_paper_report",
        lambda summary, trades: f"opened={summary.opened}, rows={len(trades)}",
    )

    config = daily_pipeline.PipelineConfig(
        project_root=tmp_path,
        source="eastmoney",
        mode="close",
        limit=10,
        max_universe=50,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        allow_online_fallback=True,
        ledger_path=ledger_path.name,
        report_path="reports/latest.md",
        csv_path="reports/latest.csv",
        briefing_path="reports/briefing.md",
        paper_report_path="reports/paper.md",
        dashboard_html="dist/dashboard/index.html",
        dashboard_db="dist/dashboard/aqsp.db",
        paper_ledger="data/paper_trades.jsonl",
        closing_review_path="reports/closing_review.md",
        notify=False,
        notify_mode="summary",
        dry_run=False,
        enable_debate=False,
        enable_auto_evolution=False,
    )

    result = daily_pipeline._step_sync_paper_trades(config, logging.getLogger("test"))

    assert result["opened"] == 1
    assert result["open_positions"] == 1
    assert (tmp_path / "reports" / "paper.md").read_text(
        encoding="utf-8"
    ) == "opened=1, rows=1"
