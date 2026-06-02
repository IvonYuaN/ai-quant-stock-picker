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
    scoring: ScoringThresholds = field(default_factory=ScoringThresholds)
    morning_breakout: MorningBreakoutThresholds = field(
        default_factory=MorningBreakoutThresholds
    )
    closing_premium: ClosingPremiumThresholds = field(
        default_factory=ClosingPremiumThresholds
    )


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
    return RegimeThresholds(
        **data,
        adjustments=adjustments_data
        if adjustments_data
        else _DEFAULT_REGIME_ADJUSTMENTS,
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
        scoring=ScoringThresholds(**data.get("scoring", {})),
        morning_breakout=_parse_morning_breakout(data.get("morning_breakout", {})),
        closing_premium=_parse_closing_premium(data.get("closing_premium", {})),
    )
