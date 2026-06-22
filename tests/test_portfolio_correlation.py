from __future__ import annotations

import pandas as pd

from aqsp.portfolio.correlation import compute_correlation, format_correlation


def _prices(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": values})


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
    assert "> 0.55" in format_correlation(strict)
