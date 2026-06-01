from __future__ import annotations

from typing import Dict
import pandas as pd
import numpy as np

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds


class VolumeBreakoutStrategy(BaseStrategy):
    name: str = "volume_breakout"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        super().__init__(
            config,
            id="volume_breakout",
            version=self.thresholds.version,
            hypothesis="放量突破是A股有效的短期信号，成交量相对均值的突破配合价格新高能预测后续收益",
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
        cfg = self.thresholds.volume
        df = df.sort_values("date").tail(cfg.lookback_days)

        if len(df) < cfg.volume_ma_period + 5:
            return 0.0

        closes = df["close"].values
        volumes = df["volume"].values

        vol_surge_score = self._volume_surge(
            volumes, cfg.volume_ma_period, cfg.surge_multiplier
        )
        price_breakout_score = self._price_breakout(closes, cfg.price_ma_period)
        vol_price_corr_score = self._volume_price_correlation(
            closes, volumes, cfg.correlation_window
        )

        w = cfg.weights
        final = (
            vol_surge_score * w.surge
            + price_breakout_score * w.breakout
            + vol_price_corr_score * w.correlation
        )
        return max(0.0, min(1.0, final))

    @staticmethod
    def _volume_surge(
        volumes: np.ndarray, ma_period: int, surge_multiplier: float
    ) -> float:
        if len(volumes) < ma_period + 1:
            return 0.0
        vol_ma = np.mean(volumes[-(ma_period + 1) : -1])
        if vol_ma <= 0:
            return 0.0
        current_vol = volumes[-1]
        ratio = current_vol / vol_ma
        if ratio >= surge_multiplier * 2:
            return 1.0
        elif ratio >= surge_multiplier:
            return (ratio - surge_multiplier) / surge_multiplier
        else:
            return max(0.0, ratio / surge_multiplier - 0.5) * 2

    @staticmethod
    def _price_breakout(closes: np.ndarray, ma_period: int) -> float:
        if len(closes) < ma_period:
            return 0.0
        ma = np.mean(closes[-ma_period:])
        if ma <= 0:
            return 0.0
        current = closes[-1]
        high_20 = np.max(closes[-min(ma_period, len(closes)) :])
        if current >= high_20 and current > ma:
            return 1.0
        elif current > ma:
            return 0.5
        else:
            return 0.0

    @staticmethod
    def _volume_price_correlation(
        closes: np.ndarray, volumes: np.ndarray, window: int
    ) -> float:
        if len(closes) < window + 1:
            return 0.0
        price_changes = np.diff(closes[-(window + 1) :])
        vol_changes = volumes[-(window + 1) : -1]
        if len(price_changes) < 3:
            return 0.0
        mask = (vol_changes > 0) & np.isfinite(price_changes) & np.isfinite(vol_changes)
        if mask.sum() < 3:
            return 0.0
        p = price_changes[mask]
        v = vol_changes[mask]
        if np.std(p) == 0 or np.std(v) == 0:
            return 0.5
        corr = np.corrcoef(p, v)[0, 1]
        if np.isnan(corr):
            return 0.5
        return max(0.0, min(1.0, (corr + 1) / 2))
