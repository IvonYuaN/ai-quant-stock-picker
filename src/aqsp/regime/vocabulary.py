"""Canonical market-regime vocabulary shared without importing strategy code."""

from __future__ import annotations

CANONICAL_REGIMES = (
    "aggressive_bull",
    "volatile_bull",
    "defensive_bear",
    "rotation_sideways",
    "emergency_defensive",
)

_REGIME_ALIASES = {
    "stable_bull": "aggressive_bull",
    "volatile_bull": "volatile_bull",
    "stable_bear": "defensive_bear",
    "volatile_bear": "defensive_bear",
    "stable_sideways": "rotation_sideways",
    "volatile_sideways": "rotation_sideways",
    "bull": "aggressive_bull",
    "bear": "defensive_bear",
    "sideways": "rotation_sideways",
}


def canonicalize_regime(regime: str) -> str:
    """Return the single five-bucket regime vocabulary used by the system."""
    normalized = str(regime or "").strip().lower()
    if normalized in CANONICAL_REGIMES:
        return normalized
    return _REGIME_ALIASES.get(normalized, normalized)


def canonical_regime_from_hmm(
    hmm_regime: str,
    *,
    annualized_volatility: float,
    volatility_high: float,
    emergency: bool = False,
) -> str:
    """Map HMM's three states and volatility into the five strategy buckets."""
    if emergency:
        return "emergency_defensive"
    state = str(hmm_regime or "sideways").strip().lower()
    if state == "bull":
        return "volatile_bull" if annualized_volatility > volatility_high else "aggressive_bull"
    if state == "bear":
        return "defensive_bear"
    return "rotation_sideways"
