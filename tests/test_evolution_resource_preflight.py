"""Keep cooling research out of market-data I/O without relaxing sample gates."""

from argparse import Namespace
from dataclasses import replace
from datetime import timedelta
import json

import pandas as pd
import pytest

import aqsp.cli as cli
from aqsp.core.time import now_shanghai
from aqsp.strategies.auto_evolution import AutoEvolution, EvolutionConfig


@pytest.fixture
def evolution(tmp_path, monkeypatch):
    thresholds = tmp_path / "thresholds.yaml"
    thresholds.write_text("version: test\nstrategies: {}\n")
    instance = AutoEvolution(
        config_path=str(tmp_path / "missing.yaml"),
        thresholds_path=str(thresholds), data_dir=str(tmp_path / "evolution"),
    )
    instance.config = EvolutionConfig(min_samples=3, check_interval_days=7)
    monkeypatch.setattr("aqsp.strategies.auto_evolution.AutoEvolution", lambda **kwargs: instance)
    return instance


@pytest.mark.parametrize("disabled", [False, True])
def test_evolution_cli_skips_all_data_io_when_cooling_or_disabled(evolution, tmp_path, monkeypatch, disabled):
    evolution._last_evolution_time = now_shanghai()
    if disabled:
        evolution.config = replace(evolution.config, enabled=False)
    monkeypatch.setattr(cli, "_resolve_run_symbols", lambda *a, **k: pytest.fail("must not resolve universe"))
    monkeypatch.setattr(cli, "_fetch_frames_for_cli", lambda *a, **k: pytest.fail("must not fetch daily data"))
    output = tmp_path / "out.json"
    output.write_text('{"strategy_name":"stale-proposal"}')
    args = Namespace(config="unused", source="eastmoney", max_universe=0, apply=False, output=str(output))
    assert cli.run_evolve(args) == 0
    result = json.loads(output.read_text())
    assert result["strategy_name"] == "" and result["status"] == "skipped"
    assert result["reason"] == ("disabled" if disabled else "cooldown_active")
    assert result["applied"] is False
    assert result["cooldown_days_remaining"] == (0 if disabled else 7)


def test_evolution_library_skips_sample_scan_when_cooling(evolution, monkeypatch):
    evolution._last_evolution_time = now_shanghai()
    monkeypatch.setattr(evolution, "_resolve_sample_count", lambda *a: pytest.fail("must not scan samples"))
    assert evolution.evolve_parameters("composite", {}) is None


@pytest.mark.parametrize("elapsed,allowed", [(6, False), (7, True)])
def test_evolution_preflight_retains_interval_boundary_when_history_present(evolution, monkeypatch, elapsed, allowed):
    current = now_shanghai()
    monkeypatch.setattr("aqsp.strategies.auto_evolution.now_shanghai", lambda: current)
    evolution._last_evolution_time = current - timedelta(days=elapsed)
    assert evolution.preflight().allowed is allowed


def test_evolution_cli_keeps_sample_guard_when_cooldown_expired(evolution, tmp_path, monkeypatch):
    evolution._last_evolution_time = now_shanghai() - timedelta(days=7)
    calls = []
    monkeypatch.setattr(cli, "_resolve_run_symbols", lambda *a, **k: calls.append("universe") or ["600000"])
    monkeypatch.setattr(cli, "_fetch_frames_for_cli", lambda *a, **k: calls.append("daily") or {
        "600000": pd.DataFrame({"date": ["2026-10-22"], "close": [10.0]}),
    })
    monkeypatch.setattr(evolution, "_evaluate_params", lambda *a: pytest.fail("insufficient samples"))
    output = tmp_path / "out.json"
    assert cli.run_evolve(Namespace(config="unused", source="eastmoney", max_universe=20, apply=False, output=str(output))) == 0
    assert calls == ["universe", "daily"]
    assert json.loads(output.read_text())["status"] == "no_change"
    assert not (evolution.data_dir / "threshold_proposals.jsonl").exists()


def test_evolution_cli_reports_failure_when_eligible_data_unavailable(evolution, monkeypatch):
    monkeypatch.setattr(cli, "_resolve_run_symbols", lambda *a, **k: ["600000"])
    monkeypatch.setattr(cli, "_fetch_frames_for_cli", lambda *a, **k: {})
    assert cli.run_evolve(Namespace(config="unused", source="eastmoney", max_universe=20, apply=False, output="")) == 1
