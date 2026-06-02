from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from pathlib import Path
import yaml

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds
from aqsp.strategies.factor_monitor import (
    FactorMonitor,
    load_factor_monitor_config,
)


@dataclass(frozen=True)
class FactorDefinition:
    name: str
    category: str
    lookback_days: int
    weight: float
    is_enabled: bool
    description: str = ""


@dataclass(frozen=True)
class FactorConfig:
    version: str
    effective_from: str
    factors: Dict[str, List[FactorDefinition]]
    regime_adjustments: Dict[str, Dict[str, float]]
    scoring: Dict[str, Any]


class FactorCalculator:
    def __init__(self, config: FactorConfig):
        self.config = config

    def calculate_momentum_factors(self, df: pd.DataFrame) -> Dict[str, float]:
        if df is None or len(df) < 20:
            return {}

        factors = {}
        close = df["close"].values

        rsi_14 = self._calculate_rsi(close, 14)
        if rsi_14 is not None:
            factors["rsi_14"] = rsi_14 / 100.0

        if len(close) >= 20:
            roc_20 = (close[-1] - close[-20]) / close[-20]
            factors["roc_20"] = float(np.clip(roc_20, -1, 1))

        macd_signal = self._calculate_macd_signal(close)
        if macd_signal is not None:
            factors["macd_signal"] = macd_signal

        if len(close) >= 20:
            ma5 = np.mean(close[-5:])
            ma20 = np.mean(close[-20:])
            factors["ma_cross"] = 1.0 if ma5 > ma20 else 0.0

        return factors

    def calculate_value_factors(
        self, df: pd.DataFrame, fundamental: Optional[Dict[str, float]] = None
    ) -> Dict[str, float]:
        if fundamental is None:
            return {}

        factors = {}

        pe = fundamental.get("pe_ratio")
        if pe is not None and pe > 0:
            factors["pe_ratio"] = float(np.clip(1.0 / (pe / 20.0), 0, 1))

        pb = fundamental.get("pb_ratio")
        if pb is not None and pb > 0:
            factors["pb_ratio"] = float(np.clip(1.0 / (pb / 3.0), 0, 1))

        dividend_yield = fundamental.get("dividend_yield")
        if dividend_yield is not None:
            factors["dividend_yield"] = float(np.clip(dividend_yield / 0.05, 0, 1))

        ev_ebitda = fundamental.get("ev_ebitda")
        if ev_ebitda is not None and ev_ebitda > 0:
            factors["ev_ebitda"] = float(np.clip(1.0 / (ev_ebitda / 10.0), 0, 1))

        return factors

    def calculate_quality_factors(
        self, df: pd.DataFrame, fundamental: Optional[Dict[str, float]] = None
    ) -> Dict[str, float]:
        if fundamental is None:
            return {}

        factors = {}

        roe = fundamental.get("roe")
        if roe is not None:
            factors["roe"] = float(np.clip(roe / 0.15, 0, 1))

        gross_margin = fundamental.get("gross_margin")
        if gross_margin is not None:
            factors["gross_margin"] = float(np.clip(gross_margin / 0.3, 0, 1))

        debt_ratio = fundamental.get("debt_ratio")
        if debt_ratio is not None:
            factors["debt_ratio"] = float(np.clip(1.0 - debt_ratio, 0, 1))

        cash_flow_ratio = fundamental.get("cash_flow_ratio")
        if cash_flow_ratio is not None:
            factors["cash_flow_ratio"] = float(np.clip(cash_flow_ratio / 1.0, 0, 1))

        return factors

    def calculate_volatility_factors(self, df: pd.DataFrame) -> Dict[str, float]:
        if df is None or len(df) < 20:
            return {}

        factors = {}
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values

        atr_14 = self._calculate_atr(high, low, close, 14)
        if atr_14 is not None:
            atr_ratio = atr_14 / close[-1]
            factors["atr_14"] = float(np.clip(1.0 - atr_ratio * 10, 0, 1))

        if len(close) >= 60:
            returns = np.diff(close[-60:]) / close[-61:-1]
            market_returns = returns
            beta = np.cov(returns, market_returns)[0][1] / np.var(market_returns)
            factors["beta_60"] = float(np.clip(1.0 - abs(beta - 1.0), 0, 1))

        if len(close) >= 20:
            returns_20 = np.diff(close[-20:]) / close[-21:-1]
            volatility_20 = np.std(returns_20) * np.sqrt(252)
            factors["volatility_20"] = float(np.clip(1.0 - volatility_20, 0, 1))

        return factors

    def calculate_liquidity_factors(self, df: pd.DataFrame) -> Dict[str, float]:
        if df is None or len(df) < 20:
            return {}

        factors = {}
        volume = df["volume"].values

        if len(volume) >= 20:
            avg_volume_20 = np.mean(volume[-20:])
            turnover_rate = volume[-1] / avg_volume_20 if avg_volume_20 > 0 else 0
            factors["turnover_rate"] = float(np.clip(turnover_rate / 2.0, 0, 1))

        if len(volume) >= 5:
            avg_volume_5 = np.mean(volume[-5:])
            volume_ratio = volume[-1] / avg_volume_5 if avg_volume_5 > 0 else 0
            factors["volume_ratio"] = float(np.clip(volume_ratio / 2.0, 0, 1))

        if len(df) >= 20:
            close = df["close"].values
            amount = close * volume
            returns = np.diff(close[-20:]) / close[-21:-1]
            abs_returns = np.abs(returns)
            avg_amount = np.mean(amount[-20:])
            if avg_amount > 0:
                amihud = np.mean(abs_returns / avg_amount) * 1e6
                factors["amihud_illiquidity"] = float(
                    np.clip(1.0 - amihud / 10.0, 0, 1)
                )

        return factors

    def _calculate_rsi(self, prices: np.ndarray, period: int) -> Optional[float]:
        if len(prices) < period + 1:
            return None

        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _calculate_macd_signal(self, prices: np.ndarray) -> Optional[float]:
        if len(prices) < 26:
            return None

        ema12 = self._ema(prices, 12)
        ema26 = self._ema(prices, 26)
        dif = ema12 - ema26
        dea = self._ema(dif, 9)

        if dea[-1] == 0:
            return 0.0

        signal = (dif[-1] - dea[-1]) / abs(dea[-1])
        return float(np.clip(signal, -1, 1))

    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        alpha = 2 / (period + 1)
        ema = np.zeros_like(data)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
        return ema

    def _calculate_atr(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int
    ) -> Optional[float]:
        if len(high) < period + 1:
            return None

        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
        )

        return float(np.mean(tr[-period:]))

    def calculate_all_factors(
        self,
        df: pd.DataFrame,
        fundamental: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Dict[str, float]]:
        return {
            "momentum": self.calculate_momentum_factors(df),
            "value": self.calculate_value_factors(df, fundamental),
            "quality": self.calculate_quality_factors(df, fundamental),
            "volatility": self.calculate_volatility_factors(df),
            "liquidity": self.calculate_liquidity_factors(df),
        }


class MultiFactorRotationStrategy(BaseStrategy):
    name: str = "multi_factor_rotation"

    def __init__(
        self,
        config: StrategyConfig | None = None,
        thresholds: Thresholds | None = None,
        factor_config_path: Optional[str] = None,
    ):
        self.thresholds = thresholds or load_thresholds()
        self.factor_config = self._load_factor_config(factor_config_path)
        self.factor_calculator = FactorCalculator(self.factor_config)
        self.factor_monitor = FactorMonitor(
            load_factor_monitor_config(factor_config_path)
        )
        self._factor_weights: Dict[str, float] = self._initialize_weights()

        config = config or StrategyConfig(name="multi_factor_rotation")
        super().__init__(
            config,
            id="multi_factor_rotation",
            version=self.factor_config.version,
            hypothesis="多因子综合评分结合市场状态轮动，能提高选股胜率和稳定性",
        )

    def _load_factor_config(self, config_path: Optional[str] = None) -> FactorConfig:
        if config_path is None:
            config_path = str(
                Path(__file__).parent.parent.parent.parent
                / "config"
                / "factor_config.yaml"
            )

        path = Path(config_path)
        if not path.exists():
            return self._default_factor_config()

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        factors = {}
        for category, category_factors in data.get("factors", {}).items():
            factors[category] = [
                FactorDefinition(
                    name=factor_name,
                    category=category,
                    lookback_days=factor_data.get("lookback", 20),
                    weight=factor_data.get("weight", 0.1),
                    is_enabled=factor_data.get("enabled", True),
                    description=factor_data.get("description", ""),
                )
                for factor_name, factor_data in category_factors.items()
            ]

        return FactorConfig(
            version=data.get("version", "1.0"),
            effective_from=data.get("effective_from", ""),
            factors=factors,
            regime_adjustments=data.get("regime_adjustments", {}),
            scoring=data.get("scoring", {}),
        )

    def _default_factor_config(self) -> FactorConfig:
        return FactorConfig(
            version="1.0",
            effective_from="",
            factors={
                "momentum": [
                    FactorDefinition("rsi_14", "momentum", 14, 0.15, True),
                    FactorDefinition("roc_20", "momentum", 20, 0.10, True),
                ],
                "value": [],
                "quality": [],
                "volatility": [],
                "liquidity": [],
            },
            regime_adjustments={},
            scoring={"min_total_score": 0.6, "top_n": 10},
        )

    def _initialize_weights(self) -> Dict[str, float]:
        weights = {}
        for category, factors in self.factor_config.factors.items():
            for factor in factors:
                if factor.is_enabled:
                    weights[factor.name] = factor.weight
        return weights

    def get_regime_adjusted_weights(self, regime: str) -> Dict[str, float]:
        adjustment = self.factor_config.regime_adjustments.get(regime, {})
        if not adjustment:
            return self._factor_weights.copy()

        adjusted_weights = {}
        for factor_name, base_weight in self._factor_weights.items():
            for category, factors in self.factor_config.factors.items():
                if any(f.name == factor_name for f in factors):
                    multiplier = adjustment.get(category, 1.0)
                    adjusted_weights[factor_name] = base_weight * multiplier
                    break

        total = sum(adjusted_weights.values())
        if total > 0:
            adjusted_weights = {k: v / total for k, v in adjusted_weights.items()}

        return adjusted_weights

    def calculate_score(
        self, data: Dict[str, pd.DataFrame], regime: str = "unknown"
    ) -> Dict[str, float]:
        scores = {}
        for symbol, df in data.items():
            if df is None or df.empty:
                scores[symbol] = 0.0
                continue
            scores[symbol] = self._calculate_single_score(df, regime)
        return scores

    def _calculate_single_score(self, df: pd.DataFrame, regime: str) -> float:
        all_factors = self.factor_calculator.calculate_all_factors(df)

        adjusted_weights = self.get_regime_adjusted_weights(regime)

        total_score = 0.0
        total_weight = 0.0

        for category, factors in all_factors.items():
            for factor_name, factor_value in factors.items():
                if factor_name in adjusted_weights:
                    weight = adjusted_weights[factor_name]
                    total_score += factor_value * weight
                    total_weight += weight

        if total_weight == 0:
            return 0.0

        return float(total_score / total_weight)

    def calculate_detailed_scores(
        self,
        data: Dict[str, pd.DataFrame],
        regime: str = "unknown",
        fundamental_data: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        detailed = {}

        for symbol, df in data.items():
            if df is None or df.empty:
                detailed[symbol] = {"total": 0.0, "factors": {}}
                continue

            fundamental = fundamental_data.get(symbol) if fundamental_data else None
            all_factors = self.factor_calculator.calculate_all_factors(df, fundamental)
            adjusted_weights = self.get_regime_adjusted_weights(regime)

            factor_scores = {}
            total_score = 0.0
            total_weight = 0.0

            for category, factors in all_factors.items():
                for factor_name, factor_value in factors.items():
                    if factor_name in adjusted_weights:
                        weight = adjusted_weights[factor_name]
                        weighted_score = factor_value * weight
                        factor_scores[factor_name] = {
                            "value": factor_value,
                            "weight": weight,
                            "weighted_score": weighted_score,
                            "category": category,
                        }
                        total_score += weighted_score
                        total_weight += weight

            detailed[symbol] = {
                "total": total_score / total_weight if total_weight > 0 else 0.0,
                "factors": factor_scores,
                "regime": regime,
            }

        return detailed

    def update_factor_weights(self, performance: Dict[str, float]) -> bool:
        if not performance:
            return False

        updated = False
        for factor_name, perf_score in performance.items():
            if factor_name in self._factor_weights:
                old_weight = self._factor_weights[factor_name]
                new_weight = old_weight * (0.5 + perf_score)

                ceiling = self.factor_config.scoring.get("weight_ceiling", 1.45)
                floor = self.factor_config.scoring.get("weight_floor", 0.65)
                new_weight = max(floor, min(ceiling, new_weight))

                if abs(new_weight - old_weight) > 0.01:
                    self._factor_weights[factor_name] = new_weight
                    updated = True

        return updated

    def get_factor_effectiveness(self) -> Dict[str, float]:
        return self.factor_monitor.get_factor_effectiveness()

    def evaluate_factor(
        self,
        factor_name: str,
        factor_values: pd.Series,
        returns: pd.Series,
    ) -> None:
        self.factor_monitor.evaluate_factor_effectiveness(
            factor_name, factor_values, returns
        )

    def get_enabled_factors(self) -> List[FactorDefinition]:
        enabled = []
        for factors in self.factor_config.factors.values():
            enabled.extend([f for f in factors if f.is_enabled])
        return enabled

    def select_stocks(
        self,
        data: Dict[str, pd.DataFrame],
        n: int = 10,
        regime: str = "unknown",
    ) -> List[str]:
        scores = self.calculate_score(data, regime)
        ranked = self.rank(scores, ascending=False)

        min_score = self.factor_config.scoring.get("min_total_score", 0.6)
        filtered = [s for s in ranked if scores[s] >= min_score]

        return filtered[:n]
