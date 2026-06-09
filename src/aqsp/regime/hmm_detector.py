"""
HMM隐马尔可夫市场状态检测器

参考：gh__Abdullah-BA__RegimeSwitchingMomentumStrategy
核心：使用HMM识别牛市/熊市/震荡三种状态，自动切换策略权重
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

RegimeType = Literal["bull", "bear", "sideways"]


@dataclass(frozen=True)
class HMMRegimeResult:
    """HMM检测结果"""

    regime: RegimeType
    confidence: float  # 0-1
    bull_prob: float
    bear_prob: float
    sideways_prob: float
    volatility: float
    trend: float


class HMMRegimeDetector:
    """
    基于HMM的市场状态检测器

    状态定义：
    - bull: 牛市（正收益 + 低波动 or 正收益 + 高波动）
    - bear: 熊市（负收益 + 低波动 or 负收益 + 高波动）
    - sideways: 震荡（收益接近0 + 波动适中）

    特征：
    1. 日收益率
    2. 5日滚动波动率
    3. 5日均线斜率
    """

    def __init__(
        self,
        n_states: int = 3,
        lookback_days: int = 60,
        min_data_points: int = 30,
    ):
        self.n_states = n_states
        self.lookback_days = lookback_days
        self.min_data_points = min_data_points
        self._model = None
        self._is_fitted = False
        self._state_mapping = {}  # 存储状态映射: {regime_type -> state_idx}

    def detect_regime(self, price_df: pd.DataFrame) -> HMMRegimeResult:
        """
        检测当前市场状态

        Args:
            price_df: 必须包含 'close' 列的DataFrame

        Returns:
            HMMRegimeResult
        """
        try:
            # hmmlearn依赖可选安装
            from hmmlearn import hmm
        except ImportError:
            logger.warning("hmmlearn未安装，使用简单规则fallback")
            return self._fallback_detect(price_df)

        if price_df is None or len(price_df) < self.min_data_points:
            return self._fallback_detect(price_df)

        # 提取特征
        features = self._extract_features(price_df)
        if features is None or len(features) < self.min_data_points:
            return self._fallback_detect(price_df)

        # 训练/更新模型
        if not self._is_fitted or self._model is None:
            self._model = hmm.GaussianHMM(
                n_components=self.n_states,
                covariance_type="full",
                n_iter=100,
                random_state=42,
            )
            try:
                self._model.fit(features)
                self._is_fitted = True
            except Exception as e:
                logger.warning(f"HMM训练失败: {e}")
                return self._fallback_detect(price_df)

        # 预测当前状态
        try:
            states = self._model.predict(features)
            current_state = states[-1]

            # 获取状态概率
            probs = self._model.predict_proba(features)
            current_probs = probs[-1]

            # 映射状态到regime
            regime, confidence = self._map_state_to_regime(
                current_state, current_probs, features, states
            )

            # 计算当前波动率和趋势
            latest = features[-1]
            volatility = float(latest[1])  # 波动率特征
            trend = float(latest[2])  # 趋势特征

            return HMMRegimeResult(
                regime=regime,
                confidence=confidence,
                bull_prob=float(current_probs[self._bull_state_idx()]),
                bear_prob=float(current_probs[self._bear_state_idx()]),
                sideways_prob=float(current_probs[self._sideways_state_idx()]),
                volatility=volatility,
                trend=trend,
            )
        except Exception as e:
            logger.warning(f"HMM预测失败: {e}")
            return self._fallback_detect(price_df)

    def _extract_features(self, df: pd.DataFrame) -> np.ndarray | None:
        """提取HMM特征"""
        if "close" not in df.columns:
            return None

        recent = df.tail(self.lookback_days).copy()
        if len(recent) < self.min_data_points:
            return None

        # 特征1: 日收益率
        recent["returns"] = recent["close"].pct_change()

        # 特征2: 5日滚动波动率
        recent["volatility"] = recent["returns"].rolling(5).std()

        # 特征3: 5日均线斜率（趋势）
        recent["ma5"] = recent["close"].rolling(5).mean()
        recent["trend"] = recent["ma5"].pct_change(5)

        # 删除NaN
        recent = recent.dropna()

        if len(recent) < self.min_data_points:
            return None

        features = recent[["returns", "volatility", "trend"]].values
        return features

    def _map_state_to_regime(
        self,
        state: int,
        probs: np.ndarray,
        features: np.ndarray,
        states: np.ndarray,
    ) -> tuple[RegimeType, float]:
        """将HMM状态映射到regime类型"""
        # 获取各状态的平均特征
        state_means = []
        for s in range(self.n_states):
            mask = states == s
            if mask.sum() > 0:
                state_mean_return = features[mask, 0].mean()
                state_mean_vol = features[mask, 1].mean()
                state_means.append((s, state_mean_return, state_mean_vol))

        if not state_means:
            return "sideways", 0.5

        # 按平均收益率排序
        state_means.sort(key=lambda x: x[1], reverse=True)

        # 最高收益率 -> bull, 最低 -> bear, 中间 -> sideways
        bull_state = state_means[0][0]
        bear_state = state_means[-1][0]
        sideways_state = state_means[len(state_means) // 2][0] if len(state_means) >= 3 else state_means[0][0]

        # 记录状态映射，供概率查询使用
        self._state_mapping = {
            "bull": bull_state,
            "bear": bear_state,
            "sideways": sideways_state,
        }

        if state == bull_state:
            return "bull", float(probs[state])
        elif state == bear_state:
            return "bear", float(probs[state])
        else:
            return "sideways", float(probs[state])

    def _bull_state_idx(self) -> int:
        """获取牛市状态索引"""
        return self._state_mapping.get("bull", 0)

    def _bear_state_idx(self) -> int:
        """获取熊市状态索引"""
        return self._state_mapping.get("bear", 2)

    def _sideways_state_idx(self) -> int:
        """获取震荡状态索引"""
        return self._state_mapping.get("sideways", 1)

    def _fallback_detect(self, df: pd.DataFrame | None) -> HMMRegimeResult:
        """简单规则fallback"""
        if df is None or len(df) < 5:
            return HMMRegimeResult(
                regime="sideways",
                confidence=0.5,
                bull_prob=0.33,
                bear_prob=0.33,
                sideways_prob=0.34,
                volatility=0.02,
                trend=0.0,
            )

        recent = df.tail(20)
        returns = recent["close"].pct_change().dropna()

        if len(returns) == 0:
            return HMMRegimeResult(
                regime="sideways",
                confidence=0.5,
                bull_prob=0.33,
                bear_prob=0.33,
                sideways_prob=0.34,
                volatility=0.02,
                trend=0.0,
            )

        mean_return = returns.mean()
        volatility = returns.std()

        # 简单规则
        if mean_return > 0.01:  # 平均日涨幅>1%
            regime = "bull"
            confidence = min(0.7, 0.5 + abs(mean_return) * 10)
        elif mean_return < -0.01:  # 平均日跌幅>1%
            regime = "bear"
            confidence = min(0.7, 0.5 + abs(mean_return) * 10)
        else:
            regime = "sideways"
            confidence = 0.6

        # 根据regime分配概率
        if regime == "bull":
            bull_prob, bear_prob, sideways_prob = 0.6, 0.2, 0.2
        elif regime == "bear":
            bull_prob, bear_prob, sideways_prob = 0.2, 0.6, 0.2
        else:
            bull_prob, bear_prob, sideways_prob = 0.25, 0.25, 0.5

        return HMMRegimeResult(
            regime=regime,
            confidence=confidence,
            bull_prob=bull_prob,
            bear_prob=bear_prob,
            sideways_prob=sideways_prob,
            volatility=float(volatility) if not np.isnan(volatility) else 0.02,
            trend=float(mean_return) if not np.isnan(mean_return) else 0.0,
        )
