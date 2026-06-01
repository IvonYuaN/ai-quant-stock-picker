from __future__ import annotations

from typing import Dict
import pandas as pd
import numpy as np

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds


class MomentumStrategy(BaseStrategy):
    name: str = "momentum"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        super().__init__(
            config,
            id="momentum",
            version=self.thresholds.version,
            hypothesis="动量效应在A股中短期有效，价格趋势和RSI信号能预测未来收益",
        )

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        scores = {}

        for symbol, df in data.items():
            if df is None or df.empty:
                scores[symbol] = 0.0
                continue

            score = self._calculate_single_score(df)
            scores[symbol] = score

        return scores

    def _calculate_single_score(self, df: pd.DataFrame) -> float:
        df = df.sort_values("date").tail(self.thresholds.momentum.lookback_days)

        if len(df) < 10:
            return 0.0

        prices = df["close"].values
        returns = np.diff(prices) / prices[:-1]

        total_return = (prices[-1] - prices[0]) / prices[0]
        volatility = np.std(returns) * np.sqrt(252)

        momentum_score = self._calculate_momentum_score(total_return, volatility)
        trend_score = self._calculate_trend_score(df)
        rsi_score = self._calculate_rsi_score(df)

        w = self.thresholds.momentum.weights
        final_score = (
            momentum_score * w.momentum + trend_score * w.trend + rsi_score * w.rsi
        )
        return max(0.0, min(1.0, final_score))

    def _calculate_momentum_score(
        self, total_return: float, volatility: float
    ) -> float:
        min_returns = self.thresholds.momentum.min_returns
        max_volatility = self.thresholds.momentum.max_volatility

        return_score = (
            max(0.0, min(total_return / min_returns, 1.0)) if min_returns > 0 else 0.5
        )
        vol_score = (
            max(1 - volatility / max_volatility, 0.0) if max_volatility > 0 else 0.5
        )

        return (return_score + vol_score) / 2

    def _calculate_trend_score(self, df: pd.DataFrame) -> float:
        ma_period = self.thresholds.momentum.ma_period
        threshold = self.thresholds.momentum.trend_strength_threshold

        if len(df) < ma_period:
            return 0.5

        df = df.copy()
        df["ma"] = df["close"].rolling(ma_period).mean()
        df["trend"] = (df["close"] - df["ma"]) / df["ma"]

        recent_trend = df["trend"].tail(5).mean()
        return max(0.0, min(recent_trend / threshold, 1.0)) if threshold > 0 else 0.5

    def _calculate_rsi_score(self, df: pd.DataFrame) -> float:
        rsi = self._calculate_rsi(df["close"])

        if rsi is None:
            return 0.5

        overbought = self.thresholds.momentum.rsi_overbought
        oversold = self.thresholds.momentum.rsi_oversold

        if rsi >= overbought:
            return 1.0
        elif rsi <= oversold:
            return 0.0
        else:
            return (rsi - oversold) / (overbought - oversold)

    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> float | None:
        if len(prices) < period + 1:
            return None

        deltas = prices.diff().dropna()
        gains = deltas.where(deltas > 0, 0)
        losses = -deltas.where(deltas < 0, 0)

        avg_gain = gains.rolling(period).mean().iloc[-1]
        avg_loss = losses.rolling(period).mean().iloc[-1]

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
