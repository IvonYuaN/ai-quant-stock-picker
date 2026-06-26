from __future__ import annotations

import argparse
import importlib.util
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

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


def _pipeline_config(
    daily_pipeline,
    tmp_path: Path,
    *,
    notify: bool = False,
    notify_mode: str = "summary",
):
    return daily_pipeline.PipelineConfig(
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
        notify=notify,
        notify_mode=notify_mode,
        dry_run=False,
        enable_debate=False,
        enable_auto_evolution=False,
    )


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


def test_morning_breakout_uses_runtime_symbols_without_sh300_override(
    monkeypatch,
) -> None:
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

    config = _pipeline_config(daily_pipeline, Path.cwd())

    daily_pipeline._step_morning_breakout(config, logging.getLogger("test"))

    assert captured == [
        "morning-breakout",
        "--source",
        "eastmoney",
        "--symbols",
        "600519,300750",
        "--max-universe",
        "50",
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

    assert result["weights_proposed"] is True
    assert result["weights_applied"] is False
    assert result["proposed_weights"] == {"volume_breakout": 1.1}
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
    monkeypatch.setenv("AQSP_SYMBOLS", "600000,600519")

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


def test_auto_evolution_step_raises_when_cli_fails(monkeypatch, tmp_path: Path) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    monkeypatch.setenv("AQSP_SYMBOLS", "600000,600519")

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


def test_auto_evolution_step_skips_when_prerequisites_missing(
    monkeypatch, tmp_path: Path
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    monkeypatch.delenv("AQSP_SYMBOLS", raising=False)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

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

    assert result == {"skipped": True, "reason": "missing_tushare_or_symbols"}


def test_auto_evolution_step_skips_when_pool_constituents_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    monkeypatch.setenv("TUSHARE_TOKEN", "configured-but-unavailable")

    def fake_main(_argv: list[str]) -> int:
        print(
            "配置错误: Pool sh300 requires TUSHARE_TOKEN or explicit --symbols for point-in-time constituents"
        )
        return 1

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

    assert result == {"skipped": True, "reason": "missing_pool_constituents"}


def test_closing_review_step_writes_output_and_skips_fanout_notify_in_summary_mode(
    monkeypatch, tmp_path: Path
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    captured: list[str] = []
    monkeypatch.setattr(
        daily_pipeline,
        "today_shanghai",
        lambda: __import__("datetime").date(2026, 6, 8),
    )

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
        "--date",
        "2026-06-08",
        "--output",
        "reports/closing_review.md",
    ]
    assert result["report_path"] == "reports/closing_review.md"
    assert result["dated_report_path"] == "reports/closing_review-2026-06-08.md"


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
                "candidate_status": "延续上升",
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
                "candidate_status": "观察阻塞",
                "candidate_blocker": "板块集中度过高，压低新能源暴露",
                "candidate_next_step": "等待板块暴露回落后，再重新评估纸面复核优先级",
                "candidate_review_window": "板块分化时",
                "candidate_review_priority": "medium",
            },
        ]
    ).to_csv(reports_dir / "latest.csv", index=False)

    sent: dict[str, str] = {}

    monkeypatch.setattr(
        "aqsp.notifier.send_notification",
        lambda title, content: (
            sent.update({"title": title, "content": content})
            or [SimpleNamespace(channel="serverchan", ok=True, detail="HTTP 200")]
        ),
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
            daily_pipeline.StepResult("策略运行", True, 2.0, details={"gate_ok": True}),
            daily_pipeline.StepResult(
                "预测验证",
                True,
                0.5,
                details={
                    "checked": 1,
                    "wins": 1,
                    "avg_return_pct": 2.3,
                    "avg_excess_pct": 1.1,
                    "skipped_not_executable": 2,
                    "not_executable_reasons": {
                        "limit_up_at_open": 1,
                        "suspended_or_no_trade": 1,
                    },
                    "strategy_not_executable_rates": {"limit_up_ladder": 0.5},
                },
            ),
        ],
        overall_success=True,
        summary="ok",
    )

    daily_pipeline._send_pipeline_digest(config, result, logging.getLogger("test"))

    assert sent["title"] == "收盘总览-2026-06-02"
    assert result.notify_status["status"] == "sent"
    assert result.notify_status["reason"] == "ok"
    assert "## 结论" in sent["content"]
    assert "## 候选" in sent["content"]
    assert "## 风险" in sent["content"]
    assert "## 明日" in sent["content"]
    assert "- 今日结论: " in sent["content"]
    assert "## 数据" in sent["content"]
    assert "## 候选" in sent["content"]
    assert "- PM 主裁决: 上调 1 / 降级 1 / 维持 0" in sent["content"]
    assert "- 现在卡在哪: " in sent["content"]
    assert "- 首要复核: 300750 宁德时代 | 中优先级 / 板块分化时" in sent["content"]
    assert (
        "600519 贵州茅台 | 纸面复核 | 延续上升 | PM 上调优先级 | 评分 71.0"
        in sent["content"]
    )
    assert "600519 贵州茅台 | 重点关注" not in sent["content"]
    assert (
        "300750 宁德时代 | 继续观察名单 | 观察阻塞 | PM 降级观察 | 评分 64.0"
        in sent["content"]
    )
    assert "现在卡在哪: 板块集中度过高，压低新能源暴露" in sent["content"]
    assert "下一步: 等待板块暴露回落后，再重新评估纸面复核优先级" in sent["content"]
    assert "再看时间: 中优先级 / 板块分化时" in sent["content"]
    assert "观察名单接下来:" in sent["content"]
    assert (
        "- 策略自检: 验证 1 条 / 胜率 100.0% / 平均收益 2.30% / 不可成交跳过 2 条"
        in sent["content"]
    )
    assert (
        "- 不可成交原因: limit_up_at_open×1, suspended_or_no_trade×1" in sent["content"]
    )
    assert "- 不可成交策略: limit_up_ladder 50%" in sent["content"]
    assert (
        "观察名单接下来: 先盯 300750 宁德时代，等待板块暴露回落后，再重新评估纸面复核优先级（中优先级 / 板块分化时）。"
        in sent["content"]
    )
    forbidden = (
        "阅读方式",
        "不是交易指令",
        "不要做",
        "怎么验证",
        "## 🧭",
        "## 📋",
        "## 🔒",
        "## ✅",
    )
    assert not any(token in sent["content"] for token in forbidden)
    assert "运行侧写" not in sent["content"]
    assert "# 收盘总览" not in sent["content"]


def test_send_pipeline_digest_logs_channel_results(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    daily_pipeline = _load_daily_pipeline_module()

    ledger_path = tmp_path / "data" / "predictions.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("{}", encoding="utf-8")
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
                "candidate_status": "延续上升",
            }
        ]
    ).to_csv(reports_dir / "latest.csv", index=False)

    monkeypatch.setattr(
        "aqsp.notifier.send_notification",
        lambda title, content: [
            SimpleNamespace(channel="serverchan", ok=True, detail="HTTP 200"),
            SimpleNamespace(channel="wechat", ok=False, detail="HTTP 500"),
        ],
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
            daily_pipeline.StepResult("策略运行", True, 2.0, details={"gate_ok": True})
        ],
        overall_success=True,
        summary="ok",
    )

    with caplog.at_level(logging.INFO, logger="test"):
        daily_pipeline._send_pipeline_digest(config, result, logging.getLogger("test"))

    assert "channels=serverchan=ok(HTTP 200), wechat=failed(HTTP 500)" in caplog.text


def test_send_pipeline_digest_dedupes_same_date_summary(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
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
                "candidate_status": "延续上升",
            }
        ]
    ).to_csv(reports_dir / "latest.csv", index=False)

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "aqsp.notifier.send_notification",
        lambda title, content: (
            calls.append((title, content))
            or [SimpleNamespace(channel="serverchan", ok=True, detail="HTTP 200")]
        ),
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
            daily_pipeline.StepResult("策略运行", True, 2.0, details={"gate_ok": True})
        ],
        overall_success=True,
        summary="ok",
    )

    with caplog.at_level(logging.INFO, logger="test"):
        daily_pipeline._send_pipeline_digest(config, result, logging.getLogger("test"))
        daily_pipeline._send_pipeline_digest(config, result, logging.getLogger("test"))

    assert len(calls) == 1
    assert "收盘汇总通知已发送过" in caplog.text


def test_send_pipeline_digest_allows_same_day_gate_block_then_ok(
    monkeypatch, tmp_path: Path
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
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
                "candidate_status": "延续上升",
            }
        ]
    ).to_csv(reports_dir / "latest.csv", index=False)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "aqsp.notifier.send_notification",
        lambda title, content: (
            calls.append((title, content))
            or [SimpleNamespace(channel="serverchan", ok=True, detail="HTTP 200")]
        ),
    )
    config = _pipeline_config(
        daily_pipeline, tmp_path, notify=True, notify_mode="summary"
    )
    blocked = daily_pipeline.PipelineResult(
        started_at="2026-06-02T18:00:00+08:00",
        finished_at="2026-06-02T18:00:30+08:00",
        duration_seconds=30.0,
        steps=[daily_pipeline.StepResult("策略运行", True, 2.0)],
        overall_success=True,
        summary="blocked",
    )
    ok = daily_pipeline.PipelineResult(
        started_at="2026-06-02T18:10:00+08:00",
        finished_at="2026-06-02T18:10:30+08:00",
        duration_seconds=30.0,
        steps=[
            daily_pipeline.StepResult("策略运行", True, 2.0, details={"gate_ok": True})
        ],
        overall_success=True,
        summary="ok",
    )

    daily_pipeline._send_pipeline_digest(config, blocked, logging.getLogger("test"))
    daily_pipeline._send_pipeline_digest(config, ok, logging.getLogger("test"))

    assert len(calls) == 2
    assert "正常候选未放行" in calls[0][1]
    assert "正常候选未放行" not in calls[1][1]


def test_send_pipeline_digest_sends_block_summary_when_strategy_gate_not_confirmed(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "aqsp.notifier.send_notification",
        lambda title, content: (
            calls.append((title, content))
            or [SimpleNamespace(channel="serverchan", ok=True, detail="HTTP 200")]
        ),
    )
    config = _pipeline_config(
        daily_pipeline, tmp_path, notify=True, notify_mode="summary"
    )
    result = daily_pipeline.PipelineResult(
        started_at="2026-06-02T18:00:00+08:00",
        finished_at="2026-06-02T18:00:30+08:00",
        duration_seconds=30.0,
        steps=[daily_pipeline.StepResult("策略运行", True, 2.0)],
        overall_success=True,
        summary="ok",
    )

    with caplog.at_level(logging.INFO, logger="test"):
        daily_pipeline._send_pipeline_digest(config, result, logging.getLogger("test"))

    assert len(calls) == 1
    assert calls[0][0] == "收盘总览-2026-06-02"
    assert "正常候选未放行" in calls[0][1]
    assert "strategy_gate_not_confirmed" in calls[0][1]
    assert result.notify_status == {
        "mode": "summary",
        "status": "sent",
        "reason": "gate_block_summary_sent",
        "date": "2026-06-02",
        "channels": "serverchan=ok(HTTP 200)",
    }
    assert "收盘汇总通知降级" in caplog.text


def test_send_pipeline_digest_skips_block_summary_when_gate_notice_recorded(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "aqsp.notifier.send_notification",
        lambda title, content: calls.append((title, content)) or [],
    )
    state_path = tmp_path / "data" / "gate_notify_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "sent_by_date": {
                    "2026-06-02": {
                        "fingerprint": "cold_start|dsr",
                        "status": "sent",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config = _pipeline_config(
        daily_pipeline, tmp_path, notify=True, notify_mode="summary"
    )
    result = daily_pipeline.PipelineResult(
        started_at="2026-06-02T18:00:00+08:00",
        finished_at="2026-06-02T18:00:30+08:00",
        duration_seconds=30.0,
        steps=[daily_pipeline.StepResult("策略运行", True, 2.0)],
        overall_success=True,
        summary="ok",
    )

    with caplog.at_level(logging.INFO, logger="test"):
        daily_pipeline._send_pipeline_digest(config, result, logging.getLogger("test"))

    assert calls == []
    assert result.notify_status == {
        "mode": "summary",
        "status": "skipped",
        "reason": "gate_block_already_notified",
        "date": "2026-06-02",
    }
    assert "gate-block 通知已由主链发送" in caplog.text


def test_send_pipeline_digest_skips_non_trading_day(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "aqsp.notifier.send_notification",
        lambda title, content: calls.append((title, content)) or [],
    )
    config = _pipeline_config(
        daily_pipeline, tmp_path, notify=True, notify_mode="summary"
    )
    result = daily_pipeline.PipelineResult(
        started_at="2026-06-19T18:00:00+08:00",
        finished_at="2026-06-19T18:00:30+08:00",
        duration_seconds=30.0,
        steps=[
            daily_pipeline.StepResult("策略运行", True, 2.0, details={"gate_ok": True})
        ],
        overall_success=True,
        summary="ok",
    )

    with caplog.at_level(logging.INFO, logger="test"):
        daily_pipeline._send_pipeline_digest(config, result, logging.getLogger("test"))

    assert calls == []
    assert "非交易日" in caplog.text


def test_write_result_file_appends_daily_run_history(tmp_path: Path) -> None:
    daily_pipeline = _load_daily_pipeline_module()
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

    daily_pipeline._write_result_file(result, tmp_path)

    result_file = tmp_path / "logs" / "pipeline" / "2026-06-02.json"
    payload = json.loads(result_file.read_text(encoding="utf-8"))
    assert payload["notify_status"] == {}

    history_path = tmp_path / "data" / "daily_run_history.jsonl"
    rows = [
        json.loads(line)
        for line in history_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows == [
        {
            "date": "2026-06-02",
            "exit_code": 0,
            "finished_at": "2026-06-02T18:00:30+08:00",
            "started_at": "2026-06-02T18:00:00+08:00",
            "success": True,
            "successful_steps": 2,
            "total_steps": 2,
        }
    ]


def test_main_writes_notify_status_after_summary_send(
    monkeypatch, tmp_path: Path
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    result = daily_pipeline.PipelineResult(
        started_at="2026-06-02T18:00:00+08:00",
        finished_at="2026-06-02T18:00:30+08:00",
        duration_seconds=30.0,
        steps=[
            daily_pipeline.StepResult("策略运行", True, 2.0, details={"gate_ok": True})
        ],
        overall_success=True,
        summary="ok",
    )

    monkeypatch.setattr(daily_pipeline, "run_pipeline", lambda _config: result)

    def fake_send(_config, sent_result, _logger):
        sent_result.notify_status = {
            "mode": "summary",
            "status": "sent",
            "reason": "ok",
            "date": "2026-06-02",
        }

    monkeypatch.setattr(daily_pipeline, "_send_pipeline_digest", fake_send)

    code = daily_pipeline.main(["--project-root", str(tmp_path), "--notify"])

    assert code == 0
    payload = json.loads(
        (tmp_path / "logs" / "pipeline" / "2026-06-02.json").read_text(encoding="utf-8")
    )
    assert payload["notify_status"] == {
        "mode": "summary",
        "status": "sent",
        "reason": "ok",
        "date": "2026-06-02",
    }


def test_run_step_logs_stable_completion_label(caplog) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    logger = logging.getLogger("test.pipeline")

    with caplog.at_level(logging.INFO, logger="test.pipeline"):
        result = daily_pipeline._run_step("策略运行", lambda: {"ok": True}, logger)

    assert result.success is True
    assert "✓ 完成步骤: 策略运行" in caplog.text


def test_run_step_marks_non_zero_exit_code_as_failure(caplog) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    logger = logging.getLogger("test.pipeline.exit")

    with caplog.at_level(logging.ERROR, logger="test.pipeline.exit"):
        result = daily_pipeline._run_step(
            "收盘复盘",
            lambda: {"exit_code": 2, "error": "参数错误"},
            logger,
        )

    assert result.success is False
    assert result.message == "参数错误"
    assert result.details["exit_code"] == 2
    assert "✗ 失败: 收盘复盘 - 参数错误" in caplog.text


def test_latest_portfolio_summary_logs_csv_read_failure(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    csv_path = tmp_path / "reports" / "latest.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text("bad", encoding="utf-8")

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
        enable_auto_evolution=False,
    )

    monkeypatch.setattr(
        daily_pipeline.pd,
        "read_csv",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("csv boom")),
    )

    with caplog.at_level(logging.WARNING, logger="aqsp.pipeline"):
        result = daily_pipeline._latest_portfolio_summary(config)

    assert result is None
    assert "读取候选 CSV 失败，无法生成组合摘要: csv boom" in caplog.text


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

    config = _pipeline_config(daily_pipeline, Path.cwd())

    daily_pipeline._step_closing_premium(config, logging.getLogger("test"))

    assert captured == [
        "closing-premium",
        "--source",
        "eastmoney",
        "--symbols",
        "000001,601318",
        "--max-universe",
        "50",
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
        "预测验证",
        "虚拟盘同步",
        "收盘复盘",
        "自适应学习",
        "策略自进化",
        "报告生成",
        "Dashboard刷新",
        "数据清理",
    ]


def test_run_pipeline_trims_writeback_steps_when_non_trade_day(monkeypatch) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    executed: list[str] = []

    def fake_run_step(name: str, fn, logger, dry_run: bool = False):
        executed.append(name)
        return daily_pipeline.StepResult(name, True, 0.0)

    monkeypatch.setattr(daily_pipeline, "_run_step", fake_run_step)
    monkeypatch.setattr(daily_pipeline, "_is_trade_day", lambda _d: False)

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
    assert executed == ["报告生成", "Dashboard刷新", "数据清理"]
    assert "数据更新" not in executed
    assert "策略运行" not in executed
    assert "虚拟盘同步" not in executed
    assert "收盘复盘" not in executed


def test_generate_report_suppresses_fanout_notify_when_non_trade_day(
    monkeypatch, tmp_path: Path
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    captured: list[str] = []

    def fake_main(argv: list[str]) -> int:
        captured[:] = argv
        output_path = tmp_path / "reports" / "briefing.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("briefing\n", encoding="utf-8")
        return 0

    monkeypatch.setattr("aqsp.cli.main", fake_main)
    config = _pipeline_config(
        daily_pipeline,
        tmp_path,
        notify=True,
        notify_mode="fanout",
    )

    result = daily_pipeline._step_generate_report(
        config, logging.getLogger("test"), allow_notify=False
    )

    assert result["exit_code"] == 0
    assert "--notify" not in captured


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


def test_step_run_strategy_uses_real_benchmark_for_regime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    seen: dict[str, list[str]] = {}

    def fake_main(argv):
        seen["argv"] = list(argv)
        return 0

    monkeypatch.setattr("aqsp.cli.main", fake_main)
    config = daily_pipeline.PipelineConfig(
        project_root=tmp_path,
        source="sqlite_db",
        mode="close",
        limit=10,
        max_universe=0,
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

    result = daily_pipeline._step_run_strategy(config, logging.getLogger("test"))

    assert result["exit_code"] == 0
    argv = seen["argv"]
    assert argv[argv.index("--benchmark-symbol") + 1] == "000300"
    assert "--notify" not in argv


def test_validate_predictions_fetches_benchmark_from_ledger(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    ledger_path = tmp_path / "predictions.jsonl"
    ledger_path.write_text('{"symbol":"600519","status":"pending"}\n', encoding="utf-8")

    rows = [
        {
            "symbol": "600519",
            "benchmark_symbol": "000300",
            "status": "pending",
        }
    ]
    seen: dict[str, object] = {}

    class FakeSource:
        def fetch_index(self, symbols, start, end):
            seen["fetch_index_symbols"] = list(symbols)
            return {
                symbol: pd.DataFrame([{"date": str(start), "close": 1.0}])
                for symbol in symbols
            }

    monkeypatch.setattr(
        daily_pipeline,
        "_build_data_source",
        lambda _config: FakeSource(),
    )
    monkeypatch.setattr("aqsp.ledger.base.read_ledger", lambda _path: rows)
    monkeypatch.setattr(
        "aqsp.data.fetch_with_source",
        lambda _source, symbols, days=60, benchmark_symbol=None: (
            seen.update(
                {
                    "symbols": list(symbols),
                    "benchmark_symbol": benchmark_symbol,
                }
            )
            or {"600519": pd.DataFrame([{"date": "2026-06-02"}])}
        ),
    )
    monkeypatch.setattr(
        "aqsp.ledger.validate_predictions",
        lambda _path, frames: (
            seen.update({"frames": frames})
            or type(
                "Validation",
                (),
                {
                    "checked": 0,
                    "wins": 0,
                    "avg_return_pct": 0.0,
                    "avg_excess_pct": 0.0,
                    "skipped_not_executable": 0,
                    "not_executable_reasons": {},
                },
            )()
        ),
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

    daily_pipeline._step_validate_predictions(config, logging.getLogger("test"))

    assert seen["symbols"] == ["600519"]
    assert seen["benchmark_symbol"] == "000300"
    assert seen["fetch_index_symbols"] == ["000300"]
    assert "000300" in seen["frames"]


def test_validate_predictions_uses_resilient_history_when_primary_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    ledger_path = tmp_path / "predictions.jsonl"
    ledger_path.write_text('{"symbol":"600519","status":"pending"}\n', encoding="utf-8")
    rows = [{"symbol": "600519", "status": "pending"}]
    seen: dict[str, object] = {}

    monkeypatch.setattr("aqsp.ledger.base.read_ledger", lambda _path: rows)
    monkeypatch.setattr(daily_pipeline, "_build_data_source", lambda _config: object())
    monkeypatch.setattr(
        daily_pipeline,
        "_build_resilient_history_source",
        lambda _config: "fallback-source",
    )

    def fake_fetch(source, symbols, days=60, benchmark_symbol=None):
        seen.setdefault("sources", []).append(source)
        if source != "fallback-source":
            raise RuntimeError("remote disconnected")
        return {"600519": pd.DataFrame([{"date": "2026-06-02", "close": 1.0}])}

    monkeypatch.setattr("aqsp.data.fetch_with_source", fake_fetch)
    monkeypatch.setattr(
        "aqsp.ledger.validate_predictions",
        lambda _path, frames: (
            seen.update({"frames": frames})
            or type(
                "Validation",
                (),
                {
                    "checked": 0,
                    "wins": 0,
                    "avg_return_pct": 0.0,
                    "avg_excess_pct": 0.0,
                    "skipped_not_executable": 0,
                    "not_executable_reasons": {},
                },
            )()
        ),
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

    result = daily_pipeline._step_validate_predictions(
        config, logging.getLogger("test")
    )

    assert len(seen["sources"]) == 2
    assert seen["sources"][1] == "fallback-source"
    assert "600519" in seen["frames"]
    assert result["sources_attempted"] == ["eastmoney", "resilient_history"]


def test_validate_predictions_returns_not_executable_summary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    ledger_path = tmp_path / "predictions.jsonl"
    ledger_path.write_text('{"symbol":"600519","status":"pending"}\n', encoding="utf-8")

    monkeypatch.setattr(
        "aqsp.ledger.base.read_ledger",
        lambda _path: [{"symbol": "600519", "status": "pending"}],
    )
    monkeypatch.setattr(daily_pipeline, "_build_data_source", lambda _config: object())
    monkeypatch.setattr(
        "aqsp.data.fetch_with_source",
        lambda *_args, **_kwargs: {
            "600519": pd.DataFrame([{"date": "2026-06-02", "close": 1.0}])
        },
    )
    monkeypatch.setattr(
        "aqsp.ledger.validate_predictions",
        lambda *_args, **_kwargs: type(
            "Validation",
            (),
            {
                "checked": 1,
                "wins": 1,
                "avg_return_pct": 2.3,
                "avg_excess_pct": 1.1,
                "skipped_not_executable": 2,
                "not_executable_reasons": {
                    "limit_up_at_open": 1,
                    "suspended_or_no_trade": 1,
                },
            },
        )(),
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

    result = daily_pipeline._step_validate_predictions(
        config, logging.getLogger("test")
    )

    assert result["skipped_not_executable"] == 2
    assert result["not_executable_reasons"] == {
        "limit_up_at_open": 1,
        "suspended_or_no_trade": 1,
    }


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


def test_resolve_symbols_keeps_full_available_universe_when_max_universe_zero(
    monkeypatch,
) -> None:
    from scripts import daily_pipeline

    class FakeSource:
        def get_available_symbols(self) -> list[str]:
            return ["000001", "000002", "000003"]

    monkeypatch.delenv("AQSP_SYMBOLS", raising=False)
    monkeypatch.setattr(
        daily_pipeline, "_build_data_source", lambda _config: FakeSource()
    )

    config = daily_pipeline.PipelineConfig(
        project_root=Path.cwd(),
        source="fake",
        mode="close",
        limit=10,
        max_universe=0,
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

    assert daily_pipeline._resolve_symbols(config, logging.getLogger("test")) == [
        "000001",
        "000002",
        "000003",
    ]


def test_resolve_symbols_truncates_available_universe_when_max_universe_positive(
    monkeypatch,
) -> None:
    from scripts import daily_pipeline

    class FakeSource:
        def get_available_symbols(self) -> list[str]:
            return ["000001", "000002", "000003"]

    monkeypatch.delenv("AQSP_SYMBOLS", raising=False)
    monkeypatch.setattr(
        daily_pipeline, "_build_data_source", lambda _config: FakeSource()
    )

    config = daily_pipeline.PipelineConfig(
        project_root=Path.cwd(),
        source="fake",
        mode="close",
        limit=10,
        max_universe=2,
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

    assert daily_pipeline._resolve_symbols(config, logging.getLogger("test")) == [
        "000001",
        "000002",
    ]
