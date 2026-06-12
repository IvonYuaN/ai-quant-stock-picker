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


def test_screen_filters_price_outside_bounds() -> None:
    frames = {
        "HIGH": _frame("HIGH", 0.004, 1.5),
        "OK": _frame("OK", 0.004, 1.5),
    }
    frames["HIGH"]["close"] = 1200.0
    frames["HIGH"]["open"] = 1190.0
    frames["HIGH"]["high"] = 1210.0
    frames["HIGH"]["low"] = 1180.0

    picks = screen_universe(
        frames,
        ScreeningConfig(min_avg_amount=1, min_price=1.0, max_price=1000.0),
    )

    assert {pick.symbol for pick in picks} == {"OK"}


def test_screen_detects_n_rebound_pattern() -> None:
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
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D")
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
    frame = pd.DataFrame(
        {
            "date": dates,
            "symbol": "NREB",
            "name": "NREB",
            "open": [price * 0.99 for price in closes],
            "high": [price * 1.01 for price in closes],
            "low": [price * 0.985 for price in closes],
            "close": closes,
            "volume": volumes,
            "amount": [price * volume * 100 for price, volume in zip(closes, volumes)],
        }
    )

    picks = screen_universe(
        {"NREB": frame},
        ScreeningConfig(min_avg_amount=1, min_bars=20),
    )

    assert picks
    assert picks[0].symbol == "NREB"
    assert "n_rebound" in picks[0].strategies


def test_screen_universe_skips_invalid_frames() -> None:
    valid = _frame("GOOD", 0.004, 1.8)
    invalid = valid.copy()
    invalid.loc[0, "high"] = invalid.loc[0, "low"] - 1

    picks = screen_universe(
        {"GOOD": valid, "BROKEN": invalid},
        ScreeningConfig(min_avg_amount=1),
    )

    assert picks
    assert {pick.symbol for pick in picks} == {"GOOD"}
