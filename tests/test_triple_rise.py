from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aqsp.strategies.triple_rise import TripleRiseStrategy
from aqsp.strategies.base import StrategyConfig
from aqsp.strategies.thresholds import Thresholds


@pytest.fixture
def strategy():
    return TripleRiseStrategy(
        StrategyConfig(name="triple_rise"),
        thresholds=Thresholds(),
    )


def _make_df(n: int = 25, base_price: float = 10.0, trend: float = 0.0):
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    rng = np.random.default_rng(42)
    prices = base_price + np.arange(n) * trend + rng.normal(0, 0.2, n)
    prices = np.maximum(prices, 1.0)
    volumes = 1e6 + rng.normal(0, 2e5, n)
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


def test_triple_rise_true():
    closes = np.array([10.0, 10.0, 10.0, 10.5, 11.0, 11.5])
    result = TripleRiseStrategy._triple_rise(closes)
    assert result > 0.3


def test_triple_rise_false():
    closes = np.array([10.0, 10.5, 10.0, 10.5, 10.0, 10.5])
    result = TripleRiseStrategy._triple_rise(closes)
    assert result == 0.0


def test_v_bottom_valid():
    closes = np.array([15.0, 14.0, 13.0, 12.0, 11.0, 10.0, 9.0, 10.0, 11.0, 12.0,
                        13.0, 14.0, 15.0, 14.0, 13.0, 12.0, 11.0, 10.5, 11.0, 12.0])
    result = TripleRiseStrategy._v_bottom(closes, 20)
    assert result > 0.0


def test_v_bottom_no_dip():
    closes = np.array([10.0 + i * 0.1 for i in range(20)])
    result = TripleRiseStrategy._v_bottom(closes, 20)
    assert result == 0.0


def test_volume_confirmation_up():
    volumes = np.array([1e6] * 17 + [1.5e6, 1.5e6, 1.5e6])
    closes = np.array([10.0] * 17 + [10.2, 10.4, 10.6])
    result = TripleRiseStrategy._volume_confirmation(volumes, closes)
    assert result > 0.5


def test_volume_confirmation_down():
    volumes = np.array([1e6] * 17 + [5e5, 5e5, 5e5])
    closes = np.array([10.0] * 17 + [9.8, 9.6, 9.4])
    result = TripleRiseStrategy._volume_confirmation(volumes, closes)
    assert result == 0.0


def test_empty_df_returns_zero(strategy):
    df = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    assert strategy._calculate_single_score(df) == 0.0


def test_short_df_returns_zero(strategy):
    df = _make_df(5)
    assert strategy._calculate_single_score(df) == 0.0


def test_uptrend_score(strategy):
    df = _make_df(25, base_price=10.0, trend=0.3)
    score = strategy._calculate_single_score(df)
    assert 0.0 <= score <= 1.0


def test_calculate_score_multiple(strategy):
    data = {"SYM1": _make_df(25), "SYM2": _make_df(25)}
    scores = strategy.calculate_score(data)
    assert set(scores.keys()) == {"SYM1", "SYM2"}
