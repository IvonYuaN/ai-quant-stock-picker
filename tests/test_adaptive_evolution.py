from __future__ import annotations

from datetime import date

from aqsp.core.time import today_shanghai
from aqsp.strategies.adaptive_evolution import (
    AdaptiveEvolutionCoordinator,
    FactorPerformance,
    FactorWeightAdaptor,
)
from aqsp.walkforward_gate import build_walkforward_gate_payload


def _passing_gate_payload() -> dict[str, object]:
    return build_walkforward_gate_payload(
        dsr=1.9,
        pbo=0.24,
        run_date=today_shanghai().isoformat(),
        start="2023-01-01",
        end="2024-12-31",
        n_periods=12,
        metadata={
            "backtest_assumptions": {
                "uses_raw_prices": True,
                "uses_point_in_time_data": True,
                "train_test_separated": True,
                "has_purge_window": True,
                "includes_transaction_costs": True,
                "includes_slippage": True,
                "excludes_not_executable": True,
                "cost_model": "fee+slippage",
            }
        },
    )


def _factor(independent_signal_days: int) -> FactorPerformance:
    return FactorPerformance(
        factor_name="momentum",
        ic_30d=-0.05,
        ic_decay=-0.1,
        win_rate_30d=0.4,
        sharpe_30d=0.2,
        sample_count=100,
        last_updated=date(2026, 7, 17),
        independent_signal_days=independent_signal_days,
    )


def test_factor_adaptor_requires_independent_signal_days() -> None:
    adaptor = FactorWeightAdaptor()

    assert adaptor.evaluate_factors({"momentum": _factor(29)}, {"momentum": 0.4}) == []
    assert (
        len(adaptor.evaluate_factors({"momentum": _factor(30)}, {"momentum": 0.4})) == 1
    )


def test_adaptive_evolution_blocks_without_gate_and_respects_cooldown(
    tmp_path, monkeypatch
) -> None:
    from aqsp.strategies.adaptive_evolution import RollbackManager

    monkeypatch.setattr(
        RollbackManager, "SNAPSHOT_FILE", str(tmp_path / "snapshots.json")
    )
    coordinator = AdaptiveEvolutionCoordinator(
        AdaptiveEvolutionCoordinator.Config(
            min_independent_signal_days=3,
            cooldown_days=30,
        )
    )
    performance = {"momentum": _factor(3)}
    weights = {"momentum": 0.4}

    blocked, snapshot_id = coordinator.weekly_factor_review(
        performance,
        weights,
        current_sharpe=1.0,
        current_win_rate=0.5,
    )
    assert blocked == []
    assert snapshot_id == ""

    adjustments, snapshot_id = coordinator.weekly_factor_review(
        performance,
        weights,
        current_sharpe=1.0,
        current_win_rate=0.5,
        walkforward_payload=_passing_gate_payload(),
    )
    assert len(adjustments) == 1
    assert snapshot_id
    assert weights == {"momentum": 0.4}

    cooled_down, cooled_snapshot = coordinator.weekly_factor_review(
        performance,
        weights,
        current_sharpe=1.0,
        current_win_rate=0.5,
        walkforward_payload=_passing_gate_payload(),
    )
    assert cooled_down == []
    assert cooled_snapshot == ""


def test_adaptive_evolution_daily_route_ignores_meta_learning() -> None:
    coordinator = AdaptiveEvolutionCoordinator()

    coordinator.meta_learner.recommend_mix = lambda _regime: (_ for _ in ()).throw(
        AssertionError("meta learning must stay proposal-only")
    )

    mix = coordinator.daily_adapt("stable_bull")

    assert "limit_up_ladder" in mix.enabled_strategies
