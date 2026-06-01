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
        df = df.sort_values("date").tail(25)

        if len(df) < 20:
            return 0.0

        closes = df["close"].values
        volumes = df["volume"].values

        triple_rise_score = self._triple_rise(closes)
        v_bottom_score = self._v_bottom(closes, lookback=20)
        volume_confirm_score = self._volume_confirmation(volumes, closes)

        final = (
            triple_rise_score * 0.40
            + v_bottom_score * 0.35
            + volume_confirm_score * 0.25
        )
        return max(0.0, min(1.0, final))

    @staticmethod
    def _triple_rise(closes: np.ndarray) -> float:
        if len(closes) < 4:
            return 0.0
        last4 = closes[-4:]
        if last4[1] > last4[0] and last4[2] > last4[1] and last4[3] > last4[2]:
            avg_rise = (
                (last4[1] - last4[0]) / last4[0]
                + (last4[2] - last4[1]) / last4[1]
                + (last4[3] - last4[2]) / last4[2]
            ) / 3
            if avg_rise > 0.03:
                return 1.0
            elif avg_rise > 0.02:
                return 0.8
            elif avg_rise > 0.01:
                return 0.6
            else:
                return 0.4
        return 0.0

    @staticmethod
    def _v_bottom(closes: np.ndarray, lookback: int = 20) -> float:
        if len(closes) < lookback:
            return 0.0
        window = closes[-lookback:]
        min_idx = np.argmin(window)
        current = closes[-1]
        min_val = window[min_idx]
        if min_val <= 0:
            return 0.0
        recovery = (current - min_val) / min_val
        if min_idx <= 3:
            return 0.0
        if min_idx >= lookback - 3:
            return 0.0
        if recovery > 0.10:
            return 1.0
        elif recovery > 0.05:
            return 0.7
        elif recovery > 0.02:
            return 0.4
        else:
            return 0.1

    @staticmethod
    def _volume_confirmation(volumes: np.ndarray, closes: np.ndarray) -> float:
        if len(volumes) < 5 or len(closes) < 5:
            return 0.0
        recent_vol = np.mean(volumes[-3:])
        avg_vol = (
            np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes[:-3])
        )
        if avg_vol <= 0:
            return 0.0
        vol_ratio = recent_vol / avg_vol
        price_up = closes[-1] > closes[-4]
        if price_up and vol_ratio > 1.3:
            return 1.0
        elif price_up and vol_ratio > 1.0:
            return 0.6
        elif price_up:
            return 0.3
        else:
            return 0.0
