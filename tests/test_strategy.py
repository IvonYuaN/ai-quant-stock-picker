from __future__ import annotations

import pandas as pd

from aqsp.models import ScreeningConfig
from aqsp.internet_strategies import evaluate_strategy_signals
from aqsp.strategies.thresholds import (
    InternetStrategyThresholds,
    NReboundThresholds,
    ScoringThresholds,
    Thresholds,
)
from aqsp.strategy import _entry_type, score_symbol, screen_universe


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


def _n_rebound_frame() -> pd.DataFrame:
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
    return pd.DataFrame(
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


def test_liquidity_penalty_uses_scoring_threshold() -> None:
    frame = _frame("LOW", 0.004, 1.5)
    config = ScreeningConfig(min_avg_amount=10**12)

    baseline = score_symbol("LOW", frame, config, ScoringThresholds())
    milder = score_symbol(
        "LOW",
        frame,
        config,
        ScoringThresholds(liquidity_penalty=-5),
    )

    assert baseline is not None
    assert milder is not None
    assert milder.score > baseline.score


def test_reversal_entry_uses_configured_rsi_threshold() -> None:
    row = pd.Series(
        {
            "close": 10.0,
            "volume_ratio": 1.0,
            "rsi12": 50.0,
            "macd_hist": 0.2,
        }
    )
    prev = pd.Series({"high_20": 12.0, "macd_hist": 0.1})

    assert _entry_type(row, prev, False, ScoringThresholds()) == "relative_strength"
    assert (
        _entry_type(row, prev, False, ScoringThresholds(reversal_rsi_threshold=60))
        == "reversal_watch"
    )


def test_internet_strategy_signal_uses_configured_score() -> None:
    df = pd.DataFrame(
        [
            {
                "close": 10.0,
                "high_20": 10.0,
                "ma5": 10.0,
                "ma10": 9.5,
                "ma20": 9.0,
                "ma60": 8.5,
                "volume_ratio": 1.0,
                "ret_20": 0.0,
                "bias20": 1.0,
                "rsi12": 50.0,
                "macd_hist": 0.1,
                "amplitude_pct": 3.0,
                "range_pos": 0.5,
                "low_20": 9.0,
            },
            {
                "close": 10.3,
                "high_20": 10.0,
                "ma5": 10.2,
                "ma10": 9.8,
                "ma20": 9.4,
                "ma60": 8.8,
                "volume_ratio": 1.4,
                "ret_20": 0.13,
                "bias20": 2.0,
                "rsi12": 50.0,
                "macd_hist": 0.2,
                "amplitude_pct": 3.0,
                "range_pos": 0.7,
                "low_20": 9.0,
            },
        ]
    )

    signals = evaluate_strategy_signals(
        df,
        thresholds=InternetStrategyThresholds(volume_breakout_score=31.0),
    )

    breakout = next(item for item in signals if item.strategy_id == "volume_breakout")
    assert breakout.score == 31.0


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
    frame = _n_rebound_frame()

    picks = screen_universe(
        {"NREB": frame},
        ScreeningConfig(min_avg_amount=1, min_bars=20),
    )

    assert picks
    assert picks[0].symbol == "NREB"
    assert "n_rebound" in picks[0].strategies


def test_score_symbol_uses_single_threshold_snapshot_for_n_rebound() -> None:
    frame = _n_rebound_frame()
    config = ScreeningConfig(min_avg_amount=1, min_bars=20)

    enabled = score_symbol("NREB", frame, config, ScoringThresholds(), Thresholds())
    disabled = score_symbol(
        "NREB",
        frame,
        config,
        ScoringThresholds(),
        Thresholds(n_rebound=NReboundThresholds(enabled=False)),
    )

    assert enabled is not None
    assert disabled is not None
    assert "n_rebound" in enabled.strategies
    assert "n_rebound" not in disabled.strategies


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
