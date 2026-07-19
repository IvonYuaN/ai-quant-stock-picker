from datetime import date, datetime, timezone

import pytest

from aqsp.runtime.recommendation_gate import (
    FreshnessEvidence,
    RecommendationGateInputs,
    evaluate,
)


EVALUATED_AT = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)


def _inputs(**overrides: object) -> RecommendationGateInputs:
    values: dict[str, object] = {
        "coldstart_days": 30,
        "paper_tracking_days": 30,
        "walkforward_ok": True,
        "walkforward_updated_at": datetime(2026, 7, 1, 12, tzinfo=timezone.utc),
        "freshness": FreshnessEvidence(ok=True, status="fresh"),
        "circuit_breaker_until": None,
        "evaluated_at": EVALUATED_AT,
    }
    values.update(overrides)
    return RecommendationGateInputs(**values)


def test_recommendation_gate_allows_complete_research_evidence() -> None:
    result = evaluate(_inputs())

    assert result.recommendation_allowed is True
    assert result.status == "allowed"
    assert result.reasons == ()


@pytest.mark.parametrize(
    ("field", "expected_reason"),
    [
        ("coldstart_days", "coldstart_below_minimum:29/30"),
        ("paper_tracking_days", "paper_tracking_below_minimum:29/30"),
    ],
)
def test_recommendation_gate_keeps_candidates_visible_during_validation(
    field: str, expected_reason: str
) -> None:
    result = evaluate(_inputs(**{field: 29}))

    assert result.recommendation_allowed is True
    assert result.status == "allowed"
    assert expected_reason not in result.reasons


def test_recommendation_gate_does_not_hide_candidates_for_walkforward_status() -> None:
    result = evaluate(
        _inputs(
            walkforward_ok=False,
            walkforward_updated_at=datetime(
                2026, 5, 1, 12, tzinfo=timezone.utc
            ),
        )
    )

    assert result.recommendation_allowed is True
    assert result.reasons == ()


def test_recommendation_gate_accepts_walkforward_at_age_boundary() -> None:
    result = evaluate(
        _inputs(
            walkforward_updated_at=datetime(
                2026, 6, 13, 12, tzinfo=timezone.utc
            )
        )
    )

    assert result.recommendation_allowed is True


def test_recommendation_gate_blocks_stale_data_but_not_paper_risk_cooldown() -> None:
    result = evaluate(
        _inputs(
            freshness=FreshnessEvidence(
                ok=False, status="timeout", reason="source_timeout"
            ),
            circuit_breaker_until=date(2026, 7, 19),
        )
    )

    assert result.recommendation_allowed is False
    assert "freshness_not_ready:source_timeout" in result.reasons
    assert "circuit_breaker_active_until:2026-07-19" not in result.reasons


def test_recommendation_gate_releases_on_cooldown_date() -> None:
    result = evaluate(_inputs(circuit_breaker_until=date(2026, 7, 18)))

    assert result.recommendation_allowed is True
    assert result.reasons == ()


def test_recommendation_gate_requires_timezone_aware_evaluation_time() -> None:
    with pytest.raises(ValueError, match="evaluated_at must be timezone-aware"):
        evaluate(_inputs(evaluated_at=datetime(2026, 7, 18, 12)))


def test_recommendation_gate_rejects_invalid_sample_thresholds() -> None:
    with pytest.raises(ValueError, match="coldstart_min_days"):
        evaluate(_inputs(coldstart_min_days=0))
