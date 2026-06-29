from __future__ import annotations

import pandas as pd

from aqsp.portfolio.correlation import compute_correlation, format_correlation


def _prices(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": values})


def _dated_prices(dates: list[str], values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"date": dates, "close": values})


def test_compute_correlation_uses_configured_threshold() -> None:
    frames = {
        "A": _prices([10.0, 11.0, 10.0, 11.0, 10.0, 11.0]),
        "B": _prices([20.0, 21.0, 20.0, 21.0, 20.0, 21.0]),
    }

    loose = compute_correlation(frames, ["A", "B"], high_corr_threshold=1.01)
    strict = compute_correlation(frames, ["A", "B"], high_corr_threshold=0.55)

    assert loose.high_corr_pairs == []
    assert strict.high_corr_pairs
    assert strict.high_corr_threshold == 0.55
    assert "高相关性配对（> 0.55）" in format_correlation(strict)


def test_compute_correlation_treats_nan_as_high_correlation() -> None:
    frames = {
        "A": _prices([10.0, 10.0, 10.0, 10.0]),
        "B": _prices([20.0, 20.0, 20.0, 20.0]),
    }

    result = compute_correlation(frames, ["A", "B"], high_corr_threshold=0.7)

    assert result.matrix["A"]["B"] == 1.0
    assert result.high_corr_pairs == [("A", "B", 1.0)]


def test_compute_correlation_aligns_returns_by_trade_date() -> None:
    frames = {
        "A": _dated_prices(
            ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"],
            [100.0, 110.0, 121.0, 133.1],
        ),
        "B": _dated_prices(
            ["2026-06-01", "2026-06-03", "2026-06-04", "2026-06-05"],
            [200.0, 220.0, 242.0, 266.2],
        ),
    }

    result = compute_correlation(frames, ["A", "B"], high_corr_threshold=0.7)

    assert result.matrix["A"]["B"] == 1.0
    assert result.high_corr_pairs == [("A", "B", 1.0)]
