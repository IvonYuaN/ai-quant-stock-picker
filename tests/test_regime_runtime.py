from __future__ import annotations

from dataclasses import replace

import pandas as pd

from aqsp.regime.detector import RegimeDetector
from aqsp.regime.runtime import detect_runtime_regime
from aqsp.strategies.thresholds import RegimeThresholds, Thresholds


def _benchmark_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=80).strftime("%Y-%m-%d"),
            "close": [100 + idx for idx in range(80)],
            "volume": [1_000_000] * 80,
        }
    )


def test_detect_runtime_regime_uses_passed_thresholds_without_reload() -> None:
    thresholds = Thresholds(
        regime=replace(
            RegimeThresholds(),
            min_sample_size=5,
            trend_bull=0.001,
            momentum_bull=10.0,
            volatility_high=10.0,
        )
    )

    regime = detect_runtime_regime(
        {"000300": _benchmark_frame()},
        benchmark_symbol="000300",
        thresholds=thresholds,
    )

    assert regime == "stable_bull"


def test_regime_detector_reloads_thresholds_when_not_fixed(monkeypatch) -> None:
    first = Thresholds(
        regime=replace(
            RegimeThresholds(),
            min_sample_size=5,
            cooldown_hours=0,
            trend_bull=1.0,
            momentum_bull=10.0,
            volatility_high=10.0,
        )
    )
    second = replace(
        first,
        regime=replace(first.regime, trend_bull=0.001),
    )
    loaded = iter([first, first, second])
    monkeypatch.setattr("aqsp.regime.detector.load_thresholds", lambda: next(loaded))

    detector = RegimeDetector()

    first_regime = detector.detect({"000300": _benchmark_frame()}).name
    second_regime = detector.detect({"000300": _benchmark_frame()}).name

    assert first_regime == "stable_sideways"
    assert second_regime == "stable_bull"


def test_detect_runtime_regime_fails_closed_when_benchmark_missing() -> None:
    frame = _benchmark_frame()

    regime = detect_runtime_regime(
        {"600519": frame},
        benchmark_symbol="000300",
        thresholds=Thresholds(
            regime=replace(
                RegimeThresholds(),
                min_sample_size=5,
                trend_bull=0.001,
                momentum_bull=10.0,
                volatility_high=10.0,
            )
        ),
    )

    assert regime == ""
