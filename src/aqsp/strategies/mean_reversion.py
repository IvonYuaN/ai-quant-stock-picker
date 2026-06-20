from __future__ import annotations

from typing import Dict
import pandas as pd
import numpy as np

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds


class MeanReversionStrategy(BaseStrategy):
    name: str = "mean_reversion"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        super().__init__(
            config,
            id="mean_reversion",
            version=self.thresholds.version,
            hypothesis="A股大盘股存在均值回归效应，超跌后短期反弹概率高于随机",
        )

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        scores = {}
        for symbol, df in data.items():
            if df is None or df.empty:
                scores[symbol] = 0.0
                continue
            scores[symbol] = self._calculate_single_score(df)
        return scores

    def _calculate_single_score(self, df: pd.DataFrame) -> float:
        config = self.thresholds.mean_reversion
        if not config.enabled:
            return 0.0
        lookback = config.lookback_days
        df = df.sort_values("date").tail(lookback + 5)

        if len(df) < lookback:
            return 0.0

        closes = df["close"].values
        volumes = df["volume"].values

        rsi_score = self._rsi_oversold(
            closes,
            period=config.rsi_period,
            strong_threshold=config.strong_oversold_threshold,
            oversold_threshold=config.oversold_threshold,
            weak_threshold=config.weak_oversold_threshold,
        )
        deviation_score = self._price_deviation(
            closes,
            ma_period=lookback,
            deep_threshold=config.deep_deviation_threshold,
            medium_threshold=config.medium_deviation_threshold,
            deviation_threshold=config.deviation_threshold,
            shallow_threshold=config.shallow_deviation_threshold,
        )
        volume_confirm_score = self._volume_confirmation(
            volumes,
            closes,
            lookback,
            strong_ratio=config.volume_strong_ratio,
            medium_ratio=config.volume_medium_ratio,
        )

        final = (
            rsi_score * config.rsi_weight
            + deviation_score * config.deviation_weight
            + volume_confirm_score * config.volume_weight
        )
        return max(0.0, min(1.0, final))

    @staticmethod
    def _rsi_oversold(
        closes: np.ndarray,
        period: int = 14,
        *,
        strong_threshold: float = 20,
        oversold_threshold: float = 30,
        weak_threshold: float = 40,
    ) -> float:
        if len(closes) < period + 1:
            return 0.0
        deltas = np.diff(closes[-(period + 1) :])
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 0.0
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        if rsi <= strong_threshold:
            return 1.0
        elif rsi <= oversold_threshold:
            return (oversold_threshold - rsi) / (oversold_threshold - strong_threshold)
        elif rsi <= weak_threshold:
            return (
                (weak_threshold - rsi)
                / ((weak_threshold - oversold_threshold) * 2)
                * 0.3
            )
        else:
            return 0.0

    @staticmethod
    def _price_deviation(
        closes: np.ndarray,
        ma_period: int,
        *,
        deep_threshold: float = -0.15,
        medium_threshold: float = -0.10,
        deviation_threshold: float = -0.05,
        shallow_threshold: float = -0.02,
    ) -> float:
        if len(closes) < ma_period:
            return 0.0
        ma = np.mean(closes[-ma_period:])
        if ma <= 0:
            return 0.0
        current = closes[-1]
        deviation = (current - ma) / ma
        if deviation <= deep_threshold:
            return 1.0
        elif deviation <= medium_threshold:
            return 0.8
        elif deviation <= deviation_threshold:
            return 0.5
        elif deviation <= shallow_threshold:
            return 0.2
        else:
            return 0.0

    @staticmethod
    def _volume_confirmation(
        volumes: np.ndarray,
        closes: np.ndarray,
        window: int,
        *,
        strong_ratio: float = 1.5,
        medium_ratio: float = 1.2,
    ) -> float:
        if len(volumes) < window or len(closes) < window:
            return 0.0
        recent_vol = volumes[-1]
        avg_vol = (
            np.mean(volumes[-(window + 1) : -1])
            if len(volumes) > window
            else np.mean(volumes[:-1])
        )
        if avg_vol <= 0:
            return 0.0
        vol_ratio = recent_vol / avg_vol
        price_down = closes[-1] < closes[-2] if len(closes) >= 2 else False
        if price_down and vol_ratio > strong_ratio:
            return 1.0
        elif price_down and vol_ratio > medium_ratio:
            return 0.6
        elif price_down:
            return 0.3
        else:
            return 0.0
