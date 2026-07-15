from __future__ import annotations

from dataclasses import replace

import pandas as pd

from aqsp.regime.hmm_detector import HMMRegimeResult
from aqsp.regime.detector import RegimeDetector
from aqsp.regime.runtime import (
    detect_runtime_regime,
    detect_runtime_regime_context,
    format_runtime_regime_lines,
)
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
    class StubHMMDetector:
        def detect_regime(self, price_df: pd.DataFrame) -> HMMRegimeResult:
            return HMMRegimeResult(
                regime="bull",
                confidence=0.72,
                bull_prob=0.72,
                bear_prob=0.08,
                sideways_prob=0.20,
                volatility=0.01,
                trend=0.03,
            )

    thresholds = Thresholds(
        regime=replace(
            RegimeThresholds(),
            volatility_high=0.2,
        )
    )

    regime = detect_runtime_regime(
        {"000300": _benchmark_frame()},
        benchmark_symbol="000300",
        hmm_detector=StubHMMDetector(),
        thresholds=thresholds,
    )

    assert regime == "aggressive_bull"


def test_regime_detector_reloads_thresholds_when_not_fixed(monkeypatch) -> None:
    first = Thresholds(
        regime=replace(
            RegimeThresholds(),
            min_sample_size=5,
            cooldown_hours=0,
            volatility_high=10.0,
        )
    )
    second = replace(
        first,
        regime=replace(first.regime, volatility_high=0.2),
    )
    loaded = iter([first, first, second])
    monkeypatch.setattr("aqsp.regime.detector.load_thresholds", lambda: next(loaded))

    class StubHMMDetector:
        def detect_regime(self, price_df: pd.DataFrame) -> HMMRegimeResult:
            return HMMRegimeResult(
                regime="bull",
                confidence=0.8,
                bull_prob=0.8,
                bear_prob=0.1,
                sideways_prob=0.1,
                volatility=0.02,
                trend=0.01,
            )

    detector = RegimeDetector(hmm_detector=StubHMMDetector())

    first_regime = detector.detect({"000300": _benchmark_frame()}).name
    second_regime = detector.detect({"000300": _benchmark_frame()}).name

    assert first_regime == "aggressive_bull"
    assert second_regime == "volatile_bull"


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


def test_detect_runtime_regime_context_maps_hmm_regime_when_volatility_is_annualized() -> (
    None
):
    class StubHMMDetector:
        def detect_regime(self, price_df: pd.DataFrame) -> HMMRegimeResult:
            return HMMRegimeResult(
                regime="sideways",
                confidence=0.68,
                bull_prob=0.16,
                bear_prob=0.16,
                sideways_prob=0.68,
                volatility=0.02,
                trend=0.0,
            )

    context = detect_runtime_regime_context(
        {"000300": _benchmark_frame()},
        benchmark_symbol="000300",
        hmm_detector=StubHMMDetector(),
        thresholds=Thresholds(
            regime=replace(
                RegimeThresholds(),
                volatility_high=0.3,
            )
        ),
    )

    assert context.regime == "rotation_sideways"
    assert context.hmm_regime == "sideways"
    assert context.confidence == 0.68
    assert context.annualized_volatility > 0.3


def test_detect_runtime_regime_context_formats_runtime_summary_line() -> None:
    lines = format_runtime_regime_lines(
        detect_runtime_regime_context(
            {"000300": _benchmark_frame()},
            benchmark_symbol="000300",
            hmm_detector=type(
                "StubHMMDetector",
                (),
                {
                    "detect_regime": lambda self, price_df: HMMRegimeResult(
                        regime="bull",
                        confidence=0.72,
                        bull_prob=0.72,
                        bear_prob=0.08,
                        sideways_prob=0.20,
                        volatility=0.01,
                        trend=0.03,
                    )
                },
            )(),
            thresholds=Thresholds(
                regime=replace(
                    RegimeThresholds(),
                    volatility_high=0.3,
                )
            ),
        )
    )

    assert lines == (
        "运行判定: HMM 牛市 | 置信度 72% | 年化波动 15.9% | 映射 进攻牛市",
    )


def test_runtime_hmm_uses_configured_minimum_sample_size(monkeypatch) -> None:
    captured: dict[str, int] = {}

    class StubHMMDetector:
        def __init__(self, **kwargs: int) -> None:
            captured.update(kwargs)

        def detect_regime(self, price_df: pd.DataFrame) -> HMMRegimeResult:
            return HMMRegimeResult(
                regime="sideways",
                confidence=0.5,
                bull_prob=0.25,
                bear_prob=0.25,
                sideways_prob=0.5,
                volatility=0.01,
                trend=0.0,
            )

    monkeypatch.setattr("aqsp.regime.runtime.HMMRegimeDetector", StubHMMDetector)
    detect_runtime_regime_context(
        {"000300": _benchmark_frame()},
        benchmark_symbol="000300",
        thresholds=Thresholds(
            regime=replace(RegimeThresholds(), min_sample_size=7)
        ),
    )

    assert captured["min_data_points"] == 7
