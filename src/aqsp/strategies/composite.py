from __future__ import annotations

from typing import Dict, List
import pandas as pd

from aqsp.regime.strategy_mixer import canonicalize_regime
from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.momentum import MomentumStrategy
from aqsp.strategies.quality import QualityStrategy
from aqsp.strategies.value import ValueStrategy
from aqsp.strategies.price_volume_corr import PriceVolumeLevelCorrelation
from aqsp.strategies.volume_surge import VolumeSurge
from aqsp.strategies.volatility_position import VolatilityPositionStrategy
from aqsp.strategies.macd_hist import MACDHistStrategy
from aqsp.strategies.lower_shadow_ratio import LowerShadowRatioStrategy
from aqsp.strategies.amount_ratio import AmountRatioStrategy
from aqsp.strategies.volume import VolumeBreakoutStrategy
from aqsp.strategies.mean_reversion import MeanReversionStrategy
from aqsp.strategies.triple_rise import TripleRiseStrategy
from aqsp.strategies.candidates import HighTightFlagCandidate
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
            StrategyConfig(name="quality", enabled=self._has_quality()),
            thresholds=self.thresholds,
        )
        self.value_strategy = ValueStrategy(
            StrategyConfig(name="value", enabled=self._has_value()),
            thresholds=self.thresholds,
        )
        self.price_volume_corr_strategy = PriceVolumeLevelCorrelation(
            StrategyConfig(name="price_volume_corr", enabled=True), self.thresholds
        )
        self.volume_surge_strategy = VolumeSurge(
            StrategyConfig(name="volume_surge", enabled=True), self.thresholds
        )
        self.volatility_position_strategy = VolatilityPositionStrategy(
            StrategyConfig(
                name="volatility_position",
                enabled=self._has_volatility_position(),
            ),
            thresholds=self.thresholds,
        )
        self.macd_hist_strategy = MACDHistStrategy(
            StrategyConfig(
                name="macd_hist",
                enabled=self._has_macd_hist(),
            ),
            thresholds=self.thresholds,
        )
        self.lower_shadow_ratio_strategy = LowerShadowRatioStrategy(
            StrategyConfig(
                name="lower_shadow_ratio",
                enabled=self._has_lower_shadow_ratio(),
            ),
            thresholds=self.thresholds,
        )
        self.amount_ratio_strategy = AmountRatioStrategy(
            StrategyConfig(
                name="amount_ratio",
                enabled=self._has_amount_ratio(),
            ),
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
        self.htf_strategy = HighTightFlagCandidate(
            StrategyConfig(
                name="high_tight_flag",
                enabled=self._has_htf(),
            ),
            thresholds=self.thresholds,
        )

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

    def _has_volume_surge(self) -> bool:
        return (
            self.thresholds.volume_surge.enabled
            and self.thresholds.composite.volume_surge_weight > 0
        )

    def _has_volatility_position(self) -> bool:
        return (
            self.thresholds.volatility_position.enabled
            and self.thresholds.composite.volatility_position_weight > 0
        )

    def _has_macd_hist(self) -> bool:
        return (
            self.thresholds.macd_hist.enabled
            and self.thresholds.composite.macd_hist_weight > 0
        )

    def _has_lower_shadow_ratio(self) -> bool:
        return (
            self.thresholds.lower_shadow_ratio.enabled
            and self.thresholds.composite.lower_shadow_ratio_weight > 0
        )

    def _has_amount_ratio(self) -> bool:
        return (
            self.thresholds.amount_ratio.enabled
            and self.thresholds.composite.amount_ratio_weight > 0
        )

    def _has_price_volume_corr(self) -> bool:
        return (
            self.thresholds.price_volume_corr.enabled
            and self.thresholds.composite.price_volume_corr_weight > 0
        )

    def _has_htf(self) -> bool:
        return (
            self.thresholds.high_tight_flag.enabled
            and self.thresholds.composite.high_tight_flag_weight > 0
        )

    def get_regime_adjusted_weights(
        self, regime: str
    ) -> tuple[float, float, float, float, float, float, float]:
        """根据市场状态调整策略权重（7 因子：mom/quality/value/vol/mr/tr/htf）"""
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

        if adjustment is None:
            return (
                base.momentum_weight,
                base.quality_weight,
                base.value_weight,
                base.volume_weight,
                base.mean_reversion_weight,
                base.triple_rise_weight,
                base.high_tight_flag_weight,
            )

        def blended(multiplier: float) -> float:
            return base.base_blend_weight + base.regime_blend_weight * multiplier

        return (
            base.momentum_weight * blended(adjustment.momentum),
            base.quality_weight * blended(adjustment.quality),
            base.value_weight * blended(adjustment.value),
            base.volume_weight * blended(adjustment.volume),
            base.mean_reversion_weight * blended(adjustment.mean_reversion),
            base.triple_rise_weight * blended(adjustment.triple_rise),
            base.high_tight_flag_weight * blended(adjustment.high_tight_flag),
        )

    def volume_surge_weight_for(self, regime: str) -> float:
        """量能冲击维度的 **regime 调整后**权重（与其余维度同源语义）。

        与 `price_volume_corr_weight_for` 同理：`regime.strategy_weights` 无
        `volume_surge` 项 ⇒ 中性乘子 1.0 ⇒ `blended(1.0) = base_blend + regime_blend = 1.0`
        ⇒ 数值与「不调整」相同，但语义正确（将来 yaml 补项即自动生效）。
        """
        base = self.thresholds.composite
        canonical = canonicalize_regime(regime)
        adj = self.thresholds.regime.strategy_weights.get(canonical)
        if adj is None:
            legacy = {
                "aggressive_bull": "stable_bull",
                "volatile_bull": "volatile_bull",
                "defensive_bear": "volatile_bear",
                "rotation_sideways": "stable_sideways",
            }.get(canonical)
            if legacy:
                adj = self.thresholds.regime.strategy_weights.get(legacy)
        mult = float(getattr(adj, "volume_surge", 0.0) or 1.0) if adj else 1.0
        blended = base.base_blend_weight + base.regime_blend_weight * mult
        return base.volume_surge_weight * blended

    def price_volume_corr_weight_for(self, regime: str) -> float:
        """价量水平相关维度的 **regime 调整后**权重（与其余 7 维同源语义）。

        🔴 **不得裸用** `composite.price_volume_corr_weight` ——
        其余 7 维在 `get_regime_adjusted_weights` 里都乘了 `blended(mult)`
        （`base_blend_weight + regime_blend_weight * mult`），
        而 `regime.strategy_weights` 目前**没有** `price_volume_corr` 项
        ⇒ 取中性乘子 1.0 ⇒ `blended(1.0) = 0.7 + 0.3 = 1.0`
        ⇒ **数值上与「不调整」相同**，但**语义正确**：将来 yaml 补上该项会自动生效。

        不这样做会让 pvc 在不同 regime 下相对其它维度**份额失衡**
        （例如 stable_bull 下 momentum 被 ×1.06 而 pvc 纹丝不动）。
        """
        base = self.thresholds.composite
        canonical = canonicalize_regime(regime)
        adj = self.thresholds.regime.strategy_weights.get(canonical)
        if adj is None:
            legacy = {
                "aggressive_bull": "stable_bull",
                "volatile_bull": "volatile_bull",
                "defensive_bear": "volatile_bear",
                "rotation_sideways": "stable_sideways",
            }.get(canonical)
            if legacy:
                adj = self.thresholds.regime.strategy_weights.get(legacy)
        mult = float(getattr(adj, "price_volume_corr", 0.0) or 1.0) if adj else 1.0
        blended = base.base_blend_weight + base.regime_blend_weight * mult
        return base.price_volume_corr_weight * blended

    def volatility_position_weight_for(self, regime: str) -> float:
        """波动/位置维度的 **regime 调整后**权重（与 pvc/volume_surge 同源语义）。

        `regime.strategy_weights` 目前**没有** `volatility_position` 项
        ⇒ 取中性乘子 1.0 ⇒ `blended(1.0) = 0.7 + 0.3 = 1.0`
        ⇒ 数值上与「不调整」相同，但语义正确（将来 yaml 补项即自动生效）。
        """
        base = self.thresholds.composite
        canonical = canonicalize_regime(regime)
        adj = self.thresholds.regime.strategy_weights.get(canonical)
        if adj is None:
            legacy = {
                "aggressive_bull": "stable_bull",
                "volatile_bull": "volatile_bull",
                "defensive_bear": "volatile_bear",
                "rotation_sideways": "stable_sideways",
            }.get(canonical)
            if legacy:
                adj = self.thresholds.regime.strategy_weights.get(legacy)
        mult = float(getattr(adj, "volatility_position", 0.0) or 1.0) if adj else 1.0
        blended = base.base_blend_weight + base.regime_blend_weight * mult
        return base.volatility_position_weight * blended

    def macd_hist_weight_for(self, regime: str) -> float:
        """MACD 柱维度的 **regime 调整后**权重（与 pvc/volume_surge/volatility_position 同源语义）。

        `regime.strategy_weights` 目前**没有** `macd_hist` 项
        ⇒ 取中性乘子 1.0 ⇒ `blended(1.0) = 0.7 + 0.3 = 1.0`
        ⇒ 数值上与「不调整」相同，但语义正确（将来 yaml 补项即自动生效）。
        """
        base = self.thresholds.composite
        canonical = canonicalize_regime(regime)
        adj = self.thresholds.regime.strategy_weights.get(canonical)
        if adj is None:
            legacy = {
                "aggressive_bull": "stable_bull",
                "volatile_bull": "volatile_bull",
                "defensive_bear": "volatile_bear",
                "rotation_sideways": "stable_sideways",
            }.get(canonical)
            if legacy:
                adj = self.thresholds.regime.strategy_weights.get(legacy)
        mult = float(getattr(adj, "macd_hist", 0.0) or 1.0) if adj else 1.0
        blended = base.base_blend_weight + base.regime_blend_weight * mult
        return base.macd_hist_weight * blended

    def lower_shadow_ratio_weight_for(self, regime: str) -> float:
        """下影线比维度的 **regime 调整后**权重（与 macd_hist/volatility_position 同源语义）。

        `regime.strategy_weights` 目前**没有** `lower_shadow_ratio` 项
        ⇒ 取中性乘子 1.0 ⇒ `blended(1.0) = 0.7 + 0.3 = 1.0`
        ⇒ 数值上与「不调整」相同，但语义正确（将来 yaml 补项即自动生效）。
        """
        base = self.thresholds.composite
        canonical = canonicalize_regime(regime)
        adj = self.thresholds.regime.strategy_weights.get(canonical)
        if adj is None:
            legacy = {
                "aggressive_bull": "stable_bull",
                "volatile_bull": "volatile_bull",
                "defensive_bear": "volatile_bear",
                "rotation_sideways": "stable_sideways",
            }.get(canonical)
            if legacy:
                adj = self.thresholds.regime.strategy_weights.get(legacy)
        mult = float(getattr(adj, "lower_shadow_ratio", 0.0) or 1.0) if adj else 1.0
        blended = base.base_blend_weight + base.regime_blend_weight * mult
        return base.lower_shadow_ratio_weight * blended

    def amount_ratio_weight_for(self, regime: str) -> float:
        """成交额比维度的 **regime 调整后**权重（与 lower_shadow_ratio/macd_hist 同源语义）。

        `regime.strategy_weights` 目前**没有** `amount_ratio` 项
        ⇒ 取中性乘子 1.0 ⇒ `blended(1.0) = 0.7 + 0.3 = 1.0`
        ⇒ 数值上与「不调整」相同，但语义正确（将来 yaml 补项即自动生效）。
        """
        base = self.thresholds.composite
        canonical = canonicalize_regime(regime)
        adj = self.thresholds.regime.strategy_weights.get(canonical)
        if adj is None:
            legacy = {
                "aggressive_bull": "stable_bull",
                "volatile_bull": "volatile_bull",
                "defensive_bear": "volatile_bear",
                "rotation_sideways": "stable_sideways",
            }.get(canonical)
            if legacy:
                adj = self.thresholds.regime.strategy_weights.get(legacy)
        mult = float(getattr(adj, "amount_ratio", 0.0) or 1.0) if adj else 1.0
        blended = base.base_blend_weight + base.regime_blend_weight * mult
        return base.amount_ratio_weight * blended

    def _dimension_specs(
        self, data: Dict[str, pd.DataFrame], regime: str
    ) -> List[tuple[str, Dict[str, float], float, float]]:
        """打分维度**单一事实源**：`calculate_score` 与 `calculate_detailed_scores` 共用。

        每项为 ``(key, score_dict, weight, missing_default)`` 四元组，按累加顺序
        排列。两版共用本清单 ⇒ 维度集 / 权重 / 兜底值 / 标的并集**逐位一致**，
        从而 ``detailed[s]["total"] ≡ calculate_score(s)``（在**任何** thresholds 下
        恒成立，不再依赖 dataclass 默认值恰好让两版退化到相同维度集）。

        行为约束（与原实现逐行对齐，零打分数值变化）：
        - momentum 恒在首位（即使权重为 0 也参与累加）
        - 其余维度仅当 ``_has_*()`` 为真时纳入（与原 gate 一致）
        - 兜底值：volume_surge 缺失取 ``0.0``（量能冲击语义），其余取 ``0.5``（中性）
        """
        mw, qw, vw, volw, mrw, trw, htfw = self.get_regime_adjusted_weights(regime)
        specs: List[tuple[str, Dict[str, float], float, float]] = [
            ("momentum", self.momentum_strategy.calculate_score(data), mw, 0.5),
        ]

        if self._has_quality():
            specs.append(("quality", self.quality_strategy.calculate_score(data), qw, 0.5))
        if self._has_value():
            specs.append(("value", self.value_strategy.calculate_score(data), vw, 0.5))
        if self._has_volume():
            specs.append(("volume", self.volume_strategy.calculate_score(data), volw, 0.5))
        if self._has_mr():
            specs.append(("mean_reversion", self.mean_reversion_strategy.calculate_score(data), mrw, 0.5))
        if self._has_tr():
            specs.append(("triple_rise", self.triple_rise_strategy.calculate_score(data), trw, 0.5))
        if self._has_htf():
            specs.append(("high_tight_flag", self.htf_strategy.calculate_score(data), htfw, 0.5))
        if self._has_price_volume_corr():
            specs.append(
                (
                    "price_volume_corr",
                    self.price_volume_corr_strategy.calculate_score(data),
                    self.price_volume_corr_weight_for(regime),
                    0.5,
                )
            )
        if self._has_volume_surge():
            specs.append(
                (
                    "volume_surge",
                    self.volume_surge_strategy.calculate_score(data),
                    self.volume_surge_weight_for(regime),
                    0.0,  # 量能冲击：缺失取 0.0（与原实现一致）
                )
            )
        if self._has_volatility_position():
            specs.append(
                (
                    "volatility_position",
                    self.volatility_position_strategy.calculate_score(data),
                    self.volatility_position_weight_for(regime),
                    0.5,
                )
            )
        if self._has_macd_hist():
            specs.append(
                (
                    "macd_hist",
                    self.macd_hist_strategy.calculate_score(data),
                    self.macd_hist_weight_for(regime),
                    0.5,
                )
            )
        if self._has_lower_shadow_ratio():
            specs.append(
                (
                    "lower_shadow_ratio",
                    self.lower_shadow_ratio_strategy.calculate_score(data),
                    self.lower_shadow_ratio_weight_for(regime),
                    0.5,
                )
            )
        return specs

    def calculate_score(
        self, data: Dict[str, pd.DataFrame], regime: str = "unknown"
    ) -> Dict[str, float]:
        specs = self._dimension_specs(data, regime)
        all_symbols = set()
        for _key, score_dict, _weight, _default in specs:
            all_symbols |= set(score_dict.keys())

        final_scores = {}
        for symbol in all_symbols:
            total = 0.0
            w_sum = 0.0
            for _key, score_dict, weight, default in specs:
                val = score_dict.get(symbol, default)
                total += val * weight
                w_sum += weight
            base_score = total / w_sum if w_sum > 0 else 0.0
            final_scores[symbol] = max(0.0, min(1.0, base_score))

        return final_scores

    def calculate_detailed_scores(
        self, data: Dict[str, pd.DataFrame], regime: str = "unknown"
    ) -> Dict[str, Dict[str, float]]:
        specs = self._dimension_specs(data, regime)
        all_symbols = set()
        for _key, score_dict, _weight, _default in specs:
            all_symbols |= set(score_dict.keys())

        detailed: Dict[str, Dict[str, float]] = {}
        for symbol in all_symbols:
            total = 0.0
            w_sum = 0.0
            entry: Dict[str, float] = {}
            for key, score_dict, weight, default in specs:
                val = score_dict.get(symbol, default)
                total += val * weight
                w_sum += weight
                entry[key] = val
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
