"""
Regime自适应策略混合器

根据HMM检测的市场状态，自动调整策略权重
参考：gh__Abdullah-BA__RegimeSwitchingMomentumStrategy
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from aqsp.regime.hmm_detector import HMMRegimeDetector, HMMRegimeResult, RegimeType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StrategyMix:
    """策略组合配置"""

    regime: RegimeType
    weights: dict[str, float]  # 策略名 -> 权重
    description: str
    focus: tuple[str, ...]  # 重点关注的策略


# 预定义策略组合
REGIME_STRATEGY_MIXES: dict[RegimeType, StrategyMix] = {
    "bull": StrategyMix(
        regime="bull",
        weights={
            "momentum": 0.40,  # 动量追涨
            "breakthrough": 0.30,  # 突破策略
            "sector_rotation": 0.20,  # 板块轮动
            "value": 0.10,  # 低估值补充
        },
        description="牛市配置：重动量+突破，轻价值",
        focus=("momentum", "breakthrough"),
    ),
    "bear": StrategyMix(
        regime="bear",
        weights={
            "value": 0.40,  # 防御性价值股
            "defensive": 0.30,  # 防御性板块
            "quality": 0.20,  # 高质量公司
            "dividend": 0.10,  # 高股息
        },
        description="熊市配置：重价值+防御，等待底部",
        focus=("value", "defensive"),
    ),
    "sideways": StrategyMix(
        regime="sideways",
        weights={
            "mean_reversion": 0.30,  # 均值回归
            "sector_rotation": 0.30,  # 板块轮动
            "momentum": 0.20,  # 短期动量
            "n_rebound": 0.20,  # N字反弹
        },
        description="震荡配置：均值回归+板块轮动，灵活切换",
        focus=("mean_reversion", "sector_rotation"),
    ),
}


class RegimeAdaptiveStrategySelector:
    """
    Regime自适应策略选择器

    核心逻辑：
    1. HMM检测当前市场状态（bull/bear/sideways）
    2. 根据状态选择预定义策略组合
    3. 返回策略权重配置
    """

    def __init__(
        self,
        hmm_detector: HMMRegimeDetector | None = None,
        custom_mixes: dict[RegimeType, StrategyMix] | None = None,
    ):
        self.hmm_detector = hmm_detector or HMMRegimeDetector()
        self.strategy_mixes = custom_mixes or REGIME_STRATEGY_MIXES

    def select_strategy_mix(
        self,
        market_data: dict[str, Any],
    ) -> tuple[StrategyMix, HMMRegimeResult]:
        """
        选择当前适合的策略组合

        Args:
            market_data: 必须包含 'index_df' (指数DataFrame)

        Returns:
            (StrategyMix, HMMRegimeResult)
        """
        try:
            # 检测regime
            index_df = market_data.get("index_df")
            if index_df is None:
                logger.warning("market_data缺少index_df，使用默认震荡配置")
                fallback_result = HMMRegimeResult(
                    regime="sideways",
                    confidence=0.0,
                    bull_prob=0.33,
                    bear_prob=0.33,
                    sideways_prob=0.34,
                    volatility=0.0,
                    trend=0.0,
                )
                return self.strategy_mixes["sideways"], fallback_result

            regime_result = self.hmm_detector.detect_regime(index_df)

            # 选择策略组合
            strategy_mix = self.strategy_mixes.get(
                regime_result.regime,
                self.strategy_mixes["sideways"],  # 默认震荡配置
            )

            logger.info(
                f"Regime检测: {regime_result.regime} "
                f"(置信度{regime_result.confidence:.2%}), "
                f"策略组合: {strategy_mix.description}"
            )

            return strategy_mix, regime_result
        except Exception as e:
            logger.error(f"Regime自适应选择失败: {e}，使用默认震荡配置", exc_info=True)
            fallback_result = HMMRegimeResult(
                regime="sideways",
                confidence=0.0,
                bull_prob=0.33,
                bear_prob=0.33,
                sideways_prob=0.34,
                volatility=0.0,
                trend=0.0,
            )
            return self.strategy_mixes["sideways"], fallback_result

    def get_strategy_weight(
        self,
        strategy_name: str,
        market_data: dict[str, Any],
    ) -> float:
        """获取特定策略的权重"""
        try:
            strategy_mix, _ = self.select_strategy_mix(market_data)
            weight = strategy_mix.weights.get(strategy_name, 0.0)
            return max(0.0, min(1.0, float(weight)))  # 确保权重在合理范围内
        except Exception as e:
            logger.error(f"获取策略权重失败 ({strategy_name}): {e}", exc_info=True)
            return 0.0

    def is_strategy_active(
        self,
        strategy_name: str,
        market_data: dict[str, any],
        min_weight: float = 0.1,
    ) -> bool:
        """判断策略是否激活（权重超过阈值）"""
        weight = self.get_strategy_weight(strategy_name, market_data)
        return weight >= min_weight


# 快捷函数
def get_current_regime_and_strategy(
    index_df: Any,
) -> tuple[RegimeType, StrategyMix, HMMRegimeResult]:
    """
    快捷函数：获取当前regime和推荐策略组合

    Args:
        index_df: 指数DataFrame（必须包含close列）

    Returns:
        (regime, strategy_mix, hmm_result)
    """
    selector = RegimeAdaptiveStrategySelector()
    strategy_mix, hmm_result = selector.select_strategy_mix(
        {"index_df": index_df}
    )
    return hmm_result.regime, strategy_mix, hmm_result
