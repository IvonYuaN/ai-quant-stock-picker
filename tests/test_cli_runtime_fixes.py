from __future__ import annotations

from argparse import Namespace
from datetime import datetime
import json
from pathlib import Path
import inspect


def test_run_evolve_uses_sh300_pool_when_auto_resolving_symbols(monkeypatch) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, object] = {}

    def fake_resolve_run_symbols(source, symbols, **kwargs):
        seen["pool_name"] = kwargs["pool_name"]
        seen["symbols"] = symbols
        return ["600519"]

    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", fake_resolve_run_symbols)

    def fake_fetch_frames_for_cli(*_args, **kwargs):
        seen["benchmark_symbol"] = kwargs.get("benchmark_symbol")
        seen["days"] = kwargs.get("days")
        return {"600519": object()}

    monkeypatch.setattr(cli_mod, "_fetch_frames_for_cli", fake_fetch_frames_for_cli)

    class FakeConfig:
        enabled = True
        confidence_threshold = 0.8

    class FakeEvolution:
        def __init__(self, config_path: str):
            self.config_path = config_path
            self.config = FakeConfig()

        def evolve_parameters(self, strategy_name: str, frames):
            return None

    monkeypatch.setattr(
        "aqsp.strategies.auto_evolution.AutoEvolution",
        FakeEvolution,
    )

    args = Namespace(
        source="eastmoney",
        config="config/evolution_config.yaml",
        apply=False,
        output="",
    )

    exit_code = cli_mod.run_evolve(args)

    assert exit_code == 0
    assert seen["pool_name"] == "sh300"
    assert seen["symbols"] == ""
    assert seen["benchmark_symbol"] is None
    assert seen["days"] == 250


def test_run_scheduled_keeps_learning_weights_proposal_only() -> None:
    import aqsp.cli as cli_mod

    source = inspect.getsource(cli_mod.run_scheduled)

    assert "strategy_weights_from_ledger(args.ledger)" not in source
    assert "learner.compute_weights(ledger_df)" in source
    assert "weights: dict[str, float] = {}" in source
    assert "未应用到本次筛选" in source


def test_run_mine_factors_uses_sh300_pool_when_auto_resolving_symbols(
    monkeypatch,
) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, object] = {}

    def fake_resolve_run_symbols(source, symbols, **kwargs):
        seen["pool_name"] = kwargs["pool_name"]
        seen["symbols"] = symbols
        return ["600519"]

    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", fake_resolve_run_symbols)

    def fake_fetch_frames_for_cli(*_args, **kwargs):
        seen["benchmark_symbol"] = kwargs.get("benchmark_symbol")
        seen["days"] = kwargs.get("days")
        return {"600519": object()}

    monkeypatch.setattr(cli_mod, "_fetch_frames_for_cli", fake_fetch_frames_for_cli)

    class FakeMiner:
        def __init__(self, min_ic: float, min_ir: float):
            self.min_ic = min_ic
            self.min_ir = min_ir

        def mine_factors(self, frames):
            return []

    class FakeLibrary:
        def load(self) -> None:
            return None

        def add_factor(self, factor) -> bool:
            return False

        def save(self) -> None:
            return None

    monkeypatch.setattr(
        "aqsp.strategies.auto_factor_mining.AutoFactorMiner",
        FakeMiner,
    )
    monkeypatch.setattr(
        "aqsp.strategies.auto_factor_mining.FactorLibrary",
        FakeLibrary,
    )

    args = Namespace(
        source="eastmoney",
        min_ic=0.03,
        min_ir=0.5,
        output="",
        report="",
    )

    exit_code = cli_mod.run_mine_factors(args)

    assert exit_code == 0
    assert seen["pool_name"] == "sh300"
    assert seen["symbols"] == ""
    assert seen["benchmark_symbol"] is None
    assert seen["days"] == 250


def test_run_mine_factors_stores_results_as_inactive_research_candidates(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import aqsp.cli as cli_mod

    added: list[dict] = []

    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *_args, **_kwargs: ["600519"],
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {"600519": object()},
    )

    class FakeMiner:
        def __init__(self, min_ic: float, min_ir: float):
            self.min_ic = min_ic
            self.min_ir = min_ir

        def mine_factors(self, frames):
            return [
                {
                    "name": "demo_factor",
                    "category": "price",
                    "formula": "close / open",
                    "lookback_period": 5,
                    "params": {},
                    "evaluation": {
                        "ic_mean": 0.04,
                        "ic_ir": 0.8,
                        "sample_size": 120,
                    },
                }
            ]

    class FakeLibrary:
        def load(self) -> None:
            return None

        def add_factor(self, factor) -> bool:
            added.append(factor)
            return True

        def save(self) -> None:
            return None

    monkeypatch.setattr(
        "aqsp.strategies.auto_factor_mining.AutoFactorMiner",
        FakeMiner,
    )
    monkeypatch.setattr(
        "aqsp.strategies.auto_factor_mining.FactorLibrary",
        FakeLibrary,
    )

    output = tmp_path / "factors.json"
    args = Namespace(
        source="eastmoney",
        min_ic=0.03,
        min_ir=0.5,
        output=str(output),
        report="",
    )

    exit_code = cli_mod.run_mine_factors(args)

    assert exit_code == 0
    assert added[0]["name"] == "demo_factor"
    assert added[0]["is_active"] is False
    assert added[0]["status"] == "research_candidate"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload[0]["is_active"] is False


def test_factor_library_treats_missing_active_flag_as_inactive(tmp_path: Path) -> None:
    from aqsp.strategies.auto_factor_mining import FactorLibrary

    library = FactorLibrary(str(tmp_path / "factor_library.json"))
    library.factors = [
        {"name": "legacy_missing_flag"},
        {"name": "disabled_factor", "is_active": False},
        {"name": "approved_factor", "is_active": True},
    ]

    assert [factor["name"] for factor in library.get_active_factors()] == [
        "approved_factor"
    ]

    assert library.add_factor({"name": "new_research_factor"}) is True
    assert library.factors[-1]["is_active"] is False
    assert library.factors[-1]["status"] == "research_candidate"


def test_run_evolve_prefers_aqsp_symbols_when_configured(monkeypatch) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, object] = {}

    def fake_resolve_run_symbols(source, symbols, **kwargs):
        seen["symbols"] = symbols
        seen["pool_name"] = kwargs["pool_name"]
        return ["600519", "300750"]

    monkeypatch.setenv("AQSP_SYMBOLS", "600519,300750")
    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", fake_resolve_run_symbols)

    def fake_fetch_frames_for_cli(*_args, **kwargs):
        seen["benchmark_symbol"] = kwargs.get("benchmark_symbol")
        seen["days"] = kwargs.get("days")
        return {"600519": object(), "300750": object()}

    monkeypatch.setattr(cli_mod, "_fetch_frames_for_cli", fake_fetch_frames_for_cli)

    class FakeConfig:
        enabled = True
        confidence_threshold = 0.8

    class FakeEvolution:
        def __init__(self, config_path: str):
            self.config_path = config_path
            self.config = FakeConfig()

        def evolve_parameters(self, strategy_name: str, frames):
            return None

    monkeypatch.setattr(
        "aqsp.strategies.auto_evolution.AutoEvolution",
        FakeEvolution,
    )

    args = Namespace(
        source="eastmoney",
        config="config/evolution_config.yaml",
        apply=False,
        output="",
    )

    exit_code = cli_mod.run_evolve(args)

    assert exit_code == 0
    assert seen["symbols"] == "600519,300750"
    assert seen["pool_name"] == "sh300"
    assert seen["benchmark_symbol"] is None
    assert seen["days"] == 250


def test_run_evolve_writes_result_when_evolution_succeeds(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.strategies.auto_evolution import EvolutionResult

    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *_args, **_kwargs: ["600519"],
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {"600519": object()},
    )

    class FakeConfig:
        enabled = True
        confidence_threshold = 0.8

    class FakeEvolution:
        def __init__(self, config_path: str):
            self.config_path = config_path
            self.config = FakeConfig()

        def evolve_parameters(self, strategy_name: str, frames):
            return EvolutionResult(
                strategy_name=strategy_name,
                old_params={"momentum_weight": 0.3},
                new_params={"momentum_weight": 0.4},
                performance_improvement=0.12,
                confidence=0.85,
                timestamp=datetime(2026, 6, 3, 12, 0, 0),
                reason="performance_improvement",
            )

        def _apply_evolution(self, _result) -> None:
            raise AssertionError("should not apply when apply=False")

    monkeypatch.setattr(
        "aqsp.strategies.auto_evolution.AutoEvolution",
        FakeEvolution,
    )

    output = tmp_path / "evolution_result.json"
    args = Namespace(
        source="eastmoney",
        config="config/evolution_config.yaml",
        apply=False,
        output=str(output),
    )

    exit_code = cli_mod.run_evolve(args)

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["strategy_name"] == "composite"
    assert payload["new_params"]["momentum_weight"] == 0.4
    assert payload["performance_improvement"] == 0.12
    assert payload["status"] == "proposal_only"
    assert payload["applied"] is False


def test_run_evolve_apply_is_proposal_only(monkeypatch, tmp_path: Path) -> None:
    import aqsp.cli as cli_mod
    from aqsp.strategies.auto_evolution import EvolutionResult

    applied = False

    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *_args, **_kwargs: ["600519"],
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {"600519": object()},
    )

    class FakeConfig:
        enabled = True
        confidence_threshold = 0.8

    class FakeEvolution:
        def __init__(self, config_path: str):
            self.config_path = config_path
            self.config = FakeConfig()

        def evolve_parameters(self, strategy_name: str, frames):
            return EvolutionResult(
                strategy_name=strategy_name,
                old_params={"momentum_weight": 0.3},
                new_params={"momentum_weight": 0.4},
                performance_improvement=0.12,
                confidence=0.95,
                timestamp=datetime(2026, 6, 3, 12, 0, 0),
                reason="performance_improvement",
            )

        def _apply_evolution(self, _result) -> None:
            nonlocal applied
            applied = True

    monkeypatch.setattr(
        "aqsp.strategies.auto_evolution.AutoEvolution",
        FakeEvolution,
    )

    output = tmp_path / "evolution_result.json"
    args = Namespace(
        source="eastmoney",
        config="config/evolution_config.yaml",
        apply=True,
        output=str(output),
    )

    exit_code = cli_mod.run_evolve(args)

    assert exit_code == 0
    assert applied is False
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["applied"] is False
    assert payload["status"] == "proposal_only"


def test_run_optimize_apply_writes_proposal_without_touching_thresholds(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.optimizer.param_optimizer import OptimizationResult
    from aqsp.strategies.thresholds import Thresholds

    applied: list[dict[str, float]] = []

    monkeypatch.setattr(cli_mod, "load_thresholds", lambda: Thresholds(version="test"))
    monkeypatch.setattr(cli_mod, "_get_hs300_symbols", lambda _as_of=None: ["600519"])
    monkeypatch.setattr(cli_mod, "_walkforward_fetch_days", lambda *_args: 120)
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {
            "600519": object(),
            "000001": object(),
            "000002": object(),
            "000003": object(),
            "000004": object(),
        },
    )
    monkeypatch.setattr(
        cli_mod,
        "_apply_best_params",
        lambda params: applied.append(params),
    )

    class FakeFrame:
        empty = False

        def __getitem__(self, _key):
            return self

        def astype(self, _type):
            return self

        def __ge__(self, _other):
            return self

        def __le__(self, _other):
            return self

        def __and__(self, _other):
            return self

        @property
        def loc(self):
            return self

        def __len__(self):
            return 120

        def copy(self):
            return self

    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {
            symbol: FakeFrame()
            for symbol in ["600519", "000001", "000002", "000003", "000004"]
        },
    )

    monkeypatch.setattr(
        "aqsp.optimizer.param_optimizer.create_walkforward_evaluator",
        lambda **_kwargs: (lambda _params: 1.0),
    )

    class FakeOptimizer:
        def __init__(self, *_args, **_kwargs):
            return None

        def optimize(self, *_args, **_kwargs):
            return OptimizationResult(
                best_params={"composite.momentum_weight": 0.4},
                best_score=1.23,
                all_results=[],
                n_trials=1,
                method="grid",
            )

    monkeypatch.setattr(
        "aqsp.optimizer.param_optimizer.GridSearchOptimizer",
        FakeOptimizer,
    )

    output = tmp_path / "optimization_result.json"
    args = Namespace(
        method="grid",
        trials=1,
        symbols="600519,000001,000002,000003,000004",
        start="2026-01-01",
        end="2026-06-01",
        source="sqlite_db",
        engine="builtin",
        output=str(output),
        apply=True,
    )

    exit_code = cli_mod.run_optimize(args)

    assert exit_code == 0
    assert applied == []
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["best_params"] == {"composite.momentum_weight": 0.4}
    assert payload["status"] == "proposal_only"
    assert payload["applied"] is False
