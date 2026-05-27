from __future__ import annotations

import pandas as pd

from aqsp.models import ScreeningConfig
from aqsp.strategy import screen_universe


def _frame(symbol: str, drift: float, volume_boost: float = 1.0) -> pd.DataFrame:
    rows = []
    close = 10.0
    for i in range(120):
        close *= 1 + drift
        if i == 119:
            close *= 1.025
        open_ = close * 0.99
        high = close * 1.02
        low = close * 0.985
        volume = 1_000_000 * (volume_boost if i == 119 else 1)
        rows.append(
            {
                "date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=i),
                "symbol": symbol,
                "name": symbol,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "amount": close * volume * 100,
            }
        )
    return pd.DataFrame(rows)


def test_screen_prefers_strong_trend() -> None:
    frames = {
        "GOOD": _frame("GOOD", 0.004, 1.8),
        "BAD": _frame("BAD", -0.002, 1.0),
    }
    picks = screen_universe(frames, ScreeningConfig(min_avg_amount=1))
    assert picks
    assert picks[0].symbol == "GOOD"
    assert picks[0].score > 50


def test_low_liquidity_penalty() -> None:
    frames = {"LOW": _frame("LOW", 0.004, 1.5)}
    picks = screen_universe(frames, ScreeningConfig(min_avg_amount=10**12))
    assert picks[0].score < 55
    assert any("流动性" in risk for risk in picks[0].risks)
