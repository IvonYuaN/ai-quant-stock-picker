from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aqsp.strategies.mean_reversion import MeanReversionStrategy
from aqsp.strategies.base import StrategyConfig
from aqsp.strategies.thresholds import MeanReversionThresholds, Thresholds


@pytest.fixture
def strategy():
    return MeanReversionStrategy(
        StrategyConfig(name="mean_reversion"),
        thresholds=Thresholds(),
    )


def _make_df(n: int = 30, base_price: float = 10.0, trend: float = 0.0):
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    rng = np.random.default_rng(42)
    prices = base_price + np.arange(n) * trend + rng.normal(0, 0.3, n)
    prices = np.maximum(prices, 1.0)
    volumes = 1e6 + rng.normal(0, 2e5, n)
    volumes = np.maximum(volumes, 1000)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": prices,
            "high": prices * 1.02,
            "low": prices * 0.98,
            "close": prices,
            "volume": volumes,
            "symbol": "TEST",
            "name": "TEST",
        }
    )


def test_rsi_oversold_extreme():
    closes = np.array([100.0 - i * 2 for i in range(20)])
    result = MeanReversionStrategy._rsi_oversold(closes, 14)
    assert result > 0.5


def test_rsi_overbought():
    closes = np.array([100.0 + i * 2 for i in range(20)])
    result = MeanReversionStrategy._rsi_oversold(closes, 14)
    assert result == 0.0


def test_price_deviation_deep():
    closes = np.array([10.0] * 19 + [8.0])
    result = MeanReversionStrategy._price_deviation(closes, 20)
    assert result >= 0.5


def test_price_deviation_none():
    closes = np.array([10.0] * 19 + [10.5])
    result = MeanReversionStrategy._price_deviation(closes, 20)
    assert result == 0.0


def test_volume_confirmation_down_high_vol():
    volumes = np.array([1e6] * 19 + [2e6])
    closes = np.array([10.0] * 19 + [9.0])
    result = MeanReversionStrategy._volume_confirmation(volumes, closes, 20)
    assert result == 1.0


def test_volume_confirmation_up_low_vol():
    volumes = np.array([1e6] * 19 + [5e5])
    closes = np.array([10.0] * 19 + [10.5])
    result = MeanReversionStrategy._volume_confirmation(volumes, closes, 20)
    assert result == 0.0


def test_oversold_stock_high_score(strategy):
    df = _make_df(30, base_price=20.0, trend=-0.5)
    score = strategy._calculate_single_score(df)
    assert 0.0 <= score <= 1.0
    assert score > 0.3


def test_uptrend_stock_low_score(strategy):
    df = _make_df(30, base_price=10.0, trend=0.5)
    score = strategy._calculate_single_score(df)
    assert 0.0 <= score <= 1.0


def test_empty_df_returns_zero(strategy):
    df = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    assert strategy._calculate_single_score(df) == 0.0


def test_short_df_returns_zero(strategy):
    df = _make_df(5)
    assert strategy._calculate_single_score(df) == 0.0


def test_calculate_score_multiple_symbols(strategy):
    data = {"SYM1": _make_df(30), "SYM2": _make_df(30)}
    scores = strategy.calculate_score(data)
    assert set(scores.keys()) == {"SYM1", "SYM2"}
    for s in scores.values():
        assert 0.0 <= s <= 1.0


def test_strategy_uses_mean_reversion_enabled_threshold() -> None:
    strategy = MeanReversionStrategy(
        StrategyConfig(name="mean_reversion"),
        thresholds=Thresholds(
            mean_reversion=MeanReversionThresholds(enabled=False),
        ),
    )

    assert (
        strategy._calculate_single_score(_make_df(30, base_price=20.0, trend=-0.5))
        == 0.0
    )


def test_strategy_uses_configured_lookback_threshold() -> None:
    strategy = MeanReversionStrategy(
        StrategyConfig(name="mean_reversion"),
        thresholds=Thresholds(
            mean_reversion=MeanReversionThresholds(lookback_days=40),
        ),
    )

    assert (
        strategy._calculate_single_score(_make_df(30, base_price=20.0, trend=-0.5))
        == 0.0
    )
