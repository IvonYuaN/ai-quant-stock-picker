from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from aqsp.regime.vocabulary import (
    canonical_regime_from_hmm as _canonical_regime_from_hmm,
    canonicalize_regime,
)

if TYPE_CHECKING:
    from aqsp.strategies.thresholds import Thresholds

_REGIME_LABELS = {
    "aggressive_bull": "进攻牛市",
    "volatile_bull": "波动牛市",
    "defensive_bear": "防守熊市",
    "rotation_sideways": "震荡轮动",
    "emergency_defensive": "紧急防守",
    "stable_bull": "稳定上涨",
    "stable_bear": "稳定下跌",
    "volatile_bear": "波动下跌",
    "stable_sideways": "稳定震荡",
    "volatile_sideways": "波动震荡",
}

_STRATEGY_LABELS = {
    "momentum": "动量趋势",
    "limit_up_ladder": "涨停接力",
    "morning_breakout": "早盘突破",
    "sector_rotation": "板块轮动",
    "triple_rise": "三连阳",
    "intraday_trade": "日内快反",
    "quality": "质量稳健",
    "value": "价值低估",
    "mean_reversion": "均值回归",
    "volume": "量价突破",
    "rps_momentum": "RPS 动量",
    "volume_breakout": "放量突破",
    "ma_pullback": "均线回踩",
    "bowl_rebound": "碗形反弹",
    "low_vol_trend": "低波趋势",
    "n_rebound": "N 字反弹",
}


def canonical_regime_from_hmm(
    hmm_regime: str,
    *,
    annualized_volatility: float,
    volatility_high: float,
    emergency: bool = False,
) -> str:
    """Compatibility export for callers importing the mixer module."""
    return _canonical_regime_from_hmm(
        hmm_regime,
        annualized_volatility=annualized_volatility,
        volatility_high=volatility_high,
        emergency=emergency,
    )


@dataclass(frozen=True)
class StrategyMixProfile:
    mix_id: str
    name: str
    description: str
    suitable_regimes: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeStrategyMix:
    regime: str
    regime_label: str
    mix_id: str = ""
    mix_name: str = ""
    mix_description: str = ""
    strategy_focus: tuple[str, ...] = ()
    strategy_weights: tuple[tuple[str, float], ...] = ()
    source: str = ""

    @property
    def has_mix(self) -> bool:
        return bool(self.mix_name)


@dataclass(frozen=True)
class StrategyMix:
    """兼容旧自适应层的 canonical 策略组合定义。"""

    name: str
    description: str
    enabled_strategies: list[str]
    weights: dict[str, float]
    suitable_regimes: list[str]
    expected_sharpe: float


STRATEGY_MIXES: dict[str, StrategyMix] = {
    "aggressive_bull": StrategyMix(
        name="进攻牛市",
        description="稳定上涨期，重仓动量+涨停板",
        enabled_strategies=[
            "momentum",
            "limit_up_ladder",
            "morning_breakout",
            "sector_rotation",
        ],
        weights={
            "momentum": 0.30,
            "limit_up_ladder": 0.30,
            "morning_breakout": 0.20,
            "sector_rotation": 0.20,
        },
        suitable_regimes=["aggressive_bull"],
        expected_sharpe=2.5,
    ),
    "volatile_bull": StrategyMix(
        name="波动牛市",
        description="波动牛市，平衡进攻和防守",
        enabled_strategies=[
            "momentum",
            "triple_rise",
            "intraday_trade",
            "sector_rotation",
        ],
        weights={
            "momentum": 0.25,
            "triple_rise": 0.25,
            "intraday_trade": 0.25,
            "sector_rotation": 0.25,
        },
        suitable_regimes=["volatile_bull"],
        expected_sharpe=2.0,
    ),
    "defensive_bear": StrategyMix(
        name="防守熊市",
        description="熊市防守，质量+均值回归",
        enabled_strategies=["quality", "value", "mean_reversion"],
        weights={
            "quality": 0.40,
            "value": 0.30,
            "mean_reversion": 0.30,
        },
        suitable_regimes=["defensive_bear"],
        expected_sharpe=1.0,
    ),
    "rotation_sideways": StrategyMix(
        name="震荡轮动",
        description="震荡市，多因子轮动",
        enabled_strategies=[
            "momentum",
            "mean_reversion",
            "sector_rotation",
            "intraday_trade",
        ],
        weights={
            "momentum": 0.20,
            "mean_reversion": 0.30,
            "sector_rotation": 0.30,
            "intraday_trade": 0.20,
        },
        suitable_regimes=["rotation_sideways"],
        expected_sharpe=1.5,
    ),
    "emergency_defensive": StrategyMix(
        name="紧急防守",
        description="系统风险触发，仅保留防守策略",
        enabled_strategies=["quality"],
        weights={"quality": 1.0},
        suitable_regimes=[],
        expected_sharpe=0.5,
    ),
}


class StrategyMixAdaptor:
    """从 canonical 策略组合表中选择当前 regime 的组合。"""

    def select_mix(self, regime: str) -> StrategyMix:
        regime = canonicalize_regime(regime)
        if regime == "emergency_defensive":
            return STRATEGY_MIXES["emergency_defensive"]
        for mix in STRATEGY_MIXES.values():
            if regime in mix.suitable_regimes:
                return mix
        return STRATEGY_MIXES["rotation_sideways"]

    def get_mix_by_name(self, name: str) -> Optional[StrategyMix]:
        return STRATEGY_MIXES.get(name)


_STRATEGY_MIX_PROFILES: tuple[StrategyMixProfile, ...] = (
    StrategyMixProfile(
        mix_id="aggressive_bull",
        name="进攻牛市",
        description="稳定上涨期，重仓动量+涨停板",
        suitable_regimes=("aggressive_bull",),
    ),
    StrategyMixProfile(
        mix_id="volatile_bull",
        name="波动牛市",
        description="波动牛市，平衡进攻和防守",
        suitable_regimes=("volatile_bull",),
    ),
    StrategyMixProfile(
        mix_id="defensive_bear",
        name="防守熊市",
        description="熊市防守，质量+均值回归",
        suitable_regimes=("defensive_bear",),
    ),
    StrategyMixProfile(
        mix_id="rotation_sideways",
        name="震荡轮动",
        description="震荡市，多因子轮动",
        suitable_regimes=("rotation_sideways",),
    ),
    StrategyMixProfile(
        mix_id="emergency_defensive",
        name="紧急防守",
        description="系统风险触发，仅保留防守策略",
        suitable_regimes=("emergency_defensive",),
    ),
)


def resolve_regime_label(regime: str) -> str:
    normalized = str(regime or "").strip().lower()
    canonical = canonicalize_regime(normalized)
    return _REGIME_LABELS.get(normalized, _REGIME_LABELS.get(canonical, regime or ""))


def resolve_strategy_label(strategy_id: str) -> str:
    return _STRATEGY_LABELS.get(strategy_id, strategy_id)


def strategy_mix_profile_for_regime(regime: str) -> StrategyMixProfile | None:
    regime = canonicalize_regime(regime)
    for profile in _STRATEGY_MIX_PROFILES:
        if regime in profile.suitable_regimes:
            return profile
    return None


def build_runtime_strategy_mix(
    regime: str,
    *,
    thresholds: Thresholds,
) -> RuntimeStrategyMix:
    requested_regime = str(regime or "").strip()
    regime = canonicalize_regime(requested_regime)
    regime_label = resolve_regime_label(requested_regime or regime)
    if not regime:
        return RuntimeStrategyMix(regime=regime, regime_label=regime_label)

    # Local import breaks the historical cycle: strategy.py consumes the
    # canonicalizer while this module consumes the concrete screening mapper.
    from aqsp.strategy import strategy_weights_for_regime

    applied_weights = strategy_weights_for_regime(thresholds, regime)
    source = "strategy_weights_for_regime"
    if regime == "emergency_defensive" and not applied_weights:
        applied_weights = STRATEGY_MIXES[regime].weights
        source = "canonical_strategy_mix"
    if not applied_weights:
        return RuntimeStrategyMix(regime=regime, regime_label=regime_label)

    profile = strategy_mix_profile_for_regime(regime)
    weights = tuple(
        (strategy_id, float(weight))
        for strategy_id, weight in sorted(applied_weights.items())
        if float(weight) > 0
    )
    focus = tuple(resolve_strategy_label(strategy_id) for strategy_id, _ in weights)
    return RuntimeStrategyMix(
        regime=regime,
        regime_label=regime_label,
        mix_id=getattr(profile, "mix_id", ""),
        mix_name=getattr(profile, "name", ""),
        mix_description=getattr(profile, "description", ""),
        strategy_focus=focus,
        strategy_weights=weights,
        source=source,
    )
