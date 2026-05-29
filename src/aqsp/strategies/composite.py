from __future__ import annotations

from typing import Dict, List
import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.momentum import MomentumStrategy
from aqsp.strategies.quality import QualityStrategy
from aqsp.strategies.value import ValueStrategy
from aqsp.strategies.volume import VolumeBreakoutStrategy
from aqsp.strategies.mean_reversion import MeanReversionStrategy
from aqsp.strategies.triple_rise import TripleRiseStrategy
from aqsp.strategies.thresholds import Thresholds, load_thresholds


class CompositeStrategy(BaseStrategy):
    name: str = "composite"

    def __init__(
        self,
        config: StrategyConfig | None = None,
        thresholds: Thresholds | None = None,
    ):
        self.thresholds = thresholds or load_thresholds()
        config = config or StrategyConfig(name="composite")
        super().__init__(
            config,
            id="composite",
            version=self.thresholds.version,
            hypothesis="多因子组合评分优于单因子，动量+成交量的加权综合能提高选股胜率",
        )

        self.momentum_strategy = MomentumStrategy(
            StrategyConfig(name="momentum"),
            thresholds=self.thresholds,
        )
        self.quality_strategy = QualityStrategy(
            StrategyConfig(name="quality", enabled=self.thresholds.quality.enabled),
            thresholds=self.thresholds,
        )
        self.value_strategy = ValueStrategy(
            StrategyConfig(name="value", enabled=self.thresholds.value.enabled),
            thresholds=self.thresholds,
        )
        self.volume_strategy = VolumeBreakoutStrategy(
            StrategyConfig(name="volume_breakout", enabled=self.thresholds.volume.enabled),
            thresholds=self.thresholds,
        )
        self.mean_reversion_strategy = MeanReversionStrategy(
            StrategyConfig(name="mean_reversion", enabled=self.thresholds.composite.mean_reversion_weight > 0),
            thresholds=self.thresholds,
        )
        self.triple_rise_strategy = TripleRiseStrategy(
            StrategyConfig(name="triple_rise", enabled=self.thresholds.composite.triple_rise_weight > 0),
            thresholds=self.thresholds,
        )

    def _has_mr(self) -> bool:
        return self.thresholds.composite.mean_reversion_weight > 0

    def _has_tr(self) -> bool:
        return self.thresholds.composite.triple_rise_weight > 0

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        momentum_scores = self.momentum_strategy.calculate_score(data)

        quality_scores: Dict[str, float] = {}
        if self.thresholds.quality.enabled:
            quality_scores = self.quality_strategy.calculate_score(data)

        value_scores: Dict[str, float] = {}
        if self.thresholds.value.enabled:
            value_scores = self.value_strategy.calculate_score(data)

        volume_scores: Dict[str, float] = {}
        if self.thresholds.volume.enabled:
            volume_scores = self.volume_strategy.calculate_score(data)

        mr_scores: Dict[str, float] = {}
        if self._has_mr():
            mr_scores = self.mean_reversion_strategy.calculate_score(data)

        tr_scores: Dict[str, float] = {}
        if self._has_tr():
            tr_scores = self.triple_rise_strategy.calculate_score(data)

        all_symbols = set(momentum_scores.keys())
        all_symbols |= set(quality_scores.keys())
        all_symbols |= set(value_scores.keys())
        all_symbols |= set(volume_scores.keys())
        all_symbols |= set(mr_scores.keys())
        all_symbols |= set(tr_scores.keys())

        weights = self.thresholds.composite
        final_scores = {}
        for symbol in all_symbols:
            total = 0.0
            w_sum = 0.0

            m = momentum_scores.get(symbol, 0.5)
            total += m * weights.momentum_weight
            w_sum += weights.momentum_weight

            if self.thresholds.quality.enabled:
                q = quality_scores.get(symbol, 0.5)
                total += q * weights.quality_weight
                w_sum += weights.quality_weight

            if self.thresholds.value.enabled:
                v = value_scores.get(symbol, 0.5)
                total += v * weights.value_weight
                w_sum += weights.value_weight

            if self.thresholds.volume.enabled:
                vol = volume_scores.get(symbol, 0.5)
                total += vol * weights.volume_weight
                w_sum += weights.volume_weight

            if self._has_mr():
                mr = mr_scores.get(symbol, 0.5)
                total += mr * weights.mean_reversion_weight
                w_sum += weights.mean_reversion_weight

            if self._has_tr():
                tr = tr_scores.get(symbol, 0.5)
                total += tr * weights.triple_rise_weight
                w_sum += weights.triple_rise_weight

            final_scores[symbol] = total / w_sum if w_sum > 0 else 0.0

        return final_scores

    def calculate_detailed_scores(
        self, data: Dict[str, pd.DataFrame]
    ) -> Dict[str, Dict[str, float]]:
        momentum_scores = self.momentum_strategy.calculate_score(data)

        quality_scores: Dict[str, float] = {}
        if self.thresholds.quality.enabled:
            quality_scores = self.quality_strategy.calculate_score(data)

        value_scores: Dict[str, float] = {}
        if self.thresholds.value.enabled:
            value_scores = self.value_strategy.calculate_score(data)

        volume_scores: Dict[str, float] = {}
        if self.thresholds.volume.enabled:
            volume_scores = self.volume_strategy.calculate_score(data)

        mr_scores: Dict[str, float] = {}
        if self._has_mr():
            mr_scores = self.mean_reversion_strategy.calculate_score(data)

        tr_scores: Dict[str, float] = {}
        if self._has_tr():
            tr_scores = self.triple_rise_strategy.calculate_score(data)

        all_symbols = set(momentum_scores.keys())
        all_symbols |= set(quality_scores.keys())
        all_symbols |= set(value_scores.keys())
        all_symbols |= set(volume_scores.keys())
        all_symbols |= set(mr_scores.keys())
        all_symbols |= set(tr_scores.keys())

        weights = self.thresholds.composite
        detailed = {}
        for symbol in all_symbols:
            m = momentum_scores.get(symbol, 0.5)
            entry: Dict[str, float] = {"momentum": m}
            total = m * weights.momentum_weight
            w_sum = weights.momentum_weight

            if self.thresholds.quality.enabled:
                q = quality_scores.get(symbol, 0.5)
                entry["quality"] = q
                total += q * weights.quality_weight
                w_sum += weights.quality_weight

            if self.thresholds.value.enabled:
                v = value_scores.get(symbol, 0.5)
                entry["value"] = v
                total += v * weights.value_weight
                w_sum += weights.value_weight

            if self.thresholds.volume.enabled:
                vol = volume_scores.get(symbol, 0.5)
                entry["volume"] = vol
                total += vol * weights.volume_weight
                w_sum += weights.volume_weight

            if self._has_mr():
                mr = mr_scores.get(symbol, 0.5)
                entry["mean_reversion"] = mr
                total += mr * weights.mean_reversion_weight
                w_sum += weights.mean_reversion_weight

            if self._has_tr():
                tr = tr_scores.get(symbol, 0.5)
                entry["triple_rise"] = tr
                total += tr * weights.triple_rise_weight
                w_sum += weights.triple_rise_weight

            entry["total"] = total / w_sum if w_sum > 0 else 0.0
            detailed[symbol] = entry

        return detailed

    def select_stocks(self, data: Dict[str, pd.DataFrame], n: int = 10) -> List[str]:
        scores = self.calculate_score(data)
        ranked = self.rank(scores, ascending=False)
        filtered = [
            s for s in ranked if scores[s] >= self.thresholds.composite.min_total_score
        ]
        return filtered[:n]
