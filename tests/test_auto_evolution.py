from __future__ import annotations

import json
from datetime import timedelta

import pandas as pd
import pytest

from aqsp.core.time import now_shanghai
from aqsp.strategies.auto_evolution import AutoEvolution, EvolutionConfig
from aqsp.strategies.thresholds import ScoringThresholds
from aqsp.walkforward_gate import build_walkforward_gate_payload


def _evolution(tmp_path) -> AutoEvolution:
    thresholds_path = tmp_path / "thresholds.yaml"
    thresholds_path.write_text("version: test\nstrategies: {}\n", encoding="utf-8")
    evolution = AutoEvolution(
        thresholds_path=str(thresholds_path),
        data_dir=str(tmp_path / "evolution"),
        walkforward_gate_path=str(tmp_path / "walkforward_gate.json"),
    )
    evolution.config = EvolutionConfig(
        min_samples=3,
        check_interval_days=7,
        performance_threshold=0.05,
        param_spaces={"composite": {"candidate": [1.0, 2.0]}},
    )
    return evolution


def _passing_gate_payload() -> dict[str, object]:
    return build_walkforward_gate_payload(
        dsr=1.9,
        pbo=0.24,
        run_date="2026-07-10",
        start="2023-01-01",
        end="2024-12-31",
        n_periods=12,
        thresholds_version="test",
        metadata={
            "backtest_assumptions": {
                "uses_raw_prices": True,
                "uses_point_in_time_data": True,
                "train_test_separated": True,
                "has_purge_window": True,
                "includes_transaction_costs": True,
                "includes_slippage": True,
                "excludes_not_executable": True,
                "cost_model": "fee+slippage",
            }
        },
    )


def test_auto_evolution_proposal_records_failure_evidence_samples_and_validation(
    tmp_path,
) -> None:
    evolution = _evolution(tmp_path)
    evolution._evaluate_params = lambda params, _data, _name: {
        "research_score": params["candidate"],
        "score_hit_rate": 1.0,
    }
    data = {
        "600000": pd.DataFrame(
            {
                "date": ["2026-06-01", "2026-06-02", "2026-06-03"],
                "close": [10.0, 10.1, 10.2],
            }
        ),
        "000001": pd.DataFrame(
            {
                "date": ["2026-06-02", "2026-06-03"],
                "close": [20.0, 20.1],
                "status": ["not_executable", "pending"],
            }
        ),
    }
    gate_payload = build_walkforward_gate_payload(
        dsr=-1.2794,
        pbo=1.0,
        run_date="2026-06-30",
        start="2023-06-30",
        end="2026-06-29",
        n_periods=19,
    )

    result = evolution.evolve_parameters(
        "composite",
        data,
        walkforward_payload=gate_payload,
    )

    assert result is not None
    assert result.sample_count == 3
    assert result.cooldown_days == 7
    assert result.gate_evidence["status"] == "fail"
    assert result.runtime_writeback is False

    evolution._apply_evolution(result)
    proposal = json.loads(
        (tmp_path / "evolution" / "threshold_proposals.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )

    assert proposal["status"] == "blocked_proposal"
    assert proposal["proposal_only"] is True
    assert proposal["applied"] is False
    assert proposal["runtime_writeback"] is False
    assert proposal["sample_count"] == 3
    assert proposal["min_samples"] == 3
    assert proposal["sample_unit"] == "independent_signal_days"
    assert proposal["cooldown_days"] == 7
    assert proposal["eligible_after"]
    assert proposal["gate_status"] == "fail"
    assert proposal["gate_evidence"]["dsr"] == -1.2794
    assert proposal["gate_evidence"]["pbo"] == 1.0
    assert proposal["validation_requirements"]
    assert any(item.startswith("DSR=") for item in proposal["gate_reasons"])
    assert proposal["research_score_improvement"] == pytest.approx(0.15)
    assert proposal["performance_metric"] == "research_score"
    assert proposal["forward_performance_validated"] is False
    assert "performance_improvement" not in proposal


def test_auto_evolution_pass_gate_remains_manual_research_proposal(tmp_path) -> None:
    evolution = _evolution(tmp_path)
    evolution._evaluate_params = lambda params, _data, _name: {
        "research_score": params["candidate"],
    }
    data = {
        "600000": pd.DataFrame(
            {
                "date": ["2026-06-01", "2026-06-02", "2026-06-03"],
                "close": [10.0, 10.1, 10.2],
            }
        )
    }

    result = evolution.evolve_parameters(
        "composite",
        data,
        walkforward_payload=_passing_gate_payload(),
    )

    assert result is not None
    assert result.status == "proposal_only"
    assert result.forward_performance_validated is False
    proposal = json.loads(
        (tmp_path / "evolution" / "threshold_proposals.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert proposal["status"] == "proposal_only"
    assert proposal["gate_status"] == "pass"
    assert proposal["research_score_improvement"] == pytest.approx(0.15)
    assert proposal["performance_metric"] == "research_score"
    assert proposal["forward_performance_validated"] is False
    assert proposal["applied"] is False
    assert proposal["runtime_writeback"] is False
    assert "performance_improvement" not in proposal


def test_auto_evolution_blocks_insufficient_samples_and_cooldown(tmp_path) -> None:
    evolution = _evolution(tmp_path)
    evolution._evaluate_params = lambda params, _data, _name: {
        "research_score": params["candidate"],
    }
    data = pd.DataFrame(
        {
            "date": ["2026-06-01", "2026-06-02"],
            "close": [10.0, 10.1],
        }
    )

    assert evolution.evolve_parameters("composite", {"600000": data}) is None
    assert not (tmp_path / "evolution" / "threshold_proposals.jsonl").exists()

    evolution._last_evolution_time = now_shanghai()
    enough_data = {
        "600000": pd.DataFrame(
            {
                "date": ["2026-06-01", "2026-06-02", "2026-06-03"],
                "close": [10.0, 10.1, 10.2],
            }
        ),
    }
    assert evolution.evolve_parameters("composite", enough_data) is None


def test_auto_evolution_missing_gate_is_blocked_proposal(tmp_path) -> None:
    evolution = _evolution(tmp_path)
    evolution._evaluate_params = lambda params, _data, _name: {
        "research_score": params["candidate"],
    }
    data = {
        "600000": pd.DataFrame(
            {
                "date": ["2026-06-01", "2026-06-02", "2026-06-03"],
                "close": [10.0, 10.1, 10.2],
            }
        )
    }

    result = evolution.evolve_parameters("composite", data)

    assert result is not None
    assert result.status == "blocked_proposal"
    proposal = json.loads(
        (tmp_path / "evolution" / "threshold_proposals.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert proposal["status"] == "blocked_proposal"
    assert proposal["gate_status"] == "missing"
    assert proposal["gate_reasons"] == ["walkforward gate evidence missing"]


def test_auto_evolution_uses_current_thresholds_as_proposal_baseline(tmp_path) -> None:
    evolution = _evolution(tmp_path)
    evolution.config = EvolutionConfig(
        min_samples=3,
        max_evolution_per_cycle=1,
        param_spaces={
            "scoring": {
                "near_high_threshold": [0.98, 1.0],
                "near_high_volume": [1.2, 2.0],
            }
        },
    )

    assert evolution._get_base_params("scoring")["near_high_threshold"] == pytest.approx(
        ScoringThresholds().near_high_threshold
    )
    candidates = evolution._generate_candidates(
        "scoring", evolution._get_base_params("scoring")
    )
    assert len(candidates) == 1
    assert set(candidates[0]) == {"near_high_threshold", "near_high_volume"}


def test_auto_evolution_should_evolve_does_not_treat_metric_keys_as_samples(
    tmp_path,
) -> None:
    evolution = _evolution(tmp_path)

    assert (
        evolution.should_evolve({"sharpe_ratio": 0.1, "hit_rate": 0.4, "drawdown": 0.2})
        is False
    )
    assert (
        evolution.should_evolve(
            {"sharpe_ratio": 0.1, "sample_count": 3},
            sample_count=3,
        )
        is True
    )


def test_auto_evolution_should_evolve_respects_cooldown(tmp_path) -> None:
    evolution = _evolution(tmp_path)
    evolution._last_evolution_time = now_shanghai()

    assert (
        evolution.should_evolve(
            {"sharpe_ratio": -0.2, "sample_count": 3},
            sample_count=3,
        )
        is False
    )


def test_auto_evolution_apply_never_refreshes_runtime_thresholds(tmp_path) -> None:
    evolution = _evolution(tmp_path)
    before = evolution.thresholds
    thresholds_before = evolution.thresholds_path.read_text(encoding="utf-8")
    result = evolution._record_evolution(
        "composite",
        {"candidate": 1.0},
        {"candidate": 1.5},
        0.5,
        "test",
        sample_count=3,
    )

    evolution._apply_evolution(result)

    assert evolution.thresholds is before
    assert evolution.thresholds_path.read_text(encoding="utf-8") == thresholds_before
    assert (now_shanghai() - result.timestamp) < timedelta(minutes=1)


def test_auto_evolution_apply_rejects_insufficient_signal_days(tmp_path) -> None:
    evolution = _evolution(tmp_path)
    result = evolution._record_evolution(
        "composite",
        {"candidate": 1.0},
        {"candidate": 1.5},
        0.5,
        "test",
        sample_count=2,
    )

    evolution._apply_evolution(result)

    assert not (tmp_path / "evolution" / "threshold_proposals.jsonl").exists()


def test_auto_evolution_counts_signal_day_groups_once(tmp_path) -> None:
    evolution = _evolution(tmp_path)
    data = {
        "600000": pd.DataFrame(
            {
                "date": ["2026-06-01", "2026-06-02"],
                "signal_day_group": ["2026-06-01_composite", "2026-06-02_composite"],
                "status": ["pending", "pending"],
            }
        ),
        "000001": pd.DataFrame(
            {
                "date": ["2026-06-01", "2026-06-03"],
                "signal_day_group": ["2026-06-01_composite", "2026-06-03_composite"],
                "status": ["pending", "pending"],
            }
        ),
    }

    assert evolution._resolve_sample_count(data, None) == 3


def test_auto_evolution_proposal_is_written_by_main_evolution_path(tmp_path) -> None:
    evolution = _evolution(tmp_path)
    evolution._evaluate_params = lambda params, _data, _name: {
        "research_score": params["candidate"],
    }
    data = {
        "600000": pd.DataFrame(
            {
                "date": ["2026-06-01", "2026-06-02", "2026-06-03"],
                "close": [10.0, 10.1, 10.2],
            }
        )
    }

    result = evolution.evolve_parameters("composite", data)

    assert result is not None
    proposal_path = tmp_path / "evolution" / "threshold_proposals.jsonl"
    assert proposal_path.exists()
    assert len(proposal_path.read_text(encoding="utf-8").splitlines()) == 1
