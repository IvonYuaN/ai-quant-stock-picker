from __future__ import annotations

import math
from typing import Dict, List

import pandas as pd

from aqsp.regime.strategy_mixer import canonicalize_regime
from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.momentum import MomentumStrategy
from aqsp.strategies.quality import QualityStrategy
from aqsp.strategies.value import ValueStrategy
from aqsp.strategies.volume import VolumeBreakoutStrategy
from aqsp.strategies.mean_reversion import MeanReversionStrategy
from aqsp.strategies.triple_rise import TripleRiseStrategy
from aqsp.strategies.family_v2 import (
    HighTightFlagStrategy,
    LowVolatilityStrategy,
    PullbackContinuationStrategy,
)
from aqsp.strategies.thresholds import Thresholds, load_thresholds


def _finite(value: float, default: float) -> float:
    """Return ``value`` when it is a finite number, else ``default``.

    Factor strategies legitimately return ``nan`` for symbols that lack the
    required history (e.g. :class:`LowVolatilityStrategy` needs
    ``vol_window + 1`` bars). Left unguarded, ``nan`` reaches
    ``min(1.0, nan)`` which evaluates to **1.0** under CPython comparison
    semantics — silently promoting data-poor symbols to the top of the
    ranking. Guard every factor contribution with this helper.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


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
            StrategyConfig(name="quality", enabled=self._has_quality()),
            thresholds=self.thresholds,
        )
        self.value_strategy = ValueStrategy(
            StrategyConfig(name="value", enabled=self._has_value()),
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
                enabled=self._has_mr(),
            ),
            thresholds=self.thresholds,
        )
        self.triple_rise_strategy = TripleRiseStrategy(
            StrategyConfig(
                name="triple_rise",
                enabled=self._has_tr(),
            ),
            thresholds=self.thresholds,
        )

        # v2 因子族（反转/低波动/强势后收敛）——仅当权重 > 0 时实例化并参与合成。
        self.high_tight_flag_strategy = HighTightFlagStrategy(
            StrategyConfig(name="high_tight_flag", enabled=self._has_htf()),
            thresholds=self.thresholds,
        )
        self.low_vol_strategy = LowVolatilityStrategy(
            StrategyConfig(name="low_volatility", enabled=self._has_lowvol()),
            thresholds=self.thresholds,
        )
        self.pullback_strategy = PullbackContinuationStrategy(
            StrategyConfig(name="pullback_continuation", enabled=self._has_pullback()),
            thresholds=self.thresholds,
        )

    def _has_htf(self) -> bool:
        return self.thresholds.composite.high_tight_flag_weight > 0

    def _has_lowvol(self) -> bool:
        return self.thresholds.composite.low_vol_weight > 0

    def _has_pullback(self) -> bool:
        return self.thresholds.composite.pullback_weight > 0

    def _has_volume(self) -> bool:
        return (
            self.thresholds.volume.enabled
            and self.thresholds.composite.volume_weight > 0
        )

    def _has_quality(self) -> bool:
        return (
            self.thresholds.quality.enabled
            and self.thresholds.composite.quality_weight > 0
        )

    def _has_value(self) -> bool:
        return (
            self.thresholds.value.enabled and self.thresholds.composite.value_weight > 0
        )

    def _has_mr(self) -> bool:
        return (
            self.thresholds.mean_reversion.enabled
            and self.thresholds.composite.mean_reversion_weight > 0
        )

    def _has_tr(self) -> bool:
        return (
            self.thresholds.triple_rise.enabled
            and self.thresholds.composite.triple_rise_weight > 0
        )

    def get_regime_adjusted_weights(
        self, regime: str
    ) -> tuple[float, float, float, float, float, float, float, float, float]:
        """根据市场状态调整策略权重。

        返回 9 元组：前 6 个为旧因子族（受 regime 调整），后 3 个为 v2 因子族
        （high_tight_flag / low_vol / pullback —— 按因子族轮换使用，**不做 regime 调整**：
        数据已证 regime 条件化救不了反向的旧族，v2 的方向是换族而非换状态）。

        兼容性：历史调用方按位置索引 [0]..[5] 取值，故 v2 权重追加在末尾不破坏契约。
        """
        base = self.thresholds.composite
        canonical_regime = canonicalize_regime(regime)
        adjustment = self.thresholds.regime.strategy_weights.get(canonical_regime)
        if adjustment is None:
            legacy_regime = {
                "aggressive_bull": "stable_bull",
                "volatile_bull": "volatile_bull",
                "defensive_bear": "volatile_bear",
                "rotation_sideways": "stable_sideways",
            }.get(canonical_regime)
            if legacy_regime:
                adjustment = self.thresholds.regime.strategy_weights.get(legacy_regime)

        v2_tail = (
            base.high_tight_flag_weight,
            base.low_vol_weight,
            base.pullback_weight,
        )

        if adjustment is None:
            return (
                base.momentum_weight,
                base.quality_weight,
                base.value_weight,
                base.volume_weight,
                base.mean_reversion_weight,
                base.triple_rise_weight,
            ) + v2_tail

        def blended(multiplier: float) -> float:
            return base.base_blend_weight + base.regime_blend_weight * multiplier

        return (
            base.momentum_weight * blended(adjustment.momentum),
            base.quality_weight * blended(adjustment.quality),
            base.value_weight * blended(adjustment.value),
            base.volume_weight * blended(adjustment.volume),
            base.mean_reversion_weight * blended(adjustment.mean_reversion),
            base.triple_rise_weight * blended(adjustment.triple_rise),
        ) + v2_tail

    def calculate_score(
        self, data: Dict[str, pd.DataFrame], regime: str = "unknown"
    ) -> Dict[str, float]:
        # 权重先解析：momentum 权重为 0 的变体（v2 三段式）无需计算 momentum ——
        # 原实现无条件计算后整体乘 0，属纯浪费（gate 时间受限时影响可观）。
        mw, qw, vw, volw, mrw, trw, htfw, lvw, pbw = (
            self.get_regime_adjusted_weights(regime)
        )

        momentum_scores: Dict[str, float] = (
            self.momentum_strategy.calculate_score(data) if mw > 0 else {}
        )

        quality_scores: Dict[str, float] = {}
        if self._has_quality():
            quality_scores = self.quality_strategy.calculate_score(data)

        value_scores: Dict[str, float] = {}
        if self._has_value():
            value_scores = self.value_strategy.calculate_score(data)

        volume_scores: Dict[str, float] = {}
        if self._has_volume():
            volume_scores = self.volume_strategy.calculate_score(data)

        mr_scores: Dict[str, float] = {}
        if self._has_mr():
            mr_scores = self.mean_reversion_strategy.calculate_score(data)

        tr_scores: Dict[str, float] = {}
        if self._has_tr():
            tr_scores = self.triple_rise_strategy.calculate_score(data)

        # v2 因子族（仅在权重 > 0 时计算）
        htf_scores: Dict[str, float] = {}
        if self._has_htf():
            htf_scores = self.high_tight_flag_strategy.calculate_score(data)
        lv_scores: Dict[str, float] = {}
        if self._has_lowvol():
            lv_scores = self.low_vol_strategy.calculate_score(data)
        pb_scores: Dict[str, float] = {}
        if self._has_pullback():
            pb_scores = self.pullback_strategy.calculate_score(data)

        all_symbols = set(momentum_scores.keys())
        all_symbols |= set(quality_scores.keys())
        all_symbols |= set(value_scores.keys())
        all_symbols |= set(volume_scores.keys())
        all_symbols |= set(mr_scores.keys())
        all_symbols |= set(tr_scores.keys())
        all_symbols |= set(htf_scores.keys())
        all_symbols |= set(lv_scores.keys())
        all_symbols |= set(pb_scores.keys())

        final_scores = {}
        for symbol in all_symbols:
            total = 0.0
            w_sum = 0.0

            m = _finite(momentum_scores.get(symbol), 0.5)
            total += m * mw
            w_sum += mw

            if self._has_quality():
                q = _finite(quality_scores.get(symbol), 0.5)
                total += q * qw
                w_sum += qw

            if self._has_value():
                v = _finite(value_scores.get(symbol), 0.5)
                total += v * vw
                w_sum += vw

            if self._has_volume():
                vol = _finite(volume_scores.get(symbol), 0.5)
                total += vol * volw
                w_sum += volw

            if self._has_mr():
                mr = _finite(mr_scores.get(symbol), 0.5)
                total += mr * mrw
                w_sum += mrw

            if self._has_tr():
                tr = _finite(tr_scores.get(symbol), 0.5)
                total += tr * trw
                w_sum += trw

            if self._has_htf():
                total += _finite(htf_scores.get(symbol), 0.0) * htfw
                w_sum += htfw

            if self._has_lowvol():
                total += _finite(lv_scores.get(symbol), 0.0) * lvw
                w_sum += lvw

            if self._has_pullback():
                total += _finite(pb_scores.get(symbol), 0.0) * pbw
                w_sum += pbw

            base_score = total / w_sum if w_sum > 0 else 0.0
            final_scores[symbol] = max(0.0, min(1.0, base_score))

        return final_scores

    def calculate_detailed_scores(
        self, data: Dict[str, pd.DataFrame], regime: str = "unknown"
    ) -> Dict[str, Dict[str, float]]:
        # 与 calculate_score 同口径：mw=0 时跳过 momentum 计算。
        mw, qw, vw, volw, mrw, trw, htfw, lvw, pbw = (
            self.get_regime_adjusted_weights(regime)
        )

        momentum_scores: Dict[str, float] = (
            self.momentum_strategy.calculate_score(data) if mw > 0 else {}
        )

        quality_scores: Dict[str, float] = {}
        if self._has_quality():
            quality_scores = self.quality_strategy.calculate_score(data)

        value_scores: Dict[str, float] = {}
        if self._has_value():
            value_scores = self.value_strategy.calculate_score(data)

        volume_scores: Dict[str, float] = {}
        if self._has_volume():
            volume_scores = self.volume_strategy.calculate_score(data)

        mr_scores: Dict[str, float] = {}
        if self._has_mr():
            mr_scores = self.mean_reversion_strategy.calculate_score(data)

        tr_scores: Dict[str, float] = {}
        if self._has_tr():
            tr_scores = self.triple_rise_strategy.calculate_score(data)

        # v2 因子族（仅在权重 > 0 时计算）
        htf_scores: Dict[str, float] = {}
        if self._has_htf():
            htf_scores = self.high_tight_flag_strategy.calculate_score(data)
        lv_scores: Dict[str, float] = {}
        if self._has_lowvol():
            lv_scores = self.low_vol_strategy.calculate_score(data)
        pb_scores: Dict[str, float] = {}
        if self._has_pullback():
            pb_scores = self.pullback_strategy.calculate_score(data)

        all_symbols = set(momentum_scores.keys())
        all_symbols |= set(quality_scores.keys())
        all_symbols |= set(value_scores.keys())
        all_symbols |= set(volume_scores.keys())
        all_symbols |= set(mr_scores.keys())
        all_symbols |= set(tr_scores.keys())
        all_symbols |= set(htf_scores.keys())
        all_symbols |= set(lv_scores.keys())
        all_symbols |= set(pb_scores.keys())

        detailed = {}
        for symbol in all_symbols:
            m = _finite(momentum_scores.get(symbol), 0.5)
            entry: Dict[str, float] = {"momentum": m}
            total = m * mw
            w_sum = mw

            if self._has_quality():
                q = _finite(quality_scores.get(symbol), 0.5)
                entry["quality"] = q
                total += q * qw
                w_sum += qw

            if self._has_value():
                v = _finite(value_scores.get(symbol), 0.5)
                entry["value"] = v
                total += v * vw
                w_sum += vw

            if self._has_volume():
                vol = _finite(volume_scores.get(symbol), 0.5)
                entry["volume"] = vol
                total += vol * volw
                w_sum += volw

            if self._has_mr():
                mr = _finite(mr_scores.get(symbol), 0.5)
                entry["mean_reversion"] = mr
                total += mr * mrw
                w_sum += mrw

            if self._has_tr():
                tr = _finite(tr_scores.get(symbol), 0.5)
                entry["triple_rise"] = tr
                total += tr * trw
                w_sum += trw

            if self._has_htf():
                htf = _finite(htf_scores.get(symbol), 0.0)
                entry["high_tight_flag"] = htf
                total += htf * htfw
                w_sum += htfw

            if self._has_lowvol():
                lv = _finite(lv_scores.get(symbol), 0.0)
                entry["low_volatility"] = lv
                total += lv * lvw
                w_sum += lvw

            if self._has_pullback():
                pb = _finite(pb_scores.get(symbol), 0.0)
                entry["pullback_continuation"] = pb
                total += pb * pbw
                w_sum += pbw

            base_total = total / w_sum if w_sum > 0 else 0.0
            entry["total"] = max(0.0, min(1.0, base_total))
            detailed[symbol] = entry

        return detailed

    def select_stocks(
        self, data: Dict[str, pd.DataFrame], n: int = 10, regime: str = "unknown"
    ) -> List[str]:
        scores = self.calculate_score(data, regime=regime)
        ranked = self.rank(scores, ascending=False)
        filtered = [
            s for s in ranked if scores[s] >= self.thresholds.composite.min_total_score
        ]
        return filtered[:n]
