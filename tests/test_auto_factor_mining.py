from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.strategies.auto_factor_mining import AutoFactorMiner


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_auto_factor_forward_label_matches_posterior_return() -> None:
    miner = AutoFactorMiner()
    close = pd.Series([10.0, 11.0, 12.0, 15.0], index=pd.RangeIndex(4))

    labels = miner._posterior_forward_returns(close, forward_days=2)

    assert labels.iloc[0] == pytest.approx(0.2)
    assert labels.iloc[1] == pytest.approx(15.0 / 11.0 - 1)
    assert np.isnan(labels.iloc[2])
    assert np.isnan(labels.iloc[3])


def test_strategy_modules_do_not_use_negative_shift() -> None:
    offenders: list[str] = []
    for path in (PROJECT_ROOT / "src" / "aqsp" / "strategies").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "shift(-" in text:
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert offenders == []


def test_auto_factor_mining_does_not_rank_quantiles_by_forward_returns() -> None:
    text = (
        PROJECT_ROOT / "src" / "aqsp" / "strategies" / "auto_factor_mining.py"
    ).read_text(encoding="utf-8")

    assert ".groupby(quantile_labels).mean()" not in text


def _synthetic_ohlcv(rows: int) -> pd.DataFrame:
    """Deterministic OHLCV frame (no network, no RNG)."""
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=rows, freq="D"),
            "open": np.linspace(10.0, 12.0, rows),
            "high": np.linspace(11.0, 13.0, rows),
            "low": np.linspace(9.0, 11.0, rows),
            "close": np.linspace(10.0, 12.0, rows),
            "volume": np.linspace(100.0, 200.0, rows),
        }
    )


def test_auto_factor_mining_raises_when_live_short_workload_passed() -> None:
    """live_short workload must be rejected at the mine_factors entry."""
    miner = AutoFactorMiner()
    frame = _synthetic_ohlcv(10)
    frame.attrs["workload"] = "live_short"

    with pytest.raises(DataError, match="live_short workload cannot enter"):
        miner.mine_factors({"600519": frame})


def test_auto_factor_mining_passes_when_workload_is_offline() -> None:
    """Non-live workloads (e.g. backtest) pass the guard without raising."""
    miner = AutoFactorMiner(min_samples=5)
    frame = _synthetic_ohlcv(60)
    frame.attrs["workload"] = "backtest"

    result = miner.mine_factors({"600519": frame})

    assert isinstance(result, list)
