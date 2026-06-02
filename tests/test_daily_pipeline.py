from __future__ import annotations

import argparse
import importlib.util
import logging
from pathlib import Path
import sys

import pandas as pd


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
        notify=False,
        dry_run=False,
        enable_debate=False,
    )

    config = daily_pipeline._build_config(args)

    assert config.source == "eastmoney"


def test_morning_breakout_uses_sh300_pool(monkeypatch) -> None:
    daily_pipeline = _load_daily_pipeline_module()
    captured: list[str] = []

    def fake_main(argv: list[str]) -> int:
        captured[:] = argv
        return 0

    monkeypatch.setattr("aqsp.cli.main", fake_main)

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
        dashboard_html="dist/dashboard/index.html",
        dashboard_db="dist/dashboard/aqsp.db",
        paper_ledger="data/paper_trades.jsonl",
        notify=False,
        dry_run=False,
        enable_debate=False,
    )

    daily_pipeline._step_morning_breakout(config, logging.getLogger("test"))

    assert captured == [
        "morning-breakout",
        "--source",
        "eastmoney",
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
        dashboard_html="dist/dashboard/index.html",
        dashboard_db="dist/dashboard/aqsp.db",
        paper_ledger="data/paper_trades.jsonl",
        notify=False,
        dry_run=False,
        enable_debate=False,
    )

    result = daily_pipeline._step_adaptive_learning(config, logging.getLogger("test"))

    assert result["weights_updated"] is True
    assert result["weights"] == {"volume_breakout": 1.1}
    assert result["decay_alerts"] == 0
