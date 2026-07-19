"""Pure global gate for short-term research recommendations.

This module deliberately has no runtime side effects.  It only decides whether
research recommendations may be shown as eligible; it never authorizes orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal


DEFAULT_COLDSTART_MIN_DAYS = 30
DEFAULT_PAPER_TRACKING_MIN_DAYS = 30
DEFAULT_WALKFORWARD_MAX_AGE_DAYS = 35

RecommendationStatus = Literal["allowed", "blocked"]


@dataclass(frozen=True)
class FreshnessEvidence:
    """Caller-owned freshness result for the live data used by a recommendation."""

    ok: bool
    status: str = ""
    reason: str = ""


@dataclass(frozen=True)
class RecommendationGateInputs:
    """All evidence required by the global recommendation gate.

    ``evaluated_at`` is explicit so the function is deterministic and testable.
    ``circuit_breaker_until`` is a calendar date because the risk state uses a
    date-level cooldown.  This object contains evidence only; it does not load it.
    """

    coldstart_days: int
    paper_tracking_days: int
    walkforward_ok: bool
    walkforward_updated_at: datetime | None
    freshness: FreshnessEvidence
    circuit_breaker_until: date | None
    evaluated_at: datetime
    coldstart_min_days: int = DEFAULT_COLDSTART_MIN_DAYS
    paper_tracking_min_days: int = DEFAULT_PAPER_TRACKING_MIN_DAYS
    walkforward_max_age_days: int = DEFAULT_WALKFORWARD_MAX_AGE_DAYS


@dataclass(frozen=True)
class RecommendationGateResult:
    """Research-only recommendation gate result."""

    recommendation_allowed: bool
    status: RecommendationStatus
    reasons: tuple[str, ...]


def evaluate(inputs: RecommendationGateInputs) -> RecommendationGateResult:
    """Evaluate the hard prerequisites for a paper research recommendation.

    Cold-start, paper-tracking, walk-forward, and portfolio-protection state
    are validation context for this non-trading application. They must not
    erase live candidates. Freshness remains a hard gate because stale data
    cannot support a short-term research recommendation.
    """
    _validate_inputs(inputs)
    reasons: list[str] = []

    if not inputs.freshness.ok:
        detail = inputs.freshness.reason or inputs.freshness.status or "unknown"
        reasons.append(f"freshness_not_ready:{detail}")

    deduped = tuple(dict.fromkeys(reasons))
    return RecommendationGateResult(
        recommendation_allowed=not deduped,
        status="allowed" if not deduped else "blocked",
        reasons=deduped,
    )


def _validate_inputs(inputs: RecommendationGateInputs) -> None:
    if not isinstance(inputs, RecommendationGateInputs):
        raise TypeError("inputs must be RecommendationGateInputs")
    for name in ("coldstart_days", "paper_tracking_days"):
        value = getattr(inputs, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    for name in (
        "coldstart_min_days",
        "paper_tracking_min_days",
        "walkforward_max_age_days",
    ):
        value = getattr(inputs, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if inputs.evaluated_at.tzinfo is None or inputs.evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    if inputs.walkforward_updated_at is not None and (
        inputs.walkforward_updated_at.tzinfo is None
        or inputs.walkforward_updated_at.utcoffset() is None
    ):
        raise ValueError("walkforward_updated_at must be timezone-aware")
