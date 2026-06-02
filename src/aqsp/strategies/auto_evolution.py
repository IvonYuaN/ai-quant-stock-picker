from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

from aqsp.core.time import now_shanghai
from aqsp.strategies.thresholds import load_thresholds


@dataclass(frozen=True)
class ParameterEvolution:
    param_name: str
    current_value: float
    optimal_value: float
    confidence: float
    last_updated: str
    performance_history: List[Dict[str, Any]]


@dataclass(frozen=True)
class EvolutionConfig:
    enabled: bool = True
    check_interval_days: int = 7
    min_samples: int = 30
    max_evolution_per_cycle: int = 3
    confidence_threshold: float = 0.7
    performance_threshold: float = 0.05
    param_spaces: Dict[str, Dict[str, List[float]]] = field(default_factory=dict)
    regime_adaptation: Dict[str, Any] = field(default_factory=dict)
    rollback: Dict[str, Any] = field(default_factory=dict)
    monitoring: Dict[str, Any] = field(default_factory=dict)
    optimization: Dict[str, Any] = field(default_factory=dict)
    walk_forward: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketRegimeAnalysis:
    regime: str
    confidence: float
    volatility: float
    trend_strength: float
    momentum: float
    timestamp: datetime


@dataclass(frozen=True)
class EvolutionResult:
    strategy_name: str
    old_params: Dict[str, float]
    new_params: Dict[str, float]
    performance_improvement: float
    confidence: float
    timestamp: datetime
    reason: str


class AutoEvolution:
    def __init__(
        self,
        config_path: str = "config/evolution_config.yaml",
        thresholds_path: str = "config/thresholds.yaml",
        data_dir: str = "data/evolution",
    ) -> None:
        self.config_path = Path(config_path)
        self.thresholds_path = Path(thresholds_path)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.config = self._load_config()
        self.thresholds = load_thresholds(str(self.thresholds_path))
        self.evolution_history: List[EvolutionResult] = []
        self._last_evolution_time: Optional[datetime] = None

    def _load_config(self) -> EvolutionConfig:
        if not self.config_path.exists():
            return EvolutionConfig()

        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        evolution_data = data.get("evolution", {})
        return EvolutionConfig(
            enabled=evolution_data.get("enabled", True),
            check_interval_days=evolution_data.get("check_interval_days", 7),
            min_samples=evolution_data.get("min_samples", 30),
            max_evolution_per_cycle=evolution_data.get("max_evolution_per_cycle", 3),
            confidence_threshold=evolution_data.get("confidence_threshold", 0.7),
            performance_threshold=evolution_data.get("performance_threshold", 0.05),
            param_spaces=evolution_data.get("param_spaces", {}),
            regime_adaptation=evolution_data.get("regime_adaptation", {}),
            rollback=evolution_data.get("rollback", {}),
            monitoring=evolution_data.get("monitoring", {}),
            optimization=evolution_data.get("optimization", {}),
            walk_forward=evolution_data.get("walk_forward", {}),
        )

    def analyze_market_regime(
        self,
        index_data: pd.DataFrame,
        lookback_days: int = 60,
    ) -> MarketRegimeAnalysis:
        if index_data is None or index_data.empty:
            return MarketRegimeAnalysis(
                regime="unknown",
                confidence=0.0,
                volatility=0.0,
                trend_strength=0.0,
                momentum=0.0,
                timestamp=now_shanghai(),
            )

        df = index_data.sort_values("date").tail(lookback_days)
        if len(df) < 20:
            return MarketRegimeAnalysis(
                regime="unknown",
                confidence=0.0,
                volatility=0.0,
                trend_strength=0.0,
                momentum=0.0,
                timestamp=now_shanghai(),
            )

        prices = df["close"].values
        returns = np.diff(prices) / prices[:-1]

        volatility = float(np.std(returns) * np.sqrt(252))
        momentum = float((prices[-1] - prices[0]) / prices[0])

        df_copy = df.copy()
        df_copy["ma20"] = df_copy["close"].rolling(20).mean()
        df_copy["ma60"] = df_copy["close"].rolling(60).mean()
        ma20_last = df_copy["ma20"].iloc[-1]
        ma60_last = df_copy["ma60"].iloc[-1]
        trend_strength = 0.0
        if pd.notna(ma20_last) and pd.notna(ma60_last) and ma60_last != 0:
            trend_strength = float((ma20_last - ma60_last) / ma60_last)

        regime = self._classify_regime(volatility, trend_strength, momentum)
        confidence = self._calculate_confidence(volatility, trend_strength, momentum)

        return MarketRegimeAnalysis(
            regime=regime,
            confidence=confidence,
            volatility=volatility,
            trend_strength=trend_strength,
            momentum=momentum,
            timestamp=now_shanghai(),
        )

    def _classify_regime(
        self,
        volatility: float,
        trend_strength: float,
        momentum: float,
    ) -> str:
        if volatility > 0.3:
            if momentum > 0.1:
                return "volatile_bull"
            elif momentum < -0.1:
                return "volatile_bear"
            else:
                return "volatile_sideways"
        else:
            if trend_strength > 0.02:
                return "stable_bull"
            elif trend_strength < -0.02:
                return "stable_bear"
            else:
                return "stable_sideways"

    def _calculate_confidence(
        self,
        volatility: float,
        trend_strength: float,
        momentum: float,
    ) -> float:
        conf = 0.5

        if volatility > 0.2:
            conf += 0.2
        if abs(trend_strength) > 0.01:
            conf += 0.15
        if abs(momentum) > 0.05:
            conf += 0.15

        return min(conf, 1.0)

    def get_optimal_params(
        self,
        regime: str,
        strategy_name: str,
    ) -> Dict[str, float]:
        regime_params = self.config.regime_adaptation.get("regime_params", {})
        regime_config = regime_params.get(regime, {})

        base_params = self._get_base_params(strategy_name)

        adapted_params = {}
        for param_name, base_value in base_params.items():
            boost_key = f"{param_name}_boost"
            adjust_key = f"{param_name}_adjust"

            if boost_key in regime_config:
                adapted_params[param_name] = base_value * regime_config[boost_key]
            elif adjust_key in regime_config:
                adapted_params[param_name] = base_value + regime_config[adjust_key]
            else:
                adapted_params[param_name] = base_value

        return adapted_params

    def _get_base_params(self, strategy_name: str) -> Dict[str, float]:
        param_spaces = self.config.param_spaces.get(strategy_name, {})
        base_params = {}

        for param_name, bounds in param_spaces.items():
            if len(bounds) == 2:
                base_params[param_name] = (bounds[0] + bounds[1]) / 2

        return base_params

    def update_params_from_performance(
        self,
        performance: Dict[str, float],
    ) -> bool:
        if not self.config.enabled:
            return False

        if self._last_evolution_time is not None:
            elapsed = now_shanghai() - self._last_evolution_time
            if elapsed.days < self.config.check_interval_days:
                return False

        return True

    def should_evolve(
        self,
        current_performance: Dict[str, float],
        threshold: float = 0.05,
    ) -> bool:
        if not self.config.enabled:
            return False

        if len(current_performance) < self.config.min_samples:
            return False

        recent_performance = self._get_recent_performance()
        if recent_performance is None:
            return True

        improvement = current_performance.get(
            "sharpe_ratio", 0
        ) - recent_performance.get("sharpe_ratio", 0)
        return improvement < -threshold

    def _get_recent_performance(self) -> Optional[Dict[str, float]]:
        history_file = self.data_dir / "performance_history.jsonl"
        if not history_file.exists():
            return None

        with open(history_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        if not lines:
            return None

        latest = json.loads(lines[-1])
        return latest.get("performance", {})

    def evolve_parameters(
        self,
        strategy_name: str,
        data: Dict[str, pd.DataFrame],
    ) -> Dict[str, float]:
        if not self.config.enabled:
            return {}

        current_params = self._get_base_params(strategy_name)
        current_performance = self._evaluate_params(current_params, data, strategy_name)

        candidates = self._generate_candidates(strategy_name, current_params)
        best_params = current_params
        best_score = current_performance.get("sharpe_ratio", 0)

        for candidate in candidates:
            score = self._evaluate_params(candidate, data, strategy_name).get(
                "sharpe_ratio", 0
            )
            if score > best_score:
                best_score = score
                best_params = candidate

        improvement = best_score - current_performance.get("sharpe_ratio", 0)
        if improvement > self.config.performance_threshold:
            self._record_evolution(
                strategy_name,
                current_params,
                best_params,
                improvement,
                "performance_improvement",
            )
            self._last_evolution_time = now_shanghai()
            return best_params

        return current_params

    def _generate_candidates(
        self,
        strategy_name: str,
        current_params: Dict[str, float],
    ) -> List[Dict[str, float]]:
        param_spaces = self.config.param_spaces.get(strategy_name, {})
        candidates = []

        for param_name, bounds in param_spaces.items():
            if len(bounds) != 2:
                continue

            current_value = current_params.get(param_name, (bounds[0] + bounds[1]) / 2)

            candidate = current_params.copy()
            candidate[param_name] = max(bounds[0], min(bounds[1], current_value * 0.9))
            candidates.append(candidate)

            candidate = current_params.copy()
            candidate[param_name] = max(bounds[0], min(bounds[1], current_value * 1.1))
            candidates.append(candidate)

        return candidates

    def _evaluate_params(
        self,
        params: Dict[str, float],
        data: Dict[str, pd.DataFrame],
        strategy_name: str,
    ) -> Dict[str, float]:
        from aqsp.strategies.composite import CompositeStrategy
        from aqsp.strategies.thresholds import load_thresholds

        base_thresholds = load_thresholds()

        strategy = CompositeStrategy(thresholds=base_thresholds)
        scores = strategy.calculate_score(data)

        if not scores:
            return {"sharpe_ratio": 0.0, "win_rate": 0.0}

        score_values = list(scores.values())
        return {
            "sharpe_ratio": float(np.mean(score_values)),
            "win_rate": float(np.mean([1 if s > 0.5 else 0 for s in score_values])),
        }

    def _record_evolution(
        self,
        strategy_name: str,
        old_params: Dict[str, float],
        new_params: Dict[str, float],
        improvement: float,
        reason: str,
    ) -> None:
        result = EvolutionResult(
            strategy_name=strategy_name,
            old_params=old_params,
            new_params=new_params,
            performance_improvement=improvement,
            confidence=0.8,
            timestamp=now_shanghai(),
            reason=reason,
        )

        self.evolution_history.append(result)

        history_file = self.data_dir / "evolution_history.jsonl"
        entry = {
            "timestamp": result.timestamp.isoformat(timespec="seconds"),
            "strategy_name": result.strategy_name,
            "old_params": result.old_params,
            "new_params": result.new_params,
            "performance_improvement": result.performance_improvement,
            "confidence": result.confidence,
            "reason": result.reason,
        }

        with open(history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def save_evolution_history(self, output_path: str) -> None:
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        history_data = []
        for result in self.evolution_history:
            history_data.append(
                {
                    "timestamp": result.timestamp.isoformat(timespec="seconds"),
                    "strategy_name": result.strategy_name,
                    "old_params": result.old_params,
                    "new_params": result.new_params,
                    "performance_improvement": result.performance_improvement,
                    "confidence": result.confidence,
                    "reason": result.reason,
                }
            )

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)


class ParameterOptimizer:
    def __init__(
        self,
        param_space: Dict[str, Tuple[float, float]],
    ) -> None:
        self.param_space = param_space

    def grid_search(
        self,
        evaluate_fn: Callable[[Dict[str, float]], float],
        n_points: int = 10,
    ) -> Dict[str, float]:
        import itertools

        param_grids = []
        for param_name, (low, high) in self.param_space.items():
            values = np.linspace(low, high, n_points)
            param_grids.append([(param_name, v) for v in values])

        best_params = {}
        best_score = -float("inf")

        for combo in itertools.product(*param_grids):
            params = {name: value for name, value in combo}
            try:
                score = evaluate_fn(params)
            except Exception:
                score = -float("inf")

            if score > best_score:
                best_score = score
                best_params = params.copy()

        return best_params

    def random_search(
        self,
        evaluate_fn: Callable[[Dict[str, float]], float],
        n_trials: int = 50,
    ) -> Dict[str, float]:
        rng = np.random.RandomState(42)

        best_params = {}
        best_score = -float("inf")

        for _ in range(n_trials):
            params = {}
            for param_name, (low, high) in self.param_space.items():
                params[param_name] = rng.uniform(low, high)

            try:
                score = evaluate_fn(params)
            except Exception:
                score = -float("inf")

            if score > best_score:
                best_score = score
                best_params = params.copy()

        return best_params

    def bayesian_optimization(
        self,
        evaluate_fn: Callable[[Dict[str, float]], float],
        n_trials: int = 30,
    ) -> Dict[str, float]:
        from aqsp.optimizer.param_optimizer import BayesianOptimizer, ParamSpace

        param_spaces = []
        for param_name, (low, high) in self.param_space.items():
            param_spaces.append(
                ParamSpace(
                    name=param_name,
                    low=low,
                    high=high,
                    step=(high - low) / 100,
                )
            )

        optimizer = BayesianOptimizer(param_spaces)
        result = optimizer.optimize(evaluate_fn, n_trials=n_trials)

        return result.best_params

    def evaluate_parameters(
        self,
        params: Dict[str, float],
        data: Dict[str, pd.DataFrame],
        forward_days: int = 5,
    ) -> float:
        from aqsp.strategies.composite import CompositeStrategy
        from aqsp.strategies.thresholds import load_thresholds

        base_thresholds = load_thresholds()

        strategy = CompositeStrategy(thresholds=base_thresholds)
        scores = strategy.calculate_score(data)

        if not scores:
            return 0.0

        score_values = list(scores.values())
        return float(np.mean(score_values))


class MarketRegimeAdapter:
    def __init__(self) -> None:
        self.regime_params: Dict[str, Dict[str, float]] = {}

    def load_regime_params(self, config_path: str) -> None:
        config_file = Path(config_path)
        if not config_file.exists():
            return

        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        evolution_data = data.get("evolution", {})
        regime_adaptation = evolution_data.get("regime_adaptation", {})
        self.regime_params = regime_adaptation.get("regime_params", {})

    def adapt_to_regime(
        self,
        base_params: Dict[str, float],
        regime: str,
        confidence: float,
    ) -> Dict[str, float]:
        if not self.regime_params:
            return base_params

        regime_config = self.regime_params.get(regime, {})
        if not regime_config:
            return base_params

        adapted_params = {}
        for param_name, base_value in base_params.items():
            boost_key = f"{param_name}_boost"
            adjust_key = f"{param_name}_adjust"

            if boost_key in regime_config:
                adapted_value = base_value * regime_config[boost_key]
            elif adjust_key in regime_config:
                adapted_value = base_value + regime_config[adjust_key]
            else:
                adapted_value = base_value

            adapted_params[param_name] = adapted_value

        return adapted_params

    def blend_params(
        self,
        params_a: Dict[str, float],
        params_b: Dict[str, float],
        weight: float,
    ) -> Dict[str, float]:
        weight = max(0.0, min(1.0, weight))

        blended = {}
        all_keys = set(params_a.keys()) | set(params_b.keys())

        for key in all_keys:
            value_a = params_a.get(key, 0.0)
            value_b = params_b.get(key, 0.0)
            blended[key] = value_a * (1 - weight) + value_b * weight

        return blended
