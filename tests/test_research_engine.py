from __future__ import annotations

import pandas as pd

from aqsp.research_engine import (
    BuiltinWalkForwardEngine,
    resolve_walkforward_engine,
    WalkForwardEngineConfig,
)


def test_resolve_walkforward_engine_builtin() -> None:
    engine, resolution = resolve_walkforward_engine("builtin")

    assert isinstance(engine, BuiltinWalkForwardEngine)
    assert resolution.resolved == "builtin"
    assert resolution.mode == "native"


def test_resolve_walkforward_engine_akquant_compat_when_missing_package(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AQSP_AKQUANT_ALLOW_COMPAT", "true")
    monkeypatch.setattr("aqsp.research_engine._akquant_importable", lambda: False)

    engine, resolution = resolve_walkforward_engine("akquant")

    assert engine.engine_id == "akquant"
    assert resolution.resolved == "builtin"
    assert resolution.mode == "compat"


def test_resolve_walkforward_engine_auto_prefers_builtin_by_default(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AQSP_PREFER_AKQUANT", raising=False)

    engine, resolution = resolve_walkforward_engine("auto")

    assert engine.engine_id == "builtin"
    assert resolution.resolved == "builtin"


def test_builtin_walkforward_engine_runs_existing_tester(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class DummyTester:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def run(self, data, start_date=None, end_date=None):
            return "ok"

    monkeypatch.setattr("aqsp.backtest.walk_forward.WalkForwardTester", DummyTester)

    engine = BuiltinWalkForwardEngine()
    result = engine.run(
        strategy=object(),
        data={"600519": pd.DataFrame({"date": ["2024-01-01"], "close": [1.0]})},
        start_date="2024-01-01",
        end_date="2024-01-31",
        config=WalkForwardEngineConfig(
            train_days=120,
            test_days=30,
            purge_days=5,
            horizon_days=3,
        ),
    )

    assert result == "ok"
    assert captured["train_period_days"] == 120
    assert captured["test_period_days"] == 30
