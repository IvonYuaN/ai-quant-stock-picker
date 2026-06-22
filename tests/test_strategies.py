from __future__ import annotations

import pytest
import pandas as pd
import numpy as np

from aqsp.strategies.base import StrategyConfig
from aqsp.strategies.momentum import MomentumStrategy
from aqsp.strategies.quality import QualityStrategy
from aqsp.strategies.value import ValueStrategy
from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.closing_premium import (
    ClosingPremiumStrategy,
    PremiumSignal,
    format_closing_signals,
)
from aqsp.strategies.n_rebound import NReboundStrategy, detect_n_rebound_signal
from aqsp.strategies.thresholds import load_thresholds


def test_load_thresholds():
    thresholds = load_thresholds()
    assert thresholds.version == "1.1.9"
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
    assert thresholds.triple_rise.enabled is True
    assert thresholds.triple_rise.lookback_days == 25
    assert thresholds.triple_rise.weights.triple_rise == 0.4
    assert thresholds.volume.enabled is True
    assert thresholds.regime.volatility_high == 0.3
    assert thresholds.regime.min_sample_size == 20
    assert thresholds.regime.cooldown_hours == 24
    assert thresholds.regime.strategy_weights["stable_bull"].momentum == 1.2
    assert thresholds.regime.strategy_weights["volatile_bear"].triple_rise == 0.7
    assert thresholds.n_rebound.enabled is True
    assert thresholds.n_rebound.lookback_days == 30
    assert thresholds.internet_strategy.volume_breakout_score == 18.0
    assert thresholds.risk.dynamic_stop_atr_multiplier == 2.0
    assert thresholds.risk.dynamic_stop_fallback_pct == 0.05
    assert thresholds.risk.dynamic_stop_recent_low_days == 5
    assert thresholds.risk.dynamic_stop_trailing_pct == 0.03
    assert thresholds.risk.dynamic_stop_support_lookback == 20


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
    assert strategy.version == "1.1.9"
    assert strategy.hypothesis != ""

    scores = strategy.calculate_score({"600000": df})
    assert "600000" in scores
    assert 0 <= scores["600000"] <= 1


def test_momentum_strategy_empty_data():
    config = StrategyConfig(name="momentum")
    strategy = MomentumStrategy(config)

    scores = strategy.calculate_score({"600000": pd.DataFrame()})
    assert scores["600000"] == 0.0


def test_momentum_rsi_score_is_directional_when_rsi_is_high() -> None:
    config = StrategyConfig(name="momentum")
    strategy = MomentumStrategy(config)

    assert strategy._calculate_rsi_score(pd.DataFrame({"close": [10.0] * 20})) == 1.0


def test_momentum_rsi_score_is_not_mean_reversion_when_rsi_is_low() -> None:
    config = StrategyConfig(name="momentum")
    strategy = MomentumStrategy(config)

    prices = [20.0 - i * 0.5 for i in range(20)]

    assert strategy._calculate_rsi_score(pd.DataFrame({"close": prices})) == 0.0


def test_momentum_return_score_does_not_go_negative_when_return_is_negative() -> None:
    config = StrategyConfig(name="momentum")
    strategy = MomentumStrategy(config)

    score = strategy._calculate_momentum_score(total_return=-0.10, volatility=0.0)

    assert 0.0 <= score <= 1.0
    assert score == pytest.approx(0.5)


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


def test_composite_strategy_applies_regime_score_multiplier():
    from dataclasses import replace

    from aqsp.strategies.thresholds import Thresholds

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
    thresholds = Thresholds()
    thresholds = replace(
        thresholds,
        regime=replace(
            thresholds.regime,
            adjustments={"test_half": 0.5},
        ),
    )
    strategy = CompositeStrategy(
        StrategyConfig(name="composite"), thresholds=thresholds
    )

    base_score = strategy.calculate_score({"600000": df}, regime="unknown")["600000"]
    half_score = strategy.calculate_score({"600000": df}, regime="test_half")["600000"]
    detailed = strategy.calculate_detailed_scores({"600000": df}, regime="test_half")[
        "600000"
    ]

    assert half_score == pytest.approx(base_score * 0.5)
    assert detailed["regime_multiplier"] == 0.5
    assert detailed["total"] == pytest.approx(half_score)


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


def test_closing_premium_strategy_init():
    config = StrategyConfig(name="closing_premium")
    strategy = ClosingPremiumStrategy(config)

    assert strategy.id == "closing_premium"
    assert strategy.name == "closing_premium"
    assert strategy.version == "1.1.9"
    assert strategy.hypothesis == "尾盘异动股票往往有资金介入，次日有溢价空间"
    assert "stable_bull" in strategy.regime_required
    assert "stable_sideways" in strategy.regime_required
    assert "volatile_bull" in strategy.regime_required


def test_closing_premium_strategy_with_thresholds():
    thresholds = load_thresholds()
    config = StrategyConfig(name="closing_premium")
    strategy = ClosingPremiumStrategy(config, thresholds=thresholds)

    assert strategy.cfg.enabled is True
    assert strategy.cfg.min_change_pct == 2.0
    assert strategy.cfg.max_change_pct == 7.0
    assert strategy.cfg.min_score == 65.0


def test_closing_premium_calculate_score_empty_data():
    config = StrategyConfig(name="closing_premium")
    strategy = ClosingPremiumStrategy(config)

    scores = strategy.calculate_score({"600000": pd.DataFrame()})
    assert scores["600000"] == 0.0


def test_closing_premium_calculate_score_insufficient_data():
    config = StrategyConfig(name="closing_premium")
    strategy = ClosingPremiumStrategy(config)

    df = pd.DataFrame(
        {
            "date": ["2026-01-01"],
            "close": [10.0],
            "open": [9.8],
            "high": [10.2],
            "low": [9.7],
            "volume": [100000],
        }
    )
    scores = strategy.calculate_score({"600000": df})
    assert scores["600000"] == 0.0


def test_closing_premium_calculate_score_normal_data():
    config = StrategyConfig(name="closing_premium")
    strategy = ClosingPremiumStrategy(config)

    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    prices = np.linspace(10, 12, 30)
    rng = np.random.default_rng(42)
    df = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": prices,
            "open": prices * 0.99,
            "high": prices * 1.01,
            "low": prices * 0.98,
            "volume": rng.integers(50000, 200000, 30),
        }
    )
    scores = strategy.calculate_score({"600000": df})
    assert "600000" in scores
    assert 0 <= scores["600000"] <= 1


def test_closing_premium_analyze_closing_returns_signals():
    config = StrategyConfig(name="closing_premium")
    strategy = ClosingPremiumStrategy(config)

    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    base_price = 10.0
    prices = [base_price]
    for i in range(1, 30):
        if i >= 25:
            prices.append(prices[-1] * 1.04)
        else:
            prices.append(prices[-1] * 1.01)

    df = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": prices,
            "open": [p * 0.98 for p in prices],
            "high": [p * 1.02 for p in prices],
            "low": [p * 0.97 for p in prices],
            "volume": [100000 + i * 10000 for i in range(30)],
            "name": ["测试股票"] * 30,
        }
    )
    signals = strategy.analyze_closing({"600000": df})
    assert isinstance(signals, list)


def test_closing_premium_premium_signal_dataclass():
    signal = PremiumSignal(
        symbol="600000",
        name="测试股票",
        signal_type="尾盘拉升",
        score=75.0,
        current_price=10.5,
        entry_price=10.3,
        stop_loss=9.8,
        take_profit_1=11.0,
        take_profit_2=11.5,
        reasons=("涨幅适中", "量价齐升"),
        risks=("高开风险",),
        confidence=0.75,
        holding_days=2,
        expected_return=6.8,
    )

    assert signal.symbol == "600000"
    assert signal.name == "测试股票"
    assert signal.score == 75.0
    assert signal.confidence == 0.75
    assert signal.holding_days == 2
    assert len(signal.reasons) == 2
    assert len(signal.risks) == 1


def test_format_closing_signals_empty():
    result = format_closing_signals([])
    assert "未发现符合条件的股票" in result


def test_format_closing_signals_with_signals():
    signals = [
        PremiumSignal(
            symbol="600000",
            name="测试股票A",
            signal_type="尾盘拉升",
            score=80.0,
            current_price=10.5,
            entry_price=10.3,
            stop_loss=9.8,
            take_profit_1=11.0,
            take_profit_2=11.5,
            reasons=("涨幅适中",),
            risks=(),
            confidence=0.8,
            holding_days=2,
            expected_return=6.8,
        ),
        PremiumSignal(
            symbol="600001",
            name="测试股票B",
            signal_type="量价突破",
            score=75.0,
            current_price=15.2,
            entry_price=15.0,
            stop_loss=14.2,
            take_profit_1=16.0,
            take_profit_2=16.5,
            reasons=("量价齐升", "MACD金叉"),
            risks=("涨幅较大",),
            confidence=0.7,
            holding_days=3,
            expected_return=6.7,
        ),
    ]

    result = format_closing_signals(signals, top_n=2)
    assert "尾盘走强观察" in result
    assert "600000" in result
    assert "600001" in result
    assert "测试股票A" in result
    assert "测试股票B" in result
    assert "复核清单" in result
    assert "策略推荐" not in result
    assert "推荐 Top" not in result
    assert "建议入场" not in result


def test_closing_premium_evaluate_regime_filter():
    config = StrategyConfig(name="closing_premium")
    strategy = ClosingPremiumStrategy(config)

    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    prices = np.linspace(10, 12, 30)
    df = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": prices,
            "open": prices * 0.99,
            "high": prices * 1.01,
            "low": prices * 0.98,
            "volume": [100000] * 30,
        }
    )

    signal = strategy.evaluate(df, "volatile_bear")
    assert signal.fired is False
    assert signal.score == 0.0

    signal = strategy.evaluate(df, "stable_bull")
    assert isinstance(signal.fired, bool)


def _make_n_rebound_df() -> pd.DataFrame:
    base_closes = [9.7 + i * 0.015 for i in range(20)]
    pattern_closes = [
        10.0,
        10.05,
        10.08,
        10.12,
        10.18,
        10.22,
        10.28,
        10.35,
        10.42,
        10.48,
        10.55,
        10.65,
        11.72,
        11.45,
        11.34,
        11.33,
        11.34,
        11.35,
        11.35,
        11.34,
        11.34,
        11.34,
    ]
    closes = base_closes + pattern_closes
    base_volumes = [1_000_000] * len(base_closes)
    pattern_volumes = [
        1_000_000,
        1_010_000,
        1_000_000,
        1_020_000,
        1_030_000,
        1_040_000,
        1_050_000,
        1_060_000,
        1_050_000,
        1_040_000,
        1_020_000,
        1_100_000,
        2_400_000,
        1_100_000,
        980_000,
        920_000,
        900_000,
        880_000,
        850_000,
        820_000,
        800_000,
        780_000,
    ]
    volumes = base_volumes + pattern_volumes
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    df = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "symbol": ["600000"] * len(closes),
            "name": ["浦发银行"] * len(closes),
            "open": [price * 0.99 for price in closes],
            "high": [price * 1.01 for price in closes],
            "low": [price * 0.985 for price in closes],
            "close": closes,
            "volume": volumes,
            "amount": [price * volume * 100 for price, volume in zip(closes, volumes)],
        }
    )
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    return df


def test_detect_n_rebound_signal() -> None:
    thresholds = load_thresholds().n_rebound
    signal = detect_n_rebound_signal(_make_n_rebound_df(), thresholds=thresholds)

    assert signal is not None
    assert signal.score >= thresholds.min_score
    assert signal.days_since_limit_up <= thresholds.max_days_since_limit_up
    assert signal.pullback_pct >= thresholds.pullback_min_pct
    assert any("涨停后回调" in reason for reason in signal.reasons)


def test_n_rebound_strategy_scores_detected_pattern() -> None:
    strategy = NReboundStrategy()
    scores = strategy.calculate_score({"600000": _make_n_rebound_df()})

    assert 0.0 < scores["600000"] <= 1.0
