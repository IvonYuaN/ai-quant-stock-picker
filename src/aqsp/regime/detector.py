from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional
import pandas as pd
import numpy as np

from aqsp.core.time import now_shanghai
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
    def __init__(
        self,
        lookback_days: int = 60,
        thresholds: Thresholds = None,
    ):
        self.lookback_days = lookback_days
        self.thresholds = thresholds or load_thresholds()
        self._history: List[MarketRegime] = []
        self._last_detect_time: Optional[datetime] = None

    def detect(self, index_data: Dict[str, pd.DataFrame]) -> MarketRegime:
        if self._is_in_cooldown():
            if self._history:
                return self._history[-1]
            return self._default_regime()

        total_rows = sum(len(df) for df in index_data.values() if df is not None)
        if total_rows < self.thresholds.regime.min_sample_size:
            return self._default_regime()

        features = self._extract_features(index_data)
        regime = self._classify_regime(features)

        self._history.append(regime)
        self._last_detect_time = now_shanghai()
        if len(self._history) > 100:
            self._history = self._history[-100:]

        return regime

    def _is_in_cooldown(self) -> bool:
        if self._last_detect_time is None:
            return False
        elapsed = now_shanghai() - self._last_detect_time
        return elapsed.total_seconds() < self.thresholds.regime.cooldown_hours * 3600

    def _default_regime(self) -> MarketRegime:
        return MarketRegime(
            name="unknown",
            description="数据不足或冷却中，无法判定市场状态",
            features={},
            confidence=0.0,
            timestamp=now_shanghai(),
        )

    def _extract_features(
        self, index_data: Dict[str, pd.DataFrame]
    ) -> Dict[str, float]:
        features = {
            "volatility": 0.0,
            "trend_strength": 0.0,
            "momentum": 0.0,
            "volume_ratio": 0.0,
            "correlation": 0.0,
        }

        # 注意：regime 基于「单一基准指数」判定（通常是沪深300）。
        # 历史 bug：此处循环对多指数逐个赋值（非累加），导致只有遍历到的
        # 最后一个指数生效、前面被静默覆盖。现改为只取第一个有效指数，
        # 行为明确、不依赖 dict 遍历顺序。如需多指数合成需另设计聚合逻辑。
        for symbol, df in index_data.items():
            if df is None or df.empty:
                continue

            df = df.sort_values("date").tail(self.lookback_days)
            if len(df) < self.thresholds.regime.min_sample_size:
                continue

            prices = df["close"].values
            returns = np.diff(prices) / prices[:-1]

            features["volatility"] = np.std(returns) * np.sqrt(252)
            features["momentum"] = (prices[-1] - prices[0]) / prices[0]

            df_copy = df.copy()
            df_copy["ma20"] = df_copy["close"].rolling(20).mean()
            df_copy["ma60"] = df_copy["close"].rolling(60).mean()
            ma20_last = df_copy["ma20"].iloc[-1]
            ma60_last = df_copy["ma60"].iloc[-1]
            if pd.notna(ma20_last) and pd.notna(ma60_last) and ma60_last != 0:
                features["trend_strength"] = (ma20_last - ma60_last) / ma60_last

            volume = df["volume"].values
            avg_vol = np.mean(volume)
            features["volume_ratio"] = volume[-1] / avg_vol if avg_vol > 0 else 1.0

            # 只用第一个有效指数判定 regime，避免被后续指数覆盖
            break

        return features

    def _classify_regime(self, features: Dict[str, float]) -> MarketRegime:
        rt = self.thresholds.regime
        volatility = features["volatility"]
        trend = features["trend_strength"]
        momentum = features["momentum"]

        if volatility > rt.volatility_high:
            if momentum > rt.momentum_bull:
                name = "volatile_bull"
                desc = "高波动牛市"
            elif momentum < rt.momentum_bear:
                name = "volatile_bear"
                desc = "高波动熊市"
            else:
                name = "volatile_sideways"
                desc = "高波动震荡"
        else:
            if trend > rt.trend_bull:
                name = "stable_bull"
                desc = "平稳牛市"
            elif trend < rt.trend_bear:
                name = "stable_bear"
                desc = "平稳熊市"
            else:
                name = "stable_sideways"
                desc = "平稳震荡"

        confidence = self._calculate_confidence(features)

        return MarketRegime(
            name=name,
            description=desc,
            features=features,
            confidence=confidence,
            timestamp=now_shanghai(),
        )

    def _calculate_confidence(self, features: Dict[str, float]) -> float:
        rt = self.thresholds.regime
        volatility = features["volatility"]
        trend = features["trend_strength"]
        momentum = features["momentum"]

        conf = 0.5

        if volatility > rt.confidence_volatility:
            conf += 0.2
        if abs(trend) > rt.confidence_trend:
            conf += 0.15
        if abs(momentum) > rt.confidence_momentum:
            conf += 0.15

        return min(conf, 1.0)

    def get_history(self, days: int = 30) -> List[MarketRegime]:
        if days <= 0:
            return self._history

        cutoff = now_shanghai() - pd.Timedelta(days=days)
        return [r for r in self._history if r.timestamp >= cutoff]

    def detect_changes(self) -> List[RegimeChange]:
        changes = []
        for i in range(1, len(self._history)):
            prev = self._history[i - 1]
            curr = self._history[i]
            if prev.name != curr.name:
                changes.append(
                    RegimeChange(
                        from_regime=prev.name,
                        to_regime=curr.name,
                        timestamp=curr.timestamp,
                        confidence=curr.confidence,
                    )
                )
        return changes

    def get_regime_adjustment(self, regime_name: str) -> float:
        return self.thresholds.regime.adjustments.get(regime_name, 1.0)
