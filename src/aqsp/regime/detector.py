"""Compatibility facade for the canonical HMM -> five-regime runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from aqsp.core.time import now_shanghai
from aqsp.regime.hmm_detector import HMMRegimeDetector
from aqsp.regime.runtime import detect_runtime_regime_context
from aqsp.regime.strategy_mixer import canonicalize_regime
from aqsp.strategies.thresholds import Thresholds, load_thresholds


@dataclass(frozen=True)
class MarketRegime:
    name: str
    description: str
    features: Dict[str, float]
    confidence: float
    timestamp: datetime


@dataclass(frozen=True)
class RegimeChange:
    from_regime: str
    to_regime: str
    timestamp: datetime
    confidence: float


class RegimeDetector:
    """Backward-compatible API backed by the same HMM runtime as production."""

    def __init__(
        self,
        lookback_days: int = 60,
        thresholds: Thresholds | None = None,
        hmm_detector: HMMRegimeDetector | None = None,
    ) -> None:
        self.lookback_days = int(lookback_days)
        self._fixed_thresholds = thresholds is not None
        self.thresholds = thresholds or load_thresholds()
        self.hmm_detector = hmm_detector or HMMRegimeDetector(
            lookback_days=self.lookback_days,
            min_data_points=max(20, self.thresholds.regime.min_sample_size),
        )
        self._history: List[MarketRegime] = []
        self._last_detect_time: Optional[datetime] = None

    def detect(self, index_data: Dict[str, pd.DataFrame]) -> MarketRegime:
        self._refresh_thresholds()
        if self._is_in_cooldown():
            return self._history[-1] if self._history else self._default_regime()

        benchmark = next(
            (
                symbol
                for symbol, frame in index_data.items()
                if isinstance(frame, pd.DataFrame) and not frame.empty
            ),
            None,
        )
        if benchmark is None:
            return self._default_regime()

        context = detect_runtime_regime_context(
            index_data,
            benchmark_symbol=benchmark,
            hmm_detector=self.hmm_detector,
            thresholds=self.thresholds,
        )
        if not context.regime:
            return self._default_regime()

        regime = MarketRegime(
            name=canonicalize_regime(context.regime),
            description=_REGIME_DESCRIPTIONS.get(
                canonicalize_regime(context.regime), "市场状态"
            ),
            features={
                "annualized_volatility": context.annualized_volatility,
                "confidence": context.confidence,
            },
            confidence=context.confidence,
            timestamp=now_shanghai(),
        )
        self._history.append(regime)
        self._last_detect_time = regime.timestamp
        self._history = self._history[-100:]
        return regime

    def _is_in_cooldown(self) -> bool:
        if self._last_detect_time is None:
            return False
        elapsed = now_shanghai() - self._last_detect_time
        return elapsed.total_seconds() < self.thresholds.regime.cooldown_hours * 3600

    def _refresh_thresholds(self) -> None:
        if not self._fixed_thresholds:
            self.thresholds = load_thresholds()

    def _default_regime(self) -> MarketRegime:
        return MarketRegime(
            name="unknown",
            description="数据不足或无法判定市场状态",
            features={},
            confidence=0.0,
            timestamp=now_shanghai(),
        )

    def get_history(self, days: int = 30) -> List[MarketRegime]:
        if days <= 0:
            return list(self._history)
        cutoff = now_shanghai() - pd.Timedelta(days=days)
        return [item for item in self._history if item.timestamp >= cutoff]

    def detect_changes(self) -> List[RegimeChange]:
        return [
            RegimeChange(
                from_regime=previous.name,
                to_regime=current.name,
                timestamp=current.timestamp,
                confidence=current.confidence,
            )
            for previous, current in zip(self._history, self._history[1:])
            if previous.name != current.name
        ]

    def get_regime_adjustment(self, regime_name: str) -> float:
        canonical = canonicalize_regime(regime_name)
        legacy = {
            "aggressive_bull": "stable_bull",
            "defensive_bear": "volatile_bear",
            "rotation_sideways": "stable_sideways",
        }.get(canonical, canonical)
        return self.thresholds.regime.adjustments.get(
            canonical,
            self.thresholds.regime.adjustments.get(legacy, 1.0),
        )


_REGIME_DESCRIPTIONS = {
    "aggressive_bull": "进攻牛市",
    "volatile_bull": "波动牛市",
    "defensive_bear": "防守熊市",
    "rotation_sideways": "震荡轮动",
    "emergency_defensive": "紧急防守",
}
