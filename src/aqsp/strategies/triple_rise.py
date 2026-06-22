from __future__ import annotations

from typing import Dict
import pandas as pd
import numpy as np

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds


class TripleRiseStrategy(BaseStrategy):
    name: str = "triple_rise"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        super().__init__(
            config,
            id="triple_rise",
            version=self.thresholds.version,
            hypothesis="三连涨+V型底确认是A股短期反转信号，配合分级止损可捕捉超跌反弹",
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
        config = self.thresholds.triple_rise
        if not config.enabled:
            return 0.0
        df = df.sort_values("date").tail(config.lookback_days)

        if len(df) < config.min_data_points:
            return 0.0

        closes = df["close"].values
        volumes = df["volume"].values

        triple_rise_score = self._triple_rise(
            closes,
            strong_threshold=config.avg_rise_strong,
            medium_threshold=config.avg_rise_medium,
            weak_threshold=config.avg_rise_weak,
            strong_score=config.avg_rise_strong_score,
            medium_score=config.avg_rise_medium_score,
            weak_score=config.avg_rise_weak_score,
            min_score=config.avg_rise_min_score,
        )
        v_bottom_score = self._v_bottom(
            closes,
            lookback=config.v_bottom_lookback,
            edge_days=config.v_bottom_edge_days,
            strong_recovery=config.v_bottom_strong_recovery,
            medium_recovery=config.v_bottom_medium_recovery,
            weak_recovery=config.v_bottom_weak_recovery,
            strong_score=config.v_bottom_strong_score,
            medium_score=config.v_bottom_medium_score,
            weak_score=config.v_bottom_weak_score,
            min_score=config.v_bottom_min_score,
        )
        volume_confirm_score = self._volume_confirmation(
            volumes,
            closes,
            recent_days=config.volume_recent_days,
            min_points=config.volume_min_points,
            avg_window=config.volume_avg_window,
            strong_ratio=config.volume_strong_ratio,
            medium_ratio=config.volume_medium_ratio,
            strong_score=config.volume_strong_score,
            medium_score=config.volume_medium_score,
            price_up_score=config.volume_price_up_score,
        )

        final = (
            triple_rise_score * config.weights.triple_rise
            + v_bottom_score * config.weights.v_bottom
            + volume_confirm_score * config.weights.volume_confirmation
        )
        return max(0.0, min(1.0, final))

    @staticmethod
    def _triple_rise(
        closes: np.ndarray,
        *,
        strong_threshold: float = 0.03,
        medium_threshold: float = 0.02,
        weak_threshold: float = 0.01,
        strong_score: float = 1.0,
        medium_score: float = 0.8,
        weak_score: float = 0.6,
        min_score: float = 0.4,
    ) -> float:
        if len(closes) < 4:
            return 0.0
        last4 = closes[-4:]
        if last4[1] > last4[0] and last4[2] > last4[1] and last4[3] > last4[2]:
            avg_rise = (
                (last4[1] - last4[0]) / last4[0]
                + (last4[2] - last4[1]) / last4[1]
                + (last4[3] - last4[2]) / last4[2]
            ) / 3
            if avg_rise > strong_threshold:
                return strong_score
            elif avg_rise > medium_threshold:
                return medium_score
            elif avg_rise > weak_threshold:
                return weak_score
            else:
                return min_score
        return 0.0

    @staticmethod
    def _v_bottom(
        closes: np.ndarray,
        lookback: int = 20,
        *,
        edge_days: int = 3,
        strong_recovery: float = 0.10,
        medium_recovery: float = 0.05,
        weak_recovery: float = 0.02,
        strong_score: float = 1.0,
        medium_score: float = 0.7,
        weak_score: float = 0.4,
        min_score: float = 0.1,
    ) -> float:
        if len(closes) < lookback:
            return 0.0
        window = closes[-lookback:]
        min_idx = np.argmin(window)
        current = closes[-1]
        min_val = window[min_idx]
        if min_val <= 0:
            return 0.0
        recovery = (current - min_val) / min_val
        if min_idx <= edge_days:
            return 0.0
        if min_idx >= lookback - edge_days:
            return 0.0
        if recovery > strong_recovery:
            return strong_score
        elif recovery > medium_recovery:
            return medium_score
        elif recovery > weak_recovery:
            return weak_score
        else:
            return min_score

    @staticmethod
    def _volume_confirmation(
        volumes: np.ndarray,
        closes: np.ndarray,
        *,
        recent_days: int = 3,
        min_points: int = 5,
        avg_window: int = 20,
        strong_ratio: float = 1.3,
        medium_ratio: float = 1.0,
        strong_score: float = 1.0,
        medium_score: float = 0.6,
        price_up_score: float = 0.3,
    ) -> float:
        if len(volumes) < min_points or len(closes) < min_points:
            return 0.0
        recent_vol = np.mean(volumes[-recent_days:])
        avg_vol = (
            np.mean(volumes[-avg_window:])
            if len(volumes) >= avg_window
            else np.mean(volumes[:-recent_days])
        )
        if avg_vol <= 0:
            return 0.0
        vol_ratio = recent_vol / avg_vol
        price_up = closes[-1] > closes[-4]
        if price_up and vol_ratio > strong_ratio:
            return strong_score
        elif price_up and vol_ratio > medium_ratio:
            return medium_score
        elif price_up:
            return price_up_score
        else:
            return 0.0
