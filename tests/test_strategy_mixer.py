from __future__ import annotations

import pytest

from aqsp.regime.strategy_mixer import (
    build_runtime_strategy_mix,
    resolve_regime_label,
    resolve_strategy_label,
    strategy_mix_profile_for_regime,
)
from aqsp.strategy import strategy_weights_for_regime
from aqsp.strategies.thresholds import Thresholds


@pytest.mark.parametrize(
    "regime",
    (
        "stable_bull",
        "volatile_bull",
        "stable_bear",
        "volatile_bear",
        "stable_sideways",
        "volatile_sideways",
    ),
)
def test_build_runtime_strategy_mix_covers_all_configured_regimes(
    regime: str,
) -> None:
    mix = build_runtime_strategy_mix(regime, thresholds=Thresholds())

    assert mix.has_mix
    assert mix.strategy_weights


def test_build_runtime_strategy_mix_returns_named_profile_when_regime_matches() -> None:
    mix = build_runtime_strategy_mix("stable_bull", thresholds=Thresholds())

    assert mix.regime_label == "稳定上涨"
    assert mix.mix_id == "aggressive_bull"
    assert mix.mix_name == "进攻牛市"
    assert mix.mix_description == "稳定上涨期，重仓动量+涨停板"
    assert "RPS 动量" in mix.strategy_focus
    assert ("rps_momentum", 1.06) in mix.strategy_weights
    assert ("momentum", 1.2) not in mix.strategy_weights
    assert dict(mix.strategy_weights) == strategy_weights_for_regime(
        Thresholds(), "stable_bull"
    )
    assert "bowl_rebound" not in dict(mix.strategy_weights)
    assert mix.source == "strategy_weights_for_regime"


def test_build_runtime_strategy_mix_accepts_canonical_five_regime() -> None:
    mix = build_runtime_strategy_mix("aggressive_bull", thresholds=Thresholds())

    assert mix.regime == "aggressive_bull"
    assert mix.mix_id == "aggressive_bull"
    assert mix.strategy_weights


def test_build_runtime_strategy_mix_exposes_emergency_defensive_profile() -> None:
    mix = build_runtime_strategy_mix("emergency_defensive", thresholds=Thresholds())

    assert mix.has_mix
    assert mix.mix_id == "emergency_defensive"
    assert mix.source == "canonical_strategy_mix"


def test_build_runtime_strategy_mix_returns_bear_profile_for_volatile_bear() -> None:
    mix = build_runtime_strategy_mix("volatile_bear", thresholds=Thresholds())

    assert mix.regime_label == "波动下跌"
    assert mix.mix_name == "防守熊市"
    assert mix.mix_description == "熊市防守，质量+均值回归"


def test_strategy_mix_profile_for_regime_returns_none_when_unknown() -> None:
    assert strategy_mix_profile_for_regime("unknown_regime") is None
    assert resolve_regime_label("unknown_regime") == "unknown_regime"
    assert resolve_strategy_label("custom_strategy") == "custom_strategy"


def test_adaptive_evolution_reuses_canonical_strategy_mix_registry() -> None:
    from aqsp.regime.strategy_mixer import STRATEGY_MIXES, StrategyMixAdaptor
    from aqsp.strategies.adaptive_evolution import (
        STRATEGY_MIXES as legacy_registry,
        StrategyMixAdaptor as LegacyAdaptor,
    )

    assert legacy_registry is STRATEGY_MIXES
    assert LegacyAdaptor is StrategyMixAdaptor
    assert legacy_registry["emergency_defensive"].suitable_regimes == []
