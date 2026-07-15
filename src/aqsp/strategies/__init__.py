from __future__ import annotations

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.momentum import MomentumStrategy
from aqsp.strategies.quality import QualityStrategy
from aqsp.strategies.value import ValueStrategy
from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.closing_premium import (
    ClosingPremiumStrategy,
    PremiumSignal,
    format_closing_signals,
)
from aqsp.strategies.morning_breakout import (
    MorningBreakoutStrategy,
    BreakoutSignal,
    format_morning_signals,
)
from aqsp.strategies.limit_up_ladder import (
    LimitUpLadderStrategy,
    LimitUpSignal,
    format_limit_up_signals,
)
from aqsp.strategies.intraday_trade import (
    IntradayTradeStrategy,
    IntradaySignal,
    format_intraday_signals,
)
from aqsp.strategies.sector_rotation import (
    SectorRotationStrategy,
    SectorSignal,
    SectorMetrics,
    format_sector_signals,
)
from aqsp.strategies.ma_breakout import (
    MABreakoutStrategy,
    MABreakoutSignal,
    format_ma_breakout_signals,
)
from aqsp.strategies.event_driven import (
    EventDrivenStrategy,
    EventSignal,
    format_event_signals,
)
from aqsp.strategies.n_rebound import (
    NReboundStrategy,
    NReboundSignal,
    detect_n_rebound_signal,
)
from aqsp.strategies.thresholds import Thresholds, load_thresholds
from aqsp.strategies.catalog import (
    StrategyCatalog,
    StrategyCatalogEntry,
    load_strategy_catalog,
)

__all__ = [
    "BaseStrategy",
    "StrategyConfig",
    "MomentumStrategy",
    "QualityStrategy",
    "ValueStrategy",
    "CompositeStrategy",
    "ClosingPremiumStrategy",
    "PremiumSignal",
    "format_closing_signals",
    "MorningBreakoutStrategy",
    "BreakoutSignal",
    "format_morning_signals",
    # 短线核心策略（默认 enabled=false，需 walk-forward 双门验证后启用）
    "LimitUpLadderStrategy",
    "LimitUpSignal",
    "format_limit_up_signals",
    "IntradayTradeStrategy",
    "IntradaySignal",
    "format_intraday_signals",
    "SectorRotationStrategy",
    "SectorSignal",
    "SectorMetrics",
    "format_sector_signals",
    "MABreakoutStrategy",
    "MABreakoutSignal",
    "format_ma_breakout_signals",
    "EventDrivenStrategy",
    "EventSignal",
    "format_event_signals",
    "NReboundStrategy",
    "NReboundSignal",
    "detect_n_rebound_signal",
    "Thresholds",
    "load_thresholds",
    "StrategyCatalog",
    "StrategyCatalogEntry",
    "load_strategy_catalog",
]
