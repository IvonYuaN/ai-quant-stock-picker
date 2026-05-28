from __future__ import annotations

from typing import Dict
import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds


class QualityStrategy(BaseStrategy):
    name: str = "quality"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        super().__init__(
            config,
            id="quality",
            version=self.thresholds.version,
            hypothesis="高ROE、低负债、稳定利润率的公司长期跑赢市场（基本面数据接入后启用）",
            regime_required=("stable_bull", "stable_sideways"),
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
        df = df.sort_values("date").tail(20)

        if len(df) < 5:
            return 0.0

        roe_score = self._calculate_roe_score(df)
        roa_score = self._calculate_roa_score(df)
        debt_score = self._calculate_debt_score(df)
        margin_score = self._calculate_margin_score(df)

        w = self.thresholds.quality.weights
        final_score = (
            roe_score * w.roe
            + roa_score * w.roa
            + debt_score * w.debt
            + margin_score * w.margin
        )
        return max(0.0, min(1.0, final_score))

    def _calculate_roe_score(self, df: pd.DataFrame) -> float:
        min_roe = self.thresholds.quality.min_roe

        if "roe" not in df.columns:
            return 0.5

        roe_values = df["roe"].dropna()
        if roe_values.empty:
            return 0.5

        avg_roe = roe_values.mean()
        return min(avg_roe / min_roe, 1.0) if min_roe > 0 else 0.5

    def _calculate_roa_score(self, df: pd.DataFrame) -> float:
        min_roa = self.thresholds.quality.min_roa

        if "roa" not in df.columns:
            return 0.5

        roa_values = df["roa"].dropna()
        if roa_values.empty:
            return 0.5

        avg_roa = roa_values.mean()
        return min(avg_roa / min_roa, 1.0) if min_roa > 0 else 0.5

    def _calculate_debt_score(self, df: pd.DataFrame) -> float:
        max_debt = self.thresholds.quality.max_debt_ratio

        if "debt_ratio" not in df.columns:
            return 0.5

        debt_values = df["debt_ratio"].dropna()
        if debt_values.empty:
            return 0.5

        avg_debt = debt_values.mean()
        return max(1 - avg_debt / max_debt, 0.0) if max_debt > 0 else 0.5

    def _calculate_margin_score(self, df: pd.DataFrame) -> float:
        threshold = self.thresholds.quality.operating_margin_threshold

        if "operating_margin" not in df.columns:
            return 0.5

        margin_values = df["operating_margin"].dropna()
        if margin_values.empty:
            return 0.5

        avg_margin = margin_values.mean()
        return min(avg_margin / threshold, 1.0) if threshold > 0 else 0.5
