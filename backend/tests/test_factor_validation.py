from __future__ import annotations

from datetime import date, timedelta
import hashlib
import math

import pytest

import factor_validation


def _trial(trial_id: str, expression: str, ic_values: list[float]) -> dict:
    return {
        "trial_id": trial_id,
        "generated_at": "2026-11-02T10:00:00+08:00",
        "expression": expression,
        "expression_sha256": hashlib.sha256(expression.encode()).hexdigest(),
        "source": "sqlite_db",
        "price_mode": "raw",
        "symbol_set_sha256": "a" * 64,
        "data_sample_sha256": "b" * 64,
        "signal_start": "2026-09-01",
        "signal_end": "2026-10-30",
        "warmup_start": "2026-05-04",
        "holding_days": 5,
        "quantile": 5,
        "requested_symbols": 30,
        "used_symbols": 30,
        "missing_data_symbols": [],
        "evidence_status": "preview_only",
        "blockers": [],
        "daily_ic": [
            {"date": (date(2026, 9, 1) + timedelta(days=index)).isoformat(), "ic": value}
            for index, value in enumerate(ic_values)
        ],
    }


def _definitions(trials: list[dict]) -> list[dict]:
    return [
        {
            "id": f"factor-{index}",
            "versions": [
                {
                    "version": 1,
                    "name": f"因子 {index}",
                    "trials": [trial],
                }
            ],
        }
        for index, trial in enumerate(trials)
    ]


def test_factor_validation_computes_newey_west_and_bh_when_family_is_comparable():
    count = 80
    stable_ic = [0.025 + 0.008 * math.sin(index * 1.3) for index in range(count)]
    noisy_ic = [0.002 + 0.018 * math.sin(index * 0.71) for index in range(count)]
    trials = [_trial("trial-a", "rank(close)", stable_ic), _trial("trial-b", "rank(volume)", noisy_ic)]

    result = factor_validation.validate_factor_trial_family(
        _definitions(trials), target_trial_id="trial-a",
    )

    assert result["status"] == "diagnostic_only"
    assert result["family_size"] == 2
    assert result["common_dates"] == count
    assert result["newey_west_lags"] == 4
    assert result["promotion_authorized"] is False
    assert result["oos_verified"] is False
    assert len(result["items"]) == 2
    assert all(item["q_value"] >= item["p_value"] for item in result["items"])
    assert all(item["n_dates"] == count for item in result["items"])


def test_factor_validation_blocks_mixed_data_samples_when_family_is_incomparable():
    first = _trial("trial-a", "rank(close)", [0.02] * 80)
    second = _trial("trial-b", "rank(volume)", [0.01] * 80)
    second["data_sample_sha256"] = "c" * 64

    result = factor_validation.validate_factor_trial_family(
        _definitions([first, second]), target_trial_id="trial-a",
    )

    assert result["status"] == "blocked"
    assert result["items"] == []
    assert any("不同的 raw OHLCV 样本" in blocker for blocker in result["blockers"])


def test_factor_validation_blocks_legacy_trials_without_sample_fingerprint():
    legacy = _trial("trial-legacy", "rank(close)", [0.02] * 80)
    legacy.pop("data_sample_sha256")

    result = factor_validation.validate_factor_trial_family(
        _definitions([legacy]), target_trial_id="trial-legacy",
    )

    assert result["status"] == "blocked"
    assert any("缺少实际 raw OHLCV 样本指纹" in blocker for blocker in result["blockers"])


def test_benjamini_hochberg_is_monotonic_in_rank_order():
    adjusted = factor_validation._benjamini_hochberg([0.01, 0.04, 0.03])
    assert adjusted == [0.03, 0.04, 0.04]


def _comparable_trials() -> list[dict]:
    count = 80
    stable_ic = [0.025 + 0.008 * math.sin(index * 1.3) for index in range(count)]
    noisy_ic = [0.002 + 0.018 * math.sin(index * 0.71) for index in range(count)]
    return [_trial("trial-a", "rank(close)", stable_ic), _trial("trial-b", "rank(volume)", noisy_ic)]


def test_build_validation_observation_is_evidence_for_human_review_when_diagnostic():
    report = factor_validation.validate_factor_trial_family(
        _definitions(_comparable_trials()), target_trial_id="trial-a",
    )
    observation = factor_validation.build_validation_observation(report)

    assert report["status"] == "diagnostic_only"
    assert observation["family_id"] == report["family_id"]
    assert observation["status"] == "diagnostic_only"
    assert observation["review_state"] == "pending_review"
    assert observation["common_dates"] == 80
    assert observation["human_review_required"] is True
    assert observation["promotion_authorized"] is False
    assert observation["oos_verified"] is False


def test_build_validation_observation_marks_blocked_report_when_family_incomparable():
    first = _trial("trial-a", "rank(close)", [0.02] * 80)
    second = _trial("trial-b", "rank(volume)", [0.01] * 80)
    second["data_sample_sha256"] = "c" * 64
    report = factor_validation.validate_factor_trial_family(
        _definitions([first, second]), target_trial_id="trial-a",
    )
    observation = factor_validation.build_validation_observation(report)

    assert observation["status"] == "blocked"
    assert observation["review_state"] == "blocked"
    assert observation["blockers"]
    assert observation["promotion_authorized"] is False


def test_build_validation_observation_rejects_malformed_reports_when_fields_missing():
    malformed = [
        {},
        {"family_id": "x"},
        {"family_id": "a" * 64, "status": "active"},
        {"family_id": "a" * 64, "status": "diagnostic_only", "generated_at": ""},
        {"family_id": "a" * 64, "status": "diagnostic_only", "generated_at": "x", "blockers": "not-a-list"},
    ]
    for report in malformed:
        with pytest.raises(ValueError):
            factor_validation.build_validation_observation(report)
