from __future__ import annotations

from argparse import Namespace


def test_run_evolve_uses_sh300_pool_when_auto_resolving_symbols(monkeypatch) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, str] = {}

    def fake_resolve_run_symbols(source, symbols, **kwargs):
        seen["pool_name"] = kwargs["pool_name"]
        seen["symbols"] = symbols
        return ["600519"]

    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", fake_resolve_run_symbols)
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


def test_run_mine_factors_uses_sh300_pool_when_auto_resolving_symbols(
    monkeypatch,
) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, str] = {}

    def fake_resolve_run_symbols(source, symbols, **kwargs):
        seen["pool_name"] = kwargs["pool_name"]
        seen["symbols"] = symbols
        return ["600519"]

    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", fake_resolve_run_symbols)
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


def test_run_evolve_prefers_aqsp_symbols_when_configured(monkeypatch) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, str] = {}

    def fake_resolve_run_symbols(source, symbols, **kwargs):
        seen["symbols"] = symbols
        seen["pool_name"] = kwargs["pool_name"]
        return ["600519", "300750"]

    monkeypatch.setenv("AQSP_SYMBOLS", "600519,300750")
    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", fake_resolve_run_symbols)
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {"600519": object(), "300750": object()},
    )

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
