"""自进化子进程隔离步（_step_auto_evolution）的单元测试。

背景：prod 自 2026-09-14 起 daily 任务 10 天每天死在 exit=124 ——
_step_auto_evolution 曾在主进程内 import aqsp.cli 调 main(["evolve",...])，
ThreadPool socket 阻塞时工作线程 join 无界等待，主线程挂死不抛异常，
外层 timeout 强杀整棵进程树。修复后该步改为独立子进程 + 超时预算 + 优雅跳过
（失败/超时都标 skipped=True、不 raise），绝不再拖死主链路。
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_daily_pipeline_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "daily_pipeline.py"
    spec = importlib.util.spec_from_file_location(
        "test_daily_evolution_pipeline_module", module_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def daily_pipeline() -> object:
    return _load_daily_pipeline_module()


def _config(daily_pipeline, tmp_path: Path, *, token: str = "dummy") -> object:
    import json

    module = daily_pipeline
    config = module.PipelineConfig(
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
    # 预写一份进化结果，验证成功路径会解析它。
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "evolution_result.json").write_text(
        json.dumps(
            {
                "strategy_name": "composite",
                "confidence": 0.82,
                "performance_improvement": 0.05,
                "reason": "research_score_improvement",
            }
        ),
        encoding="utf-8",
    )
    return config


def test_success_path_parses_evolution_result(
    daily_pipeline, tmp_path, monkeypatch
) -> None:
    module = daily_pipeline
    config = _config(daily_pipeline, tmp_path)
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy")
    calls: list[dict] = []

    def fake_run(argv, **kwargs):
        calls.append({"argv": argv, "cwd": kwargs.get("cwd"), "timeout": kwargs.get("timeout")})
        return SimpleNamespace(returncode=0, stdout="进化完成", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = module._step_auto_evolution(config, logging.getLogger("t"))

    assert result.get("skipped") is not True
    assert result.get("evolved") is True
    assert result.get("strategy_name") == "composite"
    assert abs(result.get("confidence", 0.0) - 0.82) < 1e-9
    assert len(calls) == 1
    argv = calls[0]["argv"]
    assert "-m" in argv and "aqsp" in argv and "evolve" in argv
    assert calls[0]["cwd"] == str(config.project_root)
    assert isinstance(calls[0]["timeout"], int) and calls[0]["timeout"] > 0


def test_timeout_is_graceful_skip(daily_pipeline, tmp_path, monkeypatch) -> None:
    module = daily_pipeline
    config = _config(daily_pipeline, tmp_path)
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy")

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = module._step_auto_evolution(config, logging.getLogger("t"))

    assert result.get("skipped") is True
    assert result.get("reason") == "timeout_budget_exceeded"


def test_nonzero_exit_is_graceful_skip(daily_pipeline, tmp_path, monkeypatch) -> None:
    module = daily_pipeline
    config = _config(daily_pipeline, tmp_path)
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=3, stdout="", stderr="boom")
    )

    result = module._step_auto_evolution(config, logging.getLogger("t"))

    assert result.get("skipped") is True
    assert result.get("reason") == "evolve_failed"
    assert result.get("exit_code") == 3


def test_budget_env_override_and_clamp(daily_pipeline, monkeypatch) -> None:
    module = daily_pipeline

    monkeypatch.delenv("AQSP_EVOLUTION_TIMEOUT_SECONDS", raising=False)
    assert module._resolve_evolution_timeout_budget() == 600

    monkeypatch.setenv("AQSP_EVOLUTION_TIMEOUT_SECONDS", "900")
    assert module._resolve_evolution_timeout_budget() == 900

    monkeypatch.setenv("AQSP_EVOLUTION_TIMEOUT_SECONDS", "not-an-int")
    assert module._resolve_evolution_timeout_budget() == 600

    monkeypatch.setenv("AQSP_EVOLUTION_TIMEOUT_SECONDS", "10")
    assert module._resolve_evolution_timeout_budget() == 60

    monkeypatch.setenv("AQSP_EVOLUTION_TIMEOUT_SECONDS", "99999")
    assert module._resolve_evolution_timeout_budget() == 3600


def test_missing_token_and_symbols_skips(daily_pipeline, tmp_path, monkeypatch) -> None:
    module = daily_pipeline
    config = _config(daily_pipeline, tmp_path)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("AQSP_SYMBOLS", raising=False)

    result = module._step_auto_evolution(config, logging.getLogger("t"))

    assert result.get("skipped") is True
    assert result.get("reason") == "missing_tushare_or_symbols"
