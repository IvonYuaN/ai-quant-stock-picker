from __future__ import annotations

import itertools
import random
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ParamSpace:
    name: str
    low: float
    high: float
    step: float


@dataclass(frozen=True)
class OptimizationResult:
    best_params: dict[str, float]
    best_score: float
    all_results: list[dict[str, Any]]
    n_trials: int
    method: str


class GridSearchOptimizer:
    def __init__(
        self,
        param_spaces: list[ParamSpace],
        objective: str = "sharpe",
    ) -> None:
        self.param_spaces = param_spaces
        self.objective = objective

    def optimize(
        self,
        evaluate_fn: Callable[[dict[str, float]], float],
        max_trials: int = 1000,
    ) -> OptimizationResult:
        grids: list[list[tuple[str, float]]] = []
        for ps in self.param_spaces:
            values: list[float] = []
            v = ps.low
            while v <= ps.high + ps.step * 0.001:
                values.append(round(v, 10))
                v += ps.step
            grids.append([(ps.name, val) for val in values])

        total_combos = 1
        for g in grids:
            total_combos *= len(g)

        if total_combos <= max_trials:
            combos = list(itertools.product(*grids))
        else:
            combos = []
            seen: set[tuple[float, ...]] = set()
            rng = random.Random(42)
            while len(combos) < max_trials:
                combo = tuple(
                    (ps.name, round(rng.uniform(ps.low, ps.high), 10))
                    for ps in self.param_spaces
                )
                key = tuple(v for _, v in combo)
                if key not in seen:
                    seen.add(key)
                    combos.append(combo)

        all_results: list[dict[str, Any]] = []
        best_score = -float("inf")
        best_params: dict[str, float] = {}

        for combo in combos:
            params = {name: val for name, val in combo}
            try:
                score = evaluate_fn(params)
            except Exception:
                score = -float("inf")
            result_entry: dict[str, Any] = {**params, "score": score}
            all_results.append(result_entry)
            if score > best_score:
                best_score = score
                best_params = params.copy()

        return OptimizationResult(
            best_params=best_params,
            best_score=best_score,
            all_results=all_results,
            n_trials=len(combos),
            method="grid",
        )


class _GaussianProcess:
    def __init__(
        self,
        length_scale: float = 1.0,
        signal_variance: float = 1.0,
        noise_variance: float = 1e-6,
    ) -> None:
        self.length_scale = length_scale
        self.signal_variance = signal_variance
        self.noise_variance = noise_variance
        self._X_train: np.ndarray | None = None
        self._y_train: np.ndarray | None = None
        self._K_inv: np.ndarray | None = None

    def _rbf_kernel(self, X1: np.ndarray, X2: np.ndarray) -> np.ndarray:
        sq_dist = (
            np.sum(X1**2, axis=1, keepdims=True)
            + np.sum(X2**2, axis=1, keepdims=True).T
            - 2 * X1 @ X2.T
        )
        return self.signal_variance * np.exp(-0.5 * sq_dist / self.length_scale**2)

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self._X_train = X.copy()
        self._y_train = y.copy()
        K = self._rbf_kernel(X, X) + self.noise_variance * np.eye(len(X))
        self._K_inv = np.linalg.inv(K + 1e-8 * np.eye(len(X)))

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self._X_train is None or self._K_inv is None or self._y_train is None:
            raise RuntimeError("GP not fitted")
        K_s = self._rbf_kernel(self._X_train, X)
        mu = K_s.T @ self._K_inv @ self._y_train
        K_ss = self._rbf_kernel(X, X)
        sigma = np.diag(K_ss - K_s.T @ self._K_inv @ K_s)
        sigma = np.maximum(sigma, 0.0)
        return mu.flatten(), np.sqrt(sigma.flatten())


def _expected_improvement(
    mu: np.ndarray,
    sigma: np.ndarray,
    best_value: float,
    xi: float = 0.01,
) -> np.ndarray:
    from scipy.stats import norm

    with np.errstate(divide="warn", invalid="warn"):
        imp = mu - best_value - xi
        Z = imp / np.where(sigma > 0, sigma, 1e-10)
        ei = imp * norm.cdf(Z) + sigma * norm.pdf(Z)
        ei = np.where(sigma > 0, ei, 0.0)
    return ei


class BayesianOptimizer:
    def __init__(
        self,
        param_spaces: list[ParamSpace],
        objective: str = "sharpe",
    ) -> None:
        self.param_spaces = param_spaces
        self.objective = objective
        self._bounds = [(ps.low, ps.high) for ps in param_spaces]

    def _to_unit(self, params: dict[str, float]) -> np.ndarray:
        x = np.zeros(len(self.param_spaces))
        for i, ps in enumerate(self.param_spaces):
            span = ps.high - ps.low
            x[i] = (params[ps.name] - ps.low) / span if span > 0 else 0.5
        return x

    def _from_unit(self, x: np.ndarray) -> dict[str, float]:
        params: dict[str, float] = {}
        for i, ps in enumerate(self.param_spaces):
            span = ps.high - ps.low
            val = ps.low + x[i] * span
            val = max(ps.low, min(ps.high, val))
            params[ps.name] = round(val, 10)
        return params

    def _sample_random(self, rng: random.Random) -> dict[str, float]:
        return {
            ps.name: round(rng.uniform(ps.low, ps.high), 10) for ps in self.param_spaces
        }

    def optimize(
        self,
        evaluate_fn: Callable[[dict[str, float]], float],
        n_trials: int = 50,
        n_initial: int = 10,
    ) -> OptimizationResult:
        from scipy.optimize import minimize as scipy_minimize

        rng = random.Random(42)
        all_results: list[dict[str, Any]] = []
        X_list: list[np.ndarray] = []
        y_list: list[float] = []

        for _ in range(n_initial):
            params = self._sample_random(rng)
            try:
                score = evaluate_fn(params)
            except Exception:
                score = -float("inf")
            all_results.append({**params, "score": score})
            X_list.append(self._to_unit(params))
            y_list.append(score)

        best_idx = int(np.argmax(y_list))
        best_score = y_list[best_idx]
        best_params = all_results[best_idx].copy()
        best_params.pop("score", None)

        n_dim = len(self.param_spaces)
        length_scale = max(0.5, 1.0 / np.sqrt(n_dim))

        for trial_idx in range(n_initial, n_trials):
            X = np.array(X_list)
            y = np.array(y_list)

            gp = _GaussianProcess(
                length_scale=length_scale,
                signal_variance=1.0,
                noise_variance=1e-6,
            )
            gp.fit(X, y)

            def neg_ei(x: np.ndarray) -> float:
                x_2d = x.reshape(1, -1)
                mu, sigma = gp.predict(x_2d)
                ei = _expected_improvement(mu, sigma, best_score)
                return -float(ei[0])

            best_ei = -float("inf")
            best_x = X_list[-1]

            x0_candidates = [np.random.uniform(0, 1, n_dim) for _ in range(5)]
            x0_candidates.append(X_list[best_idx])
            x0_candidates.append(X_list[-1])

            for x0 in x0_candidates:
                try:
                    result = scipy_minimize(
                        neg_ei,
                        x0,
                        bounds=[(0.0, 1.0)] * n_dim,
                        method="L-BFGS-B",
                    )
                    if -result.fun > best_ei:
                        best_ei = -result.fun
                        best_x = result.x
                except Exception:
                    continue

            if best_ei <= 0:
                params = self._sample_random(rng)
            else:
                params = self._from_unit(best_x)

            try:
                score = evaluate_fn(params)
            except Exception:
                score = -float("inf")

            all_results.append({**params, "score": score})
            X_list.append(self._to_unit(params))
            y_list.append(score)

            if score > best_score:
                best_score = score
                best_params = params.copy()

            if len(y_list) > 1:
                y_arr = np.array(y_list)
                if float(np.std(y_arr)) > 0:
                    y_norm = (y_arr - np.mean(y_arr)) / np.std(y_arr)
                    length_scale = max(0.3, min(3.0, float(np.std(y_norm)) * 2.0))

        return OptimizationResult(
            best_params=best_params,
            best_score=best_score,
            all_results=all_results,
            n_trials=n_trials,
            method="bayesian",
        )


def create_walkforward_evaluator(
    symbols: list[str],
    frames: dict[str, pd.DataFrame],
    start: str,
    end: str,
    train_days: int = 120,
    test_days: int = 30,
) -> Callable[[dict[str, float]], float]:
    from aqsp.backtest.walk_forward import WalkForwardTester
    from aqsp.strategies.composite import CompositeStrategy
    from aqsp.strategies.thresholds import load_thresholds

    base_thresholds = load_thresholds()

    def evaluate(params: dict[str, float]) -> float:
        from dataclasses import replace

        composite_overrides: dict[str, float] = {}
        scoring_overrides: dict[str, float] = {}
        for key, val in params.items():
            if key.startswith("composite."):
                composite_overrides[key.split(".", 1)[1]] = val
            elif key.startswith("scoring."):
                scoring_overrides[key.split(".", 1)[1]] = val

        if composite_overrides:
            new_composite = replace(base_thresholds.composite, **composite_overrides)
            thresholds = replace(base_thresholds, composite=new_composite)
        else:
            thresholds = base_thresholds

        if scoring_overrides:
            new_scoring = replace(thresholds.scoring, **scoring_overrides)
            thresholds = replace(thresholds, scoring=new_scoring)

        strategy = CompositeStrategy(thresholds=thresholds)
        tester = WalkForwardTester(
            strategy=strategy,
            train_period_days=train_days,
            test_period_days=test_days,
            purge_days=5,
            horizon_days=3,
        )

        filtered: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            df = frames.get(sym)
            if df is None or df.empty:
                continue
            mask = (df["date"].astype(str) >= start) & (df["date"].astype(str) <= end)
            sliced = df.loc[mask]
            if len(sliced) >= 100:
                filtered[sym] = sliced.copy()

        if len(filtered) < 5:
            return -float("inf")

        result = tester.run(filtered, start_date=start, end_date=end)
        return result.overall.sharpe_ratio

    return evaluate
