from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aqsp.strategies.volume import VolumeBreakoutStrategy
from aqsp.strategies.base import StrategyConfig
from aqsp.strategies.thresholds import Thresholds


@pytest.fixture
def strategy():
    return VolumeBreakoutStrategy(
        StrategyConfig(name="volume_breakout"),
        thresholds=Thresholds(),
    )


def _make_df(n: int = 60, base_price: float = 10.0, base_vol: float = 1e6):
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    rng = np.random.default_rng(42)
    prices = base_price + rng.normal(0, 0.5, n).cumsum()
    prices = np.maximum(prices, 1.0)
    volumes = base_vol + rng.normal(0, base_vol * 0.2, n)
    volumes = np.maximum(volumes, 1000)
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": prices,
        "high": prices * 1.02,
        "low": prices * 0.98,
        "close": prices,
        "volume": volumes,
        "symbol": "TEST",
        "name": "TEST",
    })


def test_volume_surge_high_volume(strategy):
    df = _make_df(60, base_vol=1e6)
    df.iloc[-1, df.columns.get_loc("volume")] = 3e6
    score = strategy._calculate_single_score(df)
    assert 0.0 <= score <= 1.0
    assert score > 0.3


def test_volume_surge_low_volume(strategy):
    df = _make_df(60, base_vol=1e6)
    df.iloc[-1, df.columns.get_loc("volume")] = 500_000
    score = strategy._calculate_single_score(df)
    assert 0.0 <= score <= 1.0


def test_price_breakout_at_high(strategy):
    df = _make_df(60, base_price=10.0)
    df.iloc[-1, df.columns.get_loc("close")] = 20.0
    score = strategy._calculate_single_score(df)
    assert score > 0.3


def test_price_below_ma(strategy):
    df = _make_df(60, base_price=10.0)
    df.iloc[-1, df.columns.get_loc("close")] = 5.0
    score = strategy._calculate_single_score(df)
    assert 0.0 <= score <= 1.0


def test_empty_df_returns_zero(strategy):
    df = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    assert strategy._calculate_single_score(df) == 0.0


def test_short_df_returns_zero(strategy):
    df = _make_df(5)
    assert strategy._calculate_single_score(df) == 0.0


def test_calculate_score_multiple_symbols(strategy):
    data = {"SYM1": _make_df(60), "SYM2": _make_df(60)}
    scores = strategy.calculate_score(data)
    assert set(scores.keys()) == {"SYM1", "SYM2"}
    for s in scores.values():
        assert 0.0 <= s <= 1.0


def test_volume_surge_static():
    volumes = np.array([1e6] * 20 + [2e6])
    result = VolumeBreakoutStrategy._volume_surge(volumes, 20, 1.5)
    assert result > 0.2


def test_price_breakout_static():
    closes = np.array([10.0] * 19 + [15.0])
    result = VolumeBreakoutStrategy._price_breakout(closes, 20)
    assert result == 1.0


def test_volume_price_correlation_static():
    closes = np.arange(11, dtype=float)
    volumes = np.arange(11, dtype=float) * 1000
    result = VolumeBreakoutStrategy._volume_price_correlation(closes, volumes, 10)
    assert 0.0 <= result <= 1.0
