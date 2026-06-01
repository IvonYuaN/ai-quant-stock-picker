from __future__ import annotations

import pytest
import pandas as pd
import numpy as np

from aqsp.strategies.base import StrategyConfig
from aqsp.strategies.momentum import MomentumStrategy
from aqsp.strategies.quality import QualityStrategy
from aqsp.strategies.value import ValueStrategy
from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.thresholds import load_thresholds


def test_load_thresholds():
    thresholds = load_thresholds()
    assert thresholds.version == "1.1.0"
    assert thresholds.last_walkforward_run == "2026-05-30"
    assert thresholds.momentum.lookback_days == 60
    assert thresholds.momentum.weights.momentum == 0.4
    assert thresholds.momentum.weights.trend == 0.3
    assert thresholds.momentum.weights.rsi == 0.3
    assert thresholds.quality.enabled is False
    assert thresholds.value.enabled is False
    assert thresholds.composite.momentum_weight == 0.3
    assert thresholds.composite.quality_weight == 0.2
    assert thresholds.composite.value_weight == 0.2
    assert thresholds.composite.triple_rise_weight == 0.3
    assert thresholds.volume.enabled is True
    assert thresholds.regime.volatility_high == 0.3
    assert thresholds.regime.min_sample_size == 20
    assert thresholds.regime.cooldown_hours == 24


def test_momentum_strategy():
    dates = pd.date_range("2026-01-01", periods=60, freq="D")
    prices = np.linspace(10, 15, 60)

    df = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": prices,
            "open": prices,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "volume": 100000,
        }
    )

    config = StrategyConfig(name="momentum")
    strategy = MomentumStrategy(config)

    assert strategy.id == "momentum"
    assert strategy.version == "1.1.0"
    assert strategy.hypothesis != ""

    scores = strategy.calculate_score({"600000": df})
    assert "600000" in scores
    assert 0 <= scores["600000"] <= 1


def test_momentum_strategy_empty_data():
    config = StrategyConfig(name="momentum")
    strategy = MomentumStrategy(config)

    scores = strategy.calculate_score({"600000": pd.DataFrame()})
    assert scores["600000"] == 0.0


def test_momentum_evaluate_returns_signal_score():
    dates = pd.date_range("2026-01-01", periods=60, freq="D")
    prices = np.linspace(10, 15, 60)

    df = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": prices,
            "volume": 100000,
        }
    )

    config = StrategyConfig(name="momentum")
    strategy = MomentumStrategy(config)

    signal = strategy.evaluate(df, "stable_bull")
    assert signal.strategy_id == "momentum"
    assert isinstance(signal.score, float)
    assert isinstance(signal.fired, bool)


def test_base_strategy_hypothesis_required():
    from aqsp.strategies.base import BaseStrategy

    class BadStrategy(BaseStrategy):
        def calculate_score(self, data):
            return {}

    config = StrategyConfig(name="bad")
    with pytest.raises(ValueError, match="hypothesis"):
        BadStrategy(config, id="bad", version="1.0", hypothesis="")


def test_base_strategy_protocol_fields():
    from aqsp.strategies.base import BaseStrategy

    class TestStrategy(BaseStrategy):
        def calculate_score(self, data):
            return {}

    config = StrategyConfig(name="test")
    strategy = TestStrategy(
        config,
        id="test_v1",
        version="1.0",
        hypothesis="测试策略假设",
        regime_required=("stable_bull",),
    )

    assert strategy.id == "test_v1"
    assert strategy.version == "1.0"
    assert strategy.hypothesis == "测试策略假设"
    assert strategy.regime_required == ("stable_bull",)


def test_base_strategy_regime_filter():
    from aqsp.strategies.base import BaseStrategy

    class TestStrategy(BaseStrategy):
        def calculate_score(self, data):
            return {}

        def _calculate_single_score(self, df):
            return 0.8

    config = StrategyConfig(name="test")
    strategy = TestStrategy(
        config,
        id="test_v1",
        version="1.0",
        hypothesis="测试假设",
        regime_required=("stable_bull",),
    )

    signal = strategy.evaluate(pd.DataFrame({"close": [1, 2]}), "volatile_bear")
    assert signal.fired is False
    assert signal.score == 0.0

    signal = strategy.evaluate(pd.DataFrame({"close": [1, 2]}), "stable_bull")
    assert signal.fired is True
    assert signal.score == 0.8


def test_quality_strategy():
    df = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "roe": [0.15, 0.18],
            "roa": [0.08, 0.09],
            "debt_ratio": [0.4, 0.35],
            "operating_margin": [0.08, 0.09],
        }
    )

    config = StrategyConfig(name="quality")
    strategy = QualityStrategy(config)

    assert strategy.id == "quality"
    assert strategy.hypothesis != ""

    scores = strategy.calculate_score({"600000": df})
    assert "600000" in scores
    assert 0 <= scores["600000"] <= 1


def test_value_strategy():
    df = pd.DataFrame(
        {
            "date": ["2026-01-01"],
            "pe": [15],
            "pb": [2],
            "dividend_yield": [0.03],
        }
    )

    config = StrategyConfig(name="value")
    strategy = ValueStrategy(config)

    assert strategy.id == "value"
    assert strategy.hypothesis != ""

    scores = strategy.calculate_score({"600000": df})
    assert "600000" in scores
    assert 0 <= scores["600000"] <= 1


def test_composite_strategy_momentum_only():
    dates = pd.date_range("2026-01-01", periods=60, freq="D")
    prices = np.linspace(10, 15, 60)

    df = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": prices,
            "open": prices,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "volume": 100000,
        }
    )

    config = StrategyConfig(name="composite")
    strategy = CompositeStrategy(config)

    assert strategy.id == "composite"
    assert strategy.hypothesis != ""
    assert strategy.thresholds.quality.enabled is False
    assert strategy.thresholds.value.enabled is False

    scores = strategy.calculate_score({"600000": df})
    assert "600000" in scores
    assert 0 <= scores["600000"] <= 1


def test_composite_strategy_detailed_momentum_only():
    dates = pd.date_range("2026-01-01", periods=60, freq="D")
    prices = np.linspace(10, 12, 60)

    df = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": prices,
            "open": prices,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "volume": 100000,
        }
    )

    config = StrategyConfig(name="composite")
    strategy = CompositeStrategy(config)

    detailed = strategy.calculate_detailed_scores({"600000": df, "600001": df})
    assert "600000" in detailed
    assert "momentum" in detailed["600000"]
    assert "total" in detailed["600000"]
    assert "quality" not in detailed["600000"]
    assert "value" not in detailed["600000"]


def test_composite_strategy_select_stocks():
    dates = pd.date_range("2026-01-01", periods=60, freq="D")

    data = {}
    for i in range(15):
        prices = np.linspace(10, 10 + i * 0.5, 60)
        df = pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "close": prices,
                "open": prices,
                "high": prices * 1.01,
                "low": prices * 0.99,
                "volume": 100000,
            }
        )
        data[f"6000{i:02d}"] = df

    config = StrategyConfig(name="composite")
    strategy = CompositeStrategy(config)

    selected = strategy.select_stocks(data, n=10)
    assert len(selected) <= 10


def test_base_strategy_rank():
    from aqsp.strategies.base import BaseStrategy

    class TestStrategy(BaseStrategy):
        def calculate_score(self, data):
            return {}

    config = StrategyConfig(name="test")
    strategy = TestStrategy(
        config,
        id="test",
        version="1.0",
        hypothesis="测试假设",
    )
    scores = {"A": 0.8, "B": 0.5, "C": 0.9}
    ranked = strategy.rank(scores)

    assert ranked == ["C", "A", "B"]


def test_base_strategy_select_top():
    from aqsp.strategies.base import BaseStrategy

    class TestStrategy(BaseStrategy):
        def calculate_score(self, data):
            return {}

    config = StrategyConfig(name="test")
    strategy = TestStrategy(
        config,
        id="test",
        version="1.0",
        hypothesis="测试假设",
    )
    scores = {"A": 0.8, "B": 0.5, "C": 0.9, "D": 0.7}
    top = strategy.select_top(scores, n=2)

    assert len(top) == 2
    assert "C" in top
    assert "A" in top
