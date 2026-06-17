from __future__ import annotations

from aqsp.optimizer.param_optimizer import BayesianOptimizer, ParamSpace


def test_bayesian_optimizer_is_reproducible() -> None:
    spaces = [
        ParamSpace("a", 0.0, 1.0, 0.1),
        ParamSpace("b", 0.0, 1.0, 0.1),
    ]

    def evaluate(params: dict[str, float]) -> float:
        return -((params["a"] - 0.3) ** 2 + (params["b"] - 0.7) ** 2)

    first = BayesianOptimizer(spaces).optimize(evaluate, n_trials=12, n_initial=4)
    second = BayesianOptimizer(spaces).optimize(evaluate, n_trials=12, n_initial=4)

    assert first.best_params == second.best_params
    assert first.best_score == second.best_score
    assert first.all_results == second.all_results
