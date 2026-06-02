from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from aqsp.research_engine import (
    AkquantWalkForwardEngine,
    BuiltinWalkForwardEngine,
    WalkForwardEngineConfig,
    resolve_walkforward_engine,
)


def _make_sample_data(
    *,
    start: str = "2024-01-01",
    periods: int = 12,
    open_price: float = 10.0,
) -> pd.DataFrame:
    dates = pd.date_range(start=start, periods=periods, freq="B")
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": [open_price + i * 0.1 for i in range(periods)],
            "high": [open_price + i * 0.1 + 0.5 for i in range(periods)],
            "low": [open_price + i * 0.1 - 0.2 for i in range(periods)],
            "close": [open_price + i * 0.1 + 0.2 for i in range(periods)],
            "volume": [1_000_000] * periods,
        }
    )


class _DummyStrategy:
    def select_stocks(
        self,
        signal_data: dict[str, pd.DataFrame],
        n: int = 10,
    ) -> list[str]:
        return sorted(signal_data)[:n]


def test_resolve_walkforward_engine_builtin() -> None:
    engine, resolution = resolve_walkforward_engine("builtin")

    assert isinstance(engine, BuiltinWalkForwardEngine)
    assert resolution.resolved == "builtin"
    assert resolution.mode == "native"


def test_resolve_walkforward_engine_akquant_native_when_package_available(
    monkeypatch,
) -> None:
    monkeypatch.setattr("aqsp.research_engine._akquant_importable", lambda: True)

    engine, resolution = resolve_walkforward_engine("akquant")

    assert isinstance(engine, AkquantWalkForwardEngine)
    assert resolution.resolved == "akquant"
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


def test_akquant_walkforward_engine_runs_native_bridge(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeAkquant:
        @staticmethod
        def run_backtest(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                trades_df=pd.DataFrame(
                    {
                        "symbol": ["AAA"],
                        "entry_time": [
                            pd.Timestamp("2024-01-08 09:30:00", tz="Asia/Shanghai")
                        ],
                        "exit_time": [
                            pd.Timestamp("2024-01-10 15:00:00", tz="Asia/Shanghai")
                        ],
                        "entry_price": [11.0],
                        "exit_price": [12.0],
                        "exit_tag": ["wf_hold_period_close"],
                    }
                )
            )

    monkeypatch.setattr("aqsp.research_engine._akquant_importable", lambda: True)
    monkeypatch.setattr("aqsp.research_engine._import_akquant_module", lambda: FakeAkquant)

    engine = AkquantWalkForwardEngine()
    result = engine.run(
        strategy=_DummyStrategy(),
        data={"AAA": _make_sample_data()},
        start_date="2024-01-01",
        end_date="2024-01-31",
        config=WalkForwardEngineConfig(
            train_days=5,
            test_days=3,
            purge_days=1,
            horizon_days=2,
        ),
    )

    assert calls
    assert calls[0]["symbols"] == ["AAA"]
    assert calls[0]["fill_policy"] == {"price_basis": "open", "temporal": "same_cycle"}
    assert result.overall.trades == 1
    assert result.periods[0].trades == 1


def test_akquant_walkforward_engine_marks_not_executable_without_native_run(
    monkeypatch,
) -> None:
    called = False

    def fake_run_backtest(**kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(trades_df=pd.DataFrame())

    locked = _make_sample_data()
    locked.loc[6, "open"] = 12.0
    locked.loc[6, "high"] = 12.0
    locked.loc[6, "low"] = 12.0
    locked.loc[5, "close"] = 10.0

    monkeypatch.setattr("aqsp.research_engine._akquant_importable", lambda: True)
    monkeypatch.setattr(
        "aqsp.research_engine._import_akquant_module",
        lambda: SimpleNamespace(run_backtest=fake_run_backtest),
    )

    engine = AkquantWalkForwardEngine()
    result = engine.run(
        strategy=_DummyStrategy(),
        data={"AAA": locked},
        start_date="2024-01-01",
        end_date="2024-01-31",
        config=WalkForwardEngineConfig(
            train_days=5,
            test_days=3,
            purge_days=1,
            horizon_days=2,
        ),
    )

    assert called is False
    assert result.overall.not_executable == 1
    assert result.overall.trades == 0
