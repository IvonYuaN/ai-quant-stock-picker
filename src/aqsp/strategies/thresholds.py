from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict
import yaml
from pathlib import Path


@dataclass(frozen=True)
class MomentumWeights:
    momentum: float = 0.4
    trend: float = 0.3
    rsi: float = 0.3


@dataclass(frozen=True)
class QualityWeights:
    roe: float = 0.3
    roa: float = 0.25
    debt: float = 0.25
    margin: float = 0.2


@dataclass(frozen=True)
class ValueWeights:
    pe: float = 0.4
    pb: float = 0.3
    dividend: float = 0.3


@dataclass(frozen=True)
class VolumeWeights:
    surge: float = 0.4
    breakout: float = 0.35
    correlation: float = 0.25


@dataclass(frozen=True)
class VolumeThresholds:
    enabled: bool = True
    lookback_days: int = 60
    volume_ma_period: int = 20
    surge_multiplier: float = 1.5
    price_ma_period: int = 20
    correlation_window: int = 10
    weights: VolumeWeights = field(default_factory=VolumeWeights)


@dataclass(frozen=True)
class MomentumThresholds:
    lookback_days: int = 60
    min_returns: float = 0.05
    max_volatility: float = 0.3
    rsi_overbought: int = 70
    rsi_oversold: int = 30
    ma_period: int = 20
    trend_strength_threshold: float = 0.5
    weights: MomentumWeights = field(default_factory=MomentumWeights)


@dataclass(frozen=True)
class QualityThresholds:
    enabled: bool = False
    min_roe: float = 0.1
    min_roa: float = 0.05
    max_debt_ratio: float = 0.6
    min_cash_flow: float = 0.0
    operating_margin_threshold: float = 0.05
    eps_growth_min: float = 0.1
    weights: QualityWeights = field(default_factory=QualityWeights)


@dataclass(frozen=True)
class ValueThresholds:
    enabled: bool = False
    max_pe: float = 30
    max_pb: float = 5
    min_dividend_yield: float = 0.02
    ev_ebitda_max: float = 15
    price_sales_max: float = 3
    weights: ValueWeights = field(default_factory=ValueWeights)


@dataclass(frozen=True)
class CompositeThresholds:
    momentum_weight: float = 0.4
    quality_weight: float = 0.2
    value_weight: float = 0.2
    volume_weight: float = 0.2
    mean_reversion_weight: float = 0.0
    triple_rise_weight: float = 0.0
    min_total_score: float = 0.6


@dataclass(frozen=True)
class RiskThresholds:
    max_drawdown: float = 0.2
    volatility_limit: float = 0.4
    liquidity_threshold: float = 1000000
    single_stock_stop_pct: float = 0.08
    portfolio_stop_pct: float = 0.15
    warning_threshold_pct: float = 0.05
    trailing_stop_pct: float = 0.05
    enable_trailing_stop: bool = True
    circuit_breaker_daily_loss_pct: float = 3.0
    circuit_breaker_weekly_loss_pct: float = 6.0
    circuit_breaker_monthly_loss_pct: float = 10.0
    circuit_breaker_cooldown_days: int = 5
    max_position_pct: float = 0.30
    soft_stop_loss_pct: float = 0.03
    trailing_stop_activation_pct: float = 0.05
    max_holding_days: int = 10
    profit_take_threshold_pct: float = 0.15
    portfolio_daily_loss_pct: float = 0.02
    portfolio_weekly_loss_pct: float = 0.05
    portfolio_max_drawdown_pct: float = 0.10
    max_positions: int = 8
    max_single_position_pct: float = 0.30
    max_sector_concentration: float = 0.40
    max_correlation: float = 0.70
    min_cash_reserve: float = 0.10
    market_crash_threshold: float = -0.05
    market_correction_threshold: float = -0.10
    sector_panic_threshold: int = 5
    halt_trigger_count: int = 3
    auto_resume_days: int = 1
    avg_volume_ratio_min: float = 0.7
    north_flow_exit_threshold: float = -5000000000


@dataclass(frozen=True)
class FilterThresholds:
    min_days_listed: int = 90
    min_price: float = 1.0
    max_price: float = 1000.0
    min_avg_volume: float = 1000000


@dataclass(frozen=True)
class ExecutionThresholds:
    limit_up_tolerance: float = 0.005
    limit_down_tolerance: float = 0.005
    slippage: float = 0.002
    commission_rate: float = 0.0003
    fallback_limit_main_pct: float = 0.10
    fallback_limit_growth_pct: float = 0.20
    fallback_limit_bse_pct: float = 0.30
    fallback_limit_st_pct: float = 0.05


@dataclass(frozen=True)
class RegimeStrategyWeights:
    momentum: float = 1.0
    quality: float = 1.0
    value: float = 1.0
    volume: float = 1.0
    mean_reversion: float = 1.0
    triple_rise: float = 1.0


_DEFAULT_REGIME_STRATEGY_WEIGHTS: Dict[str, RegimeStrategyWeights] = {
    "stable_bull": RegimeStrategyWeights(
        momentum=1.2,
        quality=0.9,
        value=0.8,
        volume=1.1,
        mean_reversion=0.7,
        triple_rise=1.1,
    ),
    "volatile_bull": RegimeStrategyWeights(
        momentum=1.1,
        quality=0.8,
        value=0.9,
        volume=1.2,
        mean_reversion=0.8,
        triple_rise=1.0,
    ),
    "stable_bear": RegimeStrategyWeights(
        momentum=0.7,
        quality=1.3,
        value=1.2,
        volume=0.8,
        mean_reversion=1.3,
        triple_rise=0.8,
    ),
    "volatile_bear": RegimeStrategyWeights(
        momentum=0.6,
        quality=1.2,
        value=1.1,
        volume=0.9,
        mean_reversion=1.4,
        triple_rise=0.7,
    ),
    "stable_sideways": RegimeStrategyWeights(
        momentum=0.9,
        quality=1.1,
        value=1.1,
        volume=1.0,
        mean_reversion=1.1,
        triple_rise=0.9,
    ),
    "volatile_sideways": RegimeStrategyWeights(
        momentum=0.8,
        quality=1.0,
        value=1.0,
        volume=1.1,
        mean_reversion=1.2,
        triple_rise=0.8,
    ),
}


@dataclass(frozen=True)
class RegimeThresholds:
    volatility_high: float = 0.3
    momentum_bull: float = 0.1
    momentum_bear: float = -0.1
    trend_bull: float = 0.02
    trend_bear: float = -0.02
    confidence_volatility: float = 0.2
    confidence_trend: float = 0.01
    confidence_momentum: float = 0.05
    min_sample_size: int = 20
    cooldown_hours: int = 24
    adjustments: Dict[str, float] = field(
        default_factory=lambda: {
            "stable_bull": 1.1,
            "volatile_bull": 0.9,
            "stable_bear": 0.8,
            "volatile_bear": 0.6,
            "stable_sideways": 0.95,
            "volatile_sideways": 0.85,
        }
    )
    strategy_weights: Dict[str, RegimeStrategyWeights] = field(
        default_factory=lambda: dict(_DEFAULT_REGIME_STRATEGY_WEIGHTS)
    )


@dataclass(frozen=True)
class MeanReversionThresholds:
    enabled: bool = True
    lookback_days: int = 20
    rsi_period: int = 14
    oversold_threshold: float = 30
    deviation_threshold: float = -0.05
    strong_oversold_threshold: float = 20
    weak_oversold_threshold: float = 40
    deep_deviation_threshold: float = -0.15
    medium_deviation_threshold: float = -0.10
    shallow_deviation_threshold: float = -0.02
    volume_strong_ratio: float = 1.5
    volume_medium_ratio: float = 1.2
    rsi_weight: float = 0.45
    deviation_weight: float = 0.35
    volume_weight: float = 0.20


@dataclass(frozen=True)
class TripleRiseWeights:
    triple_rise: float = 0.40
    v_bottom: float = 0.35
    volume_confirmation: float = 0.25


@dataclass(frozen=True)
class TripleRiseThresholds:
    enabled: bool = True
    lookback_days: int = 25
    min_data_points: int = 20
    v_bottom_lookback: int = 20
    avg_rise_strong: float = 0.03
    avg_rise_medium: float = 0.02
    avg_rise_weak: float = 0.01
    avg_rise_strong_score: float = 1.0
    avg_rise_medium_score: float = 0.8
    avg_rise_weak_score: float = 0.6
    avg_rise_min_score: float = 0.4
    v_bottom_edge_days: int = 3
    v_bottom_strong_recovery: float = 0.10
    v_bottom_medium_recovery: float = 0.05
    v_bottom_weak_recovery: float = 0.02
    v_bottom_strong_score: float = 1.0
    v_bottom_medium_score: float = 0.7
    v_bottom_weak_score: float = 0.4
    v_bottom_min_score: float = 0.1
    volume_recent_days: int = 3
    volume_min_points: int = 5
    volume_avg_window: int = 20
    volume_strong_ratio: float = 1.3
    volume_medium_ratio: float = 1.0
    volume_strong_score: float = 1.0
    volume_medium_score: float = 0.6
    volume_price_up_score: float = 0.3
    weights: TripleRiseWeights = field(default_factory=TripleRiseWeights)


@dataclass(frozen=True)
class ScoringThresholds:
    liquidity_penalty: float = -35
    ma_full_bull: float = 24
    ma_short_bull: float = 16
    ma_below_ma20: float = -18
    ma20_slope_up: float = 10
    ma20_slope_down: float = -10
    ret20_strong: float = 14
    ret20_strong_threshold: float = 12
    ret20_weak: float = -12
    ret20_weak_threshold: float = -8
    near_high_bonus: float = 18
    near_high_threshold: float = 0.995
    near_high_volume: float = 1.35
    pullback_bonus: float = 16
    pullback_ma5_lower: float = 0.985
    pullback_ma10_upper: float = 1.025
    pullback_volume_max: float = 1.1
    macd_improve: float = 8
    macd_weaken: float = -8
    rsi_healthy_low: float = 45
    rsi_healthy_high: float = 72
    rsi_healthy_bonus: float = 7
    rsi_overbought: float = 82
    rsi_overbought_penalty: float = -12
    bias_high_penalty: float = -18
    bias_healthy_bonus: float = 8
    bias_healthy_max: float = 8
    range_strong_threshold: float = 0.68
    range_strong_bonus: float = 6
    upper_shadow_penalty: float = -14
    upper_shadow_threshold: float = 4
    upper_shadow_volume: float = 1.5
    reversal_rsi_threshold: float = 42
    open_calm_bonus: float = 5
    open_calm_bias: float = 10
    open_calm_volume: float = 2.5
    amplitude_penalty: float = -8
    amplitude_threshold: float = 9
    confidence_strategy_weight: float = 12
    confidence_max_strategies: float = 48
    confidence_high_score: float = 60
    confidence_high_bonus: float = 25
    confidence_mid_score: float = 40
    confidence_mid_bonus: float = 15
    confidence_low_score: float = 20
    confidence_low_bonus: float = 8
    confidence_risk_base: float = 15
    confidence_risk_penalty: float = 5
    confidence_volume_low: float = 1.0
    confidence_volume_high: float = 2.5
    confidence_volume_bonus: float = 12
    confidence_volume_high_bonus: float = 4
    rating_strong: float = 70
    rating_buy: float = 55
    rating_watch: float = 40
    position_strong_score: float = 68
    position_strong_risks: float = 1
    position_mid_score: float = 52
    stop_atr_multiplier: float = 1.2
    take_profit_multiplier: float = 1.8
    ma20_slope_lookback: float = 6
    ma20_slope_up_threshold: float = 1.0
    ma20_slope_down_threshold: float = -1.5


@dataclass(frozen=True)
class MorningBreakoutWeights:
    change_pct: float = 0.30
    volume: float = 0.25
    technical: float = 0.20
    fund_flow: float = 0.15
    market: float = 0.10


@dataclass(frozen=True)
class MorningBreakoutThresholds:
    enabled: bool = True
    min_change_pct: float = 5.0
    near_limit_pct: float = 9.5
    strong_pct: float = 7.0
    volume_ratio_strong: float = 3.0
    volume_ratio_medium: float = 2.0
    min_score: float = 60.0
    atr_period: int = 14
    atr_stop_multiplier: float = 2.0
    default_stop_pct: float = 0.05
    next_day_limit_pct: float = 0.10
    position_high_score: float = 80.0
    position_high_confidence: float = 0.7
    position_high_pct: float = 0.3
    position_mid_score: float = 60.0
    position_mid_confidence: float = 0.5
    position_mid_pct: float = 0.2
    position_low_pct: float = 0.1
    weights: MorningBreakoutWeights = field(default_factory=MorningBreakoutWeights)


@dataclass(frozen=True)
class ClosingPremiumWeights:
    change_pct: float = 0.20
    volume_price: float = 0.25
    closing_trend: float = 0.20
    technical: float = 0.20
    support_resistance: float = 0.15


@dataclass(frozen=True)
class ClosingPremiumThresholds:
    enabled: bool = True
    min_change_pct: float = 2.0
    max_change_pct: float = 7.0
    optimal_change_min: float = 3.0
    optimal_change_max: float = 5.0
    min_score: float = 65.0
    volume_ratio_strong: float = 1.5
    volume_ratio_moderate: float = 1.2
    closing_volume_ratio: float = 1.3
    closing_change_threshold: float = 1.0
    ma_periods: tuple[int, ...] = (5, 10, 20)
    atr_period: int = 14
    atr_stop_multiplier: float = 2.0
    atr_tp1_multiplier: float = 2.0
    atr_tp2_multiplier: float = 3.0
    default_stop_pct: float = 0.05
    support_threshold: float = 5.0
    resistance_threshold: float = 5.0
    high_open_check_days: int = 5
    high_open_count_threshold: int = 3
    volume_shrink_ratio: float = 0.7
    volume_shrink_days: int = 3
    lookback_days: int = 30
    min_data_points: int = 10
    weights: ClosingPremiumWeights = field(default_factory=ClosingPremiumWeights)


@dataclass(frozen=True)
class NReboundThresholds:
    enabled: bool = True
    lookback_days: int = 30
    max_days_since_limit_up: int = 12
    limit_up_min_pct: float = 9.5
    pullback_min_pct: float = 3.0
    pullback_max_pct: float = 15.0
    volume_shrink_ratio: float = 0.75
    ma5_deviation_max_pct: float = 8.0
    min_score: float = 14.0


@dataclass(frozen=True)
class Thresholds:
    version: str = "2.0.0"
    effective_from: str = ""
    last_walkforward_run: str = ""
    description: str = "策略阈值配置"
    momentum: MomentumThresholds = field(default_factory=MomentumThresholds)
    quality: QualityThresholds = field(default_factory=QualityThresholds)
    value: ValueThresholds = field(default_factory=ValueThresholds)
    composite: CompositeThresholds = field(default_factory=CompositeThresholds)
    risk: RiskThresholds = field(default_factory=RiskThresholds)
    filter: FilterThresholds = field(default_factory=FilterThresholds)
    execution: ExecutionThresholds = field(default_factory=ExecutionThresholds)
    regime: RegimeThresholds = field(default_factory=RegimeThresholds)
    volume: VolumeThresholds = field(default_factory=VolumeThresholds)
    mean_reversion: MeanReversionThresholds = field(
        default_factory=MeanReversionThresholds
    )
    triple_rise: TripleRiseThresholds = field(default_factory=TripleRiseThresholds)
    scoring: ScoringThresholds = field(default_factory=ScoringThresholds)
    morning_breakout: MorningBreakoutThresholds = field(
        default_factory=MorningBreakoutThresholds
    )
    closing_premium: ClosingPremiumThresholds = field(
        default_factory=ClosingPremiumThresholds
    )
    n_rebound: NReboundThresholds = field(default_factory=NReboundThresholds)


def _parse_momentum(data: dict) -> MomentumThresholds:
    weights_data = data.pop("weights", {})
    return MomentumThresholds(
        **data,
        weights=MomentumWeights(**weights_data) if weights_data else MomentumWeights(),
    )


def _parse_quality(data: dict) -> QualityThresholds:
    weights_data = data.pop("weights", {})
    return QualityThresholds(
        **data,
        weights=QualityWeights(**weights_data) if weights_data else QualityWeights(),
    )


def _parse_value(data: dict) -> ValueThresholds:
    weights_data = data.pop("weights", {})
    return ValueThresholds(
        **data,
        weights=ValueWeights(**weights_data) if weights_data else ValueWeights(),
    )


def _parse_volume(data: dict) -> VolumeThresholds:
    weights_data = data.pop("weights", {})
    return VolumeThresholds(
        **data,
        weights=VolumeWeights(**weights_data) if weights_data else VolumeWeights(),
    )


def _parse_morning_breakout(data: dict) -> MorningBreakoutThresholds:
    weights_data = data.pop("weights", {})
    return MorningBreakoutThresholds(
        **data,
        weights=MorningBreakoutWeights(**weights_data)
        if weights_data
        else MorningBreakoutWeights(),
    )


def _parse_closing_premium(data: dict) -> ClosingPremiumThresholds:
    weights_data = data.pop("weights", {})
    ma_periods = data.pop("ma_periods", (5, 10, 20))
    if isinstance(ma_periods, list):
        ma_periods = tuple(ma_periods)
    return ClosingPremiumThresholds(
        **data,
        ma_periods=ma_periods,
        weights=ClosingPremiumWeights(**weights_data)
        if weights_data
        else ClosingPremiumWeights(),
    )


def _parse_n_rebound(data: dict) -> NReboundThresholds:
    return NReboundThresholds(**data)


def _parse_mean_reversion(data: dict) -> MeanReversionThresholds:
    return MeanReversionThresholds(**data)


def _parse_triple_rise(data: dict) -> TripleRiseThresholds:
    weights_data = data.pop("weights", {})
    return TripleRiseThresholds(
        **data,
        weights=TripleRiseWeights(**weights_data)
        if weights_data
        else TripleRiseWeights(),
    )


_DEFAULT_REGIME_ADJUSTMENTS: Dict[str, float] = {
    "stable_bull": 1.1,
    "volatile_bull": 0.9,
    "stable_bear": 0.8,
    "volatile_bear": 0.6,
    "stable_sideways": 0.95,
    "volatile_sideways": 0.85,
}


def _parse_regime(data: dict) -> RegimeThresholds:
    adjustments_data = data.pop("adjustments", {})
    strategy_weights_data = data.pop("strategy_weights", {})
    strategy_weights = dict(_DEFAULT_REGIME_STRATEGY_WEIGHTS)
    for regime, weights in strategy_weights_data.items():
        if isinstance(weights, dict):
            strategy_weights[str(regime)] = RegimeStrategyWeights(**weights)
    return RegimeThresholds(
        **data,
        adjustments=adjustments_data
        if adjustments_data
        else _DEFAULT_REGIME_ADJUSTMENTS,
        strategy_weights=strategy_weights,
    )


def load_thresholds(filepath: str = None) -> Thresholds:
    if filepath is None:
        filepath = (
            Path(__file__).parent.parent.parent.parent / "config" / "thresholds.yaml"
        )

    path = Path(filepath)
    if not path.exists():
        return Thresholds()

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return Thresholds(
        version=data.get("version", "2.0.0"),
        effective_from=data.get("effective_from", ""),
        last_walkforward_run=data.get("last_walkforward_run", ""),
        description=data.get("description", ""),
        momentum=_parse_momentum(data.get("momentum", {})),
        quality=_parse_quality(data.get("quality", {})),
        value=_parse_value(data.get("value", {})),
        composite=CompositeThresholds(**data.get("composite", {})),
        risk=RiskThresholds(**data.get("risk", {})),
        filter=FilterThresholds(**data.get("filter", {})),
        execution=ExecutionThresholds(**data.get("execution", {})),
        regime=_parse_regime(data.get("regime", {})),
        volume=_parse_volume(data.get("volume", {})),
        mean_reversion=_parse_mean_reversion(data.get("mean_reversion", {})),
        triple_rise=_parse_triple_rise(data.get("triple_rise", {})),
        scoring=ScoringThresholds(**data.get("scoring", {})),
        morning_breakout=_parse_morning_breakout(data.get("morning_breakout", {})),
        closing_premium=_parse_closing_premium(data.get("closing_premium", {})),
        n_rebound=_parse_n_rebound(data.get("n_rebound", {})),
    )
