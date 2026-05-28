from __future__ import annotations

from typing import Dict
import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds


class ValueStrategy(BaseStrategy):
    name: str = "value"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        super().__init__(
            config,
            id="value",
            version=self.thresholds.version,
            hypothesis="低估值股票在A股长期有超额收益，PE/PB/股息率是有效筛选因子（基本面数据接入后启用）",
            regime_required=("stable_bull", "stable_sideways", "stable_bear"),
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
        df = df.sort_values("date").tail(5)

        if len(df) < 1:
            return 0.0

        pe_score = self._calculate_pe_score(df)
        pb_score = self._calculate_pb_score(df)
        dividend_score = self._calculate_dividend_score(df)

        w = self.thresholds.value.weights
        final_score = pe_score * w.pe + pb_score * w.pb + dividend_score * w.dividend
        return max(0.0, min(1.0, final_score))

    def _calculate_pe_score(self, df: pd.DataFrame) -> float:
        max_pe = self.thresholds.value.max_pe

        if "pe" not in df.columns:
            return 0.5

        pe_values = df["pe"].dropna()
        if pe_values.empty:
            return 0.5

        latest_pe = pe_values.iloc[-1]

        if latest_pe <= 0:
            return 0.3

        score = max(1 - latest_pe / max_pe, 0.0) if max_pe > 0 else 0.5
        return score

    def _calculate_pb_score(self, df: pd.DataFrame) -> float:
        max_pb = self.thresholds.value.max_pb

        if "pb" not in df.columns:
            return 0.5

        pb_values = df["pb"].dropna()
        if pb_values.empty:
            return 0.5

        latest_pb = pb_values.iloc[-1]

        if latest_pb <= 0:
            return 0.3

        score = max(1 - latest_pb / max_pb, 0.0) if max_pb > 0 else 0.5
        return score

    def _calculate_dividend_score(self, df: pd.DataFrame) -> float:
        min_yield = self.thresholds.value.min_dividend_yield

        if "dividend_yield" not in df.columns:
            return 0.5

        yield_values = df["dividend_yield"].dropna()
        if yield_values.empty:
            return 0.5

        avg_yield = yield_values.mean()
        return min(avg_yield / min_yield, 1.0) if min_yield > 0 else 0.5
