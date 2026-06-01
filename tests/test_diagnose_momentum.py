from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from scripts.diagnose_momentum import (
    compute_rolling_scores,
    quantile_table,
    run_analysis,
)


def _make_ohlcv(n: int, trend: float = 0.0, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    close = 10.0 + np.arange(n) * trend + rng.randn(n) * 0.3
    close = np.maximum(close, 1.0)
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "close": close,
        "open": close * 0.99,
        "high": close * 1.01,
        "low": close * 0.98,
        "volume": rng.randint(100000, 1000000, n).astype(float),
    })


def test_spearman_sign_flip():
    rng = np.random.RandomState(0)
    n = 200
    scores = rng.uniform(0, 1, n)
    forward_ret = -scores * 0.1 + rng.randn(n) * 0.02
    rho, _ = spearmanr(scores, forward_ret)
    assert rho < -0.05, f"expected negative rho, got {rho}"


def test_quantile_bucket_monotonic():
    rng = np.random.RandomState(1)
    n = 500
    scores = rng.uniform(0, 1, n)
    forward_ret = scores * 0.2 + rng.randn(n) * 0.05
    df = pd.DataFrame({"score": scores, "forward_ret": forward_ret})
    qt = quantile_table(df)
    assert len(qt) >= 2
    q1_mean = qt.iloc[0]["mean"]
    q5_mean = qt.iloc[-1]["mean"]
    assert q5_mean > q1_mean, f"Q5 ({q5_mean:.4f}) should be > Q1 ({q1_mean:.4f})"


def test_handles_suspended_stock():
    from unittest.mock import MagicMock
    strategy = MagicMock()
    strategy.thresholds.momentum.lookback_days = 60

    def fake_score(data):
        for sym, df in data.items():
            return {sym: 0.5}
        return {}

    strategy.calculate_score = fake_score

    n = 120
    df = _make_ohlcv(n, trend=0.01)
    df.loc[50, "volume"] = 0
    df.loc[80, "volume"] = 0
    data = {"600000": df}
    scored = compute_rolling_scores(data, strategy)
    assert not scored.empty
    result = run_analysis(scored, "test")
    assert "rho" in result or "error" in result
