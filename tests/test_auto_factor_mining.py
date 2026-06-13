from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

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
