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
            StrategyConfig(
                name="volume_breakout", enabled=self.thresholds.volume.enabled
            ),
            thresholds=self.thresholds,
        )
        self.mean_reversion_strategy = MeanReversionStrategy(
            StrategyConfig(
                name="mean_reversion",
                enabled=self.thresholds.composite.mean_reversion_weight > 0,
            ),
            thresholds=self.thresholds,
        )
        self.triple_rise_strategy = TripleRiseStrategy(
            StrategyConfig(
                name="triple_rise",
                enabled=self.thresholds.composite.triple_rise_weight > 0,
            ),
            thresholds=self.thresholds,
        )

    def _has_mr(self) -> bool:
        return self.thresholds.composite.mean_reversion_weight > 0

    def _has_tr(self) -> bool:
        return self.thresholds.composite.triple_rise_weight > 0

    def get_regime_adjusted_weights(
        self, regime: str
    ) -> tuple[float, float, float, float, float, float]:
        """根据市场状态调整策略权重"""
        base = self.thresholds.composite
        adjustment = self.thresholds.regime.strategy_weights.get(regime)

        if adjustment is None:
            return (
                base.momentum_weight,
                base.quality_weight,
                base.value_weight,
                base.volume_weight,
                base.mean_reversion_weight,
                base.triple_rise_weight,
            )
        return (
            base.momentum_weight * adjustment.momentum,
            base.quality_weight * adjustment.quality,
            base.value_weight * adjustment.value,
            base.volume_weight * adjustment.volume,
            base.mean_reversion_weight * adjustment.mean_reversion,
            base.triple_rise_weight * adjustment.triple_rise,
        )

    def _regime_score_multiplier(self, regime: str) -> float:
        if not regime:
            return 1.0
        return float(self.thresholds.regime.adjustments.get(regime, 1.0))

    def calculate_score(
        self, data: Dict[str, pd.DataFrame], regime: str = "unknown"
    ) -> Dict[str, float]:
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

        # 使用市场状态调整后的权重
        mw, qw, vw, volw, mrw, trw = self.get_regime_adjusted_weights(regime)

        final_scores = {}
        for symbol in all_symbols:
            total = 0.0
            w_sum = 0.0

            m = momentum_scores.get(symbol, 0.5)
            total += m * mw
            w_sum += mw

            if self.thresholds.quality.enabled:
                q = quality_scores.get(symbol, 0.5)
                total += q * qw
                w_sum += qw

            if self.thresholds.value.enabled:
                v = value_scores.get(symbol, 0.5)
                total += v * vw
                w_sum += vw

            if self.thresholds.volume.enabled:
                vol = volume_scores.get(symbol, 0.5)
                total += vol * volw
                w_sum += volw

            if self._has_mr():
                mr = mr_scores.get(symbol, 0.5)
                total += mr * mrw
                w_sum += mrw

            if self._has_tr():
                tr = tr_scores.get(symbol, 0.5)
                total += tr * trw
                w_sum += trw

            base_score = total / w_sum if w_sum > 0 else 0.0
            final_scores[symbol] = max(
                0.0, min(1.0, base_score * self._regime_score_multiplier(regime))
            )

        return final_scores

    def calculate_detailed_scores(
        self, data: Dict[str, pd.DataFrame], regime: str = "unknown"
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

        # 使用市场状态调整后的权重
        mw, qw, vw, volw, mrw, trw = self.get_regime_adjusted_weights(regime)

        detailed = {}
        for symbol in all_symbols:
            m = momentum_scores.get(symbol, 0.5)
            entry: Dict[str, float] = {"momentum": m}
            total = m * mw
            w_sum = mw

            if self.thresholds.quality.enabled:
                q = quality_scores.get(symbol, 0.5)
                entry["quality"] = q
                total += q * qw
                w_sum += qw

            if self.thresholds.value.enabled:
                v = value_scores.get(symbol, 0.5)
                entry["value"] = v
                total += v * vw
                w_sum += vw

            if self.thresholds.volume.enabled:
                vol = volume_scores.get(symbol, 0.5)
                entry["volume"] = vol
                total += vol * volw
                w_sum += volw

            if self._has_mr():
                mr = mr_scores.get(symbol, 0.5)
                entry["mean_reversion"] = mr
                total += mr * mrw
                w_sum += mrw

            if self._has_tr():
                tr = tr_scores.get(symbol, 0.5)
                entry["triple_rise"] = tr
                total += tr * trw
                w_sum += trw

            base_total = total / w_sum if w_sum > 0 else 0.0
            entry["regime_multiplier"] = self._regime_score_multiplier(regime)
            entry["total"] = max(0.0, min(1.0, base_total * entry["regime_multiplier"]))
            detailed[symbol] = entry

        return detailed

    def select_stocks(self, data: Dict[str, pd.DataFrame], n: int = 10) -> List[str]:
        scores = self.calculate_score(data)
        ranked = self.rank(scores, ascending=False)
        filtered = [
            s for s in ranked if scores[s] >= self.thresholds.composite.min_total_score
        ]
        return filtered[:n]
