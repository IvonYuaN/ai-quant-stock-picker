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
    )
