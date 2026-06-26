from __future__ import annotations

import pandas as pd

from aqsp.risk.dynamic_stop import compute_dynamic_stop
from aqsp.strategies.thresholds import RiskThresholds, Thresholds


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=8).strftime("%Y-%m-%d"),
            "open": [10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7],
            "high": [10.5, 10.6, 10.7, 10.8, 10.9, 11.0, 11.1, 11.2],
            "low": [9.6, 9.7, 9.4, 9.8, 9.9, 10.0, 10.1, 10.2],
            "close": [10.1, 10.2, 10.0, 10.4, 10.5, 10.6, 10.7, 10.8],
        }
    )


def test_compute_dynamic_stop_uses_configured_fallback_pct_when_empty() -> None:
    stop = compute_dynamic_stop(
        pd.DataFrame(),
        100.0,
        symbol="600519",
        fallback_pct=0.08,
    )

    assert stop.symbol == "600519"
    assert stop.recommended_stop == 92.0
    assert stop.method == "fallback_8%"


def test_compute_dynamic_stop_defaults_to_thresholds(monkeypatch) -> None:
    monkeypatch.setattr(
        "aqsp.risk.dynamic_stop.load_thresholds",
        lambda: Thresholds(
            risk=RiskThresholds(
                dynamic_stop_atr_period=2,
                dynamic_stop_atr_multiplier=1.0,
                dynamic_stop_fallback_pct=0.08,
                dynamic_stop_recent_low_days=2,
                dynamic_stop_trailing_pct=0.01,
                dynamic_stop_support_lookback=2,
            )
        ),
    )

    empty = compute_dynamic_stop(pd.DataFrame(), 100.0)
    stop = compute_dynamic_stop(_frame(), 12.0)

    assert empty.recommended_stop == 92.0
    assert stop.support_level == 10.1
    assert stop.trailing_stop == 10.1


def test_compute_dynamic_stop_uses_configured_atr_multiplier() -> None:
    lower = compute_dynamic_stop(_frame(), 12.0, atr_multiplier=1.0)
    wider = compute_dynamic_stop(_frame(), 12.0, atr_multiplier=3.0)

    assert wider.atr_stop < lower.atr_stop


def test_compute_dynamic_stop_uses_configured_atr_period() -> None:
    short = compute_dynamic_stop(_frame(), 12.0, atr_period=2)
    long = compute_dynamic_stop(_frame(), 12.0, atr_period=7)

    assert short.atr_stop != long.atr_stop


def test_compute_dynamic_stop_uses_recent_low_and_trailing_pct() -> None:
    tight = compute_dynamic_stop(
        _frame(),
        12.0,
        recent_low_days=2,
        trailing_pct=0.01,
    )
    loose = compute_dynamic_stop(
        _frame(),
        12.0,
        recent_low_days=6,
        trailing_pct=0.10,
    )

    assert tight.trailing_stop > loose.trailing_stop


def test_compute_dynamic_stop_uses_configured_support_lookback() -> None:
    short = compute_dynamic_stop(_frame(), 12.0, support_lookback=2)
    long = compute_dynamic_stop(_frame(), 12.0, support_lookback=8)

    assert short.support_level == 10.1
    assert long.support_level == 9.4


def test_compute_dynamic_stop_never_recommends_stop_above_price() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=5).strftime("%Y-%m-%d"),
            "open": [10.0, 11.0, 12.0, 13.0, 8.0],
            "high": [11.0, 12.0, 13.0, 14.0, 9.0],
            "low": [9.5, 10.5, 11.5, 12.5, 7.5],
            "close": [10.5, 11.5, 12.5, 13.5, 8.0],
        }
    )

    stop = compute_dynamic_stop(
        frame,
        10.0,
        fallback_pct=0.08,
        recent_low_days=3,
        trailing_pct=0.01,
        support_lookback=3,
    )

    assert stop.recommended_stop < 8.0
